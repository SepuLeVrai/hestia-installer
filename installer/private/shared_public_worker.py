"""Private successor guard. Listener exec replaces this process in its cgroup."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

PROFILE_SHA256 = '__SHARED_PUBLIC_SHA256__'


def read(path):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    fd = os.open('/', flags | os.O_DIRECTORY)
    try:
        for name in path.parts[1:-1]:
            child = os.open(name, flags | os.O_DIRECTORY, dir_fd=fd); os.close(fd); fd = child
            info = os.fstat(fd)
            if info.st_uid or info.st_mode & 0o7022 or {'system.posix_acl_access', 'system.posix_acl_default'} & set(os.listxattr(fd)): raise ValueError()
        handle = os.open(path.name, flags, dir_fd=fd)
        try:
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_gid or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 1048576 or os.listxattr(handle): raise ValueError()
            data = os.read(handle, 1048577)
            if len(data) != info.st_size: raise ValueError()
            return data
        finally: os.close(handle)
    finally: os.close(fd)


def main():
    try:
        if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in ('http', 'https', 'backend', 'renew'): raise ValueError()
        root = Path(__file__).parent
        raw = read(root / 'profile.json')
        if hashlib.sha256(raw).hexdigest() != PROFILE_SHA256: raise ValueError()
        profile = json.loads(raw)
        for name, digest in profile['code'].items():
            if not name.startswith('installer/') or '..' in Path(name).parts or Path(name).is_absolute(): raise ValueError()
            if hashlib.sha256(read(root / 'code' / name)).hexdigest() != digest: raise ValueError()
        sys.path.insert(0, str(root / 'code'))
        from installer.shared_public_runtime import SharedPublic
        runtime = SharedPublic(profile)
        if runtime.root != root: raise ValueError()
        argv = runtime.worker(sys.argv[1])
        if argv is not None:
            os.execve(argv[0], argv, {"PATH": "/usr/sbin:/usr/bin", "LANG": "C"})
    except Exception:
        print('HESTIA_SHARED_PUBLIC_REJECTED'); return 1
    print('HESTIA_SHARED_PUBLIC_READY'); return 0


if __name__ == '__main__':
    raise SystemExit(main())
