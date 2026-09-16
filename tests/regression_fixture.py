"""Explicitly selected subprocess fixtures; excluded from ordinary discovery."""
import time
import unittest


class Cases(unittest.TestCase):
    def test_pass_a(self):
        self.assertEqual(6 * 7, 42)

    def test_pass_b(self):
        self.assertTrue(True)

    def test_fail(self):
        self.fail('worker failure evidence')

    def test_wait(self):
        time.sleep(30)
