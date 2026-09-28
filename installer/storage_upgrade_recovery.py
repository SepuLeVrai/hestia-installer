"""Reconcile a pinned storage cutover from durable identities and exact bytes.

Never restore SQL or overwrite an unknown file. The first durable resume intent
ends rollback eligibility, even if the caller loses the authorization response.
"""
from contextlib import contextmanager, ExitStack
import fcntl
import os
from pathlib import Path
import stat

from installer import storage_upgrade as u

f, fs, p = u.f, u.fs, u.p
require = u.require


def present(path):
    with fs._directory(path.parent) as fd:
        try: os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError: return False
        return True


def ensure(path, value):
    """Idempotent immutable receipt: an existing different receipt is an error."""
    try: u._save(path, value)
    except FileExistsError:
        require(u._read(path) == value, 'STORAGE_RECOVERY_RECEIPT_CHANGED')
    with fs._directory(path.parent) as fd: os.fsync(fd)


@contextmanager
def operation_lock(slot):
    with fs._directory(slot) as parent:
        u.files._private(parent, directory=True)
        try: u.files._new(parent, 'operation.lock', b'')
        except FileExistsError: pass
        require(u.files._read(parent, 'operation.lock', 0) == b'')
        fd = os.open('operation.lock', u.files.REGULAR, dir_fd=parent)
        try:
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise u.StorageUpgradeError('STORAGE_RECOVERY_BUSY') from None
            info = os.fstat(fd); named = os.stat('operation.lock', dir_fd=parent, follow_symlinks=False)
            require((info.st_dev, info.st_ino) == (named.st_dev, named.st_ino))
            yield
        finally: os.close(fd)


def node(path):
    with fs._directory(path.parent) as fd:
        try: info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError: return None
        require(stat.S_ISDIR(info.st_mode), 'STORAGE_RECOVERY_LAYOUT_CHANGED')
        return {'device': info.st_dev, 'inode': info.st_ino, 'uid': info.st_uid,
                'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}


def prepare(op, slot, lease, account, extension, old_files, old_plan, old_staged,
            old_cleaner_plan, old_receipt, uploads, data_sha, sql):
    target_digest = u.h._code_digest(op.next_web, account.pw_gid)
    next_plan = p._json({**u.strict_json_loads(op.target_http._plan(account, extension)),
                        'immutable_code_sha256': target_digest})
    _, _, cleaner_files, cleaner_plan = op.target_collector._profile_inputs(account, lease.scope, f._sha(next_plan))
    next_files = {**op.target_http._files(account, extension), **cleaner_files}
    require(set(next_files) == set(old_files))
    records = []
    def record(path, before, after, gid, mode=0o640, rollback=None):
        records.append({'path': str(path), 'before': before.hex(), 'after': after.hex(),
                        'rollback': (before if rollback is None else rollback).hex(), 'gid': gid, 'mode': mode})
    for path, raw in old_files.items():
        record(path, raw, next_files[path], account.pw_gid if path.parent == op.collector.directory else 0,
               0o644 if path.is_relative_to(u.h.drain.UNIT_ROOT) else 0o640)
    directory = lease.scope.directory.parent
    receipt = {**old_receipt, 'source_commit': u.STORAGE_COMMIT,
               'runtime_sha256': u.get_release(u.STORAGE_COMMIT).runtime_sha256}
    record(directory / 'finalized.json', p._json(old_receipt), p._json(receipt), account.pw_gid)
    record(op.http.spec.root / 'provision.attempt', old_plan, next_plan, 0)
    record(op.http.spec.root / 'staged.json', p._json(old_staged),
           p._json({**old_staged, 'plan_sha256': f._sha(next_plan), 'lease_id': lease.lease_id}), 0,
           rollback=p._json({**old_staged, 'lease_id': lease.lease_id}))
    record(op.collector.directory / 'cleaner.attempt', old_cleaner_plan, cleaner_plan, 0)
    with fs._directory(op.collector.directory) as fd: old_cleaner_staged = f._read(fd, 'staged.json', 0)
    previous = u.strict_json_loads(old_cleaner_staged)
    record(op.collector.directory / 'staged.json', old_cleaner_staged,
           p._json({**previous, 'plan_sha256': f._sha(cleaner_plan), 'lease_id': lease.lease_id}), 0,
           rollback=p._json({**previous, 'lease_id': lease.lease_id}))
    value = {'version': 1, 'instance': lease.scope.instance, 'lease_id': lease.lease_id,
        'source_node': node(op.http.spec.webroot), 'target_node': node(op.next_web),
        'uploads_node': node(slot / 'relocated/uploads'),
        'source_digest': u.strict_json_loads(old_plan)['immutable_code_sha256'], 'target_digest': target_digest,
        'runtime_plan_sha256': f._sha(next_plan), 'cleaner_plan_sha256': f._sha(cleaner_plan),
        'data_manifest_sha256': data_sha, 'sql_logical_sha256': sql['logical_sha256'], 'files': records}
    u._save(slot / 'cutover.json', value)
    u._save(slot / 'cutover-ready.json', {'version': 1, 'sha256': f._sha(p._json(value))})
    return value


def context(op, backup_root, lease_id):
    require(isinstance(backup_root, Path) and str(backup_root).startswith('/var/lib/'))
    require(type(lease_id) is str and len(lease_id) == 32 and all(x in '0123456789abcdef' for x in lease_id))
    with fs._directory(backup_root) as fd: u.files._private(fd, directory=True)
    slot = backup_root / ('upgrade-' + lease_id)
    binding = u._read(slot / 'attempt.json')
    require(binding['version'] == 1 and binding['instance'] == op.http.spec.instance
            and binding['lease_id'] == lease_id and binding['slot'] == str(slot)
            and binding['webroot'] == str(op.http.spec.webroot) and binding['runtime'] == str(op.http.spec.root)
            and binding['source_commit'] == u.LEGACY_COMMIT and binding['target_commit'] == u.STORAGE_COMMIT,
            'STORAGE_RECOVERY_BINDING_MISMATCH')
    account = u.h._identity(op.http.spec.service_user)
    return slot, binding, account, op.http._scope(account)


def prepared(op, slot, lease):
    value = u._read(slot / 'cutover.json')
    require(u._read(slot / 'cutover-ready.json') == {'version': 1, 'sha256': f._sha(p._json(value))}
            and value['instance'] == lease.scope.instance and value['lease_id'] == lease.lease_id,
            'STORAGE_RECOVERY_RECEIPT_CHANGED')
    account = u.h._identity(op.http.spec.service_user)
    # Only the provisioner's exact generated resources may be reconciled.
    _, _, cleaner_files, _ = op.collector._profile_inputs(account, lease.scope, '0' * 64)
    allowed = set(op.http._files(account, op.runtime.extension_dir)) | set(cleaner_files)
    allowed |= {lease.scope.directory.parent / 'finalized.json',
                op.http.spec.root / 'provision.attempt', op.http.spec.root / 'staged.json',
                op.collector.directory / 'cleaner.attempt', op.collector.directory / 'staged.json'}
    require(len(value['files']) == len(allowed) and {Path(x['path']) for x in value['files']} == allowed,
            'STORAGE_RECOVERY_BINDING_MISMATCH')
    for row in value['files']:
        path = Path(row['path'])
        gid = account.pw_gid if path.parent in (lease.scope.directory.parent, op.collector.directory) else 0
        if path.name in ('cleaner.attempt', 'staged.json'): gid = 0
        require(row['gid'] == gid and row['mode'] == (0o644 if path.is_relative_to(u.h.drain.UNIT_ROOT) else 0o640))
    return value


def quiescent(op, lease):
    lease.assert_held()
    for role in ('apache', 'php', 'session-cleaner'):
        unit = op.collector.unit if role == 'session-cleaner' else op.http.unit(role)
        state = u.h.drain._show(unit)
        require(state['LoadState'] == 'loaded' and state['ActiveState'] == 'inactive'
                and state['SubState'] == 'dead' and state['MainPID'] == state['ControlPID'] == '0'
                and state['Job'] == '' and u.h.drain._empty_cgroup(unit), 'STORAGE_RECOVERY_SERVICES_ACTIVE')
        dropin = u.h.drain.UNIT_ROOT / (unit + '.d/50-hestia-maintenance.conf')
        require(u.h.drain._root_file(dropin) == u.h.drain.condition_dropin(lease.scope),
                'STORAGE_RECOVERY_GATE_CHANGED')
    op.collector._timer_state(stopped=True)
    account = u.h._identity(op.http.spec.service_user)
    u.hd.identity_census(account.pw_uid, account.pw_gid, ())


def read_file(row):
    path = Path(row['path'])
    with fs._directory(path.parent) as fd:
        return f._read(fd, path.name, row['gid'], mode=row['mode'], limit=p.MAX_FILE)


def reconcile_files(value, direction, *, write):
    desired = 'after' if direction == 'forward' else 'rollback'
    for row in value['files']:
        current = read_file(row)
        require(current in [bytes.fromhex(row[x]) for x in ('before', 'after', 'rollback')],
                'STORAGE_RECOVERY_CONFIGURATION_CHANGED')
        if write:
            path = Path(row['path']); target = bytes.fromhex(row[desired])
            if current != target: u._replace(path, current, target, row['gid'], row['mode'])
            else:
                with fs._directory(path.parent) as fd: os.fsync(fd)


def layout(op, slot, lease, value):
    web = op.http.spec.webroot
    previous_web, parked_web = op.previous / 'web', op.previous / 'target-web'
    # Missing private parent means none of its children can exist.
    def inspect(path):
        return node(path) if present(path.parent) else None
    candidates = {web: ('source', 'target'), op.next_web: ('target',),
                  previous_web: ('source',), parked_web: ('target',),
                  slot / 'relocated/uploads': ('uploads',),
                  op.http.spec.root / 'data/uploads': ('uploads',), slot / 'unused-uploads': ('uploads',)}
    found = {}
    for path, labels in candidates.items():
        actual = inspect(path)
        if actual is None: continue
        matched = [label for label in labels if actual == value[label + '_node']]
        require(len(matched) == 1 and matched[0] not in found, 'STORAGE_RECOVERY_LAYOUT_CHANGED')
        found[matched[0]] = path
    require(set(found) == {'source', 'target', 'uploads'}, 'STORAGE_RECOVERY_LAYOUT_CHANGED')
    account = u.h._identity(op.http.spec.service_user)
    for label in ('source', 'target'):
        require(u.h._code_digest(found[label], account.pw_gid, private_parent=True) == value[label + '_digest'],
                'STORAGE_RECOVERY_CODE_CHANGED')
    verify_uploads(op, slot, lease, found['uploads'])
    return found


def verify_uploads(op, slot, lease, path):
    account = u.h._identity(op.http.spec.service_user)
    inventory = u.files.DataInventory((('uploads', path),), account.pw_uid, account.pw_gid)
    rows = u.files._Scan(inventory, lease).run()
    clean = lambda values: [{k: v for k, v in row.items() if k != 'blob'} for row in values]
    require(clean(rows) == clean(u._read(slot / 'uploads-after.json')), 'STORAGE_UPGRADE_FILES_CHANGED')


def switch(op, slot, lease, value, direction):
    require(direction in ('forward', 'rollback'))
    quiescent(op, lease); reconcile_files(value, direction, write=False)
    found = layout(op, slot, lease, value)
    if not present(op.previous): u._mkdir(op.previous)
    with fs._directory(op.previous) as fd: u.files._private(fd, directory=True)
    web = op.http.spec.webroot
    if direction == 'forward':
        require(found['target'] in (op.next_web, web), 'STORAGE_RECOVERY_ROLLBACK_STARTED')
        if found['source'] == web: u._move(web, op.previous / 'web')
        if found['target'] != web: u._move(found['target'], web)
        target = op.http.spec.root / 'data/uploads'
        if found['uploads'] != target: u._move(found['uploads'], target)
    else:
        if found['target'] == web: u._move(web, op.previous / 'target-web')
        if found['source'] != web: u._move(found['source'], web)
        if found['uploads'] == op.http.spec.root / 'data/uploads':
            u._move(found['uploads'], slot / 'unused-uploads')
    reconcile_files(value, direction, write=True)
    runtime = op.target_http if direction == 'forward' else op.http
    collector = op.target_collector if direction == 'forward' else op.collector
    runtime._configtest()
    u.h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
    runtime.observe(); collector.observe(); quiescent(op, lease)


def applied(value, uploads):
    return {'version': 1, 'state': 'STORAGE_UPGRADE_APPLIED_GATED',
        'source_commit': u.LEGACY_COMMIT, 'target_commit': u.STORAGE_COMMIT,
        'instance': value['instance'], 'lease_id': value['lease_id'],
        'source_profile': 'SEALED_MANAGED_ROOT_OWNED_WEB', 'backup_verified': True,
        'database_preserved': True, 'sql_migrations_executed': 0,
        'upload_files': sum(x['kind'] == 'file' for x in uploads), 'upload_bytes': sum(x.get('bytes', 0) for x in uploads),
        'upload_contents_and_dates_preserved': True, 'upload_ownership_migrated': True,
        **{key: value[key] for key in ('runtime_plan_sha256', 'cleaner_plan_sha256', 'data_manifest_sha256', 'sql_logical_sha256')},
        'services_started': False, 'activity_resumed': False, 'rollback_verified': False,
        'phase5c3_complete': False, 'phase5_complete': False, 'application_installed': False}


def release_source_fences(op, lease, configuration):
    gate = lease.scope.directory
    if not any(present(gate / name) for name in (u.wf.MARKER, u.cf.MARKER, u.inode.MARKER)):
        return
    profile = f._read(lease._directory, 'http-drain-' + lease.lease_id + '.attempt', lease.scope.web_gid)
    barrier = u.hd.HttpDrainLease(u.hd.HttpDrain(op.http, cleaner=op.collector), lease, profile)
    barrier.assert_held()
    if present(gate / u.wf.RELEASE): u.wf.recover_unseal(barrier, confirmed=True)
    elif present(gate / u.wf.MARKER):
        with u.wf.recover(barrier, confirmed=True) as fence: fence.unseal(confirmed=True)
    if present(gate / u.cf.RELEASE): u.cf.recover_unseal(lease, configuration, confirmed=True)
    elif present(gate / u.cf.MARKER):
        with u.cf.recover(lease, configuration, confirmed=True) as fence: fence.unseal(confirmed=True)
    if present(gate / u.inode.MARKER):
        with u.da.recover(op.http, lease, confirmed=True) as data:
            if present(gate / u.inode.RELEASE): u.inode.recover_unseal(data, confirmed=True)
            else:
                with u.inode.recover(data, confirmed=True) as fence: fence.unseal(confirmed=True)


def resume_started(slot, scope, lease_id):
    return any(present(slot / name) for name in ('resume-intent.json', 'resume-authorized.json', 'resume-complete.json')) \
        or present(scope.directory / ('resumed-' + lease_id + '.json'))


def recover(op, payload, authority, *, config_root, backup_root, lease_id, direction, confirmed,
            allow_global_read_lock, cancel=None):
    require(confirmed is True and allow_global_read_lock is True, 'STORAGE_UPGRADE_CONSENT_REQUIRED')
    require(direction in ('forward', 'rollback') and type(authority) is u.db.SqlAuthorityCredentials)
    slot, binding, account, scope = context(op, backup_root, lease_id)
    config = f._configuration(payload, fresh=False)
    require(config_root == scope.directory.parent.parent and Path(config['web']['webroot']) == op.http.spec.webroot)
    with operation_lock(slot):
        require(not resume_started(slot, scope, lease_id), 'STORAGE_RECOVERY_ACTIVITY_AUTHORIZED')
        require(direction == 'rollback' or not present(slot / 'rollback-intent.json'), 'STORAGE_RECOVERY_ROLLBACK_STARTED')
        with ExitStack() as stack:
            lease = stack.enter_context(scope.recover(lease_id, confirmed=True, timeout=.2))
            require(u.files._read(lease._directory, u.MARKER, 4096) == p._json(binding), 'STORAGE_RECOVERY_BINDING_MISMATCH')
            quiescent(op, lease)
            schedulers = stack.enter_context(u.sched.acquire())
            gate = scope.directory
            require(not present(gate / u.ef.RELEASE), 'STORAGE_RECOVERY_ACTIVITY_AUTHORIZED')
            external = stack.enter_context(u.ef.recover(lease, confirmed=True)
                if any(present(gate / n) for n in (u.ef.PREPARE, u.ef.MARKER)) else u.ef.acquire(lease, confirmed=True))
            conf = stack.enter_context(fs._directory(scope.directory.parent))
            configuration = stack.enter_context(u.admission.acquire(conf, op.http.spec.webroot, account.pw_gid, external=external))
            database, loader, ca = f._prepared(config, payload, scope.directory.parent, conf, account.pw_gid)
            require(authority._user != database['user'] and authority._password != database['password'])
            sql_lock = stack.enter_context(u.sqlf.acquire(op.runtime, op.source, database, ca, authority, cancel=cancel))
            def held():
                require(cancel is None or not cancel.is_set(), 'STORAGE_UPGRADE_INTERRUPTED')
                lease.assert_held(); sql_lock.assert_held(); schedulers.assert_held(); external.assert_held()
            held()
            ready = present(slot / 'cutover-ready.json')
            if ready:
                value = prepared(op, slot, lease)
                reconcile_files(value, direction, write=False); layout(op, slot, lease, value)
                check = slot / ('recheck-' + os.urandom(16).hex()); u._mkdir(check)
                u.coordinated._recheck(op.runtime, op.source, database, ca, authority, check, u._read(slot / 'backup.json')['sql'], cancel)
            else:
                require(direction == 'rollback' and not present(slot / '03-CUTOVER_STARTED.json'),
                        'STORAGE_RECOVERY_PREPARATION_REQUIRED')
                op.http._inspect_configuration(); op.collector._inspect_configuration()
            if direction == 'rollback': ensure(slot / 'rollback-intent.json', {'version': 1, 'lease_id': lease_id})
            release_source_fences(op, lease, configuration)
            held()
            if ready: switch(op, slot, lease, value, direction)
            else:
                # Nothing was switched. Only bind the unchanged source staging
                # receipts to the current maintenance lease before observation.
                for root in (op.http.spec.root, op.collector.directory):
                    with fs._directory(root) as fd: raw = f._read(fd, 'staged.json', 0)
                    record = u.strict_json_loads(raw)
                    if record['lease_id'] != lease_id:
                        u._replace(root / 'staged.json', raw, p._json({**record, 'lease_id': lease_id}), 0, 0o640)
                op.http.observe(); op.collector.observe()
            require(f._probe(op.runtime, config, scope.directory.parent, account.pw_gid, active=True, cancel=cancel)['database_verified'])
            held()
            if direction == 'forward': result = applied(value, u._read(slot / 'uploads-after.json'))
            else:
                result = {'version': 1, 'state': 'STORAGE_UPGRADE_ROLLED_BACK_GATED', 'instance': scope.instance,
                    'lease_id': lease_id, 'source_commit': u.LEGACY_COMMIT, 'sql_restored': False,
                    'data_deleted': False, 'services_started': False, 'activity_resumed': False,
                    'rollback_verified': True, 'phase5_complete': False, 'application_installed': False}
        ensure(slot / ('applied.json' if direction == 'forward' else 'rolled-back.json'), result)
        return result


def release_external(lease):
    gate = lease.scope.directory
    if present(gate / u.ef.RELEASE): u.ef.recover_unseal(lease, confirmed=True)
    elif any(present(gate / name) for name in (u.ef.PREPARE, u.ef.MARKER)):
        with u.ef.recover(lease, confirmed=True) as external: external.unseal(confirmed=True)
    else:
        for path in u.ef.PATHS: u.admission._absent(path)


def authorize_resume(op, backup_root, lease_id, *, confirmed):
    require(confirmed is True, 'STORAGE_UPGRADE_CONSENT_REQUIRED')
    slot, binding, account, scope = context(op, backup_root, lease_id)
    with operation_lock(slot):
        if present(slot / 'resume-complete.json'): return u._read(slot / 'resume-complete.json')
        rollback = present(slot / 'rolled-back.json')
        require(rollback or not present(slot / 'rollback-intent.json'), 'STORAGE_RECOVERY_ROLLBACK_STARTED')
        value = u._read(slot / ('rolled-back.json' if rollback else 'applied.json'))
        runtime, collector = (op.http, op.collector) if rollback else (op.target_http, op.target_collector)
        expected_state = 'STORAGE_UPGRADE_ROLLED_BACK_GATED' if rollback else 'STORAGE_UPGRADE_APPLIED_GATED'
        require(value['state'] == expected_state and value['instance'] == scope.instance and value['lease_id'] == lease_id)
        intent = {'version': 1, 'lease_id': lease_id, 'result_sha256': f._sha(p._json(value)), 'rollback': rollback}
        state = scope.observe()
        resumed = {'version': 1, 'instance': scope.instance, 'lease_id': lease_id, 'state': 'ACTIVITY_RESUMED'}
        if state['state'] == 'SERVING':
            require(u._read(slot / 'resume-intent.json') == intent, 'STORAGE_RECOVERY_RESUME_PROOF_REQUIRED')
            with scope._open() as (fd, _):
                require(f._json_read(fd, 'resumed-' + lease_id + '.json', account.pw_gid) == resumed)
        else:
            with scope.recover(lease_id, confirmed=True, timeout=.2) as lease:
                quiescent(op, lease)
                # A killed reopen may have chmod'ed data but not removed its
                # marker. Reclose that exact lease before auditing the runtime.
                if present(slot / 'resume-intent.json') and present(scope.directory / u.da.MARKER):
                    require(u._read(slot / 'resume-intent.json') == intent)
                    with u.da.recover(runtime, lease, confirmed=True): pass
                runtime.observe(); collector.observe()
                if present(scope.directory / u.MARKER):
                    require(u.files._read(lease._directory, u.MARKER, 4096) == p._json(binding))
                else: require(u._read(slot / 'resume-intent.json') == intent)
                if not rollback: verify_uploads(op, slot, lease, op.http.spec.root / 'data/uploads')
                # This durable boundary forbids rollback before any producer
                # can regain access, including a lost response during release.
                ensure(slot / 'resume-intent.json', intent)
                release_external(lease)
                if present(scope.directory / u.da.MARKER):
                    with u.da.recover(runtime, lease, confirmed=True) as data: data.reopen(confirmed=True)
                else: runtime._inspect_configuration()
                if present(scope.directory / u.MARKER):
                    os.unlink(u.MARKER, dir_fd=lease._directory); os.fsync(lease._directory)
                lease.resume(confirmed=True)
        result = {'state': 'STORAGE_UPGRADE_RESUME_AUTHORIZED', 'lease_id': lease_id,
            'selected_commit': u.LEGACY_COMMIT if rollback else u.STORAGE_COMMIT,
            'services_started': False, 'rollback_requires_new_assessment': True, 'application_installed': False}
        ensure(slot / 'resume-complete.json', result)
        return result


def observe(op, backup_root, lease_id):
    """Read the durable operation outcome; never certify current host activity."""
    slot, _, _, _ = context(op, backup_root, lease_id)
    for name in ('resume-complete.json', 'rolled-back.json', 'applied.json'):
        if present(slot / name): return u._read(slot / name)
    return {'state': 'STORAGE_UPGRADE_RECOVERY_REQUIRED', 'lease_id': lease_id, 'application_installed': False}
