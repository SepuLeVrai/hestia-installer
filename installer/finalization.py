"""5B2.3: private activation of a *prepared* fresh installation, never fresh replay.

No public endpoint, upgrade migration, service creation, root PHP, or API call.
The database and source must already be prepared. Uncertain attempts require
operator inspection. Mutable Assistant JSON is data, never executable PHP.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import hmac
import os
import pwd
import re
import stat
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from installer import database_config as fs
from installer import database_step as dbstep
from installer import php_transport as p
from installer.web_releases import get_release
from installer.model import strict_json_loads
from installer.transaction import _private_directory
from installer.web_config import validate_web_configuration

# Exact Web finalization contract; legacy SQL transport pins remain unchanged.
WEB_COMMIT = '46c03060625d4d53c675474b11aaa33007d9aad7'
RUNTIME_SHA256 = 'b2205c6f7b326b0692e57942f282260669e3ec147daf3dc9f18c546782c7eb0f'
ENGINE_FILES = (*dbstep.ENGINE_FILES, 'includes/installation/finalization.php')
ENGINE_SHA256 = 'afef90f5d935e944010112bcfb74dac5b86f6596c2e3bb3fc47e49714cae5424'
SQL_ERRORS = frozenset({'FINALIZATION_DATABASE_INVALID', 'FINALIZATION_SETTING_FAILED', 'REQUEST_INVALID',
    'SQL_TARGET_INVALID', 'SQL_CA_INVALID', 'SQL_CREDENTIAL_INVALID', 'SQL_DRIVER_REQUIRED',
    'SQL_CONNECTION_FAILED', 'SQL_TLS_CONNECTION_FAILED', 'FINALIZATION_CHECK_FAILED', 'ACCOUNT_POLICY_REJECTED', 'AUDIT_UNAVAILABLE'})
_SKIPPED = frozenset({'.git', '.github', '.quality', '__pycache__', 'docs', 'tests'})
_SUFFIXES = frozenset({'.php', '.phtml', '.phar', '.inc', '.js', '.mjs', '.cjs', '.css', '.py', '.sh', '.sql'})


class FinalizationError(RuntimeError):
    """Closed diagnostic, with no secret or underlying exception attached."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise FinalizationError(code)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(fd: int, name: str, gid: int, *, mode: int = 0o640, limit: int = 16384) -> bytes:
    """Pinned inode, exact mode/owner, bounded read, no ACL, hardlink or symlink."""
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(handle)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_gid == gid
                and stat.S_IMODE(before.st_mode) == mode and before.st_nlink == 1
                and 0 <= before.st_size <= limit, 'CONFIGURATION_INTEGRITY_FAILED')
        fs._no_acl(handle)
        data = bytearray()
        while len(data) <= limit:
            block = os.read(handle, min(4096, limit + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(handle)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(data) == before.st_size and all(getattr(before, k) == getattr(after, k) for k in fields),
                'CONFIGURATION_INTEGRITY_FAILED')
        return bytes(data)
    finally:
        os.close(handle)


def _json_read(fd: int, name: str, gid: int, **kwargs) -> dict:
    raw = _read(fd, name, gid, **kwargs)
    value = strict_json_loads(raw)
    require(type(value) is dict and p._json(value) == raw, 'CONFIGURATION_INTEGRITY_FAILED')
    return value


def _write(fd: int, name: str, data: bytes, gid: int, *, mode: int = 0o640) -> None:
    fs._write(fd, name, data, gid)
    # Only a newly created *data* file or private journal can have another mode.
    if mode != 0o640:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
        try:
            os.fchmod(handle, mode)
            fs._no_acl(handle)
            os.fsync(handle)
        finally:
            os.close(handle)
    os.fsync(fd)


def _runtime_digest(root: Path) -> str:
    """Check all executable runtime/control files, vendor and their path set.

    Unlisted executable additions are rejected by the digest, not silently
    ignored. Documentation, test code and generated activation pointers are not
    runtime input. Writable business data is outside this fresh code contract.
    """
    names = []
    deadline = time.monotonic() + 30
    with fs._directory(root):
        for directory, dirs, files in os.walk(root, followlinks=False):
            parent = Path(directory)
            dirs[:] = sorted(d for d in dirs if d not in _SKIPPED)
            for name in dirs:
                require(not (parent / name).is_symlink(), 'SOURCE_INTEGRITY_FAILED')
            for name in files:
                relative = (parent / name).relative_to(root).as_posix()
                if relative in ('includes/db.php', 'install.lock'):
                    continue
                if (relative.startswith('vendor/') or Path(name).suffix.lower() in _SUFFIXES
                        or name in ('.htaccess', '.user.ini', 'composer.json', 'composer.lock')):
                    names.append(relative)
            require(len(names) <= 10000 and time.monotonic() < deadline, 'SOURCE_LIMIT')
    total = 0
    digest = hashlib.sha256()
    for name in sorted(names):
        require(time.monotonic() < deadline, 'SOURCE_LIMIT')
        path = root / name
        with fs._directory(path.parent) as parent:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            require(not info.st_mode & 0o7022 and info.st_uid == 0 and stat.S_ISREG(info.st_mode),
                    'SOURCE_INTEGRITY_FAILED')
            # p._read_file pins the opened inode and rejects hardlinks/races.
            data = p._read_file(path)
            with path.open('rb') as stream:
                fs._no_acl(stream.fileno())
        total += len(data)
        require(total <= p.MAX_BUNDLE, 'SOURCE_LIMIT')
        digest.update(p._json([name, len(data), _sha(data)]) + b'\n')
    return digest.hexdigest()


def _configuration(payload: dict, *, fresh: bool) -> dict:
    try:
        normalized = copy.deepcopy(payload)
        # The original 5A validator keeps its historical INPUT_ONLY behavior.
        # In this *existing managed instance* operation blank means keep.
        if not fresh and normalized['assistant']['action'] == 'configure' and normalized['secrets']['openai_api_key'] == '':
            normalized['assistant']['action'] = 'preserve'
        config = validate_web_configuration(normalized)['configuration']
        require(config['mode'] == ('fresh' if fresh else 'upgrade'), 'FINALIZATION_MODE_REFUSED')
        if config['database']['host'] == 'localhost':
            config['database']['host'] = '127.0.0.1'
        return config
    except FinalizationError:
        raise
    except Exception:
        raise FinalizationError('INPUT_REJECTED') from None


def _open(runtime: p.PhpRuntime, config: dict, root: Path, stack: ExitStack):
    p._runtime(runtime, config['web']['service_user'])
    gid = fs._web_group(config['web']['service_user'], runtime)
    web = Path(config['web']['webroot'])
    require(isinstance(root, Path) and web != root and web not in root.parents
            and not (str(root) == '/var/www' or str(root).startswith('/var/www/')
                     or str(root) == '/srv' or str(root).startswith('/srv/')), 'CONFIGURATION_PUBLIC_PATH_REFUSED')
    directory = root / fs.configuration_slot(config)
    stack.enter_context(fs._directory(root, readable_by=gid))
    conf = stack.enter_context(fs._directory(directory, readable_by=gid))
    info = os.fstat(conf)
    require(info.st_gid == gid and stat.S_IMODE(info.st_mode) == 0o750, 'CONFIGURATION_INTEGRITY_FAILED')
    webfd = stack.enter_context(fs._directory(web, readable_by=gid))
    inc = stack.enter_context(fs._directory(web / 'includes', readable_by=gid))
    return gid, web, directory, conf, webfd, inc


def _prepared(config: dict, payload: dict, directory: Path, conf: int, gid: int) -> tuple[dict, bytes, bytes | None]:
    state = _json_read(conf, 'state.json', gid)
    expected = {'scope': 'DATABASE_CONFIGURATION_READY', 'configuration_activated': False,
                'application_installed': False, 'assistant_enabled': False, 'tls_verified': config['database']['mode'] == 'remote',
                'application_verified': True, 'migration_retained': state.get('migration_retained'), 'version': p.ENGINE_VERSION}
    require(type(state.get('migration_retained')) is bool and p._json(state) == p._json(expected), 'DATABASE_NOT_PREPARED')
    raw = _read(conf, 'database.json', gid)
    database = strict_json_loads(raw)
    require(type(database) is dict and p._json(database) == raw, 'CONFIGURATION_INTEGRITY_FAILED')
    ca = _read(conf, 'ca.pem', gid) if config['database']['mode'] == 'remote' else None
    expected_db = {'version': 2, **dbstep._target(config, directory / 'ca.pem' if ca is not None else None, ca),
                   'user': config['database']['user'], 'password': payload['secrets']['database_password'], 'charset': 'utf8mb4'}
    require(hmac.compare_digest(raw, p._json(expected_db)), 'CONFIGURATION_TARGET_MISMATCH')
    template = p._read_file(Path(__file__).parent / 'private/database_loader_v2.php')
    expected_loader = template.replace(b'__DATABASE_PATH_HEX__', str(directory / 'database.json').encode().hex().encode()).replace(
        b'__SERVICE_GID__', str(gid).encode())
    loader = _read(conf, 'db.php', gid)
    require(hmac.compare_digest(loader, expected_loader), 'CONFIGURATION_LOADER_MISMATCH')
    return database, loader, ca


def _database_receipt(runtime: p.PhpRuntime, database: dict) -> None:
    key = _sha(p._json([database['host'], database['port'], database['name'].lower()]))
    with _private_directory(runtime.state_root, create=False) as fd:
        raw = _read(fd, 'fresh-' + key + '.attempt', 0, mode=0o600)
    rows = raw.splitlines()
    require(len(rows) == 2, 'DATABASE_RECEIPT_REQUIRED')
    first, last = [strict_json_loads(row) for row in rows]
    require(type(first) is dict and set(first) == {'version', 'state', 'request_id'}
            and type(first['version']) is int and first['version'] == 1 and first['state'] == 'DISPATCHING'
            and type(first['request_id']) is str and re.fullmatch(r'[a-f0-9]{32}', first['request_id']) is not None
            and last == {'state': 'DATABASE_CONFIGURATION_READY', 'code': 'OK'}, 'DATABASE_RECEIPT_REQUIRED')


def _response(code: int, raw: bytes, request: dict, *, probe: bool) -> dict:
    try:
        require(type(code) is int and type(raw) is bytes and len(raw) <= p.MAX_OUTPUT, 'PROTOCOL_REJECTED')
        value = strict_json_loads(raw)
        require(type(value) is dict and set(value) == {'version', 'operation', 'request_id', 'ok', 'result', 'error'}
                and type(value['version']) is int and value['version'] == 1 and type(value['ok']) is bool
                and value['operation'] == request['operation'] and value['request_id'] == request['request_id'], 'PROTOCOL_REJECTED')
        if not value['ok']:
            require(code == 20 and value['result'] is None and type(value['error']) is str
                    and value['error'] in (SQL_ERRORS | {'FINALIZATION_RUNTIME_FAILED'}), 'PROTOCOL_REJECTED')
            raise FinalizationError(value['error'])
        require(code == 0 and value['error'] is None, 'PROTOCOL_REJECTED')
        result = value['result']
        keys = {'database_verified', 'assistant_enabled'}
        if probe:
            keys |= {'setting_enabled', 'key_configured', 'api_access'}
        require(type(result) is dict and set(result) == keys and result['database_verified'] is True
                and all(type(result[k]) is bool for k in keys - {'api_access'}), 'PROTOCOL_REJECTED')
        if probe:
            require(result['api_access'] == 'NOT_TESTED'
                    and result['assistant_enabled'] == (result['setting_enabled'] and result['key_configured']), 'PROTOCOL_REJECTED')
        return result
    except FinalizationError:
        raise
    except Exception:
        raise FinalizationError('PROTOCOL_REJECTED') from None


def _sql(runtime: p.PhpRuntime, source: Path, database: dict, payload: dict, config: dict, ca: bytes | None,
         *, desired: bool, mutate: bool, cancel=None) -> dict:
    with tempfile.TemporaryDirectory(prefix='finalization-sql-', dir=runtime.run_root) as tmp:
        stage = Path(tmp)
        p._copy_bundle(source, stage, runtime.worker_gid, ENGINE_FILES, ENGINE_SHA256, 'finalization_bridge.php')
        with fs._directory(stage) as fd:
            _write(fd, 'sql_accounts_policy.php', p._read_file(Path(__file__).parent / 'private/sql_accounts_policy.php'), runtime.worker_gid)
            if ca is not None:
                _write(fd, 'ca.pem', ca, runtime.worker_gid)
        target = {k: database[k] for k in ('host', 'port', 'name', 'tls_required', 'tls_ca_file', 'tls_ca_sha256')}
        if ca is not None:
            target['tls_ca_file'] = str(stage / 'ca.pem')
        request = {'version': 1, 'operation': 'set_assistant' if mutate else 'verify', 'request_id': os.urandom(16).hex(),
            'target': target, 'application': {'user': database['user'], 'password': database['password']},
            'administrator': {**config['administrator'], 'password': payload['secrets']['admin_password']}, 'desired_enabled': desired}
        code, raw = p._exchange(p._command(runtime, stage), p._json(request), stage, runtime.timeout_seconds, cancel)
        return _response(code, raw, request, probe=False)


def _probe(runtime: p.PhpRuntime, config: dict, directory: Path, gid: int, *, active: bool,
           action: str = 'preserve', key: str = '', cancel=None) -> dict:
    with tempfile.TemporaryDirectory(prefix='finalization-web-', dir=runtime.run_root) as tmp:
        stage = Path(tmp)
        os.chown(stage, 0, gid)
        stage.chmod(0o750)
        with fs._directory(stage) as fd:
            _write(fd, 'bridge.php', p._read_file(Path(__file__).parent / 'private/finalization_probe.php'), gid)
            _write(fd, 'sql_accounts_policy.php', p._read_file(Path(__file__).parent / 'private/sql_accounts_policy.php'), gid)
        command = p._command(runtime, stage)
        web = pwd.getpwnam(config['web']['service_user'])
        # Unlike the SQL snapshot this probe reads the *actual* Web/config paths
        # and all their ancestors. POSIX permissions, exact code pins, dedicated
        # uid/gid, cleared groups/env/caps and resource limits remain enforced.
        for i, value in enumerate(command):
            if value.startswith('--reuid='): command[i] = '--reuid=' + str(web.pw_uid)
            elif value.startswith('--regid='): command[i] = '--regid=' + str(gid)
            elif value.startswith('open_basedir='): command[i] = 'open_basedir='
            elif value == '--fsize=0' and action != 'preserve': command[i] = '--fsize=2048'
        request = {'version': 1, 'operation': 'settings' if action != 'preserve' else ('active' if active else 'prepared'),
                   'request_id': os.urandom(16).hex(), 'root': config['web']['webroot'], 'directory': str(directory),
                   'gid': gid, 'action': action, 'key': key}
        code, raw = p._exchange(command, p._json(request), stage, runtime.timeout_seconds, cancel)
        return _response(code, raw, request, probe=True)


def _documents(web: Path, directory: Path, gid: int, database: dict, loader: bytes, nonce: str) -> tuple[bytes, bytes, bytes]:
    seal = p._json({'version': 1, 'state': 'WEB_FRESH_FINALIZED', 'webroot': str(web), 'instance': nonce,
                    'database_sha256': _sha(p._json(database)), 'ca_sha256': database['tls_ca_sha256']})
    lock = p._json({'version': 1, 'state': 'WEB_FRESH_FINALIZED', 'instance': nonce})
    activation = _sha(p._read_file(web / 'includes/installation/activation.php'))
    pointer = ("<?php\ndeclare(strict_types=1);\n// Generated root-owned activation pointer. No credential.\n"
        "try {\n    $a=__DIR__.'/installation/activation.php';\n"
        f"    if (@hash_file('sha256',$a)!=='{activation}') throw new RuntimeException();\n"
        "    @require_once $a;\n"
        f"    hestia_installation_activate(hex2bin('{str(web).encode().hex()}'),hex2bin('{str(directory).encode().hex()}'),"
        f"{gid},'{_sha(seal)}','{_sha(lock)}','{_sha(loader)}');\n"
        "} catch (Throwable $error) {\n    if (PHP_SAPI !== 'cli') { http_response_code(503); header('Cache-Control: no-store'); exit('HESTIA_ACTIVATION_NOT_READY'); }\n"
        "    throw new RuntimeException('HESTIA_ACTIVATION_NOT_READY');\n}\n").encode()
    return seal, lock, pointer


def _revoke(conf: int, seal: bytes, gid: int) -> None:
    """Remove only our exact seal on a detected error, never SQL or credentials."""
    try:
        require(hmac.compare_digest(_read(conf, 'seal.json', gid), seal), 'SEAL_CHANGED')
        os.unlink('seal.json', dir_fd=conf)
        os.fsync(conf)
    except FileNotFoundError:
        pass


def _completed(conf: int, webfd: int, inc: int, gid: int, *, commit: str = WEB_COMMIT) -> dict:
    release = get_release(commit)
    receipt = _json_read(conf, 'finalized.json', gid)
    require(set(receipt) == {'version', 'state', 'source_commit', 'runtime_sha256', 'seal_sha256', 'lock_sha256', 'pointer_sha256'}
            and type(receipt['version']) is int and receipt['version'] == 1 and receipt['state'] == 'WEB_FRESH_FINALIZED'
            and receipt['source_commit'] == release.commit and receipt['runtime_sha256'] == release.runtime_sha256, 'FINALIZATION_RECEIPT_REQUIRED')
    for fd, name, field in ((conf, 'seal.json', 'seal_sha256'), (webfd, 'install.lock', 'lock_sha256'), (inc, 'db.php', 'pointer_sha256')):
        require(_sha(_read(fd, name, gid)) == receipt[field], 'FINALIZATION_INTEGRITY_FAILED')
    return receipt


def _result(observed: dict) -> dict:
    return {'state': 'WEB_FRESH_FINALIZED', 'code': 'OK', 'result': {
        'scope': 'WEB_FRESH_FINALIZED', 'configuration_activated': True, 'installation_sealed': True,
        'application_installed': False, 'system_qualification_required': True, **observed}}


class FinalizationStep:
    """Private trusted-host API. Nothing is registered in the HTTP/plan router."""
    def __init__(self, runtime: p.PhpRuntime, source: Path, *, repository: str, commit: str):
        require(repository == p.WEB_REPOSITORY, 'SOURCE_PIN_MISMATCH')
        try: self.release = get_release(commit)
        except ValueError: raise FinalizationError('SOURCE_PIN_MISMATCH') from None
        self.runtime, self.source = runtime, Path(source)

    def _sources(self, web: Path) -> None:
        require(_runtime_digest(self.source) == self.release.runtime_sha256 and _runtime_digest(web) == self.release.runtime_sha256, 'SOURCE_PIN_MISMATCH')

    def finalize(self, payload: dict, *, config_root: Path, confirmed: bool, cancel=None) -> dict:
        config = _configuration(payload, fresh=True)
        require(confirmed is True, 'CONFIRMATION_REQUIRED')
        started = False
        seal = None
        with ExitStack() as stack:
            try:
                gid, web, directory, conf, webfd, inc = _open(self.runtime, config, config_root, stack)
                self._sources(web)
                for fd, name in ((inc, 'db.php'), (webfd, 'install.lock'), (conf, 'assistant.json'), (conf, 'seal.json'),
                                 (conf, 'finalized.json'), (conf, 'finalization.attempt')):
                    fs._absent(fd, name)
                database, loader, ca = _prepared(config, payload, directory, conf, gid)
                _database_receipt(self.runtime, database)
                initial = _sql(self.runtime, self.source, database, payload, config, ca, desired=False, mutate=False, cancel=cancel)
                require(initial['assistant_enabled'] is False, 'DATABASE_NOT_PREPARED')
                require(cancel is None or not cancel.is_set(), 'INTERRUPTED')
                nonce = os.urandom(16).hex()
                seal, lock, pointer = _documents(web, directory, gid, database, loader, nonce)
                # Any creation attempt, even a failing fsync, prohibits replay.
                started = True
                _write(conf, 'finalization.attempt', p._json({'version': 1, 'state': 'STARTED', 'instance': nonce}), 0, mode=0o600)
                key = payload['secrets']['openai_api_key'] if config['assistant']['desired_enabled'] else ''
                _write(conf, 'assistant.json', p._json({'version': 1, 'openai_api_key': key}), gid, mode=0o660)
                desired = bool(config['assistant']['desired_enabled'])
                _sql(self.runtime, self.source, database, payload, config, ca, desired=desired, mutate=True, cancel=cancel)
                observed = _probe(self.runtime, config, directory, gid, active=False, cancel=cancel)
                require(observed['setting_enabled'] is desired and observed['key_configured'] is bool(key)
                        and observed['assistant_enabled'] is desired, 'FINALIZATION_COHERENCE_FAILED')
                self._sources(web)
                require(_prepared(config, payload, directory, conf, gid) == (database, loader, ca), 'CONFIGURATION_INTEGRITY_FAILED')
                require(cancel is None or not cancel.is_set(), 'INTERRUPTED')
                _write(inc, 'db.php', pointer, gid)
                _write(webfd, 'install.lock', lock, gid)
                _write(conf, 'seal.json', seal, gid)  # Activation becomes possible only here.
                observed = _probe(self.runtime, config, directory, gid, active=True, cancel=cancel)
                require(observed['setting_enabled'] is desired and observed['key_configured'] is bool(key)
                        and observed['assistant_enabled'] is desired, 'FINALIZATION_COHERENCE_FAILED')
                receipt = {'version': 1, 'state': 'WEB_FRESH_FINALIZED', 'source_commit': self.release.commit, 'runtime_sha256': self.release.runtime_sha256,
                           'seal_sha256': _sha(seal), 'lock_sha256': _sha(lock), 'pointer_sha256': _sha(pointer)}
                _write(conf, 'finalized.json', p._json(receipt), gid)
                _completed(conf, webfd, inc, gid, commit=self.release.commit)
                return _result(observed)
            except BaseException as error:
                if started:
                    if seal is not None:
                        try:
                            _revoke(conf, seal, gid)
                        except Exception:
                            return {'state': 'MANUAL_ACTION', 'code': 'FINALIZATION_REVOCATION_UNCERTAIN', 'result': None}
                    return {'state': 'MANUAL_ACTION', 'code': 'FINALIZATION_INCOMPLETE', 'result': None}
                if isinstance(error, (FinalizationError, SystemExit, GeneratorExit)):
                    raise
                raise FinalizationError('FINALIZATION_PREFLIGHT_FAILED') from None

    def observe(self, payload: dict, *, config_root: Path, cancel=None) -> dict:
        """Read-only observation. A lock or seal alone is never a success receipt."""
        config = _configuration(payload, fresh=False)
        with ExitStack() as stack:
            try:
                gid, web, directory, conf, webfd, inc = _open(self.runtime, config, config_root, stack)
                self._sources(web)
                _prepared(config, payload, directory, conf, gid)
                _completed(conf, webfd, inc, gid, commit=self.release.commit)
                self._pending_edits(conf)
                return _result(_probe(self.runtime, config, directory, gid, active=True, cancel=cancel))
            except Exception:
                return {'state': 'MANUAL_ACTION', 'code': 'FINALIZATION_OBSERVATION_UNAVAILABLE', 'result': None}

    @staticmethod
    def _pending_edits(conf: int) -> None:
        names = os.listdir(conf)
        require(len(names) <= 2048, 'ASSISTANT_JOURNAL_LIMIT')
        for name in names:
            if re.fullmatch(r'assistant-[a-f0-9]{32}\.attempt', name):
                request = _json_read(conf, name, 0, mode=0o600)
                done = _json_read(conf, name[:-8] + '.done', 0, mode=0o600)
                require(p._json(request) == p._json({'version': 1, 'request_id': name[10:-8]})
                        and p._json(done) == p._json(request), 'ASSISTANT_MANUAL_ACTION')

    def configure_assistant(self, payload: dict, *, config_root: Path, confirmed: bool, cancel=None) -> dict:
        """Existing finalized managed instance only; never a legacy adoption/upgrade."""
        config = _configuration(payload, fresh=False)
        require(confirmed is True, 'CONFIRMATION_REQUIRED')
        started = False
        with ExitStack() as stack:
            try:
                gid, web, directory, conf, webfd, inc = _open(self.runtime, config, config_root, stack)
                self._sources(web)
                _prepared(config, payload, directory, conf, gid)
                _completed(conf, webfd, inc, gid, commit=self.release.commit)
                # Stable private lock serializes edits without deleting crash evidence.
                try:
                    _write(conf, 'assistant-edit.lock', b'', 0, mode=0o600)
                except FileExistsError:
                    pass
                _read(conf, 'assistant-edit.lock', 0, mode=0o600)
                lockfd = os.open('assistant-edit.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=conf)
                stack.callback(os.close, lockfd)
                fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._pending_edits(conf)
                observed = _probe(self.runtime, config, directory, gid, active=True, cancel=cancel)
                action = config['assistant']['action']
                if action == 'preserve':
                    return _result(observed)
                require(cancel is None or not cancel.is_set(), 'INTERRUPTED')
                request_id = os.urandom(16).hex()
                journal = p._json({'version': 1, 'request_id': request_id})
                started = True
                _write(conf, 'assistant-' + request_id + '.attempt', journal, 0, mode=0o600)
                observed = _probe(self.runtime, config, directory, gid, active=True, action=action,
                                   key=payload['secrets']['openai_api_key'], cancel=cancel)
                require(observed['setting_enabled'] is (action == 'configure')
                        and (action != 'configure' or observed['key_configured']), 'ASSISTANT_COHERENCE_FAILED')
                _write(conf, 'assistant-' + request_id + '.done', journal, 0, mode=0o600)
                self._pending_edits(conf)
                return _result(observed)
            except Exception as error:
                if started:
                    return {'state': 'MANUAL_ACTION', 'code': 'ASSISTANT_UPDATE_INCOMPLETE', 'result': None}
                if isinstance(error, FinalizationError):
                    raise
                raise FinalizationError('ASSISTANT_PREFLIGHT_FAILED') from None
