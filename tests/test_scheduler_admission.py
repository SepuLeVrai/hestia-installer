"""Protected paths plus protocol failures; actual PID 1 is tested in Debian."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import patch

from installer import scheduler_admission as s


class SchedulerAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/var/lib')))
        self.marker = self.root / 'cron-marker'
        self.stack.enter_context(patch.object(s, 'ABSENT_PATHS', (self.marker,)))
        self.local = {'identity': 'private-host-fixture', 'broker_pid': 7}
        self.stack.enter_context(patch.object(s.t, '_local', return_value=self.local))
        self.owner = ':1.0'; self.loaded = []; self.installed = []
        self.stack.enter_context(patch.object(s.t, '_capture', side_effect=self.reply))

    def reply(self, argv, budget):
        operation = argv[argv.index('call') + 4]
        values = {
            'GetId': ('s', 'a' * 32), 'GetNameOwner': ('s', self.owner),
            'GetConnectionUnixProcessID': ('u', 7 if argv[-1] == s.t.BUS else 1),
            'GetConnectionUnixUser': ('u', 0),
            'ListUnits': ('a(ssssssouso)', self.loaded), 'ListUnitFiles': ('a(ss)', self.installed),
        }
        signature, value = values[operation]
        return json.dumps({'type': signature, 'data': [value]}).encode()

    def test_empty_profile_and_unrelated_native_units_are_admitted(self):
        self.installed = [['/usr/lib/systemd/system/phpsessionclean.timer', 'enabled'],
                          ['/etc/systemd/system/hestia-fixture-session-cleaner.timer', 'disabled']]
        with s.acquire() as observation: observation.assert_held()
        with self.assertRaises(s.SchedulerAdmissionError): observation.assert_held()

    def test_installed_inactive_masked_and_template_classic_units_are_rejected(self):
        for name, state in (('cron.service', 'disabled'), ('anacron.timer', 'masked'),
                            ('cron-mail@.service', 'static'), ('atd.service', 'enabled')):
            self.installed = [['/etc/systemd/system/' + name, state]]
            with self.assertRaisesRegex(s.SchedulerAdmissionError, s.REJECTED):
                with s.acquire(): self.fail('installed scheduler admitted')

    def test_loaded_without_installed_file_is_rejected(self):
        self.loaded = [['cron-foreign.service', 'discarded private description', 'loaded',
                        'active', 'exited', '', '/org/freedesktop/systemd1/unit/cron_2dforeign_2eservice',
                        0, '', '/']]
        with self.assertRaisesRegex(s.SchedulerAdmissionError, s.REJECTED): s.SchedulerObservation()

    def test_presence_refuses_files_directories_fifos_and_dangling_links_without_reading(self):
        makers = (lambda: self.marker.write_bytes(b'private invalid content\xff'),
                  lambda: self.marker.mkdir(), lambda: os.mkfifo(self.marker),
                  lambda: self.marker.symlink_to('/missing-private-fixture'))
        for make in makers:
            make()
            try:
                with self.assertRaisesRegex(s.SchedulerAdmissionError, s.REJECTED): s.SchedulerObservation()
            finally:
                if self.marker.is_dir(): self.marker.rmdir()
                else: self.marker.unlink()

    def test_unsafe_ancestor_is_unavailable_not_absence(self):
        self.marker.mkdir(); self.marker.chmod(0o777)
        with patch.object(s, 'ABSENT_PATHS', (self.marker / 'missing',)):
            with self.assertRaisesRegex(s.SchedulerAdmissionError, s.UNAVAILABLE): s.SchedulerObservation()

    def test_late_configuration_or_installed_unit_invalidates_observation(self):
        with s.acquire() as observation:
            self.marker.write_bytes(b'not read')
            with self.assertRaisesRegex(s.SchedulerAdmissionError, s.REJECTED): observation.assert_held()
            self.marker.unlink()
            self.installed = [['/etc/systemd/system/cron-update.path', 'static']]
            with self.assertRaisesRegex(s.SchedulerAdmissionError, s.REJECTED): observation.assert_held()

    def test_manager_change_malformed_rows_and_transport_error_fail_closed(self):
        with s.acquire() as observation:
            self.owner = ':1.1'
            with self.assertRaisesRegex(s.SchedulerAdmissionError, s.UNAVAILABLE): observation.assert_held()
        for rows in (None, [[None, 'disabled']], [['relative/cron.service', 'disabled']]):
            self.installed = rows
            with self.assertRaisesRegex(s.SchedulerAdmissionError, s.UNAVAILABLE): s.SchedulerObservation()
        with patch.object(s.t, '_capture', side_effect=RuntimeError('PRIVATE_SECRET_DETAIL')):
            with self.assertRaisesRegex(s.SchedulerAdmissionError, '^' + s.UNAVAILABLE + '$'): s.SchedulerObservation()

    def test_observation_is_process_bound_private_and_not_serializable(self):
        with s.acquire() as observation:
            self.assertNotIn(str(self.root), repr(observation))
            with self.assertRaises(TypeError): pickle.dumps(observation)
            with patch.object(s.os, 'getpid', return_value=-1):
                with self.assertRaises(s.SchedulerAdmissionError): observation.assert_held()

    def test_unknown_body_exception_is_preserved_and_observation_closed(self):
        error = RuntimeError('consumer fixture')
        with self.assertRaises(RuntimeError) as caught:
            with s.acquire() as observation: raise error
        self.assertIs(caught.exception, error)
        with self.assertRaises(s.SchedulerAdmissionError): observation.assert_held()
