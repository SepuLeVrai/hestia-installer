"""Selected leader identity joined to owned PIDFD/invocation observations.

The resulting effective-identity signal widens review, never drain authority.
"""
from dataclasses import dataclass, field, replace, asdict
from installer import process_identity as p, systemd_invocation_relations as x

v, t, d, r, require = x.v, x.t, x.d, x.r, x.require


@dataclass(frozen=True)
class ProcessInvocationBinding(v.InvocationBinding):
    identity: p.ProcessIdentity = field(repr=False)


@dataclass(frozen=True)
class ProcessSample(x.RelationSample):
    def report(self):
        data = self.private_manifest(); report = super().report()
        report.update(state='SELECTED_INVOCATION_LEADER_IDENTITIES_OBSERVED',
            selected_effective_leaders_observed=True, process_census_authenticated=False,
            effective_identities_observed=False, proc_bytes=data['procfs']['bytes'],
            proc_reads=data['procfs']['reads'], process_observations=data['procfs']['observations'])
        return report


class SystemdProcessIdentity(x.SystemdInvocationRelations):
    def __repr__(self): return '<SystemdProcessIdentity private selected leader observations>'

    def _budget(self, count):
        budget = super()._budget(count); budget.proc = p.ProcBudget(budget)
        return budget

    def _binding(self, hint, name, fd, owner, budget):
        first = p.observe(hint.pid, fd, budget.proc)
        binding = super()._binding(hint, name, fd, owner, budget)
        require(first == p.observe(hint.pid, fd, budget.proc), 'PROCESS_IDENTITY_CHANGED')
        return ProcessInvocationBinding(**asdict(binding), identity=first)

    def _check_final(self, hints, owned, details, budget):
        for hint, fd, detail in zip(hints, owned, details):
            require(detail.binding.identity == p.observe(hint.pid, fd, budget.proc), 'PROCESS_IDENTITY_CHANGED')

    def _pass(self, hints, units, owned, owner, budget):
        details = super()._pass(hints, units, owned, owner, budget)
        self._check_final(hints, owned, details, budget)
        return details

    def _sample(self, scan, index, details, context, budget):
        sample = super()._sample(scan, index, details, context, budget)
        identities = {z.binding.object_path: z.binding.identity for z in details}
        units = []
        for unit in sample.facts().units:
            identity = identities[unit.object_path]
            process = r.ProcessBinding(identity.pid, identity.start_ticks, identity.namespaces[0],
                identity.cgroup, identity.digest())
            fact = r.IdentityFact('effective', 'observed', identity.uids[1], identity.gids[1],
                identity.groups, None, process, d.l._sha(d._json(asdict(next(
                    z.binding for z in details if z.binding.object_path == unit.object_path)))))
            units.append(replace(unit, identities=(fact,)))
        facts = replace(sample.facts(), units=tuple(units))
        selection = self._selector.inspect(sample.scan(), facts, now=scan.finished_at)
        data = sample.private_manifest()
        data['selection'] = selection.private_manifest()
        data['transport']['policy'] = 'PIDFD_INVOCATION_SELECTED_LEADER_V1'
        # Final post-envelope observations occur later, and are accounted for
        # separately as checks, not fabricated in these captured IO counters.
        data['procfs'] = {'bytes': budget.proc.bytes, 'reads': budget.proc.reads,
                         'observations': budget.proc.observations, 'counters_before_final_check': True}
        data['limitations'] = ['NOT_A_PROCESS_CENSUS', 'LEADER_ONLY_NOT_THREADS_OR_DESCENDANTS',
            'PID_HISTORY_BEFORE_OPEN_UNKNOWN', 'CONFIGURED_IDENTITIES_UNKNOWN', 'UNSELECTED_UNITS_UNKNOWN',
            'RELATIONS_NOT_EXHAUSTIVE', 'NOT_ATOMIC_ABA_NOT_EXCLUDED', 'PROCFS_TRUSTS_PRIVILEGED_OBSERVER',
            'MAPPING_MAY_SELECT_ONE_OWNER', 'NO_WRITER_OR_DRAIN_AUTHORITY']
        return ProcessSample(sample.scan(), sample.index(), facts, selection, d._json(data))
