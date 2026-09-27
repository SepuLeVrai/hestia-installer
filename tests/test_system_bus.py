"""Lifecycle, error and package provenance checks without host mutations."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from installer import system_bus as b, system_packages as s


class SystemBusTests(unittest.TestCase):
    def test_consent_is_strict_and_precedes_observation(self):
        with patch.object(b, '_profile') as profile:
            for value in (False, 1, None, 'yes'):
                with self.assertRaisesRegex(b.SystemBusError, '^SYSTEM_BUS_CONSENT_REQUIRED$'):
                    b.ensure(confirmed=value)
            profile.assert_not_called()

    def test_active_broker_is_preserved_and_identity_bound(self):
        with patch.object(b, '_profile', return_value={'a': 'b'}), \
             patch.object(b, '_state', return_value={'ActiveState': 'active'}), \
             patch.object(b, '_probe', return_value=('same', 'bus', 'owner')) as probe, \
             patch.object(b, '_start') as start:
            report = b.ensure(confirmed=True)
            self.assertTrue(report['system_bus_ready']); self.assertFalse(report['system_bus_started'])
            self.assertEqual(probe.call_count, 2); start.assert_not_called()

    def test_inactive_broker_starts_once_then_must_be_ready(self):
        with patch.object(b, '_profile', return_value={'a': 'b'}), \
             patch.object(b, '_state', side_effect=[{'ActiveState': 'inactive'}, {'ActiveState': 'active'}]), \
             patch.object(b, '_probe', return_value=('same', 'bus', 'owner')), patch.object(b, '_start') as start:
            self.assertTrue(b.ensure(confirmed=True)['system_bus_started']); start.assert_called_once_with()

    def test_observation_of_stopped_bus_never_starts_or_repairs_it(self):
        with patch.object(b, '_profile', return_value={}), \
             patch.object(b, '_state', return_value={'ActiveState': 'inactive'}), patch.object(b, '_start') as start:
            with self.assertRaisesRegex(b.SystemBusError, '^SYSTEM_BUS_NOT_READY$'): b.observe()
            start.assert_not_called()

    def test_profile_or_broker_replacement_refuses_success_without_restart(self):
        for profiles, identities in (([{'x': 1}, {'x': 2}], ['a', 'a']), ([{}, {}], ['a', 'b'])):
            with patch.object(b, '_profile', side_effect=profiles), \
                 patch.object(b, '_state', return_value={'ActiveState': 'active'}), \
                 patch.object(b, '_probe', side_effect=identities), patch.object(b, '_start') as start:
                with self.assertRaisesRegex(b.SystemBusError, '^SYSTEM_BUS_CHANGED$'): b.ensure(confirmed=True)
                start.assert_not_called()

    def test_start_failure_is_closed_and_never_retried(self):
        with patch.object(b, '_profile', return_value={}), \
             patch.object(b, '_state', return_value={'ActiveState': 'inactive'}), \
             patch.object(b, '_start', side_effect=TimeoutError('private host output')) as start:
            with self.assertRaisesRegex(b.SystemBusError, '^SYSTEM_BUS_UNAVAILABLE$'): b.ensure(confirmed=True)
            start.assert_called_once_with()

    def test_unhealthy_active_broker_is_never_restarted(self):
        with patch.object(b, '_profile', return_value={}), \
             patch.object(b, '_state', return_value={'ActiveState': 'active'}), \
             patch.object(b, '_probe', side_effect=RuntimeError('private socket')), patch.object(b, '_start') as start:
            with self.assertRaisesRegex(b.SystemBusError, '^SYSTEM_BUS_UNAVAILABLE$'): b.ensure(confirmed=True)
            start.assert_not_called()

    def test_start_command_has_one_fixed_target_and_no_interactive_authorization(self):
        with patch.object(b.t, '_capture', return_value=b'') as run:
            b._start()
            self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', '--no-pager', '--no-ask-password',
                '--job-mode=fail', 'start', '--', 'dbus.service'])

    def test_vendor_checksums_refuse_change_missing_duplicate_links_and_permissions(self):
        root = Path(tempfile.mkdtemp(prefix='hestia-bus-core-', dir='/var/lib'))
        self.addCleanup(lambda: shutil.rmtree(root)); path = root / 'unit'; path.write_bytes(b'vendor unit')
        path.chmod(0o644); md5 = hashlib.md5(path.read_bytes()).hexdigest()
        record = root / 'dbus.md5sums'; original = md5 + '  ' + str(path).lstrip('/') + '\n'
        record.write_text(original); record.chmod(0o644)
        with patch.object(b, 'INFO', root):
            self.assertEqual(b._vendor_file('dbus', path), b.f._sha(b'vendor unit'))
            for raw in ('', original + original, original.replace(md5, '0' * 32)):
                record.write_text(raw)
                with self.assertRaises(b.SystemBusError): b._vendor_file('dbus', path)
            record.write_text(original); path.chmod(0o666)
            with self.assertRaises(Exception): b._vendor_file('dbus', path)
            path.chmod(0o644); link = root / 'link'; os.link(path, link)
            with self.assertRaises(Exception): b._vendor_file('dbus', path)
            link.unlink(); path.unlink(); path.symlink_to(record)
            with self.assertRaises(Exception): b._vendor_file('dbus', path)

    def test_masks_overrides_jobs_reload_and_duplicate_fields_are_rejected(self):
        good = {'Id': 'dbus.service', 'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'running',
            'FragmentPath': '/usr/lib/systemd/system/dbus.service', 'DropInPaths': '',
            'NeedDaemonReload': 'no', 'Job': '', 'MainPID': '55', 'ControlPID': '0'}
        bad = [('LoadState', 'masked'), ('FragmentPath', '/etc/systemd/system/dbus.service'),
            ('DropInPaths', '/etc/private.conf'), ('NeedDaemonReload', 'yes'), ('Job', '4 /job/4'), ('Id', 'foreign'),
            ('ActiveState', 'failed'), ('ActiveState', 'activating'), ('SubState', 'exited'),
            ('MainPID', '0'), ('ControlPID', '12')]
        def encoded(value): return ''.join(k + '=' + v + '\n' for k, v in value.items()).encode()
        with patch.object(Path, 'resolve', return_value=b.VENDOR / 'dbus.service'):
            with patch.object(b.t, '_capture', return_value=encoded(good)):
                self.assertEqual(b._state('dbus.service'), good)
            for key, value in bad:
                with patch.object(b.t, '_capture', return_value=encoded({**good, key: value})):
                    with self.assertRaises(b.SystemBusError): b._state('dbus.service')
            for raw in (encoded(good) + b'Id=dbus.service\n', b'x' * 16385, b'Id=dbus.service\n'):
                with patch.object(b.t, '_capture', return_value=raw):
                    with self.assertRaises(b.SystemBusError): b._state('dbus.service')

    def test_package_profiles_request_dbus_but_never_mask_it(self):
        packages = s.SystemPackages('a' * 32)
        for php in ('8.2', '8.4'):
            self.assertIn('dbus', packages._packages({'php': php}))
            self.assertFalse(any('dbus' in unit for unit in packages._units({'php': php})))


if __name__ == '__main__': unittest.main()
