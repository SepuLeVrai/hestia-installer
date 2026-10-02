"""Real TLS/NGINX boundary against a recording backend, disposable CI only.

No SQL, actual Gateway or Web app is qualified by this independent suite.
"""
import argparse
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from installer.mobile_ingress import MobileIngress, ROUTES
from scripts import quality

HOST='mobile.hestia.test'


class Backend(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args): pass
    def serve(self):
        body=self.rfile.read(int(self.headers.get('Content-Length','0')))
        value={'method':self.command,'path':self.path,'headers':dict(self.headers),
               'peer':self.client_address[0],'length':len(body),'body_sha256':hashlib.sha256(body).hexdigest()}
        self.server.received.append(value)
        if self.server.drop:
            self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
        raw=json.dumps(value).encode();self.send_response(200)
        self.send_header('Content-Length',str(len(raw)));self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','public, max-age=3600');self.send_header('Set-Cookie','forbidden=test')
        self.send_header('X-Accel-Redirect','/should-not-be-followed');self.send_header('Referrer-Policy','unsafe-url')
        self.send_header('X-Content-Type-Options','wrong')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'");self.end_headers()
        if self.command != 'HEAD':self.wfile.write(raw)
    do_GET=do_HEAD=do_POST=do_PUT=do_DELETE=do_OPTIONS=do_TRACE=serve


class MobileIngressLive(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.environ.get('HESTIA_MOBILE_INGRESS_TEST'),'1')
        self.assertTrue(Path('/.dockerenv').exists());self.assertEqual(os.geteuid(),0)
        self.assertEqual(Path('/proc/1/comm').read_text().strip(),'systemd')
        self.root=Path(tempfile.mkdtemp(prefix='hestia-mobile-ingress-',dir='/var/lib'));self.nginx=None;self.backend=None
        self.addCleanup(self.cleanup)
        self.global_before={str(p):p.read_bytes() for p in (Path('/etc/nginx/nginx.conf'),Path('/etc/apache2/apache2.conf'))}
        self.cert=self.root/'certificate.pem';self.key=self.root/'key.pem'
        subprocess.run(['/usr/bin/openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256',
            '-nodes','-days','1','-subj','/CN='+HOST,'-addext','subjectAltName=DNS:'+HOST,
            '-keyout',str(self.key),'-out',str(self.cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
        self.context=ssl.create_default_context(cafile=str(self.cert))
        self.backend=ThreadingHTTPServer(('127.0.0.1',9083),Backend);self.backend.received=[];self.backend.drop=False
        self.thread=threading.Thread(target=self.backend.serve_forever,daemon=True);self.thread.start()
        self.start(('127.0.0.10/32',))

    def start(self,networks):
        if self.nginx is not None:self.nginx.terminate();self.nginx.wait(timeout=5)
        server=MobileIngress(HOST,networks).nginx_server(certificate=self.cert,private_key=self.key)
        text=f'worker_processes 1;\npid {self.root}/nginx.pid;\nerror_log /dev/null crit;\nevents {{ worker_connections 64; }}\nhttp {{\n'+server+'}\n'
        self.config=self.root/'nginx.conf';self.config.write_text(text)
        result=subprocess.run(['/usr/sbin/nginx','-t','-p',str(self.root)+'/', '-c',str(self.config)],capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr.decode())
        self.nginx=subprocess.Popen(['/usr/sbin/nginx','-p',str(self.root)+'/', '-c',str(self.config),'-g','daemon off;'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            self.assertIsNone(self.nginx.poll())
            try:
                with socket.create_connection(('127.0.0.1',443),timeout=.1):return
            except OSError:time.sleep(.02)
        self.fail('NGINX did not listen')

    def cleanup(self):
        if self.nginx is not None:
            self.nginx.terminate()
            try:self.nginx.wait(timeout=5)
            except subprocess.TimeoutExpired:self.nginx.kill();self.nginx.wait(timeout=2)
        if self.backend is not None:self.backend.shutdown();self.backend.server_close();self.thread.join(timeout=2)
        if hasattr(self,'global_before'):
            self.assertEqual(self.global_before,{n:Path(n).read_bytes() for n in self.global_before})
        shutil.rmtree(self.root)

    def request(self,path,method='POST',body=b'{}',headers=None,source='127.0.0.10',sni=HOST,raw=None):
        if raw is None:
            rows=[('Host',HOST),('Connection','close')]
            if body is not None:rows += [('Content-Length',str(len(body))),('Content-Type','application/json')]
            if path.startswith('/mobile/bootstrap/'):rows.append(('Origin','https://'+HOST))
            if headers:
                names={k.lower() for k,v in headers};rows=[(k,v) for k,v in rows if k.lower() not in names]+headers
            raw=(method+' '+path+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in rows)+'\r\n').encode()+(body or b'')
        with socket.create_connection(('127.0.0.1',443),timeout=5,source_address=(source,0)) as sock:
            # Wrong SNI is intentionally tested with the same trusted leaf.
            context=self.context
            if sni!=HOST:context=ssl._create_unverified_context()
            with context.wrap_socket(sock,server_hostname=sni) as stream:
                stream.sendall(raw);response=http.client.HTTPResponse(stream,method=method);response.begin()
                return response.status,response.getheaders(),response.read(2097152)

    def refused(self,*args,**kwargs):
        before=len(self.backend.received);status,headers,body=self.request(*args,**kwargs)
        self.assertIn(status,(400,403,404,405,413,421));self.assertEqual(len(self.backend.received),before)
        return status

    def test_all_63_routes_keep_method_path_body_and_fixed_backend(self):
        for route in ROUTES:
            for method in route.methods:
                with self.subTest(path=route.path,method=method):
                    body=b'{"opaque":"unchanged"}' if route.body_limit else None
                    status,_,_=self.request(route.path,method,body)
                    self.assertEqual(status,200);value=self.backend.received[-1]
                    self.assertEqual((value['path'],value['method'],value['peer']),(route.path,method,'127.0.0.3'))
                    self.assertEqual(value['body_sha256'],hashlib.sha256(body or b'').hexdigest())
                    self.assertEqual(value['headers']['X-Hestia-Client-IP'],'127.0.0.10')

    def test_unknown_paths_queries_encodings_normalization_and_absolute_form_never_reach_backend(self):
        for path in ('/','/login.php','/internal/mobile/auth','/v1/business/unknown','/v1/auth/session/',
                     '/V1/auth/session','/v1/auth/session?','/v1/auth/session?token=canary',
                     '/v1/auth/%73ession','//v1/auth/session','/v1/../v1/auth/session',
                     '/v1/auth%2fsession','/v1/auth/session;param','https://'+HOST+'/v1/auth/session'):
            with self.subTest(path=path):self.refused(path)

    def test_wrong_methods_are_closed_even_head_health_and_options(self):
        for route in ROUTES:
            method='GET' if route.methods==('POST',) else 'POST'
            self.assertEqual(self.refused(route.path,method),405)
        for method in ('HEAD','OPTIONS','PUT','TRACE','DELETE'):self.refused('/health',method,None)

    def test_untrusted_headers_are_discarded_and_protocol_headers_preserved(self):
        headers=[('Forwarded','for=198.51.100.9'),('X-Forwarded-For','198.51.100.9'),
            ('X-Forwarded-Unknown','malicious'),('X-Hestia-Client-IP','198.51.100.9'),('X-Real-IP','198.51.100.9'),
            ('X-Arbitrary','not-forwarded'),('DPoP','synthetic-proof'),('Origin','https://'+HOST)]
        status,_,raw=self.request('/v1/auth/session',headers=headers);self.assertEqual(status,200)
        seen=json.loads(raw)['headers'];self.assertEqual(seen['DPoP'],'synthetic-proof');self.assertEqual(seen['Host'],HOST)
        self.assertEqual(seen['X-Hestia-Client-IP'],'127.0.0.10');self.assertEqual(seen['Origin'],'https://'+HOST)
        self.assertFalse(any(k.lower().startswith('x-forwarded-') or k.lower() in ('forwarded','x-real-ip','x-arbitrary') for k in seen))

    def test_cookies_authorization_encoding_upgrade_and_chunking_are_refused(self):
        for key,value in [('Cookie','session=canary'),('Authorization','Bearer canary'),('Content-Encoding','gzip'),('Upgrade','websocket')]:
            self.refused('/v1/auth/session',headers=[(key,value)])
        self.refused('/v1/auth/session',raw=(f'POST /v1/auth/session HTTP/1.1\r\nHost: {HOST}\r\n'
            'Connection: close\r\nTransfer-Encoding: chunked\r\nContent-Type: application/json\r\n\r\n2\r\n{}\r\n0\r\n\r\n').encode())

    def test_host_sni_and_independent_mobile_allowlist_are_enforced(self):
        for host in ('web.hestia.test',HOST+':443','evil.test'):
            self.assertEqual(self.refused('/v1/auth/session',headers=[('Host',host)]),421)
        self.assertEqual(self.refused('/v1/auth/session',sni='web.hestia.test'),421)
        self.assertEqual(self.refused('/v1/auth/session',source='127.0.0.11',headers=[('X-Real-IP','127.0.0.10')]),403)
        self.start(('0.0.0.0/0',));self.assertEqual(self.request('/v1/auth/session',source='127.0.0.11')[0],200)

    def test_request_body_limits_are_checked_before_forwarding(self):
        for path,limit in (('/mobile/bootstrap/check',1024),('/v1/auth/session',16384),
                           ('/v1/business/referentials-list',16384),('/v1/business/photo-upload',1048576)):
            body=b'a'*limit;self.assertEqual(self.request(path,body=body)[0],200)
            self.assertEqual(self.backend.received[-1]['length'],limit)
            # Declared size is rejected without receiving or relaying the body.
            self.assertEqual(self.refused(path,body=b'',headers=[('Content-Length',str(limit+1))]),413)

    def test_json_contract_and_bootstrap_origin_remain_explicit(self):
        for typ in ('text/plain','application/json; charset=latin1','application/json; boundary=x'):
            self.refused('/v1/auth/session',headers=[('Content-Type',typ)])
        for typ in ('application/json','Application/JSON; charset=utf-8','application/json; charset="UTF-8"'):
            self.assertEqual(self.request('/v1/auth/session',headers=[('Content-Type',typ)])[0],200)
        self.refused('/v1/auth/session',body=b'')
        self.refused('/mobile/bootstrap/check',headers=[('Origin','https://evil.test')])
        self.refused('/mobile/bootstrap/check',headers=[('Origin','')])
        self.refused('/mobile/bootstrap','GET',b'{}')

    def test_response_privacy_and_original_bootstrap_headers_are_kept(self):
        status,headers,_=self.request('/v1/auth/session');self.assertEqual(status,200)
        lower=[(k.lower(),v) for k,v in headers]
        self.assertEqual([v for k,v in lower if k=='cache-control'],['no-store'])
        self.assertEqual([v for k,v in lower if k=='referrer-policy'],['no-referrer'])
        self.assertEqual([v for k,v in lower if k=='x-content-type-options'],['nosniff'])
        self.assertFalse(any(k in ('set-cookie','x-accel-redirect') for k,v in lower))
        self.assertEqual([v for k,v in lower if k=='content-security-policy'],["default-src 'none'; script-src 'self'"])
        self.assertEqual(len(self.backend.received),1)

    def test_lost_backend_reply_never_retries_a_post(self):
        self.backend.drop=True;status,_,_=self.request('/v1/auth/complete')
        self.assertEqual(status,502);self.assertEqual(len(self.backend.received),1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    source=quality.snapshot(ROOT);suite=unittest.defaultTestLoader.loadTestsFromTestCase(MobileIngressLive)
    result=unittest.TextTestRunner(verbosity=2).run(suite);stable=source==quality.snapshot(ROOT)
    report={'suite':'Mobile ingress real NGINX/TLS','tests':result.testsRun,'expected':10,
        'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'status':'PASS' if result.wasSuccessful() and result.testsRun==10 and not result.skipped and stable else 'FAIL',
        'source_stable':stable,'source_files':len(source),'routes':63,'backend':'recording fixture',
        'gateway_application_qualified':False,'public_acme_delivered':False,'boot_delivered':False,'phase6_complete':False}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    (args.report.parent/'MOBILE-INGRESS-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));sys.exit(0 if report['status']=='PASS' else 1)
