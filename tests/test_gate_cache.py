import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pixellang.gate_cache import StageReuse, hashes, command_key


class GateCacheTests(unittest.TestCase):
    def test_exact_input_reuse_and_corruption_rejection(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);old=root/'old';new=root/'new';old.mkdir();new.mkdir()
            manifest={'files':{'vm.py':'abc'},'environment':{'python':'same'}}
            (old/'manifest.json').write_text(json.dumps(manifest))
            (old/'step.log').write_text('passed')
            (old/'step').mkdir();(old/'step/result.json').write_text('{"verified":true}')
            artifacts={k:v for k,v in hashes(old).items() if k!='manifest.json'}
            argv=['python','test','--output',str(old/'step')]
            entry=dict(name='step',exitCode=0,completed=True,commandKey=command_key(argv,old),artifacts=artifacts,seconds=7)
            (old/'summary.json').write_text(json.dumps(dict(commands=[entry],inputsUnchanged=True)))
            reuse=StageReuse(old,manifest)
            copied=reuse.restore('step',['python','test','--output',str(new/'step')],new)
            self.assertEqual(copied['originalSeconds'],7)
            self.assertEqual(copied['log'],str((new/'step.log').resolve()))
            self.assertEqual((new/'step/result.json').read_bytes(),(old/'step/result.json').read_bytes())
            with self.assertRaises(ValueError):StageReuse(old,dict(manifest,files={'vm.py':'changed'}))
            (old/'step/result.json').write_text('corrupt')
            with self.assertRaisesRegex(ValueError,'corrupt'):reuse.restore('step',['python','test','--output',str(new/'step')],new)

    def test_failed_or_changed_command_is_not_reused(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);(root/'manifest.json').write_text('{}')
            (root/'summary.json').write_text(json.dumps({'commands':[{'name':'step','exitCode':1}]}))
            self.assertIsNone(StageReuse(root,{}).restore('step',['test'],root/'new'))
            (root/'summary.json').write_text(json.dumps({'commands':[{'name':'step','exitCode':0,'completed':True,'commandKey':['other']}]}))
            self.assertIsNone(StageReuse(root,{}).restore('step',['test'],root/'new'))
