"""Native observations and explicit starts for the five already-bound units.

No drain, stop, restart, enable, unit rewrite or implicit retry exists here.
"""
from dataclasses import asdict
from pathlib import Path
import re
import time

from installer import mobile_blocker_admission as b
from installer import application_activation as a, systemd_observations as o
from installer.model import canonical_bytes, strict_json_loads

h, f, fs, drain = a.h, a.h.f, a.h.fs, a.drain
ROLES = ('php', 'apache', 'foundation', 'gateway', 'timer')
PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'InvocationID',
              'ActiveEnterTimestampMonotonic', 'Job')


class ActivationError(RuntimeError): pass


def require(ok, code='MOBILE_ACTIVATION_CHANGED'):
    if not ok: raise ActivationError(code)


def runtime_profile(http, account):
    value = asdict(http.spec)
    for key in ('root', 'webroot', 'maintenance_directory'):
        value[key] = str(value[key]) if value[key] is not None else None
    result = {'spec': value, 'uid': account.pw_uid, 'gid': account.pw_gid, 'user': account.pw_name}
    from installer.gateway_resume_authority import activation_binding
    successor = activation_binding(http)
    if successor is not None: result['gateway_successor'] = successor
    return result


def invocation(unit):
    require(type(unit) is str and re.fullmatch(r'hestia-[a-f0-9]{32}-(?:php|apache|foundation|gateway|session-cleaner)\.(?:service|timer)', unit))
    h.p._safe_path(Path('/usr/bin/systemctl'), directory=False, system=True)
    raw = o._capture(['/usr/bin/systemctl', '--system', '--no-pager', '--no-ask-password',
        'show', '--property=' + ','.join(PROPERTIES), '--', unit])
    value = {}
    for row in raw.decode('ascii').splitlines():
        key, sep, text = row.partition('=')
        require(sep == '=' and key in PROPERTIES and key not in value and len(text) <= 256)
        value[key] = text
    require(set(value) == set(PROPERTIES) and value['Id'] == unit and value['LoadState'] == 'loaded'
        and value['Job'] == '' and re.fullmatch('[0-9]{1,20}', value['ActiveEnterTimestampMonotonic'])
        and (value['InvocationID'] == '' or re.fullmatch('[a-f0-9]{32}', value['InvocationID'])),
        'MOBILE_ACTIVATION_INVOCATION_REJECTED')
    return value


class NativeRuntime:
    def __init__(self, http, original_profile, confirmation):
        require(type(http) is h.HttpRuntime and type(original_profile) is bytes)
        self.http, self.original_profile = http, original_profile
        self.profile = strict_json_loads(original_profile)
        self.order = b.s.p._services(self.profile, http.spec.instance)
        self.activation = a.Activation(http, confirmation)
        self.foundation = b.m.fd.attached(http)
        self.gateway = b.m.gd.attached(http, self.foundation)
        require(self.foundation is not None and self.gateway is not None)
        self.scope = self.configuration()

    def configuration(self):
        account, extension, plan, _ = self.http._inspect_configuration()
        scope = self.http._scope(account); generated = self.http._files(account, extension)
        owner, gate, collector, cleaner_plan = self.activation.cleaner._inspect_configuration()
        require((owner.pw_uid, owner.pw_gid, gate.instance, gate.directory) ==
            (account.pw_uid, account.pw_gid, scope.instance, scope.directory))
        rows = [{'role': role, 'fragment_sha256': f._sha(generated[drain.UNIT_ROOT/self.http.unit(role)])}
                for role in ('apache', 'php')]
        rows.append({'role': 'session-cleaner', 'fragment_sha256': f._sha(collector[drain.UNIT_ROOT/self.activation.cleaner.unit])})
        self.foundation.inspect(); self.gateway.inspect()
        current = {'version': 1, 'instance': scope.instance, 'maintenance': str(scope.directory),
            'policy': 'PROVISIONED_HTTP_AND_CLEANER_STOP_ONLY_V1', 'runtime_plan_sha256': f._sha(plan),
            'uid': account.pw_uid, 'gid': account.pw_gid, 'units': rows,
            'cleaner_plan_sha256': f._sha(cleaner_plan),
            'timer_sha256': f._sha(collector[drain.UNIT_ROOT/self.activation.cleaner.timer]),
            'foundation': b.m.fd.binding(self.foundation), 'gateway_service': b.m.gd.binding(self.gateway)}
        require(h.p._json(current) == self.original_profile, 'MOBILE_ACTIVATION_PROFILE_CHANGED')
        for row in rows:
            drain.audit_unit(scope, drain.UnitBinding(row['role'], row['fragment_sha256']),
                running_collector=row['role']=='session-cleaner')
        self.activation.cleaner._timer_state(stopped=False)
        allowed = tuple(self.http.unit(x) for x in ('php','apache')) + (self.foundation.unit,self.activation.cleaner.unit)
        b.r.hd._identity_census(account.pw_uid, account.pw_gid, allowed)
        gateway = self.gateway.account()
        b.r.hd._identity_census(gateway.pw_uid, gateway.pw_gid, (self.gateway.unit,))
        self.account = account
        return scope

    def unit(self, role):
        require(role in ROLES)
        return self.order[ROLES.index(role)]['unit']

    def observed(self, role, *, active):
        unit = self.unit(role); before = invocation(unit)
        if active:
            if role in ('php','apache','timer'): require(self.activation.running(role), 'MOBILE_ACTIVATION_NOT_RUNNING')
            else: getattr(self, role).owned(serving=False)
            require(before['ActiveState']=='active' and before['SubState'] in
                (('waiting','running','elapsed') if role=='timer' else ('running',))
                and before['InvocationID'] not in ('','0'*32), 'MOBILE_ACTIVATION_INVOCATION_REJECTED')
        else:
            require(before['ActiveState']=='inactive' and before['SubState']=='dead', 'MOBILE_ACTIVATION_NOT_STOPPED')
            if role in ('foundation','gateway'): getattr(self, role).stopped()
            elif role=='timer': self.activation.cleaner._timer_state(stopped=True)
            else:
                row = self.order[ROLES.index(role)]
                drain.audit_unit(self.scope, drain.UnitBinding(role,row['fragment_sha256']), stopped=True)
        after = invocation(unit)
        require(before == after, 'MOBILE_ACTIVATION_INVOCATION_CHANGED')
        return {'unit': unit, 'invocation_id': after['InvocationID'],
            'active_enter_monotonic_us': int(after['ActiveEnterTimestampMonotonic'])}

    def start(self, role):
        # No retries here: a lost result must be reconciled against a new owned
        # invocation by the durable coordinator, or refused as ambiguous.
        h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'start', '--', self.unit(role)])

    def running_after(self, role):
        deadline = time.monotonic() + 10
        while True:
            try: return self.observed(role, active=True)
            except Exception:
                if time.monotonic() >= deadline: raise ActivationError('MOBILE_ACTIVATION_NOT_RUNNING') from None
                time.sleep(.1)
