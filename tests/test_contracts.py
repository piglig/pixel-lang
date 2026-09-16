"""Cross-layer contracts and malformed-input regression checks."""

import copy
import unittest

from examples.build import examples
from pixellang.compiler import compile_source
from pixellang.model import PixelError, Span
from pixellang.vm import VM


class ContractTests(unittest.TestCase):
    def test_malformed_input_diagnostics(self):
        values = [None, True, False, 0, -1, 1, 3.2, "bad", [], {}, [1], {"a": 2}]
        source = examples()["addition"]
        bytecode = compile_source(source).bytecode
        cases = [
            (
                source,
                compile_source,
                [
                    ("magic",),
                    ("versions",),
                    ("dimensions",),
                    ("pixels",),
                    ("spatial",),
                    ("pixels", 0),
                    ("pixels", 0, "position"),
                    ("pixels", 0, "rgba"),
                    ("spatial", "links"),
                ],
            ),
            (
                bytecode,
                lambda bc: VM(bc).run(),
                [
                    ("constants",),
                    ("constants", 0),
                    ("constants", 0, "type"),
                    ("entry",),
                    ("functions",),
                    ("functions", "main:99"),
                    ("functions", "main:99", "params"),
                    ("functions", "main:99", "locals"),
                    ("functions", "main:99", "instructions"),
                    ("functions", "main:99", "instructions", 0),
                    ("functions", "main:99", "instructions", 0, "op"),
                    ("functions", "main:99", "instructions", 0, "span"),
                ],
            ),
        ]
        for original, action, paths in cases:
            for path in paths:
                for value in values:
                    with self.subTest(path=path, value=value):
                        doc = copy.deepcopy(original)
                        target = doc
                        for k in path[:-1]:
                            target = target[k]
                        target[path[-1]] = value
                        # Some mutations (e.g. absent provenance) are legal. Invalid
                        # cases must produce the public diagnostic, never a host crash.
                        try:
                            action(doc)
                        except PixelError as error:
                            self.assertTrue(error.message)
                            self.assertIsInstance(str(error), str)

    def test_step_and_batch_are_equivalent(self):
        bytecode = compile_source(examples()["factorial"]).bytecode
        stepped, batch = VM(bytecode, trace=True), VM(bytecode, trace=True)
        while not stepped.halted:
            stepped.step()
        batch.run()
        self.assertEqual(stepped.state(), batch.state())
        self.assertEqual(stepped.trace, batch.trace)

    def test_provenance_model_has_no_temporal_axis(self):
        span = Span("future.voxel", ((1, 2, 3), (4, 5, 6)))
        self.assertEqual(span.to_dict()["min"], [1, 2, 3])
        self.assertEqual(span.to_dict()["max"], [4, 5, 6])
        self.assertNotIn("time", span.to_dict())


if __name__ == "__main__":
    unittest.main()
