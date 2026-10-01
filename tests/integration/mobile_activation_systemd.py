"""Final native SQL admission and recoverable ordered MAIN activation."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import signal
import stat
import time
import traceback
from unittest.mock import patch
from installer import mobile_activation_admission as n
from mobile_reopen_files_systemd import killed_at_boundary

t,v=n.t,n.v

def exercise(test,http,runtime,scope,lease_id,backups,worker,source,payload,authority,preserved,confirmation):
    root=backups/('mobile-activation-'+lease_id);stage='initial';fence_released=False
    timing_path=Path('/evidence/mobile-activation-timings.json')
    starts_path=Path('/evidence/mobile-activation-starts.json')
    saved={p:(p.read_bytes(),p.stat().st_uid,p.stat().st_gid,stat.S_IMODE(p.stat().st_mode))
        for p in (backups/('mobile-resume-'+lease_id)).iterdir()}
    original_acquire=n.a.c.rf.acquire;original_start=v.NativeRuntime.start
    def append(path,value):
        rows=json.loads(path.read_text()) if path.exists() else [];rows.append(value)
        path.write_text(json.dumps(rows,indent=2)+'\n')
    @contextmanager
    def fenced(*args,**kwargs):
        nonlocal fence_released
        fence_released=False
        with original_acquire(*args,**kwargs) as fence:
            started=fence._deadline-180
            append(timing_path,{'stage':stage,'event':'entered','elapsed_sql_seconds':round(time.monotonic()-started,6)})
            yield fence
            fence.assert_held()
            append(timing_path,{'stage':stage,'event':'consumer-complete','elapsed_sql_seconds':round(time.monotonic()-started,6)})
        fence_released=True
        append(timing_path,{'stage':stage,'event':'normally-released','elapsed_sql_seconds':round(time.monotonic()-started,6)})
    def start(native,role):
        test.assertTrue(fence_released)
        test.assertFalse((scope.directory/'maintenance.attempt').exists())
        test.assertTrue((scope.directory/('resumed-'+lease_id+'.json')).exists())
        test.assertTrue((root/(role+'.intent.json')).exists())
        with test.assertRaises(n.r.hd.m.MaintenanceError):
            with scope.writer():test.fail('writer admitted during ordered activation')
        original_start(native,role)
        append(starts_path,{'role':role,'unit':native.unit(role),'actual_start':True})
        if stage=='sigkill-after-php' and role=='php':os.kill(os.getpid(),signal.SIGKILL)
    def invoke(action):
        try:
            with patch.object(n.a.c.rf,'acquire',side_effect=fenced),patch.object(v.NativeRuntime,'start',start):
                return n.execute(http,scope,lease_id,backups,worker,source,payload,authority,confirmation,
                    action=action,confirmed=True,allow_global_read_lock=True)
        except BaseException as error:
            chain=[];current=error
            while current is not None and len(chain)<6:
                code=str(current)
                chain.append({'type':type(current).__name__,'code':code if re.fullmatch('[A-Z][A-Z0-9_]{1,80}',code) else 'REDACTED',
                    'frames':[{'file':Path(x.filename).name,'line':x.lineno,'function':x.name}
                        for x in traceback.extract_tb(current.__traceback__)]})
                current=current.__context__
            Path('/evidence/mobile-activation-error.json').write_text(json.dumps({'stage':stage,'chain':chain},indent=2)+'\n')
            raise
    stage='sql-drift-before-plan'
    test.sql([f'CREATE TABLE `{test.db}`.Hestia_Activation_Drift (id INT PRIMARY KEY)'])
    try:
        with test.assertRaises(v.ActivationError):invoke('apply')
    finally:test.sql([f'DROP TABLE `{test.db}`.Hestia_Activation_Drift'])
    test.assertFalse(root.exists());test.assertTrue((scope.directory/t.s.MARKER).exists())
    test.assertEqual(scope.observe()['state'],'MAINTENANCE_REQUIRED');runtime.stopped();runtime.foundation.stopped()
    stage='sigkill-after-php';killed_at_boundary(test,lambda:invoke('apply'))
    test.assertEqual(scope.observe()['state'],'SERVING')
    test.assertTrue((root/'php.intent.json').exists());test.assertFalse((root/'php.started.json').exists())
    test.assertFalse((root/'apache.intent.json').exists())
    test.assertEqual([x['role'] for x in json.loads(starts_path.read_text())],['php'])
    native=v.NativeRuntime(http,(backups/('mobile-resume-'+lease_id)/'http-drain-original.json').read_bytes(),confirmation)
    first=native.observed('php',active=True)
    # SQL is now legitimately mutable. Serving recovery must neither export
    # historical SQL again nor stop/reclose already admitted activity.
    stage='serving-explicit-resume';fence_released=True
    with patch.object(n.a.c,'_recheck',side_effect=AssertionError('SQL recheck after admission')), \
            patch.object(n.r.hd.HttpDrain,'recover',side_effect=AssertionError('drain replay')):
        result=invoke('resume')
    test.assertEqual(native.observed('php',active=True),first)
    test.assertEqual([x['role'] for x in json.loads(starts_path.read_text())],list(v.ROLES))
    test.assertTrue(result['services_started']);test.assertTrue(result['local_web']['login_page'])
    test.assertFalse(result['boot_persistence']);test.assertFalse(result['phase6_complete'])
    before={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in root.iterdir()}
    stage='completed-read-only-check'
    with patch.object(t.ActivationRecord,'save',side_effect=AssertionError('completed check wrote a record')):
        checked=invoke('check')
    test.assertTrue(checked['local_web']['login_page'])
    test.assertEqual(before,{p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in root.iterdir()})
    runtime.owned();runtime.foundation.owned()
    with scope.writer():pass
    for path,raw in preserved.items():test.assertEqual(path.read_bytes(),raw)
    for path,expected in saved.items():
        info=path.stat();test.assertEqual((path.read_bytes(),info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode)),expected)
    timings=json.loads(timing_path.read_text())
    complete=[row for row in timings if row['stage']=='sigkill-after-php']
    test.assertEqual([x['event'] for x in complete],['entered','consumer-complete','normally-released'])
    test.assertTrue(all(0<=x['elapsed_sql_seconds']<180 for x in complete))
    proof={'status':'PASS','resume_plan_sha256':confirmation,'actual_final_sql_export':True,
        'sql_drift_refused_before_plan':True,'sql_fence_released_before_first_start':True,
        'maintenance_last_blocker':True,'actual_activity_lock_held_across_all_starts':True,
        'sigkill_after_actual_php_start':True,'lost_reply_adopted_without_second_start':True,
        'five_starts_in_bound_order':True,'serving_resume_without_sql_export_or_drain':True,
        'completed_check_read_only':True,'parent_journals_bytes_modes_owners_preserved':True,
        'local_login_page_available':True,'native_foundation_and_gateway_owned':True,
        'services_started':True,'activity_resumed':True,'sql_read_fence_max_seconds':180,
        'window_timings':complete,'completed_fresh_windows':1,'boot_persistence':False,
        'public_tls_verified':False,'phase6_complete':False}
    Path('/evidence/mobile-activation-native.json').write_text(json.dumps(proof,indent=2)+'\n')
    return proof
