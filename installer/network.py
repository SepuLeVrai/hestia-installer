from __future__ import annotations

import errno
import ipaddress
import json
import secrets
import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Iterable

from installer.constants import PORT_RESERVATION_ATTEMPTS, TEMP_PORT_MAX, TEMP_PORT_MIN


@dataclass(frozen=True, slots=True)
class IPv4Candidate:
    address: str
    interface: str
    is_default: bool = False

    @property
    def ip(self) -> ipaddress.IPv4Address:
        return ipaddress.IPv4Address(self.address)

    @property
    def is_private(self) -> bool:
        return self.ip.is_private and not self.ip.is_loopback and not self.ip.is_link_local

    @property
    def is_global(self) -> bool:
        return self.ip.is_global


@dataclass(frozen=True, slots=True)
class BindDecision:
    bind_address: str
    advertised_address: str
    tunnel_required: bool
    reason: str


def _run_ip_json(args: list[str]) -> list[dict]:
    completed = subprocess.run(
        ["ip", "-j", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    payload = json.loads(completed.stdout or "[]")
    if not isinstance(payload, list):
        raise RuntimeError("Réponse iproute2 inattendue")
    return payload


def candidates_from_payload(routes: list[dict], addresses: list[dict]) -> list[IPv4Candidate]:
    default_by_interface: dict[str, str | None] = {}
    for route in routes:
        dev = str(route.get("dev") or "")
        if not dev:
            continue
        source = route.get("prefsrc") or route.get("src")
        default_by_interface.setdefault(dev, str(source) if source else None)

    found: list[IPv4Candidate] = []
    seen: set[str] = set()
    for interface in addresses:
        name = str(interface.get("ifname") or "")
        if not name:
            continue
        preferred_source = default_by_interface.get(name)
        for info in interface.get("addr_info") or []:
            if info.get("family") != "inet":
                continue
            if info.get("scope") not in (None, "global"):
                continue
            raw = info.get("local")
            if not raw:
                continue
            try:
                ip = ipaddress.IPv4Address(str(raw))
            except ipaddress.AddressValueError:
                continue
            if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                continue
            value = str(ip)
            if value in seen:
                continue
            seen.add(value)
            found.append(
                IPv4Candidate(
                    address=value,
                    interface=name,
                    is_default=(preferred_source == value or (preferred_source is None and name in default_by_interface)),
                )
            )

    found.sort(key=lambda item: (not item.is_default, not item.is_private, item.interface, item.address))
    return found


def discover_ipv4_candidates() -> list[IPv4Candidate]:
    routes = _run_ip_json(["route", "show", "default"])
    addresses = _run_ip_json(["addr", "show"])
    return candidates_from_payload(routes, addresses)


def _candidate_by_address(candidates: Iterable[IPv4Candidate], value: str) -> IPv4Candidate | None:
    return next((candidate for candidate in candidates if candidate.address == value), None)


def choose_admin_candidate(
    candidates: list[IPv4Candidate],
    *,
    requested: str | None = None,
    allow_public: bool = False,
    interactive: bool | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> BindDecision:
    if requested:
        try:
            requested_ip = ipaddress.IPv4Address(requested)
        except ipaddress.AddressValueError as exc:
            raise ValueError("Adresse IPv4 demandée invalide") from exc
        if requested_ip.is_loopback:
            return BindDecision("127.0.0.1", "127.0.0.1", True, "loopback demandé explicitement")
        candidate = _candidate_by_address(candidates, str(requested_ip))
        if candidate is None:
            raise ValueError("L'adresse demandée n'appartient pas aux interfaces détectées")
        if candidate.is_global and not allow_public:
            return BindDecision("127.0.0.1", "127.0.0.1", True, "IP publique refusée par défaut")
        return BindDecision(candidate.address, candidate.address, False, "adresse demandée")

    if not candidates:
        return BindDecision("127.0.0.1", "127.0.0.1", True, "aucune IPv4 d'administration détectée")

    private_candidates = [candidate for candidate in candidates if candidate.is_private]
    if not private_candidates:
        return BindDecision("127.0.0.1", "127.0.0.1", True, "seules des IPv4 publiques sont disponibles")

    if len(private_candidates) == 1:
        selected = private_candidates[0]
        return BindDecision(selected.address, selected.address, False, "IPv4 privée unique")

    if interactive is None:
        interactive = sys.stdin.isatty()

    default_candidate = next((candidate for candidate in private_candidates if candidate.is_default), private_candidates[0])
    if not interactive:
        return BindDecision(default_candidate.address, default_candidate.address, False, "sélection non interactive de la route par défaut")

    output_fn("Plusieurs IPv4 d'administration sont disponibles :")
    for index, candidate in enumerate(private_candidates, start=1):
        marker = " - route par défaut" if candidate.is_default else ""
        output_fn(f"  [{index}] {candidate.address} ({candidate.interface}){marker}")
    default_index = private_candidates.index(default_candidate) + 1

    for _ in range(3):
        raw = input_fn(f"Adresse à utiliser [{default_index}] : ").strip()
        if not raw:
            return BindDecision(default_candidate.address, default_candidate.address, False, "choix interactif par défaut")
        try:
            chosen = private_candidates[int(raw) - 1]
        except (ValueError, IndexError):
            output_fn("Choix invalide.")
            continue
        return BindDecision(chosen.address, chosen.address, False, "choix interactif")
    raise RuntimeError("Impossible de sélectionner une IPv4 d'administration valide")


class PortReservation:
    def __init__(self, host: str, port: int, sock: socket.socket) -> None:
        self.host = host
        self.port = port
        self._socket = sock

    @property
    def socket(self) -> socket.socket:
        if self._socket is None:
            raise RuntimeError("La réservation de port a déjà été transférée")
        return self._socket

    def detach(self) -> socket.socket:
        sock = self.socket
        self._socket = None
        return sock

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def __enter__(self) -> "PortReservation":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def reserve_random_port(
    host: str,
    *,
    port_min: int = TEMP_PORT_MIN,
    port_max: int = TEMP_PORT_MAX,
    attempts: int = PORT_RESERVATION_ATTEMPTS,
    chooser: Callable[[int, int], int] | None = None,
) -> PortReservation:
    if port_min < 1 or port_max > 65535 or port_min > port_max:
        raise ValueError("Plage de ports invalide")
    if attempts < 1:
        raise ValueError("Le nombre de tentatives doit être positif")

    random_source = secrets.SystemRandom()
    choose = chooser or random_source.randint
    last_error: OSError | None = None

    for _ in range(attempts):
        port = int(choose(port_min, port_max))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, port))
            sock.listen(64)
            sock.set_inheritable(False)
            return PortReservation(host, port, sock)
        except OSError as exc:
            sock.close()
            if exc.errno in (errno.EADDRINUSE, errno.EACCES):
                last_error = exc
                continue
            raise

    raise RuntimeError(f"Impossible de réserver un port HTTPS temporaire: {last_error}")


def verify_port_closed(host: str, port: int, *, timeout: float = 0.2) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(timeout)
    try:
        return probe.connect_ex((host, port)) != 0
    finally:
        probe.close()
