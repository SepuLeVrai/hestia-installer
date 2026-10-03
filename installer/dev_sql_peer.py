"""Closed SQL-server exception for the separately sealed DEV Web only.

The MAIN backup still covers MAIN and Gateway. No arbitrary secondary schema,
DEV data backup, or caller-provided SQL allowlist is admitted by this binding.
"""
from installer import foundation_drain
from installer.dev_target import digest
from installer.model import ErrorCode, require


def selected(runtime):
    path = runtime.spec.root.parent / 'foundation/dev-target.json'
    try: path.lstat()
    except FileNotFoundError: return None
    return foundation_drain.attached(runtime)


def binding(runtime, database):
    foundation = selected(runtime)
    if foundation is None: return None
    require(foundation.dev is not None, ErrorCode.INCOMPATIBLE_STATE)
    target = foundation.dev.target.inspect().separate(runtime)
    peer = target.value['configuration']['database']
    require(database['host'] == peer['host'] == '127.0.0.1'
            and database['port'] == peer['port'] == 3306
            and database['name'].lower() != peer['name'].lower()
            and database['user'] != peer['user'], ErrorCode.INCOMPATIBLE_STATE)
    return {'policy': 'MANAGED_DEV_SQL_PEER_V1', 'database': peer['name'],
            'target_sha256': digest(target.value)}
