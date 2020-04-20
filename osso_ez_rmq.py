# vim: set ts=8 sw=4 sts=4 et ai:
import json
import logging
import os
import ssl
import time

from collections import namedtuple
from datetime import datetime
from urllib.parse import urlparse

import pika  # >=0.10
from pika.exceptions import ConnectionClosed

# See also:
# https://pika.readthedocs.io/en/0.12.0/modules/parameters.html#urlparameters
# The RMQ_URI has a different scheme, takes no query_params, but takes a
# queue.
# RMQ_URI = 'rmq[s]://[USER:PASS@]HOST[:PORT]/VHOST/EXCHANGEorQUEUE[#KEY]'

__all__ = (
    'BaseConsumer', 'BaseEnvConsumer',
    'BaseProducer', 'EnvProducer',  # EnvProducer is actually complete
    'rmq_connect', 'rmq_init_consumer', 'rmq_uri',
)

log = logging.getLogger('osso_ez_rmq')


class RmqException(Exception):
    pass


def rmq_connect(rmqc):
    log.info('Begin connection to RMQ %s:%s', rmqc.host, rmqc.port)
    conn_kwargs = {}

    try:
        pika.SSLOptions
    except AttributeError:  # pika<1.0
        if rmqc.tls:
            conn_kwargs['ssl'] = True
        else:
            conn_kwargs['ssl'] = False
    else:  # pika>=1.0
        if rmqc.tls:
            # Quick and dirty hack, using a local ca file.
            ca_fn = os.path.join(os.path.dirname(__file__), rmqc.host + '.ca')
            try:
                with open(ca_fn) as ca_fp:
                    ca_data = ca_fp.read()
            except OSError:
                ca_data = None

            ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)

            if ca_data:
                ctx.check_hostname = False  # this host uses non-standard CN
                ctx.load_verify_locations(cadata=ca_data)

            conn_kwargs['ssl_options'] = pika.SSLOptions(context=ctx)

    # Timeout options for pika >= 0.11:
    if tuple(int(i) for i in pika.__version__.split('.')[0:2]) >= (0, 11):
        conn_kwargs['heartbeat'] = 600
        conn_kwargs['blocked_connection_timeout'] = 300

    if rmqc.username and rmqc.password:
        creds = pika.PlainCredentials(rmqc.username, rmqc.password)
        params = pika.ConnectionParameters(
            host=rmqc.host, port=rmqc.port, credentials=creds,
            virtual_host=rmqc.vhost, **conn_kwargs)
    else:
        params = pika.ConnectionParameters(
            host=rmqc.host, port=rmqc.port, virtual_host=rmqc.vhost,
            **conn_kwargs)

    connection = pika.BlockingConnection(params)
    log.info('Connected to RMQ %s:%s', rmqc.host, rmqc.port)
    return connection.channel()


def rmq_init_consumer(channel, rmqc_queue, on_message=None):
    channel.basic_consume(on_message, rmqc_queue, no_ack=False)


class _BaseRmqChannel(object):
    """
    Connect methods that work with self._rmqc. Provides self._channel.
    """
    def __init__(self):
        super(_BaseRmqChannel, self).__init__()
        self._channel = None

    def connect(self):
        if self._channel is None:
            self._channel = rmq_connect(self._rmqc)
            log.info('Connected RMQ %s', self._channel)

    def close(self):
        if self._channel:
            log.info('Disconnecting RMQ %s', self._channel)
            self._channel.stop_consuming()
            self._channel.connection.close()
            self._channel = None


class _BaseRmqEnv(object):
    """
    Take uri from RMQ_URI env and parse. Provides self._rmqc.
    """
    def __init__(self):
        super(_BaseRmqEnv, self).__init__()
        env_uri = os.getenv('RMQ_URI', '')
        try:
            self._rmqc = rmq_uri(env_uri)
        except Exception as e:
            raise RmqException(
                'RMQ_URI env should be:\n  rmq[s]://host/vhost/exchange\n\n{}'
                .format(e)) from e


class BaseConsumerInterface(object):
    """
    RabbitMQ (Rmq) message consumer interface.
    """
    def on_message(self, channel, deliver, properties, body):
        raise NotImplementedError()


class BaseConsumer(BaseConsumerInterface, _BaseRmqChannel):
    """
    Provide run() method that infloops. Requires self._rmqc and an
    implementation of on_message().
    """
    def connect(self):
        if self._channel is None:
            self._channel = rmq_connect(self._rmqc)
            rmq_init_consumer(
                self._channel, self._rmqc.queue, on_message=self.on_message)

    def run(self, timeout=None, timeout_callback=None):
        self.connect()

        if timeout:
            self._timeout_callback = timeout_callback
            self._channel.connection.add_timeout(timeout, self._on_timeout)

        # Trap pika.exceptions.ConnectionClosed with args[1] == 'EOF'?
        self._channel.start_consuming()

    def ack_messages(self, tags):
        for tag in tags:
            self._channel.basic_ack(tag)

    def _on_timeout(self):
        self._channel.stop_consuming()


class BaseEnvConsumer(BaseConsumer, _BaseRmqEnv):
    """
    Take uri from RMQ_URI env and parse. Provides run() method that infloops
    and calls on_message().
    """
    pass


class BaseProducer(_BaseRmqChannel):
    """
    Provides publish() method. Requires self._rmqc.
    """
    def publish(self, message, routing_key=None):
        max_tries = 3
        for retry in range(1, max_tries + 1):
            try:
                if not self._channel:
                    self.connect()

                # Send the blank routing_key if nothing is defined.
                if routing_key is None:
                    routing_key = self._rmqc.routing_key
                    if routing_key == '#':  # '#' is wildcard
                        # But a wildcard doesn't make sense as input..
                        routing_key = ''

                self._publish(
                    message, self._rmqc.exchange, routing_key)
            except Exception as e:
                if not (retry == 1 and isinstance(e, ConnectionClosed)):
                    # ConnectionClosed "timeout" after being connected for too
                    # long. Ignore the first failure.
                    log.exception(
                        'RMQ connection %d/%d failed', retry, max_tries)
                self._channel = None  # or should we attempt to close it?
                if retry == max_tries:
                    raise RmqException(
                        'Failure communicating with RabbitMQ: {}'.format(e)) \
                        from e
                time.sleep(retry * 5)
            else:
                break

    def _publish(self, payload, exchange_name, routing_key):
        self._channel.basic_publish(
            exchange=exchange_name,
            routing_key=routing_key,
            properties=pika.BasicProperties(
                content_type='application/json',
                delivery_mode=2,  # make message persistent
            ),
            body=json.dumps(payload, default=_json_serial))


class EnvProducer(BaseProducer, _BaseRmqEnv):
    """
    Take uri from RMQ_URI env and parse. Provides publish() method.
    """
    pass


def _json_serial(obj):
    """
    JSON serializer for objects not serializable by default json code.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError("Type not serializable")


def _hide_secrets(secret_fields, namedtuple_):
    secret_fields = secret_fields.split()
    hidden_value = '***'

    def __repr__(self):
        args = [
            '{}={!r}'.format(
                i, (hidden_value if i in secret_fields else getattr(self, i)))
            for i in self._fields]
        return '{}({})'.format(self.__class__.__name__, ', '.join(args))

    namedtuple_.__repr__ = __repr__
    return namedtuple_


def rmq_uri(uri):
    RmqUriBase = _hide_secrets('username password', namedtuple(
        'RmqUri',
        'host port username password vhost exchange routing_key tls'))

    class RmqUri(RmqUriBase):
        @property
        def queue(self):
            "Used for reading. Exchange is used for publishing."
            return self.exchange

    # RMQ_URI = rmq[s]://[USER:PASS@]HOST[:PORT]/VHOST/EXCHANGEorQUEUE[#KEY]
    parsed = urlparse(uri)
    assert parsed.scheme in ('rmq', 'rmqs'), parsed
    host = parsed.hostname
    if parsed.scheme == 'rmq':
        port = parsed.port or 5672
        tls = False
    elif parsed.scheme == 'rmqs':
        port = parsed.port or 5671
        tls = True
    path = parsed.path.split('/', 2)
    assert len(path) >= 2, parsed
    (blank, vhost, exchange) = path
    if vhost == '' or vhost == '%2F':
        vhost = '/'
    assert blank == '', parsed
    (username, password) = (parsed.username, parsed.password)
    assert not parsed.query, parsed
    assert bool(parsed.username) == bool(parsed.password), parsed
    return RmqUri(
        host=host, port=port, username=username, password=password,
        vhost=vhost, exchange=exchange, routing_key=(parsed.fragment or '#'),
        tls=tls)


if __name__ == '__main__':
    if False:
        class MyConsumer(BaseConsumer):
            def __init__(self, rmqc):
                super().__init__()
                self._rmqc = rmqc

            def on_message(self, channel, deliver, properties, body):
                # Display the message parts
                print(channel, deliver, properties, body)
                print()
                # channel.basic_ack(deliver.delivery_tag)
                raise StopIteration()

        env_uri = os.getenv(
            'RMQ_URI',
            'rmq[s]://[USER:PASS@]HOST[:PORT]/VHOST/QUEUE[#KEY]')
        consumer = MyConsumer(rmq_uri(env_uri))
        consumer.run()
    else:
        class MyProducer(BaseProducer):
            def __init__(self, rmqc):
                super().__init__()
                self._rmqc = rmqc

        env_uri = os.getenv(
            'RMQ_URI',
            'rmq[s]://[USER:PASS@]HOST[:PORT]/VHOST/EXCHANGE[#KEY]')
        producer = MyProducer(rmq_uri(env_uri))
        producer.publish([{'test': 'yes'}])
