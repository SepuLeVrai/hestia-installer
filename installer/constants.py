from pathlib import Path

INSTALLER_NAME = "HESTIA Installer"
INSTALLER_VERSION = "0.1.0.dev0"

TEMP_PORT_MIN = 57000
TEMP_PORT_MAX = 57999

DEFAULT_RUNTIME_ROOT = Path("/run/hestia-installer")
DEFAULT_STATE_ROOT = Path("/var/lib/hestia-installer")
DEFAULT_BACKUP_ROOT = Path("/root/_HestiaBCKP")

REQUIRED_BOOTSTRAP_COMMANDS = ("python3", "openssl", "ip")
