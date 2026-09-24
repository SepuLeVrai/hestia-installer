"""Private Phase 5B composition, never imported or registered by the HTTP wizard.

The host prepares PHP, isolated identities, the exact acquired Web tree and
protected directories (5D). This API configures the database/application and
seals that boundary; it never installs packages or claims HTTP/system readiness.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import pwd
import re
import stat
import tempfile
import time
from pathlib import Path

from installer import php_transport as p
from installer import database_config as fs
from installer.model import strict_json_loads
from installer.transaction import _private_directory
from installer.web_config import validate_web_configuration


class Phase5BError(RuntimeError):
    """Only closed codes may leave this boundary."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise Phase5BError(code)


class SqlAuthority:
    """Private authority used for metadata audit and managed resource creation.

    It is NEVER a runtime account and is NEVER persisted by this module.
    Existing/remote modes require read access to mysql privilege metadata only;
    managed additionally needs CREATE DATABASE/USER/GRANT/DROP USER authority.
    """
    __slots__ = ('_user', '_password')

    def __init__(self, user: str, password: str):
        require(type(user) is str and re.fullmatch(r'[A-Za-z0-9_]{1,32}', user) is not None, 'AUTHORITY_INVALID')
        # Reuse exact byte/control validation, without forbidding an authority named root.
        try:
            p.ProvisioningCredentials('authority_check', password)
        except Exception:
            raise Phase5BError('AUTHORITY_INVALID') from None
        self._user, self._password = user, password

    def __repr__(self):
        return '<SqlAuthority private>'

    def __reduce__(self):
        raise TypeError('Private authority cannot be serialized')


SQL_ERRORS = frozenset({'REQUEST_INVALID', 'ACCOUNT_SEPARATION_REQUIRED', 'ACCOUNT_POLICY_REJECTED',
    'ACCOUNT_TARGET_OCCUPIED', 'DATABASE_TARGET_OCCUPIED', 'DATABASE_NOT_EMPTY', 'DATABASE_CONNECTION_FAILED',
    'DATABASE_TLS_REQUIRED', 'DATABASE_TLS_UNAVAILABLE', 'SERVER_PROFILE_UNSUPPORTED', 'TARGET_IDENTITY_MISMATCH',
    'INSTALLATION_BUSY', 'AUTHORITY_PRIVILEGES_REQUIRED', 'ADMIN_INPUT_INVALID', 'SQL_MANUAL_ACTION', 'SCHEMA_INVALID', 'PRIVATE_BRIDGE_FAILED'})


def configuration(payload: dict, *, fresh: bool = True) -> dict:
    try:
        config = validate_web_configuration(payload)['configuration']
        require(not fresh or config['mode'] == 'fresh', 'UPGRADE_REQUIRES_PHASE5C')
        require(':' not in config['web']['webroot'], 'PATH_REJECTED')
        p.ProvisioningCredentials(config['database']['user'], payload['secrets']['database_password'])
        if config['database']['host'] == 'localhost':
            config['database']['host'] = '127.0.0.1'
        return config
    except Phase5BError:
        raise
    except Exception:
        raise Phase5BError('INPUT_REJECTED') from None


# SHA-256 of canonical {relative_path: {sha256, mode}} for the exact runtime
# distribution. A compiled trusted pin, never a user-supplied manifest. This
# retains the same closed-file guarantee without shipping a bulky hash catalog.
WEB_RUNTIME_SHA256 = "d150401ea9af63692b34ac30ab39fac1c12925bd7088cb826445f8c40af4765e"
_EXCLUDED = {'.git', '.github', 'docs', 'tests', 'scripts'}


def verify_source(root: Path, *, activated: bool = False) -> dict:
    """Verify every runtime file, mode, owner and ancestor before any execution.

    The aggregate digest rejects added, removed or modified files. Documentation,
    test and SCM root directories are not executed or copied. Generated db.php
    and install.lock are checked separately when operating on a sealed instance.
    """
    with fs._directory(root):
        deadline = time.monotonic() + 30
        manifest = {}; total = 0; entries = 0; pending = [root]
        while pending:
            current = pending.pop()
            with fs._directory(current), os.scandir(current) as scan:
                for entry in scan:
                    require(time.monotonic() < deadline, 'SOURCE_REJECTED')
                    relative = Path(entry.path).relative_to(root)
                    if current == root and entry.name in _EXCLUDED:
                        continue
                    entries += 1
                    require(entries <= 10000 and len(relative.parts) <= 32 and not entry.is_symlink(), 'SOURCE_REJECTED')
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path)); continue
                    name = relative.as_posix()
                    if activated and name in {'includes/db.php', 'install.lock'}:
                        continue
                    data = p._read_file(Path(entry.path)); total += len(data)
                    require(total <= p.MAX_BUNDLE, 'SOURCE_REJECTED')
                    manifest[name] = {'sha256':hashlib.sha256(data).hexdigest(),
                        'mode':format(stat.S_IMODE(entry.stat(follow_symlinks=False).st_mode), '04o')}
        require(hashlib.sha256(p._json(manifest)).hexdigest() == WEB_RUNTIME_SHA256, 'SOURCE_PIN_MISMATCH')
    return manifest


def _resource(stage: Path, name: str, destination: str, gid: int) -> None:
    data = p._read_file(Path(__file__).parent / 'private' / name)
    path = stage / destination
    with path.open('xb') as out:
        out.write(data)
    os.chown(path, 0, gid); path.chmod(0o640)


def _snapshot(root: Path, stage: Path, gid: int) -> None:
    manifest = verify_source(root, activated=True)
    names = [n for n in manifest if n.startswith('vendor/') or n.startswith('includes/installation/')
             or n in {'includes/version.php', 'includes/assistant/config.php', 'sql/schema.sql'}]
    total = 0
    for name in names:
        data = p._read_file(root / name)
        total += len(data)
        require(total <= p.MAX_BUNDLE and hashlib.sha256(data).hexdigest() == manifest[name]['sha256'], 'SOURCE_PIN_MISMATCH')
        target = stage / 'engine' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            output.write(data)
        os.chown(target, 0, gid); target.chmod(0o640)
    for current, _, _ in os.walk(stage):
        os.chown(current, 0, gid); os.chmod(current, 0o750)


def _sql_response(code: int, raw: bytes, request: dict) -> dict:
    try:
        r = strict_json_loads(raw)
        require(type(r) is dict and set(r) == {'version','request_id','operation','ok','result','error'}
                and type(r['version']) is int and r['version'] == 2 and r['request_id'] == request['request_id']
                and r['operation'] == request['operation'] and type(r['ok']) is bool, 'PROTOCOL_REJECTED')
        if not r['ok']:
            require(code == 20 and r['result'] is None and type(r['error']) is str and r['error'] in SQL_ERRORS, 'PROTOCOL_REJECTED')
            raise Phase5BError(r['error'])
        expected = {'preflight':'TARGET_VERIFIED','fresh':'DATABASE_CONFIGURED','release':'TEMPORARY_ACCOUNT_RELEASED'}[request['operation']]
        require(code == 0 and r['error'] is None and r['result'] == {'state':expected}, 'PROTOCOL_REJECTED')
        return r['result']
    except Phase5BError:
        raise
    except Exception:
        raise Phase5BError('PROTOCOL_REJECTED') from None


def _sql(runtime, root: Path, config: dict, payload: dict, migration, authority, operation: str, ca: bytes | None, cancel=None):
    with tempfile.TemporaryDirectory(prefix='b5-', dir=runtime.run_root) as temporary:
        stage = Path(temporary)
        _snapshot(root, stage, runtime.worker_gid)
        _resource(stage, 'phase5b_bridge.php', 'bridge.php', runtime.worker_gid)
        _resource(stage, 'phase5b_policy.php', 'phase5b_policy.php', runtime.worker_gid)
        db = dict(config['database']); db.pop('user')
        if ca is not None:
            ca_fd = os.open(stage, fs._DIRECTORY_FLAGS)
            try: fs._write(ca_fd, 'ca.pem', ca, runtime.worker_gid)
            finally: os.close(ca_fd)
            db['tls_ca_file'] = str(stage / 'ca.pem')
        req = {'version':2,'operation':operation,'confirmed':operation!='preflight','request_id':os.urandom(16).hex(),
               'database':db,'application':{'user':config['database']['user'],'password':payload['secrets']['database_password']},
               'migration':{'user':migration._user,'password':migration._password},
               'authority':{'user':authority._user,'password':authority._password},
               'administrator':{**config['administrator'],'password':payload['secrets']['admin_password']},
               'assistant_enabled':config['assistant']['desired_enabled']}
        code, raw = p._exchange(p._command(runtime, stage), p._json(req), stage, runtime.timeout_seconds, cancel)
        return _sql_response(code, raw, req)


def _loader(data_path: Path, gid: int) -> bytes:
    return ("<?php\ndeclare(strict_types=1);\nrequire_once __DIR__ . '/installation/managed_config.php';\n"
            + "hestia_managed_load(hex2bin('" + str(data_path).encode().hex() + "'), " + str(gid) + ");\n").encode()


def _runtime_check(runtime, config: dict, payload: dict | None, slot: Path, *, operation='verify', enabled=False, cancel=None) -> dict:
    web = pwd.getpwnam(config['web']['service_user'])
    with tempfile.TemporaryDirectory(prefix='b5-check-', dir=runtime.run_root) as temporary:
        stage = Path(temporary); os.chown(stage, 0, web.pw_gid); stage.chmod(0o750)
        _resource(stage,'phase5b_verify.php','bridge.php',web.pw_gid)
        command = p._command(runtime,stage)
        command = [('--reuid='+str(web.pw_uid)) if a.startswith('--reuid=') else
                   ('--regid='+str(web.pw_gid)) if a.startswith('--regid=') else
                   ('open_basedir='+':'.join(map(str,[stage,Path(config['web']['webroot']),slot])))
                   if a.startswith('open_basedir=') else a for a in command]
        req = {'version':2,'request_id':os.urandom(16).hex(),'operation':operation,'webroot':config['web']['webroot'],
               'configroot':str(slot),'assistant_enabled':enabled,'administrator':None if payload is None else
               {**config['administrator'],'password':payload['secrets']['admin_password']}}
        code, raw = p._exchange(command,p._json(req),stage,runtime.timeout_seconds,cancel)
        r = strict_json_loads(raw)
        require(type(r) is dict and set(r) == {'version','request_id','operation','ok','result','error'}
                and type(r['version']) is int and r['version'] == 2 and r['request_id'] == req['request_id']
                and r['operation'] == 'runtime' and r['ok'] is True and code == 0 and r['error'] is None, 'RUNTIME_VERIFICATION_FAILED')
        result = r['result']
        require(type(result) is dict and set(result) == {'state','assistant_enabled','key_configured','api_access_tested'}
                and result['state'] == 'RUNTIME_VERIFIED' and type(result['assistant_enabled']) is bool
                and type(result['key_configured']) is bool and result['api_access_tested'] is False,
                'RUNTIME_VERIFICATION_FAILED')
        if operation != 'assistant_observe':
            require(result['assistant_enabled'] is enabled, 'RUNTIME_VERIFICATION_FAILED')
        return result


def _ca(config: dict) -> bytes | None:
    if config['database']['mode'] != 'remote':
        return None
    path = Path(config['database']['tls_ca_file'])
    require(':' not in str(path), 'CA_REJECTED')
    with fs._directory(path.parent):
        data = p._read_file(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try: fs._no_acl(fd)
        finally: os.close(fd)
    require(0 < len(data) <= 16384 and b'-----BEGIN CERTIFICATE-----' in data
            and b'PRIVATE KEY' not in data, 'CA_REJECTED')
    return data


def _state(fd: int, state: str) -> None:
    raw = p._json({'version':2,'state':state})+b'\n'
    require(os.write(fd,raw) == len(raw), 'STATE_WRITE_FAILED')
    os.fsync(fd)


def _validate_private(config: dict, payload: dict, migration, authority) -> None:
    require(type(migration) is p.ProvisioningCredentials and type(authority) is SqlAuthority, 'CREDENTIALS_REQUIRED')
    require(len({config['database']['user'],migration._user,authority._user})==3
            and len({payload['secrets']['database_password'],migration._password,authority._password})==3,
            'ACCOUNT_SEPARATION_REQUIRED')


def complete_fresh(runtime: p.PhpRuntime, payload: dict, migration: p.ProvisioningCredentials, authority: SqlAuthority,
                   *, config_root: Path, confirmed: bool, cancel=None) -> dict:
    """Finish ONLY Phase 5B on a trusted pre-deployed Web tree.

    Source, target, authority/privileges and consent are checked before mutation.
    After durable reservation, any uncertainty is MANUAL_ACTION, never retry.
    No destructive cleanup, implicit upgrade, package install or public binding.
    """
    config = configuration(payload)
    require(confirmed is True,'CONFIRMATION_REQUIRED')
    _validate_private(config,payload,migration,authority)
    marker = None; reserved = False
    try:
        root = Path(config['web']['webroot'])
        require(isinstance(config_root,Path) and ':' not in str(config_root) and config_root not in {root,Path('/'),Path('/srv'),Path('/var/www')}
                and root not in config_root.parents and not str(config_root).startswith(('/srv/','/var/www/')),
                'CONFIGURATION_PUBLIC_PATH_REFUSED')
        with fs._directory(runtime.run_root): pass
        p._runtime(runtime,config['web']['service_user'])
        gid = fs._web_group(config['web']['service_user'],runtime)
        verify_source(root)
        ca = _ca(config)
        slot_name = fs.configuration_slot(config); slot = config_root / slot_name
        with fs._directory(config_root,readable_by=gid) as parent, fs._directory(root) as webfd, fs._directory(root/'includes') as incfd:
            fs._absent(parent,slot_name); fs._absent(webfd,'install.lock'); fs._absent(incfd,'db.php')
            require(cancel is None or not cancel.is_set(),'INTERRUPTED')
            _sql(runtime,root,config,payload,migration,authority,'preflight',ca,cancel)
            target = config['database']
            key = hashlib.sha256(p._json([target['host'],target['port'],target['name'].lower()])).hexdigest()
            with _private_directory(runtime.state_root,create=True) as statefd:
                try:
                    marker=os.open('fresh-'+key+'.attempt',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=statefd)
                except FileExistsError:
                    raise Phase5BError('FRESH_REPLAY_BLOCKED') from None
                _state(marker,'RESERVING'); os.fsync(statefd)
            # Same slot reservation as 5B2.2a: staged or incomplete configurations
            # cannot be silently adopted, overwritten or presumed trustworthy.
            os.mkdir(slot_name,0o700,dir_fd=parent);reserved=True;os.fsync(parent)
            child=os.open(slot_name,fs._DIRECTORY_FLAGS,dir_fd=parent)
            try:
                fs._no_acl(child)
                database={**config['database'],'password':payload['secrets']['database_password']}
                if ca is not None:
                    fs._write(child,'ca.pem',ca,gid);database['tls_ca_file']=str(slot/'ca.pem')
                data={'version':2,'database':database,'assistant_key':payload['secrets']['openai_api_key'] if config['assistant']['desired_enabled'] else ''}
                fs._write(child,'instance.json',p._json(data),gid);os.fsync(child)
                _state(marker,'SQL_DISPATCHING')
                _sql(runtime,root,config,payload,migration,authority,'fresh',ca,cancel)
                _state(marker,'SQL_CONFIGURED')
                # Recheck the distribution before exposing its configuration.
                verify_source(root)
                fs._absent(webfd,'install.lock');fs._absent(incfd,'db.php')
                os.fchown(child,0,gid);os.fchmod(child,0o750);os.fsync(child)
                fs._write(incfd,'db.php',_loader(slot/'instance.json',gid),gid);os.fsync(incfd)
                _runtime_check(runtime,config,payload,slot,enabled=config['assistant']['desired_enabled'],cancel=cancel)
                if database['mode']=='managed':
                    _sql(runtime,root,config,payload,migration,authority,'release',ca,cancel)
                _state(marker,'RUNTIME_VERIFIED')
                # Seals the configured boundary only. HTTP/NGINX/service tests are 5D.
                seal={'version':2,'scope':'WEB_CONFIGURED','application_installed':False,
                      'runtime_verified':True,'api_access_tested':False,'http_verified':False}
                fs._write(webfd,'install.lock',p._json(seal)+b'\n',gid);os.fsync(webfd)
                fs._write(child,'receipt.json',p._json(seal),gid);os.fsync(child);os.fsync(parent)
                _state(marker,'WEB_CONFIGURED')
            finally:
                os.close(child)
        return {**seal,'assistant_enabled':config['assistant']['desired_enabled'],
                'temporary_account_released':config['database']['mode']=='managed'}
    except Exception as error:
        if marker is not None or reserved:
            raise Phase5BError('MANUAL_ACTION_REQUIRED') from None
        if isinstance(error,Phase5BError):
            raise
        raise Phase5BError('LOCAL_PREFLIGHT_FAILED') from None
    finally:
        if marker is not None: os.close(marker)


def _read_managed(slot: Path, gid: int) -> dict:
    path = slot / 'instance.json'
    with fs._directory(slot, readable_by=gid):
        data = p._read_file(path)
        st = path.lstat()
        require(st.st_gid == gid and stat.S_IMODE(st.st_mode) == 0o640, 'MANAGED_CONFIG_REJECTED')
        fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try: fs._no_acl(fd)
        finally: os.close(fd)
    r = strict_json_loads(data)
    require(type(r) is dict and set(r)=={'version','database','assistant_key'} and type(r['version']) is int
            and r['version']==2 and type(r['database']) is dict and p._json(r)==data
            and type(r['assistant_key']) is str and (not r['assistant_key'] or re.fullmatch(r'[A-Za-z0-9_.-]{20,500}',r['assistant_key'])),
            'MANAGED_CONFIG_REJECTED')
    return r


def update_assistant(runtime: p.PhpRuntime, payload: dict, *, config_root: Path, confirmed: bool, cancel=None) -> dict:
    """Apply preserve/configure/disabled to an already sealed managed instance.

    No schema upgrade or legacy PHP-secret evaluation. The existing managed
    connection must match. Empty replacement means preserve, never erase.
    Any interrupted mutation leaves a durable block for manual inspection/5C.
    """
    config = configuration(payload, fresh=False)
    require(config['mode']=='upgrade' and confirmed is True,'ASSISTANT_CONFIRMATION_REQUIRED')
    state = None; dispatched = False
    try:
        root = Path(config['web']['webroot']); gid = fs._web_group(config['web']['service_user'],runtime)
        require(isinstance(config_root,Path) and ':' not in str(config_root) and root not in config_root.parents
                and config_root not in {root,Path('/'),Path('/srv'),Path('/var/www')} and not str(config_root).startswith(('/srv/','/var/www/')),'CONFIGURATION_PUBLIC_PATH_REFUSED')
        with fs._directory(runtime.run_root): pass
        p._runtime(runtime,config['web']['service_user'])
        verify_source(root, activated=True)
        slot_name = fs.configuration_slot(config); slot = config_root / slot_name
        with fs._directory(config_root,readable_by=gid), fs._directory(root), fs._directory(root/'includes'), fs._directory(slot,readable_by=gid) as slotfd:
            require(p._read_file(root/'includes/db.php')==_loader(slot/'instance.json',gid),'MANAGED_CONFIG_REJECTED')
            seal={'version':2,'scope':'WEB_CONFIGURED','application_installed':False,'runtime_verified':True,'api_access_tested':False,'http_verified':False}
            require(p._read_file(root/'install.lock')==p._json(seal)+b'\n' and p._read_file(slot/'receipt.json')==p._json(seal),'MANAGED_CONFIG_REJECTED')
            with _private_directory(runtime.state_root,create=True) as directory:
                name='assistant-'+slot_name+'.state'; new=False
                try:
                    state=os.open(name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=directory);new=True
                except FileExistsError:
                    state=os.open(name,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=directory)
                st=os.fstat(state)
                require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and st.st_nlink==1 and stat.S_IMODE(st.st_mode)==0o600,'STATE_REJECTED')
                try: fcntl.flock(state,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError: raise Phase5BError('ASSISTANT_BUSY') from None
                previous=os.read(state,65)
                require(new or previous==b'DONE\n','ASSISTANT_MANUAL_ACTION')
                if new:
                    # No mutation has started. A refused observation may be retried;
                    # only a durable DISPATCHING marker prohibits a blind retry.
                    os.write(state,b'DONE\n');os.fsync(state);os.fsync(directory)
                data=_read_managed(slot,gid)
                current=data['database']; expected=config['database']
                require(set(current)==set(expected)|{'password'} and all(current[k]==expected[k] for k in ('host','port','name','user'))
                        and hmac.compare_digest(current['password'].encode(),payload['secrets']['database_password'].encode()),'MANAGED_TARGET_MISMATCH')
                actual=_runtime_check(runtime,config,None,slot,operation='assistant_observe',cancel=cancel)
                action=payload['assistant']['action']
                if action=='preserve' or (action=='configure' and payload['secrets']['openai_api_key']==''):
                    # Observation is from the actual runtime, not the desired input state.
                    os.lseek(state,0,0);os.write(state,b'DONE\n');os.ftruncate(state,5);os.fsync(state);os.fsync(directory)
                    return {**actual,'state':'ASSISTANT_PRESERVED','application_installed':False}
                require(cancel is None or not cancel.is_set(),'INTERRUPTED')
                os.lseek(state,0,0);os.write(state,b'DISPATCHING\n');os.ftruncate(state,12);os.fsync(state);os.fsync(directory);dispatched=True
                enabled=action=='configure'
                if not enabled:
                    _runtime_check(runtime,config,None,slot,operation='assistant_set',enabled=False,cancel=cancel)
                data['assistant_key']=payload['secrets']['openai_api_key'] if enabled else ''
                name='instance-'+os.urandom(16).hex()+'.tmp'
                fs._write(slotfd,name,p._json(data),gid)
                # The old inode is never opened for write or followed; directory is pinned/root-owned.
                os.replace(name,'instance.json',src_dir_fd=slotfd,dst_dir_fd=slotfd);os.fsync(slotfd)
                actual=_runtime_check(runtime,config,None,slot,operation='assistant_set',enabled=enabled,cancel=cancel)
                os.lseek(state,0,0);os.write(state,b'DONE\n');os.ftruncate(state,5);os.fsync(state);os.fsync(directory)
                return {**actual,'state':'ASSISTANT_CONFIGURED','application_installed':False}
    except Exception as error:
        if dispatched: raise Phase5BError('ASSISTANT_MANUAL_ACTION') from None
        if isinstance(error,Phase5BError): raise
        raise Phase5BError('ASSISTANT_PREFLIGHT_FAILED') from None
    finally:
        if state is not None: os.close(state)
