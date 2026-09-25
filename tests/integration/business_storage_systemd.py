#!/usr/bin/env python3
"""Actual managed SQL + immutable Web + external writes and restored reads.

Disposable network-isolated systemd fixture only. Activation/drain here remain
fixture responsibilities; no exhaustive host producer inventory is inferred.
"""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import time
import unittest
import urllib.error
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import quality
import deployed_web_systemd as previous
from installer import backup_files as files, coordinated_backup as backup
from installer import finalization as f, http_runtime as h, php_transport as p, system_drain as drain
from installer.web_releases import STORAGE_COMMIT, get_release
from http_runtime_systemd import command, until


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', 8, 8, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress((b'\x00' + b'\x20\x70\xb0' * 8) * 8)) + chunk(b'IEND', b''))


class BusinessStorageLive(previous.DeployedWebLive):
    release_commit = STORAGE_COMMIT
    external_uploads = True

    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_BUSINESS_STORAGE_TEST') != '1':
            raise RuntimeError('Explicit business storage opt-in required')
        super().setUpClass()

    def finish(self):
        self.managed()
        result = self.prepare(authority=self.authority)
        self.assertEqual(result['state'], 'DATABASE_CONFIGURATION_READY', result)
        result = self.finalize(); self.assertEqual(result['state'], 'WEB_FRESH_FINALIZED', result)
        return result

    def tearDown(self):
        try:
            self.stop_services()
            name = 'hdf_' + hashlib.sha256(self.db.lower().encode()).hexdigest()[:24]
            self.sql([f"DROP USER IF EXISTS `{name}`@'localhost'"])
        finally: super().tearDown()

    def ready(self):
        self.start(); self.login()
        self.uploads = self.http_root / 'data/uploads'
        self.code_before = h._code_digest(self.webroot, self.web.pw_gid)
        self.assertEqual(self.scope.directory, self.directory / 'maintenance')
        self.assertFalse(self.permission(self.web, '-w', self.webroot / 'uploads'))
        self.assertTrue(self.permission(self.web, '-w', self.uploads))

    def csrf(self, path='/index.php?page=mon_profil'):
        status, body, _, _ = self.request(path); self.assertEqual(status, 200)
        value = re.search(r'name="csrf_token" value="([a-f0-9]+)"', body)
        self.assertIsNotNone(value); return value.group(1)

    def binary(self, path, wire=None, content_type=None):
        headers = {'Host': self.spec.hostname}
        if content_type: headers['Content-Type'] = content_type
        request = urllib.request.Request(self.url + path, data=wire, headers=headers)
        try: response = self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as error: response = error
        with response: return response.status, response.read(), dict(response.headers)

    def upload(self, path, fields, name, filename, content, mime):
        boundary = 'HESTIA' + os.urandom(16).hex()
        body = bytearray()
        for key, value in fields.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
        body += content + f'\r\n--{boundary}--\r\n'.encode()
        return self.binary(path, bytes(body), 'multipart/form-data; boundary=' + boundary)

    def photo(self):
        result = self.upload('/ajax/profile_photo_upload.php', {'csrf_token': self.csrf()}, 'photo', 'portrait.png', png(), 'image/png')
        self.assertEqual(result[0], 200)
        value = self.sql(query=f'SELECT photo_profil FROM `{self.db}`.UserInfo WHERE id_user=1')[0]['photo_profil']
        self.assertRegex(value or '', r'^uploads/profiles/user_1_[a-f0-9]+[.]webp$')
        path = self.uploads / value.removeprefix('uploads/')
        self.assertTrue(path.is_file()); self.assertFalse((self.webroot / value).exists())
        self.assertEqual((path.stat().st_uid, path.stat().st_gid), (self.web.pw_uid, self.web.pw_gid))
        result = self.binary('/' + value); self.assertEqual(result[0], 200)
        self.assertEqual(result[1], path.read_bytes()); self.assertEqual(result[2]['Content-Type'], 'image/webp')
        self.assertEqual(result[1][:4], b'RIFF'); self.assertEqual(result[1][8:12], b'WEBP')
        return value, path

    def document(self):
        data = 'Contrat privé, fixture sans donnée réelle. 漢字🙂\n'.encode()
        route = '/index.php?page=ged_admin_documents'
        response = self.upload(route, {'csrf_token': self.csrf(route), 'action': 'upload',
            'title': 'Document de recette', 'download_policy': 'OPEN'}, 'document_file', 'contrat.txt', data, 'text/plain')
        self.assertEqual(response[0], 200)
        rows = self.sql(query=f'SELECT id_document,relative_path,checksum_sha256 FROM `{self.db}`.Ged_Document')
        self.assertEqual(len(rows), 1); row = rows[0]
        path = self.uploads / 'ged_documents' / row['relative_path']
        self.assertEqual(path.read_bytes(), data); self.assertEqual(row['checksum_sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.binary('/index.php?page=ged_download&id=' + str(row['id_document']))[:2], (200, data))
        self.assertEqual(self.binary('/uploads/ged_documents/' + row['relative_path'])[0], 403)
        return row, path, data

    def import_file(self):
        data = 'Contrat;Libellé\nHESTIA-RECETTE;Import externe 漢字\n'.encode()
        response = self.upload('/ajax/import_upload.php', {'csrf_token': self.csrf(), 'mode': 'DAR'},
            'files[]', 'recette.csv', data, 'text/csv')
        self.assertEqual(response[0], 200, response[1].decode(errors='replace'))
        result = json.loads(response[1]); self.assertTrue(result['ok'])
        rows = self.sql(query=f"SELECT chemin_relatif,hash_sha256 FROM `{self.db}`.P_Import_File WHERE id_import_batch={int(result['batch_id'])}")
        self.assertEqual(len(rows), 1); path = Path(rows[0]['chemin_relatif'])
        self.assertTrue(path.is_relative_to(self.http_root / 'data/imports'))
        self.assertEqual(path.read_bytes(), data); self.assertEqual(rows[0]['hash_sha256'], hashlib.sha256(data).hexdigest())
        self.assertFalse(self.permission(self.web, '-w', self.webroot / 'var/imports'))
        self.assertEqual(list((self.http_root / 'data/upload-tmp').iterdir()), [])
        return path, data

    def immutable(self):
        self.assertEqual(h._code_digest(self.webroot, self.web.pw_gid), self.code_before)
        self.assertEqual(self.observe()['state'], 'WEB_FRESH_FINALIZED')

    def restart_fixture_services(self):
        # Type=simple start completion precedes socket/application readiness.
        # Use the same bounded real HTTP readiness condition as initial start;
        # never retry the subsequent data/session assertions.
        for role in ('php', 'apache'): command('systemctl', 'start', self.http_runtime.unit(role))
        def ready():
            try: return self.request('/login.php')[0] == 200
            except (OSError, urllib.error.URLError): return False
        until(ready, timeout=12)
        for role in ('php', 'apache'):
            value = drain._show(self.http_runtime.unit(role))
            self.assertEqual(value['ActiveState'], 'active'); self.assertNotEqual(value['MainPID'], '0')
        result = self.request('/index.php')
        self.assertEqual(result[0], 200); self.assertNotIn('/login.php', result[3])

    def test_business_photo_replacement_uses_external_root_and_deletes_old_image(self):
        self.ready(); old, path = self.photo(); new, _ = self.photo()
        self.assertNotEqual(old, new); self.assertFalse(path.exists()); self.immutable()

    def test_business_ged_real_upload_authenticated_download_and_public_denial(self):
        self.ready(); self.document(); self.immutable()

    def test_business_import_real_multipart_staging_keeps_database_reference(self):
        self.ready(); self.import_file(); self.immutable()

    def test_business_public_alias_cannot_execute_or_publish_private_controls(self):
        self.ready(); _, image = self.photo()
        for name in ('probe.php', 'probe.phtml', 'probe.php.png', '.hidden.png', 'private.txt', '.htaccess'):
            path = image.parent / name; path.write_bytes(b'<?php echo "MUST_NOT_EXECUTE"; ?>')
            os.chown(path, self.web.pw_uid, self.web.pw_gid); path.chmod(0o600)
            status, body, headers = self.binary('/uploads/profiles/' + name)
            if name == 'probe.php.png':
                self.assertEqual((status, body), (200, path.read_bytes()))
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            else: self.assertIn(status, (403, 404))
        for path in ('/uploads/', '/uploads/profiles/', '/uploads/ged_documents/', '/uploads/reference_templates/', '/uploads/mobile/'):
            self.assertIn(self.binary(path)[0], (403, 404))
        self.immutable()

    def test_business_common_gate_blocks_php_and_native_collector(self):
        self.ready(); current = self.session(); before = current.read_bytes(), current.stat().st_mtime_ns
        expired = current.parent / 'sess_expired'; expired.write_bytes(b'fixture|i:1;'); expired.chmod(0o600)
        os.chown(expired, self.web.pw_uid, self.web.pw_gid)
        stamp = time.time() - 50000; os.utime(expired, (stamp, stamp))
        with self.scope.acquire(confirmed=True) as lease:
            self.assertEqual(self.request('/index.php')[0], 503)
            self.assertEqual((current.read_bytes(), current.stat().st_mtime_ns), before)
            command('systemctl', 'start', self.collector.unit)
            self.assertTrue(expired.exists())
            lease.resume(confirmed=True)
        command('systemctl', 'start', self.collector.unit); self.assertFalse(expired.exists())
        self.assertNotIn('/login.php', self.request('/index.php')[3]); self.immutable()

    def test_business_same_slot_gate_prevents_service_restart_and_cgroups_drain(self):
        self.ready()
        with self.scope.acquire(confirmed=True) as lease:
            for role in ('apache', 'php'):
                unit = self.http_runtime.unit(role)
                command('systemctl', 'stop', unit)
                self.assertTrue(drain._empty_cgroup(unit))
                command('systemctl', 'start', unit)
                self.assertEqual(drain._show(unit)['ActiveState'], 'inactive')
            lease.resume(confirmed=True)
        self.restart_fixture_services(); self.immutable()

    def test_business_backup_restores_real_uploaded_files_and_session_under_common_gate(self):
        self.ready(); relative, photo = self.photo(); doc, document, doc_data = self.document()
        imported, import_data = self.import_file(); self.immutable()
        current = self.session(); session_bytes = current.read_bytes(); photo_bytes = photo.read_bytes()
        backups = self.root / 'backups'; backups.mkdir(mode=0o700)
        labels = (*h.DATA, 'uploads')
        inventory = files.DataInventory(tuple((name.replace('-', '_'), self.http_root / 'data' / name) for name in labels),
                                        self.web.pw_uid, self.web.pw_gid)
        coordinator = backup.CoordinatedBackup(replace(self.runtime, timeout_seconds=120), previous.WEB,
            repository=p.WEB_REPOSITORY, commit=self.release_commit)
        with self.scope.acquire(confirmed=True) as lease:
            # Stop actual fixture producers before snapshot; the PHP guard alone
            # cannot cover multipart reception or descendant converter processes.
            for unit in (self.collector.timer, self.collector.unit, self.http_runtime.unit('apache'), self.http_runtime.unit('php')):
                command('systemctl', 'stop', unit)
                if unit.endswith('.service'): self.assertTrue(drain._empty_cgroup(unit))
            result = coordinator.create_and_verify(self.existing(), self.authority, config_root=self.output,
                backup_root=backups, inventory=inventory, maintenance=lease, confirmed=True,
                allow_global_read_lock=True).report()
            self.assertEqual(result['state'], 'COORDINATED_BACKUP_RESTORE_VERIFIED', result)
            self.assertEqual(result['registered_roots'], 6); self.assertEqual(result['trigger_smoke_verified'], 5)
            self.assertFalse(result['complete_web_backup'])
            slot = backups / result['backup_id']; manifest = json.loads((slot / 'coordinated.json').read_bytes())
            data = manifest['data_snapshot']
            snapshot = files.FileSnapshot(slot / 'data' / data['snapshot_id'], data['manifest_sha256'],
                                          self.scope.instance, lease.lease_id, self.web.pw_gid)
            restored = backups / 'restored'; snapshot.restore_new(restored, lease)
            for name in labels:
                original = self.http_root / 'data' / name
                os.rename(original, self.root / ('retained-' + name))
                os.rename(restored / name.replace('-', '_'), original)
            self.assertEqual(photo.read_bytes(), photo_bytes); self.assertEqual(document.read_bytes(), doc_data)
            self.assertEqual(imported.read_bytes(), import_data); self.assertEqual(current.read_bytes(), session_bytes)
            lease.resume(confirmed=True)
        self.restart_fixture_services()
        self.assertEqual(self.binary('/' + relative)[:2], (200, photo_bytes))
        self.assertEqual(self.binary('/index.php?page=ged_download&id=' + str(doc['id_document']))[:2], (200, doc_data))
        self.immutable()

    def test_business_managed_session_policy_and_valid_eight_hour_collection(self):
        self.test_deployed_collector_preserves_valid_eight_hour_session()

    def test_business_copied_valid_slot_cannot_redirect_the_maintenance_gate(self):
        self.ready()
        base = self.root / 'cloned-configuration'; base.mkdir(mode=0o755)
        copied = base / self.directory.name
        shutil.copytree(self.directory, copied)
        for path in (copied, *copied.rglob('*')):
            original = self.directory / path.relative_to(copied)
            info = original.stat(); os.chown(path, info.st_uid, info.st_gid)
        other = h.HttpRuntime(replace(self.spec, maintenance_directory=copied / 'maintenance'))
        with self.assertRaisesRegex(h.HttpRuntimeError, 'CONFIGURATION_BINDING_REQUIRED'):
            other._verify_sealed_slot(self.web)
        self.assertNotIn('/login.php', self.request('/index.php')[3]); self.immutable()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True); args = parser.parse_args(); previous.WEB = args.web
    source = quality.snapshot(ROOT)
    names = sorted(name for name in BusinessStorageLive.__dict__ if name.startswith('test_business_'))
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(BusinessStorageLive(name) for name in names))
    stable = source == quality.snapshot(ROOT); release = get_release(STORAGE_COMMIT)
    report = {'suite': 'External business storage with managed SQL and real Apache FPM TLS',
        'tests': result.testsRun, 'expected': 9, 'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 9 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'web_commit': release.commit, 'web_tree': release.tree,
        'database_profile': 'fresh_managed', 'service_activation_delivered': False,
        'storage_inventory_complete': False, 'complete_web_backup': False, 'application_installed': False, 'phase5_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'BUSINESS-STORAGE-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
