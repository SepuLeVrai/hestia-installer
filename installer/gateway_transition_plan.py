"""A frozen, metadata-only transition plan; no effect endpoint in this sub-lot."""
from installer.gateway_service_profile import GatewayServiceProfile
from installer.gateway_transition import assess
from installer.gateway_release import sha
from installer.model import ErrorCode, canonical_bytes, exact_keys, require
from installer.package_plan import PackagePlan


class GatewayTransitionPlan:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, service, *, root=None, cycle=None):
        self.service, self.parent = service, service.parent
        self.root = service.gateway.root / 'transition' if root is None else root
        self.cycle = cycle

    def profile(self):
        value = self._read('profile.json')
        if value is not None:
            exact_keys(value, {'version', 'source_plan_sha256', 'source_profile_sha256', 'assessment'} |
                       ({'source_generation'} if self.cycle is not None else set()))
            if self.cycle is not None:
                require(value['source_generation'] == self.cycle, ErrorCode.INCOMPATIBLE_STATE)
            require(type(value['version']) is int and value['version'] == 1, ErrorCode.INVALID_STATE)
            for key in ('source_plan_sha256', 'source_profile_sha256'):
                require(type(value[key]) is str and len(value[key]) == 64
                        and all(c in '0123456789abcdef' for c in value[key]), ErrorCode.INVALID_STATE)
            require(type(value['assessment']) is dict, ErrorCode.INVALID_STATE)
        return value

    def current(self, parent, target_commit, direction):
        engine, runtime = self.service.engine(parent)
        document = engine.report()
        require(document is not None and document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        profile = self.service.profile()
        source = GatewayServiceProfile.from_binding(runtime.foundation,
            profile['binding'] if self.cycle is None else self.cycle['source_binding'])
        return {'version': 1, 'source_plan_sha256': document['plan_sha256'],
                **({} if self.cycle is None else {'source_generation': self.cycle}),
                'source_profile_sha256': sha(canonical_bytes(profile)),
                'assessment': assess(source, target_commit=target_commit, direction=direction).report()}

    def state(self):
        # Only saved metadata. An old compatible plan is not a live inspection.
        value = self.profile()
        return {'state': 'NOT_PLANNED' if value is None else 'COMPATIBILITY_ONLY',
                'profile': value, 'confirmation': None if value is None else sha(canonical_bytes(value)),
                'source_commit': None if self.cycle is None else self.cycle['source_binding']['release']['commit'],
                'historical_only': True, 'apply_allowed': False, 'phase6_complete': False}

    def execute(self, action, payload):
        require(action == 'plan', ErrorCode.DEPENDENCY_BLOCKED)
        exact_keys(payload, {'source_plan_sha256', 'target_commit', 'direction'})
        with self.parent.journal.locked(create=False) as locked:
            value = self.current(locked.read(), payload['target_commit'], payload['direction'])
            require(payload['source_plan_sha256'] == value['source_plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
            saved = self.profile()
            if saved is None:
                self.parent.secrets.reject_in(value)
                self._write('profile.json', value)
            else: require(saved == value, ErrorCode.PLAN_EXISTS)
            return self.state()
