"""Bounded fixture-only diagnostics for deliberately private runtime refusals."""
import json
from pathlib import PurePath
import re


class DiagnosedCollect:
    def collect(self, *args, **kwargs):
        try: return super().collect(*args, **kwargs)
        except Exception as error:
            chain, seen, current = [], set(), error
            while current is not None and id(current) not in seen and len(chain) < 8:
                seen.add(id(current)); message = str(current); frames = []
                tb = current.__traceback__
                while tb is not None and len(frames) < 16:
                    frame = tb.tb_frame; name = frame.f_code.co_name
                    row = {'file': PurePath(frame.f_code.co_filename).name[:96],
                        'function': name[:96], 'line': tb.tb_lineno}
                    # Only numeric process coordinates and closed parser tokens;
                    # never free-form exceptions, cmdline, credentials or a path.
                    for key in ('pid', 'tid', 'tgid'):
                        value = frame.f_locals.get(key)
                        if type(value) is int and 0 <= value <= 2**31-1: row[key] = value
                    if row['file'] == 'systemd_discovery.py' and name == '_token':
                        value = frame.f_locals.get('value')
                        if type(value) is str and re.fullmatch('[A-Za-z0-9_-]{0,64}', value): row['state_token'] = value
                    frames.append(row); tb = tb.tb_next
                chain.append({'type': type(current).__name__[:96],
                    'code': message if re.fullmatch('[A-Z][A-Z0-9_]{1,95}', message) else 'REDACTED',
                    'errno': current.errno if isinstance(current, OSError) else None, 'frames': frames})
                current = current.__context__
            print(json.dumps({'fixture_rejection_diagnostics': chain}), flush=True)
            raise
