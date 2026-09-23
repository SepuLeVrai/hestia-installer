from __future__ import annotations

import ipaddress
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TLSMaterial:
    certificate: Path
    private_key: Path


def generate_ephemeral_certificate(
    staging_dir: Path,
    bind_address: str,
    *,
    openssl_binary: str = "openssl",
) -> TLSMaterial:
    ip = ipaddress.IPv4Address(bind_address)
    staging_dir = staging_dir.resolve()
    certificate = staging_dir / "bootstrap-cert.pem"
    private_key = staging_dir / "bootstrap-key.pem"
    config = staging_dir / "openssl-bootstrap.cnf"

    config.write_text(
        "\n".join(
            [
                "[req]",
                "prompt = no",
                "distinguished_name = dn",
                "x509_extensions = v3_req",
                "[dn]",
                "CN = HESTIA Installer Bootstrap",
                "[v3_req]",
                f"subjectAltName = IP:{ip}",
                "basicConstraints = critical,CA:FALSE",
                "keyUsage = critical,digitalSignature,keyEncipherment",
                "extendedKeyUsage = serverAuth",
                "",
            ]
        ),
        encoding="utf-8",
    )
    os.chmod(config, 0o600)

    command = [
        openssl_binary,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-sha256",
        "-nodes",
        "-keyout",
        str(private_key),
        "-out",
        str(certificate),
        "-days",
        "1",
        "-config",
        str(config),
        "-extensions",
        "v3_req",
    ]
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
        os.chmod(private_key, 0o600)
        os.chmod(certificate, 0o600)
        _verify_subject_alt_name(certificate, str(ip), openssl_binary=openssl_binary)
    except Exception:
        private_key.unlink(missing_ok=True)
        certificate.unlink(missing_ok=True)
        raise
    finally:
        config.unlink(missing_ok=True)

    return TLSMaterial(certificate=certificate, private_key=private_key)


def _verify_subject_alt_name(certificate: Path, expected_ip: str, *, openssl_binary: str) -> None:
    completed = subprocess.run(
        [openssl_binary, "x509", "-in", str(certificate), "-noout", "-ext", "subjectAltName"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    normalized = completed.stdout.replace(" ", "")
    if f"IPAddress:{expected_ip}" not in normalized:
        raise RuntimeError("Le certificat temporaire ne contient pas le SAN IP attendu")
