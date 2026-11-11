"""Tests for timestamped, per-run application log files."""

import io
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import app_logging


class AppLoggingTests(unittest.TestCase):
    def test_creates_timestamped_log_and_captures_stdout_and_stderr(self):
        console_out = io.StringIO()
        console_err = io.StringIO()

        with tempfile.TemporaryDirectory() as base_dir:
            with patch.object(sys, "stdout", console_out), patch.object(sys, "stderr", console_err):
                session = app_logging.start_file_logging("Live Traffic/GUI", base_dir)
                log_path = session.path
                print("stdout diagnostic marker")
                print("stderr diagnostic marker", file=sys.stderr)
                app_logging.log_action("test.user_action", control="map", enabled=True)
                session.close()

                self.assertIs(sys.stdout, console_out)
                self.assertIs(sys.stderr, console_err)

            self.assertTrue(os.path.isfile(log_path))
            self.assertEqual(os.path.basename(os.path.dirname(log_path)), "logs")
            self.assertRegex(
                os.path.basename(log_path),
                r"^Live_Traffic_GUI_\d{8}_\d{6}_\d{6}_\d+\.log$",
            )
            with open(log_path, "r", encoding="utf-8") as log_file:
                contents = log_file.read()

        self.assertIn("log started:", contents)
        self.assertIn("stdout diagnostic marker", contents)
        self.assertIn("stderr diagnostic marker", contents)
        self.assertRegex(contents, r"\d{4}-\d{2}-\d{2}T.* \[ACTION\] test\.user_action")
        self.assertIn('"enabled": true', contents)
        self.assertIn("stdout diagnostic marker", console_out.getvalue())
        self.assertIn("stderr diagnostic marker", console_err.getvalue())

    def test_captures_output_when_frozen_gui_has_no_console(self):
        with tempfile.TemporaryDirectory() as base_dir:
            with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
                session = app_logging.start_file_logging(base_dir=base_dir)
                log_path = session.path
                print("windowed stdout marker")
                print("windowed stderr marker", file=sys.stderr)
                session.close()
                self.assertIsNone(sys.stdout)
                self.assertIsNone(sys.stderr)

            with open(log_path, "r", encoding="utf-8") as log_file:
                contents = log_file.read()

        self.assertIn("windowed stdout marker", contents)
        self.assertIn("windowed stderr marker", contents)

    def test_close_is_safe_to_call_twice(self):
        with tempfile.TemporaryDirectory() as base_dir:
            with patch.object(sys, "stdout", io.StringIO()), patch.object(sys, "stderr", io.StringIO()):
                session = app_logging.start_file_logging(base_dir=base_dir)
                session.close()
                session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
