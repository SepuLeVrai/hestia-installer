"""Pure input guards: safe to run without native filesystem fixtures."""
from pathlib import Path
import unittest
from unittest.mock import patch

from installer import mobile_reopen_external as e
from installer.model import InstallerError


class ExternalReleasePolicyTests(unittest.TestCase):
    def test_no_consent_never_reads_the_control(self):
        with self.assertRaises(InstallerError): e.begin(object(), confirmed=False)

    def test_foreign_control_is_not_admitted(self):
        with self.assertRaises(InstallerError): e.begin(object(), confirmed=True)

    def test_recovery_requires_real_lease_before_any_path_read(self):
        with patch.object(e.fs, '_directory', side_effect=AssertionError('unexpected read')):
            with self.assertRaises(InstallerError): e.recover(object(), Path('/absent'), confirmed=True)

    def test_recovery_consent_is_exact_boolean(self):
        for consent in (False, 1, None, 'yes'):
            with self.subTest(consent=consent), self.assertRaises(InstallerError):
                e.recover(object(), Path('/absent'), confirmed=consent)


if __name__ == '__main__': unittest.main()
