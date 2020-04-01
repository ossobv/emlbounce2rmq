#!/usr/bin/env python3
# vim: set ts=8 sw=4 sts=4 et ai:
import argparse
import logging
import logging.config
import os
import sys
import traceback

from collections import defaultdict

from . import mailproc
from .osso_ez_rmq import BaseProducer, rmq_uri
from .settings import PUBLISH_API


log = logging.getLogger('emlbounce2rmq')


class Publisher(BaseProducer):
    def __init__(self):
        log.debug('Setting up RabbitMQ connection from URI: %s', PUBLISH_API)
        self._rmqc = rmq_uri(PUBLISH_API)
        super().__init__()


def emlbounce2rmq(filenames, do_move, do_publish):
    # Collect totals.
    parser = mailproc.MailParser()
    invalids = mailproc.InvalidAddressCollector()
    handlers_count = defaultdict(int)
    for filename in filenames:
        with open(filename, 'rb') as fp:
            stat = os.fstat(fp.fileno())
            parsed = parser.parse(fp)
        efile = mailproc.EmailFile(filename, stat, parsed)
        try:
            for handler in mailproc.handlers:
                handler(efile)
            handler = None
        except mailproc.Email2xx as e:
            log.debug(
                '%s - %s: Moving to .Junk.Autoreply (subj = %s)',
                efile.filename, e.__class__.__name__, efile.get_subject())
            if do_move:
                mailproc.move_email(efile.filename, '.Junk-Autoreply')
        except mailproc.Email299 as e:
            log.debug(
                '%s - %s: Moving to .Junk.Checkme (subj = %s)',
                efile.filename, e.__class__.__name__)
            if do_move:
                mailproc.move_email(efile.filename, '.Junk-Checkme')
        except mailproc.Email4xx as e:
            # A 4xx means that it will be retried, and we'll get a 5xx
            # later on. Drop the mail?
            log.debug(
                '%s - %s: Keeping. Should be deleted! (rcpt = %s)',
                efile.filename, e.__class__.__name__, e.final_rcpt)
            if do_move:
                mailproc.move_email(efile.filename, '.Junk-Deleted')
        except mailproc.Email5xx as e:
            log.debug(
                '%s - %s: Marked as invalid-destination (rcpt = %s)',
                efile.filename, e.__class__.__name__, e.final_rcpt)
            invalids.add(efile)
        else:
            raise NotImplementedError(
                'programming error on: {fn}'.format(efile.filename))
        finally:
            handlers_count[handler.__name__] += 1

    # Time for a summary:
    if invalids:
        if do_publish:
            publisher = Publisher()
            for invalid in invalids:
                doc = invalid.as_dict()
                log.debug('publish: %r', doc)
                publisher.publish(doc)
            publisher.close()
        else:
            for invalid in invalids:
                doc = invalid.as_dict()
                log.info('Summary of bad RCPT: %s', invalid)

        # Move to .Bad-Recipient/
        if do_move:
            # NOTE: We'll want to purge these from the disk at one point.
            # Use a cron job with find for now.
            #   find .../bounces -mtime +180 -regex '.*/[0-9]+[.].*' -type f \
            #     -delete
            invalids.move_all_to('.Bad-Recipient')

    # Debug what handlers were used:
    if len(handlers_count):
        for key, value in sorted(handlers_count.items()):
            log.debug('Summary of internal handlers: %s = %s', key, value)


def main():
    # Arguments.
    parser = argparse.ArgumentParser(description=(
        'Process EML files, take bounces, output to RabbitMQ. '
        'Maildir format email files should be supplied LF-separated on stdin. '
        'Or supplied as arguments.'))
    parser.add_argument('-n', '--dry-run', action='store_true', help=(
        'Dry run. Implies --verbose, --no-move and --no-publish.'))
    parser.add_argument('-v', '--verbose', action='store_true', help=(
        'Verbose mode.'))
    parser.add_argument('--no-move', action='store_true', help=(
        'Do not move the EML files after processing.'))
    parser.add_argument('--no-publish', action='store_true', help=(
        'Do not publish anything to the RabbitMQ exchange.'))
    parser.add_argument('filenames', nargs='*', help=(
        'EML filenames if not supplied on stdin.'))
    args = parser.parse_args()

    if args.dry_run:
        args.no_move = args.no_publish = args.verbose = True

    # Configure logging.
    logconfig = {
        'version': 1,
        'formatters': {
            'full': {
                'format': '%(asctime)-15s: %(levelname)s: %(message)s'}},
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler', 'formatter': 'full'}},
        'loggers': {
            '': {'handlers': ['console'], 'level': 'WARNING'},
            'emlbounce2rmq': {
                'handlers': ['console'],
                'level': ('DEBUG' if args.verbose else 'INFO'),
                'propagate': False}},
    }
    logging.config.dictConfig(logconfig)

    # Accept filenames either on stdin or through argv.
    if args.filenames:
        filenames = args.filenames
    else:
        filenames = map((lambda x: x.rstrip('\n')), iter(sys.stdin))

    emlbounce2rmq(
        filenames,
        do_move=(not args.no_move),
        do_publish=(not args.no_publish))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(255)  # for xargs
