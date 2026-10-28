"""Generated service acceptance must never substitute the installed compiler."""
import hashlib
import unittest
import tempfile
from pathlib import Path
from pixellang.native_codegen import build_native
from unittest.mock import patch

from pixellang.bootstrap import normalized
from pixellang.compiler_service import CompilerService, Limits
from scripts.verify_generated_compiler import artifact_suite
from tests.bytecode_helpers import program


class GeneratedArtifactSelectionTests(unittest.TestCase):
    def test_service_and_budget_overrides_use_candidate_with_installed_loader_blocked(self):
        code=program([])
        del code['functions']['subject']
        expected=hashlib.sha256(normalized(code).encode()).hexdigest()
        suite,_=artifact_suite(code)
        case=next(test for test in suite if type(test).__name__=='GeneratedCompilerServiceTests')
        with patch.object(CompilerService,'installed',side_effect=AssertionError('Installed compiler substituted')):
            type(case).setUpClass()
            case.setUp()
            self.assertEqual(case.service.compiler_sha256,expected)
            self.assertIsNotNone(case.service.vm)
            limited=case.make_service(limits=Limits(file_bytes=10))
            self.assertEqual(limited.compiler_sha256,expected)
            self.assertEqual(limited.limits.file_bytes,10)

    def test_native_service_uses_bound_candidate_and_rejects_other_bytecode(self):
        code=program([])
        del code['functions']['subject']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'candidate',optimization=0)
            with patch.object(CompilerService,'installed',side_effect=AssertionError('Installed compiler substituted')):
                suite,_=artifact_suite(code,native_executable=executable)
                case=next(test for test in suite if type(test).__name__=='GeneratedCompilerServiceTests')
                type(case).setUpClass()
                case.setUp()
                self.assertIsNone(case.service.vm)
                self.assertEqual(case.service.native.executable,executable.resolve())
                self.assertEqual(case.make_service(limits=Limits(file_bytes=10)).limits.file_bytes,10)
                import copy
                changed=copy.deepcopy(code)
                changed['functions']['main']['instructions'][0]['arg']=1
                with self.assertRaisesRegex(ValueError,'does not match'):
                    artifact_suite(changed,native_executable=executable)
