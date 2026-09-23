from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path

from installer import __version__
from installer.bootstrap import prepare_bootstrap
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
    parser.add_argument(
        "--check",
        action="store_true",
        help="exécuter uniquement le preflight non destructif",
    )
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

    try:
        with prepare_bootstrap(
            web_root=_web_root(),
            bind_address=args.bind_address,
            allow_public=args.allow_public_bootstrap,
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
    except Exception as exc:
        print(f"[FAIL] bootstrap: {exc}")
        return 3

    print("[PASS] Mini-web arrêté, staging nettoyé et port fermé")
    return 0
