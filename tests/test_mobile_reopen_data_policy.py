"""Pure boundary checks; no host mutation or native service access."""
from pathlib import Path
import unittest
from unittest.mock import patch

from installer import mobile_reopen_data as d
from installer.model import InstallerError


class DataReleasePolicyTests(unittest.TestCase):
    def test_no_consent_never_reads_inputs(self):
        for consent in (False, 1, None, 'yes'):
            with self.subTest(consent=consent), self.assertRaises(InstallerError):
                d.begin(object(), object(), confirmed=consent)

    def test_foreign_controls_are_rejected(self):
        with self.assertRaises(InstallerError): d.begin(object(), object(), confirmed=True)

    def test_recovery_requires_real_lease_before_path_access(self):
        with patch.object(d.fs, '_directory', side_effect=AssertionError('unexpected read')):
            with self.assertRaises(InstallerError): d.recover(object(), object(), Path('/absent'), confirmed=True)

    def test_recovery_requires_exact_consent(self):
        for consent in (False, 1, None, 'yes'):
            with self.subTest(consent=consent), self.assertRaises(InstallerError):
                d.recover(object(), object(), Path('/absent'), confirmed=consent)


if __name__ == '__main__': unittest.main()
