from pathlib import Path
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from pixellang.bootstrap_image import HOST_STAGES
from pixellang.compiler_service import CompilerService
from pixellang.project import compile_project
from pixellang.vm import VM


class SelfHostedDebugTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parents[1]
        cls.compiler=compile_project({p.name:p.read_text() for p in (root/'selfhost').glob('*.pxl')})[1].bytecode

    def test_debug_sidecar_matches_generic_and_closure_frames(self):
        files={'main.pxl':'''import "lib.pxl" as lib
fn main(){
 let base = 20
 let twice = fn(value:int)->int = base + value
 print(lib.identity(twice(22)))
}''','lib.pxl':'export fn identity[T](value:T)->T=value'}
        service=CompilerService(self.compiler)
        with ExitStack() as stack:
            for target in (*HOST_STAGES,'pixellang.project.compile_project'):
                stack.enter_context(patch(target,side_effect=AssertionError('Host frontend used')))
            built=service.request(dict(version=1,operation='debug-compile',files=files))
            plain=service.request(dict(version=1,operation='compile',files=files))
        self.assertEqual(built['status'],'ok',built)
        result=built['result'];code=result['bytecode'];debug=result['debug']
        self.assertEqual(code,plain['result']['bytecode'])
        self.assertEqual(VM(code).run(),[42])
        self.assertEqual(set(debug['functions']),set(code['functions']))
        identity=next(f for f in debug['functions'].values() if f['name']=='identity')
        self.assertEqual(identity['source'],'lib.pxl')
        self.assertEqual(identity['module'],'1')
        self.assertEqual(identity['typeArguments'],['int'])
        self.assertEqual(identity['names'],['value'])
        closure=next((key,f) for key,f in debug['functions'].items() if f['name']=='lambda')
        self.assertEqual(closure[1]['names'][:2],['value','base'])
        self.assertEqual(closure[1]['module'],'main')
        for key, info in debug['functions'].items():
            self.assertLessEqual(len(info['names']),code['functions'][key]['locals'])

    def test_invalid_program_has_no_debug_artifact(self):
        service=CompilerService(self.compiler)
        result=service.request(dict(version=1,operation='debug-compile',files={'main.pxl':'fn main(){print(missing)}'}))
        self.assertEqual(result['status'],'diagnostics',result)
        self.assertIsNone(result['result'])

    def test_generated_source_maps_drive_native_timeline_and_variable_names(self):
        from pixellang.debug_artifact import prepare_debug
        from pixellang.temporal import Timeline
        from pixellang.workstation import Workstation
        files={'main.pxl':'import "lib.pxl" as lib\nfn main(){\nlet result=lib.twice(21)\nprint(result)\n}',
               'lib.pxl':'export fn twice(value:int)->int {\nreturn value*2\n}'}
        service=CompilerService(self.compiler)
        result=service.request(dict(version=1,operation='debug-compile',files=files))
        mapped=service.request(dict(version=1,operation='debug-pixels',files=files))
        self.assertEqual(mapped['status'],'ok',mapped)
        artifact=prepare_debug(result['result']['bytecode'],result['result']['debug'],mapped['result']['document'])
        with patch('pixellang.temporal.compile_source',side_effect=AssertionError('Host frontend used')):
            timeline=Timeline(artifact.source,compiled=artifact)
        studio=Workstation();self.addCleanup(studio.close)
        seen=[]
        while not timeline.vm.halted:
            status=studio.status(timeline)
            seen.extend(status['frames'])
            timeline.advance()
        self.assertEqual(timeline.vm.output,[42])
        self.assertTrue(any(f['display_name']=='twice' and 'value' in f['names'] for f in seen))
        self.assertTrue(any(f['location'] and f['location']['source']=='lib.pxl' for f in seen))
        artifact.source_project={'files':files,'entry':'main.pxl'}
        timeline.source_project=artifact.source_project
        timeline.seek(3)
        recording=timeline.document()
        with patch('pixellang.temporal.compile_source',side_effect=AssertionError('Host replay compiler')):
            restored=Timeline.restore(recording,compiled=artifact)
        self.assertEqual(restored.seek(3),timeline.seek(3))
        from copy import deepcopy
        damaged=deepcopy(recording);damaged['compiled_sha256']='0'*64
        from pixellang.model import PixelError
        with self.assertRaisesRegex(PixelError,'differs from recorded'):
            Timeline.restore(damaged,compiled=artifact)
        damaged=deepcopy(recording);damaged['source']['metadata']['filename']='wrong.pxl'
        with self.assertRaisesRegex(PixelError,'differs from timeline'):
            Timeline.restore(damaged,compiled=artifact)
        volume=timeline.volume()
        self.assertTrue(volume['voxels'])
        self.assertEqual({v['module'] for v in volume['voxels']},{'main','1'})
        documents={'main':artifact.source,**artifact.source['bundle']['modules']}
        for voxel in volume['voxels']:
            points={tuple(p['position']) for p in documents[voxel['module']]['pixels']}
            self.assertIn(tuple(voxel['position'][:2]),points)

    def test_studio_build_and_restore_use_native_service_and_named_png(self):
        import base64,io
        from PIL import Image
        from pixellang.picture import decode_picture
        from pixellang.project import project_text
        from pixellang.compiler_client import CompilerClient
        from pixellang.workstation import Workstation
        service=CompilerService(self.compiler)
        submissions=[]
        class InlineWorker:
            def request(self,request,**kwargs):
                submissions.append(kwargs.get('submitted_at'))
                return service.request(request,cancelled=kwargs.get('cancelled',lambda:False))
        studio=Workstation();self.addCleanup(studio.close)
        studio.compiler=CompilerClient(InlineWorker())
        files={'main.pxl':'fn main(){\nlet values=[3,1,2]\nprint(values)\n}'}
        with ExitStack() as stack:
            for target in (*HOST_STAGES,'pixellang.workstation.compile_project','pixellang.workstation.encode_picture','pixellang.temporal.compile_source'):
                stack.enter_context(patch(target,side_effect=AssertionError('Host Studio frontend used')))
            built=studio.request('build',{'files':files})
            self.assertEqual(len(submissions),1,'Build must use one compilation transaction')
            recovered=studio.request('recover',{'image':built['image']})
            self.assertIn('let values',recovered['files']['main.pxl'])
            self.assertIsNone(built['inspection']['ir'])
            inspected=studio.request('inspect-ir',{'session':built['session']})
            self.assertTrue(inspected['ir']['functions'])
            count=len(submissions)
            self.assertEqual(studio.request('inspect-ir',{'session':built['session']}),inspected)
            self.assertEqual(len(submissions),count,'IR must be retained for the session')
            run=studio.request('run',{'session':built['session']})
            self.assertEqual(run['output'],['[3, 1, 2]'])
            record=studio.request('recording',{'session':built['session']})
            submissions.clear()
            restored=studio.request('restore',{'document':record})
            self.assertEqual(restored['output'],run['output'])
            self.assertEqual(len(submissions),1,'Restore must use one compilation transaction')
            self.assertIsNotNone(submissions[0])
            self.assertEqual(len(set(submissions)),1,'Restore phases must share one deadline')
        with Image.open(io.BytesIO(base64.b64decode(built['image']))) as image:
            document=decode_picture(image)
        self.assertNotIn('source_map',document['metadata'])
        self.assertEqual(document['metadata']['filename'],'main.pxl')
        recovered=project_text(document)
        self.assertIn('let values',recovered['files']['main.pxl'])
