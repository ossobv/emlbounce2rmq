import os

from unittest import TestCase

from . import mailproc

testdata_dir = os.path.join(os.path.dirname(__file__), 'testdata')
testdata_dirs = [i for i in os.listdir(testdata_dir) if not i.startswith('.')]
tests = {}
for dir_ in testdata_dirs:
    try:
        tests[dir_] = [
            os.path.join(testdata_dir, dir_, i)
            for i in os.listdir(os.path.join(testdata_dir, dir_))
            if not i.startswith('.')]
    except NotADirectoryError:
        pass


class TestMailInTestdata(TestCase):
    "Test emails in testdata/*/ subdirectories"
    def test_tests(self):
        parser = mailproc.MailParser()

        for expected_etype, files in sorted(tests.items()):
            for filename in files:
                msg = '{} checking - {}'.format(
                    expected_etype, os.path.basename(filename))
                print('TEST:', msg)
                with self.subTest(msg=msg):
                    with open(filename, 'rb') as fp:
                        stat = os.fstat(fp.fileno())
                        parsed = parser.parse(fp)
                    efile = mailproc.EmailFile(filename, stat, parsed)
                    try:
                        for handler in mailproc.handlers:
                            handler(efile)
                        handler = None
                    except Exception as e:
                        etype = e.__class__.__name__
                        self.assertEqual(etype, expected_etype)
                        if len(e.args) > 1:
                            filename_parts = filename.split(',')[1:]
                            args = list(e.args[1:])
                            self.assertEqual(filename_parts, args)
                    else:
                        raise AssertionError(
                            'Unhandled {} file {}'.format(
                                expected_etype, filename))


# vim: set ts=8 sw=4 sts=4 et ai:
