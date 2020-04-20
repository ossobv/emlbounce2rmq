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

Current "production" configuration::

    #-- /etc/crontab

    55 0 * * * root
      find /var/mail/example.com/bounces -mtime +180 -regex '.*/[0-9]+[.].*'
        -type f -delete

    45 12 * * * root
      find /var/mail/example.com/bounces/new /var/mail/example.com/bounces/cur
        -regex '.*/[0-9].*' -type f | /usr/local/bin/emlbounce2rmq.sh


    #-- /etc/postfix/sender_canonical_maps

    # Rewrite some Example.com stuff to their own. This is not something we can
    # solve. They should use authenticated senders and actually *read* the
    # responses.
    /^(bounces[+].*@example[.]com)$/        $1
    /^(jira)@(example[.]com)$/              bounces+$1-at-$2@example.com
    /^(noreply|timeline)@(example[.]nl)$/   bounces+$1-at-$2@example.com


    #-- /usr/local/bin/emlbounce2rmq/settings.py

    ACC_PUBLISH_API = (
      'rmqs://xxx:xxx@acceptance:5671/acc2/cas.mail.exchange')
    PROD_PUBLISH_API = (
      'rmqs://xxx:xxx@production:5671//cas.mail.exchange#email')
    PUBLISH_API = PROD_PUBLISH_API

Requirements::

    pika>=0.10  # python3-pika

If pika 1.0+ complains that the certificate is invalid, you may place a
``<HOSTNAME>.ca`` file in this directory.
