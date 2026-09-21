from __future__ import annotations

import ipaddress
import re

_FQDN_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def validate_fqdn(value: str) -> str:
    fqdn = value.strip().rstrip(".")
    if not fqdn or len(fqdn) > 253:
        raise ValueError("FQDN invalide")
    if "://" in fqdn or "/" in fqdn or "\n" in fqdn or "\r" in fqdn:
        raise ValueError("Le champ attend un FQDN, pas une URL")
    labels = fqdn.split(".")
    if len(labels) < 2 or not all(_FQDN_LABEL.fullmatch(label) for label in labels):
        raise ValueError("FQDN invalide")
    return fqdn.lower()


def normalize_network(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError("Réseau vide")
    return ipaddress.ip_network(candidate, strict=False).compressed


def normalize_networks(values: list[str]) -> list[str]:
    return sorted({normalize_network(value) for value in values})
