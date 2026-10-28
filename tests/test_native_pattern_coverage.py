"""Native usefulness checked against explicit finite-domain enumeration."""
import itertools
import json
from pathlib import Path
import random
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "pattern_coverage.pxl" as coverage
record Request {rows:[coverage.Row], subjects:[int], domains:[coverage.Domain], budget:int}
effect fn main() {
 match jsonDecode::[[Request]](input()) {
  .Ok(requests) => { Dispatch(requests) },
  .Err(error) => { print(error.message) }
 }
}
effect fn Dispatch(requests:[Request]) {
 var results:[coverage.Analysis]=[]
 for request in requests {
  results.append(coverage.Check(request.rows,request.subjects,request.domains,request.budget))
 }
 print(results)
}'''


def pattern(tag='', *fields):
    return dict(tag=tag, fields=list(fields))


def domain(*constructors, closed=True):
    return dict(closed=closed, constructors=[dict(tag=tag, fields=fields) for tag, fields in constructors])


def row(*patterns, guarded=False):
    return dict(patterns=list(patterns), guarded=guarded)


def request(rows, subjects, domains, budget=100_000):
    return dict(rows=rows, subjects=subjects, domains=domains, budget=budget)


class NativePatternCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = compile_native_sources({
            'main.pxl': DRIVER,
            'pattern_coverage.pxl': (ROOT/'selfhost/pattern_coverage.pxl').read_text(),
        })

    def check(self, *requests):
        return VM(self.code, input_text=json.dumps(requests), max_steps=40_000_000).run()[0]

    def test_product_coverage_and_unreachable_arms_match_finite_oracle(self):
        rng = random.Random(2381)
        domains = [domain(('A', []), ('B', []), ('C', []))]
        values = list(itertools.product('ABC', repeat=3))
        requests, expected = [], []
        for _ in range(80):
            rows = [row(*(pattern(rng.choice(['', 'A', 'B', 'C'])) for _ in range(3)),
                        guarded=rng.randrange(4) == 0) for _ in range(rng.randrange(1, 15))]
            covered, unreachable = set(), []
            for index, arm in enumerate(rows):
                matches = {v for v in values if all(not p['tag'] or p['tag'] == item
                           for p, item in zip(arm['patterns'], v))}
                if not matches - covered:
                    unreachable.append(index)
                if not arm['guarded']:
                    covered.update(matches)
            expected.append(dict(status='ok', exhaustive=len(covered) == len(values), unreachable=unreachable))
            requests.append(request(rows, [0, 0, 0], domains))
        self.assertEqual(self.check(*requests), expected)

    def test_nested_payloads_do_not_cover_an_entire_variant(self):
        domains = [domain(('None', []), ('Some', [1])), domain(('On', []), ('Off', []))]
        rows = [row(pattern('None')), row(pattern('Some', pattern('On')))]
        incomplete, complete, duplicate = self.check(
            request(rows, [0], domains),
            request(rows + [row(pattern('Some', pattern('Off')))], [0], domains),
            request(rows + [row(pattern('Some', pattern('On')))], [0], domains))
        self.assertFalse(incomplete['exhaustive'])
        self.assertTrue(complete['exhaustive'])
        self.assertEqual(duplicate['unreachable'], [2])

    def test_guards_never_establish_coverage(self):
        domains = [domain(('A', []), ('B', []))]
        guarded = row(pattern(), guarded=True)
        first, second, third = self.check(
            request([guarded, guarded], [0], domains),
            request([guarded, row(pattern())], [0], domains),
            request([row(pattern()), guarded], [0], domains))
        self.assertEqual(first, dict(status='ok', exhaustive=False, unreachable=[]))
        self.assertEqual(second, dict(status='ok', exhaustive=True, unreachable=[]))
        self.assertEqual(third, dict(status='ok', exhaustive=True, unreachable=[1]))

    def test_recursive_and_uninhabited_domains(self):
        domains = [domain(('Nil', []), ('Cons', [0])), domain(('Loop', [1]))]
        recursive, empty, impossible = self.check(
            request([row(pattern('Nil')), row(pattern('Cons', pattern()))], [0], domains),
            request([], [1], domains),
            request([row(pattern())], [1], domains))
        self.assertEqual(recursive, dict(status='ok', exhaustive=True, unreachable=[]))
        self.assertEqual(empty, dict(status='ok', exhaustive=True, unreachable=[]))
        self.assertEqual(impossible, dict(status='ok', exhaustive=True, unreachable=[0]))

    def test_open_domain_literals_need_a_fallback(self):
        domains = [domain(closed=False)]
        rows = [row(pattern('1')), row(pattern('2')), row(pattern('1'))]
        missing, complete = self.check(request(rows, [0], domains),
                                       request(rows + [row(pattern())], [0], domains))
        self.assertEqual(missing, dict(status='ok', exhaustive=False, unreachable=[2]))
        self.assertEqual(complete, dict(status='ok', exhaustive=True, unreachable=[2]))

    def test_budget_exhaustion_cannot_claim_coverage_or_unreachability(self):
        answer = self.check(request([row(pattern())], [0], [domain(('A', []))], budget=1))[0]
        self.assertEqual(answer, dict(status='limit', exhaustive=False, unreachable=[]))

    def test_wide_product_does_not_enumerate_the_cartesian_space(self):
        # This covers 2**32 combinations with one row under a small work budget.
        answer = self.check(request([row(*(pattern() for _ in range(32)))],
                                    [0] * 32, [domain(('A', []), ('B', []))], budget=1000))[0]
        self.assertEqual(answer, dict(status='ok', exhaustive=True, unreachable=[]))

    def test_malformed_typed_patterns_are_rejected(self):
        domains = [domain(('A', []))]
        results = self.check(
            request([row(pattern('Missing'))], [0], domains),
            request([row(pattern('A', pattern()))], [0], domains),
            request([row(pattern('', pattern()))], [0], domains),
            request([row()], [0], domains),
            request([], [2], domains),
            request([], [0], [domain(('A', [7]))]))
        self.assertTrue(all(r == dict(status='invalid', exhaustive=False, unreachable=[]) for r in results))


if __name__ == '__main__':
    unittest.main()
