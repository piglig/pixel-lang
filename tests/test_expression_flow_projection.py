"""Studio sees original expression-flow syntax and checked binding dependencies."""
import json
from pathlib import Path
import re
import subprocess
import unittest
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT=Path(__file__).resolve().parents[1]
DRIVER='''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "flow_projection.pxl" as projection
effect fn main(){
 match jsonDecode::[map[string,string]](input()) { .Ok(files)=>{Dispatch(files)}, .Err(error)=>{print(error.message)} }
}
effect fn Dispatch(files:map[string,string]) {
 print(projection.Build(checker.Check(types.Analyze(bindings.Bind(project.Build(files,"main.pxl"))))))
}'''

class ExpressionFlowProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files={'main.pxl':DRIVER}
        pending=re.findall(r'import "([^"]+)"',DRIVER)
        while pending:
            name=pending.pop()
            if name in files:continue
            files[name]=(ROOT/'selfhost'/name).read_text()
            pending.extend(re.findall(r'import "([^"]+)"',files[name]))
        cls.code=compile_native_sources(files)
        Path('/tmp/pixel-expression-projection-driver.json').write_text(json.dumps(cls.code))

    def project(self,source,**modules):
        return VM(self.code,input_text=json.dumps({'main.pxl':source,**modules}),
                  max_steps=40_000_000,max_output_chars=16_000_000).run()[0]

    def test_forward_expression_ranges_types_and_dependencies(self):
        source='''// 星 🛰️
flow Pipeline() step(x:int)->int {
 let result = doubled + 1
 let doubled = x * 2
 output result
}
effect fn main(){let pipeline=Pipeline();print(pipeline.step(3))}'''
        projection=self.project(source)
        self.assertEqual(projection['diagnostics'],[])
        self.assertEqual(projection['version'],'pixellang-native-graphs/2')
        graph=projection['graphs'][0]
        self.assertNotIn('nodes',graph);self.assertNotIn('connections',graph)
        result,doubled=graph['values']
        self.assertEqual([result['type'],doubled['type']],['int','int'])
        self.assertEqual(graph['order'],[doubled['node'],result['node']])
        span=result['expression']['range']
        self.assertEqual(source[span['start']:span['end']],'doubled + 1')
        span=graph['body'];self.assertEqual(source[span['start']],'{')
        refs=[source[d['range']['start']:d['range']['end']] for d in graph['dependencies']]
        self.assertEqual(refs,['doubled','x','result'])
        self.assertEqual([(d['source'],d['target']) for d in graph['dependencies']],
            [(doubled['node'],result['node']),(graph['inputs'][0]['node'],doubled['node']),
             (result['node'],graph['outputs'][0]['node'])])

    def test_imported_child_constructor_and_conditional_activation(self):
        source='''import "lib.pxl" as lib
enum Event {Add(value:int),Skip}
flow Parent(seed:int) step(event:Event)->int {
 instance counter=lib.Counter(seed+1)
 let value=match event {.Add(x)=>counter.step(x),.Skip=>-1}
 output value
}
effect fn main(){}'''
        projection=self.project(source,**{'lib.pxl':'''export flow Counter(seed:int) step(delta:int)->int {
 state n=seed
 next n=n+delta
 output n
}'''})
        self.assertEqual(projection['diagnostics'],[])
        graph=next(g for g in projection['graphs'] if g['name']=='Parent')
        child=graph['children'][0]
        self.assertEqual(child['target'],'lib.Counter')
        self.assertEqual([i['name'] for i in child['inputs']],['delta'])
        self.assertEqual(child['result'],'int')
        span=child['initializers'][0];self.assertEqual(source[span['start']:span['end']],'seed+1')
        span=child['constructor']['range'];self.assertEqual(source[span['start']:span['end']],'lib.Counter(seed+1)')
        edge=next(d for d in graph['dependencies'] if d['source']==child['node'])
        self.assertEqual(edge['target'],graph['values'][0]['node'])
        span=edge['range'];self.assertEqual(source[span['start']:span['end']],'counter')

    def test_inferred_state_and_full_next_output_expressions(self):
        source='flow F() step(x:int)->int {state n=0;next n=n+x;output n*2}\neffect fn main(){}'
        projection=self.project(source)
        self.assertEqual(projection['diagnostics'],[])
        graph=projection['graphs'][0]
        self.assertEqual(graph['states'][0]['type'],'int')
        span=graph['states'][0]['initializer'];self.assertEqual(source[span['start']:span['end']],'0')
        span=graph['updates'][0]['expression']['range'];self.assertEqual(source[span['start']:span['end']],'n+x')
        self.assertEqual(graph['outputs'][0]['expression']['type'],'int')

    def test_grouped_expression_slots_preserve_reference_ranges(self):
        source='''flow Child(a:int,b:string) step(x:int)->int {output x}
flow Parent(seed:int) step(x:int)->int {
 instance child=Child(((seed + 1)), (")"))
 state n=((0))
 let value=((x))
 next n=(value + 1)
 output ((child.step(value)))
}
effect fn main(){}'''
        projection=self.project(source)
        self.assertEqual(projection['diagnostics'],[])
        graph=next(g for g in projection['graphs'] if g['name']=='Parent')
        def text(span):return source[span['start']:span['end']]
        self.assertEqual([text(s) for s in graph['children'][0]['initializers']],
                         ['((seed + 1))','(")")'])
        self.assertEqual(text(graph['states'][0]['initializer']),'((0))')
        self.assertEqual(text(graph['values'][0]['expression']['range']),'((x))')
        self.assertEqual(text(graph['updates'][0]['expression']['range']),'(value + 1)')
        self.assertEqual(text(graph['outputs'][0]['expression']['range']),'((child.step(value)))')
        self.assertEqual([text(d['range']) for d in graph['dependencies']],
                         ['x','value','child','value'])

    def test_invalid_bindings_preserve_syntax_without_inventing_edges(self):
        source='flow F() step(x:int)->int {let value=missing+1;output value}\neffect fn main(){}'
        projection=self.project(source)
        self.assertTrue(projection['diagnostics'])
        graph=projection['graphs'][0]
        self.assertEqual(graph['values'][0]['name'],'value')
        self.assertEqual(graph['dependencies'],[])
        span=graph['values'][0]['expression']['range']
        self.assertEqual(source[span['start']:span['end']],'missing+1')

    def test_shadowed_closure_parameter_is_not_a_flow_dependency(self):
        projection=self.project('''flow F() step(x:int)->int {
 let apply=fn(x:int)->int=x+1
 output apply(x)
}
effect fn main(){}''')
        self.assertEqual(projection['diagnostics'],[])
        graph=projection['graphs'][0]
        self.assertFalse(any(d['target']==graph['values'][0]['node'] for d in graph['dependencies']))

    def test_studio_edits_reparse_native_source_and_preserve_unresolved_uses(self):
        source='// 星 🛰️\nflow F() step(x:int)->int {\n state n=0\n next n=x\n output n\n}\neffect fn main(){}'
        script='''const fs=require('node:fs');
const {planEdit,validateGraphEdit}=require('./vscode/src/native-graph-edits');
const r=JSON.parse(fs.readFileSync(0,'utf8'));
if(r.after){validateGraphEdit(r.graph,r.after,r.action,r.source,r.candidate);console.log('true');}
else {let text=r.source;for(const e of planEdit(text,r.graph,r.action))text=text.slice(0,e.start)+e.text+text.slice(e.end);console.log(JSON.stringify(text));}'''
        def js(request):
            run=subprocess.run(['node','-e',script],cwd=ROOT,input=json.dumps(request),text=True,capture_output=True)
            self.assertEqual(run.returncode,0,run.stderr)
            return json.loads(run.stdout)
        graph=self.project(source)['graphs'][0]
        for action in [dict(kind='add-value',name='doubled',expression='x * 2'),
                       dict(kind='set-output',expression='doubled + n'),
                       dict(kind='remove-value')]:
            if action['kind']=='remove-value': action['node']=graph['values'][0]['node']
            candidate=js(dict(source=source,graph=graph,action=action))
            parsed=self.project(candidate)
            after=parsed['graphs'][0]
            self.assertTrue(js(dict(source=source,candidate=candidate,graph=graph,after=after,action=action)))
            if action['kind']=='remove-value':
                self.assertTrue(parsed['diagnostics'])
                self.assertIn('output doubled + n',candidate)
            else: self.assertEqual(parsed['diagnostics'],[])
            source,graph=candidate,after

if __name__=='__main__':unittest.main()
