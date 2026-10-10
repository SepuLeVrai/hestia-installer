"""Real files and maintenance locks; no claim of native public/boot admission."""
from copy import deepcopy
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import patch

from installer import gateway_public_fragments as f
from installer.maintenance import MaintenanceScope
from installer.model import canonical_bytes


class PublicFragmentResourceTests(unittest.TestCase):
    def test_instance_grammar_is_closed(self):
        for value in ('', '../foreign', 'a' * 31, 'a' * 33, 'A' * 32, 'a' * 32 + '\n', 1, True, None):
            with self.assertRaises(f.FragmentError): f.resources(value)

    def test_allowlist_matches_actual_generated_public_and_boot_resources(self):
        from test_mobile_boot import profile
        from installer.mobile_boot_runtime import MobileBootRuntime
        runtime = MobileBootRuntime(profile()); public = runtime.shared; boot = public.boot
        expected = (boot.guard, boot.ready, runtime.target, public.web.unit('http'),
                    public.web.unit('https'), public.web.unit('renew'), public.web.unit('timer'),
                    runtime.http.unit('apache') + '.d/60-hestia-public.conf')
        self.assertEqual(f.resources(runtime.layout.instance), expected)
        self.assertEqual(len(set(expected)), 8)
        self.assertTrue(set(expected[:-1]) <= set(boot.units()) | set(runtime.units()) | set(public.units()))


class PublicFragmentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hestia-public-fragments-', dir='/var/lib')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.base.chmod(0o755)
        self.units = self.base / 'units'; self.units.mkdir()
        self.private = self.base / 'control'; self.private.mkdir(mode=0o700)
        self.scope = MaintenanceScope(self.base / 'maintenance', 65534, 'a' * 32)
        self.scope.create(confirmed=True)
        self.lease = self.scope.acquire(confirmed=True)
        self.addCleanup(lambda: self.lease.close())
        self.lease_id = self.lease.lease_id
        self.root = self.private / ('public-fragments-' + self.lease_id)
        self.refs = dict.fromkeys(f.REFS, 'b' * 64); self.refs['target_gateway'] = 'c' * 64
        self.replacements = {name: (('source-' + str(i)).encode(), ('target-' + str(i)).encode())
                             for i, name in enumerate(f.resources(self.scope.instance))}
        for name, (raw, _) in self.replacements.items():
            path = self.units / name; path.parent.mkdir(exist_ok=True)
            path.write_bytes(raw); path.chmod(0o644)
        self.transfer = self.new()

    def new(self, **kwargs):
        return f.FragmentTransfer(**{'root': self.root, 'unit_root': self.units, 'lease': self.lease,
                                     'references': self.refs, 'replacements': self.replacements, **kwargs})

    def prepare(self):
        report = self.transfer.prepare(confirmed=True)
        self.confirmation = report['plan_sha256']
        return report

    def step(self, **kwargs):
        return self.transfer.replace_next(**{'confirmation': self.confirmation, 'confirmed': True, **kwargs})

    def first(self): return self.units / next(iter(self.replacements))

    def snapshot(self):
        return {str(p.relative_to(self.base)): (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
                for p in self.base.rglob('*') if p.is_file()}

    def seed_interruption(self, suffix):
        put = f._put
        def interrupted(fd, name, value):
            put(fd, name, value)
            if name == '0.' + suffix + '.json': raise RuntimeError('lost receipt response')
        with patch.object(f, '_put', interrupted), self.assertRaises(RuntimeError): self.step()

    def test_exact_generated_resource_order_excludes_database_and_binary(self):
        names = f.resources('a' * 32)
        self.assertEqual(len(names), 8)
        self.assertEqual(len(set(names)), 8)
        self.assertTrue(all(n.startswith('hestia-' + 'a' * 32) for n in names))
        self.assertIn('-boot-web.service', names[1])
        self.assertTrue(names[-1].endswith('apache.service.d/60-hestia-public.conf'))

    def test_plan_binds_generations_source_inodes_directories_code_and_lease(self):
        report = self.prepare()
        with f.fs._directory(self.root) as fd: plan = f._optional(fd, 'plan.json')
        self.assertEqual(plan['binding']['references'], self.refs)
        self.assertEqual(plan['binding']['lease_id'], self.lease_id)
        self.assertEqual(plan['sources'][next(iter(self.replacements))]['inode'], self.first().stat().st_ino)
        self.assertEqual(report['state'], 'INCOMPLETE')
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def test_full_transfer_preserves_unrelated_files_and_never_reloads_or_starts(self):
        unrelated = self.base / 'database.sqlite'; unrelated.write_bytes(b'unchanged-sqlite')
        before = (unrelated.read_bytes(), unrelated.stat().st_ino)
        self.prepare()
        with patch('subprocess.run', side_effect=AssertionError('native command')):
            for _ in self.replacements: report = self.step()
            self.assertEqual(report['state'], 'FRAGMENTS_REPLACED')
            saved = self.snapshot(); self.assertEqual(self.step(), report)
            self.assertEqual(self.transfer.check(), report); self.assertEqual(saved, self.snapshot())
        self.assertEqual(before, (unrelated.read_bytes(), unrelated.stat().st_ino))
        for name, (_, target) in self.replacements.items(): self.assertEqual((self.units / name).read_bytes(), target)
        for name in ('services_started', 'systemd_reloaded', 'activity_resumed', 'boot_requalified', 'phase6_complete'):
            self.assertIs(report[name], False)

    def test_check_and_prepare_do_not_replace_active_fragments(self):
        before = {name: ((self.units / name).read_bytes(), (self.units / name).stat().st_ino)
                  for name in self.replacements}
        self.prepare(); saved = self.snapshot()
        self.transfer.check(); self.transfer.check()
        self.assertEqual(saved, self.snapshot())
        self.assertEqual(before, {name: ((self.units / name).read_bytes(), (self.units / name).stat().st_ino)
                                 for name in self.replacements})

    def test_consent_and_exact_confirmation_precede_effects(self):
        for value in (False, 1, None, 'true'):
            with self.assertRaises(f.FragmentError): self.transfer.prepare(confirmed=value)
            self.assertFalse(self.root.exists())
        self.prepare(); saved = self.snapshot()
        for kwargs in ({'confirmed': False}, {'confirmed': 1}, {'confirmation': '0' * 64}, {'confirmation': None}):
            with self.assertRaises(f.FragmentError): self.step(**kwargs)
            self.assertEqual(saved, self.snapshot())

    def test_invalid_references_and_resource_sets_refused_before_effect(self):
        for refs in ({}, {**self.refs, 'extra': 'b' * 64}, {**self.refs, 'publication': True},
                     {**self.refs, 'source_gateway': self.refs['target_gateway']}):
            with self.assertRaises(f.FragmentError): self.new(references=refs)
        pairs = list(self.replacements.items())
        for values in ({}, dict(reversed(pairs)), {**self.replacements, '../foreign': (b'a', b'b')},
                       {**self.replacements, pairs[0][0]: (b'', b'b')},
                       {**self.replacements, pairs[0][0]: (b'a', b'a')},
                       {**self.replacements, pairs[0][0]: (b'a', b'b' * (f.LIMIT + 1))}):
            with self.assertRaises(f.FragmentError): self.new(replacements=values)
        self.assertFalse(self.root.exists())

    def test_mutated_caller_maps_do_not_change_binding(self):
        self.prepare(); before = deepcopy(self.transfer.binding)
        self.refs['publication'] = 'd' * 64; self.replacements.clear()
        self.assertEqual(self.transfer.binding, before)
        self.assertEqual(self.transfer.check()['plan_sha256'], self.confirmation)

    def test_closed_lease_is_not_an_authority(self):
        self.prepare(); saved = self.snapshot(); self.lease.close()
        with self.assertRaises(Exception): self.step()
        self.assertEqual(saved, self.snapshot())

    def test_parent_directory_same_contents_new_inode_refused(self):
        self.prepare()
        self.units.rename(self.base / 'old-units'); self.units.mkdir()
        for name, (raw, _) in self.replacements.items():
            path = self.units / name; path.parent.mkdir(exist_ok=True); path.write_bytes(raw); path.chmod(0o644)
        with self.assertRaises(f.FragmentError): self.step()

    def test_source_same_bytes_new_inode_refused(self):
        self.prepare(); path = self.first(); path.rename(path.with_suffix('.old'))
        path.write_bytes(next(iter(self.replacements.values()))[0]); path.chmod(0o644)
        with self.assertRaises(f.FragmentError): self.step()

    def test_target_same_bytes_foreign_inode_refused_after_rename(self):
        self.prepare(); self.step(); path = self.first(); raw = path.read_bytes()
        path.rename(path.with_suffix('.old')); path.write_bytes(raw); path.chmod(0o644)
        with self.assertRaises(f.FragmentError): self.transfer.check()

    def test_symlink_hardlink_and_writable_fragment_refused(self):
        path = self.first(); before = path.read_bytes()
        path.rename(path.with_suffix('.old')); path.symlink_to(path.with_suffix('.old'))
        with self.assertRaises(Exception): self.prepare()
        path.unlink(); os.link(path.with_suffix('.old'), path)
        with self.assertRaises(Exception): self.prepare()
        path.unlink(); path.write_bytes(before); path.chmod(0o666)
        with self.assertRaises(Exception): self.prepare()
        self.assertFalse(self.root.exists())

    def test_foreign_temporary_is_never_adopted_even_with_target_bytes(self):
        self.prepare(); temporary = self.first().with_name(self.transfer._temporary(self.first().name))
        temporary.write_bytes(next(iter(self.replacements.values()))[1]); temporary.chmod(0o644)
        with self.assertRaises(f.FragmentError): self.step()
        self.assertTrue(temporary.exists())

    def test_same_byte_foreign_seed_inode_is_refused(self):
        self.prepare(); self.seed_interruption('seed')
        temporary = self.first().with_name(self.transfer._temporary(self.first().name))
        temporary.rename(temporary.with_suffix('.old')); temporary.write_bytes(b''); temporary.chmod(0o644)
        with self.assertRaises(f.FragmentError): self.step()

    def test_partial_owned_prefix_resumes_but_wrong_prefix_refuses(self):
        self.prepare(); self.seed_interruption('seed')
        temporary = self.first().with_name(self.transfer._temporary(self.first().name))
        temporary.write_bytes(b'tar')
        self.assertEqual(self.step()['fragments'][0]['state'], 'DONE')

    def test_partial_owned_wrong_bytes_refused(self):
        self.prepare(); self.seed_interruption('seed')
        temporary = self.first().with_name(self.transfer._temporary(self.first().name))
        temporary.write_bytes(b'foreign')
        with self.assertRaises(f.FragmentError): self.step()

    def test_armed_lost_reply_resumes_without_new_inode(self):
        self.prepare(); self.seed_interruption('armed')
        temporary = self.first().with_name(self.transfer._temporary(self.first().name)); inode = temporary.stat().st_ino
        self.assertEqual(self.step()['fragments'][0]['state'], 'DONE')
        self.assertEqual(self.first().stat().st_ino, inode)

    def test_done_lost_reply_does_not_replay_previous_rename(self):
        self.prepare(); self.seed_interruption('done'); inode = self.first().stat().st_ino
        self.assertEqual(self.step()['fragments'][1]['state'], 'DONE')
        self.assertEqual(self.first().stat().st_ino, inode)

    def test_lost_rename_reply_is_observed_without_replay(self):
        self.prepare(); rename = os.rename
        def interrupted(*args, **kwargs):
            rename(*args, **kwargs); raise RuntimeError('lost rename reply')
        with patch.object(f.os, 'rename', interrupted), self.assertRaises(RuntimeError): self.step()
        inode = self.first().stat().st_ino
        self.assertEqual(self.transfer.check()['fragments'][0]['state'], 'RENAMED')
        with patch.object(f.os, 'rename', side_effect=AssertionError('rename replay')):
            self.assertEqual(self.step()['fragments'][0]['state'], 'DONE')
        self.assertEqual(self.first().stat().st_ino, inode)

    def test_journal_tamper_unknown_file_and_out_of_order_receipt_refused(self):
        self.prepare(); (self.root / 'foreign.json').write_bytes(b'{}')
        with self.assertRaises(f.FragmentError): self.step()
        (self.root / 'foreign.json').unlink()
        (self.root / '1.done.json').write_bytes(canonical_bytes({'foreign': True})); (self.root / '1.done.json').chmod(0o600)
        with self.assertRaises(f.FragmentError): self.step()

    def test_plan_same_bytes_different_slot_refused(self):
        self.prepare(); self.root.rename(self.private / 'old-slot'); self.root.mkdir(mode=0o700)
        (self.root / 'plan.json').write_bytes((self.private / 'old-slot/plan.json').read_bytes())
        (self.root / 'plan.json').chmod(0o600)
        with self.assertRaises(f.FragmentError): self.step()

    def test_changed_generation_cannot_resume_existing_transaction(self):
        self.prepare(); self.refs['publication'] = 'd' * 64
        with self.assertRaises(f.FragmentError): self.new().check()

    def test_real_sigkill_after_rename_reacquires_maintenance_and_never_replays(self):
        self.prepare(); self.lease.close()
        pid = os.fork()
        if pid == 0:
            try:
                self.lease = self.scope.recover(self.lease_id, confirmed=True); self.transfer = self.new()
                rename = os.rename
                def kill_after(*args, **kwargs):
                    rename(*args, **kwargs)
                    os.fsync(kwargs['dst_dir_fd']); os.kill(os.getpid(), signal.SIGKILL)
                with patch.object(f.os, 'rename', kill_after): self.step()
            except BaseException: os._exit(98)
            os._exit(97)
        deadline = time.monotonic() + 10
        while True:
            found, status = os.waitpid(pid, os.WNOHANG)
            if found: break
            if time.monotonic() > deadline:
                os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0); self.fail('Child timeout')
            time.sleep(.02)
        self.assertTrue(os.WIFSIGNALED(status)); self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        self.lease = self.scope.recover(self.lease_id, confirmed=True); self.transfer = self.new()
        with patch.object(f.os, 'rename', side_effect=AssertionError('rename replay')):
            self.assertEqual(self.step()['fragments'][0]['state'], 'DONE')

    def reader(self, **kwargs):
        return f.CompletedFragments(**{'root': self.root, 'unit_root': self.units,
            'instance': self.scope.instance, 'lease_id': self.lease_id,
            'maintenance': self.scope.directory, 'references': self.refs,
            'replacements': self.replacements, **kwargs})

    def complete(self):
        self.prepare()
        for _ in self.replacements: self.step()

    def test_completed_reader_after_lease_close_has_no_effect_or_mutation_api(self):
        self.complete(); self.lease.close(); saved = self.snapshot(); reader = self.reader()
        self.assertFalse(hasattr(reader, 'replace_next')); self.assertFalse(hasattr(reader, 'prepare'))
        with patch('subprocess.run', side_effect=AssertionError('native command')):
            self.assertEqual(reader.check(self.confirmation)['state'], 'FRAGMENTS_REPLACED')
            reader.check(self.confirmation)
        self.assertEqual(saved, self.snapshot())

    def test_completed_reader_refuses_incomplete_and_lost_done_receipt(self):
        self.prepare()
        with self.assertRaises(f.FragmentError): self.reader().check(self.confirmation)
        for _ in self.replacements: self.step()
        (self.root / '7.done.json').unlink()
        with self.assertRaises(f.FragmentError): self.reader().check(self.confirmation)

    def test_completed_reader_rejects_same_bytes_foreign_inode_and_changed_reference(self):
        self.complete()
        with self.assertRaises(f.FragmentError):
            self.reader(references={**self.refs, 'publication': 'f' * 64}).check(self.confirmation)
        with self.assertRaises(f.FragmentError): self.reader().check('f' * 64)
        path = self.first(); raw = path.read_bytes(); path.rename(path.with_suffix('.old'))
        path.write_bytes(raw); path.chmod(0o644)
        with self.assertRaises(f.FragmentError): self.reader().check(self.confirmation)


if __name__ == '__main__': unittest.main()
