from __future__ import annotations

import argparse

from installer import __version__
from installer.preflight import run_read_only_preflight


def _print_preflight() -> int:
    failed = False
    print("HESTIA Installer - preflight non destructif")
    print("")

    for result in run_read_only_preflight():
        status = "PASS" if result.ok else "WARN"
        print(f"[{status:4}] {result.name:<20} {result.detail}")
        if result.name.startswith("command:") and not result.ok:
            failed = True

    return 1 if failed else 0


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.check:
        return _print_preflight()

    print("HESTIA Installer - socle initial")
    print("")
    print("Le bootstrap HTTPS interactif est suivi dans l'issue #1.")
    print("Aucune mutation système n'est exécutée par ce socle.")
    print("")
    print("Utilisez --check pour le preflight non destructif.")
    return 0
