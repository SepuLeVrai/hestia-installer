"""Successor units/guards on disposable PID 1, with a real frozen 6B10 bundle.

Apache/boot/Gateway are fixture dependencies, not a native application proof.
Issuance and dry-run are intercepted with locally trusted fixture certificates.
Renewal uses real Certbot (not-due lineages), real workers and real NGINX HUP.
"""
import argparse
from contextlib import ExitStack
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import traceback
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(Path(__file__).parent)]
from installer import shared_public_runtime as s
from installer import public_tls_profile as old_profile
from installer.model import canonical_bytes
from installer.operations import OperationContext, SecretVault, RecoveryDecision
from installer.transaction import StateJournal
from test_public_tls import profile
from test_shared_public_runtime import selected
from shared_mobile_tls_nginx import Backend, WEB, MOBILE
from http.server import ThreadingHTTPServer
from scripts import quality

FROZEN = Path('/opt/frozen-public-parent/installer')
PARENT = '14d7a8ca26e8c7f7a5c4f07acc5848fd452fd15f'


def command(argv, *, check=True, timeout=45):
    return subprocess.run(argv, capture_output=True, timeout=timeout, check=check)


def until(callback):
    end=time.monotonic()+10
    while time.monotonic()<end:
        try:
            value=callback()
            if value: return value
        except (OSError, AssertionError): pass
        time.sleep(.05)
    raise AssertionError('Bounded listener transition did not complete')


def context(operation): return OperationContext('a'*32,operation.spec.as_dict(),{},SecretVault())


class SharedSystemd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SHARED_PUBLIC_TEST')!='1' or os.geteuid()!=0 or not Path('/.dockerenv').exists() or Path('/proc/1/comm').read_text().strip()!='systemd':
            raise RuntimeError('Explicit disposable Debian 13 systemd required')
        if Path('/opt/frozen-public-parent-commit.txt').read_text().strip()!=PARENT: raise RuntimeError('Frozen parent pin mismatch')
        cls.stack=ExitStack(); cls.addClassCleanup(cls.stack.close)
        cls.default={unit:command(['/usr/bin/systemctl','is-active','--quiet',unit],check=False).returncode==0 for unit in ('apache2.service','nginx.service','certbot.timer')}
        command(['/usr/bin/systemctl','stop',*cls.default]); cls.stack.callback(cls.restore_defaults)
        for port in (80,443,9080,9083):
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); sock.bind(('0.0.0.0',port))
        value=profile(); instance=uuid.uuid4().hex
        value['boot']['application']={'instance':instance,'configuration':{'web':s.boot.app.FreshProfile(instance).web(WEB)}}
        value['choices']['networks']=['127.0.0.11/32']
        with patch.object(s.boot,'SOURCE',FROZEN): value['code']={name:s.f._sha(raw) for name,raw in s.boot.code_files().items()}
        cls.r=s.SharedPublic(selected(value)); cls.web=cls.r.web
        cls.layout=cls.web.layout.root; cls.layout.mkdir(mode=0o755)
        cls.stack.callback(shutil.rmtree,cls.layout)
        cls.web.identity.create(confirmed=True)
        cls.stack.callback(shutil.rmtree,cls.web.identity.directory)
        cls.stack.callback(command,['/usr/sbin/userdel',cls.web.identity.user])
        cls.global_files={str(p):p.read_bytes() for p in (Path('/etc/nginx/nginx.conf'),Path('/etc/apache2/apache2.conf'))}
        # Already-running dependencies isolate the public lifecycle. Their
        # actual generated private guard is tested separately as a refusal.
        cls.dependencies={cls.web.http.unit('apache'):b'[Service]\nExecStart=/usr/bin/sleep infinity\n',
                          cls.web.boot.target:b'[Unit]\nDescription=Disposable dependency fixture\n'}
        for name,raw in cls.dependencies.items():
            path=s.h.drain.UNIT_ROOT/name
            if path.exists(): raise RuntimeError('Occupied fixture unit')
            path.write_bytes(raw); path.chmod(0o644)
        cls.web.dropin.parent.mkdir(mode=0o755)
        cls.stack.callback(cls.remove_units)
        command(['/usr/bin/systemctl','daemon-reload'])
        command(['/usr/bin/systemctl','start',cls.web.http.unit('apache'),cls.web.boot.target])
        ctx=context(s.old.PublicOperation(cls.web,'stage','web.public.identity'))
        with patch.object(cls.web,'absent'),patch.object(cls.web,'network_ready'),patch.object(s.boot,'SOURCE',FROZEN): cls.web.stage(ctx)
        # Fixture parent receipts are not a claim of a Phase-5 installation.
        for phase in s.old.PHASES[1:]:
            binding=cls.web.binding(context(s.old.PublicOperation(cls.web,phase,'web.public.stage')))
            cls.web._write(phase+'.attempt',binding); cls.web._write(phase+'.json',binding)
        cls.ca=cls.layout/'fixture-ca'; cls.ca.mkdir(mode=0o700)
        command(['/usr/bin/openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-days','30',
            '-subj','/CN=HESTIA disposable shared public CA','-addext','basicConstraints=critical,CA:TRUE',
            '-keyout',str(cls.ca/'key.pem'),'-out',str(cls.ca/'cert.pem')])
        cls.trust=Path('/usr/local/share/ca-certificates')/('hestia-shared-'+instance+'.crt')
        shutil.copyfile(cls.ca/'cert.pem',cls.trust); command(['/usr/sbin/update-ca-certificates']); cls.stack.callback(cls.remove_trust)
        cls.issue(cls.web.acme_root,'hestia-web',WEB,cls.web.public,1)
        cls.web._write('renewal.json',cls.web.renewal_configuration())
        (cls.web.root/'apache-public.conf').write_bytes(cls.web.apache_include())
        cls.web.dropin.write_bytes(cls.web.apache_dropin()); cls.web.dropin.chmod(0o644)
        for path,target in cls.web.links().items():
            path.parent.mkdir(exist_ok=True); path.symlink_to(target)
        command(['/usr/bin/systemctl','daemon-reload'])
        for role in ('http','https'): cls.web.systemctl('start',role)
        # Enroll expects an active original timer. Avoid its immediate renewal
        # by first recording a recent persistent stamp in this disposable host.
        stamp=Path('/var/lib/systemd/timers')/('stamp-'+cls.web.unit('timer'))
        stamp.parent.mkdir(exist_ok=True); stamp.touch(); cls.stack.callback(stamp.unlink,missing_ok=True)
        cls.web.systemctl('start','timer')
        cls.frozen_manifest={name:s.f._sha(raw) for name,raw in cls.web.files().items()}
        cls.frozen_profile=(cls.web.root/'profile.json').read_bytes()
        cls.frozen_code={p.relative_to(cls.web.root).as_posix():s.f._sha(p.read_bytes()) for p in (cls.web.root/'code').rglob('*') if p.is_file()}
        cls.backends={}
        for label,port in (('web',9080),('mobile',9083)):
            server=ThreadingHTTPServer(('127.0.0.1',port),Backend); server.label=label; server.received=[]
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            cls.backends[label]=server; cls.stack.callback(server.server_close); cls.stack.callback(server.shutdown)
        cls.effects=[]; real_command=s.old.command
        def acquire(argv,**options):
            if argv[0]=='/usr/bin/certbot':
                cls.effects.append(argv)
                if 'certonly' in argv: cls.issue(cls.r.shared.acme_root,'hestia-mobile',MOBILE,cls.r.shared.public,1)
                elif '--dry-run' not in argv: return real_command(argv,**options)
                return b''
            return real_command(argv,**options)
        cls.engine,cls.r=s.engine(StateJournal(cls.layout/'controller/state.json'),cls.r.value)
        cls.web=cls.r.web
        cls.document=cls.engine.plan(mode='upgrade')
        def diagnostic(callback):
            def checked(operation,*args):
                try: return callback(operation,*args)
                except Exception:
                    with Path('/evidence/shared-public-operation-error.txt').open('a') as output: traceback.print_exc(file=output)
                    raise
            return checked
        with patch.object(cls.r.boot,'configuration'),patch.object(cls.r.boot,'live'),patch.object(cls.r,'gateway'),patch.object(cls.r,'network_ready'),patch.object(s.old,'command',side_effect=acquire),patch.object(s.gateway_service_probe,'check'),patch.object(s.SharedOperation,'apply',diagnostic(s.SharedOperation.apply)):
            result=cls.engine.apply(cls.document['plan_sha256'])
        if result['state']!='DONE':
            Path('/evidence/shared-public-failed-journal.json').write_bytes(canonical_bytes(result))
            raise AssertionError('Successor fixture transaction failed: '+str([(x['name'],x['state'],x['last_error_redacted']) for x in result['steps']]))
        cls.document=result
        cls.before_invocations={role:cls.web.systemctl('show',role)['MainPID'] for role in ('http','https')}
        Path('/evidence/shared-public-parent-manifest.json').write_bytes(canonical_bytes({'commit':PARENT,'files':cls.frozen_code}))

    @classmethod
    def issue(cls,acme,name,host,public,version):
        archive=acme/'archive'/name; live=acme/'live'/name; renewal=acme/'renewal'
        for path in (archive,live,renewal): path.mkdir(parents=True,exist_ok=True)
        key=archive/f'privkey{version}.pem'; leaf=archive/f'cert{version}.pem'; csr=cls.ca/'request.csr'
        ext=cls.ca/'extensions'; ext.write_text('subjectAltName=DNS:'+host+'\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n')
        command(['/usr/bin/openssl','req','-new','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-subj','/CN='+host,'-keyout',str(key),'-out',str(csr)])
        command(['/usr/bin/openssl','x509','-req','-in',str(csr),'-CA',str(cls.ca/'cert.pem'),'-CAkey',str(cls.ca/'key.pem'),'-CAcreateserial','-days','30','-extfile',str(ext),'-out',str(leaf)])
        (archive/f'chain{version}.pem').write_bytes((cls.ca/'cert.pem').read_bytes())
        (archive/f'fullchain{version}.pem').write_bytes(leaf.read_bytes()+(cls.ca/'cert.pem').read_bytes())
        for kind in ('cert','chain','fullchain','privkey'):
            (archive/f'{kind}{version}.pem').chmod(0o600 if kind=='privkey' else 0o644)
            link=live/(kind+'.pem'); link.unlink(missing_ok=True); link.symlink_to(f'../../archive/{name}/{kind}{version}.pem')
        raw='renew_before_expiry = 1 days\narchive_dir = '+str(archive)+'\n'
        raw+=''.join(kind+' = '+str(live/(kind+'.pem'))+'\n' for kind in ('cert','privkey','chain','fullchain'))
        raw+='\n[renewalparams]\nauthenticator = webroot\nserver = '+s.old.PRODUCTION+'\nkey_type = ecdsa\n[[webroot_map]]\n'+host+' = '+str(public/'htdocs')+'\n'
        config=renewal/(name+'.conf')
        if config.exists():
            if config.read_text()!=raw: raise RuntimeError('Renewal fixture changed')
        else: config.write_text(raw); config.chmod(0o644)

    @classmethod
    def remove_trust(cls): cls.trust.unlink(); command(['/usr/sbin/update-ca-certificates'])

    @classmethod
    def remove_units(cls):
        names=[*cls.web.units(),*cls.dependencies]
        command(['/usr/bin/systemctl','stop',*names],check=False)
        for path in cls.web.links(): path.unlink(missing_ok=True)
        for name in names: (s.h.drain.UNIT_ROOT/name).unlink(missing_ok=True)
        shutil.rmtree(cls.web.dropin.parent)
        command(['/usr/bin/systemctl','daemon-reload'])
        command(['/usr/bin/systemctl','reset-failed'],check=False)

    @classmethod
    def restore_defaults(cls):
        for unit,active in cls.default.items():
            if active: command(['/usr/bin/systemctl','start',unit],check=False)

    def request(self,host,path='/',*,tls=True):
        if not tls:
            connection=http.client.HTTPConnection('127.0.0.1',80,timeout=5)
        else:
            connection=http.client.HTTPSConnection(host,443,context=ssl.create_default_context(),timeout=5)
            raw=socket.create_connection(('127.0.0.1',443),timeout=5,source_address=('127.0.0.10' if host==MOBILE else '127.0.0.11',0))
            connection.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=host)
        try:
            connection.request('GET',path,headers={'Host':host,'Connection':'close'})
            response=connection.getresponse(); return response.status,response.read()
        finally: connection.close()

    def worker(self,role,*,parent=False):
        return command(['/usr/bin/python3.13','-I','-B',str((self.web.root if parent else self.r.root)/'worker.py'),role],check=False)

    def test_01_original_frozen_worker_refuses_successor_while_new_listeners_serve(self):
        self.assertNotEqual(self.worker('http',parent=True).returncode,0)
        self.assertEqual(self.request(WEB,'/login.php')[0],200)
        self.assertEqual(self.request(MOBILE,'/health')[0],200)
        self.assertEqual(self.request(MOBILE,tls=False)[0],308)
        self.assertTrue(all(self.web.running(role) for role in ('http','https','timer')))

    def test_02_parent_sources_configurations_and_original_fragments_remain_exact(self):
        self.assertEqual((self.web.root/'profile.json').read_bytes(),self.frozen_profile)
        self.assertEqual({p.relative_to(self.web.root).as_posix():s.f._sha(p.read_bytes()) for p in (self.web.root/'code').rglob('*') if p.is_file()},self.frozen_code)
        self.assertEqual({name:s.f._sha((self.web.root/name).read_bytes()) for name in self.web.files()},self.frozen_manifest)
        self.r.enrolled(); self.r.configuration()
        self.assertEqual(self.global_files,{name:Path(name).read_bytes() for name in self.global_files})

    def test_03_real_certbot_both_lineages_and_hup_publish_two_rotated_certificates(self):
        pid=self.web.systemctl('show','https')['MainPID']
        self.issue(self.web.acme_root,'hestia-web',WEB,self.web.public,2)
        self.issue(self.r.shared.acme_root,'hestia-mobile',MOBILE,self.r.shared.public,2)
        # Real Certbot renew sees two not-due lineages, without any external CA.
        self.web.systemctl('start','renew')
        self.assertEqual(self.web.systemctl('show','https')['MainPID'],pid)
        for host,acme,name in ((WEB,self.web.acme_root,'hestia-web'),(MOBILE,self.r.shared.acme_root,'hestia-mobile')):
            expected=ssl.PEM_cert_to_DER_cert((acme/'live'/name/'cert.pem').read_text())
            def current():
                with socket.create_connection(('127.0.0.1',443),timeout=2) as raw,ssl.create_default_context().wrap_socket(raw,server_hostname=host) as stream:
                    return stream.getpeercert(binary_form=True)==expected
            until(current)

    def test_04_foreign_dropin_is_refused_by_installed_worker_before_renewal(self):
        directory=s.h.drain.UNIT_ROOT/(self.web.unit('http')+'.d'); directory.mkdir()
        try: self.assertNotEqual(self.worker('renew').returncode,0)
        finally: directory.rmdir()
        self.r.configuration()

    def test_05_lost_handoff_reply_reconciles_readonly_without_second_start(self):
        record=self.document['steps'][1]; spec=self.document['plan']['steps'][1]
        operation=self.engine.registry.get(spec); ctx=self.engine._context(self.document,spec,record)
        before={role:self.web.systemctl('show',role)['MainPID'] for role in ('http','https')}
        self.assertEqual(operation.recover(ctx,'apply').decision,RecoveryDecision.APPLIED)
        self.assertEqual(before,{role:self.web.systemctl('show',role)['MainPID'] for role in before})

    def test_06_partial_ownership_is_manual_without_native_replay(self):
        path=self.r.root/'ownership.json'; raw=path.read_bytes(); path.unlink()
        try:
            record=self.document['steps'][1]; spec=self.document['plan']['steps'][1]
            operation=self.engine.registry.get(spec)
            self.assertEqual(operation.recover(self.engine._context(self.document,spec,record),'apply').decision,RecoveryDecision.MANUAL)
            self.assertNotEqual(self.worker('renew').returncode,0)
        finally: path.write_bytes(raw); path.chmod(0o600)
        self.r.configuration()

    def test_07_current_overlay_admits_exact_successor_and_rejects_drift(self):
        scope=SimpleScope(self.r)
        proof=old_profile.overlay_evidence(scope,self.web.value['backend_fragment_sha256'])
        self.assertEqual(proof['successor_profile_sha256'],self.r.digest)
        raw=self.r.dropin.read_bytes(); self.r.dropin.write_bytes(raw+b'# foreign\n')
        try:
            with self.assertRaises(Exception): old_profile.overlay_evidence(scope,self.web.value['backend_fragment_sha256'])
        finally: self.r.dropin.write_bytes(raw)

    def test_08_successor_public_units_restart_with_frozen_guards_and_existing_links(self):
        before=self.web.systemctl('show','https')['MainPID']
        for role in ('https','http'): self.r.control('stop',role); self.r.stopped(role)
        for role in ('http','https'): self.r.control('start',role); self.assertTrue(self.web.running(role))
        self.assertNotEqual(before,self.web.systemctl('show','https')['MainPID'])
        self.assertEqual(self.request(WEB,'/login.php')[0],200); self.assertEqual(self.request(MOBILE,'/health')[0],200)
        self.web.enabled()

    def test_09_backend_guard_still_refuses_an_unprovisioned_application(self):
        self.assertNotEqual(self.worker('backend').returncode,0)

    def test_10_changed_frozen_parent_bundle_blocks_new_worker(self):
        path=self.web.root/'code/installer/public_tls_profile.py'; raw=path.read_bytes(); path.write_bytes(raw+b'\n')
        try: self.assertNotEqual(self.worker('renew').returncode,0)
        finally: path.write_bytes(raw)
        self.r.configuration()


class SimpleScope:
    def __init__(self,r): self.instance=r.layout.instance; self.directory=r.http.spec.maintenance_directory


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--report',type=Path,required=True); args=parser.parse_args()
    before=quality.snapshot(ROOT)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SharedSystemd))
    stable=before==quality.snapshot(ROOT); passed=result.wasSuccessful() and result.testsRun==10 and not result.skipped and stable
    report={'suite':'shared-public-systemd','tests':result.testsRun,'expected':10,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'status':'PASS' if passed else 'FAIL','source_stable':stable,'source_files':len(before),'frozen_parent':PARENT,
        'real_systemd_nginx_certbot':True,'fixture_dependencies':['Apache','boot','Gateway','Foundation'],
        'acme_issuance':'intercepted local CA fixture','acme_dry_run':'intercepted fixture','renewal':'real Certbot with not-due lineages',
        'full_application_boot_qualified':False,'public_acme_qualified':False}
    args.report.write_bytes(quality.encode(report))
    args.report.with_name('SOURCE-MANIFEST-shared-public-systemd.json').write_bytes(quality.encode(before))
    print(json.dumps(report)); sys.exit(0 if passed else 1)
