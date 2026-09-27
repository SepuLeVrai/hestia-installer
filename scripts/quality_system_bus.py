#!/usr/bin/env python3
"""Affected package and D-Bus controllers; not a global promotion gate."""
import argparse
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import quality

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(); before = quality.snapshot(ROOT)
    suite = unittest.defaultTestLoader.loadTestsFromNames(('test_system_bus', 'test_system_packages'))
    ids = sorted(t.id() for t in quality.flatten(suite))
    required = json.loads((ROOT / 'tests/quality-baseline.json').read_bytes())['required_tests']['core']
    assert len(ids) == len(set(ids)) == 35 and set(ids) <= set(required)
    result = unittest.TextTestRunner(verbosity=2).run(suite); stable = before == quality.snapshot(ROOT)
    passed = result.wasSuccessful() and result.testsRun == 35 and not result.skipped and stable
    report = {'suite': 'System bus and packages controllers', 'tests': result.testsRun, 'expected': 35,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'source_stable': stable, 'source_files': len(before), 'test_ids': ids,
        'status': 'PASS' if passed else 'FAIL', 'global_quality': False, 'phase5_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(quality.encode(report))
    (args.report.parent / 'SYSTEM-BUS-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    print(json.dumps(report)); sys.exit(0 if passed else 1)
