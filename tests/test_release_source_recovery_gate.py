import contextlib,hashlib,io,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from scripts import verify_preview as gate
class ReleaseSourceGate(unittest.TestCase):
 def verify(self,recovery_pass):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)/'repo';(root/'selfhost').mkdir(parents=True);(root/'selfhost/main.pxl').write_text('fn main(){}')
   artifact=root/'pixellang/artifacts/compiler.json';artifact.parent.mkdir(parents=True);artifact.write_text('{}')
   sources=json.dumps({'main.pxl':'fn main(){}'},ensure_ascii=False,sort_keys=True).encode()
   (artifact.parent/'compiler-manifest.json').write_text(json.dumps({'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),'sourceSha256':hashlib.sha256(sources).hexdigest()}))
   output=Path(directory)/'evidence';calls=[]
   def command(argv,log,timeout):
    name=log.stem;calls.append((name,argv,timeout));log.write_text('mocked orchestration only\n')
    if '--output' in argv:
     dest=Path(argv[argv.index('--output')+1])
     report={'verified':recovery_pass if name=='source-recovery' else True,'fixedPointVerified':True,'gate':'passed','status':'passed'}
     if dest.suffix=='.json':dest.write_text(json.dumps(report))
     else:
      dest.mkdir(exist_ok=True);(dest/'summary.json').write_text(json.dumps(report))
      if name=='native-bootstrap':
       (dest/'status.json').write_text(json.dumps(report));(dest/'compiler-g3.json').write_bytes(artifact.read_bytes());(dest/'compiler.png').write_bytes(b'fixture');(dest/'recovered-compiler.json').write_bytes(artifact.read_bytes())
      if name=='image':(dest/'compiler.png').write_bytes(b'fixture')
      if name=='image-generated':(dest/'compiler.json').write_bytes(artifact.read_bytes())
    return 0
   with patch.object(gate,'ROOT',root),patch.object(gate.platform,'platform',return_value='test-platform'),patch.object(gate,'inputs',return_value={'stable':'hash'}),patch.object(gate,'command',side_effect=command),patch.object(gate.importlib.metadata,'distributions',return_value=[]),patch.object(gate.subprocess,'run',return_value=types.SimpleNamespace(stdout='v22.0.0')),patch.object(sys,'argv',['verify_preview.py','release','--output',str(output)]),contextlib.redirect_stdout(io.StringIO()):
    status=gate.main()
   return status,json.loads((output/'summary.json').read_text()),calls
 def test_source_recovery_is_required_and_uses_exact_fixed_point(self):
  status,summary,calls=self.verify(True);self.assertEqual(status,0);self.assertTrue(summary['verified'])
  names=[n for n,_,_ in calls];self.assertLess(names.index('native-bootstrap'),names.index('source-recovery'));self.assertLess(names.index('source-recovery'),names.index('recovered-regression'))
  _,argv,timeout=next(c for c in calls if c[0]=='source-recovery');self.assertEqual(Path(argv[argv.index('--compiler')+1]).name,'compiler-g3.json');self.assertEqual(Path(argv[argv.index('--image')+1]).name,'compiler.png');self.assertEqual(timeout,7500)
  _,argv,timeout=next(c for c in calls if c[0]=='native-bootstrap');self.assertIn('scripts/verify_native_selfhost.py',argv);self.assertEqual(Path(argv[argv.index('--native-compiler')+1]).name,'compiler-native');self.assertEqual(timeout,6000)
  _,argv,_=next(c for c in calls if c[0]=='source-recovery');self.assertIn('scripts/verify_native_source_recovery.py',argv);self.assertEqual(Path(argv[argv.index('--native-compiler')+1]).name,'compiler-g3')
  for name,expected in [('generated','compiler-native'),('recovered-regression','recovered-compiler')]:
   _,argv,_=next(c for c in calls if c[0]==name);self.assertEqual(Path(argv[argv.index('--native-compiler')+1]).name,expected)
 def test_failed_source_recovery_cannot_mark_release_verified(self):
  status,summary,calls=self.verify(False);self.assertEqual(status,1);self.assertFalse(summary['verified']);self.assertIn('source-recovery did not prove verified',summary['error']);self.assertNotIn('recovered-regression',[n for n,_,_ in calls])


class RecoverySmokeTests(unittest.TestCase):
 def code(self, value):
  from tests.bytecode_helpers import program, op
  code = program([op('RETURN')])
  code['constants'] = [{'type': 'int', 'value': value}, {'type': 'int', 'value': 0}]
  code['functions']['main']['instructions'] = [op('PUSH', 0), op('PRINT'), op('PUSH', 1), op('RETURN')]
  return code

 def test_both_backends_must_produce_expected_output(self):
  from scripts import verify_native_source_recovery as recovery
  for status, output in [(0, '42\n'), (0, '41\n'), (1, '42\n')]:
   with self.subTest(status=status, output=output), patch.object(
       recovery.subprocess, 'run', return_value=types.SimpleNamespace(returncode=status, stdout=output)):
    if status == 0 and output == '42\n':
     self.assertEqual(recovery.verify_smoke(self.code(42), Path('smoke')), [42])
    else:
     with self.assertRaisesRegex(ValueError, 'native smoke failed'):
      recovery.verify_smoke(self.code(42), Path('smoke'))

 def test_wrong_reference_result_cannot_pass_matching_native_output(self):
  from scripts import verify_native_source_recovery as recovery
  with patch.object(recovery.subprocess, 'run') as execute:
   with self.assertRaisesRegex(ValueError, 'reference smoke failed'):
    recovery.verify_smoke(self.code(41), Path('smoke'))
   execute.assert_not_called()


class RecoveryCompilerIdentityTests(unittest.TestCase):
 def test_self_consistent_but_different_compiler_is_rejected(self):
  from scripts.verify_native_source_recovery import verify_canonical_compiler
  expected = RecoverySmokeTests().code(42)
  different = RecoverySmokeTests().code(41)
  verify_canonical_compiler(expected, json.loads(json.dumps(expected)))
  with self.assertRaisesRegex(ValueError, 'canonical source compiler'):
   verify_canonical_compiler(expected, different)

if __name__=='__main__':unittest.main(verbosity=2)
