"""Phase 5A: input validation only. No storage, network or deployment side effects."""
from __future__ import annotations

import ipaddress
import re
import unicodedata
from copy import deepcopy
from pathlib import PurePosixPath

from installer.model import ErrorCode, exact_keys, integer, require
from installer.validation import validate_fqdn

# This is an installer input contract, not the future PHP execution contract.
CONTRACT_VERSION = 1
TOP = {"version", "mode", "web", "database", "administrator", "assistant", "secrets"}
SECRET_FIELDS = {"database_password", "admin_password", "openai_api_key"}


def _string(value: object, minimum: int, maximum: int) -> str:
    require(type(value) is str and minimum <= len(value) <= maximum)
    # Includes surrogates, bidi controls, NUL and other non-printing characters.
    require(not any(unicodedata.category(c).startswith("C") for c in value))
    return value


def _choice(value: object, allowed: tuple[str, ...]) -> str:
    require(type(value) is str and value in allowed)
    return value


def _path(value: object) -> str:
    value = _string(value, 2, 1024)
    parts = value.split("/")
    require(parts[0] == "" and all(p not in ("", ".", "..") for p in parts[1:]))
    require(all(len(p.encode("utf-8")) <= 255 for p in parts[1:]))
    require("\\" not in value and str(PurePosixPath(value)) == value)
    return value


def _identifier(value: object, maximum: int) -> str:
    value = _string(value, 1, maximum)
    require(re.fullmatch(r"[A-Za-z0-9_]+", value) is not None)
    return value


def _host(value: object) -> str:
    value = _string(value, 1, 253)
    require(value == value.strip() and "%" not in value)
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        require(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value) is not None)
        require(all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                    for label in value.split(".")))
        # An invalid dotted numerical IPv4 must not silently become a DNS name.
        require(not re.fullmatch(r"[0-9.]+", value))
        return value.lower()
    require(not (address.is_unspecified or address.is_multicast or address.is_link_local))
    return address.compressed


def _local(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_web_configuration(payload: dict) -> dict:
    """Return a non-secret preview, never an approved or deployable plan.

    Credentials belong only to this call. The caller must not persist the input.
    Passwords are not stripped or normalized. No credential validity is inferred
    from syntax, no filesystem permission is inferred from a path string.
    """
    exact_keys(payload, TOP)
    integer(payload["version"], CONTRACT_VERSION, CONTRACT_VERSION)
    mode = _choice(payload["mode"], ("fresh", "upgrade"))
    secret = payload["secrets"]
    exact_keys(secret, SECRET_FIELDS)
    for name in SECRET_FIELDS:
        _string(secret[name], 0, 1024 if name != "openai_api_key" else 500)

    web = payload["web"]
    exact_keys(web, {"hostname", "webroot", "service_user"})
    hostname = _string(web["hostname"], 1, 253)
    require(hostname == hostname.strip() and not hostname.endswith("."))
    try:
        hostname = validate_fqdn(hostname)
    except ValueError:
        require(False)
    webroot = _path(web["webroot"])
    # Conservative v1 scope: deployment is a child, never /var/www itself.
    require(webroot.startswith("/var/www/") or webroot.startswith("/srv/"))
    service_user = _string(web["service_user"], 1, 32)
    require(re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", service_user) is not None)
    require(service_user not in ("root", "nobody"))

    database = payload["database"]
    exact_keys(database, {"mode", "host", "port", "name", "user", "tls_ca_file"})
    dbmode = _choice(database["mode"], ("managed", "existing_local", "remote"))
    host = _host(database["host"])
    port = integer(database["port"], 1, 65535)
    name = _identifier(database["name"], 64)
    user = _identifier(database["user"], 32)
    require(user.lower() != "root")
    require(name.lower() not in ("mysql", "sys", "information_schema", "performance_schema"))
    if dbmode == "remote":
        require(not _local(host))
        ca_file = _path(database["tls_ca_file"])
        require(not (ca_file == webroot or ca_file.startswith(webroot + "/")))
    else:
        require(_local(host) and database["tls_ca_file"] is None)
        ca_file = None
    if dbmode == "managed":
        require(mode == "fresh" and port == 3306)
    _string(secret["database_password"], 1, 1024)

    admin = payload["administrator"]
    if mode == "fresh":
        exact_keys(admin, {"first_name", "last_name", "email"})
        first = _string(admin["first_name"], 1, 100)
        last = _string(admin["last_name"], 1, 100)
        require(first == first.strip() and last == last.strip())
        email = _string(admin["email"], 3, 254)
        require(re.fullmatch(r"[A-Za-z0-9._+%-]{1,64}@[A-Za-z0-9.-]+", email) is not None)
        local = email.split("@", 1)[0]
        require(not (local.startswith(".") or local.endswith(".") or ".." in local))
        try:
            validate_fqdn(email.split("@", 1)[1])
        except ValueError:
            require(False)
        # 72 UTF-8 bytes keeps the input compatible with a PHP bcrypt backend.
        password = _string(secret["admin_password"], 12, 72)
        require(len(password.encode("utf-8")) <= 72 and bool(password.strip()))
        admin = {"first_name": first, "last_name": last, "email": email.lower()}
    else:
        # Updating an existing instance must never recreate/reset its first admin.
        require(admin is None and secret["admin_password"] == "")

    assistant = payload["assistant"]
    exact_keys(assistant, {"action"})
    action = _choice(assistant["action"], ("disabled", "configure", "preserve"))
    key = secret["openai_api_key"]
    require(key == key.strip())
    notices = []
    if action == "preserve":
        require(mode == "upgrade" and key == "")
        enabled = None  # The actual state is read from the target in a later lot.
    elif action == "disabled":
        require(key == "")
        enabled = False
    elif not key:
        action, enabled = "disabled", False
        notices.append("ASSISTANT_DISABLED_NO_KEY")
    else:
        require(re.fullmatch(r"[A-Za-z0-9_.-]{20,500}", key) is not None)
        require(key.upper() not in ("COLLER_LA_CLE_OPENAI_ICI", "YOUR_OPENAI_API_KEY_HERE"))
        enabled = True

    configuration = {
        "version": CONTRACT_VERSION, "mode": mode,
        "web": {"hostname": hostname, "webroot": webroot, "service_user": service_user},
        "database": {"mode": dbmode, "host": host, "port": port, "name": name,
                     "user": user, "tls_ca_file": ca_file},
        "administrator": admin,
        "assistant": {"action": action, "desired_enabled": enabled},
    }
    # Even an allowed text field must not echo a known supplied credential.
    # Scan values (not fixed schema keys) in original and canonical spelling.
    def strings(value):
        if type(value) is str:
            yield value
        elif type(value) is dict:
            for item in value.values():
                yield from strings(item)
    public_values = tuple(strings(configuration)) + tuple(strings({k: v for k, v in payload.items() if k != "secrets"}))
    for supplied in secret.values():
        if supplied:
            require(not any(supplied.casefold() in v.casefold() for v in public_values), ErrorCode.SECRET_REJECTED)
    return {"scope": "INPUT_ONLY", "deployment_ready": False,
            "configuration": deepcopy(configuration), "notices": notices,
            "pending_checks": ["TARGET_IDENTITY", "FILESYSTEM_PERMISSIONS", "DATABASE_CONNECTIVITY",
                               "WEB_EXECUTION_CONTRACT", "BACKUP_AND_MIGRATIONS", "ASSISTANT_RUNTIME"]}
