"""Explicit orphan repair under an already established, instance-bound maintenance.

No public route, implicit backup repair, automatic retry or automatic resume.
The caller must have wired every producer to the guarded deployment contract.
"""
from __future__ import annotations

import copy
import os
import re
import tempfile
from contextlib import ExitStack
from pathlib import Path

from installer import database_config as fs
from installer import database_step as d
from installer import finalization as f
from installer import maintenance as m
from installer import php_transport as p
from installer import upgrade_backup as b
from installer.model import strict_json_loads

ERRORS=frozenset({'REQUEST_INVALID','ACCOUNT_SEPARATION_REQUIRED','ACCOUNT_POLICY_REJECTED','AUDIT_UNAVAILABLE',
    'REPAIR_PROFILE_REJECTED','REPAIR_WRITERS_ACTIVE','REPAIR_BUSY','REPAIR_SOURCE_CHANGED','REPAIR_WORKER_FAILED',
    'BACKUP_AUTHORITY_REJECTED','BACKUP_RESCUE_PROFILE_REJECTED','BACKUP_TRIGGER_PROFILE_REJECTED',
    'BACKUP_PROFILE_REJECTED','BACKUP_SOURCE_CHANGED','BACKUP_LIMIT','BACKUP_SPECIAL_OBJECTS_UNSUPPORTED',
    'DEFINER_ACCOUNT_OCCUPIED','DEFINER_PROFILE_REJECTED','DEFINER_TRIGGER_PROFILE_REJECTED','DEFINER_REBIND_FAILED'})


class DefinerRepairError(RuntimeError):
    """Fixed diagnostic, never raw SQL or credential-bearing subprocess errors."""


def require(ok: bool, code: str) -> None:
    if not ok:raise DefinerRepairError(code)


def _stage(runtime,source,stage):
    b._worker_stage(runtime,source,stage,None)
    os.rename(stage/'bridge.php',stage/'backup_bridge.php')
    with fs._directory(stage) as fd:
        f._write(fd,'bridge.php',p._read_file(Path(__file__).parent/'private/definer_repair_bridge.php'),runtime.worker_gid)


def _response(code: int, raw: bytes, request: dict) -> dict:
    try:
        value=strict_json_loads(raw)
        require(type(value) is dict and set(value)=={'version','request_id','ok','uncertain','result','error'}
            and type(value['version']) is int and value['version']==1 and value['request_id']==request['request_id']
            and type(value['ok']) is bool and type(value['uncertain']) is bool,'REPAIR_PROTOCOL_REJECTED')
        if not value['ok']:
            require(code==20 and value['result'] is None and value['error'] in ERRORS,'REPAIR_PROTOCOL_REJECTED')
            raise DefinerRepairError(value['error'])
        result=value['result']
        require(code==0 and value['error'] is None and value['uncertain'] is False
            and type(result) is dict and set(result)=={'state','triggers','business_rows_written','source_snapshot_preserved','logical_sha256','rebound_logical_sha256','tables','rows'}
            and result['state']=='DEFINERS_REBOUND' and type(result['triggers']) is int and result['triggers']==5
            and result['business_rows_written'] is False and result['source_snapshot_preserved'] is True
            and result['logical_sha256']==request['expected_logical_sha256']
            and type(result['rebound_logical_sha256']) is str and re.fullmatch(r'[a-f0-9]{64}',result['rebound_logical_sha256']) is not None
            and type(result['tables']) is int and 6<=result['tables']<=512
            and type(result['rows']) is str and re.fullmatch(r'0|[1-9][0-9]{0,6}',result['rows']) is not None,
            'REPAIR_PROTOCOL_REJECTED')
        return result
    except DefinerRepairError:raise
    except Exception:raise DefinerRepairError('REPAIR_PROTOCOL_REJECTED') from None


class DefinerRepair:
    def __init__(self,runtime:p.PhpRuntime,source:Path,*,repository:str,commit:str):
        require(repository == p.WEB_REPOSITORY, 'SOURCE_PIN_MISMATCH')
        try: self.release = f.get_release(commit)
        except ValueError: raise DefinerRepairError('SOURCE_PIN_MISMATCH') from None
        self.runtime,self.source=runtime,Path(source)

    def repair(self,payload:dict,authority:d.SqlAuthorityCredentials,*,config_root:Path,backup_root:Path,
               maintenance:m.MaintenanceLease,expected_orphaned_definer:str,confirmed:bool,
               allow_global_read_lock:bool,cancel=None) -> dict:
        started=False
        try:
            require(confirmed is True and allow_global_read_lock is True,'REPAIR_CONSENT_REQUIRED')
            require(type(maintenance) is m.MaintenanceLease,'REPAIR_MAINTENANCE_REQUIRED')
            maintenance.assert_held()
            value=copy.deepcopy(payload);config=f._configuration(value,fresh=False)
            with ExitStack() as stack:
                gid,web,directory,conf,webfd,inc=f._open(self.runtime,config,config_root,stack)
                current=f.FinalizationStep(self.runtime,self.source,repository=p.WEB_REPOSITORY,commit=self.release.commit)
                current._sources(web)
                database,loader,ca=f._prepared(config,value,directory,conf,gid)
                completed=f._completed(conf,webfd,inc,gid,commit=self.release.commit)
                seal=f._json_read(conf,'seal.json',gid)
                require(maintenance.scope.instance==seal['instance'] and maintenance.scope.web_gid==gid
                    and maintenance.scope.directory==directory/'maintenance','REPAIR_MAINTENANCE_BINDING_REQUIRED')
                require(config['database']['mode']=='existing_local' and database['host']=='127.0.0.1'
                    and database['port']==3306 and ca is None
                    and f._json_read(conf,'state.json',gid)['migration_retained'] is False,'REPAIR_PROFILE_REJECTED')
                state=stack.enter_context(fs._directory(self.runtime.state_root))
                name='definer-repair-'+f._sha(p._json([database['host'],database['port'],database['name'].lower()]))
                try:fs._absent(state,name+'.attempt');fs._absent(state,name+'.done')
                except fs.AccountConfigurationError:raise DefinerRepairError('REPAIR_PENDING') from None
                backup=b.UpgradeBackup(self.runtime,self.source,repository=p.WEB_REPOSITORY,commit=self.release.commit)
                rescue=backup.create_rescue_and_verify(value,authority,config_root=config_root,backup_root=backup_root,
                    confirmed=True,allow_global_read_lock=True,expected_orphaned_definer=expected_orphaned_definer,cancel=cancel).report()
                require(rescue.get('state')=='RESCUE_RESTORE_VERIFIED'
                    and rescue.get('rescue_restoration_verified') is True,'REPAIR_RESCUE_REQUIRED')
                maintenance.assert_held()
                current._sources(web)
                require(f._completed(conf,webfd,inc,gid,commit=self.release.commit)==completed
                    and f._prepared(config,value,directory,conf,gid)==(database,loader,ca),'REPAIR_SOURCE_CHANGED')
                require(cancel is None or not cancel.is_set(),'REPAIR_INTERRUPTED')
                request_id=os.urandom(16).hex()
                attempt={'version':1,'state':'DISPATCHING','request_id':request_id,'instance':seal['instance'],
                    'maintenance_lease_id':maintenance.lease_id,'rescue_backup_id':rescue['backup_id'],
                    'rescue_database_sha256':rescue['database_sha256'],'expected_logical_sha256':rescue['logical_sha256'],
                    'expected_orphaned_definer':expected_orphaned_definer}
                # A partial reservation also blocks replay, including an fsync failure.
                started=True;f._write(state,name+'.attempt',p._json(attempt),0,mode=0o600)
                with tempfile.TemporaryDirectory(prefix='dr-',dir=self.runtime.run_root) as tmp:
                    stage=Path(tmp);_stage(self.runtime,self.source,stage)
                    request={'version':1,'operation':'repair_definers','request_id':request_id,
                        'target':{k:database[k] for k in ('host','port','name','tls_required','tls_ca_file','tls_ca_sha256')},
                        'application':{'user':database['user'],'password':database['password']},
                        'authority':{'user':authority._user,'password':authority._password},
                        'expected_orphaned_definer':expected_orphaned_definer,'expected_logical_sha256':rescue['logical_sha256']}
                    code,raw=p._exchange(p._command(self.runtime,stage),p._json(request),stage,self.runtime.timeout_seconds,cancel)
                    result=_response(code,raw,request)
                maintenance.assert_held()
                require(f._completed(conf,webfd,inc,gid,commit=self.release.commit)==completed
                    and f._prepared(config,value,directory,conf,gid)==(database,loader,ca),'REPAIR_SOURCE_CHANGED')
                # Independent normal backup/restore proves the repaired identities
                # and all five effects. It never inserts probes into source data.
                verified=backup.create_and_verify(value,authority,config_root=config_root,backup_root=backup_root,
                    confirmed=True,allow_global_read_lock=True,cancel=cancel).report()
                require(verified.get('state')=='BACKUP_RESTORE_VERIFIED' and verified.get('trigger_smoke_verified')==5
                    and verified.get('tables')==result['tables'] and verified.get('rows')==result['rows']
                    and verified.get('logical_sha256')==result['rebound_logical_sha256'],
                    'REPAIR_POST_RESTORE_REQUIRED')
                manifests=[]
                for receipt in (rescue,verified):
                    raw,_=b._file(backup_root/receipt['backup_id']/'manifest.json')
                    require(f._sha(raw)==receipt['manifest_sha256'],'REPAIR_SOURCE_CHANGED')
                    manifests.append(strict_json_loads(raw))
                require(manifests[0]['files']==manifests[1]['files']
                    and manifests[0]['fresh_attempt_sha256']==manifests[1]['fresh_attempt_sha256'],'REPAIR_SOURCE_CHANGED')
                maintenance.assert_held()
                report={'version':1,'state':'DEFINER_REPAIR_VERIFIED','request_id':request_id,'instance':seal['instance'],
                    'maintenance_lease_id':maintenance.lease_id,'rescue_backup_id':rescue['backup_id'],
                    'verified_backup_id':verified['backup_id'],'source_business_data_preserved':True,
                    'source_business_rows_written':False,'durable_definers_verified':5,'isolated_trigger_smoke_verified':5,
                    'maintenance_required':True,'activity_resumed':False,'application_installed':False}
                f._write(state,name+'.done',p._json(report),0,mode=0o600)
                return report
        except Exception as error:
            code=str(error) if isinstance(error,DefinerRepairError) else 'REPAIR_OPERATION_UNAVAILABLE'
            if started:
                return {'state':'REPAIR_MANUAL_ACTION','code':code,'maintenance_required':True,
                    'activity_resumed':False,'automatic_retry_allowed':False,'application_installed':False}
            raise DefinerRepairError(code) from None
