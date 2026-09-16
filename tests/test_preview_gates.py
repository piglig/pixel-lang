import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.benchmark_service import load_limits
from scripts.verify_preview import command

ROOT=Path(__file__).resolve().parents[1]


class PreviewGateTests(unittest.TestCase):
    def test_related_workers_and_timing_history_preserve_exact_coverage(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            names=['tests.regression_fixture.Cases.test_pass_a',
                   'tests.regression_fixture.Cases.test_pass_b']
            history=root/'history'/'related'
            history.mkdir(parents=True)
            # History schedules work even if it came from an interrupted run;
            # it never supplies passing outcomes.
            (history/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in [
                dict(kind='start',test=names[0]),
                dict(kind='stop',test=names[0],seconds=10),
                dict(kind='start',test=names[1])]))
            output=root/'run'
            cmd=[sys.executable,str(ROOT/'scripts/verify_preview.py'),'related',
                 '--output',str(output),'--workers','2','--timings-from',str(root/'history'),
                 *[arg for name in names for arg in ('--test',name)]]
            result=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads((output/'summary.json').read_text())
            self.assertTrue(report['verified'])
            self.assertTrue(report['fresh'])
            manifest=json.loads((output/'related/manifest.json').read_text())
            self.assertEqual(len(manifest['shards']),2)
            self.assertEqual(sorted(sum(manifest['shards'],[])),names)
            self.assertEqual(manifest['selection']['timingCosts'],dict.fromkeys(names,10))
            events=[json.loads(line) for line in (output/'related/events.jsonl').read_text().splitlines()]
            self.assertEqual(sorted(e['test'] for e in events if e['kind']=='success'),names)

    def test_deadline_stops_owned_process(self):
        with TemporaryDirectory() as directory:
            log=Path(directory)/'process.log'
            with self.assertRaises(subprocess.TimeoutExpired):
                command([sys.executable,'-c','import os,time;print(os.getpid(),flush=True);time.sleep(30)'],log,0.3)
            pid=int(log.read_text().splitlines()[0])
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    def test_related_gate_propagates_real_failure_and_preserves_evidence(self):
        with TemporaryDirectory() as directory:
            for case,expected in [('test_pass_a',True),('test_fail',False)]:
                output=Path(directory)/case
                command=[sys.executable,str(ROOT/'scripts/verify_preview.py'),'related','--output',str(output),
                         '--test','tests.regression_fixture.Cases.'+case]
                result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=60)
                report=json.loads((output/'summary.json').read_text())
                self.assertEqual(result.returncode,0 if expected else 1,result.stdout+result.stderr)
                self.assertEqual(report['verified'],expected)
                self.assertTrue(report['inputsUnchanged'])
                self.assertTrue(Path(report['commands'][0]['log']).exists())
                original=(output/'summary.json').read_bytes()
                repeated=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=60)
                self.assertNotEqual(repeated.returncode,0)
                self.assertEqual((output/'summary.json').read_bytes(),original)

    def test_nonfinite_or_incomplete_limits_cannot_disable_gate(self):
        limits=json.loads((ROOT/'config/service-performance-limits.json').read_text())
        with TemporaryDirectory() as directory:
            path=Path(directory)/'limits.json'
            for value in [float('nan'),float('inf'),0,-1,True]:
                limits['phases']['warm-check']['seconds']=value
                path.write_text(json.dumps(limits))
                with self.subTest(value=value),self.assertRaises(ValueError):load_limits(path)
            path.write_text('{}')
            with self.assertRaises(KeyError):load_limits(path)
