#!/usr/bin/env python3
"""Explicit opt-in 5B2.3 cross-repository SQL/TLS/HTTP recipe, disposable only.

No deployed SQL server is reused. Source pins are production pins. Faults are
injected at process/filesystem boundaries, never by loosening a production pin.
"""
import argparse
import copy
import http.cookiejar
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(Path(__file__).resolve().parent)]
from installer import finalization as f
from installer import database_step as step
from installer import php_transport as p
import database_step_mariadb as previous

WEB=None


class FinalizationIntegration(previous.DatabaseStepIntegration):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_FINALIZATION_TEST')!='1':raise RuntimeError('Explicit finalization opt-in required')
        if WEB is None:raise RuntimeError('Exact Web source required')
        previous.WEB=WEB
        super().setUpClass()

    def setUp(self):
        super().setUp()
        shutil.copytree(WEB,self.webroot,dirs_exist_ok=True,ignore=shutil.ignore_patterns('.git','.github','docs','tests','__pycache__'))
        self.final=f.FinalizationStep(self.runtime,WEB,repository=p.WEB_REPOSITORY,commit=f.WEB_COMMIT)

    def finalize(self):
        return self.final.finalize(self.payload,config_root=self.output,confirmed=True)

    def finish(self):
        result=self.prepare();self.assertEqual(result['state'],'DATABASE_CONFIGURATION_READY',result)
        result=self.finalize();self.assertEqual(result['state'],'WEB_FRESH_FINALIZED',result)
        return result

    def existing(self,action='preserve',key=''):
        value=copy.deepcopy(self.payload);value.update(mode='upgrade',administrator=None)
        if value['database']['mode']=='managed':value['database']['mode']='existing_local'
        value['secrets'].update(admin_password='',openai_api_key=key);value['assistant']['action']=action
        return value

    def observe(self):
        return self.final.observe(self.existing(),config_root=self.output)

    def settings(self,action='preserve',key=''):
        return self.final.configure_assistant(self.existing(action,key),config_root=self.output,confirmed=True)

    @contextmanager
    def http(self):
        sessions=self.root/'sessions';sessions.mkdir(exist_ok=True)
        os.chown(sessions,self.web.pw_uid,self.web.pw_gid);sessions.chmod(0o700)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        log=(self.root/'http.log').open('wb')
        command=['setpriv','--reuid='+str(self.web.pw_uid),'--regid='+str(self.web.pw_gid),'--clear-groups','--no-new-privs',
                 str(self.runtime.php),'-d','display_errors=0','-d','log_errors=1','-d','zend.exception_ignore_args=1',
                 '-d','session.save_path='+str(sessions),'-S','127.0.0.1:'+str(port),'-t',str(self.webroot)]
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,cwd=self.webroot,
            env={'PATH':'/usr/bin:/bin','LANG':'C','TZ':'UTC'},start_new_session=True)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def request(path,data=None):
            wire=urllib.parse.urlencode(data).encode() if data is not None else None
            try:
                response=opener.open('http://127.0.0.1:'+str(port)+path,data=wire,timeout=15)
            except urllib.error.HTTPError as error:response=error
            with response:
                return response.status,response.read().decode('utf-8'),dict(response.headers),response.geturl()
        try:
            for _ in range(80):
                if process.poll() is not None:raise RuntimeError('HTTP fixture start failed')
                try:
                    request('/login.php');break
                except urllib.error.URLError:time.sleep(.05)
            else:raise RuntimeError('HTTP fixture start timeout')
            yield request
        finally:
            process.terminate();process.wait(timeout=10);log.close()
            text=(self.root/'http.log').read_text(errors='replace')
            for secret in self.payload['secrets'].values():
                if secret:self.assertNotIn(secret,text)

    def web_helper(self,action,key='',clear=False):
        script=r'''$v=json_decode(stream_get_contents(STDIN),true);define('APP_ROOT',$v['root']);
function is_admin_general():bool {global $v;return $v['action']!=='denied';}
function pa_rbac_can(string $a,string $b):bool {global $v;return $v['action']!=='denied';}
try { require APP_ROOT.'/includes/db.php';require APP_ROOT.'/includes/functions.php';require APP_ROOT.'/includes/assistant/config.php';
putenv('HESTIA_OPENAI_API_KEY=environment-fixture-AAAAAAAAAAAAAAAAAAAA');
putenv('OPENAI_API_KEY=other-environment-fixture-BBBBBBBBBBBBBBBBB');
putenv('HESTIA_AI_CONFIG_FILE='.$v['trap']);
if($v['action']==='save'||$v['action']==='denied') hestia_assistant_save_api_key($v['key'],$v['clear']);
echo json_encode(['configured'=>hestia_assistant_api_key()!=='','enabled'=>hestia_assistant_enabled(),
'key_matches'=>hash_equals($v['key'],hestia_assistant_api_key()),'environment_managed'=>hestia_assistant_api_key_status()['environment_managed']]);
} catch(Throwable $e){echo json_encode(['error'=>'HELPER_REFUSED']);exit(20);}'''
        value={'root':str(self.webroot),'action':action,'key':key,'clear':clear,'trap':str(self.root/'trap.php')}
        trap=self.root/'trap.php';trap.write_text('<?php file_put_contents('+repr(str(self.root/'EVALUATED'))+',"bad");return [];')
        out=subprocess.run(['setpriv','--reuid='+str(self.web.pw_uid),'--regid='+str(self.web.pw_gid),'--clear-groups',
            str(self.runtime.php),'-d','display_errors=0','-d','log_errors=0','-d','zend.exception_ignore_args=1','-r',script],
            input=json.dumps(value).encode(),capture_output=True,timeout=10)
        self.assertEqual(out.stderr,b'');self.assertFalse((self.root/'EVALUATED').exists())
        return out.returncode,json.loads(out.stdout)

    def test_final_concurrent_controllers_never_both_finalize(self):
        self.prepare();context=multiprocessing.get_context('fork');ready=context.Event();results=context.Queue()
        def child():
            if not ready.wait(10):return
            try:results.put(self.finalize()['state'])
            except f.FinalizationError:results.put('REFUSED')
        processes=[context.Process(target=child) for _ in range(2)]
        for process in processes:process.start()
        ready.set()
        for process in processes:
            process.join(30)
            if process.is_alive():process.kill();process.join();self.fail('Concurrent controller timed out')
            self.assertEqual(process.exitcode,0)
        states=[results.get(timeout=2) for _ in range(2)];results.close();results.join_thread()
        self.assertEqual(states.count('WEB_FRESH_FINALIZED'),1,states)
        self.assertTrue(all(state in ('WEB_FRESH_FINALIZED','REFUSED','MANUAL_ACTION') for state in states))
        self.assertEqual(self.observe()['state'],'WEB_FRESH_FINALIZED')
        self.assertEqual(self.sql(query=f'SELECT COUNT(*) n FROM `{self.db}`.UserInfo')[0]['n'],1)

    def test_final_privileges_rechecked_on_existing_observation(self):
        self.finish();self.sql([f'GRANT ALTER ON {self.schema} TO {self.app_account}'])
        self.assertEqual(self.observe()['state'],'MANUAL_ACTION')
        before=(self.directory/'assistant.json').read_bytes()
        with self.assertRaises(f.FinalizationError):self.settings('configure','P'*40)
        self.assertEqual(before,(self.directory/'assistant.json').read_bytes())
        self.assertFalse(list(self.directory.glob('assistant-*.attempt')))

    def test_final_disabled_full_fresh_and_readonly_observation(self):
        result=self.finish();self.assertFalse(result['result']['assistant_enabled'])
        self.assertTrue(result['result']['installation_sealed']);self.assertFalse(result['result']['application_installed'])
        self.assertEqual(result,self.observe())
        self.assertEqual(self.web_helper('read')[1]['configured'],False)

    def test_final_managed_sql_account_retired_before_activation(self):
        self.managed();self.assertEqual(self.prepare(authority=self.authority)['state'],'DATABASE_CONFIGURATION_READY')
        self.assertEqual(self.finalize()['state'],'WEB_FRESH_FINALIZED')
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM mysql.global_priv WHERE User='{self.migration}'")[0]['n'],0)
        self.assertEqual(self.observe()['state'],'WEB_FRESH_FINALIZED')

    def test_final_key_and_existing_keep_replace_disable(self):
        key='A'*20;self.payload['assistant']['action']='configure';self.payload['secrets']['openai_api_key']=key
        self.assertTrue(self.finish()['result']['assistant_enabled'])
        before=self.sql(query=f'SELECT id_user,password_hash,nom,prenom,email FROM `{self.db}`.UserInfo')
        for action in ('preserve','configure'):
            self.assertTrue(self.settings(action)['result']['assistant_enabled'])
            self.assertTrue(self.web_helper('read',key)[1]['key_matches'])
        key='B'*500;self.assertTrue(self.settings('configure',key)['result']['assistant_enabled'])
        self.assertTrue(self.web_helper('read',key)[1]['key_matches'])
        self.assertFalse(self.settings('disabled')['result']['assistant_enabled'])
        self.assertTrue(self.web_helper('read',key)[1]['key_matches'])
        self.assertFalse(self.settings('configure','')['result']['assistant_enabled'])
        self.assertEqual(before,self.sql(query=f'SELECT id_user,password_hash,nom,prenom,email FROM `{self.db}`.UserInfo'))
        serialized=''.join((self.directory/name).read_text() for name in ('state.json','seal.json','finalized.json','finalization.attempt'))
        self.assertNotIn(key,serialized)

    def test_final_unicode_long_names_login_dashboard_logout(self):
        self.payload['administrator'].update(first_name='Élise <&> '+('語'*90),last_name="D'Angelo "+('Z'*90))
        self.finish()
        with self.http() as request:
            status,body,headers,url=request('/install.php');self.assertEqual(status,403);self.assertIn('Installation verrouillée',body)
            status,body,headers,url=request('/login.php');self.assertEqual(status,200)
            token=re.search(r'name="csrf_token" value="([a-f0-9]+)"',body).group(1)
            status,bad,_,_=request('/login.php',{'csrf_token':'wrong','identifier':self.payload['administrator']['email'],'password':self.payload['secrets']['admin_password']})
            self.assertEqual(status,403)
            status,body,_,_=request('/login.php');token=re.search(r'name="csrf_token" value="([a-f0-9]+)"',body).group(1)
            status,body,_,url=request('/login.php',{'csrf_token':token,'identifier':self.payload['administrator']['email'],'password':self.payload['secrets']['admin_password']})
            self.assertEqual(status,200);self.assertNotIn('/login.php',url)
            self.assertIn('D&#039;Angelo',body);self.assertNotIn('Élise <&>',body)
            self.assertNotIn(self.payload['secrets']['admin_password'],body)
            status,body,_,url=request('/logout.php');self.assertEqual(status,200);self.assertIn('/login.php',url)
            status,body,_,url=request('/index.php');self.assertIn('/login.php',url)

    def test_final_sql_privilege_drift_is_reaudited(self):
        self.prepare();self.sql([f'GRANT ALTER ON {self.schema} TO {self.app_account}'])
        with self.assertRaisesRegex(f.FinalizationError,'ACCOUNT_POLICY_REJECTED'):self.finalize()
        self.assertFalse((self.webroot/'install.lock').exists())

    def test_final_wrong_administrator_password_is_not_reset(self):
        self.prepare();before=self.sql(query=f'SELECT password_hash FROM `{self.db}`.UserInfo')
        self.payload['secrets']['admin_password']='Another-admin-pass-2026'
        with self.assertRaisesRegex(f.FinalizationError,'FINALIZATION_DATABASE_INVALID'):self.finalize()
        self.assertEqual(before,self.sql(query=f'SELECT password_hash FROM `{self.db}`.UserInfo'))
        self.assertFalse((self.webroot/'includes/db.php').exists())

    def test_final_version_marker_alone_is_not_sufficient(self):
        self.prepare();self.sql([f'DROP TABLE `{self.db}`.Assistant_Query_Log'])
        with self.assertRaisesRegex(f.FinalizationError,'FINALIZATION_DATABASE_INVALID'):self.finalize()
        self.assertFalse((self.webroot/'install.lock').exists())

    def test_final_modified_source_is_not_executed(self):
        self.prepare();target=self.webroot/'includes/assistant/config.php';target.write_text('<?php throw new Exception("MUST NOT EXECUTE");')
        with self.assertRaisesRegex(f.FinalizationError,'SOURCE_PIN_MISMATCH'):self.finalize()
        self.assertFalse((self.directory/'finalization.attempt').exists())

    def test_final_unlisted_php_is_not_ignored(self):
        self.prepare();(self.webroot/'unexpected.php').write_text('<?php // unknown runtime')
        with self.assertRaisesRegex(f.FinalizationError,'SOURCE_PIN_MISMATCH'):self.finalize()
        self.assertFalse((self.webroot/'install.lock').exists())

    def test_final_remote_tls_full_activation_and_observation(self):
        self.remote();result=self.finish();self.assertTrue(result['result']['database_verified'])
        self.assertEqual(self.observe()['state'],'WEB_FRESH_FINALIZED')
        (self.directory/'ca.pem').write_text('bad CA fixture')
        self.assertEqual(self.observe()['state'],'MANUAL_ACTION')
        with self.http() as request:
            status,body,headers,_=request('/login.php');self.assertEqual(status,503)
            self.assertEqual(body,'HESTIA_ACTIVATION_NOT_READY');self.assertIn('no-store',headers.get('Cache-Control',''))

    def test_final_tls_failure_after_preparation_never_downgrades(self):
        self.remote();self.prepare();self.tls.start(None)
        with self.assertRaisesRegex(f.FinalizationError,'SQL_TLS_CONNECTION_FAILED'):self.finalize()
        self.assertFalse((self.webroot/'install.lock').exists())

    def test_final_pointer_without_seal_returns_503_and_form_is_locked(self):
        self.prepare();write=f._write
        def fail(fd,name,*args,**kwargs):
            if name=='install.lock':raise OSError('synthetic disk failure')
            return write(fd,name,*args,**kwargs)
        with patch.object(f,'_write',side_effect=fail):self.assertEqual(self.finalize()['state'],'MANUAL_ACTION')
        with self.http() as request:
            status,body,headers,_=request('/login.php');self.assertEqual(status,503);self.assertEqual(body,'HESTIA_ACTIVATION_NOT_READY')
            status,body,_,_=request('/install.php');self.assertEqual(status,403)
        with self.assertRaises(f.FinalizationError):self.finalize()

    def test_final_detected_post_activation_probe_failure_revokes_seal(self):
        self.prepare();probe=f._probe
        def fail(*args,active,**kwargs):
            result=probe(*args,active=active,**kwargs)
            if active:raise f.FinalizationError('FINALIZATION_RUNTIME_FAILED')
            return result
        with patch.object(f,'_probe',side_effect=fail):self.assertEqual(self.finalize()['state'],'MANUAL_ACTION')
        self.assertFalse((self.directory/'seal.json').exists());self.assertTrue((self.directory/'database.json').exists())
        self.assertEqual(self.observe()['state'],'MANUAL_ACTION')

    def test_final_process_death_after_seal_requires_observation_not_replay(self):
        self.prepare()
        def child():
            write=f._write
            def stop(fd,name,*args,**kwargs):
                result=write(fd,name,*args,**kwargs)
                if name=='seal.json':os._exit(97)
                return result
            with patch.object(f,'_write',side_effect=stop):self.finalize()
        process=multiprocessing.get_context('fork').Process(target=child);process.start();process.join(30)
        if process.is_alive():process.kill();process.join();self.fail('Injected process did not stop')
        self.assertEqual(process.exitcode,97);self.assertTrue((self.directory/'seal.json').exists())
        self.assertFalse((self.directory/'finalized.json').exists());self.assertEqual(self.observe()['state'],'MANUAL_ACTION')
        with self.assertRaises(f.FinalizationError):self.finalize()

    def test_final_lost_response_after_receipt_is_observable_without_replay(self):
        self.prepare()
        def child():
            write=f._write
            def stop(fd,name,*args,**kwargs):
                result=write(fd,name,*args,**kwargs)
                if name=='finalized.json':os._exit(98)
                return result
            with patch.object(f,'_write',side_effect=stop):self.finalize()
        process=multiprocessing.get_context('fork').Process(target=child);process.start();process.join(30)
        if process.is_alive():process.kill();process.join();self.fail('Injected process did not stop')
        self.assertEqual(process.exitcode,98);self.assertEqual(self.observe()['state'],'WEB_FRESH_FINALIZED')
        with self.assertRaises(f.FinalizationError):self.finalize()

    def test_final_web_key_management_keeps_rbac_and_explicit_clear(self):
        self.finish();key='web-fixture-'+('K'*40)
        self.assertEqual(self.web_helper('denied',key)[0],20)
        self.assertFalse(self.web_helper('read')[1]['configured'])
        self.assertEqual(self.web_helper('save',key)[0],0)
        self.assertTrue(self.web_helper('save','')[1]['configured'])
        self.assertTrue(self.web_helper('read',key)[1]['key_matches'])
        self.assertFalse(self.web_helper('save','',True)[1]['configured'])
        self.assertFalse(self.web_helper('read')[1]['environment_managed'])

    def test_final_corrupt_key_store_has_no_environment_or_php_fallback(self):
        self.payload['assistant']['action']='configure';self.payload['secrets']['openai_api_key']='Z'*40
        self.finish();(self.directory/'assistant.json').write_text('{')
        status,result=self.web_helper('read');self.assertEqual(status,0)
        self.assertFalse(result['configured']);self.assertFalse(result['enabled']);self.assertFalse(result['environment_managed'])

    def test_final_existing_data_unusual_numeric_values_are_preserved(self):
        self.finish();text="  D'Exemple <&> 漢字🙂 "+'x'*4000
        self.sql([f'CREATE TABLE `{self.db}`.preservation(id BIGINT UNSIGNED PRIMARY KEY, value TEXT)',
            f'INSERT INTO `{self.db}`.preservation VALUES(18446744073709551615,{previous.literal(text)})'])
        before=self.sql(query=f'SELECT id, SHA2(value,256) hash FROM `{self.db}`.preservation')
        self.settings('configure','R'*40);self.settings('disabled');self.settings('preserve')
        self.assertEqual(before,self.sql(query=f'SELECT id, SHA2(value,256) hash FROM `{self.db}`.preservation'))

    def test_final_settings_interruption_is_not_blindly_retried(self):
        self.finish();probe=f._probe
        def lost(*args,action='preserve',**kwargs):
            result=probe(*args,action=action,**kwargs)
            if action!='preserve':raise f.FinalizationError('FINALIZATION_RUNTIME_FAILED')
            return result
        with patch.object(f,'_probe',side_effect=lost):
            self.assertEqual(self.settings('configure','S'*40)['state'],'MANUAL_ACTION')
        self.assertEqual(self.observe()['state'],'MANUAL_ACTION')
        with self.assertRaises(f.FinalizationError):self.settings('disabled')


def main():
    global WEB
    parser=argparse.ArgumentParser();parser.add_argument('--web',type=Path,required=True);parser.add_argument('--report',type=Path)
    args=parser.parse_args();WEB=args.web.resolve()
    names=[n for n in unittest.defaultTestLoader.getTestCaseNames(FinalizationIntegration) if n.startswith('test_final_')]
    suite=unittest.TestSuite(FinalizationIntegration(n) for n in names)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'suite':'5B2.3 cross-repository SQL/TLS/HTTP','tests':result.testsRun,'expected':len(names),
        'errors':len(result.errors),'failures':len(result.failures),'skips':len(result.skipped),
        'web_commit':f.WEB_COMMIT,'web_runtime_sha256':f.RUNTIME_SHA256,'api_access':'NOT_TESTED',
        'status':'PASS' if result.wasSuccessful() and result.testsRun==len(names) and not result.skipped else 'FAIL'}
    if args.report:args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report));return 0 if report['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
