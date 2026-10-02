"""Native maintenance-closed bridge from a verified backup to activation readiness.

Every stage reacquires the original real lease. Native readers and fresh bounded
SQL admissions remain authoritative; cockpit receipts only select the next stage.
"""
from contextlib import ExitStack, contextmanager
from pathlib import Path
import re

from installer import mobile_reopen_files as r, mobile_external_admission as external
from installer import mobile_data_admission as data, mobile_resume_plan as resume
from installer import mobile_blocker_admission as blockers
from installer import gateway_state_backup as backup, gateway_state_fence as fence
from installer import foundation_drain, gateway_service_drain
from installer.session_cleaner import SessionCleaner
from installer.mobile_activation_plan import digest, read_private
from installer.model import ErrorCode, require

STAGES = ('gateway', 'files', 'external', 'data', 'resume', 'blockers')


class NativePreparation:
    def __init__(self, http, scope, lease_id, backups, worker, source, payload, authority):
        require(type(http) is r.hd.h.HttpRuntime and type(scope) is r.hd.m.MaintenanceScope
                and type(lease_id) is str and re.fullmatch('[a-f0-9]{32}', lease_id)
                and isinstance(backups, Path) and backups.is_absolute()
                and type(worker) is external.p.PhpRuntime and isinstance(source, Path)
                and type(authority) is external.a.c.d.SqlAuthorityCredentials, ErrorCode.INVALID_DATA)
        self.http, self.scope, self.lease_id, self.backups = http, scope, lease_id, backups
        self.worker, self.source, self.payload, self.authority = worker, source, payload, authority

    def _barrier(self, lease):
        raw = r.f._read(lease._directory, 'http-drain-' + self.lease_id + '.attempt', self.scope.web_gid)
        return r.hd.HttpDrainLease(r.hd.HttpDrain(self.http, cleaner=SessionCleaner(self.http)), lease, raw)

    def _gateway(self): return gateway_service_drain.attached(self.http, foundation_drain.attached(self.http))

    @contextmanager
    def _files(self):
        with ExitStack() as stack:
            lease = stack.enter_context(self.scope.recover(self.lease_id, confirmed=True))
            access = stack.enter_context(r.da.recover(self.http, lease, confirmed=True))
            barrier = self._barrier(lease); barrier.assert_held()
            reservations = stack.enter_context(r.ef.ExternalFence(lease,
                r.files._read(lease._directory, r.ef.MARKER, r.ef.MAX_JOURNAL)))
            conf = stack.enter_context(r.fs._directory(self.scope.directory.parent))
            configuration = stack.enter_context(r.cf.admission.acquire(
                conf, self.http.spec.webroot, self.scope.web_gid, external=reservations))
            yield r.ReopenFilesPlan(barrier, access, configuration, reservations, self._gateway(), self.backups)

    def _present(self, prefix, name='plan.json'):
        return read_private(self.backups / (prefix + self.lease_id), name) is not None

    def _gateway_release(self):
        with self.scope.recover(self.lease_id, confirmed=True) as lease:
            barrier = self._barrier(lease); barrier.assert_held(); gateway = self._gateway()
            if any(r._optional(lease._directory, name, fence.MAX_JOURNAL * 2) is not None
                   for name in (r.gateway.RELEASE, r.gateway.RELEASED)):
                return r.gateway.recover(gateway, barrier, self.backups, confirmed=True).report()
            with fence.recover(gateway, barrier, confirmed=True) as held:
                snapshot = backup.recover_snapshot(held, self.worker, self.backups, confirmed=True)
                return r.gateway.release(snapshot, confirmed=True).report()

    def _file_release(self):
        with self._files() as control:
            document = control.plan(confirmed=True)
            action = 'check' if document['state'] == 'DONE' else 'apply' if document['approved_plan_sha256'] is None else 'resume'
            result = control.execute(action, document['plan_sha256'], confirmed=True)
            require(result['transaction']['state'] == 'DONE', ErrorCode.MANUAL_ACTION_REQUIRED)
            return result

    def _external_release(self):
        if not self._present('external-release-'):
            with self._files() as control: external.e.begin(control, confirmed=True)
        with ExitStack() as stack:
            lease = stack.enter_context(self.scope.recover(self.lease_id, confirmed=True))
            access = stack.enter_context(r.da.recover(self.http, lease, confirmed=True))
            barrier = self._barrier(lease); barrier.assert_held(); plan = external.e.recover(lease, self.backups, confirmed=True)
            action = 'check' if plan._read('released.json') is not None else 'resume' if plan._read('intent.json') is not None else 'apply'
            with external.acquire(plan, barrier, access, self._gateway(), self.worker, self.source, self.payload,
                    self.authority, plan.plan_sha256, action=action, confirmed=True, allow_global_read_lock=True) as window:
                result = window.report()
            return result

    def _data_plan(self, lease):
        if not self._present('data-release-'):
            with r.da.recover(self.http, lease, confirmed=True) as access:
                parent = external.e.recover(lease, self.backups, confirmed=True)
                data.d.begin(parent, access, confirmed=True)
        return data.d.recover(self.http, lease, self.backups, confirmed=True)

    def _data_release(self):
        with self.scope.recover(self.lease_id, confirmed=True) as lease:
            plan = self._data_plan(lease); barrier = self._barrier(lease)
            # Do not attach stopped runtimes before the native partial-chmod reclosure.
            action = 'check' if plan._read('released.json') is not None else 'resume' if plan._read('intent.json') is not None else 'apply'
            with data.acquire(plan, barrier, None, self.worker, self.source, self.payload, self.authority,
                    plan.plan_sha256, action=action, confirmed=True, allow_global_read_lock=True) as window:
                result = window.report()
            return result

    def _resume_plan(self):
        with self.scope.recover(self.lease_id, confirmed=True) as lease:
            plan = data.d.recover(self.http, lease, self.backups, confirmed=True)
            with data.acquire(plan, self._barrier(lease), None, self.worker, self.source, self.payload, self.authority,
                    plan.plan_sha256, action='check', confirmed=True, allow_global_read_lock=True) as window:
                if self._present('mobile-resume-'):
                    prepared = resume.recover(window, confirmed=True)
                    result = prepared.prepare(window, prepared.plan_sha256, confirmed=True)
                else:
                    prepared = resume.begin(window, confirmed=True); result = prepared._report()
            return result

    def _blocker_handoff(self):
        raw = external.e._read_path(self.backups / ('mobile-resume-' + self.lease_id), 'plan.json', resume.MAX_PLAN)
        confirmation = r.f._sha(raw)
        with self.scope.recover(self.lease_id, confirmed=True) as lease:
            state = blockers.s.BlockerState(self.http, lease, self.backups, confirmation)
            value = state.state(); action = 'check' if value['done'] else 'resume' if value['intent'] else 'apply'
            with blockers.acquire(state, self._barrier(lease), None, self.worker, self.source, self.payload, self.authority,
                    confirmation, action=action, confirmed=True, allow_global_read_lock=True) as window:
                result = window.report()
            return result

    def execute(self, stage):
        require(stage in STAGES, ErrorCode.INVALID_DATA)
        operations = dict(zip(STAGES, (self._gateway_release, self._file_release, self._external_release,
                                      self._data_release, self._resume_plan, self._blocker_handoff)))
        result = operations[stage]()
        require(result['maintenance_released' if stage == 'resume' else 'activity_resumed'] is False
                and result['services_started'] is False,
                ErrorCode.INVALID_STATE)
        return {'stage': stage, 'instance': self.scope.instance, 'lease_id': self.lease_id,
                'result_sha256': digest(result), 'services_started': False, 'activity_resumed': False}
