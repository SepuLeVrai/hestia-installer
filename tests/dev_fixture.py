"""Public DEV target descriptors and real temporary P-256 keys for contracts."""
from copy import deepcopy
from installer import application_plan as app
from installer.application_activation import Activation
from installer.dev_target import DevTarget, digest
from installer.foundation_runtime import FoundationRuntime
from installer.gateway_identity import GatewayIdentityStore
from installer.engine import TransactionEngine
from installer.operations import default_registry
from installer.transaction import StateJournal
from test_application_plan import setup_payload


def target_fixture(root):
    drafts = []
    for environment in ('main', 'dev'):
        engine = TransactionEngine(StateJournal(root / environment / 'state.json'), default_registry())
        payload = setup_payload(); payload['profile'] = 'fresh-mobile-staged-v2'
        if environment == 'dev':
            payload['configuration']['hostname'] = 'dev.example.test'
            payload['configuration']['database'].update(name='hestia_dev', user='hestia_dev')
        drafts.append(app.ApplicationPlan(engine, None).save(payload))
    main, dev = drafts
    main_layout, dev_layout = (app.FreshProfile.from_draft(d) for d in drafts)
    http = dev_layout.http(dev['configuration'])
    descriptor = {'version': 2, 'http': {k: str(getattr(http.spec, k)) if k in
        ('root', 'webroot', 'maintenance_directory') else getattr(http.spec, k) for k in
        ('instance', 'root', 'webroot', 'service_user', 'hostname', 'port', 'maintenance_directory')},
        'worker': {'user': dev_layout.worker.user, 'run_root': str(dev_layout.root / 'run'),
                   'state_root': str(dev_layout.root / 'attempts')}}
    descriptor['http']['port'] = 9084
    config = deepcopy(dev['configuration'])
    config.update(mode='upgrade', administrator=None, assistant={'action': 'preserve', 'desired_enabled': None})
    config['database']['mode'] = 'existing_local'
    value = {'version': 1, 'descriptor': descriptor, 'configuration': config,
        'preparation_sha256': 'b' * 64, 'main_configuration_sha256': digest(main['configuration']),
        'debug_subjects': ['11111111-1111-4111-8111-111111111111']}
    identity = {'version': 1, 'instance': 'c' * 32,
                'public_origin': 'https://mobile.example.test', 'dev_enabled': True}
    store = GatewayIdentityStore(root / 'identities'); identities = store.prepare(identity)['identities']
    native = FoundationRuntime(Activation(main_layout.http(main['configuration']), 'a' * 64),
        identities['main'], public_origin=identity['public_origin'], dev={'target': value, 'identity': identities['dev']})
    return main, DevTarget(value), identity, store, native
