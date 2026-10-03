"""Explicit private FCM preparation before a Gateway service plan is sealed."""
from installer.fcm_credentials import FcmCredentials, MAX_BYTES
from installer.gateway_release import FCM_COMMIT
from installer.model import ErrorCode, exact_keys, now, require


class FcmPlan:
    def __init__(self, gateway, service):
        self.gateway, self.service, self.parent = gateway, service, gateway.parent
        self.store = FcmCredentials(gateway.root / 'fcm')
        self.availability = None

    def state(self):
        value = self.store.report()
        return {**(value or {'state': 'NOT_PLANNED', 'profile': None, 'confirmation': None, 'receipt': None,
            'historical_only': True, 'service_configured': False, 'google_authorization_verified': False,
            'phone_delivery_verified': False}), 'availability': self.availability}

    def binding(self, parent):
        self.gateway._parent(parent)
        profile = self.gateway.profile(); completed = self.gateway.engine().report()
        require(profile is not None, ErrorCode.NOT_PLANNED)
        require(profile['release']['commit'] == FCM_COMMIT, ErrorCode.INCOMPATIBLE_STATE)
        require(completed is not None and completed['state'] == 'DONE'
                and profile['web_plan_sha256'] == parent['plan_sha256'], ErrorCode.DEPENDENCY_BLOCKED)
        return completed['plan_sha256']

    def execute(self, action, payload):
        require(action in ('plan', 'check'))
        with self.parent.journal.locked(create=False) as locked:
            binding = self.binding(locked.read())
            if action == 'plan':
                exact_keys(payload, {'gateway_plan_sha256', 'project_id'})
                require(self.service.profile() is None, ErrorCode.PLAN_EXISTS)
                require(payload['gateway_plan_sha256'] == binding, ErrorCode.CONFIRMATION_REQUIRED)
                self.store.plan({'version': 1, **payload})
            else:
                exact_keys(payload, {'confirmation', 'confirm'})
                value = self.store.report(); require(value is not None, ErrorCode.NOT_PLANNED)
                require(payload['confirm'] is True and payload['confirmation'] == value['confirmation'], ErrorCode.CONFIRMATION_REQUIRED)
                require(value['profile']['gateway_plan_sha256'] == binding, ErrorCode.SOURCE_DRIFT)
                self.availability = None
                self.store.verify()
                self.availability = {'credential_valid': True, 'checked_at': now(), 'google_authorization_verified': False}
            return self.state()

    def import_file(self, confirmation, stream, length):
        require(type(length) is int and 0 < length <= MAX_BYTES, ErrorCode.SOURCE_LIMIT)
        with self.parent.journal.locked(create=False) as locked:
            binding = self.binding(locked.read())
            require(self.service.profile() is None, ErrorCode.PLAN_EXISTS)
            value = self.store.report(); require(value is not None, ErrorCode.NOT_PLANNED)
            require(confirmation == value['confirmation'] and value['profile']['gateway_plan_sha256'] == binding,
                    ErrorCode.CONFIRMATION_REQUIRED)
            raw = stream.read(length)
            require(type(raw) is bytes and len(raw) == length, ErrorCode.SOURCE_LIMIT)
            self.availability = None
            self.store.import_bytes(raw, confirmation, confirmed=True)
            return self.state()
