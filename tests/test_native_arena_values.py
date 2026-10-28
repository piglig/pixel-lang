"""Compiler phases publish their type arena without changing earlier values."""
import json
from pathlib import Path
import re
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "model.pxl" as model
import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "instances.pxl" as instances
import "closures.pxl" as closures
import "frames.pxl" as frames
import "schemas.pxl" as schemas
import "ir.pxl" as ir
import "assembler.pxl" as assembler
import "debug_info.pxl" as debugInfo
record Request { files:map[string,string], operation:string }
effect fn RunTest()->Result[int,Error] {
    let request = (jsonDecode::[Request](input()))?
    let bound = bindings.Bind(project.Build(request.files, "main.pxl"))
    let untypedSymbols = bound.symbols
    var resolved = types.Analyze(bound)
    assert(bound.symbols == untypedSymbols)
    assert(len(resolved.diagnostics) == 0)
    let originalTypes = resolved.types
    let originalSymbols = resolved.bound.symbols
    if request.operation == "intern" {
        let integer = types.Scalar("int", &resolved)
        let array = types.Structural("array", [integer], &resolved)
        let nested = types.Structural("array", [array], &resolved)
        assert(types.Structural("array", [array], &resolved) == nested)
        assert(len(resolved.types) > len(originalTypes))
        assert(len(originalTypes) == 6)
        var independent = resolved
        let retained = resolved.types
        types.OptionType(nested, &independent)
        assert(resolved.types == retained)
        assert(len(independent.types) > len(resolved.types))
        print("isolated")
        return .Ok(0)
    }
    let checked = checker.Check(resolved)
    assert(len(checked.diagnostics) == 0)
    assert(resolved.types == originalTypes)
    assert(resolved.bound.symbols == originalSymbols)
    let checkedTypes = checked.resolved.types
    let named = instances.Build(checked)
    assert(len(named.diagnostics) == 0)
    assert(checked.resolved.types == checkedTypes)
    let namedTypes = named.checked.resolved.types
    var planned = closures.Build(named)
    assert(len(planned.diagnostics) == 0)
    assert(named.checked.resolved.types == namedTypes)
    let beforeLayout = planned
    let layoutTypes = planned.named.checked.resolved.types
    let framePlan = frames.Build(&planned)
    assert(len(framePlan.diagnostics) == 0)
    let schemaPlan = schemas.Build(&planned)
    assert(len(schemaPlan.diagnostics) == 0)
    let lowered = ir.Build(&planned)
    assert(len(lowered.diagnostics) == 0)
    let metadata = debugInfo.Build(&planned)
    assert(len(metadata.functions) > 0)
    assert(beforeLayout.named.checked.resolved.types == layoutTypes)
    let count = len(planned.named.checked.resolved.types)
    for frame in framePlan.functions {
        assert(frame.typeId >= 0 && frame.typeId < count)
        for slot in frame.slots { assert(slot.typeId >= 0 && slot.typeId < count) }
    }
    for schema in schemaPlan.schemas {
        assert(schema.typeId >= 0 && schema.typeId < count)
        for field in schema.fields { assert(field.typeId >= 0 && field.typeId < count) }
        for variant in schema.variants {
            for field in variant.fields { assert(field.typeId >= 0 && field.typeId < count) }
        }
    }
    let executable = assembler.Assemble(lowered)
    assert(len(executable.diagnostics) == 0)
    print(jsonStringify(jsonFrom(executable.bytecode)))

 return .Ok(0)
}

effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
'''


def sources():
    files = {'main.pxl': DRIVER}
    pending = re.findall(r'import "([^"]+)"', DRIVER)
    while pending:
        name = pending.pop()
        if name in files:
            continue
        files[name] = (ROOT / 'selfhost' / name).read_text()
        pending.extend(re.findall(r'import "([^"]+)"', files[name]))
    return files


class NativeArenaValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = compile_native_sources(sources())

    def compile(self, source, operation='pipeline'):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        output = VM(self.code, input_text=json.dumps(dict(files=files, operation=operation)),
                    max_steps=80_000_000, max_heap_items=32_000_000,
                    max_depth=1024).run()
        self.assertEqual(len(output), 1)
        return output[0]

    def test_interned_types_are_owned_and_deduplicated(self):
        self.assertEqual(self.compile('', 'intern'), 'isolated')

    def test_generic_fields_and_closures_retain_concrete_type_ids(self):
        program = self.compile('''record Box[T] { values:[T] }
fn Make[T](value:T) -> fn() -> Box[T] = fn() = Box::[T]{values:[value]}
effect fn main() {
 let make = Make(7)
 let first = make()
 var second = first
 second.values.append(9)
 print(first.values)
 print(second.values)
}''')
        self.assertEqual(VM(json.loads(program)).run(), [[7], [7, 9]])

    def test_cross_module_enum_payloads_and_match_callbacks(self):
        program = self.compile({
            'values.pxl': '''export record Box[T] { value:T }
export enum Message[T] { Empty, Data(value:Box[T]) }
export fn Wrap[T](value:T) -> Message[T] = Message::[T].Data(Box::[T]{value})''',
            'main.pxl': '''import "values.pxl" as values
fn Read[T](message:values.Message[T], fallback:T) -> T = match message {
 .Empty => fallback,
 .Data(box) => box.value
}
effect fn main() { print(Read(values.Wrap([3,4]), [])) }''',
        })
        self.assertEqual(VM(json.loads(program)).run(), [[3, 4]])

    def test_stateful_child_and_initializer_publish_type_arena(self):
        program = self.compile('''record State { values:[int] }
fn Update(previous:State, delta:int) -> State {
 var next = previous
 next.values.append(delta)
 return next
}
flow Counter(seed:State) step(delta:int) -> State {
 state value:State = seed
 let updated = Update(value, delta)
 next value = updated
 output updated
}
flow Parent(seed:State) step(delta:int) -> State {
 instance counter = Counter(seed)
 output counter.step(delta)
}
effect fn main() {
 let parent = Parent(State{values:[1]})
 print(parent.step(2).values)
 print(parent.step(3).values)
}''')
        self.assertEqual(VM(json.loads(program)).run(), [[1, 2], [1, 2, 3]])


if __name__ == '__main__':
    unittest.main()
