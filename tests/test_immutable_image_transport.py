"""Native chunk transport publishes immutable data without collapsing batches."""
import json
from pathlib import Path
import tempfile
import unittest

from pixellang.fileaccess import FileAccess
from pixellang.model import PixelError
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources
from tests.test_immutable_pixel_recovery import sources

DRIVER = '''import "image_transport.pxl" as transport
import "pixel_statements.pxl" as statements
record Request { operation: string, path: string, parsed: statements.Output }
effect fn main(){
 match jsonDecode::[Request](input()) { .Ok(request)=>{Dispatch(request)}, .Err(error)=>{print(jsonFrom(error))} }
}
effect fn Dispatch(request:Request) {
 if request.operation == "emit" { transport.EmitParsed(request.parsed) }
 else if request.operation == "source" {
  match transport.LoadSource(request.path) { .Ok(value)=>{print(jsonFrom(value))}, .Err(error)=>{print(jsonFrom(error))} }
 } else {
  match transport.LoadParsed(request.path) { .Ok(value)=>{print(jsonFrom(value))}, .Err(error)=>{print(jsonFrom(error))} }
 }
}'''

def parsed(tokens=(), nodes=()):
    return dict(syntax=dict(tokens=list(tokens), nodes=list(nodes), roots=[], diagnostics=[]), entries=[], diagnostics=[])

def token(index, text='x'):
    return dict(kind='name',text=text,value=text,start=index,end=index+1,line=1,column=index+1)

class ImmutableImageTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = compile_native_sources(sources(DRIVER))

    def run_request(self, operation, value=None, directory=None):
        request=dict(operation=operation,path='header.json',parsed=value or parsed())
        vm=VM(self.compiler,input_text=json.dumps(request),file_access=FileAccess(directory) if directory else None,
              max_steps=20_000_000,max_depth=1024,max_heap_items=4_000_000,max_heap_objects=500_000,max_output_chars=4_000_000)
        return [json.loads(item) for item in vm.run()]

    def test_count_boundary_and_parsed_file_roundtrip(self):
        original=parsed([token(i) for i in range(1001)], [dict(kind='name',text='x',children=[],start=0,end=1)])
        original['syntax']['roots']=[0];original['entries']=[0]
        events=self.run_request('emit',original)
        chunks=[event for event in events if event['kind']=='tokens']
        self.assertEqual([len(chunk['items']) for chunk in chunks],[1000,1])
        self.assertEqual([item for chunk in chunks for item in chunk['items']],original['syntax']['tokens'])
        self.assertEqual(events[-1],dict(kind='parsed-end',first=1001,second=1))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); header=events[0]
            header={k:v for k,v in header.items() if k!='kind'}
            header.update(tokenFiles=[],nodeFiles=[])
            for i,event in enumerate(events[1:-1]):
                filename=f'{i}.json';(root/filename).write_text(json.dumps(event['items']))
                header['tokenFiles' if event['kind']=='tokens' else 'nodeFiles'].append(filename)
            (root/'header.json').write_text(json.dumps(header))
            self.assertEqual(self.run_request('parsed',directory=directory),[original])

    def test_serialized_size_boundary(self):
        original=parsed([token(i,'x'*50000) for i in range(3)])
        events=self.run_request('emit',original)
        chunks=[event for event in events if event['kind']=='tokens']
        self.assertEqual([len(chunk['items']) for chunk in chunks],[1,1,1])
        self.assertEqual([item for chunk in chunks for item in chunk['items']],original['syntax']['tokens'])

    def test_empty_parsed_stream(self):
        events=self.run_request('emit')
        self.assertEqual([e['kind'] for e in events],['parsed','parsed-end'])
        self.assertEqual(events[-1],dict(kind='parsed-end',first=0,second=0))

    def test_decoded_source_chunks(self):
        tokens=[dict(kind='int',value=42,position=[0,0]),dict(kind='str',value='中文',position=[1,0])]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for i,item in enumerate(tokens):(root/f'{i}.json').write_text(json.dumps([item]))
            (root/'header.json').write_text(json.dumps(dict(links=[],tokenFiles=['0.json','1.json'])))
            self.assertEqual(self.run_request('source',directory=directory),[dict(links=[],decoded=dict(tokens=tokens,diagnostics=[]))])

    def test_file_capability_is_required(self):
        errors=self.run_request('source')
        self.assertEqual(len(errors),1)
        self.assertEqual(errors[0]['kind'],'io')
        self.assertEqual(errors[0]['operation'],'read')
