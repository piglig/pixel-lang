"""Standard library contracts: effects, readonly access and isolated values."""
from tests.native_compiler import native_service
from pathlib import Path
import unittest
from pixellang.vm import VM

CASES = [
    ('empty-collections', '''import "std/collections.pxl" as c
effect fn RunTest()->Result[int,Error] {
 let empty: [int] = []
 print(c.Unique(empty)); print(c.Sorted(empty)); print(((c.Repeat(1, 0))?))
 print(c.Map(empty, fn(value: int) = value + 1))
 print(c.Filter(empty, fn(value: int) = true))
 print(c.GroupBy(empty, fn(value: int) = string(value)))
 print(c.Sum(empty, 7)); print(c.Fold(empty, 9, fn(total: int, value: int) = total + value))
 match c.Repeat(1, -1) { .Ok(value) => { print(value) }, .Err(error) => { print(error.code) } }

 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
''', [[], [], [], [], [], {}, 7, 9, 'collection.negative_count']),
    ('collections', '''import "std/collections.pxl" as c
effect fn main() {
 let values = [3, 1, 3, 2]
 print(values)
 print(c.Contains(values, 2))
 print(c.Unique(values))
 print(c.Sorted(values))
 print(c.Sum(values, 0))
 print(c.Map(values, fn(value: int) = value * 2))
 print(c.Filter(values, fn(value: int) = value > 1))
 print(c.Fold(values, 0, fn(total: int, value: int) = total + value))
 print(c.GroupBy(values, fn(value: int) = string(value % 2)))
 print(values)
}''', [[3,1,3,2], True, [3,1,2], [1,2,3,3], 9, [6,2,6,4], [3,3,2], 9,
       {'1':[3,1,3], '0':[2]}, [3,1,3,2]]),
    ('stable-sort', '''import "std/collections.pxl" as c
record Item { key: int, ordinal: int }
effect fn main() {
 let values = [Item{key: 2, ordinal: 0}, Item{key: 1, ordinal: 1}, Item{key: 2, ordinal: 2}]
 let result = c.SortedBy(values, fn(a: Item, b: Item) = a.key < b.key)
 for value in result { print(value.ordinal) }
 print(values[0].ordinal)
}''', [1,0,2,0]),
    ('repeat-snapshot', '''import "std/collections.pxl" as c
effect fn RunTest()->Result[int,Error] {
 var value: [int] = [1]
 let repeated = ((c.Repeat(value, 2))?)
 value.append(2)
 print(repeated)
 print(value)

 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
''', [[[1],[1]], [1,2]]),
    ('map-value-result', '''import "std/collections.pxl" as c
effect fn main() {
 let result = c.Map([1, 2], fn(value: int) -> [int] {
  var built: [int] = [value]
  built.append(value + 1)
  return built
 })
 print(result)
}''', [[[1,2],[2,3]]]),
    ('sort-stats', '''import "std/collections.pxl" as collections
import "std/stats.pxl" as stats
effect fn main() {
 let values = [3, -1, 3, 0]
 print(collections.Sorted(values)); print(values)
 print(collections.Sorted([]: [int]))
 match stats.Summary(values) { Option.Some(value) => { print(value) }, Option.None => { print("missing") } }
}''', [[-1,0,3,3], [3,-1,3,0], [], {'count':4,'total':5,'min':-1,'max':3,'mean':1}]),
    ('maps', '''import "std/collections.pxl" as collections
effect fn main() {
 var counts: map[string, int] = map[string, int]{}
 collections.Increment(&counts, "a"); collections.Increment(&counts, "b"); collections.Increment(&counts, "a")
 let stable = counts
 print(stable)
 match stable.get("a") { Option.Some(value) => { print(value) }, Option.None => { print(0) } }
 collections.Increment(&counts, "a")
 print(stable["a"])
}''', [{'a':2,'b':1},2,2]),
    ('csv', '''import "std/csv.pxl" as csv
effect fn RunTest()->Result[int,Error] {
 let rows = ((csv.Parse("name,value\\r\\n\\"a,b\\",\\"line1\\nline2\\"\\r\\n"))?)
 print(rows)
 let encoded = (csv.Encode(rows))?
 match csv.Parse(encoded) { .Ok(value) => { print(value) }, .Err(error) => { print(error) } }

 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
''',
     [[['name','value'],['a,b','line1\nline2']], [['name','value'],['a,b','line1\nline2']]]),
    ('cli', '''import "std/cli.pxl" as cli
effect fn RunTest()->Result[int,Error] {
 let args = ((cli.Parse(["--verbose", "--output=out.txt", "input.csv"], ["verbose"], ["output"]))?)
 print(args.options); print(args.files)

 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
''', [{'verbose':'true','output':'out.txt'}, ['input.csv']]),
    ('result', '''import "std/result.pxl" as r
effect fn main() {
 let ok = Result::[int, string].Ok(2)
 let mapped = r.Map(ok, fn(value: int) = value * 3)
 match mapped { .Ok(value) => { print(value) }, .Err(error) => { print(error) } }
 let failure = Result::[int, string].Err("bad")
 let changed = r.MapError(failure, fn(error: string) = error + "!")
 match changed { .Ok(value) => { print(value) }, .Err(error) => { print(error) } }
 let chained = r.AndThen(ok, fn(value: int) = Result::[int, string].Ok(value + 1))
 match chained { .Ok(value) => { print(value) }, .Err(error) => { print(error) } }
 r.Ignore(chained)
}''', [6, 'bad!', 3]),
    ('path-text-json', '''import "std/path.pxl" as path
import "std/text.pxl" as text
import "std/json.pxl" as json
import "std/io.pxl" as io
effect fn RunTest()->Result[int,Error] {
 print(((path.Clean("a/./b/../c.txt"))?))
 print(((path.Join("a", "b"))?))
 print(((path.Base("a/c.txt"))?)); print(((path.Dir("a/c.txt"))?)); print(((path.Extension("a/c.txt"))?))
 print(text.Lines("a\\r\\nb\\r"))
 print(json.Stringify(json.Array([((json.Parse("1"))?)])))

 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
''', ['a/c.txt', 'a/b', 'c.txt', 'a', '.txt', ['a','b',''], '[1]']),
    ('time', '''import "std/time.pxl" as time
effect fn RunTest()->Result[int,Error] {
 let interval = (time.Milliseconds(10))?
 let step = (time.At(2, interval))?
 print(step.elapsed.nanoseconds)

 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
''', [20000000]),
]

REJECTED_CASES = [
    ('readonly-binding', '''import "std/collections.pxl" as c
effect fn main() { let values = [1]; let copy = values; copy.append(2) }'''),
    ('readonly-counts', '''import "std/collections.pxl" as collections
effect fn main() { let counts = map[string, int]{}; collections.Increment(&counts, "a") }'''),
    ('impure-callback-contract', '''import "std/collections.pxl" as c
effect fn main() { print(c.Map([1], effect fn(value: int) -> int {print(value);return value})) }'''),
    ('wrong-nested-callback-result', '''import "std/result.pxl" as r
effect fn main() {
 let ok = Result::[int, string].Ok(1)
 let result = r.AndThen::[int, int, string](ok, fn(value: int) = Result::[string, string].Ok("wrong"))
 r.Ignore(result)
}'''),
]


class StandardLibraryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler=native_service()
        cls.addClassCleanup(cls.compiler.close)
        cls.library={'std/'+p.name:p.read_text() for p in (Path(__file__).resolve().parents[1]/'pixellang/stdlib').glob('*.pxl')}

    def request(self, source, operation='compile'):
        return self.compiler.request(dict(version=1,operation=operation,files={**self.library,'main.pxl':source}))

    def test_all_standard_modules_check_with_one_function_model(self):
        source='\n'.join(f'import "{name}" as library{i}' for i,name in enumerate(sorted(self.library)))+'\nfn main() {}'
        response=self.request(source,'check')
        self.assertEqual(response['status'],'ok',response)

    def test_collection_and_result_callbacks_remain_pure(self):
        response=self.request('''import "std/collections.pxl" as collections
import "std/result.pxl" as result
fn twice(value: int) -> int = value*2
effect fn main() {
 print(collections.Sorted([3,1,2]))
 print(collections.Map([1,2],twice))
 let changed=result.Map(Result::[int,string].Ok(3),twice)
 match changed { Result.Ok(value)=>{print(value)}, Result.Err(error)=>{print(error)} }
}
''')
        self.assertEqual(response['status'],'ok',response)
        self.assertEqual(VM(response['result']['bytecode']).run(),[[1,2,3],[2,4],6])
        denied=self.request('''import "std/collections.pxl" as collections
effect fn external(value: int) -> int = value
effect fn main() { let values=collections.Map([1],external) }
''')
        self.assertNotEqual(denied['status'],'ok')

    def test_io_wrappers_require_effect_permission(self):
        for body in ('let value=io.Read("file.txt")','io.ForEachLine("file.txt",fn(number: int,text: string){})','io.Walk("",fn(path: string){})'):
            with self.subTest(body=body):
                response=self.request('import "std/io.pxl" as io\nfn main(){'+body+'}','check')
                self.assertNotEqual(response['status'],'ok')
        response=self.request('''import "std/io.pxl" as io
effect fn line(number: int,text: string){print(text)}
effect fn path(value: string){print(value)}
effect fn main(){let lines=io.ForEachLine("file.txt",line);let paths=io.Walk("",path)}
''','check')
        self.assertEqual(response['status'],'ok',response)

    def test_value_based_standard_algorithms_preserve_inputs(self):
        response=self.request('''import "std/collections.pxl" as collections
import "std/csv.pxl" as csv
import "std/path.pxl" as path
import "std/cli.pxl" as cli
effect fn main() {
 let original=[3,1,2]
 print(collections.Sorted(original));print(original)
 let rows=csv.Parse("a,b\\nc,d\\n")
 match rows { .Ok(value) => { print(value); match csv.Encode(value) { .Ok(encoded) => { match csv.Parse(encoded) { .Ok(decoded) => { print(decoded) }, .Err(error) => { assert(false) } } }, .Err(error) => { assert(false) } } }, .Err(error) => { assert(false) } }
 match path.Clean("a/b/../c") { .Ok(value) => { print(value) }, .Err(error) => { assert(false) } }
 let before=map[string,int]{"a":1}
 var counts=before
 collections.Increment(&counts,"a");collections.Increment(&counts,"b")
 print(before);print(counts)
 let options=cli.Parse(["--verbose","input.txt"],["verbose"],[])
 match options { .Ok(value) => { print(value.options); print(value.files) }, .Err(error) => { assert(false) } }
}
''')
        self.assertEqual(response['status'],'ok',response)
        self.assertEqual(VM(response['result']['bytecode']).run(),[
            [1,2,3],[3,1,2],[['a','b'],['c','d']],[['a','b'],['c','d']],
            'a/c',{'a':1},{'a':2,'b':1},{'verbose':'true'},['input.txt']])

    def test_generic_collection_updates_preserve_groups_and_sort_stability(self):
        response=self.request("""import "std/collections.pxl" as collections
record Item { rank: int, label: string }
effect fn main() {
 let original=[Item{rank:2,label:"a"},Item{rank:1,label:"b"},Item{rank:2,label:"c"}]
 let sorted=collections.SortedBy(original,fn(left: Item,right: Item)=left.rank<right.rank)
 print(collections.Map(sorted,fn(item: Item)=item.label))
 print(collections.Map(original,fn(item: Item)=item.label))
 let groups=collections.GroupBy([1,2,3],fn(value: int)=string(value%2))
 var changed=groups
 changed["1"].append(5)
 print(groups["1"]); print(changed["1"])
}
""")
        self.assertEqual(response['status'],'ok',response)
        self.assertEqual(VM(response['result']['bytecode']).run(),[['b','a','c'],['a','b','c'],[1,3],[1,3,5]])

    def test_generic_adapters_compose_without_snapshot_types(self):
        response=self.request('''import "std/collections.pxl" as collections
import "std/result.pxl" as result
fn copy[T](values: [T]) -> [T] = values
fn mapped[T,U](values: [T], transform: fn(T)->U) -> [U] = collections.Map(values,transform)
fn copiedMap[T](values: map[string,T]) -> map[string,T] = values
fn mappedResult[T,U,E](value: Result[T,E], transform: fn(T)->U) -> Result[U,E] = result.Map(value,transform)
fn increment(value: int) -> int = value+1
effect fn main() {
 print(copy([[1],[2]]))
 print(mapped([1,2],increment))
 print(copiedMap(map[string,[int]]{"x":[3]}))
 match mappedResult(Result::[int,string].Ok(4),increment) {
  Result.Ok(value) => {print(value)}, Result.Err(error) => {print(error)}
 }
 let functions=collections.Repeat(increment,2)
 match functions { .Ok(values) => { let callback=values[1]; print(callback(5)) }, .Err(error) => { assert(false) } }
}
''')
        self.assertEqual(response['status'],'ok',response)
        self.assertEqual(VM(response['result']['bytecode']).run(),[[[1],[2]],[2,3],{'x':[3]},5,6])

    def test_library_programs_preserve_values(self):
        for name, source, expected in CASES:
            with self.subTest(name=name):
                response = self.request(source)
                self.assertEqual(response['status'], 'ok', response)
                code = response['result']['bytecode']
                self.assertEqual(code['vm_version'], '0.9')
                self.assertEqual(VM(code, max_steps=2000000).run(), expected)

    def test_readonly_results_and_callback_contracts_are_enforced(self):
        for name, source in REJECTED_CASES:
            with self.subTest(name=name):
                response = self.request(source)
                self.assertEqual(response['status'], 'diagnostics', response)
                diagnostics = response['diagnostics']
                self.assertTrue(diagnostics)
                self.assertTrue(all(item['phase'] == 'check' for item in diagnostics), diagnostics)
                if name.startswith('readonly-'):
                    self.assertTrue(any('readonly' in item['message'] for item in diagnostics), diagnostics)


    def test_hashable_group_keys_and_histograms_roundtrip(self):
        source = '''import "std/collections.pxl" as c
enum Key { Left, Right }
fn group[T, K: Hashable](values: [T], key: fn(T)->K)->map[K,[T]] = c.GroupBy(values,key)
effect fn main() {
 let values=[3,2,1,4]
 let groups=group(values,fn(value: int)=value%2)
 print(groups.keys());print(groups[1]);print(groups[0]);print(values)
 let empty: [int]=[]
 print(c.GroupBy(empty,fn(value: int)=value>0).keys())
 var counts=map[Key,int]{}
 c.Increment(&counts,Key.Left)
 let before=counts
 c.Increment(&counts,Key.Left);c.Increment(&counts,Key.Right)
 print(before[Key.Left]);print(counts[Key.Left]);print(counts[Key.Right])
 let enums=c.GroupBy([Key.Right,Key.Left,Key.Right],fn(key: Key)=key)
 print(len(enums[Key.Right]));print(len(enums[Key.Left]))
}
'''
        expected = [[1,0],[3,1],[2,4],[3,2,1,4],[],1,2,1,2,1]
        compiled=self.request(source)
        self.assertEqual(compiled['status'],'ok',compiled)
        self.assertEqual(VM(compiled['result']['bytecode']).run(),expected)
        exported=self.request(source,'export-png')
        self.assertEqual(exported['status'],'ok',exported)
        image=exported['result']['image']
        restored=self.compiler.request(dict(version=1,operation='compile-png',image=image))
        self.assertEqual(restored['status'],'ok',restored)
        self.assertEqual(VM(restored['result']['bytecode']).run(),expected)
        recovered=self.compiler.request(dict(version=1,operation='recover-png',image=image))
        self.assertEqual(recovered['status'],'ok',recovered)
        rebuilt=self.compiler.request(dict(version=1,operation='compile',**recovered['result']))
        self.assertEqual(rebuilt['status'],'ok',rebuilt)
        self.assertEqual(VM(rebuilt['result']['bytecode']).run(),expected)

    def test_group_key_constraints_and_counter_overflow(self):
        from pixellang.model import PixelError
        for body in (
            'let groups=c.GroupBy([1],fn(value: int)=[value])',
            'let groups=c.GroupBy::[int,string]([1],fn(value: int)=value)',
            'var counts=map[int,int]{};c.Increment(&counts,"wrong")',
        ):
            with self.subTest(body=body):
                response=self.request('import "std/collections.pxl" as c\nfn main(){'+body+'}','check')
                self.assertEqual(response['status'],'diagnostics',response)
                self.assertTrue(all(item['phase']=='check' for item in response['diagnostics']),response)
        response=self.request('''import "std/collections.pxl" as c
effect fn main(){var counts=map[int,int]{1:9223372036854775807};c.Increment(&counts,1)}''')
        self.assertEqual(response['status'],'ok',response)
        with self.assertRaises(PixelError):
            VM(response['result']['bytecode']).run()
