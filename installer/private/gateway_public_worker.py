"""Fixed-generation worker for the transferred public and boot fragments."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

PROFILE_SHA256 = '__GATEWAY_PUBLIC_GENERATION_SHA256__'


def read(path):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    directory = os.open('/', flags | os.O_DIRECTORY)
    try:
        for name in path.parts[1:-1]:
            child = os.open(name, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory); directory = child
            info = os.fstat(directory)
            if info.st_uid or info.st_mode & 0o7022 or os.listxattr(directory): raise ValueError()
        handle = os.open(path.name, flags, dir_fd=directory)
        try:
            info = os.fstat(handle)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_gid
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or info.st_size > 1048576 or os.listxattr(handle)): raise ValueError()
            raw = bytearray()
            while len(raw) <= 1048576:
                part = os.read(handle, min(65536, 1048577 - len(raw)))
                if not part: break
                raw.extend(part)
            after = os.fstat(handle); named = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            if len(raw) != info.st_size or any(getattr(info, k) != getattr(other, k)
                    for other in (after, named) for k in fields): raise ValueError()
            return bytes(raw)
        finally: os.close(handle)
    finally: os.close(directory)


def main():
    try:
        if os.getuid() != 0 or os.geteuid() != 0 or len(sys.argv) != 2: raise ValueError()
        if sys.argv[1] not in ('sql', 'web', 'mobile', 'http', 'https', 'backend', 'renew'): raise ValueError()
        root = Path(__file__).parent; raw = read(root / 'profile.json')
        if hashlib.sha256(raw).hexdigest() != PROFILE_SHA256: raise ValueError()
        value = json.loads(raw)
        for name, digest in value['code'].items():
            path = Path(name)
            if not name.startswith('installer/') or '..' in path.parts or path.is_absolute(): raise ValueError()
            if hashlib.sha256(read(root / 'code' / name)).hexdigest() != digest: raise ValueError()
        sys.path.insert(0, str(root / 'code'))
        from installer.gateway_public_generation import Generation
        generation = Generation(value)
        if generation.root != root: raise ValueError()
        argv = generation.worker(sys.argv[1])
        if argv is not None: os.execve(argv[0], argv, {'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C'})
    except Exception:
        print('HESTIA_GATEWAY_PUBLIC_REJECTED'); return 1
    print('HESTIA_GATEWAY_PUBLIC_READY'); return 0


if __name__ == '__main__': raise SystemExit(main())
