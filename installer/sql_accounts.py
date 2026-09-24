"""5B2.2a: private read-only audit of pre-existing local MariaDB accounts.

No creation, GRANT, ALTER USER, public operation or fresh/upgrade dispatch.
The strict local-dml-v1 profile is a prerequisite, not a runtime certification.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from installer import php_transport as transport
from installer.model import strict_json_loads
from installer.web_config import validate_web_configuration

ERRORS = frozenset({"REQUEST_INVALID", "ACCOUNT_SEPARATION_REQUIRED", "CONNECTION_FAILED",
                    "SERVER_PROFILE_UNSUPPORTED", "TARGET_IDENTITY_MISMATCH",
                    "ACCOUNT_POLICY_REJECTED", "AUDIT_UNAVAILABLE"})
VERIFIED = {"scope": "ACCOUNT_PRIVILEGES_ONLY", "state": "ACCOUNTS_VERIFIED", "code": "OK",
            "policy": "local-dml-v1", "application_installed": False}


class AccountConfigurationError(RuntimeError):
    """Fixed code only; no chained diagnostic or credentials."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AccountConfigurationError(code)


def local_configuration(payload: dict, *, fresh_only: bool = False) -> dict:
    try:
        config = validate_web_configuration(payload)["configuration"]
        require(not fresh_only or config["mode"] == "fresh", "UPGRADE_CONFIGURATION_REFUSED")
        database = config["database"]
        require(database["mode"] == "existing_local" and database["host"] in ("127.0.0.1", "localhost")
                and database["port"] == 3306 and database["tls_ca_file"] is None,
                "LOCAL_PROFILE_UNSUPPORTED")
        # Apply the byte limit as well as the 5A character limit, with no trim.
        transport.ProvisioningCredentials(database["user"], payload["secrets"]["database_password"])
        return config
    except AccountConfigurationError:
        raise
    except Exception:
        raise AccountConfigurationError("INPUT_REJECTED") from None


def _request(payload: dict, credentials: transport.ProvisioningCredentials) -> dict:
    config = local_configuration(payload)
    require(type(credentials) is transport.ProvisioningCredentials, "PROVISIONING_ACCOUNT_REQUIRED")
    database = config["database"]
    password = payload["secrets"]["database_password"]
    require(credentials._user != database["user"] and credentials._password != password,
            "ACCOUNT_SEPARATION_REQUIRED")
    return {"version": 1, "operation": "audit_local_accounts", "request_id": os.urandom(16).hex(),
            "database": {"host": "127.0.0.1", "port": 3306, "name": database["name"]},
            "application": {"user": database["user"], "password": password},
            "provisioning": {"user": credentials._user, "password": credentials._password}}


def _response(code: int, data: bytes, request_id: str) -> dict:
    try:
        require(type(code) is int and type(data) is bytes and len(data) <= transport.MAX_OUTPUT,
                "AUDIT_PROTOCOL_REJECTED")
        reply = strict_json_loads(data)
        require(type(reply) is dict and set(reply) == {"version", "operation", "request_id", "ok", "error",
                                                       "policy", "application_installed"}, "AUDIT_PROTOCOL_REJECTED")
        require(type(reply["version"]) is int and reply["version"] == 1
                and reply["operation"] == "audit_local_accounts" and reply["request_id"] == request_id
                and type(reply["ok"]) is bool and reply["application_installed"] is False, "AUDIT_PROTOCOL_REJECTED")
        if reply["ok"]:
            require(code == 0 and reply["error"] is None and reply["policy"] == "local-dml-v1", "AUDIT_PROTOCOL_REJECTED")
            return dict(VERIFIED)
        require(code == 20 and type(reply["error"]) is str and reply["error"] in ERRORS
                and reply["policy"] is None, "AUDIT_PROTOCOL_REJECTED")
        return {"scope": "ACCOUNT_PRIVILEGES_ONLY", "state": "REFUSED", "code": reply["error"],
                "policy": None, "application_installed": False}
    except AccountConfigurationError:
        raise
    except Exception:
        raise AccountConfigurationError("AUDIT_PROTOCOL_REJECTED") from None


def audit_local_accounts(runtime: transport.PhpRuntime, payload: dict,
                         credentials: transport.ProvisioningCredentials, *, cancel=None) -> dict:
    """Audit current grants of both real connections, never a caller-supplied report.

    Upgrade may be observed read-only. No secrets, names, raw grants, password
    hashes or database content appear in this function's public result.
    """
    request = _request(payload, credentials)
    try:
        transport._runtime(runtime, payload["web"]["service_user"])
        require(cancel is None or not cancel.is_set(), "AUDIT_INTERRUPTED")
        with tempfile.TemporaryDirectory(prefix="accounts-", dir=runtime.run_root) as temporary:
            stage = Path(temporary)
            for source, destination in (("sql_accounts_bridge.php", "bridge.php"),
                                        ("sql_accounts_policy.php", "sql_accounts_policy.php")):
                data = transport._read_file(Path(__file__).parent / "private" / source)
                path = stage / destination
                with path.open("xb") as stream:
                    stream.write(data)
                os.chown(path, 0, runtime.worker_gid)
                path.chmod(0o640)
            os.chown(stage, 0, runtime.worker_gid)
            stage.chmod(0o750)
            code, data = transport._exchange(transport._command(runtime, stage), transport._json(request),
                                             stage, runtime.timeout_seconds, cancel)
            return _response(code, data, request["request_id"])
    except AccountConfigurationError:
        raise
    except transport.TransportError as error:
        allowed = {"TIMEOUT", "INTERRUPTED", "OUTPUT_LIMIT", "UNEXPECTED_STDERR", "CHANNEL_FAILED"}
        raise AccountConfigurationError("AUDIT_" + str(error) if str(error) in allowed else "AUDIT_LOCAL_PREFLIGHT_FAILED") from None
    except Exception:
        raise AccountConfigurationError("AUDIT_LOCAL_PREFLIGHT_FAILED") from None
