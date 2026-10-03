"""Only a sealed DEV permits a second SQL schema; old admission stays closed."""
from copy import deepcopy
from pathlib import Path
import os
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from installer import dev_sql_peer as peer, sql_read_fence as fence
from installer import coordinated_backup as backup
from installer.dev_target import DevTarget
from installer.model import InstallerError
from dev_fixture import target_fixture


class DevSqlPeerTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        main,self.target,_,_,self.foundation=target_fixture(Path(temp.name))
        self.database=main['configuration']['database']
        self.enterContext(patch.object(peer,'selected',return_value=self.foundation))
        self.inspect=self.enterContext(patch.object(DevTarget,'inspect',lambda target:target))

    def test_exact_peer_is_public_binding_without_sql_or_secret(self):
        with patch('subprocess.run',side_effect=AssertionError('SQL effect')):
            value=peer.binding(self.foundation.web,self.database)
        self.assertEqual(value,{'policy':'MANAGED_DEV_SQL_PEER_V1','database':'hestia_dev',
            'target_sha256':peer.digest(self.target.value)})

    def test_legacy_has_no_exception_and_unpaired_metadata_is_refused(self):
        peer.selected.return_value=None
        self.assertIsNone(peer.binding(self.foundation.web,self.database))
        peer.selected.return_value=SimpleNamespace(dev=None)
        with self.assertRaises(InstallerError):peer.binding(self.foundation.web,self.database)

    def test_shared_database_user_or_other_server_is_refused(self):
        other=self.target.value['configuration']['database']
        for key,value in (('host','127.0.0.2'),('port',3307),('name',other['name'].upper()),('user',other['user'])):
            with self.assertRaises(InstallerError):peer.binding(self.foundation.web,{**self.database,key:value})

    def test_native_dev_drift_cannot_produce_an_admission(self):
        with patch.object(DevTarget,'inspect',side_effect=InstallerError('SOURCE_DRIFT')):
            with self.assertRaises(InstallerError):peer.binding(self.foundation.web,self.database)

    def test_sql_protocol_refuses_unbounded_or_same_peer_before_process(self):
        with patch.object(fence.subprocess,'Popen') as start:
            for value in ('',self.database['name'].upper(),'mysql.other','x;DROP DATABASE x','x'*65,[],True):
                with self.assertRaises(fence.SqlReadFenceError):
                    with fence.acquire(None,None,self.database,None,None,peer_database=value):pass
            start.assert_not_called()


class PairedBackupGuardTests(unittest.TestCase):
    def setUp(self):
        self.barrier = object.__new__(backup.hd.HttpDrainLease)
        self.barrier._lease = SimpleNamespace(_directory=17)
        self.web = object.__new__(backup.wf.WebFence)
        self.web._barrier = self.barrier
        self.web._pid, self.web._closed = os.getpid(), False
        self.web._root, self.web._raw = 19, b'bound-native-journal'
        self.http = self.enterContext(patch.object(backup.hd.HttpDrainLease, 'assert_held'))
        self.enterContext(patch.object(backup.wf.fs, '_absent'))
        self.load = self.enterContext(patch.object(backup.wf, '_load',
            return_value=(self.web._raw, None, [{'flags': backup.wf.inode.IMMUTABLE}])))

    def test_every_check_rechecks_http_and_rejects_later_inode_drift(self):
        backup._paired_web_held(self.barrier, self.web)
        self.load.return_value = (self.web._raw, None, [{'flags': 0}])
        with self.assertRaisesRegex(backup.wf.WebFenceError, 'WEB_FENCE_CHANGED'):
            backup._paired_web_held(self.barrier, self.web)
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(self.load.call_count, 2)

    def test_http_drift_still_refuses_before_inode_observation(self):
        self.http.side_effect = backup.hd.HttpDrainError('HTTP_DRAIN_PROFILE_CHANGED')
        with self.assertRaisesRegex(backup.wf.WebFenceError, 'WEB_FENCE_UNAVAILABLE'):
            backup._paired_web_held(self.barrier, self.web)
        self.load.assert_not_called()

    def test_foreign_barrier_or_caller_wrapper_cannot_replace_native_guard(self):
        other = object.__new__(backup.hd.HttpDrainLease)
        for barrier, web in ((other, self.web), (self.barrier, SimpleNamespace(
                _barrier=self.barrier, assert_held=lambda: None))):
            with self.assertRaisesRegex(backup.CoordinatedBackupError, 'COORDINATED_SERVICE_BARRIER_REQUIRED'):
                backup._paired_web_held(barrier, web)
        self.http.assert_not_called(); self.load.assert_not_called()

    def test_closed_native_fence_never_reuses_an_earlier_pass(self):
        backup._paired_web_held(self.barrier, self.web)
        self.web._closed = True
        with self.assertRaisesRegex(backup.wf.WebFenceError, 'WEB_FENCE_LEASE_REQUIRED'):
            backup._paired_web_held(self.barrier, self.web)
        self.assertEqual(self.http.call_count, 1)
