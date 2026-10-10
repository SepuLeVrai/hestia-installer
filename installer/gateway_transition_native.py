"""Bridge to qualified native motors, with one HTTP identity across all scopes."""
from copy import deepcopy
from dataclasses import replace

from installer import gateway_transition_stage as stage, gateway_transition_cutover as cutover
from installer import gateway_active_profile as publication, gateway_transition_resume as successor
from installer import gateway_resume_authority as authority, mobile_activation_admission as activation
from installer.application_activation import Activation
from installer.application_plan import FreshProfile
from installer.database_step import SqlAuthorityCredentials
from installer.foundation_runtime import FoundationRuntime
from installer.gateway_service_runtime import GatewayServiceRuntime
from installer.github_sources import AcquireOperation
from installer.mobile_activation_plan import digest, read_private
from installer.model import ErrorCode, SourceSpec, require
from installer.php_transport import WEB_REPOSITORY
from installer.session_cleaner import SessionCleaner


class NativeTransition:
    def __init__(self, controller, parent, profile, credentials):
        self.controller = controller
        self.profile = profile; self.lease_id = profile['lease_id']
        draft = controller.backup.application.read(); fresh = FreshProfile.from_draft(draft)
        self.http = fresh.http(draft['configuration'])
        # Do not run the full configuration audit before native data reclosure.
        self.scope = self.http._scope(cutover.hd.h._identity(self.http.spec.service_user))
        _, original = controller.transition.service.engine(parent)
        assessment = controller.transition.profile()['assessment']
        foundation = FoundationRuntime.for_gateway(Activation(self.http, original.foundation.activation.parent_sha256),
            original.profile.main, original.profile.identity)
        self.runtime = GatewayServiceRuntime.from_binding(foundation, assessment['source'])
        if controller.transition.cycle is not None:
            from installer.gateway_publication_chain import source
            source(self.runtime, controller.transition.cycle['predecessor']['publication_sha256'])
        require(self.runtime.profile.dev is None and self.runtime.profile.push is None, ErrorCode.UNSUPPORTED_MODULE)
        self.backups = controller.backup.backups(profile)
        self.worker = replace(fresh.runtime(), timeout_seconds=120)
        self.source = AcquireOperation(controller.parent.journal.path.parent, 'web',
            SourceSpec(WEB_REPOSITORY, fresh.source_commit, fresh.source_commit), None).path / 'tree'
        self.kwargs = {'source_package': controller.source_package,
            'target_package': controller.root / 'binary/package.zip', 'target_commit': assessment['target_release']['commit'],
            'direction': assessment['direction'], 'confirmed': True}
        self.payload, self.credentials = None, None
        if credentials is not None:
            self.payload = deepcopy(draft['configuration'])
            self.payload.update(mode='upgrade', administrator=None, assistant={'action': 'preserve'})
            self.payload['database']['mode'] = 'existing_local'
            self.payload['secrets'] = {'database_password': credentials['database_password'], 'admin_password': '', 'openai_api_key': ''}
            self.credentials = SqlAuthorityCredentials(credentials['authority_user'], credentials['authority_password'])

    def preflight(self):
        self.http._inspect_configuration()
        require(self.scope.observe() == {'state': 'MAINTENANCE_REQUIRED', 'instance': self.profile['instance'],
                                        'lease_id': self.lease_id}, ErrorCode.MANUAL_ACTION_REQUIRED)
        if 'public_source' in self.profile:
            from installer import gateway_public_generation as public
            original = public.mobile.MobileBootRuntime(self.profile['public_source']['mobile'])
            require(original.http.spec == self.http.spec, ErrorCode.INCOMPATIBLE_STATE)
            if self.controller.transition.cycle is None: public.source_configuration(original)
            else:
                from installer.gateway_public_selection import selected
                previous = selected(original.shared)
                require(previous.digest == self.controller.transition.cycle['predecessor']['generation_sha256'],
                        ErrorCode.SOURCE_DRIFT)
                public.enabled_sources(previous.readers()[0], previous.readers()[2])
            original.shared.web.certificate(minimum_lifetime=0)
            original.shared.mobile.verify(minimum_lifetime=0)
        else:
            with stage.fs._directory(self.http.spec.root.parent) as fd:
                stage.fs._absent(fd, 'boot'); stage.fs._absent(fd, 'public')
        self.runtime.stopped()
        # Exclude a previous transition or competing re-opening before approval.
        from installer.gateway_publication_chain import require_unpublished
        require_unpublished(self.runtime)
        require(not (self.backups / ('mobile-resume-' + self.lease_id)).exists(), ErrorCode.INCOMPATIBLE_STATE)

    def action(self, marker):
        with self.scope.recover(self.lease_id, confirmed=True) as lease:
            return 'resume' if cutover._optional(lease._directory, marker) is not None else 'apply'

    def fenced(self, operation):
        with cutover.hd.HttpDrain(self.http, cleaner=SessionCleaner(self.http)).recover(self.lease_id, confirmed=True) as barrier:
            with stage.g.recover(self.runtime, barrier, confirmed=True) as fence:
                snapshot = stage.b.recover_snapshot(fence, self.worker, self.backups, confirmed=True)
                return operation(snapshot)

    def execute(self, name):
        if name == 'binaries':
            recovery = (self.backups / ('gateway-transition-' + self.lease_id + '.json')).exists()
            return self.fenced(lambda snapshot: stage.prepare(snapshot, recovery=recovery, **self.kwargs).report())
        if name == 'cutover':
            if self.action(cutover.MARKER) == 'resume':
                return cutover.recover(self.runtime, self.backups, self.lease_id, action='resume', **self.kwargs)
            return self.fenced(lambda snapshot: cutover.apply(snapshot, **self.kwargs))
        if name == 'publication':
            return publication.publish(self.runtime, self.backups, self.lease_id,
                action=self.action(publication.MARKER), **self.kwargs)
        if name == 'public-transfer':
            from installer import gateway_public_generation as public
            generation = self.public_generation()
            with self.scope.recover(self.lease_id, confirmed=True) as lease:
                manager = public.systemd_manager(generation, lease)
                record = self.controller._read('public-systemd.json')
                if record is None:
                    if manager.root.exists():
                        with stage.fs._directory(manager.root) as fd:
                            plan = public.fragments._optional(fd, 'plan.json')
                        confirmation = public.sha(plan)
                        with manager.slot(confirmation): pass
                    else: confirmation = public.prepare_systemd(generation, lease, confirmed=True)['confirmation']
                    record = {'generation_sha256': generation.digest, 'confirmation': confirmation}
                    self.controller._write('public-systemd.json', record)
                require(set(record) == {'generation_sha256', 'confirmation'}
                    and record['generation_sha256'] == generation.digest, ErrorCode.SOURCE_DRIFT)
                return public.transfer_systemd(generation, lease, record['confirmation'], confirmed=True)
        if name == 'admission':
            return successor.prepare(self.runtime, self.backups, self.lease_id,
                action=self.action(authority.MARKER), **self.kwargs)
        if name == 'public-open':
            from installer.gateway_public_opening import Opening
            selected = authority.selected_for_admission(self.http)
            admitted = authority.Authority.load(selected, self.backups, self.lease_id)
            opening = Opening(admitted)
            if opening.generation._read('opening.json') is None: opening.prepare(confirmed=True)
            return opening.apply(confirmed=True)
        require(name == 'activation', ErrorCode.INVALID_DATA)
        selected = authority.selected_for_admission(self.http)
        admitted = authority.Authority.load(selected, self.backups, self.lease_id)
        return successor.execute(self.http, self.scope, self.lease_id, self.backups, self.worker, self.source,
            self.payload, self.credentials, confirmation=authority.sha(admitted.raw), action='resume',
            confirmed=True, allow_global_read_lock=True)

    def check(self):
        selected = authority.selected_for_admission(self.http)
        admitted = authority.Authority.load(selected, self.backups, self.lease_id)
        require(admitted.read('consumed.json') is not None, ErrorCode.DEPENDENCY_BLOCKED)
        if 'public_source' in self.profile:
            from installer.gateway_public_opening import Opening
            public = Opening(admitted).check()
            return {'state': 'MOBILE_SERVICES_RUNNING_LOCAL_WEB_AVAILABLE', 'public': public,
                    'local_web': public['local_web'],
                    'target_commit': selected.profile.selected_release['commit']}
        confirmation = digest(read_private(self.backups / ('mobile-resume-' + self.lease_id), 'plan.json'))
        with admitted.admitted(public_closed='public_source' not in self.profile):
            result = activation.continue_serving(self.http, self.backups, self.lease_id, confirmation,
                                                action='check', confirmed=True)
        require(result['state'] == 'MOBILE_SERVICES_RUNNING_LOCAL_WEB_AVAILABLE', ErrorCode.VALIDATION_FAILED)
        return {**result, 'target_commit': selected.profile.selected_release['commit']}

    def public_generation(self):
        from installer import gateway_public_generation as public
        require('public_source' in self.profile, ErrorCode.INCOMPATIBLE_STATE)
        selected = authority.selected_for_admission(self.http)
        source = self.profile['public_source']
        value = public.selection(source['mobile']['shared'], source['mobile'], self.kwargs['target_commit'],
            self.kwargs['direction'], authority.sha(selected._active_profile), self.lease_id,
            predecessor=source.get('predecessor'),
            source_binding=None if self.controller.transition.cycle is None else self.controller.transition.cycle['source_binding'])
        require(value['code'] == source['successor_code'], ErrorCode.SOURCE_DRIFT)
        previous = self.controller._read('public-generation.json')
        if previous is None: self.controller._write('public-generation.json', value)
        else: require(previous == value, ErrorCode.SOURCE_DRIFT)
        return public.Generation(value)
