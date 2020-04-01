# vim: set ts=8 sw=4 sts=4 et ai:
import os
import warnings

from collections import defaultdict
from datetime import datetime

from email.header import decode_header, make_header
from email.parser import BytesParser


MailParser = BytesParser  # export


class EmailResponse(Exception):
    def __init__(self, filename):
        self.filename = filename
        self.final_rcpt = None

    def __repr__(self):
        return '<{cls}({fn} => {rcpt})>'.format(
            cls=self.__class__.__name__, fn=self.filename,
            rcpt=self.final_rcpt)


class Email2xx(EmailResponse):
    pass


class Email299(EmailResponse):
    pass


class Email4xx(EmailResponse):
    def __init__(self, filename, final_rcpt):
        super().__init__(filename)
        self.final_rcpt = final_rcpt


class Email5xx(EmailResponse):
    def __init__(self, filename, final_rcpt):
        super().__init__(filename)
        self.final_rcpt = final_rcpt


class IgnoreEmail(Email299):
    pass


class IgnoreAndDropEmail(Email2xx):
    pass


class HopCountExceeded(Email4xx):
    pass


class EmailFile:
    def __init__(self, filename, stat, email):
        self.filename = filename
        self.stat = stat
        self.email = email

    def is_from_mailer_daemon(self):
        if not hasattr(self, '_is_from_mailer_daemon'):
            self._is_from_mailer_daemon = (
                self.email.get('Return-Path') == '<MAILER-DAEMON>')
        return self._is_from_mailer_daemon

    def is_auto_reply(self):
        return self.email.get('Auto-Submitted') == 'auto-generated'

    def get_date(self):
        if not hasattr(self, '_get_date'):
            self._get_date = datetime.utcfromtimestamp(self.stat.st_mtime)
        return self._get_date

    def get_subject(self):
        "Returns a Header()"
        if not hasattr(self, '_get_subject'):
            self._get_subject = str(
                make_header(decode_header(self.email.get('Subject'))))
        return self._get_subject

    def get_calendar_reply_body(self):
        calendar = [
            i for i in self.email.walk()
            if i.get_content_type() == 'text/calendar']
        if len(calendar) != 1:
            raise KeyError('text/calendar count {}'.format(
                len(calendar)))

        calendar = calendar[0]
        if calendar.get_param('method') != 'REPLY':
            raise KeyError('text/calendar param not REPLY')

        return calendar.get_payload()

    def get_delivery_status_body(self):
        message_delivery_status = [
            i for i in self.email.walk()
            if i.get_content_type() == 'message/delivery-status']
        if len(message_delivery_status) != 1:
            raise KeyError('message/delivery-status count {}'.format(
                len(message_delivery_status)))

        msg = message_delivery_status[0]
        if msg.is_multipart():
            body = ''.join(
                str(i) for i in msg.walk()
                if i.get_content_maintype() == 'text')
        else:
            body = msg.get_payload()

        return body

    def get_first_plain_body(self):
        text_plain = [
            i for i in self.email.walk()
            if i.get_content_type() == 'text/plain']
        if len(text_plain) < 1:  # allow more, e.g. in original
            raise KeyError('text/plain count {}'.format(
                len(text_plain)))
        return text_plain[0].get_payload()

    def ignore_and_drop_exception(self):
        return IgnoreAndDropEmail(self.filename)

    def __repr__(self):
        return '<filename={!r}>'.format(self.filename)

    def get_original_envelope_from(self):
        to = self.email.get('Delivered-To')
        assert '<' not in to, to

        # HACK: Extract 'bounces+X-at-Y@DOM' to 'X@Y'.
        if to.startswith('bounces+') and '-at-' in to:
            to = to[8:]
            to = to.rsplit('@', 1)[0]
            assert '-at-' in to, to
            to = to.replace('-at-', '@', 1)
            assert '-at-' not in to, to

        return to

    def get_original_recipient(self):
        # Will AttributeError if the original is not set yet.
        return self._manual_original_recipient

    def set_original_recipient(self, original_recipient):
        self._manual_original_recipient = original_recipient


def valid_user_reply(efile):
    if not efile.is_from_mailer_daemon():
        # Manually check these? Add them?
        raise IgnoreEmail(efile.filename)


def valid_calendar_reply(efile):
    if not efile.is_from_mailer_daemon():
        try:
            efile.get_calendar_reply_body()
        except KeyError:
            pass
        else:
            if not efile.get_subject().startswith((
                'Accepted:',
                'Afgewezen (afwezig):',
                'Geaccepteerd:',
                'Geweigerd:',
                'Voorlopig:',
                'Voorlopig geaccepteerd:',
                'Afgeslagen:',
                'Afgewezen:',
                'Declined:',
                'Tentatively Accepted:',
            )):
                warnings.warn(
                    '{}: Unexpected calendar reply subject: {!r}'.format(
                        efile.filename, efile.get_subject()))
            raise efile.ignore_and_drop_exception()


def valid_daemon_autoreply(efile):
    if efile.is_from_mailer_daemon():
        if efile.is_auto_reply():
            raise efile.ignore_and_drop_exception()


def valid_user_autoreply(efile):
    if efile.is_from_mailer_daemon() and efile.get_subject().startswith((
            'Automatisch antwoord: ',
            # "Automatisch antwooord:"
            '=?utf-8?B?QXV0b21hdGlzY2ggYW50d29vcmQ6',
            'Automatic reply: ',
            'Niet aanwezig: ',
            # "Niet aanwezig: "
            '=?utf-8?B?TmlldCBhYW53ZXppZzog',
            'Out of Office: ',
            '*SPAM*  Automatisch antwoord: ',
            )):
        raise efile.ignore_and_drop_exception()


def auto_replied_bulk(efile):
    if not efile.is_from_mailer_daemon():
        if (efile.email.get('Precedence') == 'bulk' and
                efile.email.get('Auto-Submitted') == 'auto-replied'):
            raise efile.ignore_and_drop_exception()
        if efile.email.get('X-Zarafa-Vacation') == 'autorespond':
            raise efile.ignore_and_drop_exception()


def has_message_delivery_status(efile):
    if efile.is_from_mailer_daemon():
        try:
            delivery_status = efile.get_delivery_status_body()
        except KeyError:
            pass
        else:
            lines = [i.rstrip() for i in delivery_status.split('\n')]
            rcpt = final_rcpt = action = status = None
            for line in lines:
                if line.startswith('Final-Recipient: rfc822;'):
                    final_rcpt = line[len('Final-Recipient: rfc822;'):].strip()
                if line.startswith('Original-Recipient: rfc822;'):
                    rcpt = line[len('Original-Recipient: rfc822;'):].strip()
                if line.startswith('Action: '):
                    action = line[len('Action: '):].strip()
                if line.startswith('Status: '):
                    status = line[len('Status: '):].strip()
            if not rcpt:
                rcpt = final_rcpt
            if rcpt and status:
                assert not efile.is_auto_reply(), efile
                if status[0] == '5' or (
                        status == '4.4.1' and action == 'failed'):
                    efile.set_original_recipient(rcpt)
                    raise Email5xx(efile.filename, rcpt)  # source?
                elif status[0] == '4' and action == 'delayed':
                    efile.set_original_recipient(rcpt)
                    raise Email4xx(efile.filename, rcpt)  # source?
                elif status[0] == '4':
                    raise IgnoreEmail(efile.filename)


def imss7_ndr(efile):
    if (efile.is_from_mailer_daemon() and
            efile.email.get_content_type() == 'multipart/mixed' and
            efile.email.get_boundary() == '----=_IMSS7_NDR_MIME_Boundary'):
        try:
            body = efile.get_first_plain_body()
        except KeyError as e:
            print(efile.filename, e)
            raise
        else:
            # Can not deliver the message you sent. Will not retry.
            #
            # Sender: <bounces+timeline-at-domain.nl@example.com>
            #
            # The following addresses had delivery problems
            #
            # <someuser@somedomain.nl> : Reply from
            #      mail-am5eur020036.inbound.protection.outlook.com
            #      [1.2.3.4]:
            #  <<< 554 5.4.14 Hop count exceeded - possible mail loop ATTR34
            #      [AM5EUR02FT037.eop-EUR02.prod.protection.outlook.com]
            if ('Can not deliver the message you sent. Will not retry'
                    not in body):
                return

            lines = [i.rstrip() for i in body.split('\n')]
            sender = rcpt = status = None
            for line in lines:
                if line.startswith('Sender: '):
                    sender = line[len('Sender: '):].strip()
                if line.startswith('<'):
                    rcpt = line.split('>', 1)[0][1:].strip()
                if line.startswith(('        <<< ', '\t<<< ')):
                    status = line.lstrip().split()[1]
            if sender and rcpt and status:
                assert not efile.is_auto_reply(), efile
                if status[0] == '4':
                    efile.set_original_recipient(rcpt)
                    raise Email4xx(efile.filename, rcpt)
                elif status[0] == '5':
                    efile.set_original_recipient(rcpt)
                    raise Email5xx(efile.filename, rcpt)


def hacks_hop_count_exceeded(efile):
    if efile.is_from_mailer_daemon():
        # This message was created automatically by the SMTP relay on
        #   smtp.domain.nl.
        #
        # A message that you sent could not be delivered to all of its
        #   recipients.
        # The following address(es) failed:
        #
        #   1234@domain.nl
        #     SMTP error from remote mail server after end of data:
        #     host 172.16.0.31 [172.16.0.31]: 554 5.4.12 SMTP; Hop count
        #     exceeded - possible mail loop detected on message id
        #     <983499893.1939...JavaMail.tomcat@server-core1-salt>
        rcpt = efile.email.get('X-Failed-Recipients')
        if not rcpt:
            return

        if 'possible mail loop detected' in efile.email.get_payload():
            assert efile.email.get('Auto-Submitted') != 'auto-generated'
            raise HopCountExceeded(efile.filename, rcpt)
        if 'this may indicate a mail loop' in efile.email.get_payload():
            assert efile.email.get('Auto-Submitted') == 'auto-replied'
            raise HopCountExceeded(efile.filename, rcpt)


def abort_if_not_matched_handler(efile):
    raise ValueError('could not handle: {!r}'.format(efile.filename))


handlers = (
    # Sample run over 17540 mails.
    has_message_delivery_status,    # count: 11391
    valid_calendar_reply,           # count:  4081 -> ignore-and-drop
    valid_daemon_autoreply,         # count:  1068 -> ignore-and-drop
    valid_user_autoreply,           # count:   680 -> ignore-and-drop
    auto_replied_bulk,              # count: (new)
    valid_user_reply,               # count:   262 -> ignore (and drop anyway)
    imss7_ndr,                      # count:    50
    hacks_hop_count_exceeded,       # count:     8
    abort_if_not_matched_handler,
)


class InvalidAddressList(list):
    def as_dict(self):
        begin = self[0]
        end = self[-1]
        return {
            'first_seen': begin.get_date().strftime('%Y-%m-%d'),
            'last_seen': end.get_date().strftime('%Y-%m-%d'),
            'count': len(self),
            'from': begin.get_original_envelope_from(),
            'to': begin.get_original_recipient(),
        }

    def __str__(self):
        begin = self[0]
        end = self[-1]
        return (
            '{begin_date}..{end_date} {count:5d}x [from={orig_from}] '
            '{address}').format(
                begin_date=begin.get_date().strftime('%Y-%m-%d'),
                end_date=end.get_date().strftime('%Y-%m-%d'),
                count=len(self), address=begin.get_original_recipient(),
                orig_from=begin.get_original_envelope_from())


class InvalidAddressCollector:
    def __init__(self):
        self.by_from_to = defaultdict(InvalidAddressList)

    def __iter__(self):
        return iter(
            self.by_from_to[key] for key in sorted(self.by_from_to.keys()))

    def __bool__(self):
        return bool(self.by_from_to)

    def add(self, efile):
        lower_from = efile.get_original_envelope_from().lower()
        lower_to = efile.get_original_recipient().lower()
        to_user, to_domain = lower_to.split('@', 1)
        key = (lower_from, to_domain, to_user)  # sort-order (domain first)
        self.by_from_to[key].append(efile)

    def move_all_to(self, new_folder):
        # Move to <new_directory>/
        for addrlist in self.by_from_to.values():
            for efile in addrlist:
                move_email(efile.filename, new_folder)


def move_email(filename, new_folder='.Junk'):
    assert new_folder.startswith('.') and '/' not in new_folder
    assert (
        filename.rsplit('/', 1)[0].endswith(('/cur', '/new')) and
        '/{}/'.format(new_folder) not in filename), filename
    new_name = os.path.join(
        filename.rsplit('/', 2)[0], new_folder, 'new',
        os.path.basename(filename))
    os.rename(filename, new_name)
