"""Leading-dot variants resolve solely through the expected nominal enum type."""
from tests.native_compiler import native_service
import unittest
from pixellang.vm import VM
from pixellang.native_editor import NativeSymbolIndex


class NativeContextVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, source, operation='compile'):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        return self.compiler.request(dict(version=1, operation=operation, files=files))

    def test_nested_context_and_pattern_payloads(self):
        source = '''enum Status { Pending, Paid(amount:int) }
enum Outcome[T] { Ok(value:T), Rejected }
fn pay(amount:int)->Outcome[Status]=.Ok(.Paid(amount))
effect fn main(){
 let initial:Status=.Pending
 match initial {.Pending=>{print(0)},.Paid(amount)=>{print(amount)}}
 match pay(42) {.Ok(status)=>{match status {.Pending=>{},.Paid(amount)=>{print(amount)}}},.Rejected=>{}}
}'''
        response=self.request(source)
        self.assertEqual(response['status'],'ok',response)
        self.assertEqual(VM(response['result']['bytecode']).run(),[0,42])

    def test_later_generic_evidence_and_imported_type(self):
        response=self.request({
            'model.pxl':'export enum Choice[T]{Empty,Value(item:T)}',
            'main.pxl':'''import "model.pxl" as m
fn choose[T](value:m.Choice[T],fallback:T)->T=match value {.Value(item)=>item,.Empty=>fallback}
effect fn main(){print(choose(.Value([1]),[0]));print(choose(.Empty,[2]))}
'''})
        self.assertEqual(response['status'],'ok',response)
        self.assertEqual(VM(response['result']['bytecode']).run(),[[1],[2]])

    def test_no_global_search_or_invalid_payloads(self):
        declarations='enum A{Empty,Value(item:int)}\nenum B{Empty}\n'
        for body in ('let x=.Empty','let x:int=.Empty','let x:A=.Missing',
                     'let x:A=.Value','let x:A=.Value("wrong")',
                     'let x:A=.Value(1,2)'):
            with self.subTest(body=body):
                response=self.request(declarations+'fn main(){'+body+'}')
                self.assertEqual(response['status'],'diagnostics',response)

    def test_contextual_patterns_remain_exhaustive(self):
        for body in ('match value {.Empty=>{}}',
                     'match value {.Missing=>{},_=>{}}',
                     'match value {.Value=>{},.Empty=>{}}'):
            with self.subTest(body=body):
                response=self.request('enum A{Empty,Value(item:int)}\nfn test(value:A){'+body+'}\nfn main(){}')
                self.assertEqual(response['status'],'diagnostics',response)

    def test_state_initializers_and_png_recovery_preserve_variant_identity(self):
        source='''enum Status { Pending, Paid(amount:int) }
flow Hold() step()->Status {
 state status:Status=.Paid(4)
 next status=status
 output status
}
effect fn main(){
 let instance=Hold()
 match instance.step() {.Pending=>{print(0)},.Paid(amount)=>{print(amount)}}
}'''
        exported=self.request(source,'export-png')
        self.assertEqual(exported['status'],'ok',exported)
        recovered=self.compiler.request(dict(version=1,operation='recover-png',
                                             image=exported['result']['image']))
        self.assertEqual(recovered['status'],'ok',recovered)
        rebuilt=self.compiler.request(dict(version=1,operation='compile',**recovered['result']))
        self.assertEqual(rebuilt['status'],'ok',rebuilt)
        self.assertEqual(VM(rebuilt['result']['bytecode']).run(),[4])

    def test_editor_navigation_and_rename_follow_expected_nominal_identity(self):
        files={
            'model.pxl':'export enum Choice { Empty, Value(item:int) }\nexport enum Other { Value(item:int) }',
            'main.pxl':'''import "model.pxl" as model
fn read(value:model.Choice)->int=match value {.Value(item)=>item,.Empty=>0}
fn main(){let value:model.Choice=.Value(1);let other:model.Other=.Value(2);read(value)}
'''}

        def analyze(sources):
            response=self.request(sources,'editor-index')
            self.assertEqual(response['status'],'ok',response)
            result=NativeSymbolIndex(sources,'main.pxl',response['result']['index'])
            self.assertEqual(result.diagnostics,[])
            return result

        index=analyze(files)
        source=files['main.pxl']
        for use in ('.Value(1)', '.Value(item)'):
            target=index.definition('main.pxl',source.index(use)+1)
            self.assertEqual(target['source'],'model.pxl')
            self.assertEqual(target['start']['offset'],files['model.pxl'].index('Value'))
        edits=index.rename('main.pxl',source.index('.Value(1)')+1,'Item',analyze=analyze)
        self.assertEqual(len(edits),3)

    def test_formatter_keeps_contextual_variants_and_is_idempotent(self):
        source='enum A { Empty, Value(item:int) }\nfn read(value:A)->int=match value {.Empty=>0,.Value(item)=>item}\nfn main(){let value:A=.Value(2);read(value)}'
        formatted=self.request(source,'format')
        self.assertEqual(formatted['status'],'ok',formatted)
        text=formatted['result']['text']
        self.assertIn('.Value',text)
        self.assertIn('.Empty',text)
        second=self.request(text,'format')
        self.assertEqual(second['status'],'ok',second)
        self.assertEqual(second['result']['text'],text)
        self.assertEqual(self.request(text)['status'],'ok')

    def test_completion_uses_expected_type_without_accepting_unfinished_code(self):
        source='enum A{Empty,Value(item:int)}\nenum B{Other}\nfn main(){let value:A=.V}'

        def analyze(files):
            response=self.request(files,'editor-index')
            self.assertEqual(response['status'],'ok',response)
            return NativeSymbolIndex(files,'main.pxl',response['result']['index'])

        index=analyze({'main.pxl':source})
        self.assertTrue(index.diagnostics)
        items=index.complete('main.pxl',source.index('.V')+2,analyze=analyze)
        self.assertEqual([item['label'] for item in items],['Value'])
        self.assertEqual(self.request(source)['status'],'diagnostics')
