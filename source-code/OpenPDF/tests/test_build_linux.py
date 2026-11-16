"""build-linux.sh must show the frozen self-test's log in the terminal.

The script is run in a scratch folder with a stand-in `python3` (so nothing is
installed or built) and a stand-in `dist/OpenPDF` that writes the log.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'build-linux.sh'


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('bash'), 'Linux build script')
class BuildLinuxSelfTestLog(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        shutil.copy(SCRIPT, self.folder / 'build-linux.sh')
        (self.folder / 'bin').mkdir()
        (self.folder / 'dist').mkdir()
        self.program(self.folder / 'bin' / 'python3', 'exit 0')

    def program(self, path, body):
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def build(self, self_test):
        self.program(self.folder / 'dist' / 'OpenPDF', self_test)
        # A set DISPLAY keeps xvfb-run out of it; the stand-in opens no window.
        env = {'PATH': f"{self.folder / 'bin'}:/usr/bin:/bin", 'DISPLAY': ':0'}
        return subprocess.run(['bash', str(self.folder / 'build-linux.sh')], env=env,
                              capture_output=True, text=True, timeout=60)

    def test_failed_self_test_prints_its_log_and_keeps_its_exit_code(self):
        done = self.build("printf 'UI_CAPTURE one.png\\nAssertionError: Missing tool categories\\n'"
                          ' > smoke-output.log; exit 3')
        self.assertEqual(done.returncode, 3)
        self.assertIn('AssertionError: Missing tool categories', done.stdout)
        self.assertNotIn('UI_CAPTURE', done.stdout)
        self.assertNotIn('Built dist/OpenPDF', done.stdout)

    def test_passing_self_test_prints_its_lines_before_the_built_line(self):
        done = self.build("printf 'UI_CAPTURE one.png\\nUI checks passed\\nPERFORMANCE open 12 ms\\n'"
                          ' > smoke-output.log')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('UI_CAPTURE', done.stdout)
        lines = done.stdout.splitlines()
        self.assertLess(lines.index('UI checks passed'), lines.index('Built dist/OpenPDF'))
        self.assertLess(lines.index('PERFORMANCE open 12 ms'), lines.index('Built dist/OpenPDF'))

    def test_log_with_only_capture_lines_does_not_fail_a_passing_build(self):
        done = self.build("printf 'UI_CAPTURE one.png\\n' > smoke-output.log")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('Built dist/OpenPDF', done.stdout)

    def test_missing_log_is_reported_and_an_old_log_is_not_shown(self):
        (self.folder / 'smoke-output.log').write_text('left over from an earlier run\n')
        done = self.build('exit 2')
        self.assertEqual(done.returncode, 2)
        self.assertNotIn('left over', done.stdout + done.stderr)
        self.assertIn('smoke-output.log was not written', done.stderr)


if __name__ == '__main__':
    unittest.main()
