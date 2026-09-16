import json
from pathlib import Path
import tempfile
import time
import unittest

from pixellang.image_workspace import Execution, execute, read_modules, read_parsed, stage_parsed
from pixellang.project import compile_project

ROOT=Path(__file__).resolve().parents[1]


class ImageTransportTests(unittest.TestCase):
    def test_large_unicode_arena_round_trip_through_bounded_files_and_stream(self):
        sources={p.name:p.read_text() for p in (ROOT/'selfhost').glob('*.pxl')}
        sources['main.pxl']='import "image_transport.pxl" as t\nfn main(){t.EmitParsed(t.LoadParsed("parsed-0.json"))}'
        code=compile_project(sources)[1].bytecode
        value=dict(syntax=dict(tokens=[],nodes=[dict(kind='string',text='汉字🙂'*20,
            children=[],start=i,end=i+1) for i in range(12000)], roots=[],diagnostics=[]), entries=[],diagnostics=[])
        self.assertGreater(len(json.dumps(value)),1000000)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);stage_parsed(p,'0',value)
            self.assertGreater(len(list(p.glob('nodes-*.json'))),1)
            self.assertTrue(all(f.stat().st_size<1000000 for f in p.glob('*.json')))
            output,_=execute(Execution(code),{},p,time.monotonic()+120)
            self.assertTrue(all(len(item)<1000000 for item in output))
            self.assertEqual(read_parsed(output),value)

    def test_stream_truncation_and_counts_are_rejected(self):
        header=dict(kind='parsed',roots=[],entries=[],diagnostics=[],syntaxDiagnostics=[])
        for records in ([header],[header,dict(kind='parsed-end',first=1,second=0)],
                        [header,dict(kind='parsed-end',first=0,second=0),header]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                read_parsed([json.dumps(x) for x in records])
        with self.assertRaises(ValueError):
            read_modules([json.dumps(dict(kind='module',id='0',source=dict(pixels=[])))])

    def test_reused_execution_resets_heap_output_and_effect_state(self):
        code=compile_project({'main.pxl':'fn main(){let a=[1];append(a,2);print(a);print(input())}'})[1].bytecode
        session=Execution(code)
        with tempfile.TemporaryDirectory() as root:
            first,_=execute(session,{'run':1},root,time.monotonic()+10)
            session.vm.cancel()
            second,_=execute(session,{'run':2},root,time.monotonic()+10)
            self.assertEqual(first,[[1,2],json.dumps({'run':1})])
            self.assertEqual(second,[[1,2],json.dumps({'run':2})])
            with self.assertRaisesRegex(ValueError,'bounded VM input'):
                execute(session,{'large':'x'*1000000},root,time.monotonic()+10)
