import ast
import copy
import io
import json
import pickle
import tempfile
import threading
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from installer import __version__
from installer.cli import main, build_parser
from installer.engine import TransactionEngine
from installer.model import (
    InstallerError, Receipt, ResourceSpec, StepSpec, build_plan, initial_document,
    plan_digest, validate_document, validate_receipt,
)
from installer.operations import OperationRegistry, SecretVault, default_registry
from installer.preflight import CheckResult
from installer.security import BootstrapToken, SessionStore
from installer.transaction import StateJournal
from transaction_fixture import FileOperation


class ContractTests(unittest.TestCase):
    def spec(self, **kwargs):
        return replace(StepSpec("step", "example.install", "core", "boundary", "Contrôle lisible"), **kwargs)

    def test_empty_huge_and_wrongly_typed_plans_fail_closed(self):
        with self.assertRaises(InstallerError):
            build_plan([])
        for fields in ({"name": ""}, {"name": "a" * 65}, {"name": True},
                       {"action": "x" * 513}, {"action": ""}, {"action": "test\n"},
                       {"adapter_version": True}, {"adapter_version": -1}, {"adapter_version": 1.1},
                       {"warnings": ("x" * 1025,)}, {"requires_secrets": ("same", "same")},
                       {"action": "token=fake-sensitive-data"}):
            with self.subTest(fields=fields), self.assertRaises(InstallerError):
                self.spec(**fields).as_dict()
        for mode in (None, False, -1, 1e40, {}, "unknown"):
            with self.subTest(mode=mode), self.assertRaises(InstallerError):
                build_plan([self.spec()], mode=mode)

    def test_unicode_punctuation_nonsecret_text_is_supported(self):
        spec = self.spec(action="Équipe d'Alsace — vérifier <sans exécuter> « HESTIA »",
                         warnings=("L'administrateur conserve sa clé existante.",))
        doc = initial_document(build_plan([spec]))
        validate_document(doc)
        self.assertIn("Équipe", doc["plan"]["steps"][0]["action"])

    def test_resource_ports_paths_fqdn_and_rollback_contract(self):
        for port in (True, False, None, -1, 0, 65536, 1.5, "443", 10**100):
            with self.subTest(port=port), self.assertRaises(InstallerError):
                ResourceSpec("port", "port", port).as_dict()
        self.assertEqual(ResourceSpec("port", "port", 443).as_dict()["target"], 443)
        for path in ("relative", "/", "/tmp/../x", "/tmp//x", "/tmp/x/", "/tmp/\x00", "/tmp/" + "x" * 4100):
            with self.subTest(path=path[:40]), self.assertRaises(InstallerError):
                ResourceSpec("path", "file", path).as_dict()
        for domain in ("localhost", "https://example.invalid", "UPPER.example", "bad;cmd.invalid"):
            with self.subTest(domain=domain), self.assertRaises(InstallerError):
                ResourceSpec("name", "fqdn", domain).as_dict()
        for resource in (ResourceSpec("r", "file", "/tmp/a", True, "delete-created"),
                         ResourceSpec("r", "file", "/tmp/a", False, "restore-backup"),
                         ResourceSpec("r", "file", "/tmp/a", True, "restore-backup", "/tmp/a")):
            with self.assertRaises(InstallerError):
                resource.as_dict()
        with self.assertRaises(InstallerError):
            self.spec(rollback_supported=True, resources=(ResourceSpec("r", "file", "/tmp/a"),)).as_dict()

    def test_dependencies_cycles_duplicates_and_resource_ownership_conflicts(self):
        a = self.spec(name="a")
        b = self.spec(name="b", dependencies=("a",))
        self.assertEqual(len(build_plan([a, b])["steps"]), 2)
        for specs in ([b, a], [a, a], [replace(a, dependencies=("a",))],
                      [replace(a, dependencies=("b",)), b]):
            with self.assertRaises(InstallerError):
                build_plan(specs)
        for kind in ("file", "directory"):
            with self.assertRaises(InstallerError):
                build_plan([replace(a, resources=(ResourceSpec("x", "file", "/tmp/owned"),)),
                            replace(b, resources=(ResourceSpec("y", kind, "/tmp/owned"),))])
        with self.assertRaises(InstallerError):
            build_plan([replace(a, resources=(ResourceSpec("x", "file", "/tmp/owned", True, "restore-backup", "/tmp/backup"),)),
                        replace(b, resources=(ResourceSpec("y", "file", "/tmp/backup"),))])

    def test_receipts_cannot_introduce_undeclared_resources_or_arbitrary_metadata(self):
        spec = self.spec().as_dict()
        for value in (Receipt(created_resources=("unknown",)).as_dict(),
                      Receipt(backups=("unknown",)).as_dict(),
                      Receipt(commit_shas=(("source", "bad"),)).as_dict(),
                      Receipt(hashes_non_secret=(("artifact", "bad"),)).as_dict(),
                      Receipt().as_dict() | {"stdout": "sensitive output"}):
            with self.assertRaises(InstallerError):
                validate_receipt(value, spec)
        with self.assertRaises(InstallerError):
            Receipt(commit_shas=(("a", "a" * 40), ("a", "b" * 40))).as_dict()

    def test_new_installer_version_requires_explicit_compatibility_migration(self):
        document = initial_document(build_plan(default_registry().specs()))
        document["installer_version"] = "999.0"
        document["plan"]["installer_version"] = "999.0"
        document["plan_sha256"] = plan_digest(document["plan"])
        validate_document(document)  # Reportable, but not executable by this version.
        with self.assertRaises(InstallerError):
            default_registry().validate_document(document)

    def test_secret_vault_cannot_be_serialized_and_known_secrets_do_not_escape(self):
        vault = SecretVault()
        secret = 'sensible"\\é-example'
        vault.put("github", secret)
        self.assertNotIn(secret, repr(vault))
        with self.assertRaises(TypeError):
            pickle.dumps(vault)
        with self.assertRaises(InstallerError):
            vault.reject_in({"nested": [secret]})
        vault.clear()
        with self.assertRaises(InstallerError):
            vault.require("github")

    def test_token_one_shot_is_atomic_under_concurrent_unlock(self):
        code, token = BootstrapToken.generate()
        barrier = threading.Barrier(3)
        results = []
        def unlock():
            barrier.wait()
            results.append(token.verify_and_consume(code))
        threads = [threading.Thread(target=unlock) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(results), [False, True])

    def test_global_state_preserves_independent_failure_after_targeted_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a = FileOperation(root, "a", fail="apply-after")
            b = FileOperation(root, "b", fail="apply-after")
            journal = StateJournal(root / "private" / "state.json")
            engine = TransactionEngine(journal, OperationRegistry((a, b)))
            plan = engine.plan()
            # Build a legitimate history with two failures on independent steps.
            engine.apply(plan["plan_sha256"])
            document = engine.report()
            document["steps"][1].update(state="FAILED", phase="prepare", attempts=1, last_error_redacted="OPERATION_FAILED")
            document["revision"] += 1
            journal.write(document, expected_revision=document["revision"] - 1)
            a.fail = None
            result = engine.retry("a", plan["plan_sha256"])
            self.assertEqual(result["steps"][0]["state"], "DONE")
            self.assertEqual(result["state"], "FAILED")
            self.assertEqual(result["last_error_redacted"], "OPERATION_FAILED")
            self.assertEqual(b.calls("apply"), 0)

    def test_production_contains_no_arbitrary_shell_or_dynamic_evaluation(self):
        root = Path(__file__).resolve().parents[1] / "installer"
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else (node.func.attr if isinstance(node.func, ast.Attribute) else "")
                self.assertNotIn(name, {"eval", "exec", "system", "popen", "__import__"}, str(path))
                for keyword in node.keywords:
                    if keyword.arg == "shell":
                        self.assertIsInstance(keyword.value, ast.Constant)
                        self.assertIs(keyword.value.value, False)


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "private"
        self.flags = ["--state-dir", str(self.directory)]

    def run_cli(self, *flags):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main([*self.flags, *flags])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_dry_run_is_inspectable_and_writes_nothing(self):
        status, output, _ = self.run_cli("--dry-run")
        document = json.loads(output)
        self.assertEqual(status, 0)
        self.assertEqual(document["plan"]["modules"], ["core"])
        self.assertEqual(document["plan_sha256"], plan_digest(document["plan"]))
        self.assertFalse(self.directory.exists())

    def test_report_absent_and_resume_absent_are_non_destructive(self):
        status, output, _ = self.run_cli("--report")
        self.assertEqual((status, json.loads(output)), (0, {"installation": None}))
        status, output, _ = self.run_cli("--resume")
        self.assertEqual(status, 4)
        self.assertIn("NOT_PLANNED", output)
        self.assertFalse(self.directory.exists())

    def test_check_behavior_is_preserved_for_non_root(self):
        results = [CheckResult("os", True, "Debian 12"), CheckResult("root", False, "non-root", False)]
        with patch("installer.cli.run_read_only_preflight", return_value=results):
            status, output, _ = self.run_cli("--check")
        self.assertEqual(status, 0)
        self.assertIn("preflight non destructif", output)
        self.assertIn("WARN", output)
        self.assertFalse(self.directory.exists())

    def test_cli_modes_are_mutually_exclusive(self):
        for flags in (("--check", "--report"), ("--dry-run", "--resume")):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                build_parser().parse_args(list(flags))
            self.assertEqual(caught.exception.code, 2)

    def test_report_corruption_is_redacted_without_starting_tls(self):
        self.directory.mkdir(mode=0o700)
        path = self.directory / "state.json"
        path.write_text('{"password":"EXAMPLE-CORRUPT-SECRET"}')
        path.chmod(0o600)
        with patch("installer.cli.prepare_bootstrap") as bootstrap:
            status, output, _ = self.run_cli("--report")
            bootstrap.assert_not_called()
        self.assertEqual(status, 4)
        self.assertNotIn("EXAMPLE-CORRUPT-SECRET", output)

    def test_resume_opens_new_cockpit_without_replaying_plan(self):
        journal = StateJournal(self.directory / "state.json")
        engine = TransactionEngine(journal, default_registry())
        plan = engine.plan()
        frozen = journal.path.read_bytes()
        prepared = MagicMock()
        prepared.server.serve_forever.side_effect = KeyboardInterrupt
        prepared.bind_decision.tunnel_required = False
        results = [CheckResult("os", True, "Debian 13"), CheckResult("root", True, "root", False)]
        with patch("installer.cli.prepare_bootstrap") as bootstrap, patch("installer.cli._install_signal_handlers"), \
             patch("installer.cli.run_read_only_preflight", return_value=results), patch("installer.cli.os.geteuid", return_value=0):
            bootstrap.return_value.__enter__.return_value = prepared
            status, output, _ = self.run_cli("--resume")
        self.assertEqual(status, 0)
        self.assertIn(plan["installation_id"], output)
        self.assertEqual(journal.path.read_bytes(), frozen)
        prepared.server.shutdown.assert_called_once()
