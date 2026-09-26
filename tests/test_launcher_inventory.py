"""Pure declaration contract tests; these do not simulate a verified host."""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from installer import launcher_inventory as l
from installer import php_transport as p, storage_inventory as s
from installer.web_releases import STORAGE_COMMIT, LEGACY_COMMIT, get_release

NOW = 1790404200
HASH = 'a' * 64


def target():
    return l.LauncherTarget('a'*32, STORAGE_COMMIT, get_release(STORAGE_COMMIT).tree,
        '/srv/private-web', '/var/lib/private-slot', '/var/lib/private-slot/maintenance',
        991, 991, 'b'*32, '11111111-2222-3333-4444-555555555555')


def storage():
    t = target()
    # Real resolver semantics, synthetic configuration only. Source-pin IO is
    # exercised by its own integration suite, never claimed by this model.
    facts = s.StorageFacts(Path(t.webroot), Path(t.configuration),
        {**{name: '' for name in s.ENVIRONMENT}, 'HESTIA_UPLOAD_STORAGE': '/var/lib/private-data/uploads'},
        {'HESTIA_IMPORT_STORAGE': None}, {name: '' for name in s.APP_CONFIG},
        {'session.save_handler': 'files', 'session.save_path': '/var/lib/private-data/sessions',
         'session.gc_maxlifetime': '43200', 'effective_sys_temp_dir': '/var/lib/private-data/tmp',
         'upload_tmp_dir': '/var/lib/private-data/upload-tmp', 'error_log': '/var/lib/private-data/log/php.log'})
    with patch.object(s, '_verify_source'):
        return s.StorageInventory(Path('/unused'), repository=p.WEB_REPOSITORY, commit=STORAGE_COMMIT).inspect(facts)


def launcher():
    return l.LauncherObservation('private-task', 'systemd_system',
        (l.FileObservation('/etc/systemd/system/private.service', HASH),),
        (l.FileObservation('/usr/bin/php8.4', HASH), l.FileObservation('/srv/private-web/scripts/private.php', HASH)),
        'fixed', HASH, HASH, 991, 991, (), '/', ('imports_effective',), HASH,
        'idle', 'disarmed', '/system.slice/private.service', target().maintenance)


def snapshot():
    return l.LauncherSnapshot(target(), NOW,
        tuple(l.CoverageObservation(c, 'observed', HASH) for c in l.CHANNELS), (launcher(),))


class LauncherInventoryTests(unittest.TestCase):
    def setUp(self):
        self.storage = storage()
        self.inventory = l.LauncherInventory(target(), self.storage)
        self.snapshot = snapshot()

    def inspect(self, value=None, **kwargs):
        return self.inventory.inspect(self.snapshot if value is None else value, now=NOW, **kwargs)

    def row(self, **changes):
        return replace(self.snapshot, launchers=(replace(launcher(), **changes),))

    def test_complete_declarations_do_not_grant_execution_drain_or_host_coverage(self):
        result = self.inspect(); report = result.report()
        self.assertEqual(report['state'], 'LAUNCHER_DECLARATIONS_RECORDED')
        self.assertEqual((report['input_channels'], report['input_launchers']), (6, 1))
        self.assertTrue(set(l.BASE_BLOCKERS) <= set(report['blockers']))
        self.assertIn('LAUNCHER_EXECUTION_NOT_ENROLLED', report['blockers'])
        self.assertIn('STORAGE_REQUIREMENTS_UNRESOLVED', report['blockers'])
        for key, value in report.items():
            if type(value) is bool: self.assertIs(value, False, key)

    def test_empty_observed_channels_never_prove_absence_or_complete_inventory(self):
        result = self.inspect(replace(self.snapshot, launchers=()))
        self.assertEqual(result.report()['input_launchers'], 0)
        self.assertFalse(result.report()['host_scheduler_inventory_complete'])
        self.assertIn('COVERAGE_NOT_CERTIFIED', result.report()['blockers'])

    def test_unknown_unreadable_and_partial_channels_are_retained_explicitly(self):
        for state in ('unknown', 'unreadable', 'partial'):
            value = replace(self.snapshot, coverage=tuple(l.CoverageObservation(c, state, None) for c in l.CHANNELS))
            result = self.inspect(value)
            self.assertEqual({c['state'] for c in result.private_manifest()['coverage']}, {state})
            self.assertIn('CHANNEL_COVERAGE_UNRESOLVED', result.report()['blockers'])
            self.assertEqual(result.report()['input_launchers'], 1)

    def test_missing_duplicate_unknown_channels_and_observed_without_evidence_are_rejected(self):
        for rows in (self.snapshot.coverage[:-1], (self.snapshot.coverage[0],)*6,
            (*self.snapshot.coverage[:-1], l.CoverageObservation('invented', 'observed', HASH)),
            (*self.snapshot.coverage[:-1], l.CoverageObservation(l.CHANNELS[-1], 'observed', None))):
            with self.subTest(rows=rows), self.assertRaises(l.LauncherInventoryError):
                self.inspect(replace(self.snapshot, coverage=rows))

    def test_snapshot_must_match_instance_host_boot_identity_and_paths(self):
        changes = ({'instance': 'c'*32}, {'host_id': 'd'*32}, {'boot_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'},
                   {'web_uid': 992}, {'web_gid': 992}, {'webroot': '/srv/other'},
                   {'configuration': '/var/lib/other', 'maintenance': '/var/lib/other/maintenance'})
        for change in changes:
            with self.subTest(change=change), self.assertRaisesRegex(l.LauncherInventoryError, 'TARGET_MISMATCH'):
                self.inspect(replace(self.snapshot, target=replace(target(), **change)))

    def test_only_exact_supported_release_and_tree_bindings_are_accepted(self):
        for changes in ({'source_commit': LEGACY_COMMIT}, {'source_commit': 'main'},
                        {'source_commit': STORAGE_COMMIT[:8]}, {'source_tree': '0'*40}):
            with self.subTest(changes=changes), self.assertRaisesRegex(l.LauncherInventoryError, 'SOURCE_MISMATCH'):
                l.LauncherInventory(replace(target(), **changes), self.storage)

    def test_storage_source_paths_duplicate_roles_and_producer_claims_cannot_be_substituted(self):
        for change in ('source', 'runtime', 'webroot', 'configuration', 'duplicate', 'producer', 'missing', 'unknown_key', 'version'):
            data = self.storage.private_manifest()
            if change == 'source': data['source_commit'] = LEGACY_COMMIT
            if change == 'runtime': data['runtime_sha256'] = '0'*64
            if change in ('webroot', 'configuration'):
                role = 'uploads' if change == 'webroot' else 'managed_configuration'
                next(r for r in data['scopes'] if r['role'] == role)['path'] = '/other'
            if change == 'duplicate': data['scopes'].append(data['scopes'][0])
            if change == 'producer': data['producers'][0]['state'] = 'VERIFIED'
            if change == 'missing': data['producers'].pop()
            if change == 'unknown_key': data['secret'] = 'must-not-leak'
            if change == 'version': data['version'] = True
            with self.subTest(change=change), self.assertRaises(l.LauncherInventoryError):
                l.LauncherInventory(target(), s.StorageRequirements(p._json(data)))

    def test_noncanonical_paths_and_invalid_target_identity_are_rejected(self):
        for path in ('relative', '/', '//server/share', '/x/../y', '/x//y', '/x/./y', '/x/', '/bad\nname', '/bad\x00name'):
            with self.subTest(path=path), self.assertRaises(l.LauncherInventoryError):
                self.inspect(self.row(chain=(l.FileObservation(path, HASH),)))
        for change in ({'web_uid': True}, {'web_uid': 0}, {'web_gid': -1}, {'host_id': 'name'},
                       {'boot_id': 'private-invalid'}, {'maintenance': '/var/lib/wrong/maintenance'},
                       {'configuration': '/etc/slot', 'maintenance': '/etc/slot/maintenance'}):
            with self.subTest(change=change), self.assertRaises(l.LauncherInventoryError):
                l.LauncherInventory(replace(target(), **change), self.storage)

    def test_observation_freshness_uses_explicit_time_and_rejects_future_stale_or_boolean(self):
        self.inspect(replace(self.snapshot, observed_at=NOW-l.MAX_AGE_SECONDS))
        for at in (NOW+1, NOW-l.MAX_AGE_SECONDS-1, -1, True, float(NOW), str(NOW)):
            with self.subTest(at=at), self.assertRaisesRegex(l.LauncherInventoryError, 'OBSERVATION_STALE'):
                self.inspect(replace(self.snapshot, observed_at=at))
        for now in (True, float(NOW), str(NOW), 2**63):
            with self.assertRaises(l.LauncherInventoryError): self.inventory.inspect(self.snapshot, now=now)

    def test_unresolved_context_is_retained_and_never_invented_from_defaults(self):
        result = self.inspect(self.row(definitions=(), chain=(), chain_mode='unknown', argv_sha256=None,
            environment_sha256=None, sql_identities_sha256=None, uid=None, gid=None, groups=None,
            cwd=None, storage_roles=None, state='unknown', trigger='unknown', cgroup=None, maintenance=None))
        row = result.private_manifest()['launchers'][0]
        self.assertIsNone(row['uid']); self.assertIsNone(row['storage_roles']); self.assertEqual(row['chain'], [])
        self.assertTrue({'IDENTITY_UNRESOLVED', 'EXECUTION_CONTEXT_UNRESOLVED', 'LIVE_STATE_UNRESOLVED',
            'COMMON_GATE_UNRESOLVED_OR_DIFFERENT', 'DEFINITION_OR_CHAIN_UNRESOLVED'} <= set(row['issues']))

    def test_dynamic_chain_and_missing_fingerprint_remain_blockers(self):
        for mode in ('dynamic', 'unknown'):
            result = self.inspect(self.row(chain_mode=mode, chain=(l.FileObservation('/usr/bin/php8.4', None),)))
            self.assertIn('DYNAMIC_OR_UNKNOWN_CHAIN', result.report()['blockers'])
            self.assertIn('DEFINITION_OR_CHAIN_UNRESOLVED', result.report()['blockers'])

    def test_root_and_other_identities_and_supplementary_groups_are_not_dropped(self):
        for uid in (0, 1234):
            result = self.inspect(self.row(uid=uid, gid=uid, groups=(0, 555), cgroup='/'))
            self.assertEqual(result.private_manifest()['launchers'][0]['uid'], uid)
            self.assertIn('OTHER_IDENTITY_REQUIRES_COORDINATION', result.report()['blockers'])
            self.assertIn('SUPPLEMENTARY_GROUPS_REQUIRE_REVIEW', result.report()['blockers'])

    def test_active_queued_rearmed_or_foreign_gate_cannot_look_drained(self):
        for state in ('active', 'queued'):
            report = self.inspect(self.row(state=state, trigger='armed', maintenance='/var/lib/other/maintenance')).report()
            self.assertTrue({'ACTIVE_OR_QUEUED_LAUNCHER', 'ARMED_TRIGGER', 'COMMON_GATE_UNRESOLVED_OR_DIFFERENT'} <= set(report['blockers']))
            self.assertFalse(report['drain_allowed'])

    def test_roles_must_exist_in_storage_requirements_and_empty_is_distinct_from_unknown(self):
        with self.assertRaisesRegex(l.LauncherInventoryError, 'UNKNOWN_STORAGE_ROLE'):
            self.inspect(self.row(storage_roles=('invented-root',)))
        self.assertEqual(self.inspect(self.row(storage_roles=())).private_manifest()['launchers'][0]['storage_roles'], [])
        self.assertIsNone(self.inspect(self.row(storage_roles=None)).private_manifest()['launchers'][0]['storage_roles'])

    def test_duplicate_records_files_groups_and_roots_are_rejected(self):
        values = [replace(self.snapshot, launchers=(launcher(), launcher())),
            self.row(definitions=(launcher().definitions[0],)*2), self.row(chain=(launcher().chain[0],)*2),
            self.row(groups=(1,1)), self.row(storage_roles=('uploads','uploads'))]
        for value in values:
            with self.subTest(value=value), self.assertRaisesRegex(l.LauncherInventoryError, 'DUPLICATE_REFERENCE'):
                self.inspect(value)

    def test_unknown_enums_raw_arguments_and_non_typed_observations_are_rejected(self):
        for change in ({'channel':'new'}, {'state':'stopped'}, {'trigger':'disabled'}, {'chain_mode':'shell'},
                       {'argv_sha256':'--password=private'}, {'environment_sha256':'SECRET=private'},
                       {'uid':True}, {'groups':(False,)}, {'chain':[launcher().chain[0]]}, {'key':'unsafe/key'}):
            with self.subTest(change=change), self.assertRaises(l.LauncherInventoryError): self.inspect(self.row(**change))
        for value in ({}, object(), None):
            with self.assertRaises(l.LauncherInventoryError): self.inventory.inspect(value, now=NOW)
        with self.assertRaises(l.LauncherInventoryError): self.inspect(replace(self.snapshot, launchers=({},)))

    def test_counts_strings_and_serialized_document_are_bounded(self):
        rows = tuple(replace(launcher(), key='job'+str(n), definitions=(), chain=()) for n in range(l.MAX_LAUNCHERS))
        self.assertEqual(self.inspect(replace(self.snapshot, launchers=rows)).report()['input_launchers'], l.MAX_LAUNCHERS)
        for value in (replace(self.snapshot, launchers=rows+(launcher(),)),
                      self.row(chain=tuple(l.FileObservation('/bin/e'+str(n), HASH) for n in range(l.MAX_CHAIN+1))),
                      self.row(definitions=tuple(l.FileObservation('/etc/e'+str(n), HASH) for n in range(l.MAX_DEFINITIONS+1))),
                      self.row(cwd='/'+'x'*2048)):
            with self.assertRaises(l.LauncherInventoryError): self.inspect(value)
        chain = tuple(l.FileObservation('/'+str(n)+'x'*2000, HASH) for n in range(l.MAX_CHAIN))
        large = tuple(replace(launcher(), key='job'+str(n), chain=chain) for n in range(20))
        with self.assertRaisesRegex(l.LauncherInventoryError, 'DOCUMENT_LIMIT'):
            self.inspect(replace(self.snapshot, launchers=large))

    def test_semantic_order_is_stable_but_executable_chain_order_is_significant(self):
        rows = (launcher(), replace(launcher(), key='another'))
        first = self.inspect(replace(self.snapshot, launchers=rows))
        shuffled = replace(self.snapshot, coverage=tuple(reversed(self.snapshot.coverage)), launchers=tuple(reversed(rows)))
        self.assertEqual(self.inspect(shuffled, previous=first)._canonical, first._canonical)
        with self.assertRaisesRegex(l.LauncherInventoryError, 'OBSERVATIONS_CHANGED'):
            self.inspect(self.row(chain=tuple(reversed(launcher().chain))), previous=self.inspect())

    def test_later_identical_snapshot_rechecks_but_report_is_not_a_live_lease(self):
        before = self.inspect(); later = replace(self.snapshot, observed_at=NOW+10)
        after = self.inventory.inspect(later, now=NOW+10, previous=before)
        self.assertNotEqual(before.report()['manifest_sha256'], after.report()['manifest_sha256'])
        self.assertFalse(after.report()['live_receipt'])
        with self.assertRaisesRegex(l.LauncherInventoryError, 'OBSERVATION_REPLAYED'):
            self.inspect(previous=after)

    def test_recheck_rejects_definition_state_environment_or_coverage_drift(self):
        previous = self.inspect()
        values = [self.row(state='active'), self.row(trigger='armed'), self.row(environment_sha256='b'*64),
            self.row(definitions=(l.FileObservation(launcher().definitions[0].path, 'b'*64),)),
            replace(self.snapshot, coverage=(l.CoverageObservation(l.CHANNELS[0], 'partial', HASH), *self.snapshot.coverage[1:]))]
        for value in values:
            with self.subTest(value=value), self.assertRaisesRegex(l.LauncherInventoryError, 'OBSERVATIONS_CHANGED'):
                self.inspect(value, previous=previous)

    def test_recheck_binds_storage_digest_and_rejects_foreign_or_malformed_previous_result(self):
        previous = self.inspect(); data = self.storage.private_manifest()
        next(x for x in data['scopes'] if x['role']=='temporary')['path'] = '/var/lib/changed-temp'
        other = l.LauncherInventory(target(), s.StorageRequirements(p._json(data)))
        with self.assertRaisesRegex(l.LauncherInventoryError, 'OBSERVATIONS_CHANGED'):
            other.inspect(self.snapshot, now=NOW, previous=previous)
        for bad in ({}, l.LauncherRequirements(b'not-json'), l.LauncherRequirements(b'{}')):
            with self.assertRaises(l.LauncherInventoryError): self.inspect(previous=bad)

    def test_reports_reprs_errors_and_returned_manifests_preserve_privacy_and_immutability(self):
        result = self.inspect(); before = result._canonical
        result.private_manifest()['launchers'].clear(); result.report()['blockers'].clear()
        self.assertEqual(result._canonical, before); self.assertEqual(result.report()['input_launchers'], 1)
        with self.assertRaises(FrozenInstanceError): result._canonical = b'{}'
        with self.assertRaises(FrozenInstanceError): self.snapshot.target.web_uid = 0
        public = json.dumps(result.report()) + repr(result) + repr(self.inventory) + repr(self.snapshot) + repr(launcher())
        for private in ('private-task','private-web','private-slot','private.service',target().host_id,target().boot_id):
            self.assertNotIn(private, public)
        with self.assertRaises(l.LauncherInventoryError) as context:
            self.inspect(self.row(argv_sha256='private-password'))
        self.assertEqual(str(context.exception), 'LAUNCHER_INPUT_REJECTED')

    def test_validator_never_opens_files_processes_network_or_an_implicit_clock(self):
        with patch('builtins.open', side_effect=AssertionError('IO forbidden')), \
             patch('pathlib.Path.read_bytes', side_effect=AssertionError('IO forbidden')), \
             patch('subprocess.run', side_effect=AssertionError('process forbidden')), \
             patch('subprocess.Popen', side_effect=AssertionError('process forbidden')), \
             patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             patch('time.time', side_effect=AssertionError('implicit clock forbidden')):
            inventory = l.LauncherInventory(target(), self.storage)
            first = inventory.inspect(self.snapshot, now=NOW)
            inventory.inspect(self.snapshot, now=NOW, previous=first)


if __name__ == '__main__': unittest.main()
