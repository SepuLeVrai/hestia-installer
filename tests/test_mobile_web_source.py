"""Successor source, SQL and configuration bindings without host effects."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from installer import application_plan as app, database_step as db, finalization as f
from installer import http_runtime as h, mobile_web_source as mobile, upgrade_catalog as catalog
from installer.application_activation import Activation
from installer.engine import TransactionEngine
from installer.foundation_runtime import FoundationRuntime
from installer.gateway_identity import public_identity
from installer.gateway_service_profile import GatewayServiceProfile
from installer.model import InstallerError, strict_json_loads
from installer.operations import default_registry
from installer.transaction import StateJournal
from installer.web_releases import LEGACY_COMMIT, STORAGE_COMMIT, get_release
from test_application_plan import setup_payload


class MobileWebSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = TransactionEngine(StateJournal(self.root / 'state.json'), default_registry())
        self.plan = app.ApplicationPlan(self.engine, Mock())

    def draft(self, version=2):
        payload = setup_payload()
        if version == 2: payload['profile'] = 'fresh-mobile-staged-v2'
        return self.plan.save(payload)

    def foundation(self, version=2, origin='https://mobile.example.test'):
        draft = self.draft(version); layout = app.FreshProfile.from_draft(draft)
        http = layout.http(draft['configuration'])
        # Standard P-256 generator, public verification material only.
        from installer.gateway_identity import _b64
        jwk = {'kty': 'EC', 'crv': 'P-256',
            'x': _b64(bytes.fromhex('6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296')),
            'y': _b64(bytes.fromhex('4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5'))}
        identity = {'version': 1, 'instance': draft['instance'], 'public_origin': origin, 'dev_enabled': False}
        foundation = FoundationRuntime.for_gateway(Activation(http, 'b' * 64), public_identity('main', jwk), identity)
        return foundation, identity

    def test_historical_pins_and_sql_contracts_are_unchanged(self):
        self.assertEqual(get_release(LEGACY_COMMIT).runtime_sha256,
            'b2205c6f7b326b0692e57942f282260669e3ec147daf3dc9f18c546782c7eb0f')
        self.assertEqual(get_release(STORAGE_COMMIT).runtime_sha256,
            '42c99a13f41b50a5263c69d14557dd088b5787b40ffdc51873b8d61ea1bc8edb')
        self.assertEqual(db.engine_digest(db.WEB_COMMIT), db.ENGINE_SHA256)
        for commit in (LEGACY_COMMIT, STORAGE_COMMIT): self.assertEqual(f.engine_digest(commit), f.ENGINE_SHA256)
        self.assertNotEqual(db.engine_digest(mobile.COMMIT), db.ENGINE_SHA256)
        self.assertNotEqual(f.engine_digest(mobile.COMMIT), f.ENGINE_SHA256)

    def test_new_release_does_not_grant_sql_upgrade_or_downgrade(self):
        for source, target in ((STORAGE_COMMIT, mobile.COMMIT), (mobile.COMMIT, STORAGE_COMMIT),
                               (mobile.COMMIT, mobile.COMMIT)):
            with self.subTest(source=source, target=target), self.assertRaises(catalog.UpgradeCatalogError):
                catalog.assess_transition(repository=catalog.REPOSITORY, source_commit=source, target_commit=target)

    def test_unknown_source_selectors_cannot_fall_back_to_old_sql(self):
        for value in (None, True, 2, 'main', 'a' * 40, mobile.COMMIT + '\n'):
            for function in (get_release, f.engine_digest, db.engine_digest):
                with self.subTest(function=function.__name__, value=value), self.assertRaises((ValueError, db.DatabaseStepError)):
                    function(value)

    def test_versioned_draft_survives_restart_without_implicit_downgrade(self):
        saved = self.draft(); self.assertEqual(saved['version'], 2)
        restored = app.ApplicationPlan(self.engine, Mock()); self.assertEqual(restored.read(), saved)
        payload = setup_payload(); payload['revision'] = saved['revision']
        self.assertEqual(restored.save(payload)['version'], 2)
        for secret in payload['credentials'].values(): self.assertNotIn(secret, (self.root / app.FILENAME).read_text())
        self.assertFalse(app.FreshProfile.from_draft(saved).root.exists())

    def test_legacy_draft_and_runtime_keep_their_exact_source_selection(self):
        saved = self.draft(1); layout = app.FreshProfile.from_draft(saved)
        self.assertEqual(layout.source_commit, STORAGE_COMMIT)
        http = layout.http(saved['configuration'])
        self.assertIsNone(http.spec.source_commit); self.assertEqual(http.source_commit, STORAGE_COMMIT)
        files = http._files(SimpleNamespace(pw_gid=991), Path('/usr/lib/php/20240924'))
        self.assertFalse(any(b'env[HESTIA_MOBILE_FOUNDATION_CONFIG]' in raw for raw in files.values()))

    def test_composition_binds_acquisition_deployment_database_and_finalization(self):
        saved = self.draft(); registry = app.composition(self.engine, Mock(), saved)
        sources = [spec.source.commit_sha for spec in registry.specs() if spec.source is not None]
        self.assertTrue(sources); self.assertEqual(set(sources), {mobile.COMMIT})
        database = registry.get(next(s.as_dict() for s in registry.specs() if s.name == 'web.database'))
        self.assertEqual(database.factory(planning=True).controller.commit, mobile.COMMIT)
        finalized = registry.get(next(s.as_dict() for s in registry.specs() if s.name == 'web.finalization'))
        self.assertEqual(finalized.factory(planning=True).controller.release.commit, mobile.COMMIT)

    def test_http_source_cannot_be_arbitrary_or_mixed_with_legacy_storage(self):
        saved = self.draft(); http = app.FreshProfile.from_draft(saved).http(saved['configuration'])
        self.assertEqual(http.source_commit, mobile.COMMIT)
        for changes in ({'source_commit': STORAGE_COMMIT}, {'source_commit': 'main'},
                        {'source_commit': True}, {'external_uploads': False}, {'maintenance_directory': None}):
            with self.subTest(changes=changes), self.assertRaises(h.HttpRuntimeError): replace(http.spec, **changes)

    def test_new_web_reads_only_the_private_owned_foundation_configuration(self):
        foundation, identity = self.foundation()
        http = foundation.web; files = http._files(SimpleNamespace(pw_gid=991), Path('/usr/lib/php/20240924'))
        directive = f'env[HESTIA_MOBILE_FOUNDATION_CONFIG] = {foundation.root}/main.json\n'.encode()
        self.assertEqual(sum(raw.count(directive) for raw in files.values()), 1)
        config = strict_json_loads(foundation.files(991)[foundation.root / 'main.json'])
        self.assertEqual(config['public_origin'], identity['public_origin']); self.assertEqual(config['gateway_port'], 9083)
        self.assertEqual(set(config), {'environment', 'gateway_keys', 'canonical_contexts', 'canonical_distribution', 'public_origin', 'gateway_port'})
        GatewayServiceProfile(foundation, identity, Path('/var/lib/hestia-test-keys'))
        bad = {**identity, 'public_origin': 'https://different.example.test'}
        with self.assertRaises(InstallerError): GatewayServiceProfile(foundation, bad, Path('/var/lib/hestia-test-keys'))

    def test_legacy_foundation_bytes_do_not_acquire_new_configuration_fields(self):
        foundation, _ = self.foundation(1)
        config = strict_json_loads(foundation.files(991)[foundation.root / 'main.json'])
        self.assertEqual(set(config), {'environment', 'gateway_keys', 'canonical_contexts', 'canonical_distribution'})
        self.assertIsNone(foundation.public_origin)

    def test_new_foundation_refuses_missing_or_noncanonical_origin(self):
        foundation, _ = self.foundation()
        for origin in (None, 'http://mobile.example.test', 'https://127.0.0.1', 'https://mobile.example.test:443',
                       'https://mobile.example.test/', 'https://MOBILE.example.test'):
            with self.subTest(origin=origin), self.assertRaises(InstallerError):
                FoundationRuntime(foundation.activation, foundation.identity, public_origin=origin)


if __name__ == '__main__': unittest.main()
