"""Private configuration filesystem and service-identity tests, no SQL server."""
import copy
import json
import multiprocessing
import os
from pathlib import Path
import pwd
import shutil
import stat
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from installer import database_config as config
from installer import php_transport as p
from installer import sql_accounts as accounts
from web_configuration_fixture import local_request


class ProtectedConfigurationFixture:
    @classmethod
    def setUpClass(cls):
        if os.geteuid() != 0: raise RuntimeError('Disposable root host required')
        cls.users = []
        for prefix in ('hqweb', 'hqwork', 'hqother'):
            name = prefix + os.urandom(3).hex()
            subprocess.run(['useradd', '--system', '--user-group', '--no-create-home', '--home-dir', '/nonexistent',
                            '--shell', '/usr/sbin/nologin', name], check=True, capture_output=True)
            cls.users.append(pwd.getpwnam(name))
        cls.web, cls.worker, cls.other = cls.users

    @classmethod
    def tearDownClass(cls):
        for user in cls.users:
            subprocess.run(['userdel', user.pw_name], check=True, capture_output=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hestia-conf-quality-', dir='/var/lib')
        self.root = Path(self.tmp.name); self.root.chmod(0o755)
        self.web_tmp = tempfile.TemporaryDirectory(prefix='hestia-conf-quality-', dir='/srv')
        self.webroot = Path(self.web_tmp.name); self.webroot.chmod(0o755)
        (self.webroot / 'includes').mkdir()
        self.output = self.root / 'config'; self.output.mkdir()
        self.run = self.root / 'run'; self.run.mkdir(mode=0o711)
        php = Path(shutil.which('php')).resolve()
        ext = Path(subprocess.check_output([str(php), '-n', '-r', 'echo ini_get("extension_dir");'], text=True))
        self.runtime = p.PhpRuntime(php, ext, self.worker.pw_uid, self.worker.pw_gid, self.run, self.root / 'attempts')
        self.payload = local_request()
        self.payload['web'].update(webroot=str(self.webroot), service_user=self.web.pw_name)
        self.credentials = p.ProvisioningCredentials('setup_fixture', 'setup-sensitive-fixture')
        self.directory = self.output / config.configuration_slot(accounts.local_configuration(self.payload))

    def tearDown(self):
        self.tmp.cleanup(); self.web_tmp.cleanup()

    def stage(self, payload=None, confirmed=True):
        return config.stage_local_database_configuration(self.runtime, self.payload if payload is None else payload,
            self.credentials, config_root=self.output, confirmed=confirmed)

    def permission(self, user, flag, path):
        run = subprocess.run(['setpriv', '--reuid=' + str(user.pw_uid), '--regid=' + str(user.pw_gid), '--clear-groups',
                              '/usr/bin/test', flag, str(path)], capture_output=True, timeout=3)
        return run.returncode == 0

    def load(self, user=None, *, expect_error=False):
        # Password and paths go to this synthetic PHP checker through stdin too.
        data = {'path': str(self.directory / 'db.php'), 'password': self.payload['secrets']['database_password'], 'user': self.payload['database']['user']}
        script = ('$v=json_decode(stream_get_contents(STDIN),true); try {require $v["path"]; '
                  'echo DB_HOST==="127.0.0.1" && DB_CHARSET==="utf8mb4" && DB_USER===$v["user"] '
                  '&& hash_equals(DB_PASS,$v["password"]) ? "MATCH" : "MISMATCH"; } '
                  'catch(Throwable $e) {echo $e->getMessage();exit(20);}')
        who = user or self.web
        return subprocess.run(['setpriv', '--reuid=' + str(who.pw_uid), '--regid=' + str(who.pw_gid), '--clear-groups',
                               'php', '-n', '-d', 'display_errors=0', '-d', 'log_errors=0', '-r', script],
                              input=json.dumps(data).encode(), capture_output=True, timeout=3)

