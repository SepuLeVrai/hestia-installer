"""5C2 private, scoped backup with an actual isolated restoration proof.

No upgrade, original-schema restore, legacy adoption, Web activation or UI route.
Supported files are a root-owned immutable deployment plus the cooperatively
locked Assistant data. Unknown mutable file trees fail closed, not get omitted.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import hmac
import io
import os
import re
import shutil
import stat
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from installer import backup_runtime as br
from installer import database_config as fs
from installer import database_step as d
from installer import finalization as f
from installer import php_transport as p
from installer import upgrade_preflight as u
from installer.model import strict_json_loads

MAX_FILES = 10000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES_BYTES = 128 * 1024 * 1024
EXCLUDED = frozenset({'.git', '.github', '.quality', '__pycache__'})
ERRORS = frozenset({'REQUEST_INVALID','SQL_TARGET_INVALID','SQL_CA_INVALID','SQL_CREDENTIAL_INVALID','SQL_DRIVER_REQUIRED',
    'SQL_CONNECTION_FAILED','SQL_TLS_CONNECTION_FAILED','BACKUP_PROFILE_REJECTED','BACKUP_AUTHORITY_REJECTED','AUDIT_UNAVAILABLE',
    'BACKUP_SPECIAL_OBJECTS_UNSUPPORTED','BACKUP_LIMIT','BACKUP_CHANNEL_FAILED','BACKUP_SOURCE_CHANGED','BACKUP_ARCHIVE_INVALID',
    'BACKUP_VERIFIER_VERSION_MISMATCH','BACKUP_VERIFIER_NOT_ISOLATED','BACKUP_VERIFIER_TARGET_OCCUPIED','BACKUP_RESTORE_WARNING',
    'BACKUP_RESTORE_MISMATCH','BACKUP_VERIFIER_CLEANUP_FAILED','BACKUP_WORKER_FAILED','BACKUP_TRIGGER_PROFILE_REJECTED',
    'BACKUP_DEFINER_PROFILE_REJECTED','BACKUP_DEFINER_MISSING','BACKUP_RESCUE_PROFILE_REJECTED','BACKUP_TRIGGER_RESTORE_MISMATCH','BACKUP_ROW_ORDER_COLLISION','BACKUP_NUMERIC_UNSUPPORTED','BACKUP_FOREIGN_KEY_MISMATCH','BACKUP_TRIGGER_SMOKE_FAILED'})


class UpgradeBackupError(RuntimeError):
    """Fixed diagnostic, never chains a credential-bearing exception."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise UpgradeBackupError(code)


@dataclass(frozen=True)
class BackupVerification:
    _canonical: bytes = field(repr=False)

    def report(self) -> dict:
        return strict_json_loads(self._canonical)


def _metadata(info: os.stat_result) -> dict:
    return {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid}


def _file(path: Path, *, assistant: bool = False) -> tuple[bytes, dict]:
    """Only trusted-owner regular files, pinned inode, no links, ACL or races."""
    with fs._directory(path.parent) as directory:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and before.st_size <= MAX_FILE_BYTES and not before.st_mode & (0o7002 if assistant else 0o7022),
                'BACKUP_FILE_PROFILE_UNSUPPORTED')
            fs._no_acl(fd)
            data = bytearray()
            while len(data) <= MAX_FILE_BYTES:
                block = os.read(fd, min(65536, MAX_FILE_BYTES+1-len(data)))
                if not block:
                    break
                data.extend(block)
            after = os.fstat(fd)
            fields = ('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
            named = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            require(len(data) == before.st_size and all(getattr(before,k) == getattr(after,k) for k in fields)
                and (before.st_ino,before.st_dev) == (named.st_ino,named.st_dev), 'BACKUP_FILES_CHANGED')
            return bytes(data), _metadata(before)
        finally:
            os.close(fd)


def _new_file(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        offset = 0
        while offset < len(data):
            count = os.write(fd, data[offset:offset+65536])
            require(count > 0, 'BACKUP_DISK_FAILED')
            offset += count
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    finally:
        os.close(fd)


def _scan(roots: dict[str, Path], *, destination: Path | None = None, cancel=None) -> list[dict]:
    """Capture all accepted files, never follow deployment links or silently skip data.

    Git/CI/cache metadata alone is outside the deployment backup contract.
    Names and file metadata stay in the PRIVATE manifest, not the public result.
    """
    records = []
    total = 0
    deadline = time.monotonic()+60
    for label, root in sorted(roots.items()):
        with fs._directory(root):
            pass
        for directory, dirs, files in os.walk(root, followlinks=False):
            require(len(records)<MAX_FILES and time.monotonic()<deadline,'BACKUP_FILES_LIMIT')
            require(cancel is None or not cancel.is_set(),'BACKUP_INTERRUPTED')
            parent = Path(directory)
            with fs._directory(parent) as parentfd:
                info = os.fstat(parentfd)
            relative = parent.relative_to(root).as_posix()
            require(len(relative.encode())<=2048 and not any(ord(c)<32 for c in relative),'BACKUP_FILE_PROFILE_UNSUPPORTED')
            records.append({'scope':label,'path':relative,'kind':'directory',**_metadata(info)})
            dirs[:] = sorted(x for x in dirs if x not in EXCLUDED)
            for name in dirs:
                require(not (parent/name).is_symlink(), 'BACKUP_FILE_PROFILE_UNSUPPORTED')
            for name in sorted(files):
                if name in EXCLUDED:
                    continue
                require(cancel is None or not cancel.is_set(), 'BACKUP_INTERRUPTED')
                require(len(records)<MAX_FILES and time.monotonic()<deadline, 'BACKUP_FILES_LIMIT')
                path = parent/name
                relative = path.relative_to(root).as_posix()
                require(len(relative.encode()) <= 2048 and not any(ord(c)<32 for c in relative), 'BACKUP_FILE_PROFILE_UNSUPPORTED')
                data, meta = _file(path, assistant=label=='configuration' and relative=='assistant.json')
                total += len(data)
                require(total <= MAX_FILES_BYTES, 'BACKUP_FILES_LIMIT')
                blob = f'{len(records):05d}.bin'
                records.append({'scope':label,'path':relative,'kind':'file','blob':blob,'bytes':len(data),
                    'sha256':f._sha(data),**meta})
                if destination is not None:
                    _new_file(destination/blob, data)
    return records


def _verify_files(slot: Path, records: list[dict]) -> None:
    """Restore the stored bytes/permissions under a non-served private directory.

    Absolute original activation pointers are preserved as DATA and never executed
    in this clone. An activation at new paths is a separate 5C3 operation.
    """
    restored = slot/'restored-files'
    restored.mkdir(mode=0o700)
    try:
        for record in records:
            require(record['scope'] in ('web','configuration'), 'BACKUP_MANIFEST_INVALID')
            relative = Path(record['path'])
            require(not relative.is_absolute() and '..' not in relative.parts, 'BACKUP_MANIFEST_INVALID')
            path = restored/record['scope']/relative
            if record['kind']=='directory':
                path.mkdir(parents=True, exist_ok=False)
                os.chown(path, record['uid'], record['gid'])
                path.chmod(record['mode'])
            else:
                require(record['kind']=='file' and type(record.get('blob')) is str
                    and re.fullmatch(r'[0-9]{5}\.bin',record['blob']) is not None,'BACKUP_MANIFEST_INVALID')
                data, _ = _file(slot/'files'/record['blob'])
                require(len(data)==record['bytes'] and hmac.compare_digest(f._sha(data),record['sha256']), 'BACKUP_FILES_CHANGED')
                _new_file(path, data)
                os.chown(path,record['uid'],record['gid'])
                path.chmod(record['mode'])
                raw, meta = _file(path, assistant=record['scope']=='configuration' and record['path']=='assistant.json')
                require(hmac.compare_digest(raw,data) and meta=={k:record[k] for k in ('uid','gid','mode')}, 'BACKUP_FILE_RESTORE_MISMATCH')
        # Re-read from disk and compare the complete restored tree, including empty directories.
        check = _scan({'web':restored/'web', 'configuration':restored/'configuration'})
        require(check==records, 'BACKUP_FILE_RESTORE_MISMATCH')
    finally:
        # Only our exclusive private clone, never the source or durable archive.
        shutil.rmtree(restored)


def _tail(path: Path, size: int = 8192) -> dict:
    with path.open('rb') as stream:
        stream.seek(max(0,path.stat().st_size-size))
        lines=stream.read(size).splitlines()
    require(bool(lines), 'BACKUP_ARCHIVE_INVALID')
    return strict_json_loads(lines[-1])


def _worker_stage(runtime: p.PhpRuntime, source: Path, stage: Path, ca: bytes | None) -> None:
    p._copy_bundle(source,stage,runtime.worker_gid,f.ENGINE_FILES,f.ENGINE_SHA256,'backup_bridge.php')
    with fs._directory(stage) as fd:
        f._write(fd,'sql_accounts_policy.php',p._read_file(Path(__file__).parent/'private/sql_accounts_policy.php'),runtime.worker_gid)
        f._write(fd,'trigger_definer.php',p._read_file(Path(__file__).parent/'private/trigger_definer.php'),runtime.worker_gid)
        if ca is not None:
            f._write(fd,'ca.pem',ca,runtime.worker_gid)


def _restore(runtime: p.PhpRuntime, source: Path, slot: Path, request_id: str, sha256: str, cancel=None, *, rescue: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix='bv-', dir=runtime.run_root) as tmp:
        stage = Path(tmp)
        _worker_stage(runtime,source,stage,None)
        target = stage/'database.ndjson'
        # Independent COPY, never a link to the durable archive or to the source DB.
        shutil.copyfile(slot/'database.ndjson',target)
        os.chown(target,0,runtime.worker_gid)
        target.chmod(0o640)
        require(_hash(target)==sha256,'BACKUP_ARCHIVE_INVALID')
        with br.verification_server(runtime,stage,cancel=cancel):
            request = {'version':1,'operation':'verify_rescue' if rescue else 'verify','request_id':request_id,'archive_sha256':sha256,'password':os.urandom(32).hex()}
            code, raw = p._exchange(p._command(runtime,stage),p._json(request),stage,runtime.timeout_seconds,cancel)
            try:
                response = strict_json_loads(raw)
                require(type(response) is dict and set(response)=={'version','request_id','ok','result','error'}
                    and type(response['version']) is int and response['version']==1 and response['request_id']==request_id
                    and type(response['ok']) is bool,'BACKUP_PROTOCOL_REJECTED')
                if not response['ok']:
                    require(code==20 and response['result'] is None and response['error'] in ERRORS,'BACKUP_PROTOCOL_REJECTED')
                    raise UpgradeBackupError(response['error'])
                result = response['result']
                require(code==0 and response['error'] is None and type(result) is dict and set(result)=={'tables','rows','logical_sha256','server_version','verification_objects_removed','canonical_triggers_verified','trigger_smoke_verified','foreign_keys_verified'}
                    and result['verification_objects_removed'] is True and type(result['canonical_triggers_verified']) is int and result['canonical_triggers_verified']==5
                    and type(result['trigger_smoke_verified']) is int and result['trigger_smoke_verified']==5
                    and type(result['foreign_keys_verified']) is int and 0<=result['foreign_keys_verified']<=4096,'BACKUP_PROTOCOL_REJECTED')
                require(type(result['tables']) is int and 6<=result['tables']<=512 and type(result['rows']) is str
                    and re.fullmatch(r'0|[1-9][0-9]{0,6}',result['rows']) is not None and int(result['rows'])<=1000000
                    and type(result['logical_sha256']) is str and re.fullmatch(r'[0-9a-f]{64}',result['logical_sha256']) is not None
                    and type(result['server_version']) is str and len(result['server_version'])<=200,'BACKUP_PROTOCOL_REJECTED')
                return result
            except UpgradeBackupError:
                raise
            except Exception:
                raise UpgradeBackupError('BACKUP_PROTOCOL_REJECTED') from None


def _hash(path: Path) -> str:
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1 and not before.st_mode&0o7022
            and before.st_size<=br.MAX_ARCHIVE,'BACKUP_ARCHIVE_INVALID')
        fs._no_acl(fd)
        digest=hashlib.sha256();total=0
        with os.fdopen(fd,'rb',closefd=False) as stream:
            for block in iter(lambda:stream.read(65536),b''):
                total+=len(block)
                require(total<=br.MAX_ARCHIVE,'BACKUP_ARCHIVE_INVALID')
                digest.update(block)
        after=os.fstat(fd)
        require((before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_size,after.st_mtime_ns,after.st_ctime_ns),'BACKUP_ARCHIVE_INVALID')
        return digest.hexdigest()
    finally:
        os.close(fd)


class UpgradeBackup:
    """Private SEALED_5B23 backup. No restore-to-original or upgrade method."""
    def __init__(self, runtime: p.PhpRuntime, source: Path, *, repository: str, commit: str):
        require(repository==p.WEB_REPOSITORY and commit==f.WEB_COMMIT,'SOURCE_PIN_MISMATCH')
        self.runtime,self.source=runtime,Path(source)

    def create_and_verify(self, payload: dict, authority: d.SqlAuthorityCredentials, *, config_root: Path,
                          backup_root: Path, confirmed: bool, allow_global_read_lock: bool, cancel=None) -> BackupVerification:
        return self._create(payload,authority,config_root=config_root,backup_root=backup_root,confirmed=confirmed,
                            allow_global_read_lock=allow_global_read_lock,cancel=cancel,orphan=None)

    def create_rescue_and_verify(self, payload: dict, authority: d.SqlAuthorityCredentials, *, config_root: Path,
                                backup_root: Path, expected_orphaned_definer: str, confirmed: bool,
                                allow_global_read_lock: bool, cancel=None) -> BackupVerification:
        """Capture the explicitly named historical defect without repairing it.

        Separate receipt and archive format: never certifies an operational
        source or authorizes apply. All ordinary object/file guards still apply.
        """
        require(type(expected_orphaned_definer) is str
            and re.fullmatch(r'[A-Za-z0-9_]{1,32}@127\.0\.0\.1',expected_orphaned_definer) is not None
            and expected_orphaned_definer.split('@')[0]!='root'
            and not expected_orphaned_definer.startswith('hdf_'),'BACKUP_RESCUE_PROFILE_REJECTED')
        return self._create(payload,authority,config_root=config_root,backup_root=backup_root,confirmed=confirmed,
                            allow_global_read_lock=allow_global_read_lock,cancel=cancel,orphan=expected_orphaned_definer)

    def _create(self, payload: dict, authority: d.SqlAuthorityCredentials, *, config_root: Path, backup_root: Path,
                confirmed: bool, allow_global_read_lock: bool, cancel, orphan: str | None) -> BackupVerification:
        started=False
        try:
            require(confirmed is True and allow_global_read_lock is True,'BACKUP_CONSENT_REQUIRED')
            require(type(authority) is d.SqlAuthorityCredentials,'BACKUP_AUTHORITY_REQUIRED')
            # Fresh requests, Admin input, settings changes and unknown installations stay refused.
            assessment=u.UpgradePreflight(self.runtime,self.source,repository=p.WEB_REPOSITORY,commit=f.WEB_COMMIT).inspect(
                payload,config_root=config_root,cancel=cancel)
            value=copy.deepcopy(payload)
            config=f._configuration(value,fresh=False)
            require(authority._user!=config['database']['user'] and authority._password!=value['secrets']['database_password'],'ACCOUNT_SEPARATION_REQUIRED')
            require(isinstance(backup_root,Path),'BACKUP_PATH_REJECTED')
            for path in (Path(config['web']['webroot']),config_root,self.runtime.run_root,self.runtime.state_root,self.source):
                require(backup_root!=path and backup_root not in path.parents and path not in backup_root.parents,'BACKUP_PATH_REJECTED')
            require(not any(backup_root==Path(x) or Path(x) in backup_root.parents for x in ('/srv','/var/www','/tmp')),'BACKUP_PUBLIC_PATH_REJECTED')
            with ExitStack() as stack:
                backupfd=stack.enter_context(fs._directory(backup_root))
                require(stat.S_IMODE(os.fstat(backupfd).st_mode)==0o700 and os.fstat(backupfd).st_gid==0,'BACKUP_PATH_REJECTED')
                gid,web,directory,conf,webfd,inc=f._open(self.runtime,config,config_root,stack)
                current=f.FinalizationStep(self.runtime,self.source,repository=p.WEB_REPOSITORY,commit=f.WEB_COMMIT)
                current._sources(web)
                database,loader,ca=f._prepared(config,value,directory,conf,gid)
                if orphan is not None:
                    # Existing payloads intentionally use existing_local, even
                    # after managed fresh. Read the retained provisioning receipt.
                    require(config['database']['mode']=='existing_local' and database['host']=='127.0.0.1'
                        and f._json_read(conf,'state.json',gid)['migration_retained'] is False,
                        'BACKUP_RESCUE_PROFILE_REJECTED')
                completed=f._completed(conf,webfd,inc,gid)
                try:
                    u._shared_file(conf,'assistant-edit.lock',0,stack,mode=0o600,limit=0)
                except FileNotFoundError:
                    pass
                secret=u._shared_file(conf,'assistant.json',gid,stack,mode=0o660,limit=2048)
                current._pending_edits(conf)
                require(cancel is None or not cancel.is_set(),'BACKUP_INTERRUPTED')
                require(shutil.disk_usage(backup_root).free >= 512*1024*1024
                    and shutil.disk_usage(self.runtime.run_root).free >= 2*1024*1024*1024,'BACKUP_FREE_SPACE_REQUIRED')
                run_id=os.urandom(16).hex()
                slot=backup_root/run_id
                os.mkdir(run_id,0o700,dir_fd=backupfd)
                started=True
                os.fsync(backupfd)
                _new_file(slot/'attempt.json',p._json({'version':1,'state':'STARTED','backup_id':run_id,'source_commit':f.WEB_COMMIT}))
                (slot/'files').mkdir(mode=0o700)
                roots={'web':web,'configuration':directory}
                records=_scan(roots,destination=slot/'files',cancel=cancel)
                # Preserve the fresh interlock as data; never back up unrelated instances' journals.
                f._database_receipt(self.runtime,database)
                key=f._sha(p._json([database['host'],database['port'],database['name'].lower()]))
                with fs._directory(self.runtime.state_root) as statefd:
                    journal=f._read(statefd,'fresh-'+key+'.attempt',0,mode=0o600)
                _new_file(slot/'fresh.attempt',journal)
                with tempfile.TemporaryDirectory(prefix='be-',dir=self.runtime.run_root) as tmp:
                    stage=Path(tmp)
                    _worker_stage(self.runtime,self.source,stage,ca)
                    target={k:database[k] for k in ('host','port','name','tls_required','tls_ca_file','tls_ca_sha256')}
                    if ca is not None:
                        target['tls_ca_file']=str(stage/'ca.pem')
                    request={'version':1,'operation':'export','request_id':run_id,'target':target,
                        'authority':{'user':authority._user,'password':authority._password}}
                    if orphan is not None:
                        request.update(operation='export_rescue',expected_orphaned_definer=orphan)
                    fd=os.open(slot/'database.ndjson',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                    with os.fdopen(fd,'wb') as output:
                        code,sql_sha,sql_bytes=br.capture(p._command(self.runtime,stage),p._json(request),stage,output,self.runtime.timeout_seconds,cancel=cancel)
                        output.flush()
                        os.fsync(output.fileno())
                    last=_tail(slot/'database.ndjson')
                    if code!=0:
                        require(code==20 and set(last)=={'version','request_id','ok','result','error'} and last.get('version')==1 and last.get('result') is None and last.get('ok') is False and last.get('request_id')==run_id
                            and last.get('error') in ERRORS,'BACKUP_PROTOCOL_REJECTED')
                        raise UpgradeBackupError(last['error'])
                    require(last.get('type')=='complete' and last.get('request_id')==run_id,'BACKUP_ARCHIVE_INVALID')
                require(_scan(roots,cancel=cancel)==records and hmac.compare_digest(secret,f._read(conf,'assistant.json',gid,mode=0o660,limit=2048)), 'BACKUP_FILES_CHANGED')
                current._sources(web)
                require(f._completed(conf,webfd,inc,gid)==completed and f._prepared(config,value,directory,conf,gid)==(database,loader,ca),'BACKUP_SOURCE_CHANGED')
                current._pending_edits(conf)
                manifest={'version':1,'backup_id':run_id,'source_commit':f.WEB_COMMIT,'runtime_sha256':f.RUNTIME_SHA256,
                    'source_webroot':str(web),'source_configuration':str(directory),'source_state_root':str(self.runtime.state_root),
                    'database_sha256':sql_sha,'database_bytes':sql_bytes,'fresh_attempt_name':'fresh-'+key+'.attempt',
                    'fresh_attempt_sha256':f._sha(journal),'files':records}
                if orphan is not None:
                    manifest.update(version=2,purpose='ORPHANED_DEFINER_RESCUE',expected_orphaned_definer=orphan)
                _new_file(slot/'manifest.json',p._json(manifest))
                # Work ONLY from saved copies. The verifier receives no source connection or authority credential.
                _verify_files(slot,records)
                saved_journal,_=_file(slot/'fresh.attempt')
                require(hmac.compare_digest(saved_journal,journal),'BACKUP_FILES_CHANGED')
                _new_file(slot/'journal-restore-check',saved_journal)
                restored_journal,_=_file(slot/'journal-restore-check')
                require(hmac.compare_digest(restored_journal,journal),'BACKUP_FILE_RESTORE_MISMATCH')
                (slot/'journal-restore-check').unlink()
                verified=(_restore(self.runtime,self.source,slot,run_id,sql_sha,cancel) if orphan is None
                    else _restore(self.runtime,self.source,slot,run_id,sql_sha,cancel,rescue=True))
                require(verified['logical_sha256']==last['logical_sha256'] and verified['tables']==last['tables']
                    and verified['rows']==last['rows'] and _hash(slot/'database.ndjson')==sql_sha,'BACKUP_RESTORE_MISMATCH')
                require(cancel is None or not cancel.is_set(),'BACKUP_INTERRUPTED')
                result={'state':'BACKUP_RESTORE_VERIFIED','code':'OK','backup_id':run_id,'backup_verified':True,
                    'database_restoration_verified':True,'private_files_restoration_verified':True,'restore_to_original_allowed':False,
                    'apply_allowed':False,'rollback_verified':False,'web_activation_verified':False,'application_installed':False,
                    'source_commit':f.WEB_COMMIT,'source_schema_written':False,'database_sha256':sql_sha,
                    'manifest_sha256':f._sha(p._json(manifest)), 'files':sum(r['kind']=='file' for r in records),**verified,
                    'scope':'SQL_ROOT_OWNED_WEB_AND_PRIVATE_ENVELOPE',
                    'limitations':['NO_MUTABLE_BUSINESS_FILE_TREES','NO_EXTERNAL_PHP_SESSION_STORAGE',
                        'ONLY_FIVE_CANONICAL_SCOPED_DEFINER_TRIGGERS','POINT_IN_TIME_NOT_LIVE_SYNCHRONIZATION','NO_WEB_REACTIVATION_OR_UPGRADE']}
                if orphan is not None:
                    result.update(state='RESCUE_RESTORE_VERIFIED',backup_verified=False,rescue_restoration_verified=True,
                        operational_source_verified=False,source_definers_missing=True,purpose='ORPHANED_DEFINER_RESCUE')
                    result['limitations'].append('SOURCE_TRIGGERS_STILL_ORPHANED_NO_REPAIR_AUTHORIZED')
                with fs._directory(slot/'files') as fd:
                    os.fsync(fd)
                with fs._directory(slot) as fd:
                    os.fsync(fd)
                os.fsync(backupfd)
                _new_file(slot/('verified.json' if orphan is None else 'rescue-verified.json'),p._json(result))
                with fs._directory(slot) as fd:os.fsync(fd)
                return BackupVerification(p._json(result))
        except Exception as error:
            code=str(error) if isinstance(error,(UpgradeBackupError,br.BackupRuntimeError)) else 'BACKUP_OPERATION_UNAVAILABLE'
            if started:
                # A returned failure never leaves a valid success receipt. Only
                # this exclusive slot's receipt is removed, never its archive.
                try:
                    (slot/'verified.json').unlink(missing_ok=True)
                    (slot/'rescue-verified.json').unlink(missing_ok=True)
                    with fs._directory(slot) as fd:os.fsync(fd)
                except OSError:
                    pass
                return BackupVerification(p._json({'state':'BACKUP_INCOMPLETE','code':code,'backup_id':run_id,
                    'backup_verified':False,'apply_allowed':False,'rollback_verified':False,'manual_inspection_required':True}))
            raise UpgradeBackupError(code) from None
