"""Durable preparation of a public Web/Mobile handoff, without system effects.

Only plan/check are implemented. The saved responsibility map is NOT execution
consent or an ownership receipt. The later lifecycle must enroll its exact code,
obtain separate consent and revalidate the live parent before any transfer.
"""
import re

from installer.frozen_public_tls import reference
from installer.gateway_release import sha
from installer.model import ErrorCode, canonical_bytes, exact_keys, require
from installer.package_plan import PackagePlan
from installer.public_tls_runtime import PublicTLS
from installer.shared_mobile_tls import SharedMobileTLS

POLICY = 'SHARED_PUBLIC_PREPARATION_V1'
PARENTS = {'web', 'public', 'public_journal', 'gateway', 'gateway_journal', 'gateway_identities'}
STAGES = ('enroll', 'http-handoff', 'mobile-certificate', 'mobile-dry-run',
          'https-handoff', 'renewal-handoff', 'verify')


def digest(value): return sha(canonical_bytes(value))


def candidate(web, identity, networks, parents):
    exact_keys(parents, PARENTS)
    require(all(type(v) is str and re.fullmatch('[a-f0-9]{64}', v) for v in parents.values()))
    require(type(networks) is list)
    shared = SharedMobileTLS(web, identity, tuple(networks))
    public = PublicTLS(shared.web.value)
    units = public.units()
    # Each old unit is accounted for, including the timer and Web certificate.
    # Keeping Web renewal under its old worker after the transfer would cause
    # that worker to reject the composed configuration. It must move as a unit.
    roles = {}
    for role in ('http', 'https', 'renew', 'timer'):
        name = public.unit(role)
        roles[role] = {'unit': name, 'fragment_sha256': sha(units[name]),
                       'current_owner': 'frozen-public', 'intended_owner': 'shared-public'}
    return {'version': 1, 'policy': POLICY, 'instance': public.layout.instance,
        'parents': parents, 'web_profile': shared.web.value, 'gateway_identity': identity,
        'client_networks': networks, 'composition': shared.manifest(),
        'responsibilities': roles,
        'enable_links': {str(path): target for path, target in public.links().items()},
        'renewal': {'web': {'issue': public.certbot(), 'dry_run': public.certbot(renew=True, dry_run=True),
                            'renew': public.certbot(renew=True)},
                    'mobile': shared.manifest()['commands']},
        'stages': list(STAGES), 'execution_authorized': False, 'ownership_transferred': False}


class SharedPublicPlan:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, public, gateway):
        self.public, self.gateway, self.parent = public, gateway, public.parent
        self.root = self.parent.journal.path.parent / 'shared-public'

    def profile(self):
        value = self._read('profile.json')
        if value is not None:
            exact_keys(value, {'version', 'policy', 'instance', 'parents', 'web_profile', 'gateway_identity',
                'client_networks', 'composition', 'responsibilities', 'enable_links', 'renewal', 'stages',
                'execution_authorized', 'ownership_transferred'})
            expected = candidate(value['web_profile'], value['gateway_identity'], value['client_networks'], value['parents'])
            # Canonical comparison rejects bool/int aliases as well as added keys.
            require(canonical_bytes(value) == canonical_bytes(expected), ErrorCode.INVALID_STATE)
        return value

    def state(self):
        value = self.profile()
        return {'state': 'NOT_PLANNED' if value is None else 'PREPARED_HISTORICAL',
                'plan': value, 'plan_sha256': None if value is None else digest(value),
                'historical_only': True, 'current_admission': False, 'execution_authorized': False,
                'ownership_transferred': False, 'public_tls_verified': False, 'phase6_complete': False}

    def binding(self, parent, networks, *, observe):
        document, runtime = reference(self.public, parent, observe=observe)
        gateway = self.gateway.engine().report()
        require(gateway is not None and gateway['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        selected = self.gateway.profile()
        require(selected is not None and selected['web_plan_sha256'] == parent['plan_sha256'],
                ErrorCode.INCOMPATIBLE_STATE)
        identities = self.gateway.identities.report()
        require(identities is not None and identities['profile'] == selected['identity']
                and identities['receipt'] is not None, ErrorCode.DEPENDENCY_BLOCKED)
        parents = {'web': parent['plan_sha256'], 'public': document['plan_sha256'],
                   'public_journal': digest(document), 'gateway': gateway['plan_sha256'],
                   'gateway_journal': digest(gateway), 'gateway_identities': digest(identities['receipt'])}
        return candidate(runtime.value, selected['identity'], networks, parents)

    def execute(self, action, payload):
        require(action in ('plan', 'check'))
        exact_keys(payload, {'public_sha256', 'gateway_sha256', 'client_networks'} if action == 'plan' else {'plan_sha256'})
        with self.parent.journal.locked(create=False) as locked:
            parent = locked.read()
            require(parent is not None and parent['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
            previous = self.profile()
            if action == 'check': require(previous is not None, ErrorCode.NOT_PLANNED)
            networks = payload['client_networks'] if action == 'plan' else previous['client_networks']
            # Check confirmation before expensive live observation; neither path
            # calls apply/recover/start/reload/Certbot or modifies a parent file.
            value = self.binding(parent, networks, observe=False)
            if action == 'plan':
                require(payload['public_sha256'] == value['parents']['public']
                        and payload['gateway_sha256'] == value['parents']['gateway'], ErrorCode.CONFIRMATION_REQUIRED)
                require(previous is None or previous == value, ErrorCode.PLAN_EXISTS)
            else:
                require(payload['plan_sha256'] == digest(previous), ErrorCode.CONFIRMATION_REQUIRED)
                require(previous == value, ErrorCode.SOURCE_DRIFT)
            require(self.binding(parent, networks, observe=True) == value, ErrorCode.SOURCE_DRIFT)
            require(locked.read() == parent, ErrorCode.SOURCE_DRIFT)
            self.parent.secrets.reject_in(value)
            if previous is None: self._write('profile.json', value)
            return self.state()
