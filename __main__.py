#!/usr/bin/env python3
# vim: set ts=8 sw=4 sts=4 et ai:
import os
import sys
import traceback

from collections import defaultdict

from . import mailproc
from .osso_ez_rmq import BaseProducer, rmq_uri
from .settings import PUBLISH_API


class Publisher(BaseProducer):
    def __init__(self):
        self._rmqc = rmq_uri(PUBLISH_API)
        super().__init__()


def main():
    args = list(sys.argv)
    args.pop(0)  # drop argv0
    if args and args[0] == '--keep':
        keep_files = True
        args.pop(0)
    else:
        keep_files = False

    # Accept filenames either on stdin or through argv.
    if args:
        filenames = args
    else:
        filenames = map((lambda x: x.rstrip('\n')), iter(sys.stdin))

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
            # #print('ignore and drop:', e, file=sys.stderr)
            # Move to .Junk/
            if not keep_files:
                mailproc.move_email(efile.filename, '.Junk')
        except mailproc.Email299 as e:
            # #print('ignore check later:', e, file=sys.stderr)
            if not keep_files:
                mailproc.move_email(efile.filename, '.Junk')
        except mailproc.Email4xx as e:
            # #print('ignore for now:', e, file=sys.stderr)
            pass
        except mailproc.Email5xx as e:
            invalids.add(efile)
        else:
            assert False, 'should not get here'
        finally:
            handlers_count[handler.__name__] += 1

    if invalids:
        publisher = Publisher()
        print('Publishing invalid destinations:')
        for invalid in invalids:
            doc = invalid.as_json()
            publisher.publish(doc)
            print(' ', invalid)
        print()
        publisher.close()

        # Move to .Bad-Recipient/
        if not keep_files:
            # NOTE: We'll want to purge these in the end.
            invalids.move_all_to('.Bad-Recipient')

    if len(handlers_count):
        print('Handler summary:')
        for key, value in sorted(handlers_count.items()):
            print(' ', key, value)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        sys.exit(255)  # for xargs
