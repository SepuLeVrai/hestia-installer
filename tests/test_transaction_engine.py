import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from installer.engine import TransactionEngine
from installer.model import ErrorCode, InstallerError, Receipt, ResourceSpec, StepSpec, initial_document, build_plan
from installer.operations import OperationRegistry, SecretVault
from installer.transaction import StateJournal
from transaction_fixture import FileOperation, InjectedCrash, ORIGINAL, TEST_ERROR, UnrecoverableOperation


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.journal = StateJournal(self.root / "private" / "state.json")

    def engine(self, *operations, **kwargs):
        return TransactionEngine(self.journal, OperationRegistry(tuple(operations)), **kwargs)

    def fault(self, phase, event="after", name="first"):
        def hook(current_name, current_phase, current_event):
            if (current_name, current_phase, current_event) == (name, phase, event):
                raise InjectedCrash()
        return hook

    def test_dry_run_does_not_create_a_journal_or_resource(self):
        op = FileOperation(self.root)
        plan = self.engine(op).dry_run(mode="fresh")
        self.assertEqual(plan["mode"], "fresh")
        self.assertEqual(plan["steps"][0]["resources"][0]["target"], str(op.target))
        self.assertFalse(self.journal.path.parent.exists())
        self.assertFalse(op.target.exists())
        self.assertEqual(op.calls("prepare"), 0)

    def test_plan_is_inspectable_and_idempotent_before_confirmation(self):
        op = FileOperation(self.root)
        engine = self.engine(op)
        first = engine.plan()
        self.assertEqual(first, engine.plan())
        self.assertEqual(first["state"], "PLANNED")
        self.assertIsNone(first["approved_plan_sha256"])
        self.assertFalse(op.target.exists())
        for key in ("action", "module", "boundary", "dependencies", "resources", "warnings", "manual_actions"):
            self.assertIn(key, first["plan"]["steps"][0])

    def test_apply_requires_the_exact_plan_confirmation(self):
        op = FileOperation(self.root)
        engine = self.engine(op)
        document = engine.plan()
        for bad in (None, "", "a" * 64, 0, True, "é" * 64, "x" * 100000):
            with self.subTest(bad_type=type(bad).__name__), self.assertRaises(InstallerError):
                engine.apply(bad)
        self.assertEqual(engine.report(), document)
        self.assertFalse(op.target.exists())

    def test_success_runs_all_phases_and_records_only_typed_evidence(self):
        op = FileOperation(self.root)
        engine = self.engine(op)
        document = engine.plan()
        result = engine.apply(document["plan_sha256"])
        self.assertEqual(result["state"], "DONE")
        self.assertEqual(op.calls_path.read_text().splitlines(), ["prepare", "apply", "validate", "commit"])
        self.assertEqual(result["resources_created"], ["first:payload"])
        self.assertEqual(result["commit_shas"]["first:source"], "a" * 40)
        self.assertEqual(json.loads(op.target.read_text())["phase"], "committed")

    def test_done_is_not_replayed_or_even_rewritten_by_refresh_apply_resume(self):
        op = FileOperation(self.root)
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        completed = engine.apply(digest)
        for _ in range(3):
            self.assertEqual(engine.apply(digest), completed)
            self.assertEqual(self.engine(FileOperation(self.root)).resume(digest), completed)
            self.assertEqual(engine.plan(), completed)
        self.assertEqual(op.calls("apply"), 1)
        self.assertEqual(op.calls("commit"), 1)

    def test_crash_at_every_forward_window_resumes_without_duplicate_effect(self):
        for phase, event in (("prepare", "checkpoint"), ("apply", "checkpoint"), ("apply", "after"),
                             ("validate", "after"), ("commit", "checkpoint"), ("commit", "after"), ("done", "checkpoint")):
            with self.subTest(phase=phase, event=event), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                journal = StateJournal(root / "private" / "state.json")
                op = FileOperation(root)
                engine = TransactionEngine(journal, OperationRegistry((op,)), fault_hook=self.fault(phase, event))
                digest = engine.plan()["plan_sha256"]
                with self.assertRaises(InjectedCrash):
                    engine.apply(digest)
                restored_op = FileOperation(root)
                restored = TransactionEngine(journal, OperationRegistry((restored_op,)))
                result = restored.resume(digest)
                self.assertEqual(result["state"], "DONE")
                self.assertEqual(op.calls("apply"), 1)
                self.assertEqual(op.calls("commit"), 1)

    def test_uncertain_effect_without_adapter_proof_requires_manual_action(self):
        op = UnrecoverableOperation(self.root)
        engine = self.engine(op, fault_hook=self.fault("apply"))
        digest = engine.plan()["plan_sha256"]
        with self.assertRaises(InjectedCrash):
            engine.apply(digest)
        result = self.engine(op).resume(digest)
        self.assertEqual(result["state"], "MANUAL_ACTION_REQUIRED")
        self.assertEqual(op.calls("apply"), 1)
        self.assertEqual(op.calls("commit"), 0)

    def test_failed_apply_is_not_automatically_retried_by_resume(self):
        op = FileOperation(self.root, fail="apply-after")
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        result = engine.apply(digest)
        self.assertEqual(result["state"], "FAILED")
        with self.assertRaises(InstallerError):
            engine.resume(digest)
        self.assertEqual(op.calls("apply"), 1)

    def test_retry_is_targeted_and_reconciles_partial_apply(self):
        first = FileOperation(self.root, fail="apply-after")
        second = FileOperation(self.root, "second", dependencies=("first",))
        engine = self.engine(first, second)
        digest = engine.plan()["plan_sha256"]
        engine.apply(digest)
        first.fail = None
        result = engine.retry("first", digest)
        self.assertEqual([step["state"] for step in result["steps"]], ["DONE", "PLANNED"])
        self.assertEqual(second.calls("apply"), 0)
        self.assertEqual(first.calls("apply"), 1)
        self.assertEqual(engine.resume(digest)["state"], "DONE")

    def test_commit_failure_after_effect_does_not_recommit_on_retry(self):
        op = FileOperation(self.root, fail="commit-after")
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        self.assertEqual(engine.apply(digest)["state"], "FAILED")
        op.fail = None
        self.assertEqual(engine.retry("first", digest)["state"], "DONE")
        self.assertEqual(op.calls("commit"), 1)

    def test_retry_of_done_step_is_forbidden(self):
        op = FileOperation(self.root)
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        engine.apply(digest)
        with self.assertRaises(InstallerError):
            engine.retry("first", digest)
        self.assertEqual(op.calls("apply"), 1)

    def test_boundary_rollback_preserves_other_committed_boundary(self):
        first = FileOperation(self.root, boundary="base")
        second = FileOperation(self.root, "second", boundary="extension", dependencies=("first",))
        third = FileOperation(self.root, "third", boundary="extension", dependencies=("second",))
        engine = self.engine(first, second, third)
        digest = engine.plan()["plan_sha256"]
        engine.apply(digest)
        result = engine.rollback("extension", digest)
        self.assertEqual([r["state"] for r in result["steps"]], ["DONE", "ROLLED_BACK", "ROLLED_BACK"])
        self.assertTrue(first.target.exists())
        self.assertFalse(second.target.exists())
        self.assertFalse(third.target.exists())
        before = result
        self.assertEqual(engine.rollback("extension", digest), before)
        with self.assertRaises(InstallerError):
            engine.resume(digest)
        engine.retry("second", digest)
        self.assertEqual(engine.retry("third", digest)["state"], "DONE")
        self.assertEqual(first.calls("apply"), 1)

    def test_rollback_blocks_committed_dependents_outside_boundary(self):
        first = FileOperation(self.root)
        second = FileOperation(self.root, "second", dependencies=("first",))
        engine = self.engine(first, second)
        digest = engine.plan()["plan_sha256"]
        done = engine.apply(digest)
        with self.assertRaises(InstallerError) as caught:
            engine.rollback("first", digest)
        self.assertEqual(caught.exception.code, ErrorCode.DEPENDENCY_BLOCKED)
        self.assertEqual(engine.report(), done)
        self.assertTrue(first.target.exists())

    def test_irreversible_effect_does_not_promise_rollback(self):
        op = FileOperation(self.root, reversible=False)
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        done = engine.apply(digest)
        with self.assertRaises(InstallerError) as caught:
            engine.rollback("first", digest)
        self.assertEqual(caught.exception.code, ErrorCode.ROLLBACK_UNSUPPORTED)
        self.assertEqual(engine.report(), done)
        self.assertTrue(op.target.exists())

    def test_real_existing_resource_backup_and_restore(self):
        op = FileOperation(self.root, preexisting=True)
        op.target.write_bytes(ORIGINAL)
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        result = engine.apply(digest)
        self.assertEqual(result["resources_preexisting"], ["first:payload"])
        self.assertEqual(result["resources_created"], [])
        self.assertEqual(result["backups"], [{"resource": "first:payload", "path": str(op.backup)}])
        self.assertEqual(op.backup.read_bytes(), ORIGINAL)
        self.assertEqual(engine.rollback("first", digest)["state"], "ROLLED_BACK")
        self.assertEqual(op.target.read_bytes(), ORIGINAL)
        self.assertEqual(engine.retry("first", digest)["state"], "DONE")
        self.assertEqual(op.backup.read_bytes(), ORIGINAL)

    def test_rollback_does_not_delete_a_foreign_modified_resource(self):
        op = FileOperation(self.root)
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        engine.apply(digest)
        op.target.write_text("belongs to somebody else")
        result = engine.rollback("first", digest)
        self.assertEqual(result["state"], "MANUAL_ACTION_REQUIRED")
        self.assertEqual(op.target.read_text(), "belongs to somebody else")

    def test_interrupted_rollback_resumes_rollback_not_installation(self):
        op = FileOperation(self.root)
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        engine.apply(digest)
        with self.assertRaises(InjectedCrash):
            self.engine(op, fault_hook=self.fault("rollback")).rollback("first", digest)
        result = self.engine(op).resume(digest)
        self.assertEqual(result["state"], "ROLLED_BACK")
        self.assertIsNone(result["rollback_boundary"])
        self.assertEqual(op.calls("rollback"), 1)
        self.assertEqual(op.calls("apply"), 1)

    def test_rollback_failure_after_undo_is_reconciled_without_double_undo(self):
        op = FileOperation(self.root)
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        engine.apply(digest)
        op.fail = "rollback-after"
        self.assertEqual(engine.rollback("first", digest)["state"], "MANUAL_ACTION_REQUIRED")
        op.fail = None
        self.assertEqual(engine.resume(digest)["state"], "ROLLED_BACK")
        self.assertEqual(op.calls("rollback"), 1)

    def test_adapter_contract_change_fails_before_any_replay(self):
        op = FileOperation(self.root)
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        other = FileOperation(self.root)
        other.spec = replace(other.spec, adapter_version=2)
        with self.assertRaises(InstallerError) as caught:
            self.engine(other).apply(digest)
        self.assertEqual(caught.exception.code, ErrorCode.INCOMPATIBLE_STATE)
        self.assertFalse(op.target.exists())

    def test_secrets_are_required_again_after_restart_but_not_for_done(self):
        op = FileOperation(self.root, needs_secret=True)
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        result = engine.apply(digest)
        self.assertEqual(result["last_error_redacted"], "SECRET_REQUIRED")
        self.assertFalse(op.target.exists())
        vault = SecretVault()
        vault.put("github", "not-a-real-credential-987654")
        resumed = self.engine(op, secrets=vault)
        self.assertEqual(resumed.retry("first", digest)["state"], "DONE")
        self.assertNotIn("not-a-real-credential-987654", self.journal.path.read_text())
        vault.clear()
        self.assertEqual(resumed.resume(digest)["state"], "DONE")
        self.assertEqual(op.calls("apply"), 1)

    def test_untrusted_exception_messages_never_enter_state_or_report(self):
        op = FileOperation(self.root, fail="apply-after")
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        result = engine.apply(digest)
        self.assertEqual(result["last_error_redacted"], "OPERATION_FAILED")
        self.assertNotIn(TEST_ERROR, self.journal.path.read_text())
        self.assertNotIn("EXAMPLE-DO-NOT-PERSIST", json.dumps(engine.report()))

    def test_known_secret_in_plan_is_rejected_before_state_directory_creation(self):
        op = FileOperation(self.root)
        secret = 'étrange " valeur-secret-456'
        op.spec = replace(op.spec, action="Action " + secret)
        vault = SecretVault()
        vault.put("github", secret)
        with self.assertRaises(InstallerError) as caught:
            self.engine(op, secrets=vault).plan()
        self.assertEqual(caught.exception.code, ErrorCode.SECRET_REJECTED)
        self.assertFalse(self.journal.path.parent.exists())

    def test_plaintext_cannot_be_smuggled_in_a_receipt(self):
        op = FileOperation(self.root)
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        op.apply = lambda ctx: Receipt(hashes_non_secret=(("source", "not-a-hash-secret"),))
        result = engine.apply(digest)
        self.assertEqual(result["state"], "FAILED")
        self.assertNotIn("not-a-hash-secret", self.journal.path.read_text())

    def test_parallel_apply_is_locked_but_status_reads_remain_available(self):
        entered, release = threading.Event(), threading.Event()
        op = FileOperation(self.root, entered=entered, release=release)
        engine = self.engine(op)
        digest = engine.plan()["plan_sha256"]
        results = []
        thread = threading.Thread(target=lambda: results.append(engine.apply(digest)))
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=3))
            self.assertEqual(engine.report()["state"], "RUNNING")
            with self.assertRaises(InstallerError) as caught:
                self.engine(FileOperation(self.root)).apply(digest)
            self.assertEqual(caught.exception.code, ErrorCode.BUSY)
        finally:
            release.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["state"], "DONE")
        self.assertEqual(op.calls("apply"), 1)

    def test_journal_write_failure_prevents_the_next_resource_mutation(self):
        op = FileOperation(self.root)
        engine = self.engine(op)
        document = engine.plan()
        with patch("installer.transaction.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(InstallerError):
                engine.apply(document["plan_sha256"])
        self.assertEqual(engine.report(), document)
        self.assertFalse(op.target.exists())
        self.assertEqual(op.calls("apply"), 0)

    def test_fresh_and_upgrade_execute_the_same_safe_contract(self):
        for mode in ("fresh", "upgrade"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                op = FileOperation(root, preexisting=mode == "upgrade")
                if mode == "upgrade":
                    op.target.write_bytes(ORIGINAL)
                engine = TransactionEngine(StateJournal(root / "private" / "state.json"), OperationRegistry((op,)))
                document = engine.plan(mode=mode)
                result = engine.apply(document["plan_sha256"])
                self.assertEqual(result["mode"], mode)
                self.assertEqual(result["state"], "DONE")
                engine.rollback("first", document["plan_sha256"])
                if mode == "upgrade":
                    self.assertEqual(op.target.read_bytes(), ORIGINAL)
                else:
                    self.assertFalse(op.target.exists())

    def test_real_process_exit_after_apply_and_commit_releases_lock_and_resumes(self):
        source = Path(__file__).resolve().parents[1]
        for phase in ("apply", "commit"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                op = FileOperation(root)
                journal = StateJournal(root / "private" / "state.json")
                engine = TransactionEngine(journal, OperationRegistry((op,)))
                digest = engine.plan()["plan_sha256"]
                code = '''import os,sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], sys.argv[1] + '/tests']
from installer.engine import TransactionEngine
from installer.operations import OperationRegistry
from installer.transaction import StateJournal
from transaction_fixture import FileOperation
root=Path(sys.argv[2])
def crash(name, phase, event):
    if phase == sys.argv[3] and event == 'after':
        os._exit(77)
e=TransactionEngine(StateJournal(root/'private'/'state.json'), OperationRegistry((FileOperation(root),)), fault_hook=crash)
e.apply(sys.argv[4])
'''
                run = subprocess.run([sys.executable, "-I", "-c", code, str(source), str(root), phase, digest],
                                     capture_output=True, timeout=10)
                self.assertEqual(run.returncode, 77, run.stderr.decode())
                self.assertEqual(engine.report()["steps"][0]["phase"], phase)
                result = engine.resume(digest)
                self.assertEqual(result["state"], "DONE")
                self.assertEqual(op.calls("apply"), 1)
                self.assertEqual(op.calls("commit"), 1)
                final = json.loads(op.target.read_text())
                self.assertEqual((final["content"], final["phase"]), ("installed", "committed"))


    def test_complete_boundary_rolls_back_in_reverse_dependency_order(self):
        first = FileOperation(self.root, "a", boundary="stack")
        second = FileOperation(self.root, "b", boundary="stack", dependencies=("a",))
        events = []
        engine = self.engine(first, second, fault_hook=lambda name, phase, event: events.append((name, phase, event)))
        plan = engine.plan()
        engine.apply(plan["plan_sha256"])
        result = engine.rollback("stack", plan["plan_sha256"])
        self.assertEqual(result["state"], "ROLLED_BACK")
        self.assertEqual([name for name, phase, event in events if phase == "rollback" and event == "before"], ["b", "a"])
        self.assertFalse(first.target.exists())
        self.assertFalse(second.target.exists())

    def test_commit_resume_revalidates_without_reapplying(self):
        operation = FileOperation(self.root)
        crashing = self.engine(operation, fault_hook=self.fault("commit", "checkpoint"))
        plan = crashing.plan()
        with self.assertRaises(InjectedCrash):
            crashing.apply(plan["plan_sha256"])
        self.assertEqual(operation.calls("validate"), 1)
        result = self.engine(operation).resume(plan["plan_sha256"])
        self.assertEqual(result["state"], "DONE")
        self.assertEqual(operation.calls("validate"), 2)
        self.assertEqual(operation.calls("apply"), 1)

    def test_subprocess_hard_exit_during_rollback_preserves_undo_direction(self):
        operation = FileOperation(self.root)
        engine = self.engine(operation)
        plan = engine.plan()
        engine.apply(plan["plan_sha256"])
        script = r"""
import os, sys
from pathlib import Path
sys.path.insert(0, 'tests')
from installer.engine import TransactionEngine
from installer.operations import OperationRegistry
from installer.transaction import StateJournal
from transaction_fixture import FileOperation
root = Path(sys.argv[1])
def hook(name, phase, event):
    if phase == 'rollback' and event == 'after':
        os._exit(78)
engine = TransactionEngine(StateJournal(root / 'private' / 'state.json'), OperationRegistry((FileOperation(root),)), fault_hook=hook)
engine.rollback('first', engine.report()['plan_sha256'])
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.root)], cwd=Path(__file__).resolve().parents[1], timeout=10)
        self.assertEqual(result.returncode, 78)
        self.assertFalse(operation.target.exists())
        result = self.engine(operation).resume(plan["plan_sha256"])
        self.assertEqual(result["state"], "ROLLED_BACK")
        self.assertEqual(operation.calls("rollback"), 1)
        self.assertEqual(operation.calls("apply"), 1)

if __name__ == "__main__":
    unittest.main()
