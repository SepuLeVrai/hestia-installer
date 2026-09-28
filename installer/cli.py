from __future__ import annotations

import argparse
import json
import os
import signal
from pathlib import Path

from installer import __version__
from installer.bootstrap import prepare_bootstrap
from installer.constants import DEFAULT_STATE_ROOT
from installer.engine import TransactionEngine
from installer.github_sources import GitHubAcquisition
from installer.application_plan import ApplicationPlan
from installer.upgrade_plan import UpgradePlan
from installer.model import ErrorCode, InstallerError, plan_digest
from installer.operations import default_registry
from installer.service import TransactionService
from installer.transaction import StateJournal
from installer.preflight import bootstrap_blockers, run_read_only_preflight


def _print_preflight() -> tuple[int, list]:
    results = run_read_only_preflight()
    print("HESTIA Installer - preflight non destructif")
    print("")
    for result in results:
        status = "PASS" if result.ok else "WARN"
        print(f"[{status:4}] {result.name:<20} {result.detail}")
    blockers = [item for item in results if item.required_for_bootstrap and not item.ok]
    return (1 if blockers else 0), results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hestia-installer",
        description="Orchestrateur one-shot HESTIA",
    )
    parser.add_argument("--version", action="version", version=__version__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--check",
        action="store_true",
        help="exécuter uniquement le preflight non destructif",
    )
    actions.add_argument("--dry-run", action="store_true", help="afficher le plan existant ou le contrôle core, sans écriture")
    actions.add_argument("--resume", action="store_true", help="rouvrir le cockpit sur le journal existant, sans replay automatique")
    actions.add_argument("--report", action="store_true", help="lire le rapport non secret sans démarrer HTTPS")
    actions.add_argument("--register-managed-upgrade", type=Path, metavar="PROFILE.json",
                         help="vérifier et enregistrer un profil local géré/scellé pour le wizard upgrade, sans migration")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_ROOT,
                        help="répertoire privé persistant du journal (chemin absolu, mode 0700)")
    parser.add_argument(
        "--bind-address",
        help="IPv4 locale à utiliser pour le mini-web HTTPS",
    )
    parser.add_argument(
        "--allow-public-bootstrap",
        action="store_true",
        help="autoriser explicitement un bind sur une IPv4 publique",
    )
    return parser


def _web_root() -> Path:
    return Path(__file__).resolve().parent / "web"


def _install_signal_handlers() -> None:
    def _request_shutdown(_signum, _frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _request_shutdown)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.check:
        status, _ = _print_preflight()
        return status

    try:
        engine = TransactionEngine(StateJournal(args.state_dir / "state.json"), default_registry())
        github = GitHubAcquisition(engine, restore=False)
        upgrade = UpgradePlan(engine, github)
        if args.register_managed_upgrade:
            print(json.dumps(upgrade.register(args.register_managed_upgrade), ensure_ascii=False, indent=2))
            return 0
        if not ApplicationPlan(engine, github).restore() and not upgrade.restore():
            github.restore_registry()
        if args.dry_run:
            existing = engine.report()
            plan = existing["plan"] if existing is not None else engine.dry_run()
            title = ("PLAN DE MIGRATION WEB SOUS MAINTENANCE" if UpgradePlan.owns(existing) else
                     "PLAN DE PRÉPARATION WEB SOUS MAINTENANCE" if ApplicationPlan.owns(existing) else
                     "PLAN D'ACQUISITION DES SOURCES" if any("source" in s for s in plan["steps"]) else "PLAN D'INSTALLATION - CORE CHECK UNIQUEMENT")
            print(json.dumps({"title": title, "plan": plan,
                              "plan_sha256": plan_digest(plan)}, ensure_ascii=False, indent=2))
            return 0
        if args.report:
            from installer.package_plan import PackagePlan
            result = {"installation": engine.report()}
            packages = PackagePlan(engine).state()
            if packages['profile'] is not None: result['packages'] = packages
            from installer.mariadb_plan import MariaDBPlan
            mariadb = MariaDBPlan(engine, PackagePlan(engine)).state()
            if mariadb['profile'] is not None: result['mariadb'] = mariadb
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.resume:
            document = engine.report()
            if document is None:
                raise InstallerError(ErrorCode.NOT_PLANNED)
            engine.registry.validate_document(document)
            print(f"Reprise du journal {document['installation_id']} : {document['state']}")
            print("Aucune étape n'est rejouée avant confirmation explicite du plan.")
    except InstallerError as exc:
        print(f"[FAIL] transaction: {exc.code.value}")
        return 4
    except Exception:
        print("[FAIL] transaction: état indisponible")
        return 4

    _, results = _print_preflight()
    blockers = bootstrap_blockers(results)
    if blockers:
        print("")
        for blocker in blockers:
            print(f"[FAIL] {blocker.name}: {blocker.detail}")
        return 2

    if os.geteuid() != 0:
        print("[FAIL] root requis pour démarrer HESTIA Installer")
        return 2

    _install_signal_handlers()
    print("")
    print("Préparation du mini-web HTTPS temporaire...")

    service = TransactionService(engine, github=github)
    try:
        with prepare_bootstrap(
            web_root=_web_root(),
            bind_address=args.bind_address,
            allow_public=args.allow_public_bootstrap,
            transaction_service=service,
        ) as prepared:
            print(f"[PASS] IPv4 d'administration : {prepared.bind_decision.bind_address}")
            print(f"[PASS] Port HTTPS temporaire réservé : {prepared.port}")
            print("[PASS] Certificat temporaire généré")
            print("[PASS] Orchestrateur prêt")
            print("")
            if prepared.bind_decision.tunnel_required:
                print("Sécurité : le mini-web écoute uniquement sur loopback.")
                print(f"Tunnel conseillé : ssh -L {prepared.port}:127.0.0.1:{prepared.port} <user>@<serveur>")
                print("")
            print("Continuer l'installation :")
            print(prepared.url)
            print("")
            print("Code d'accès temporaire :")
            print(prepared.bootstrap_code)
            print("")
            print("État : EN ATTENTE DE L'UTILISATEUR...")
            try:
                prepared.server.serve_forever(poll_interval=0.2)
            except KeyboardInterrupt:
                print("\nArrêt demandé. Nettoyage du bootstrap...")
            finally:
                prepared.server.shutdown()
    except Exception:
        print("[FAIL] bootstrap: préparation ou exécution indisponible")
        return 3
    finally:
        service.close()

    print("[PASS] Mini-web arrêté, staging nettoyé et port fermé")
    return 0
