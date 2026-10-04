#!/usr/bin/env python3
"""Real successor admission and target service starts, independent directions."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import signal
import time
import traceback
from unittest.mock import patch

import gateway_active_profile_systemd as prior
from installer import gateway_transition_resume as resume, mobile_activation_admission as activation
from installer.github_sources import AcquireOperation
from installer.model import SourceSpec
from installer.web_releases import WEB_REPOSITORY


class SuccessorLive(prior.ActiveProfileLive):
    def exercise_resume(self, source_pin, target_pin, direction):
        fixture = self.exercise_publication(source_pin, target_pin, direction)
        http, original, scope, backups, lease_id = (fixture[k] for k in ('http', 'runtime', 'scope', 'backups', 'lease_id'))
        kwargs = fixture['packages']
        prepared = resume.prepare(original, backups, lease_id, action='apply', **kwargs)
        self.assertEqual(prepared, resume.prepare(original, backups, lease_id, action='check', **kwargs))
        draft = self.service.application.read(); payload = deepcopy(draft['configuration'])
        payload.update(mode='upgrade', administrator=None, assistant={'action': 'preserve'})
        payload['database']['mode'] = 'existing_local'
        payload['secrets'] = {'database_password': self.payload['secrets']['database_password'],
                              'admin_password': '', 'openai_api_key': ''}
        source = AcquireOperation(self.service.engine.journal.path.parent, 'web',
            SourceSpec(WEB_REPOSITORY, self.profile.source_commit, self.profile.source_commit), None).path / 'tree'
        worker = replace(self.profile.runtime(), timeout_seconds=120)
        root = original.root / 'control' / ('resume-' + lease_id)
        starts = Path('/evidence/gateway-successor-starts.json'); timing = Path('/evidence/gateway-successor-windows.json')
        real_acquire = activation.a.c.rf.acquire; real_start = activation.v.NativeRuntime.start
        released = False; boundary = 'guard-unlink'
        def append(path, value):
            rows = json.loads(path.read_text()) if path.exists() else []; rows.append(value)
            path.write_text(json.dumps(rows, indent=2) + '\n')
        @contextmanager
        def fenced(*args, **options):
            nonlocal released
            released = False
            with real_acquire(*args, **options) as held:
                began = held._deadline - 180
                yield held
                held.assert_held()
                append(timing, {'boundary': boundary, 'elapsed': time.monotonic() - began})
            released = True
        def start(native, role):
            self.assertTrue(released)
            self.assertFalse((scope.directory / 'maintenance.attempt').exists())
            self.assertTrue(all(not (scope.directory / name).exists() for name in resume.h.MARKERS))
            with self.assertRaises(Exception):
                with scope.writer(): self.fail('Activity writer entered ordered activation')
            real_start(native, role); append(starts, {'role': role, 'unit': native.unit(role)})
            if boundary == 'php-start' and role == 'php': os.kill(os.getpid(), signal.SIGKILL)
        def invoke(action):
            try:
                with patch.object(activation.a.c.rf, 'acquire', fenced), patch.object(activation.v.NativeRuntime, 'start', start):
                    return resume.execute(http, scope, lease_id, backups, worker, source, payload, self.authority,
                        confirmation=prepared['plan_sha256'], action=action, confirmed=True, allow_global_read_lock=True)
            except BaseException as error:
                chain = []; current = error
                while current is not None and len(chain) < 12:
                    code = str(current)
                    chain.append({'type': type(current).__name__,
                        'code': code if resume.re.fullmatch('[A-Z][A-Z0-9_]{1,100}', code) else 'REDACTED',
                        'frames': [{'file': Path(row.filename).name, 'line': row.lineno, 'function': row.name}
                            for row in traceback.extract_tb(current.__traceback__)]})
                    current = current.__context__
                Path('/evidence/successor-failure.json').write_text(json.dumps({'boundary': boundary, 'chain': chain}, indent=2) + '\n')
                raise
        # Complete all native preparations before the bounded final-admission
        # crash child. The production coordinator checkpoints the same stages.
        selected = resume.gd.attached(http, original.foundation)
        authority = resume.h.Authority.load(selected, backups, lease_id)
        from installer.mobile_preparation_runtime import NativePreparation
        preparation = NativePreparation(http, scope, lease_id, backups, worker, source, payload, self.authority)
        with authority.admitted():
            for stage in resume.h.STAGES:
                result = preparation.execute(stage)
                authority.save(stage + '.done.json', resume.canonical_bytes({'owner': authority.binding(),
                    'stage': stage, 'result_sha256': resume.sha(resume.canonical_bytes(result))}))
        real_unlink = os.unlink
        def interrupted():
            def unlink(name, *args, **options):
                real_unlink(name, *args, **options)
                if name == resume.h.MARKERS[0]: os.kill(os.getpid(), signal.SIGKILL)
            with patch.object(resume.h.os, 'unlink', unlink): invoke('resume')
        self.kill_child(interrupted)
        self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        self.assertFalse((scope.directory / resume.h.MARKERS[0]).exists())
        self.assertFalse((root / (resume.h.MARKERS[0] + '.removed.json')).exists())
        self.assertFalse(starts.exists())
        selected.stopped(); selected.foundation.stopped()
        boundary = 'php-start'; self.kill_child(lambda: invoke('resume'))
        self.assertEqual(scope.observe()['state'], 'SERVING')
        self.assertEqual([row['role'] for row in json.loads(starts.read_text())], ['php'])
        boundary = 'serving'; released = True
        with patch.object(activation.a.c, '_recheck', side_effect=AssertionError('SQL replay after admission')):
            result = invoke('resume')
        self.assertEqual([row['role'] for row in json.loads(starts.read_text())], list(activation.v.ROLES))
        self.assertTrue(result['local_web']['login_page']); self.assertEqual(result['target_commit'], target_pin)
        selected.owned(); selected.foundation.owned()
        self.assertEqual(self.sqlite_identity(selected), fixture['uuid'])
        self.assertEqual(selected.profile.selected_release['commit'], target_pin)
        before = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in root.iterdir()}
        boundary = 'check'
        with patch.object(activation.v.NativeRuntime, 'start', side_effect=AssertionError('start replay')):
            checked = resume.execute(http, scope, lease_id, backups, worker, source, payload, self.authority,
                confirmation=prepared['plan_sha256'], action='check', confirmed=True, allow_global_read_lock=True)
        self.assertTrue(checked['local_web']['login_page'])
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in root.iterdir()})
        for path, raw in fixture['preserved'].items(): self.assertEqual(path.read_bytes(), raw)
        with scope.writer(): pass
        Path('/evidence/gateway-successor-' + direction + '.json').write_bytes(prior.quality.encode({
            'status': 'PASS', 'direction': direction, 'source': source_pin, 'target': target_pin,
            'successor_sigkill_boundaries': ['cutover-guard-unlink-before-receipt', 'php-start-before-receipt'],
            'sql_fence_released_before_starts': True, 'real_activity_lock_through_five_starts': True,
            'source_journals_config_keys_preserved': True, 'lost_start_reply_without_second_start': True,
            'completed_check_read_only': True, 'local_login_page_available': True,
            'target_gateway_owned': True, 'installation_uuid_sha256': hashlib.sha256(fixture['uuid'].encode()).hexdigest(),
            'sqlite_schema': 6, 'result': result, 'boot_requalified': False, 'phase6_complete': False}))

    def test_upgrade_successor_native_sigkill(self):
        self.exercise_resume(prior.LEGACY_COMMIT, prior.FCM_COMMIT, 'upgrade')

    def test_downgrade_successor_native_sigkill(self):
        self.exercise_resume(prior.FCM_COMMIT, prior.LEGACY_COMMIT, 'rollback')
