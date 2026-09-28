#!/usr/bin/env python3
"""Affected controls only; full Quality remains a separate promotion gate."""
import argparse
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import quality

MODULES=('test_web_fence','test_configuration_fence','test_inode_fence','test_data_access','test_scheduler_admission','test_provisioned_admission','test_provisioned_backup','test_http_drain','test_http_runtime','test_session_cleaner','test_http_cleaner_drain')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--with-files',action='store_true');args=parser.parse_args()
    modules=MODULES+(('test_web_fence_files','test_configuration_fence_files','test_inode_fence_files','test_data_access_files','test_coordinated_backup','test_backup_files') if args.with_files else ())
    before=quality.snapshot(ROOT);suite=unittest.defaultTestLoader.loadTestsFromNames(modules)
    ids=sorted(t.id() for t in quality.flatten(suite));expected=178 if args.with_files else 112
    required=json.loads((ROOT/'tests/quality-baseline.json').read_bytes())['required_tests']['core']
    assert set(ids)<=set(required) and len(ids)==expected and len(set(ids))==expected
    result=unittest.TextTestRunner(verbosity=2).run(suite);stable=quality.snapshot(ROOT)==before
    report={'suite':'Provisioned backup affected controllers and files','tests':result.testsRun,'expected':expected,
        'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),'test_ids':ids,
        'source_stable':stable,'source_files':len(before),
        'status':'PASS' if result.wasSuccessful() and not result.skipped and stable and result.testsRun==expected else 'FAIL',
        'global_quality':False,'phase5_complete':False}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    (args.report.parent/'CONTROLLERS-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_bytes(quality.encode(report));sys.exit(0 if report['status']=='PASS' else 1)
