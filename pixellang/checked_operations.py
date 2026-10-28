"""Native Result-returning data operations; language handlers are never involved.

Python exceptions are an implementation detail of the bounded codecs and host I/O.
Only their structured data failures become Error values at this opcode boundary.
Resource exhaustion, cancellation and invalid bytecode remain execution failures.
"""
from .model import PixelError

# Concrete success types for non-generic checked operations. jsonDecode has a
# dedicated typed instruction because its success schema comes from source T.
RESULT_TYPES = {
    'parseInt': 'int', 'parseFloat64': 'float64',
    'utf8Decode': 'str', 'float64FromBytes': 'float64',
    'zlibCompress': 'int[]', 'zlibDecompress': 'int[]',
    'jsonParse': 'json', 'jsonGet': 'json', 'jsonAt': 'json',
    'jsonAsInt': 'int', 'jsonAsFloat64': 'float64',
    'jsonAsString': 'str', 'jsonAsBool': 'bool', 'jsonKeys': 'str[]',
    'readText': 'str', 'writeText': 'bool',
    'readBytes': 'int[]', 'writeBytes': 'bool',
    'readChunk': 'json', 'listDirectory': 'json',
}


def execute(vm, name, arguments, result_type, span, *, decode_target=None):
    try:
        value = (vm.decode_json(arguments[0], decode_target, span)
                 if decode_target is not None else vm.builtin(name, arguments, span))
    except PixelError as error:
        if error.phase != 'data':
            raise
        fields = error.fields()
        fields['operation'] = fields['operation'] or name
        fields['message'] = fields['message'][:256]
    else:
        # Wrapping is outside the catch boundary: allocation failure must not
        # become a recoverable business result, including after a disk write.
        return vm.make_enum(result_type, 'Ok', ['value'], [value], span)
    payload = vm.allocate('Error', fields, span)
    return vm.make_enum(result_type, 'Err', ['error'], [payload], span)
