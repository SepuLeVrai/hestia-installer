#!/usr/bin/env python3
"""Real PHP request barriers on disposable Web/SQL fixtures, no system activation."""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(Path(__file__).resolve().parent)]
from installer import maintenance as m
import upgrade_backup_mariadb as previous

WEB=None


class MaintenanceLive(previous.BackupLive):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_MAINTENANCE_TEST')!='1':raise RuntimeError('Explicit maintenance opt-in required')
        previous.WEB=WEB
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.scope=m.MaintenanceScope(self.root/'maintenance',self.web.pw_gid,'7'*32)
        self.scope.create(confirmed=True)
        self.guard=self.scope.directory/'request_guard.php'

    def test_maintenance_real_http_login_session_survives_explicit_resume(self):
        self.managed_ready()
        with self.http(prepend=self.guard) as request:
            status,body,_,_=request('/login.php');self.assertEqual(status,200)
            token=re.search(r'name="csrf_token" value="([a-f0-9]+)"',body).group(1)
            status,_,_,_=request('/login.php',{'csrf_token':token,'identifier':self.payload['administrator']['email'],
                'password':self.payload['secrets']['admin_password']});self.assertEqual(status,200)
            before=self.logical_dump()
            with self.scope.acquire(confirmed=True) as lease:
                lease.assert_held()
                status,body,headers,_=request('/index.php')
                self.assertEqual(status,503);self.assertEqual(headers['Cache-Control'],'no-store')
                self.assertNotIn('Dashboard',body)
                self.assertEqual(before,self.logical_dump())
                with self.assertRaisesRegex(m.MaintenanceError,'MAINTENANCE_ACTIVE'):
                    with self.scope.writer():self.fail('Writer entered maintenance')
                lease.resume(confirmed=True)
            status,_,_,url=request('/index.php');self.assertEqual(status,200);self.assertNotIn('login.php',url)
            status,_,_,url=request('/logout.php');self.assertEqual(status,200);self.assertIn('login.php',url)
            self.assertTrue((self.scope.directory/('resumed-'+lease.lease_id+'.json')).is_file())

    def test_maintenance_drains_an_actual_inflight_php_request(self):
        self.managed_ready()
        marker=self.root/'request-entered';marker.touch();marker.chmod(0o600);os.chown(marker,self.web.pw_uid,self.web.pw_gid)
        # Test-only endpoint outside the product source pin, controlled by this fixture.
        (self.webroot/'delay-fixture.php').write_text('<?php file_put_contents(hex2bin("'+os.fsencode(marker).hex()+'"),"entered"); usleep(800000); echo "done";')
        with self.http(prepend=self.guard) as request:
            outcome=[]
            thread=threading.Thread(target=lambda:outcome.append(request('/delay-fixture.php')),daemon=True);thread.start()
            deadline=time.monotonic()+5
            while marker.read_bytes()!=b'entered' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(marker.read_bytes(),b'entered')
            with self.assertRaisesRegex(m.MaintenanceError,'MAINTENANCE_DRAIN_TIMEOUT'):
                self.scope.acquire(confirmed=True,timeout=.1)
            thread.join(5);self.assertFalse(thread.is_alive());self.assertEqual(outcome[0][0],200)
            self.assertEqual(request('/login.php')[0],503)
            state=self.scope.observe()
            with self.scope.recover(state['lease_id'],confirmed=True) as lease:
                lease.assert_held();lease.resume(confirmed=True)
            self.assertEqual(request('/login.php')[0],200)

    def test_maintenance_controller_death_preserves_gate_and_refuses_blind_retry(self):
        self.managed_ready();context=multiprocessing.get_context('fork');queue=context.Queue()
        def child():
            lease=self.scope.acquire(confirmed=True)
            queue.put(lease.lease_id);queue.close();queue.join_thread();os._exit(92)
        with self.http(prepend=self.guard) as request:
            process=context.Process(target=child);process.start();process.join(10)
            if process.is_alive():process.kill();process.join();self.fail('Controller did not exit')
            self.assertEqual(process.exitcode,92);lease_id=queue.get(timeout=2);queue.close();queue.join_thread()
            self.assertEqual(request('/login.php')[0],503)
            with self.assertRaisesRegex(m.MaintenanceError,'MAINTENANCE_PENDING'):self.scope.acquire(confirmed=True)
            with self.assertRaisesRegex(m.MaintenanceError,'MAINTENANCE_RECOVERY_MISMATCH'):
                self.scope.recover('8'*32,confirmed=True)
            with self.scope.recover(lease_id,confirmed=True) as lease:
                self.assertEqual(request('/login.php')[0],503);lease.resume(confirmed=True)
            self.assertEqual(request('/login.php')[0],200)

    def test_maintenance_non_http_writer_participates_in_same_barrier(self):
        with self.scope.writer():
            with self.assertRaisesRegex(m.MaintenanceError,'MAINTENANCE_DRAIN_TIMEOUT'):
                self.scope.acquire(confirmed=True,timeout=.05)
        state=self.scope.observe()
        with self.scope.recover(state['lease_id'],confirmed=True) as lease:lease.assert_held()
        self.assertEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')
        with self.assertRaisesRegex(m.MaintenanceError,'MAINTENANCE_ACTIVE'):
            with self.scope.writer():self.fail('Writer reopened on context exit')
        with self.scope.recover(state['lease_id'],confirmed=True) as lease:lease.resume(confirmed=True)
        with self.scope.writer():pass

    def test_maintenance_permissions_links_and_profile_drift_are_refused(self):
        with self.assertRaises(Exception):self.scope.create(confirmed=True)
        lock=self.scope.directory/'activity.lock'
        lock.chmod(0o660)
        with self.assertRaises(Exception):self.scope.acquire(confirmed=True)
        lock.chmod(0o640)
        linked=self.root/'linked-lock';os.link(lock,linked)
        with self.assertRaises(Exception):self.scope.acquire(confirmed=True)
        linked.unlink()
        lock.unlink();lock.symlink_to(self.root/'absent')
        with self.assertRaises(Exception):self.scope.acquire(confirmed=True)
        lock.unlink();lock.touch();lock.chmod(0o640);os.chown(lock,0,self.web.pw_gid)
        with self.scope.acquire(confirmed=True) as lease:
            self.guard.write_bytes(self.guard.read_bytes()+b'\n')
            with self.assertRaisesRegex(m.MaintenanceError,'MAINTENANCE_PROFILE_REJECTED'):lease.assert_held()
        self.assertTrue((self.scope.directory/'maintenance.attempt').exists())

    def test_maintenance_missing_consent_and_precancel_create_no_gate(self):
        for value in (False,1,'true',None):
            with self.assertRaisesRegex(m.MaintenanceError,'MAINTENANCE_CONSENT_REQUIRED'):
                self.scope.acquire(confirmed=value)
        event=threading.Event();event.set()
        with self.assertRaisesRegex(m.MaintenanceError,'MAINTENANCE_INTERRUPTED'):
            self.scope.acquire(confirmed=True,cancel=event)
        self.assertEqual(self.scope.observe()['state'],'SERVING')

    def test_maintenance_stale_or_cross_process_lease_never_authorizes_mutation(self):
        context=multiprocessing.get_context('fork');queue=context.Queue()
        with self.scope.acquire(confirmed=True) as lease:
            def child():
                try:lease.assert_held();queue.put('UNSAFE')
                except m.MaintenanceError:queue.put('REFUSED')
            process=context.Process(target=child);process.start();process.join(5)
            if process.is_alive():process.kill();process.join();self.fail('Lease child did not exit')
            self.assertEqual(queue.get(timeout=2),'REFUSED');queue.close();queue.join_thread()
        with self.assertRaisesRegex(m.MaintenanceError,'MAINTENANCE_LEASE_REQUIRED'):lease.assert_held()
        self.assertEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--web',type=Path,required=True);parser.add_argument('--report',type=Path)
    args=parser.parse_args();WEB=args.web.resolve()
    names=[n for n in unittest.defaultTestLoader.getTestCaseNames(MaintenanceLive) if n.startswith('test_maintenance_')]
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(MaintenanceLive(n) for n in names))
    report={'suite':'Cooperative Web maintenance, real PHP and SQL','tests':result.testsRun,'expected':len(names),
        'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'status':'PASS' if result.wasSuccessful() and result.testsRun==len(names) and names and not result.skipped else 'FAIL'}
    if args.report:args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report));raise SystemExit(report['status']!='PASS')
