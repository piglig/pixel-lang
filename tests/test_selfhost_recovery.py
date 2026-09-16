"""Full semantic PNG recovery through the native compiler, with host stages blocked."""
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import patch

from pixellang.bootstrap_image import HOST_STAGES
from pixellang.compiler_service import CompilerService
from pixellang.project import compile_project
from pixellang.vm import VM
from tests import test_selfhost_pixel_ast as fixtures


class SelfHostedRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code=compile_project({p.name:p.read_text() for p in Path('selfhost').glob('*.pxl')})[1].bytecode

    def assert_execution(self,source,expected):
        files=source if isinstance(source,dict) else {'main.pxl':source}
        service=CompilerService(self.code)
        with ExitStack() as stack:
            for target in HOST_STAGES:
                stack.enter_context(patch(target,side_effect=AssertionError('Host frontend used')))
            exported=service.request(dict(version=1,operation='export-debug-png',files=files))
            self.assertEqual(exported['status'],'ok',exported)
            recovered=service.request(dict(version=1,operation='recover-png',image=exported['result']['image']))
            self.assertEqual(recovered['status'],'ok',recovered)
            project=recovered['result']
            self.assertEqual(set(project['files']),set(files))
            compiled=service.request(dict(version=1,operation='compile',**project))
            self.assertEqual(compiled['status'],'ok',compiled)
            self.assertEqual(VM(compiled['result']['bytecode']).run(),expected)
            document=service.request(dict(version=1,operation='pixels',files=files))
        return document['result']['document']

    def test_nested_paths_and_readable_names(self):
        self.assert_execution({'main.pxl':'import "src/lib.pxl" as lib\nfn main(){print(lib.answer())}',
            'src/lib.pxl':'import "../data/value.pxl" as data\nexport fn answer()->int=data.get()',
            'data/value.pxl':'export fn get()->int=42'},[42])

    def test_corrupted_png_reports_diagnostic(self):
        result=CompilerService(self.code).request(dict(version=1,operation='recover-png',image=b'not a PNG'))
        self.assertEqual(result['status'],'diagnostics',result)
        self.assertTrue(result['diagnostics'])

    def test_metadata_free_png_and_readable_named_png(self):
        service=CompilerService(self.code)
        files={'main.pxl':'fn main(){let values=[3,1,2];print(values)}'}
        for operation in ('export-png','export-debug-png'):
            with self.subTest(operation=operation):
                image=service.request(dict(version=1,operation=operation,files=files))['result']['image']
                recovered=service.request(dict(version=1,operation='recover-png',image=image))
                self.assertEqual(recovered['status'],'ok',recovered)
                project=recovered['result']
                if operation=='export-debug-png':
                    self.assertIn('let values',project['files']['main.pxl'])
                compiled=service.request(dict(version=1,operation='compile',**project))
                self.assertEqual(compiled['status'],'ok',compiled)
                self.assertEqual(VM(compiled['result']['bytecode']).run(),[[3,1,2]])

    def test_conflicting_metadata_cannot_escape_paths_or_capture_names(self):
        # Static PNG fixture: main imports a function returning 42; every symbol
        # hint is "print" and both filename hints escape the output directory.
        # A stored fixture keeps even fixture setup free of host image codecs.
        image=(Path(__file__).parent/'fixtures/recovery-conflicting-metadata.png').read_bytes()
        service=CompilerService(self.code)
        with ExitStack() as stack:
            for target in HOST_STAGES:
                stack.enter_context(patch(target,side_effect=AssertionError('Host frontend used')))
            recovered=service.request(dict(version=1,operation='recover-png',image=image))
            self.assertEqual(recovered['status'],'ok',recovered)
            project=recovered['result']
            self.assertTrue(all(name.startswith('_module_') for name in project['files']))
            compiled=service.request(dict(version=1,operation='compile',**project))
            self.assertEqual(compiled['status'],'ok',compiled)
            self.assertEqual(VM(compiled['result']['bytecode']).run(),[42])


# Share the complete language fixtures, while replacing their assertion with a
# native PNG -> source -> native compiler -> execution check.
for name in dir(fixtures.SelfHostedPixelASTTests):
    if name.startswith('test_') and name not in ('test_real_data_processing_projects','test_rejected_sources_are_not_executable'):
        setattr(SelfHostedRecoveryTests,name,getattr(fixtures.SelfHostedPixelASTTests,name))
