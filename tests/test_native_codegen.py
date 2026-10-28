"""Standalone machine-code output is checked against the reference VM."""
import json
import selectors
import struct
import sys
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from pixellang.native_codegen import build_native, emit_c, UnsupportedNativeProgram
from pixellang.vm import VM
from tests.bytecode_helpers import op, program


@unittest.skipUnless(shutil.which('clang') or shutil.which('cc'), 'C compiler required')
class NativeCodegenTests(unittest.TestCase):
    def code(self, instructions, constants=(0,), locals=0):
        code = program([])
        types = {bool: 'bool', int: 'int', float: 'float64', str: 'str'}
        code['constants'] = [dict(type=types[type(v)], value=v) for v in constants]
        code['functions'] = {'main': dict(params=[], locals=locals, result='unit', instructions=instructions)}
        return code

    def execute(self, code, *, steps=1000000):
        with tempfile.TemporaryDirectory() as directory:
            path = build_native(code, Path(directory)/'program')
            completed = subprocess.run([str(path), str(steps)], capture_output=True, text=True, timeout=10)
            mapping = json.loads(path.with_name('program.map.json').read_text())
            return completed, mapping

    def test_integer_edges_and_boolean_operations_match_vm(self):
        instructions = []
        constants = [-17, 5, -(1 << 63), -1, False, True]
        for operation in ('ADD', 'SUB', 'MUL', 'DIV', 'MOD', 'EQ', 'NE', 'LT', 'LE', 'GT', 'GE'):
            instructions += [op('PUSH', 0), op('PUSH', 1), op(operation), op('PRINT')]
        instructions += [op('PUSH',2),op('PUSH',3),op('MOD'),op('PRINT'),
                         op('PUSH',4),op('NOT'),op('PRINT'),op('PUSH',5),op('RETURN')]
        code = self.code(instructions, constants)
        result, _ = self.execute(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()], VM(code).run())

    def test_service_output_budget_and_explicit_execution_statistics(self):
        code = self.code([op('PUSH',0),op('PRINT'),op('PUSH',1),op('RETURN')], ['hello',0])
        with tempfile.TemporaryDirectory() as directory:
            executable = build_native(code, Path(directory)/'program')
            def run(*args):
                return subprocess.run([str(executable),*args],capture_output=True,text=True,timeout=10)
            ordinary = run()
            self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
            self.assertEqual(ordinary.stderr, '')
            limited = run('--output-budget','4','--report-stats')
            self.assertEqual(limited.returncode, 70)
            self.assertEqual(limited.stdout, '')
            self.assertIn('Output budget exceeded', limited.stderr)
            exact = run('--output-budget','5','--report-stats')
            self.assertEqual(exact.returncode, 0, exact.stderr)
            self.assertEqual(json.loads(exact.stdout), 'hello')
            self.assertEqual(json.loads(exact.stderr), {'nativeStats':1,'steps':4})
            buffered = run('--output-budget','5','--report-stats','--buffered-output')
            self.assertEqual(buffered.returncode, 0, buffered.stderr)
            self.assertEqual(buffered.stdout, exact.stdout)
            self.assertEqual(buffered.stderr, exact.stderr)
            self.assertEqual(run('--output-budget','4','--buffered-output').returncode, 70)
            if Path('/dev/full').exists():
                with open('/dev/full', 'wb') as sink:
                    failed = subprocess.run([str(executable),'--buffered-output','--report-stats'],
                                            stdout=sink,stderr=subprocess.PIPE,text=True,timeout=10)
                self.assertEqual(failed.returncode, 70)
                self.assertIn('Output write failed', failed.stderr)
            for args in [('--output-budget',),('--output-budget','0'),('--output-budget','64000001'),
                         ('--output-budget','-1'),('--output-budget','18446744073709551616')]:
                with self.subTest(args=args):
                    self.assertEqual(run(*args).returncode, 64)

    def test_calls_recursion_and_frame_stack_boundaries(self):
        code = self.code([op('PUSH',0),op('CALL','factorial'),op('PRINT'),op('PUSH',1),op('RETURN')], [7,1])
        code['functions']['factorial'] = dict(params=['int'], locals=1, result='int', instructions=[
            op('LOAD',0),op('PUSH',1),op('LE'),op('JUMP_IF_FALSE',6),op('PUSH',1),op('RETURN'),
            op('LOAD',0),op('LOAD',0),op('PUSH',1),op('SUB'),op('CALL','factorial'),op('MUL'),op('RETURN')])
        result, _ = self.execute(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([json.loads(result.stdout)], VM(code).run())

    def test_float64_operations_and_bit_exact_output_match_vm(self):
        constants = [-17.5, 5.25, 0.0, -0.0, 5e-324, -5e-324,
                     sys.float_info.max, sys.float_info.min, 1.0000000000000002]
        instructions = []
        for index in range(len(constants)):
            instructions += [op('PUSH',index),op('PRINT')]
        for operation in ('ADD', 'SUB', 'MUL', 'DIV', 'MOD', 'EQ', 'NE', 'LT', 'LE', 'GT', 'GE'):
            instructions += [op('PUSH',0),op('PUSH',1),op(operation),op('PRINT')]
        instructions += [op('PUSH',2),op('NEG'),op('PRINT'),op('PUSH',2),op('RETURN')]
        code = self.code(instructions, constants)
        result, _ = self.execute(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = [json.loads(line) for line in result.stdout.splitlines()]
        expected = VM(code).run()
        self.assertEqual(actual, expected)
        for a, b in zip(actual, expected):
            self.assertIs(type(a), type(b))
            if type(a) is float:
                self.assertEqual(struct.pack('>d',a), struct.pack('>d',b))

    def test_unicode_strings_and_dynamic_aliases_match_vm(self):
        constants = ['雪\x00"\\\n😀', 'é', 0, 1, 5, '\ud800']
        body = [op('PUSH',0),op('PUSH',1),op('CONCAT'),op('BIND',0),
                op('LOAD',0),op('DUP'),op('PRINT'),op('BUILTIN','len'),op('PRINT'),
                op('LOAD',0),op('PUSH',3),op('PUSH',4),op('BUILTIN','slice'),op('PRINT'),
                op('LOAD',0),op('PUSH',2),op('INDEX'),op('PRINT'),op('PUSH',5),op('PRINT')]
        for operation in ('EQ','NE','LT','LE','GT','GE'):
            body += [op('LOAD',0),op('PUSH',1),op(operation),op('PRINT')]
        body += [op('PUSH',2),op('RETURN')]
        code = self.code(body, constants, locals=1)
        result, _ = self.execute(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()], VM(code).run())

    def test_discarded_string_temporaries_and_unwind_release_storage(self):
        code = self.code([op('PUSH',0),op('PUSH',1),op('CONCAT'),op('DUP2'),op('RETURN')], ['a','b'])
        # DUP2 needs two owned operands; arrange a temporary below another value.
        code['functions']['main'].update(locals=1, instructions=[
            op('STACK_DEPTH'),op('BIND',0),op('PUSH',0),op('PUSH',1),op('CONCAT'),
            op('PUSH',0),op('PUSH',1),op('CONCAT'),op('DUP2'),op('UNWIND',0),
            op('PUSH',0),op('PUSH',1),op('CONCAT'),op('RETURN')])
        result, _ = self.execute(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_array_equality_tracks_shared_pairs_without_exponential_work(self):
        body = []
        for slot in (0,1):
            body += [op('PUSH',0),op('ARRAY',dict(type='int[]',count=1)),op('BIND',slot)]
        typ = 'int[]'
        for _ in range(24):
            typ += '[]'
            for slot in (0,1):
                body += [op('LOAD',slot),op('DUP'),op('ARRAY',dict(type=typ,count=2)),op('BIND',slot)]
        body += [op('LOAD',0),op('LOAD',1),op('EQ'),op('PRINT'),op('PUSH',0),op('RETURN')]
        code = self.code(body,[1],locals=2)
        result, _ = self.execute(code)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual([json.loads(result.stdout)],VM(code).run())

    def test_array_type_checks_and_output_budget_reject_before_printing(self):
        code = self.code([op('PUSH',0),op('ARRAY',dict(type='bool[]',count=1)),op('RETURN')],[1])
        result, _ = self.execute(code)
        self.assertEqual(result.returncode,70)
        self.assertIn('Operand type mismatch',result.stderr)
        body=[op('PUSH',0),op('ARRAY',dict(type='int[]',count=1))]
        typ='int[]'
        for _ in range(16):
            typ+='[]'
            body += [op('DUP'),op('ARRAY',dict(type=typ,count=2))]
        body += [op('PRINT'),op('PUSH',0),op('RETURN')]
        result, _ = self.execute(self.code(body,[1]))
        self.assertEqual(result.returncode,70)
        self.assertIn('Output budget exceeded',result.stderr)
        self.assertEqual(result.stdout,'')

    def test_record_nominal_types_and_unknown_fields_remain_checked(self):
        for mode,message in [('call','Operand record type mismatch'),
                             ('equal','Equality operands have different types'),
                             ('field','Invalid record field access')]:
            with self.subTest(mode=mode):
                body=[op('PUSH',0),op('RECORD',dict(type='@0:1',fields=['value']))]
                if mode=='call': body += [op('CALL','accept')]
                elif mode=='equal':
                    body += [op('PUSH',0),op('RECORD',dict(type='@0:2',fields=['value'])),op('EQ')]
                else: body += [op('FIELD','missing')]
                body += [op('RETURN')]
                code=self.code(body,[1])
                code['records'].update({'@0:1':{'value':'int'},'@0:2':{'value':'int'}})
                code['functions']['accept']=dict(params=['@0:2'],locals=1,result='unit',
                    instructions=[op('PUSH',0),op('RETURN')])
                result,_=self.execute(code)
                self.assertEqual(result.returncode,70,result.stderr)
                self.assertEqual(json.loads(result.stderr)['error'],message)

    def test_result_and_option_propagation_preserve_caller_operands(self):
        from tests.test_native_propagation_vm import fixture
        for optional in (False,True):
            for failed in (False,True):
                with self.subTest(optional=optional,failed=failed):
                    code=fixture(failed=failed,optional=optional,prefix=True)
                    result,_=self.execute(code)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_enum_projection_requires_matching_variant(self):
        code=self.code([op('PUSH',0),op('ENUM',dict(type='@0:1',variant='First',fields=['value'])),
                        op('ENUM_GET',dict(type='@0:1',variant='Second',field='value')),op('RETURN')],[1])
        code['enums']={'@0:1':{'First':{'value':'int'},'Second':{'value':'int'}}}
        result,_=self.execute(code)
        self.assertEqual(result.returncode,70,result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'],'Enum payload projection requires matching variant')

    def test_string_bounds_and_size_limits_are_checked(self):
        for body, constants, message in (
            ([op('PUSH',0),op('PUSH',1),op('INDEX'),op('RETURN')], ['雪',-1], 'Index out of bounds'),
            ([op('PUSH',0),op('PUSH',1),op('PUSH',2),op('BUILTIN','slice'),op('RETURN')],
             ['雪',0,2], 'Slice bounds out of range'),
            ([op('PUSH',0)] + [ins for _ in range(20) for ins in (op('DUP'),op('CONCAT'))] + [op('RETURN')],
             ['x'], 'String size budget exceeded'),
        ):
            with self.subTest(message=message):
                result, _ = self.execute(self.code(body,constants))
                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(json.loads(result.stderr)['error'], message)

    def test_runtime_errors_retain_original_instruction_location(self):
        for operation, values, message in (
            ('ADD', [(1 << 63)-1,1], 'Signed 64-bit integer overflow'),
            ('MUL', [1 << 62,4], 'Signed 64-bit integer overflow'),
            ('DIV', [-(1 << 63),-1], 'Signed 64-bit integer overflow'),
            ('DIV', [1,0], 'Division by zero'),
            ('ADD', [True,1], 'Operand type mismatch'),
            ('ADD', [1.0,1], 'Operand type mismatch'),
            ('ADD', [sys.float_info.max,sys.float_info.max], 'Float64 arithmetic exceeds finite range'),
            ('DIV', [1.0,-0.0], 'Division by zero'),
            ('MOD', [1.0,0.0], 'Division by zero'),
        ):
            with self.subTest(operation=operation, values=values):
                code = self.code([op('PUSH',0),op('PUSH',1),op(operation),op('RETURN')], values)
                result, mapping = self.execute(code)
                self.assertEqual(result.returncode, 70, result.stderr)
                error = json.loads(result.stderr)
                self.assertEqual((error['error'],error['function'],error['pc'],error['steps']), (message,0,2,3))
                self.assertEqual(mapping['format'], 'pixellang-native-source-map/2')
                function = mapping['functions'][error['function']]
                self.assertEqual(function['instructionCount'], len(code['functions'][function['name']]['instructions']))
                self.assertEqual(code['functions'][function['name']]['instructions'][error['pc']]['op'], operation)

    def test_instruction_budget_terminates_loop(self):
        code = self.code([op('JUMP',0)])
        result, _ = self.execute(code, steps=12345)
        self.assertEqual(result.returncode, 70)
        self.assertEqual(json.loads(result.stderr)['steps'], 12345)
        self.assertIn('Instruction budget exceeded', result.stderr)

    def test_signal_cancellation_stops_native_loop(self):
        code = self.code([op('PUSH',0),op('PRINT'),op('JUMP',2)])
        with tempfile.TemporaryDirectory() as directory:
            executable = build_native(code, Path(directory)/'program')
            process = subprocess.Popen([str(executable), '1000000000000'], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=5), 'native startup did not publish output')
                self.assertEqual(process.stdout.readline(), '0\n')
                process.terminate()
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 70, stderr)
                self.assertEqual(json.loads(stderr)['error'], 'Execution cancelled')
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate()

    def test_effect_checks_and_stack_underflow_remain_enforced(self):
        code = self.code([op('PUSH',0),op('PRINT'),op('PUSH',0),op('RETURN')])
        code['effectFunctions'] = []
        result, _ = self.execute(code)
        self.assertEqual(result.returncode, 70)
        self.assertIn('External effects are forbidden', result.stderr)
        result, _ = self.execute(self.code([op('POP'),op('PUSH',0),op('RETURN')]))
        self.assertEqual(result.returncode, 70)
        self.assertIn('Operand stack underflow', result.stderr)
        result, _ = self.execute(self.code([op('TRAP')]))
        self.assertEqual(result.returncode, 70)
        self.assertIn('Function reached unreachable terminator', result.stderr)

    def test_native_input_rejects_invalid_utf8_and_cancels_wait(self):
        code=self.code([op('PUSH',0),op('PRINT'),op('BUILTIN','input'),op('PRINT'),op('PUSH',0),op('RETURN')])
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],input=b'\xff',capture_output=True,timeout=10)
            self.assertEqual(result.returncode,70,result.stderr)
            self.assertIn(b'Invalid UTF-8',result.stderr)
            with subprocess.Popen([str(executable)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as process:
                # Keep stdin open and empty: input must be interruptible while waiting.
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout,selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=5),'native startup did not publish output')
                self.assertEqual(process.stdout.readline(),b'0\n')
                process.terminate()
                process.wait(timeout=3)
                self.assertEqual(process.returncode,70)
                self.assertEqual(json.loads(process.stderr.read())['error'],'Execution cancelled')
        code=self.code([op('BUILTIN','input'),op('RETURN')])
        code['effectFunctions']=[]
        result,_=self.execute(code)
        self.assertEqual(result.returncode,70,result.stderr)
        self.assertIn('External effects are forbidden',result.stderr)

    def test_json_float_spellings_match_shortest_roundtrip_decimal(self):
        import random
        import math
        values=[0.0,-0.0,0.1,1e-4,1e-5,1e15,1e16,5e-324,1.7976931348623157e308,1.0000000000000002]
        rng=random.Random(20261019)
        while len(values)<266:
            value=struct.unpack('>d',rng.getrandbits(64).to_bytes(8,'big'))[0]
            if math.isfinite(value):values.append(value)
        body=[]
        for index in range(len(values)):
            body += [op('PUSH',index),op('BUILTIN','jsonFrom'),op('BUILTIN','jsonStringify'),op('PRINT')]
        body += [op('PUSH',0),op('RETURN')]
        result,_=self.execute(self.code(body,values))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],[repr(value) for value in values])

    def test_json_rejects_equality_and_invalid_scalar_length(self):
        for operation in ('EQ','len'):
            body=[op('BUILTIN','jsonNull')]
            body += [op('DUP'),op('EQ')] if operation=='EQ' else [op('BUILTIN','len')]
            body += [op('RETURN')]
            result,_=self.execute(self.code(body))
            self.assertEqual(result.returncode,70,result.stderr)
            self.assertIn('Type does not support value equality' if operation=='EQ' else 'JSON length requires',result.stderr)

    def test_json_serialization_bounds_shared_subtrees_and_escaped_output(self):
        for copies,levels,expected in [(2,17,'JSON structure budget exceeded'),(1,130,'JSON structure budget exceeded')]:
            with self.subTest(copies=copies):
                body=[op('BUILTIN','jsonNull'),op('BIND',0)]
                for _ in range(levels):
                    body += [op('LOAD',0)]*copies+[op('ARRAY',{'type':'json[]','count':copies}),op('BUILTIN','jsonFrom'),op('BIND',0)]
                body += [op('PUSH',0),op('RETURN')]
                result,_=self.execute(self.code(body,locals=1))
                self.assertEqual(result.returncode,70,result.stderr)
                self.assertIn(expected,result.stderr)
        body=[op('PUSH',0)]
        for _ in range(18):body += [op('DUP'),op('CONCAT')]
        body += [op('BUILTIN','jsonFrom'),op('RETURN')]
        result,_=self.execute(self.code(body,['\0']))
        self.assertEqual(result.returncode,70,result.stderr)
        self.assertIn('JSON output exceeds one million characters',result.stderr)

    def test_json_parser_generated_documents_and_exact_structure_boundaries(self):
        import random
        from pixellang import jsonvalue
        from pixellang.model import PixelError
        rng=random.Random(331)
        def document(depth=0):
            leaf=rng.choice([None,True,False,0,-17,0.1,'雪😀','quote"\\\n'])
            if depth==3:return leaf
            kind=rng.randrange(3)
            if kind==0:return [document(depth+1) for _ in range(rng.randrange(4))]
            if kind==1:return {f'key{index}雪':document(depth+1) for index in range(rng.randrange(4))}
            return leaf
        texts=[json.dumps(document(),ensure_ascii=rng.choice([False,True])) for _ in range(40)]
        texts += ['{'+json.dumps('雪'*350000,ensure_ascii=False)+':0}']
        texts += ['['*depth+'0'+']'*depth for depth in (128,129)]
        texts += ['['+','.join(['0']*count)+']' for count in (99999,100000)]
        texts += ['{"a":1,"a":2}','"\\ud800"','[1,]','{"a":[{},true]','false trailing']
        code=self.code([op('BUILTIN','input'),op('RESULT_BUILTIN',{'name':'jsonParse','result':'Result<json,Error>'}),
                        op('PRINT'),op('PUSH',0),op('RETURN')])
        code['enums']['Result<json,Error>']={'Ok':{'value':'json'},'Err':{'error':'Error'}}
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            for text in texts:
                with self.subTest(size=len(text),prefix=text[:40]):
                    result=subprocess.run([str(executable)],input=text,capture_output=True,text=True,timeout=10)
                    self.assertEqual(result.returncode,0,result.stderr)
                    actual=json.loads(result.stdout)
                    try:expected=jsonvalue.stringify(jsonvalue.parse(text))
                    except PixelError as error:
                        self.assertEqual(actual['variant'],'Err')
                        self.assertEqual(actual['fields']['error']['code'],error.code)
                    else:
                        self.assertEqual(actual,{'variant':'Ok','fields':{'value':expected}})

    def test_native_remaining_builtins_reject_invalid_arguments_and_repeat_overflow(self):
        cases=[([op('PUSH',0),op('PUSH',1),op('BUILTIN','split')],['text',''],'split separator cannot be empty'),
               ([op('PUSH',0),op('BUILTIN','unicodeCategory')],[''],'Expected one Unicode scalar'),
               ([op('PUSH',0),op('BUILTIN','unicodeCategory')],['ab'],'Expected one Unicode scalar'),
               ([op('PUSH',0),op('ARRAY',{'type':'int[]','count':1}),op('PUSH',1),op('BUILTIN','arrayRepeat')],[1,-1],'Array repeat count must be nonnegative'),
               ([op('PUSH',0),op('ARRAY',{'type':'int[]','count':1}),op('PUSH',1),op('BUILTIN','arrayRepeat')],[1,9223372036854775807],'Array storage budget exceeded'),
               ([op('ARRAY',{'type':'int[]','count':0}),op('BUILTIN','string')],[0],'string conversion expects a scalar')]
        for body,constants,message in cases:
            with self.subTest(message=message):
                result,_=self.execute(self.code(body+[op('RETURN')],constants))
                self.assertEqual(result.returncode,70,result.stderr)
                self.assertIn(message,result.stderr)

    def test_generated_unicode_categories_are_current_and_reproducible(self):
        import unicodedata
        from pixellang.unicode_data import UNICODE_VERSION
        if unicodedata.unidata_version!=UNICODE_VERSION:
            self.skipTest('Regeneration requires a Python host with Unicode '+UNICODE_VERSION)
        root=Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable,str(root/'scripts/generate_native_unicode.py'),'--check'],check=True,capture_output=True,text=True,timeout=10)

    def test_typed_json_decode_recursive_budget_and_ambiguous_options(self):
        tree=None
        for _ in range(70):tree={'next':tree}
        code=self.code([op('PUSH',0),op('RESULT_DECODE',{'result':'Result<@0:1,Error>'}),op('PRINT'),op('PUSH',1),op('RETURN')],[json.dumps(tree),0])
        code['records']['@0:1']={'next':'Option<@0:1>'}
        code['enums']['Option<@0:1>']={'Some':{'value':'@0:1'},'None':{}}
        code['enums']['Result<@0:1,Error>']={'Ok':{'value':'@0:1'},'Err':{'error':'Error'}}
        result,_=self.execute(code)
        self.assertEqual(result.returncode,0,result.stderr)
        error=json.loads(result.stdout)['fields']['error']
        self.assertEqual(error['code'],'json.structure_limit')
        self.assertEqual(error['operation'],'jsonDecode')
        self.assertTrue(error['path'].startswith('$.next'))
        code=self.code([op('PUSH',0),op('RESULT_DECODE',{'result':'Result<Option<Option<int>>,Error>'}),op('PRINT'),op('PUSH',1),op('RETURN')],['null',0])
        code['enums']['Option<int>']={'Some':{'value':'int'},'None':{}}
        code['enums']['Option<Option<int>>']={'Some':{'value':'Option<int>'},'None':{}}
        code['enums']['Result<Option<Option<int>>,Error>']={'Ok':{'value':'Option<Option<int>>'},'Err':{'error':'Error'}}
        result,_=self.execute(code)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['fields']['error']['code'],'json.unsupported_type')

    def test_unimplemented_features_fail_before_creating_output(self):
        code = self.code([op('BUILTIN','programArgs'),op('RETURN')])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(UnsupportedNativeProgram, 'builtin programArgs'):
                build_native(code, Path(directory)/'program')
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(emit_c(self.code([op('PUSH',0),op('RETURN')])),
                         emit_c(self.code([op('PUSH',0),op('RETURN')])))

    def test_callable_effect_signature_and_readonly_checks(self):
        for mode,message in [('effect','Pure function cannot call an effect fn'),
                             ('signature','Callable signature mismatch'),
                             ('capture','Captured values are readonly')]:
            with self.subTest(mode=mode):
                prefix=[op('FUNCTION','target')]
                target=dict(params=[],locals=0,result='int',instructions=[op('PUSH',0),op('RETURN')])
                signature='fn()->(int)'
                if mode=='capture':
                    prefix=[op('PUSH',0),op('BIND',0),op('CAPTURE_VALUE',{'slot':0,'type':'int'}),op('CLOSURE','target')]
                    target.update(captures=['int'],locals=1,instructions=[op('PUSH',0),op('STORE',0),op('LOAD',0),op('RETURN')])
                elif mode=='effect':
                    signature='effect_fn()->(int)'
                else:
                    signature='fn()->(bool)'
                code=self.code(prefix+[op('INVOKE',signature),op('RETURN')],locals=1)
                code['functions']['target']=target
                code['effectFunctions']=['target'] if mode=='effect' else ['main']
                result,_=self.execute(code)
                self.assertEqual(result.returncode,70,result.stderr)
                self.assertIn(message,result.stderr)

    def test_empty_function_array_is_not_comparable(self):
        code=self.code([op('ARRAY',{'type':'fn()->(int)[]','count':0}),op('DUP'),op('EQ'),op('RETURN')])
        result,_=self.execute(code)
        self.assertEqual(result.returncode,70,result.stderr)
        self.assertIn('Type does not support value equality',result.stderr)

    def test_text_builtins_unicode_empty_and_repetitive_input(self):
        constants=[]; body=[]
        def push(value):
            constants.append(value); body.append(op('PUSH',len(constants)-1))
        for text,old,new in [('aaaaa','aa','雪'),('abc','','😀'),('','','x'),
                             ('abc','x',''),('雪\x00雪','雪','😀'),
                             ('a'*50000+'b','a'*5000+'b','done')]:
            for value in (text,old,new): push(value)
            body.extend([op('BUILTIN','replace'),op('PRINT')])
        for text in ['', '\u2003\x1c雪😀\u3000', '\u200bkeep\u200b', ' \t\n']:
            push(text); body.extend([op('BUILTIN','trim'),op('PRINT')])
        for items,separator in [([],','),([''],'😀'),(['雪','','\x00'],'😀')]:
            for value in items: push(value)
            body.append(op('ARRAY',{'type':'str[]','count':len(items)}))
            push(separator); body.extend([op('BUILTIN','join'),op('PRINT')])
        push(True); body.extend([op('BUILTIN','assert'),op('RETURN')])
        code=self.code(body,constants)
        result,_=self.execute(code)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_text_builtins_reject_oversize_results_and_failed_assertion(self):
        cases=[(self.code([op('PUSH',0),op('PUSH',1),op('PUSH',2),op('BUILTIN','replace'),op('RETURN')],['a'*1000,'','b'*1000]),'String size budget exceeded'),
               (self.code([op('PUSH',0),op('PUSH',0),op('ARRAY',{'type':'str[]','count':2}),op('PUSH',1),op('BUILTIN','join'),op('RETURN')],['a'*500001,'']),'String size budget exceeded'),
               (self.code([op('PUSH',0),op('BUILTIN','assert'),op('RETURN')],[False]),'Assertion failed')]
        for code,message in cases:
            with self.subTest(message=message):
                result,_=self.execute(code)
                self.assertEqual(result.returncode,70,result.stderr)
                self.assertIn(message,result.stderr)
                self.assertEqual(result.stdout,'')

    def test_binary_encoding_and_crc_match_vm(self):
        constants=['','123456789','\x00\x7f\u0080\u07ff\u0800雪😀\U0010ffff',0.0,-0.0,5e-324,sys.float_info.max]
        body=[]
        for index in range(3):
            body += [op('PUSH',index),op('BUILTIN','utf8Encode'),op('DUP'),op('PRINT'),op('BUILTIN','crc32'),op('PRINT')]
        for index in range(3,len(constants)):
            body += [op('PUSH',index),op('BUILTIN','float64Bytes'),op('DUP'),op('PRINT'),op('BUILTIN','crc32'),op('PRINT')]
        body += [op('PUSH',0),op('RETURN')]
        code=self.code(body,constants)
        result,_=self.execute(code)
        self.assertEqual(result.returncode,0,result.stderr)
        output=[json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(output,VM(code).run())
        self.assertEqual(output[3],0xcbf43926)

    def test_binary_encoding_rejects_invalid_scalars_and_bytes(self):
        for value in ('\ud800','\udfff',-1,256):
            with self.subTest(value=repr(value)):
                body=[op('PUSH',0)]
                if isinstance(value,int):
                    body += [op('ARRAY',{'type':'int[]','count':1}),op('BUILTIN','crc32')]
                    message='Byte values must be integers from 0 to 255'
                else:
                    body += [op('BUILTIN','utf8Encode')]
                    message='Text contains an invalid Unicode scalar'
                code=self.code(body+[op('RETURN')],[value])
                result,_=self.execute(code)
                self.assertEqual(result.returncode,70,result.stderr)
                self.assertIn(message,result.stderr)

    def test_unsupported_checked_builtin_is_rejected_before_build(self):
        code=self.code([op('PUSH',0),op('RESULT_BUILTIN',{'name':'listDirectory','result':'Result<json,Error>'}),op('RETURN')],['1'])
        code['enums']['Result<json,Error>']={'Ok':{'value':'json'},'Err':{'error':'Error'}}
        with self.assertRaisesRegex(UnsupportedNativeProgram,'result builtin listDirectory'):
            emit_c(code)

    def test_persistent_append_crosses_tree_boundaries_and_keeps_snapshot(self):
        body=[op('ARRAY',{'type':'int[]','count':0}),op('DUP'),op('BIND',0),op('BIND',2),op('PUSH',0),op('BIND',1)]
        loop=len(body)
        body += [op('LOAD',1),op('PUSH',2),op('LT')]
        exit_jump=len(body); body.append(op('JUMP_IF_FALSE',0))
        body += [op('LOAD',0),op('LOAD',1),op('VALUE_COLLECTION',{'method':'append','result':'unit'}),op('POP'),op('BIND',0),
                 op('LOAD',1),op('PUSH',1),op('ADD'),op('BIND',1),op('LOAD',1),op('PUSH',3),op('EQ')]
        skip=len(body);body.append(op('JUMP_IF_FALSE',0))
        body += [op('LOAD',0),op('BIND',2)]
        body[skip]['arg']=len(body)
        body.append(op('JUMP',loop));body[exit_jump]['arg']=len(body)
        body += [op('LOAD',0),op('BUILTIN','len'),op('PRINT'),op('LOAD',2),op('BUILTIN','len'),op('PRINT')]
        for index in range(4,8):body += [op('LOAD',0),op('PUSH',index),op('INDEX'),op('PRINT')]
        body += [op('PUSH',0),op('RETURN')]
        code=self.code(body,[0,1,70000,1024,31,32,32767,32768],locals=3)
        result,_=self.execute(code,steps=2000000)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code,max_steps=2000000).run())

    def test_collection_operations_reject_mismatched_types_even_when_empty(self):
        for method in ('extend','pop'):
            with self.subTest(method=method):
                body=[op('ARRAY',{'type':'int[]','count':0})]
                if method=='extend':body += [op('ARRAY',{'type':'str[]','count':0})]
                result_type='unit' if method=='extend' else 'Option<str>'
                body += [op('VALUE_COLLECTION',{'method':method,'result':result_type}),op('RETURN')]
                code=self.code(body)
                if method=='pop':code['enums']['Option<str>']={'Some':{'value':'str'},'None':{}}
                result,_=self.execute(code)
                self.assertEqual(result.returncode,70,result.stderr)
                self.assertIn('Operand array type mismatch',result.stderr)

    def test_map_remove_rejects_wrong_result_type_for_absent_key(self):
        code=self.code([op('MAP',{'type':'map<int,int>','count':0}),op('PUSH',0),
                        op('VALUE_COLLECTION',{'method':'remove','result':'Option<str>'}),op('RETURN')])
        code['enums']['Option<str>']={'Some':{'value':'str'},'None':{}}
        result,_=self.execute(code)
        self.assertEqual(result.returncode,70,result.stderr)
        self.assertIn('Collection method result disagrees with element type',result.stderr)

    def test_update_paths_reject_bounds_types_and_unknown_fields(self):
        for operation in ('VALUE_CHECK','VALUE_SET'):
            for index in (-1,1,'bad'):
                with self.subTest(operation=operation,index=index):
                    body=[op('PUSH',0),op('ARRAY',{'type':'int[]','count':1}),op('PUSH',1)]
                    if operation=='VALUE_SET':body.append(op('PUSH',0))
                    body += [op(operation,[{'index':True}]),op('PUSH',0),op('RETURN')]
                    result,_=self.execute(self.code(body,[7,index]))
                    self.assertEqual(result.returncode,70,result.stderr)
                    self.assertIn('Operand type mismatch' if index=='bad' else 'Value update index out of bounds',result.stderr)
        for field,replacement,message in [('missing',1,'Unknown value update field'),('value','bad','Operand type mismatch')]:
            code=self.code([op('PUSH',0),op('RECORD',{'type':'@0:1','fields':['value']}),op('PUSH',1),
                            op('VALUE_SET',[{'field':field}]),op('RETURN')],[7,replacement])
            code['records']['@0:1']={'value':'int'}
            result,_=self.execute(code)
            self.assertEqual(result.returncode,70,result.stderr)
            self.assertIn(message,result.stderr)

    def test_recursive_call_limit_reports_callsite_before_native_stack_overflow(self):
        code=self.code([op('CALL','main'),op('RETURN')])
        result,_=self.execute(code)
        self.assertEqual(result.returncode,70,result.stderr)
        error=json.loads(result.stderr)
        self.assertEqual(error['error'],'Call stack limit exceeded')
        self.assertEqual(error['pc'],0)
        self.assertEqual(error['steps'],256)

    def test_build_specific_call_depth_is_bounded_and_enforced(self):
        code=self.code([op('CALL','main'),op('RETURN')])
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'program'
            for depth in (0,1025,True,1.5):
                with self.subTest(depth=depth),self.assertRaises(ValueError):
                    build_native(code,output,max_depth=depth)
            executable=build_native(code,output,max_depth=17)
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,70,result.stderr)
            self.assertEqual(json.loads(result.stderr)['steps'],17)
            self.assertEqual(json.loads(output.with_name('program.map.json').read_text())['maxCallDepth'],17)

    def test_inout_dynamic_calls_pending_reads_and_unwind_release(self):
        from tests.test_inout_bytecode import InoutBytecodeTests
        fixtures=[InoutBytecodeTests().code(dynamic=flag) for flag in (False,True)]
        pending=InoutBytecodeTests().code()
        pending['functions']['add']=dict(params=['inout<int>','int'],locals=2,result='unit',instructions=[
            op('LOAD',0),op('LOAD',1),op('ADD'),op('STORE',0),op('PUSH',0),op('RETURN')])
        pending['functions']['edit']['instructions']=[op('ACCESS',{'slot':0,'type':'int','path':[]}),op('LOAD',0),
            op('CALL','add'),op('POP'),op('PUSH',0),op('RETURN')]
        fixtures.append(pending)
        unwind=self.code([op('PUSH',0),op('BIND',0),op('STACK_DEPTH'),op('BIND',1),
            op('ACCESS',{'slot':0,'type':'int','path':[]}),op('UNWIND',1),op('PUSH',0),op('STORE',0),op('LOAD',0),op('PRINT'),op('RETURN')],locals=2)
        # Keep a unit return operand after printing.
        unwind['functions']['main']['instructions'].insert(-1,op('PUSH',0))
        fixtures.append(unwind)
        for code in fixtures:
            result,_=self.execute(code)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_inout_conflicts_expiry_and_duplicate_claim_are_rejected(self):
        from tests.test_inout_bytecode import InoutBytecodeTests
        for mode,message in [('overlap','Overlapping inout accesses'),('write','Cannot write a root'),
                             ('expired','Expired inout access'),('duplicate','fresh scoped borrow')]:
            with self.subTest(mode=mode):
                code=InoutBytecodeTests().code()
                body=code['functions']['main']['instructions']
                if mode=='overlap':body.insert(3,op('ACCESS',{'slot':0,'type':'int','path':[]}))
                elif mode=='write':body[3:3]=[op('PUSH',0),op('STORE',0)]
                elif mode=='expired':
                    body.insert(3,op('DUP'));body[6:6]=[op('CALL','edit')]
                else:
                    body.insert(3,op('DUP'))
                    code['functions']['edit'].update(params=['inout<int>','inout<int>'],locals=2)
                result,_=self.execute(code)
                self.assertEqual(result.returncode,70,result.stderr)
                self.assertIn(message,result.stderr)

    def test_pure_helper_return_preserves_pending_access_and_exclusive_lock(self):
        from tests.test_inout_bytecode import InoutBytecodeTests
        for conflict in (False, True):
            with self.subTest(conflict=conflict):
                code=InoutBytecodeTests().code()
                code['functions']['helper']=dict(params=[],locals=0,result='int',
                    instructions=[op('PUSH',0),op('RETURN')])
                body=code['functions']['main']['instructions']
                body[3:3]=[op('CALL','helper'),op('POP')]
                if conflict: body[5:5]=[op('PUSH',0),op('STORE',0)]
                result,_=self.execute(code)
                if conflict:
                    self.assertEqual(result.returncode,70,result.stderr)
                    self.assertIn('Cannot write a root',result.stderr)
                else:
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_map_duplicate_keys_missing_lookup_and_key_type_checks(self):
        for constants,typ in [([1,2,1,3],'map<int,int>'),([0.0,2,-0.0,3],'map<float64,int>')]:
            code=self.code([op('PUSH',i) for i in range(4)]+[op('MAP',{'type':typ,'count':4}),op('RETURN')],constants)
            result,_=self.execute(code)
            self.assertEqual(result.returncode,70,result.stderr)
            self.assertIn('Duplicate map key',result.stderr)
        for key,operation,message in [(9,'INDEX','Missing map key'),(True,'has','Operand type mismatch')]:
            body=[op('ARRAY',{'type':'int[]','count':0}),op('POP'),op('MAP',{'type':'map<int,int>','count':0}),op('PUSH',0)]
            body += [op('INDEX') if operation=='INDEX' else op('BUILTIN',operation),op('RETURN')]
            result,_=self.execute(self.code(body,[key]))
            self.assertEqual(result.returncode,70,result.stderr)
            self.assertIn(message,result.stderr)

    def test_map_hash_index_and_equality_across_insertion_orders(self):
        order=[(i*73)%256 for i in range(256)]
        body=[]
        for slot,sequence in enumerate((order,list(reversed(order)))):
            for key in sequence:body += [op('PUSH',key),op('PUSH',255-key)]
            body += [op('MAP',{'type':'map<int,int>','count':512}),op('BIND',slot)]
        body += [op('LOAD',0),op('LOAD',1),op('EQ'),op('PRINT'),op('LOAD',0),op('BUILTIN','keys'),op('PRINT')]
        for key in (0,31,63,127,255):body += [op('LOAD',0),op('PUSH',key),op('INDEX'),op('PRINT')]
        body += [op('PUSH',0),op('RETURN')]
        code=self.code(body,list(range(256)),locals=2)
        result,_=self.execute(code)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_map_path_inserts_missing_leaf_key(self):
        code=self.code([op('MAP',{'type':'map<int,int>','count':0}),op('PUSH',0),op('PUSH',0),
                        op('VALUE_SET',[{'index':True}]),op('PRINT'),op('PUSH',0),op('RETURN')])
        result,_=self.execute(code)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_map_path_rejects_missing_ancestor_and_missing_inout_key(self):
        for borrow in (False,True):
            with self.subTest(borrow=borrow):
                body=[op('MAP',{'type':'map<int,int[]>','count':0})]
                if borrow:
                    body += [op('BIND',0),op('PUSH',0),op('ACCESS',{'slot':0,'type':'int[]','path':[{'index':True}]})]
                    message='inout map key does not exist'
                else:
                    body += [op('PUSH',0),op('PUSH',0),op('PUSH',0),op('VALUE_SET',[{'index':True},{'index':True}])]
                    message='Value update map key does not exist'
                result,_=self.execute(self.code(body+[op('RETURN')],locals=1))
                self.assertEqual(result.returncode,70,result.stderr)
                self.assertIn(message,result.stderr)
