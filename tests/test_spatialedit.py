import unittest
import time

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from unittest.mock import patch
from pixellang.spatialedit import SpatialProject
from pixellang.vm import VM


class SpatialEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def setUp(self):
        self.files = {
            "main.pxl": 'import "math.pxl" as math\neffect fn main() { print(math.Value()) }',
            "math.pxl": "export effect fn Value() -> int {\n let x = 2 + 3\n print(10)\n print(20)\n return x\n}",
        }
        self.project = SpatialProject(self.files, compiler=self.compiler)

    def execute(self, files):
        code = self.compiler.request('compile', files=files)['result']['bytecode']
        return VM(code).run()

    def target(self, kind, text):
        return next(
            t
            for t in self.project.describe("math.pxl")["targets"].values()
            if t["kind"] == kind and t["text"].strip() == text
        )

    def edit(self, target, action, value=None):
        return self.project.edit(
            {
                "revision": self.project.revision,
                "target": target["id"],
                "action": action,
                "value": value,
            }
        )

    def test_pixel_mapping_and_literal_edit(self):
        target = self.target("literal", "2")
        self.assertEqual(target["explanation"], "Produces a constant int value.")
        view = self.project.describe("math.pxl")
        self.assertTrue(
            any(
                target["id"] in token["targets"]
                for r in view["regions"]
                for token in r["tokens"]
            )
        )
        result = self.edit(target, "literal", "7")
        self.assertEqual(
            self.execute(result["files"]), [10, 20, 10]
        )
        self.assertEqual(result["files"]["main.pxl"], self.files["main.pxl"])
        self.assertEqual(self.project.files, self.files)

    def test_operator_edit_preserves_grouping(self):
        result = self.edit(self.target("binary", "2 + 3"), "operator", "*")
        self.assertEqual(
            self.execute(result["files"]), [10, 20, 6]
        )

    def test_operator_preserves_comments_and_child_expression_grouping(self):
        project = SpatialProject(
            {"main.pxl": "effect fn main() { print((1 + 2) /* keep */ * (3 + 4)) }"}, compiler=self.compiler
        )
        target = next(
            t
            for t in project.describe("main.pxl")["targets"].values()
            if t["kind"] == "binary" and "keep" in t["text"]
        )
        result = project.edit(
            {
                "revision": project.revision,
                "target": target["id"],
                "action": "operator",
                "value": "-",
            }
        )
        self.assertIn("/* keep */", result["files"]["main.pxl"])
        self.assertEqual(self.execute(result["files"]), [-4])

    def test_move_statement_and_reject_broken_dependency(self):
        result = self.edit(self.target("print", "print(20)"), "up")
        self.assertEqual(
            self.execute(result["files"]), [20, 10, 5]
        )
        with self.assertRaises(PixelError):
            self.edit(self.target("return", "return x"), "up")

    def test_invalid_literal_type_and_stale_snapshot_leave_source_intact(self):
        target = self.target("literal", "2")
        for value in [
            '"bad"',
            "1 + 2",
            "1); print(2",
            "1) } fn extra() {} fn other() { print(2",
        ]:
            with self.subTest(value=value), self.assertRaises(PixelError):
                self.edit(target, "literal", value)
        with self.assertRaisesRegex(PixelError, "Source changed"):
            self.project.edit(
                {
                    "revision": "stale",
                    "target": target["id"],
                    "action": "literal",
                    "value": "7",
                }
            )
        self.assertEqual(self.project.files, self.files)

    def test_native_projection_and_edits_do_not_call_host_frontends(self):
        with patch('pixellang.text.parse_text', side_effect=AssertionError('Host text parser used')):
            files = {'main.pxl': 'effect fn main() { print("猫🌍"); print(1.25) }\r\n'}
            project = SpatialProject(files, compiler=self.compiler)
            target = next(t for t in project.describe('main.pxl')['targets'].values()
                          if t['kind'] == 'literal' and t['text'] == '1.25')
            result = project.edit(dict(revision=project.revision, target=target['id'], action='literal', value='-2.5'))
            self.assertEqual(self.execute(result['files']), ['猫🌍', -2.5])
            self.assertTrue(result['files']['main.pxl'].endswith('\r\n'))
            self.assertEqual(project.files, files)

    def test_all_native_edit_stages_share_cancellation_and_deadline(self):
        cancelled = lambda: False
        submitted_at = time.monotonic()
        with patch.object(self.compiler, 'request', wraps=self.compiler.request) as request:
            project = SpatialProject(self.files, compiler=self.compiler,
                                     cancelled=cancelled, submitted_at=submitted_at)
            target = next(t for t in project.describe('math.pxl')['targets'].values()
                          if t['kind'] == 'literal' and t['text'] == '2')
            project.edit(dict(revision=project.revision, target=target['id'], action='literal', value='7'))
        self.assertEqual([call.args[0] for call in request.call_args_list],
                         ['check', 'editor-index', 'debug-pixels', 'check', 'editor-index'])
        for call in request.call_args_list:
            self.assertIs(call.kwargs['cancelled'], cancelled)
            self.assertEqual(call.kwargs['submitted_at'], submitted_at)

    def test_cancellation_during_validation_discards_proposal(self):
        stopped = False
        project = SpatialProject(self.files, compiler=self.compiler, cancelled=lambda: stopped)
        target = next(t for t in project.describe('math.pxl')['targets'].values()
                      if t['kind'] == 'literal' and t['text'] == '2')
        native_request = self.compiler.request
        def finish_then_cancel(*args, **kwargs):
            nonlocal stopped
            result = native_request(*args, **kwargs)
            stopped = True
            return result
        with patch.object(self.compiler, 'request', side_effect=finish_then_cancel) as request:
            with self.assertRaises(PixelError) as failure:
                project.edit(dict(revision=project.revision, target=target['id'], action='literal', value='7'))
        self.assertEqual(failure.exception.code, 'service.cancelled')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(project.files, self.files)
        with self.assertRaises(PixelError):
            project.describe('math.pxl')

    def test_workstation_rejects_cancelled_pixel_requests_before_compilation(self):
        from pixellang.workstation import Workstation
        workstation = Workstation()
        self.addCleanup(workstation.close)
        with patch.object(workstation.compiler, 'request', side_effect=AssertionError('Cancelled work compiled')):
            for operation in ('spatial-inspect', 'spatial-edit'):
                with self.subTest(operation=operation), self.assertRaises(PixelError) as failure:
                    workstation.request(operation, dict(files=self.files, source='math.pxl'),
                                        cancelled=lambda: True, submitted_at=time.monotonic())
                self.assertEqual(failure.exception.code, 'service.cancelled')
