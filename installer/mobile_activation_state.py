"""Private activation journal and retained native activity lock.

Serving recovery never recreates maintenance or invokes the historical drain.
An intent without a provable running invocation is manual, never a retry.
"""
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
import fcntl
import os
from pathlib import Path
import re
import time

from installer import mobile_activation_runtime as v
from installer.model import canonical_bytes, strict_json_loads

b, s = v.b, v.b.s
fs, f, files, p = s.fs, s.f, s.files, s.p
require, ActivationError = v.require, v.ActivationError
MAX_RECORD = 65536
POLICY = 'MOBILE_EXPLICIT_ACTIVATION_NO_START_RETRY_V1'
NAMES = {'plan.json','armed.json','admitted.json','done.json',
         *(role+suffix for role in v.ROLES for suffix in ('.intent.json','.started.json'))}


def closed(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        try: return fn(*args, **kwargs)
        except ActivationError: raise
        except Exception: raise ActivationError('MOBILE_ACTIVATION_UNAVAILABLE') from None
    return call


def _identity(fd):
    info=os.fstat(fd);return {'device':info.st_dev,'inode':info.st_ino}


def resumed(scope, lease_id):
    return v.h.p._json({'version':1,'instance':scope.instance,'lease_id':lease_id,'state':'ACTIVITY_RESUMED'})


class ActivityLock:
    """Actual exclusive activity.lock, usable only after its exact gate release."""
    def __init__(self, record, directory, lock):
        self.record,self.directory,self.lock=record,directory,lock
        self._pid,self._closed=os.getpid(),False

    def __reduce__(self): raise TypeError('Activity locks cannot be serialized')

    def assert_held(self):
        require(not self._closed and self._pid==os.getpid(),'MOBILE_ACTIVATION_LOCK_CLOSED')
        self.record.check();scope=self.record.scope
        require(_identity(self.directory)==self.record.value['scope_identity'])
        require(scope._flag(self.directory) is None,'MOBILE_ACTIVATION_MAINTENANCE_REQUIRED')
        require(f._read(self.directory,'activity.lock',scope.web_gid)==b''
            and f._read(self.directory,'profile.json',scope.web_gid)==scope._profile()
            and f._read(self.directory,'request_guard.php',scope.web_gid)==scope._guard())
        named=os.stat('activity.lock',dir_fd=self.directory,follow_symlinks=False)
        opened=os.fstat(self.lock)
        require((named.st_dev,named.st_ino)==(opened.st_dev,opened.st_ino))
        require(f._read(self.directory,'resumed-'+self.record.lease_id+'.json',scope.web_gid)
                ==resumed(scope,self.record.lease_id))
        for name in (s.MARKER,*s.OLD,'gateway-cutover.attempt','gateway-active-profile.attempt',
                'gateway-resume.attempt','gateway-state.attempt','gateway-state.release','upgrade.attempt',
                'data-access.attempt','inode-fence.attempt','inode-fence.release',
                'configuration-inodes.attempt','configuration-inodes.release','web-inodes.attempt',
                'web-inodes.release','external-paths.prepare','external-paths.attempt','external-paths.release'):
            fs._absent(self.directory,name)
        require(self.record.read('armed.json')==self.record.owner())


class ActivationRecord:
    def __init__(self, native, backups, lease_id, confirmation, raw):
        require(type(native) is v.NativeRuntime and isinstance(backups,Path)
            and type(lease_id) is str and re.fullmatch('[a-f0-9]{32}',lease_id)
            and type(confirmation) is str and re.fullmatch('[a-f0-9]{64}',confirmation),
            'MOBILE_ACTIVATION_INPUT_REJECTED')
        self.native,self.scope,self.backups=native,native.scope,backups
        self.lease_id,self.confirmation=lease_id,confirmation
        self.root=backups/('mobile-activation-'+lease_id)
        self._raw,self.value,self._pid=raw,strict_json_loads(raw),os.getpid()
        self.check()

    def __repr__(self):return '<ActivationRecord private explicit activity transition>'
    def __reduce__(self):raise TypeError('Activation records cannot be serialized')

    @classmethod
    @closed
    def load(cls,native,backups,lease_id,confirmation):
        raw=s.e._read_path(backups/('mobile-activation-'+lease_id),'plan.json',MAX_RECORD)
        return cls(native,backups,lease_id,confirmation,raw)

    @classmethod
    @closed
    def begin(cls,window,native):
        # The admission coordinator has just made a full native check and owns
        # SQL, configuration and the real maintenance lease through this call.
        from installer.mobile_activation_admission import ActivationWindow
        require(type(window) is ActivationWindow and type(native) is v.NativeRuntime,
            'MOBILE_ACTIVATION_LIVE_ADMISSION_REQUIRED')
        window.assert_held();state=window.control.state
        require(type(state) is s.BlockerState)
        state.static();require(state.state()['done'])
        with fs._directory(state.backups) as fd:backup=_identity(fd)
        value={'version':1,'policy':POLICY,'instance':state.lease.scope.instance,
            'lease_id':state.lease.lease_id,'backup_root':str(state.backups),'backup_identity':backup,
            'scope_identity':_identity(state.lease._directory),'web_gid':state.lease.scope.web_gid,
            'resume_plan_sha256':state.confirmation,'blocker_done_sha256':f._sha(state.done()),
            'activation_marker_sha256':f._sha(state.activation()),'http_profile_sha256':f._sha(native.original_profile),
            'runtime':v.runtime_profile(native.http,native.account),'provenance':v.o._provenance(),
            'start_order':native.order,'automatic_start_retry_allowed':False,'maintenance_last_blocker':True}
        raw=canonical_bytes(value);root=state.backups/('mobile-activation-'+state.lease.lease_id)
        with fs._directory(state.backups) as parent:
            files._private(parent,directory=True)
            try:os.mkdir(root.name,0o700,dir_fd=parent);os.fsync(parent)
            except FileExistsError:pass
        with fs._directory(root) as fd:
            files._private(fd,directory=True)
            old=b.r._optional(fd,'plan.json',MAX_RECORD)
            if old is None:
                require(not os.listdir(fd),'MOBILE_ACTIVATION_FOREIGN_RECORD')
                files._new(fd,'plan.json',raw)
            else:require(old==raw,'MOBILE_ACTIVATION_PLAN_CHANGED')
        return cls(native,state.backups,state.lease.lease_id,state.confirmation,raw)

    @contextmanager
    def slot(self):
        require(self._pid==os.getpid(),'MOBILE_ACTIVATION_PROCESS_CHANGED')
        with fs._directory(self.root) as fd:
            files._private(fd,directory=True)
            require(set(os.listdir(fd))<=NAMES,'MOBILE_ACTIVATION_FOREIGN_RECORD')
            require(files._read(fd,'plan.json',MAX_RECORD)==self._raw)
            yield fd

    def read(self,name):
        require(name in NAMES)
        with self.slot() as fd:return b.r._optional(fd,name,MAX_RECORD)

    def save(self,name,raw):
        require(name in NAMES and name!='plan.json')
        with self.slot() as fd:
            old=b.r._optional(fd,name,MAX_RECORD)
            if old is None:files._new(fd,name,raw)
            else:require(old==raw,'MOBILE_ACTIVATION_RECORD_CHANGED')

    def owner(self):
        return canonical_bytes({'version':1,'instance':self.scope.instance,'lease_id':self.lease_id,
            'activation_plan_sha256':f._sha(self._raw),'resume_plan_sha256':self.confirmation})

    def marker(self):
        owner={'version':1,'instance':self.scope.instance,'lease_id':self.lease_id,
            'resume_plan_sha256':self.confirmation,'mobile_guard_sha256':f._sha(self.originals[p.COPIES[1]]),
            'gateway_release_sha256':f._sha(self.originals[p.COPIES[2]])}
        return canonical_bytes({'owner':owner,'state':'MOBILE_ACTIVATION_BLOCKED',
            'maintenance_released':False,'services_started':False,'current_sql_admission':False})

    @closed
    def check(self):
        require(self._pid==os.getpid(),'MOBILE_ACTIVATION_PROCESS_CHANGED')
        with fs._directory(self.backups) as fd:files._private(fd,directory=True);identity=_identity(fd)
        require(canonical_bytes(self.value)==self._raw and set(self.value)=={'version','policy','instance',
            'lease_id','backup_root','backup_identity','scope_identity','web_gid','resume_plan_sha256',
            'blocker_done_sha256','activation_marker_sha256','http_profile_sha256','runtime','provenance',
            'start_order','automatic_start_retry_allowed','maintenance_last_blocker'})
        require(type(self.value['version']) is int and self.value['version']==1 and self.value['policy']==POLICY
            and self.value['instance']==self.scope.instance and self.value['lease_id']==self.lease_id
            and self.value['backup_root']==str(self.backups) and self.value['backup_identity']==identity
            and self.value['web_gid']==self.scope.web_gid and self.value['resume_plan_sha256']==self.confirmation
            and self.value['automatic_start_retry_allowed'] is False and self.value['maintenance_last_blocker'] is True)
        require(self.value['provenance']==v.o._provenance(),'MOBILE_ACTIVATION_BOOT_CHANGED')
        identity=self.value['scope_identity']
        require(type(identity) is dict and set(identity)=={'device','inode'}
            and all(type(item) is int and item>=0 for item in identity.values()))
        require(canonical_bytes(self.value['runtime'])==canonical_bytes(v.runtime_profile(self.native.http,self.native.account)))
        root=self.backups/('mobile-resume-'+self.lease_id)
        raw=s.e._read_path(root,'plan.json',p.MAX_PLAN);require(f._sha(raw)==self.confirmation)
        plan=p.ResumePlan(root,raw)
        require(canonical_bytes(plan.value)==raw and plan.value['instance']==self.scope.instance
            and plan.value['lease_id']==self.lease_id and plan.value['backup_root']==str(self.backups)
            and plan.value['maintenance_last'] is True
            and plan.value['automatic_start_retry_allowed'] is False)
        self.originals={name:s.e._read_path(root,name,MAX_RECORD) for name in p.COPIES}
        with plan._slot() as fd:plan._records(fd,self.originals,complete=True)
        require(self.originals[p.COPIES[3]]==self.native.original_profile
            and self.value['http_profile_sha256']==f._sha(self.native.original_profile)
            and self.value['start_order']==self.native.order==plan.value['start_order'])
        for name,raw in self.originals.items():
            row=plan.value['originals'][name];require(row['sha256']==f._sha(raw) and row['bytes']==len(raw))
        require(self.value['activation_marker_sha256']==f._sha(self.marker()))
        parent=self.backups/('mobile-blockers-'+self.lease_id)
        owner=strict_json_loads(self.marker())['owner']
        require(s.e._read_path(parent,'intent.json',MAX_RECORD)==canonical_bytes(owner))
        for index,name in enumerate(s.RECEIPTS):
            expected={'owner':owner,'removed':s.OLD[index],'activation_blocker_sha256':f._sha(self.marker())}
            require(s.e._read_path(parent,name,MAX_RECORD)==canonical_bytes(expected))
        expected={'owner':owner,'state':'OLD_BLOCKERS_REPLACED_ACTIVITY_CLOSED',
            'activation_blocker_sha256':f._sha(self.marker()),'maintenance_released':False,
            'services_started':False,'current_sql_admission':False}
        require(s.e._read_path(parent,'done.json',MAX_RECORD)==canonical_bytes(expected)
            and self.value['blocker_done_sha256']==f._sha(canonical_bytes(expected)))
        with self.slot():pass

    def admitted(self):return canonical_bytes({'owner':strict_json_loads(self.owner()),'state':'ACTIVITY_GATE_RELEASED',
        'services_started':False,'current_sql_admission':False,'automatic_start_retry_allowed':False})

    def restore_owned_blocker(self,lease):
        # Only the new activation owner's interrupted removal can be reclosed,
        # while the ORIGINAL maintenance marker and exclusive native lease live.
        self.check();lease.assert_held();require(self.read('armed.json')==self.owner() and self.read('admitted.json') is None)
        require(not any(self.read(role+'.intent.json') for role in v.ROLES))
        require(lease._raw==self.originals[p.COPIES[0]] and _identity(lease._directory)==self.value['scope_identity'])
        old=b.r._optional(lease._directory,s.MARKER,s.MAX_RECORD)
        if old is None:files._new(lease._directory,s.MARKER,self.marker())
        else:require(old==self.marker())

    def release(self,window):
        from installer.mobile_activation_admission import ActivationWindow
        require(type(window) is ActivationWindow and window.record is self,'MOBILE_ACTIVATION_LIVE_ADMISSION_REQUIRED')
        window.assert_held();self.save('armed.json',self.owner());window.assert_held()
        from installer.gateway_resume_authority import consume
        consume(window,self)
        lease=window.control.lease
        require(b.r._optional(lease._directory,s.MARKER,s.MAX_RECORD)==self.marker())
        window.fence.assert_held();os.unlink(s.MARKER,dir_fd=lease._directory);os.fsync(lease._directory)
        # No old blocker/window reader is called after its owned transition.
        window.closed=True;window.check_files();window.fence.assert_held()
        lease._release_marker(confirmed=True)
        guard=ActivityLock(self,lease._directory,lease._lock);guard.assert_held()
        window.fence.assert_held();self.save('admitted.json',self.admitted());guard.assert_held()
        return guard

    @contextmanager
    def serving_lock(self):
        self.check()
        with self.scope._open() as (directory,lock):
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ActivationError('MOBILE_ACTIVATION_BUSY') from None
            guard=ActivityLock(self,directory,lock)
            try:guard.assert_held();yield guard;guard.assert_held()
            finally:guard._closed=True

    def _intent(self,role,before):
        return canonical_bytes({'owner':strict_json_loads(self.owner()),'role':role,'before':before,
            'not_before_monotonic_us':time.monotonic_ns()//1000})

    def _observation(self, role, value, *, active):
        require(type(value) is dict and set(value)=={'unit','invocation_id','active_enter_monotonic_us'}
            and value['unit']==self.native.unit(role)
            and type(value['invocation_id']) is str
            and (re.fullmatch('[a-f0-9]{32}',value['invocation_id']) or not active and value['invocation_id']=='')
            and type(value['active_enter_monotonic_us']) is int and value['active_enter_monotonic_us']>=0,
            'MOBILE_ACTIVATION_INVOCATION_REJECTED')
        if active:require(value['invocation_id'] not in ('','0'*32) and value['active_enter_monotonic_us']>0)

    def _parse_intent(self, role, raw):
        value=strict_json_loads(raw)
        require(canonical_bytes(value)==raw and set(value)=={'owner','role','before','not_before_monotonic_us'}
            and value['owner']==strict_json_loads(self.owner()) and value['role']==role
            and type(value['not_before_monotonic_us']) is int
            and 0<value['not_before_monotonic_us']<=time.monotonic_ns()//1000)
        self._observation(role,value['before'],active=False)
        return value

    def _receipt(self, role, intent, observed):
        value=self._parse_intent(role,intent);self._observation(role,observed,active=True)
        require(observed['invocation_id']!=value['before']['invocation_id']
            and observed['active_enter_monotonic_us']>=value['not_before_monotonic_us'],
            'MOBILE_ACTIVATION_INVOCATION_CHANGED')
        return canonical_bytes({'owner':strict_json_loads(self.owner()),'role':role,
            'intent_sha256':f._sha(intent),'invocation':observed})

    def _done(self, results):
        return canonical_bytes({'owner':strict_json_loads(self.owner()),'state':'MOBILE_SERVICES_RUNNING',
            'invocations':results,'maintenance_released':True,'services_started':True,'boot_persistence':False,
            'automatic_start_retry_allowed':False,'phase6_complete':False})

    def _journal(self, *, check_only):
        admitted=self.read('admitted.json')
        require(admitted is None or admitted==self.admitted())
        pending=False;results=[]
        for role in v.ROLES:
            intent,receipt=self.read(role+'.intent.json'),self.read(role+'.started.json')
            require(not pending or intent is None and receipt is None,'MOBILE_ACTIVATION_ORDER_CHANGED')
            if intent is not None:
                require(admitted is not None);self._parse_intent(role,intent)
            if receipt is not None:
                require(intent is not None and admitted is not None)
                observed=strict_json_loads(receipt)['invocation']
                require(receipt==self._receipt(role,intent,observed))
                results.append(observed)
            else:pending=True
            if check_only:require(intent is not None and receipt is not None,'MOBILE_ACTIVATION_INCOMPLETE')
        done=self.read('done.json')
        if done is not None:require(not pending and admitted is not None and done==self._done(results))
        if check_only:require(done is not None and admitted is not None,'MOBILE_ACTIVATION_INCOMPLETE')

    @closed
    def start_services(self,guard,*,check_only=False):
        require(type(guard) is ActivityLock and guard.record is self,'MOBILE_ACTIVATION_LOCK_REQUIRED')
        require(type(check_only) is bool)
        guard.assert_held();self._journal(check_only=check_only)
        if not check_only:self.save('admitted.json',self.admitted())
        results=[]
        for role in v.ROLES:
            guard.assert_held();self.native.configuration()
            intent=self.read(role+'.intent.json');receipt=self.read(role+'.started.json')
            if intent is None:
                require(not check_only and receipt is None)
                before=self.native.observed(role,active=False);self._observation(role,before,active=False)
                intent=self._intent(role,before);self._parse_intent(role,intent)
                self.save(role+'.intent.json',intent)
                guard.assert_held();self.native.start(role)
                observed=self.native.running_after(role)
            else:
                # An existing intent NEVER permits another start, even explicit
                # resume. Only the same owned running invocation can reconcile.
                observed=self.native.observed(role,active=True)
            expected=self._receipt(role,intent,observed)
            if receipt is None:
                require(not check_only);self.save(role+'.started.json',expected)
            else:require(receipt==expected,'MOBILE_ACTIVATION_INVOCATION_CHANGED')
            results.append(observed);guard.assert_held()
        self.native.configuration()
        require([self.native.observed(role,active=True) for role in v.ROLES]==results)
        done=self._done(results)
        if check_only:require(self.read('done.json')==done)
        else:self.save('done.json',done)
        guard.assert_held()
        return deepcopy(strict_json_loads(done))
