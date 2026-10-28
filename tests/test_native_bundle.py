import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from pixellang.bootstrap import normalized
from pixellang.compiler_service import CompilerService
from pixellang.native_bundle import load_bundle
from pixellang.native_codegen import build_native
from tests.bytecode_helpers import program,op


@unittest.skipUnless(shutil.which('clang'),'C compiler required')
class NativeBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory()
        cls.root=Path(cls.directory.name)
        code=program([])
        code['constants']=[{'type':'int','value':0}]
        code['functions']={'main':dict(params=[],locals=0,result='unit',instructions=[op('PUSH',0),op('RETURN')])}
        cls.code=code
        cls.executable=build_native(code,cls.root/'compiler-native',optimization=0)
        (cls.root/'compiler.json').write_text(normalized(code))
        cls.manifest=dict(protocolVersion=1,sha256=hashlib.sha256((cls.root/'compiler.json').read_bytes()).hexdigest(),native=dict(
            file='compiler-native',platform=sys.platform,machine=platform.machine(),
            sha256=hashlib.sha256(cls.executable.read_bytes()).hexdigest()))

    @classmethod
    def tearDownClass(cls):cls.directory.cleanup()

    def setUp(self):
        (self.root/'compiler-manifest.json').write_text(json.dumps(self.manifest))

    def test_verified_bundle_selects_native_execution_without_vm(self):
        with patch('pixellang.compiler_service.VM',side_effect=AssertionError('Interpreter fallback')):
            service=CompilerService.from_bundle(self.root)
        self.assertIsNone(service.vm)
        self.assertEqual(service.native.executable,self.executable)

    def test_compact_source_map_rejects_wrong_function_identity_or_size(self):
        path=self.root/'compiler-native.map.json'
        original=path.read_bytes()
        mapping=json.loads(original)
        try:
            for change in [dict(format='pixellang-native-source-map/1'),
                           dict(functions=[dict(name='missing',instructionCount=3)]),
                           dict(functions=[dict(name='main',instructionCount=99)]),
                           dict(functions=[])]:
                with self.subTest(change=change):
                    path.write_text(json.dumps({**mapping,**change}))
                    with self.assertRaises(ValueError):load_bundle(self.root)
        finally:path.write_bytes(original)

    def test_missing_or_foreign_native_identity_is_rejected(self):
        for change in [dict(native=None),dict(native={**self.manifest['native'],'machine':'other'}),
                       dict(native={**self.manifest['native'],'file':'../compiler-native'})]:
            with self.subTest(change=change):
                (self.root/'compiler-manifest.json').write_text(json.dumps({**self.manifest,**change}))
                with self.assertRaises(ValueError):load_bundle(self.root)

    def test_changed_bytecode_is_rejected_before_execution(self):
        path=self.root/'compiler.json';original=path.read_bytes()
        try:
            path.write_bytes(original+b' ')
            with self.assertRaisesRegex(ValueError,'bytecode checksum'):load_bundle(self.root)
        finally:path.write_bytes(original)

    def test_changed_executable_is_rejected_before_execution(self):
        original=self.executable.read_bytes()
        try:
            self.executable.write_bytes(original+b'changed')
            with self.assertRaisesRegex(ValueError,'executable checksum'):load_bundle(self.root)
        finally:self.executable.write_bytes(original)

    def test_map_binding_must_match_bytecode(self):
        path=self.root/'compiler-native.map.json';original=path.read_bytes()
        try:
            data=json.loads(original);data['bytecodeSha256']='wrong';path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'different bytecode'):load_bundle(self.root)
        finally:path.write_bytes(original)
