"""Fresh final SQL admission, last gate release and explicit service activation.

The real activity lock survives the maintenance unlink. Old maintenance/drain
readers are never used after that boundary. Existing serving recovery observes
owned invocations and never reopens SQL backups or restarts a stopped intent.
"""
from contextlib import ExitStack
from copy import deepcopy
import os
from pathlib import Path
import re

from installer import mobile_activation_state as t

v,b,s=t.v,t.b,t.s
n,e,r,a=b,b.e,b.r,b.a
fs,files,f,p=b.fs,b.files,b.f,b.p
require,ActivationError=t.require,t.ActivationError


def transition_envelope(archives,control,record):
    expected=b._transition_envelope(archives,control)
    name='resumed-'+control.lease.lease_id+'.json'
    try:raw=f._read(control.lease._directory,name,control.lease.scope.web_gid,limit=2048)
    except FileNotFoundError:return expected
    require(record is not None and record.read('armed.json')==record.owner()
        and record.read('admitted.json') is None and t.resumed(control.lease.scope,control.lease.lease_id).startswith(raw),
        'MOBILE_ACTIVATION_RESUMED_RECORD_REJECTED')
    key=('configuration','maintenance/'+name);require(key not in expected)
    expected[key]={**a._private_record(name,raw),'gid':control.lease.scope.web_gid,'mode':0o640}
    return expected


class ActivationWindow:
    def __init__(self,control,fence,schedulers,locked,check_files,archives,envelope,record=None):
        self.control,self.fence,self.schedulers=control,fence,schedulers
        self.locked,self.check_files,self.archives=locked,check_files,archives
        self.envelope,self.record=envelope,record
        self.closed,self.pid=False,os.getpid()

    def __reduce__(self):raise TypeError('Activation windows cannot be serialized')

    @t.closed
    def assert_held(self):
        require(not self.closed and self.pid==os.getpid(),'MOBILE_ACTIVATION_WINDOW_CLOSED')
        self.fence.assert_held();self.schedulers.assert_held();self.locked.assert_held()
        self.control.live(locked=self.locked)
        if self.record is not None:self.record.check()
        self.archives.expected=transition_envelope(self.archives,self.control,self.record)
        self.envelope();self.archives.check()
        self.locked.assert_held();self.schedulers.assert_held();self.fence.assert_held()


def _release(state,native,barrier,runtime,source,payload,authority,record,cancel):
    control=b._ParentFiles(state,barrier,native.gateway);document=control.journal.read();control.engine()
    lease=state.lease;value=deepcopy(payload);config=f._configuration(value,fresh=False)
    window=locked=None
    try:
        with ExitStack() as stack:
            gid,web,directory,conf,webfd,inc=f._open(runtime,config,lease.scope.directory.parent.parent,stack)
            require(directory==lease.scope.directory.parent and gid==lease.scope.web_gid and web==native.http.spec.webroot)
            commit = native.http.source_commit
            current=f.FinalizationStep(runtime,source,repository=p.WEB_REPOSITORY,commit=commit)
            database,loader,ca=f._prepared(config,value,directory,conf,gid)
            completed=f._completed(conf,webfd,inc,gid,commit=commit)
            require(database['host']=='127.0.0.1' and database['tls_required'] is False and ca is None
                and f._json_read(conf,'state.json',gid)['migration_retained'] is False,
                'MOBILE_ACTIVATION_PROFILE_REJECTED')
            require(authority._user!=database['user'] and authority._password!=database['password'],
                'MOBILE_ACTIVATION_ACCOUNT_SEPARATION_REQUIRED')
            def envelope():
                current._sources(web);current._pending_edits(conf)
                require(f._prepared(config,value,directory,conf,gid)==(database,loader,ca)
                    and f._completed(conf,webfd,inc,gid,commit=commit)==completed)
            schedulers=stack.enter_context(a.sa.acquire())
            # Same real native file locks/checker as the old lease wrapper.
            # Only the final coordinator retains these locks across gate release.
            with control.external._configuration_files() as check_files:
                locked=e._ConfigurationGuard(control.external,check_files);locked.assert_held()
                control.live(locked=locked)
                archives=b._Archives(control,runtime,database,document,cancel)
                archives.expected=transition_envelope(archives,control,record);envelope();archives.check()
                parent=stack.enter_context(fs._directory(state.backups));files._private(parent,directory=True)
                name='activation-admission-'+os.urandom(16).hex();os.mkdir(name,0o700,dir_fd=parent);os.fsync(parent)
                slot=state.backups/name;slotfd=stack.enter_context(fs._directory(slot))
                binding={'version':1,'instance':lease.scope.instance,'lease_id':lease.lease_id,
                    'resume_plan_sha256':state.confirmation,'observation_id':name}
                files._new(slotfd,'attempt.json',p._json({'state':'FINAL_ADMISSION_STARTED',**binding}))
                with a.c.rf.acquire(runtime,source,database,ca,authority,
                        cancel=cancel,commit=commit,**archives.fence_options) as fence:
                    recheck=a.c._recheck(runtime,source,database,ca,authority,slot,archives.sql,cancel,commit=commit)
                    window=ActivationWindow(control,fence,schedulers,locked,check_files,archives,envelope,record)
                    window.assert_held()
                    if record is None:record=t.ActivationRecord.begin(window,native);window.record=record
                    observation={'state':'FINAL_ADMISSION_OBSERVED',**binding,'sql_recheck':recheck,
                        'activation_plan_sha256':f._sha(record._raw),'sql_read_fence_max_seconds':180,
                        'historical_observation_only':True,'valid_after_window_close':False}
                    files._new(slotfd,'observed.json',p._json(observation))
                    guard=record.release(window)
                    guard.assert_held();fence.assert_held();check_files()
                # No service command until the real SQL fence has released normally.
                guard.assert_held();check_files()
        return record,guard
    finally:
        if window is not None:window.closed=True
        if locked is not None:locked._closed=True


@t.closed
def execute(http,scope,lease_id,backups,runtime,source,payload,authority,confirmation,*,
            action,confirmed,allow_global_read_lock,cancel=None):
    require(confirmed is True and allow_global_read_lock is True,'MOBILE_ACTIVATION_CONSENT_REQUIRED')
    require(type(http) is v.h.HttpRuntime and type(scope) is r.hd.m.MaintenanceScope
        and type(runtime) is p.PhpRuntime and type(authority) is a.c.d.SqlAuthorityCredentials
        and isinstance(source,Path) and isinstance(backups,Path) and backups.is_absolute()
        and type(payload) is dict and payload.get('mode')=='upgrade'
        and payload.get('assistant')=={'action':'preserve'} and action in ('apply','resume','check'),
        'MOBILE_ACTIVATION_INPUT_REJECTED')
    require(type(lease_id) is str and re.fullmatch('[a-f0-9]{32}',lease_id)
        and type(confirmation) is str and re.fullmatch('[a-f0-9]{64}',confirmation),
        'MOBILE_ACTIVATION_CONFIRMATION_REQUIRED')
    require(cancel is None or not cancel.is_set(),'MOBILE_ACTIVATION_INTERRUPTED')
    root=backups/('mobile-resume-'+lease_id)
    require(f._sha(e._read_path(root,'plan.json',s.p.MAX_PLAN))==confirmation,'MOBILE_ACTIVATION_CONFIRMATION_REQUIRED')
    raw=e._read_path(root,'http-drain-original.json',32768)
    native=v.NativeRuntime(http,raw,confirmation)
    require((native.scope.instance,native.scope.directory,native.scope.web_gid)==(scope.instance,scope.directory,scope.web_gid))
    current=scope.observe()
    if current['state']=='MAINTENANCE_REQUIRED':
        require(current['lease_id']==lease_id and action!='check','MOBILE_ACTIVATION_ACTION_REJECTED')
        record=None
        with scope.recover(lease_id,confirmed=True) as lease:
            target=backups/('mobile-activation-'+lease_id)
            if action=='resume':
                record=t.ActivationRecord.load(native,backups,lease_id,confirmation)
                require(record.read('admitted.json') is None,'MOBILE_ACTIVATION_FOREIGN_MAINTENANCE')
                if b.r._optional(lease._directory,s.MARKER,s.MAX_RECORD) is None:record.restore_owned_blocker(lease)
            elif target.exists():
                record=t.ActivationRecord.load(native,backups,lease_id,confirmation)
                require(record.read('armed.json') is None,'MOBILE_ACTIVATION_EXPLICIT_RESUME_REQUIRED')
            state=s.BlockerState(http,lease,backups,confirmation)
            barrier=r.hd.HttpDrainLease(r.hd.HttpDrain(http,cleaner=native.activation.cleaner),lease,raw)
            record,guard=_release(state,native,barrier,runtime,source,payload,authority,record,cancel)
            try:result=record.start_services(guard)
            finally:guard._closed=True
    else:
        require(current['state']=='SERVING' and action in ('resume','check'),'MOBILE_ACTIVATION_ACTION_REJECTED')
        record=t.ActivationRecord.load(native,backups,lease_id,confirmation)
        if action=='check':require(record.read('done.json') is not None,'MOBILE_ACTIVATION_INCOMPLETE')
        with record.serving_lock() as guard:result=record.start_services(guard,check_only=action=='check')
    # Requests can pass only after the actual exclusive activity lock closes.
    # Probe failure never stops/restarts units or makes rollback permissible.
    availability=native.activation.check()
    result.update({'local_web':availability,'state':'MOBILE_SERVICES_RUNNING_LOCAL_WEB_AVAILABLE',
        'public_tls_verified':False,'boot_persistence':False,'phase6_complete':False})
    return result


@t.closed
def continue_serving(http,backups,lease_id,confirmation,*,action,confirmed):
    """Explicit post-admission recovery/check, without SQL credentials or export.

    A still-closed maintenance gate cannot enter this path. The original native
    record and activity lock remain the only authority for every service action.
    """
    require(confirmed is True,'MOBILE_ACTIVATION_CONSENT_REQUIRED')
    require(type(http) is v.h.HttpRuntime and isinstance(backups,Path) and backups.is_absolute()
        and type(lease_id) is str and re.fullmatch('[a-f0-9]{32}',lease_id)
        and type(confirmation) is str and re.fullmatch('[a-f0-9]{64}',confirmation)
        and action in ('resume','check'),'MOBILE_ACTIVATION_INPUT_REJECTED')
    root=backups/('mobile-resume-'+lease_id)
    require(f._sha(e._read_path(root,'plan.json',s.p.MAX_PLAN))==confirmation,
        'MOBILE_ACTIVATION_CONFIRMATION_REQUIRED')
    native=v.NativeRuntime(http,e._read_path(root,'http-drain-original.json',32768),confirmation)
    require(native.scope.observe()['state']=='SERVING','MOBILE_ACTIVATION_MAINTENANCE_REQUIRED')
    record=t.ActivationRecord.load(native,backups,lease_id,confirmation)
    if action=='check':require(record.read('done.json') is not None,'MOBILE_ACTIVATION_INCOMPLETE')
    with record.serving_lock() as guard:result=record.start_services(guard,check_only=action=='check')
    result.update({'local_web':native.activation.check(),'state':'MOBILE_SERVICES_RUNNING_LOCAL_WEB_AVAILABLE',
        'public_tls_verified':False,'boot_persistence':False,'phase6_complete':False})
    return result
