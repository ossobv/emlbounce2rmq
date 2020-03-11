emlbounce2rmq
=============

*Internal tool.*


Example ``emlbounce2rmq.sh``::

    #!/bin/sh
    PYTHONPATH=/usr/local/bin /usr/bin/env python3 -m emlbounce2rmq "$@"

Example run::

    emlbounce2rmq.sh --keep /path/to/eml/file

Example run with multiple filenames fed through stdin::

    find /var/mail/example.com/bounces/new \
         /var/mail/example.com/bounces/cur \
      -regex '.*/[0-9].*' -type f | sort | emlbounce2rmq.sh --keep

Example published message::

    {"first_seen": "2020-01-02",
     "last_seen": "2020-01-31",
     "count": 173,
     "from": "noreply@example.com",
     "to": "old.removed.user@anonymous.invalid"}

Example ``settings.py``::

    PUBLISH_API = (
      'rmqs://USER:PASSWORD@'
      'HOSTNAME:PORT/VHOST/cas.mail.exchange')
    # you'll configure a cas.mail.queue that binds to this exchange in RabbitMQ

Requirements::

    pika>=0.10  # python3-pika

If pika 1.0+ complains that the certificate is invalid, you may place a
``<HOSTNAME>.ca`` file in this directory.
