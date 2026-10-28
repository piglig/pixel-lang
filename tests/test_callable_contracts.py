"""Effect compatibility laws, independently of frontend inference."""

import unittest

from pixellang.typeexpr import Type, is_assignable, is_value_type, parse_type
from pixellang.model import PixelError
from pixellang.vm import VM, validate_bytecode
from tests.bytecode_helpers import op, program


class CallableContractTests(unittest.TestCase):
    def accepts(self, expected, actual):
        return is_assignable(parse_type(expected), parse_type(actual))

    def test_effect_roundtrip_and_display(self):
        spelling = "fn(int,effect_fn(str)->(bool))->(fn()->(unit))"
        typ = parse_type(spelling)
        self.assertEqual(typ.spelling(), spelling)
        self.assertEqual(typ.display(), "fn(int, effect fn(string) -> bool) -> fn()")
        self.assertTrue(is_value_type(typ))

    def test_substitution_preserves_contract(self):
        typ = parse_type("fn($T)->($T[])")
        self.assertTrue(is_value_type(typ, {"T"}))
        self.assertEqual(typ.substitute({"T": parse_type("int")}).spelling(),
                         "fn(int)->(int[])")

    def test_effect_can_be_forgotten_but_not_invented(self):
        self.assertTrue(self.accepts("effect_fn(int)->(int)", "fn(int)->(int)"))
        self.assertFalse(self.accepts("fn(int)->(int)", "effect_fn(int)->(int)"))

    def test_callback_parameter_is_contravariant(self):
        accepts_pure = "effect_fn(fn(int)->(int))->(int)"
        accepts_any = "effect_fn(effect_fn(int)->(int))->(int)"
        self.assertTrue(self.accepts(accepts_pure, accepts_any))
        self.assertFalse(self.accepts(accepts_any, accepts_pure))

    def test_returned_callback_is_covariant(self):
        returns_any = "effect_fn()->(effect_fn(int)->(int))"
        returns_pure = "effect_fn()->(fn(int)->(int))"
        self.assertTrue(self.accepts(returns_any, returns_pure))
        self.assertFalse(self.accepts(returns_pure, returns_any))

    def test_containers_and_nominal_arguments_remain_invariant(self):
        for template in ("{}[]", "map<str,{}>", "Option<{}>", "@0:1<{}>"):
            ordinary = template.format("effect_fn(int)->(int)")
            component = template.format("fn(int)->(int)")
            self.assertFalse(self.accepts(ordinary, component))
            self.assertFalse(self.accepts(component, ordinary))

    def test_retired_permission_wrappers_are_not_callable_containers(self):
        for typ in ("mut<fn(int)->(int)[]>", "mut<map<str,fn(int)->(int)>>"):
            with self.subTest(typ=typ), self.assertRaises(ValueError):
                parse_type(typ)

    def test_no_arity_or_scalar_coercion(self):
        self.assertFalse(self.accepts("effect_fn(int)->(int)", "fn()->(int)"))
        self.assertFalse(self.accepts("effect_fn(int)->(int)", "fn(float64)->(int)"))
        self.assertFalse(self.accepts("effect_fn(int)->(int)", "fn(int)->(float64)"))

    def test_invalid_function_contract_is_not_a_value_type(self):
        self.assertFalse(is_value_type(Type("function")))
        self.assertFalse(is_value_type(Type("function", "unchecked", (parse_type("int"),))))


class CallableRuntimeTests(unittest.TestCase):
    def code(self, main):
        code = program([op("LOAD", 0), op("RETURN")], "int", "int")
        code["functions"]["main"]["instructions"] = main
        code["functions"]["ordinary"] = dict(
            params=["int"], locals=1, result="int",
            instructions=[op("LOAD", 0), op("RETURN")])
        code["effectFunctions"].append("ordinary")
        return code

    def test_pure_identity_survives_widened_invocation_and_replay(self):
        code = self.code([op("FUNCTION", "subject"), op("PUSH", 0),
                          op("INVOKE", "effect_fn(int)->(int)"), op("PRINT"),
                          op("PUSH", 0), op("RETURN")])
        vm = VM(code)
        vm.step(snapshot=False)
        self.assertEqual(vm.value_type(vm.stack[-1]), "fn(int)->(int)")
        checkpoint = vm.checkpoint()
        self.assertEqual(vm.run(), [5])
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(), [5])

    def test_effectful_function_cannot_satisfy_pure_invocation(self):
        vm = VM(self.code([op("FUNCTION", "ordinary"), op("PUSH", 0),
                           op("INVOKE", "fn(int)->(int)"), op("RETURN")]))
        with self.assertRaisesRegex(PixelError, "Expected runtime fn"):
            vm.run()

    def test_allocation_cannot_forge_or_erase_target_contract(self):
        vm = VM(self.code([op("PUSH", 0), op("RETURN")]))
        for typ, target in [("fn(int)->(int)", "ordinary"),
                            ("effect_fn(int)->(int)", "subject")]:
            with self.subTest(typ=typ), self.assertRaisesRegex(PixelError, "Invalid function value"):
                vm.allocate(typ, {"$function": target}, None)

    def test_forged_component_body_still_cannot_print(self):
        code = self.code([op("FUNCTION", "subject"), op("PUSH", 0),
                          op("INVOKE", "fn(int)->(int)"), op("RETURN")])
        code["functions"]["subject"]["instructions"] = [
            op("LOAD", 0), op("PRINT"), op("LOAD", 0), op("RETURN")]
        vm = VM(code)
        with self.assertRaises(PixelError):
            vm.run()
        self.assertEqual(vm.output, [])

    def test_capture_retains_declared_type_when_initial_value_is_narrower(self):
        code = self.code([op("FUNCTION", "subject"), op("BIND", 0),
                          op("CAPTURE_VALUE", {"slot": 0, "type": "effect_fn(int)->(int)"}), op("POP"),
                          op("FUNCTION", "ordinary"), op("STORE", 0), op("LOAD", 0),
                          op("PUSH", 0), op("INVOKE", "effect_fn(int)->(int)"),
                          op("PRINT"), op("PUSH", 0), op("RETURN")])
        self.assertEqual(VM(code).run(), [5])

    def test_mutable_capture_cell_is_invariant(self):
        vm = VM(self.code([op("PUSH", 0), op("RETURN")]))
        value = vm.allocate("fn(int)->(int)", {"$function": "subject"}, None)
        cell = vm.allocate("fn(int)->(int)", {"value": value}, None, cell=True)
        with self.assertRaisesRegex(PixelError, "Expected runtime effect_fn"):
            vm.typed(cell, "effect_fn(int)->(int)", None)
        broad = vm.allocate("effect_fn(int)->(int)", {"value": value}, None, cell=True)
        with self.assertRaisesRegex(PixelError, "Expected runtime fn"):
            vm.typed(broad, "fn(int)->(int)", None)

    def test_invalid_capture_contract_rejected_at_load(self):
        for typ in ("unit", "missing", None):
            code = self.code([op("CAPTURE_VALUE", {"slot": 0, "type": typ}), op("RETURN")])
            with self.subTest(typ=typ), self.assertRaisesRegex(PixelError, "Invalid capture binding type"):
                validate_bytecode(code)

    def test_append_checks_element_contract_without_widening_container(self):
        vm = VM(self.code([op("PUSH", 0), op("RETURN")]))
        pure = vm.allocate("fn(int)->(int)", {"$function": "subject"}, None)
        ordinary = vm.allocate("effect_fn(int)->(int)", {"$function": "ordinary"}, None)
        broad = vm.allocate("effect_fn(int)->(int)[]", [], None)
        narrow = vm.allocate("fn(int)->(int)[]", [], None)
        from pixellang.value_update import edit_collection
        updated, _ = edit_collection(vm, broad, "append", [pure], "unit", None)
        self.assertEqual(list(vm.heap[broad.address]['items']), [])
        self.assertEqual(list(vm.heap[updated.address]['items']), [pure])
        with self.assertRaises(PixelError):
            edit_collection(vm, narrow, "append", [ordinary], "unit", None)
        with self.assertRaises(PixelError):
            vm.typed(narrow, "effect_fn(int)->(int)[]", None)


if __name__ == "__main__":
    unittest.main()
