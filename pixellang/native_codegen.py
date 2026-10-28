"""Ahead-of-time C lowering, with explicit rejection of unimplemented features.

CompilerService can explicitly select its prebuilt executable; installed Studio
selection is still pending. There is no fallback to the Python VM. The source map
uses original bytecode PCs and binds the executable to its input bytecode.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from .vm import validate_bytecode
from .typeexpr import array_item, map_parts, parse_type, is_assignable

_TYPES = {'int': 'PIXEL_INT', 'bool': 'PIXEL_BOOL', 'unit': 'PIXEL_INT', 'float64': 'PIXEL_FLOAT', 'str': 'PIXEL_STRING', 'json': 'PIXEL_JSON'}
_BINARY = ('ADD', 'SUB', 'MUL', 'DIV', 'MOD', 'EQ', 'NE', 'LT', 'LE', 'GT', 'GE')
_OPS = frozenset((*_BINARY, 'PUSH', 'LOAD', 'BIND', 'STORE', 'POP', 'DUP', 'DUP2',
                  'NEG', 'NOT', 'JUMP', 'JUMP_IF_TRUE', 'JUMP_IF_FALSE', 'CALL',
                  'RETURN', 'PRINT', 'STACK_DEPTH', 'UNWIND', 'TRAP', 'CONCAT', 'INDEX', 'BUILTIN', 'ARRAY', 'MAP', 'ITER_SNAPSHOT', 'RECORD', 'FIELD',
                  'ENUM', 'ENUM_IS', 'ENUM_GET', 'PROPAGATE', 'FUNCTION', 'CLOSURE', 'CAPTURE_VALUE', 'INVOKE', 'RESULT_BUILTIN', 'RESULT_DECODE', 'VALUE_COLLECTION', 'VALUE_CHECK', 'VALUE_SET', 'ACCESS'))
_BUILTINS = frozenset(('len', 'slice', 'join', 'replace', 'trim', 'assert', 'float64Bytes', 'crc32', 'utf8Encode', 'has', 'keys', 'lookup', 'input', 'jsonFrom', 'jsonNull', 'jsonStringify', 'jsonKind', 'string', 'split', 'arrayRepeat', 'unicodeCategory'))
_RESULT_BUILTINS = frozenset(('utf8Decode', 'float64FromBytes', 'zlibCompress', 'zlibDecompress', 'parseInt', 'parseFloat64', 'readText', 'readBytes', 'writeText', 'writeBytes', 'jsonParse', 'jsonGet', 'jsonAt', 'jsonKeys', 'jsonAsString', 'jsonAsBool', 'jsonAsInt', 'jsonAsFloat64'))


class UnsupportedNativeProgram(ValueError):
    pass


def _supported_key(typ, enums):
    pending,seen=[typ],set()
    while pending:
        typ=pending.pop()
        if typ in seen or typ in ('int','float64','bool','str'):continue
        seen.add(typ)
        if typ not in enums:return False
        for fields in enums[typ].values():pending.extend(fields.values())
    return True


def _supported_type(typ, records, enums, *, comparable=False):
    pending, seen = [typ], set()
    while pending:
        typ = pending.pop()
        if comparable and typ=='json':return False
        if typ in seen or typ in _TYPES:
            continue
        seen.add(typ)
        if parse_type(typ).kind=='inout':
            if comparable:return False
            pending.append(parse_type(typ).args[0].spelling())
        elif (parts:=map_parts(typ)) is not None:
            if not _supported_key(parts[0],enums):return False
            pending.extend(parts)
        elif (element := array_item(typ)) is not None:
            pending.append(element)
        elif parse_type(typ).kind=='function':
            if comparable:
                return False
            pending.extend(arg.spelling() for arg in parse_type(typ).args)
        elif typ in records:
            pending.extend(records[typ].values())
        elif typ in enums:
            for fields in enums[typ].values():
                pending.extend(fields.values())
        else:
            return False
    return True


def unsupported_features(code):
    """Report the complete unsupported surface, including unreachable functions."""
    reasons = set()
    if code.get('flows'):
        reasons.add('flow runtime')
    for item in code['constants']:
        if item['type'] not in ('int', 'bool', 'float64', 'str'):
            reasons.add('constant type ' + item['type'])
    for fn in code['functions'].values():
        for typ in (*fn['params'], *fn.get('captures',[]), fn['result']):
            if not _supported_type(typ,code['records'],code['enums']):
                reasons.add('value type ' + typ)
        for ins in fn['instructions']:
            if ins['op'] not in _OPS:
                reasons.add('instruction ' + ins['op'])
            elif ins['op'] == 'BUILTIN' and ins['arg'] not in _BUILTINS:
                reasons.add('builtin ' + ins['arg'])
            elif ins['op']=='VALUE_COLLECTION':
                if ins['arg']['method'] not in ('append','extend','pop','remove'):
                    reasons.add('collection method '+ins['arg']['method'])
                if not _supported_type(ins['arg']['result'],code['records'],code['enums']):
                    reasons.add('value type '+ins['arg']['result'])
            elif ins['op'] in ('RESULT_BUILTIN','RESULT_DECODE'):
                if ins['op']=='RESULT_BUILTIN' and ins['arg']['name'] not in _RESULT_BUILTINS:
                    reasons.add('result builtin ' + ins['arg']['name'])
                if not _supported_type(ins['arg']['result'],code['records'],code['enums']):
                    reasons.add('value type ' + ins['arg']['result'])
            elif ins['op'] in ('ARRAY','MAP','RECORD','ENUM','ENUM_IS','ENUM_GET') and not _supported_type(ins['arg']['type'],code['records'],code['enums']):
                reasons.add('value type ' + ins['arg']['type'])
            elif ins['op']=='PROPAGATE':
                for typ in ins['arg'].values():
                    if not _supported_type(typ,code['records'],code['enums']):
                        reasons.add('value type ' + typ)
            elif ins['op']=='INVOKE' and not _supported_type(ins['arg'],code['records'],code['enums']):
                reasons.add('value type ' + ins['arg'])
            elif ins['op']=='CAPTURE_VALUE' and isinstance(ins['arg'],dict):
                typ=ins['arg']['type']
                if not _supported_type(typ,code['records'],code['enums']):
                    reasons.add('value type ' + typ)
            elif ins['op']=='ACCESS' and not _supported_type(ins['arg']['type'],code['records'],code['enums']):
                reasons.add('value type '+ins['arg']['type'])
    return sorted(reasons)


def _dead_local_releases(fn):
    """Last-use releases on all control-flow paths; borrowed roots stay pinned.

    Stack values/captures own their references independently. Binding metadata
    (notably readonly) survives release so invalid writes still fail normally.
    """
    instructions=fn['instructions']
    protected={i for i,t in enumerate([*fn['params'],*fn.get('captures',[])])
               if parse_type(t).kind=='inout'}
    protected.update(i['arg']['slot'] for i in instructions if i['op']=='ACCESS')
    uses=[]; defines=[]; successors=[]; slots=[]
    for pc,ins in enumerate(instructions):
        op,arg=ins['op'],ins.get('arg')
        slot=(arg['slot'] if isinstance(arg,dict) else arg) if op=='CAPTURE_VALUE' else arg
        reads=op in ('LOAD','CAPTURE_VALUE','UNWIND')
        writes=op in ('BIND','STORE')
        uses.append(1<<slot if reads else 0)
        defines.append(1<<slot if writes else 0)
        slots.append(slot if reads or writes else None)
        following=[pc+1] if pc+1<len(instructions) else []
        successors.append([] if op in ('RETURN','TRAP') else [arg] if op=='JUMP'
                          else [arg,*following] if op in ('JUMP_IF_TRUE','JUMP_IF_FALSE') else following)
    live=[0]*len(instructions)
    outgoing=[0]*len(instructions)
    changed=True
    while changed:
        changed=False
        for pc in reversed(range(len(instructions))):
            after=0
            for target in successors[pc]:after|=live[target]
            before=uses[pc]|(after & ~defines[pc])
            outgoing[pc]=after
            if before!=live[pc]:live[pc]=before;changed=True
    return {pc:slot for pc,slot in enumerate(slots)
            if slot is not None and slot not in protected and not outgoing[pc] & (1<<slot)}


def emit_c(code, *, max_depth=256, session=None):
    if type(max_depth) is not int or not 1 <= max_depth <= 1024:
        raise ValueError('Native call depth must be an integer in 1..1024')
    validate_bytecode(code)
    unsupported = unsupported_features(code)
    if unsupported:
        raise UnsupportedNativeProgram('; '.join(unsupported))
    functions = code['functions']
    if session is not None:
        if not isinstance(session, dict) or set(session) != {'initialize', 'advance'}:
            raise ValueError('Session requires initialize and advance function IDs')
        if any(not isinstance(name, str) or name not in functions for name in session.values()):
            raise ValueError('Session function is missing')
        initial, advance = (functions[session[key]] for key in ('initialize', 'advance'))
        if (initial['params'] or initial.get('captures') or advance.get('captures')
                or initial['result'] == 'unit' or parse_type(initial['result']).kind == 'inout'
                or advance['params'] != [initial['result']] or advance['result'] != initial['result']):
            raise ValueError('Session functions must initialize and transform the same owned value type')
    ids = {name: index for index, name in enumerate(functions)}
    effects = set(code['effectFunctions'])
    def signature(name):
        fn=functions[name]
        return ('effect_fn' if name in effects else 'fn')+'('+','.join(fn['params'])+')->('+fn['result']+')'
    callable_targets={ins['arg'] for fn in functions.values() for ins in fn['instructions'] if ins['op'] in ('FUNCTION','CLOSURE')}
    value_types = {}
    def register_type(typ):
        pending = [typ]
        while pending:
            typ = pending.pop()
            if typ in _TYPES or typ in value_types:
                continue
            if parse_type(typ).kind=='inout':
                pending.append(parse_type(typ).args[0].spelling())
                continue
            value_types[typ] = len(value_types)
            if (parts:=map_parts(typ)) is not None:
                pending.extend([*parts,parts[0]+'[]',parts[1]+'[]'])
                option='Option<'+parts[1]+'>'
                if option in code['enums']:pending.append(option)
            elif (element := array_item(typ)) is not None:
                pending.append(element)
            elif parse_type(typ).kind=='function':
                pending.extend(arg.spelling() for arg in reversed(parse_type(typ).args))
            elif typ in code['records']:
                pending.extend(reversed(list(code['records'][typ].values())))
            else:
                for fields in reversed(list(code['enums'][typ].values())):
                    pending.extend(reversed(list(fields.values())))
    for fn in functions.values():
        for typ in (*fn['params'], *fn.get('captures',[]), fn['result']):
            register_type(typ)
        for ins in fn['instructions']:
            if ins['op']=='VALUE_COLLECTION' and ins['arg']['method'] in ('pop','remove'):
                register_type(ins['arg']['result'])
                if ins['arg']['method']=='pop':register_type(parse_type(ins['arg']['result']).args[0].spelling()+'[]')
            elif ins['op']=='RESULT_DECODE':
                register_type(ins['arg']['result'])
            elif ins['op']=='RESULT_BUILTIN':
                register_type(ins['arg']['result'])
                register_type('int[]')
                if ins['arg']['name'].startswith('json'):register_type('str[]')
            elif ins['op'] in ('ARRAY','MAP','RECORD','ENUM','ENUM_IS','ENUM_GET'):
                register_type(ins['arg']['type'])
            elif ins['op']=='PROPAGATE':
                register_type(ins['arg']['source'])
                register_type(ins['arg']['target'])
            elif ins['op'] in ('FUNCTION','CLOSURE'):
                register_type(signature(ins['arg']))
            elif ins['op']=='INVOKE':
                register_type(ins['arg'])
            elif ins['op']=='CAPTURE_VALUE' and isinstance(ins['arg'],dict):
                register_type(ins['arg']['type'])
            elif ins['op']=='ACCESS':
                register_type(ins['arg']['type'])
            elif ins['op'] == 'ITER_SNAPSHOT':
                register_type('str[]')
                register_type('int[]')
            elif ins['op'] == 'BUILTIN' and ins['arg'] in ('join','split'):
                register_type('str[]')
            elif ins['op'] == 'BUILTIN' and ins['arg'] in ('float64Bytes','crc32','utf8Encode'):
                register_type('int[]')
    def type_id(typ):
        return -1-list(_TYPES).index(typ) if typ in _TYPES else value_types[typ]
    def check(value, typ):
        if parse_type(typ).kind=='inout':
            return f'pixel_check_access(c,{value},{type_id(parse_type(typ).args[0].spelling())});'
        if typ in _TYPES:
            return f'pixel_check_type(c,{value},{_TYPES[typ]});'
        if map_parts(typ) is not None:
            return f'pixel_check_map(c,{value},{value_types[typ]});'
        if typ in code['records']:
            return f'pixel_check_record(c,{value},{value_types[typ]});'
        if typ in code['enums']:
            return f'pixel_check_enum(c,{value},{value_types[typ]});'
        if parse_type(typ).kind=='function':
            return f'pixel_check_callable(c,{value},{value_types[typ]});'
        return f'pixel_check_array(c,{value},{value_types[typ]});'
    lines = [f'#define PIXEL_MAX_CALL_DEPTH {max_depth}',Path(__file__).with_name('native_runtime.h').read_text(),Path(__file__).with_name('native_map.h').read_text(),Path(__file__).with_name('native_io.h').read_text(),Path(__file__).with_name('native_session.h').read_text(),Path(__file__).with_name('native_json.h').read_text(),Path(__file__).with_name('native_unicode.h').read_text(),Path(__file__).with_name('native_decode.h').read_text()]
    lines += ['static size_t pixel_key_type_length(int type) { switch (type) {']
    for typ,index in value_types.items():lines += [f'case {index}: return {len(typ.encode("utf-8"))};']
    lines += ['default: return 0; } }']
    lines += ['static int pixel_type_comparable(int type) { switch (type) {']
    for typ,index in value_types.items():
        lines += [f'case {index}: return {int(_supported_type(typ,code["records"],code["enums"],comparable=True))};']
    lines += ['default: return 0; } }']
    lines += ['static int pixel_json_option_type(int type) { switch (type) {']
    for typ,index in value_types.items():
        parsed=parse_type(typ)
        if typ in code['enums'] and parsed.name=='Option':
            lines += [f'case {index}: return {2 if parse_type(parsed.args[0].spelling()).name=="Option" else 1};']
    lines += ['default: return 0; } }']
    callable_types={typ:index for typ,index in value_types.items() if parse_type(typ).kind=='function'}
    lines += ['static void pixel_check_callable(PixelContext *c, PixelValue value, int type) {',
              'pixel_check_type(c,value,PIXEL_CALLABLE); switch (type) {']
    for expected,index in callable_types.items():
        allowed=[actual_id for actual,actual_id in callable_types.items() if is_assignable(parse_type(expected),parse_type(actual))]
        condition=' || '.join(f'value.array->type=={actual_id}' for actual_id in allowed) or '0'
        lines += [f'case {index}: if ({condition}) return; break;']
    lines += ['default: break; } pixel_fail(c,"Callable signature mismatch"); }']
    lines += ['static void pixel_check_array_item(PixelContext *c, PixelValue array, PixelValue value) {',
              'pixel_check_type(c,array,PIXEL_ARRAY); switch (array.array->type) {']
    for typ,index in value_types.items():
        if (element:=array_item(typ)) is not None:
            lines += [f'case {index}:',check('value',element),'return;']
    lines += ['default: pixel_fail(c,"Unknown array type"); } }']
    field_names = sorted({field for typ in value_types if typ in code['records'] for field in code['records'][typ]}
                         | {field for typ in value_types if typ in code['enums'] for fields in code['enums'][typ].values() for field in fields}
                         | {variant for typ in value_types if typ in code['enums'] for variant in code['enums'][typ]}
                         | {ins['arg'] for fn in functions.values() for ins in fn['instructions'] if ins['op']=='FIELD'}
                         | {part['field'] for fn in functions.values() for ins in fn['instructions']
                            if ins['op'] in ('VALUE_CHECK','VALUE_SET','ACCESS')
                            for part in (ins['arg']['path'] if ins['op']=='ACCESS' else ins['arg']) if 'field' in part})
    field_ids = {name:index for index,name in enumerate(field_names)}
    for name,index in field_ids.items():
        points=','.join(str(ord(ch)) for ch in name) or '0'
        lines += [f'static const uint32_t pixel_field_text_{index}[] = {{{points}}};',
                  f'static PixelString pixel_field_name_{index} = {{SIZE_MAX,{len(name)},pixel_field_text_{index}}};']
    for typ,index in value_types.items():
        if typ in code['records']:
            names=','.join(f'&pixel_field_name_{field_ids[name]}' for name in code['records'][typ]) or 'NULL'
            lines += [f'static PixelString *const pixel_record_names_{index}[] = {{{names}}};']
        elif typ in code['enums']:
            for tag,fields in enumerate(code['enums'][typ].values()):
                names=','.join(f'&pixel_field_name_{field_ids[name]}' for name in fields) or 'NULL'
                lines += [f'static PixelString *const pixel_enum_names_{index}_{tag}[] = {{{names}}};']
    def enum_tag(typ,variant):
        return list(code['enums'][typ]).index(variant)
    def new_enum(typ,variant):
        index,tag=value_types[typ],enum_tag(typ,variant)
        return (f'pixel_enum_new(c,{len(code["enums"][typ][variant])},{index},{tag},'
                f'&pixel_field_name_{field_ids[variant]},pixel_enum_names_{index}_{tag})')
    # Runtime schemas use numeric type references so recursive records need no
    # construction-time recursion or host-language decoding fallback.
    for typ,index in value_types.items():
        groups=[(None,code['records'][typ])] if typ in code['records'] else list(enumerate(code['enums'][typ].values())) if typ in code['enums'] else []
        for tag,fields in groups:
            suffix=str(index) if tag is None else f'{index}_{tag}'
            entries=','.join(f'{{&pixel_field_name_{field_ids[field]},{type_id(field_type)}}}' for field,field_type in fields.items()) or '{NULL,0}'
            lines += [f'static const PixelDecodeField pixel_decode_fields_{suffix}[] = {{{entries}}};']
        if typ in code['enums']:
            lines += [f'static const PixelDecodeVariant pixel_decode_variants_{index}[] = {{']
            for tag,(variant,fields) in enumerate(code['enums'][typ].items()):
                lines += [f'{{&pixel_field_name_{field_ids[variant]},{tag},{len(fields)},pixel_decode_fields_{index}_{tag},pixel_enum_names_{index}_{tag}}},']
            lines += ['};']
    descriptor_names={}
    scalar_kinds={'json':1,'int':2,'float64':3,'bool':4,'str':5,'unit':0}
    for ordinal,typ in enumerate([*_TYPES,*value_types]):
        descriptor=f'pixel_decode_type_{ordinal}'; descriptor_names[typ]=descriptor
        kind=scalar_kinds.get(typ,0); first=second=count=0; fields=names=variants='NULL'
        if (element:=array_item(typ)) is not None:
            kind=6; first=type_id(element)
        elif (parts:=map_parts(typ)) is not None:
            kind=7; first,second=map(type_id,parts)
        elif typ in code['records']:
            index=value_types[typ]; kind=0 if typ=='Error' else 8; count=len(code['records'][typ])
            fields=f'pixel_decode_fields_{index}'; names=f'pixel_record_names_{index}'
        elif typ in code['enums']:
            index=value_types[typ]; kind=9; count=len(code['enums'][typ]); variants=f'pixel_decode_variants_{index}'
            if parse_type(typ).name=='Option':
                kind=10; first=type_id(parse_type(typ).args[0].spelling()); second=enum_tag(typ,'None')
        lines += [f'static const PixelDecodeSchema {descriptor} = {{{kind},{first},{second},{count},{fields},{names},{variants},{json.dumps(typ)}}};']
    lines += ['static const PixelDecodeSchema *pixel_decode_schema(int type) { switch (type) {']
    for typ,descriptor in descriptor_names.items():lines += [f'case {type_id(typ)}: return &{descriptor};']
    lines += ['default: { static const PixelDecodeSchema unknown={.spelling="unknown"}; return &unknown; } } }']
    lines += ['static void pixel_check_map_key(PixelContext *c, PixelValue map, PixelValue key) {',
              'pixel_check_type(c,map,PIXEL_MAP); switch (map.array->type) {']
    for typ,index in value_types.items():
        if (parts:=map_parts(typ)) is not None:lines += [f'case {index}:',check('key',parts[0]),'return;']
    lines += ['default: pixel_fail(c,"Unknown map type"); } }']
    for which,position in (('keys',0),('values',1)):
        lines += [f'static PixelValue pixel_map_{which}(PixelContext *c, PixelValue map) {{',
                  'pixel_check_type(c,map,PIXEL_MAP); PixelValue result; switch (map.array->type) {']
        for typ,index in value_types.items():
            if (parts:=map_parts(typ)) is not None:
                lines += [f'case {index}: result=pixel_array_new(c,map.array->length,{value_types[parts[position]+"[]"]}); break;']
        lines += ['default: pixel_fail(c,"Unknown map type"); }',
                  'size_t output=0;',
                  f'for (size_t i=pixel_map_next(map.array,0); i<map.array->data[1].array->length; i=pixel_map_next(map.array,i+1)) pixel_array_init(result.array,output++,pixel_retain(pixel_map_pair(map.array,i)->data[{position}]));',
                  'return result; }']
    lines += ['static PixelValue pixel_map_lookup(PixelContext *c, PixelValue map, PixelValue key) {',
              'pixel_check_map_key(c,map,key); size_t slot=pixel_map_find(c,map.array,key); PixelValue result;',
              'switch (map.array->type) {']
    for typ,index in value_types.items():
        if (parts:=map_parts(typ)) is not None:
            option='Option<'+parts[1]+'>'
            if option in value_types:
                lines += [f'case {index}: result=slot==SIZE_MAX ? {new_enum(option,"None")} : {new_enum(option,"Some")}; break;']
    lines += ['default: pixel_fail(c,"Map lookup result schema is missing"); }',
              'if (slot!=SIZE_MAX) result.array->data[0]=pixel_retain(pixel_map_pair(map.array,slot)->data[1]); return result; }']
    if any(ins['op'] in ('RESULT_BUILTIN','RESULT_DECODE') for fn in functions.values() for ins in fn['instructions']):
        error_id=value_types['Error']
        lines += ['static PixelValue pixel_decode_error(PixelContext *c, PixelDecoded decoded, const char *operation) {',
                  f'PixelValue value=pixel_record_new(c,7,{error_id},pixel_record_names_{error_id});']
        fields={'kind':'decoded.kind','code':'decoded.code','message':'decoded.message',
                'operation':'decoded.operation ? decoded.operation : operation','path':'""','expected':'decoded.expected ? decoded.expected : ""','actual':'decoded.actual ? decoded.actual : ""'}
        for slot,name in enumerate(code['records']['Error']):
            if name=='path':
                lines += [f'value.array->data[{slot}]=decoded.path.kind ? pixel_slice(c,decoded.path,0,decoded.path.string->length>1024 ? 1024 : decoded.path.string->length) : pixel_ascii(c,"");']
            elif name=='actual':
                lines += [f'value.array->data[{slot}]=decoded.actual_value.kind ? pixel_slice(c,decoded.actual_value,0,decoded.actual_value.string->length>256 ? 256 : decoded.actual_value.string->length) : pixel_ascii(c,{fields[name]});']
            else:
                lines += [f'value.array->data[{slot}]=pixel_ascii(c,{fields[name]});']
        lines += ['return value; }']
    lines += ['static PixelValue pixel_field(PixelContext *c, PixelValue value, int field) {',
              'pixel_check_type(c,value,PIXEL_RECORD);', 'switch (value.array->type) {']
    for typ,index in value_types.items():
        if typ in code['records']:
            lines += [f'case {index}: switch (field) {{']
            for slot,name in enumerate(code['records'][typ]):
                lines += [f'case {field_ids[name]}: return pixel_retain(value.array->data[{slot}]);']
            lines += ['default: break; } break;']
    lines += ['default: break; } pixel_fail(c,"Invalid record field access"); return pixel_value(PIXEL_INT,0); }']
    lines += ['static size_t pixel_update_slot(PixelContext *c, PixelValue parent, int field, PixelValue index) {',
              'if (field<0) {',
              'if (parent.kind==PIXEL_MAP) { pixel_check_map_key(c,parent,index); return pixel_map_find(c,parent.array,index); }',
              'if (parent.kind!=PIXEL_ARRAY) pixel_fail(c,"Value update index requires an array or map");',
              'int64_t slot=pixel_typed(c,index,PIXEL_INT);',
              'if (slot<0 || (uint64_t)slot>=parent.array->length) pixel_fail(c,"Value update index out of bounds");',
              'return (size_t)slot; }',
              'if (parent.kind!=PIXEL_RECORD) pixel_fail(c,"Unknown value update field");',
              'switch (parent.array->type) {']
    for typ,index in value_types.items():
        if typ in code['records']:
            lines += [f'case {index}: switch (field) {{']
            for slot,name in enumerate(code['records'][typ]):
                lines += [f'case {field_ids[name]}: return {slot};']
            lines += ['default: break; } break;']
    lines += ['default: break; } pixel_fail(c,"Unknown value update field"); }']
    lines += ['static PixelValue pixel_selected_child(PixelContext *c, PixelValue parent, size_t slot) {',
              'if (parent.kind==PIXEL_MAP) { if (slot==SIZE_MAX) pixel_fail(c,"Value update map key does not exist"); return pixel_map_pair(parent.array,slot)->data[1]; }',
              'return pixel_item(parent.array,slot); }',
              'static PixelValue pixel_replace_child(PixelContext *c, PixelValue parent, size_t slot, PixelValue key, PixelValue value) {',
              'if (parent.kind==PIXEL_MAP) { switch (parent.array->type) {']
    for typ,index in value_types.items():
        if (parts:=map_parts(typ)) is not None:
            lines += [f'case {index}:',check('key',parts[0]),check('value',parts[1]),'break;']
    lines += ['default: pixel_fail(c,"Unknown map type"); } return pixel_map_set(c,parent,pixel_retain(key),value); }',
              'if (parent.kind==PIXEL_ARRAY) { pixel_check_array_item(c,parent,value); return pixel_array_update(c,parent,slot,value,0); }',
              'pixel_check_type(c,parent,PIXEL_RECORD); switch (parent.array->type) {']
    for typ,index in value_types.items():
        if typ in code['records']:
            lines += [f'case {index}: switch (slot) {{']
            for slot,field_type in enumerate(code['records'][typ].values()):
                lines += [f'case {slot}:',check('value',field_type),'break;']
            lines += ['default: pixel_fail(c,"Unknown value update field"); } break;']
    lines += ['default: pixel_fail(c,"Unknown record type"); }',
              'PixelValue result=pixel_record_new(c,parent.array->length,parent.array->type,parent.array->names);',
              'for (size_t i=0; i<parent.array->length; i++) result.array->data[i]=i==slot ? value : pixel_retain(parent.array->data[i]);',
              'return result; }',
              'static PixelValue pixel_update_path(PixelContext *c, PixelValue base, const int *fields, PixelValue *indices, size_t count, int update, PixelValue value) {',
              'PixelValue parents[128],keys[128]; size_t slots[128]; size_t index=0; PixelValue current=base;',
              'for (size_t depth=0; depth<count; depth++) {',
              'if (pixel_cancelled) pixel_fail(c,"Execution cancelled");',
              'parents[depth]=current;',
              'PixelValue key=fields[depth]<0 ? indices[index++] : pixel_value(PIXEL_INT,0);',
              'keys[depth]=key;',
              'slots[depth]=pixel_update_slot(c,current,fields[depth],key);',
              'if (depth+1<count) current=pixel_selected_child(c,current,slots[depth]); }',
              'if (update) for (size_t depth=count; depth>0; depth--) value=pixel_replace_child(c,parents[depth-1],slots[depth-1],keys[depth-1],value);',
              'return value; }']
    lines += ['static void pixel_check_dynamic(PixelContext *c, PixelValue value, int type) { switch (type) {']
    for typ in [*_TYPES,*value_types]:
        lines += [f'case {type_id(typ)}:',check('value',typ),'return;']
    lines += ['default: pixel_fail(c,"Unknown access value type"); } }']
    lines += [Path(__file__).with_name('native_access.h').read_text()]
    for index, constant in enumerate(code['constants']):
        if constant['type'] == 'str':
            points = ','.join(str(ord(ch)) for ch in constant['value']) or '0'
            lines += [f'static const uint32_t pixel_text_{index}[] = {{{points}}};',
                      f'static PixelString pixel_string_{index} = {{SIZE_MAX,{len(constant["value"])},pixel_text_{index}}};']
    lines += [f'static PixelValue pixel_fn_{i}(PixelContext *, PixelValue *);' for i in ids.values()]
    lines += ['static PixelValue pixel_invoke(PixelContext *c, PixelValue callable, PixelValue *parameters, size_t count, int pure) {',
              'size_t size=count+callable.array->length;',
              'PixelValue *args=calloc(size ? size : 1,sizeof(PixelValue));',
              'if (!args) pixel_fail(c,"Allocation failed");',
              'for (size_t i=0; i<count; i++) args[i]=parameters[i];',
              'for (size_t i=0; i<callable.array->length; i++) args[count+i]=pixel_retain(callable.array->data[i]);',
              'PixelValue result; switch (callable.array->tag) {']
    for target in functions:
        if target not in callable_targets:
            continue
        target_fn=functions[target]
        lines += [f'case {ids[target]}:',
                  f'if (count!={len(target_fn["params"])} || callable.array->length!={len(target_fn.get("captures",[]))}) pixel_fail(c,"Invalid callable environment");']
        if target in effects:
            lines += ['if (pure) pixel_fail(c,"Pure function cannot call an effect fn");']
        lines += [f'result=pixel_fn_{ids[target]}(c,args); break;']
    lines += ['default: pixel_fail(c,"Unknown callable target"); } free(args); return result; }']
    for name, fn in functions.items():
        fid = ids[name]
        releases=_dead_local_releases(fn)
        lines += [f'static PixelValue pixel_fn_{fid}(PixelContext *c, PixelValue *args) {{',
                  f'PixelFrame *frame=pixel_frame_enter(c,{fn["locals"]});',
                  'size_t base=frame->base;']
        for index, typ in enumerate([*fn['params'],*fn.get('captures',[])]):
            lines += [check(f'args[{index}]',typ), f'frame->bindings[{index}].value = args[{index}];']
            if parse_type(typ).kind=='inout':
                lines += [f'pixel_access_claim(c,args[{index}],frame);']
            if index>=len(fn['params']):
                lines += [f'frame->bindings[{index}].readonly=1;']
        lines += ['PixelValue a, b, result;', 'int64_t integer, start, end;']
        for pc, ins in enumerate(fn['instructions']):
            op, arg = ins['op'], ins.get('arg')
            lines += [f'p{pc}: pixel_tick(c, {fid}, {pc});']
            if op == 'PUSH':
                constant = code['constants'][arg]
                if constant['type'] == 'str':
                    lines += [f'pixel_push(c,(PixelValue){{.kind=PIXEL_STRING,.string=&pixel_string_{arg}}});']
                elif constant['type'] == 'float64':
                    lines += [f'pixel_push(c, pixel_float(c, {constant["value"].hex()}));']
                else:
                    number = int(constant['value'])
                    literal = 'INT64_MIN' if number == -(1 << 63) else f'INT64_C({number})'
                    lines += [f'pixel_push(c, pixel_value({_TYPES[constant["type"]]}, {literal}));']
            elif op == 'LOAD':
                lines += [f'a=frame->bindings[{arg}].value;',
                          'if (!a.kind) pixel_fail(c,"Read of uninitialized memory");',
                          'pixel_push(c,a.kind==PIXEL_ACCESS ? pixel_access_read(c,a) : pixel_retain(a));']
            elif op in ('BIND', 'STORE'):
                lines += ['a=pixel_pop(c,base);',
                          'if (a.kind==PIXEL_ACCESS) pixel_fail(c,"inout access cannot be stored as a value");']
                if op=='STORE':
                    lines += [f'if (frame->bindings[{arg}].readonly) pixel_fail(c,"Captured values are readonly");']
                    lines += [f'if (frame->bindings[{arg}].value.kind==PIXEL_ACCESS) pixel_access_write(c,frame->bindings[{arg}].value,a); else {{']
                else:
                    lines += [f'frame->bindings[{arg}].readonly=0;']
                lines += [f'pixel_access_direct_write(c,frame,{arg}); pixel_release(c,frame->bindings[{arg}].value); frame->bindings[{arg}].value=a;']
                if op=='STORE':lines += ['}']
            elif op=='CAPTURE_VALUE':
                slot=arg['slot'] if isinstance(arg,dict) else arg
                lines += [f'if (!frame->bindings[{slot}].value.kind) pixel_fail(c,"Capture of uninitialized binding");']
                lines += [f'if (frame->bindings[{slot}].value.kind==PIXEL_ACCESS) pixel_fail(c,"inout parameters cannot be captured");']
                if isinstance(arg,dict):
                    lines += [check(f'frame->bindings[{slot}].value',arg['type'])]
                lines += [f'pixel_push(c,pixel_retain(frame->bindings[{slot}].value));']
            elif op in ('FUNCTION','CLOSURE'):
                captures=functions[arg].get('captures',[]) if op=='CLOSURE' else []
                lines += [f'if (c->size-base < {len(captures)}) pixel_fail(c,"Operand stack underflow");',
                          f'result=pixel_callable_new(c,{len(captures)},{value_types[signature(arg)]},{ids[arg]});']
                for index in reversed(range(len(captures))):
                    lines += ['a=pixel_pop(c,base);',check('a',captures[index]),f'result.array->data[{index}]=a;']
                lines += ['pixel_push(c,result);']
            elif op=='INVOKE':
                parameters=parse_type(arg).args[:-1]
                lines += ['{',f'PixelValue arguments[{max(1,len(parameters))}];']
                for index in reversed(range(len(parameters))):
                    lines += [f'arguments[{index}]=pixel_pop(c,base);',check(f'arguments[{index}]',parameters[index].spelling())]
                lines += ['b=pixel_pop(c,base);',check('b',arg),
                          f'result=pixel_invoke(c,b,arguments,{len(parameters)},{int(name not in effects)});',
                          f'c->function={fid}; c->pc={pc}; pixel_release(c,b); pixel_push(c,result);','}']
            elif op == 'POP':
                lines += ['pixel_release(c,pixel_pop(c,base));']
            elif op == 'DUP':
                lines += ['a = pixel_pop(c, base); pixel_push(c,a); pixel_push(c,pixel_retain(a));']
            elif op == 'DUP2':
                lines += ['b = pixel_pop(c,base); a = pixel_pop(c,base);',
                          'pixel_push(c,a); pixel_push(c,b); pixel_push(c,pixel_retain(a)); pixel_push(c,pixel_retain(b));']
            elif op in _BINARY:
                lines += ['b = pixel_pop(c,base); a = pixel_pop(c,base);',
                          f'result = pixel_binary(c,a,b,{_BINARY.index(op)});',
                          'pixel_release(c,a); pixel_release(c,b); pixel_push(c,result);']
            elif op == 'CONCAT':
                lines += ['b = pixel_pop(c,base); a = pixel_pop(c,base); result = pixel_concat(c,a,b);',
                          'pixel_release(c,a); pixel_release(c,b); pixel_push(c,result);']
            elif op == 'INDEX':
                lines += ['b=pixel_pop(c,base); a=pixel_pop(c,base);',
                          'if (a.kind==PIXEL_MAP) { pixel_check_map_key(c,a,b); size_t slot=pixel_map_find(c,a.array,b);',
                          'if (slot==SIZE_MAX) pixel_fail(c,"Missing map key"); result=pixel_retain(pixel_map_pair(a.array,slot)->data[1]);',
                          '} else result=pixel_index(c,a,pixel_typed(c,b,PIXEL_INT));',
                          'pixel_release(c,a); pixel_release(c,b); pixel_push(c,result);']
            elif op=='MAP':
                typ=arg['type']; key_type,value_type=map_parts(typ); count=arg['count']
                lines += ['{',f'PixelValue *items=calloc({max(1,count)},sizeof(PixelValue));',
                          'if (!items) pixel_fail(c,"Allocation failed");',
                          f'for (size_t i={count}; i>0; i--) items[i-1]=pixel_pop(c,base);',
                          f'result=pixel_map_new(c,{value_types[typ]},{int(key_type=="str")});',
                          f'for (size_t i=0; i<{count}; i+=2) {{',check('items[i]',key_type),check('items[i+1]',value_type),
                          'if (pixel_map_find(c,result.array,items[i])!=SIZE_MAX) pixel_fail(c,"Duplicate map key");',
                          'a=pixel_map_set(c,result,items[i],items[i+1]); pixel_release(c,result); result=a;',
                          '} free(items); pixel_push(c,result); }']
            elif op == 'ARRAY':
                count, typ = arg['count'], arg['type']
                lines += [f'if (c->size-base < {count}) pixel_fail(c,"Operand stack underflow");']
                if count <= 256:
                    # Publish literals through the same compact leaf representation
                    # as decoded arrays and updates. The operand stack owns inputs
                    # until the leaf has retained any non-scalar children.
                    lines += [f'for (size_t i=0; i<{count}; i++) {{',
                              f'a=c->stack[c->size-{count}+i];', check('a',array_item(typ)), '}',
                              f'result=pixel_array_wrap(c,pixel_leaf_new(c,c->stack+c->size-{count},{count}),{count},{value_types[typ]});',
                              f'for (size_t i=0; i<{count}; i++) pixel_release(c,pixel_pop(c,base));',
                              'pixel_push(c,result);']
                else:
                    lines += [f'result = pixel_array_new(c,{count},{value_types[typ]});',
                              f'for (size_t i={count}; i>0; i--) {{', 'a = pixel_pop(c,base);',
                              check('a',array_item(typ)), 'pixel_array_init(result.array,i-1,a);', '}', 'pixel_push(c,result);']
            elif op == 'RECORD':
                typ, fields = arg['type'], arg['fields']
                schema = code['records'][typ]
                slots = {name:index for index,name in enumerate(schema)}
                lines += [f'if (c->size-base < {len(fields)}) pixel_fail(c,"Operand stack underflow");',
                          f'result = pixel_record_new(c,{len(schema)},{value_types[typ]},pixel_record_names_{value_types[typ]});']
                for field in reversed(fields):
                    lines += ['a = pixel_pop(c,base);',check('a',schema[field]),f'result.array->data[{slots[field]}] = a;']
                lines += ['pixel_push(c,result);']
            elif op == 'FIELD':
                lines += ['a = pixel_pop(c,base);',f'result = pixel_field(c,a,{field_ids[arg]});',
                          'pixel_release(c,a); pixel_push(c,result);']
            elif op == 'ENUM':
                typ,variant,fields=arg['type'],arg['variant'],arg['fields']
                schema=code['enums'][typ][variant]
                slots={name:index for index,name in enumerate(schema)}
                lines += [f'if (c->size-base < {len(fields)}) pixel_fail(c,"Operand stack underflow");',
                          f'result = {new_enum(typ,variant)};']
                for field in reversed(fields):
                    lines += ['a = pixel_pop(c,base);',check('a',schema[field]),f'result.array->data[{slots[field]}] = a;']
                lines += ['pixel_push(c,result);']
            elif op in ('ENUM_IS','ENUM_GET'):
                typ,variant=arg['type'],arg['variant']
                lines += ['a = pixel_pop(c,base);',check('a',typ)]
                tag=enum_tag(typ,variant)
                if op=='ENUM_IS':
                    lines += [f'result = pixel_value(PIXEL_BOOL,a.array->tag=={tag});']
                else:
                    slot=list(code['enums'][typ][variant]).index(arg['field'])
                    lines += [f'if (a.array->tag!={tag}) pixel_fail(c,"Enum payload projection requires matching variant");',
                              f'result = pixel_retain(a.array->data[{slot}]);']
                lines += ['pixel_release(c,a); pixel_push(c,result);']
            elif op=='PROPAGATE':
                source,target=arg['source'],arg['target']
                success,failure=('Ok','Err') if parse_type(source).name=='Result' else ('Some','None')
                lines += ['a = pixel_pop(c,base);',check('a',source),
                          f'if (a.array->tag=={enum_tag(source,success)}) {{',
                          'result = pixel_retain(a.array->data[0]); pixel_release(c,a); pixel_push(c,result);',
                          '} else {',f'if (a.array->tag!={enum_tag(source,failure)}) pixel_fail(c,"Invalid propagation variant");']
                if source==target:
                    lines += ['result = a;']
                else:
                    lines += [f'result = {new_enum(target,failure)};']
                    if failure=='Err':
                        lines += ['result.array->data[0] = pixel_retain(a.array->data[0]);']
                    lines += ['pixel_release(c,a);']
                lines += ['goto pixel_return;', '}']
            elif op=='ACCESS':
                path=arg['path']; count=sum('index' in part for part in path)
                fields=','.join(str(field_ids[part['field']]) if 'field' in part else '-1' for part in path) or '0'
                lines += ['{',f'const int fields[]={{{fields}}};',f'PixelValue indices[{max(1,count)}];']
                for index in reversed(range(count)):lines += [f'indices[{index}]=pixel_pop(c,base);']
                lines += [f'result=pixel_access_begin(c,frame,{arg["slot"]},fields,indices,{len(path)},{count},{type_id(arg["type"])});',
                          f'for (size_t i=0; i<{count}; i++) pixel_release(c,indices[i]);',
                          'pixel_push(c,result); }']
            elif op in ('VALUE_CHECK','VALUE_SET'):
                count=sum('index' in part for part in arg)
                fields=','.join(str(field_ids[part['field']]) if 'field' in part else '-1' for part in arg)
                lines += ['{',f'const int fields[]={{{fields}}};',f'PixelValue indices[{max(1,count)}];']
                lines += ['b=pixel_pop(c,base);'] if op=='VALUE_SET' else ['b=pixel_value(PIXEL_INT,0);']
                for index in reversed(range(count)):
                    lines += [f'indices[{index}]=pixel_pop(c,base);']
                lines += ['a=pixel_pop(c,base);',f'result=pixel_update_path(c,a,fields,indices,{len(arg)},{int(op=="VALUE_SET")},b);',
                          'pixel_release(c,a);',f'for (size_t i=0; i<{count}; i++) pixel_release(c,indices[i]);']
                if op=='VALUE_SET':lines += ['pixel_push(c,result);']
                lines += ['}']
            elif op == 'VALUE_COLLECTION':
                if arg['method']=='remove':
                    typ=arg['result']; element=parse_type(typ).args[0].spelling()
                    lines += ['{ PixelValue key=pixel_pop(c,base); a=pixel_pop(c,base); pixel_check_map_key(c,a,key);',
                              'switch (a.array->type) {']
                    for map_type,index in value_types.items():
                        if (parts:=map_parts(map_type)) is not None and parts[1]==element:
                            lines += [f'case {index}: break;']
                    lines += ['default: pixel_fail(c,"Collection method result disagrees with element type"); }',
                              'size_t slot=pixel_map_find(c,a.array,key);',
                              'if (slot==SIZE_MAX) {',f'b={new_enum(typ,"None")};',
                              '} else {',f'b={new_enum(typ,"Some")};',
                              'b.array->data[0]=pixel_retain(pixel_map_pair(a.array,slot)->data[1]); }',
                              'result=pixel_map_remove(c,a,key); pixel_release(c,a); pixel_release(c,key); pixel_push(c,result); pixel_push(c,b); }']
                elif arg['method']=='pop':
                    typ=arg['result']; element=parse_type(typ).args[0].spelling()
                    lines += ['a=pixel_pop(c,base);',check('a',element+'[]'),
                              'if (a.array->length) {',f'b={new_enum(typ,"Some")};',
                              'b.array->data[0]=pixel_retain(pixel_item(a.array,a.array->length-1));',
                              '} else {',f'b={new_enum(typ,"None")};','}',
                              'result=pixel_array_pop(c,a); pixel_release(c,a); pixel_push(c,result); pixel_push(c,b);']
                else:
                    lines += ['b=pixel_pop(c,base); a=pixel_pop(c,base);']
                    if arg['method']=='append':
                        lines += ['pixel_check_array_item(c,a,b); result=pixel_array_update(c,a,a.array->length,b,1);']
                    else:
                        lines += ['result=pixel_array_extend(c,a,b); pixel_release(c,b);']
                    lines += ['pixel_release(c,a); pixel_push(c,result); pixel_push(c,pixel_value(PIXEL_INT,0));']
            elif op in ('RESULT_BUILTIN','RESULT_DECODE'):
                typ=arg['result']; operation=arg['name'] if op=='RESULT_BUILTIN' else 'jsonDecode'
                lines += ['{']
                file_operation=operation in ('readText','readBytes','writeText','writeBytes')
                extra_argument=file_operation or operation in ('jsonGet','jsonAt')
                if op=='RESULT_DECODE':
                    lines += ['a=pixel_pop(c,base);']
                    expression=f'pixel_json_decode(c,a,{type_id(parse_type(typ).args[0].spelling())})'
                elif operation=='jsonParse':
                    lines += ['a=pixel_pop(c,base);',check('a','str')]
                    expression='pixel_json_parse(c,a)'
                elif operation.startswith('json'):
                    if operation in ('jsonGet','jsonAt'):
                        lines += ['b=pixel_pop(c,base);',check('b','str' if operation=='jsonGet' else 'int')]
                    else:lines += ['b=pixel_value(PIXEL_INT,0);']
                    lines += ['a=pixel_pop(c,base);',check('a','json')]
                    index=('jsonGet','jsonAt','jsonKeys','jsonAsString','jsonAsBool','jsonAsInt','jsonAsFloat64').index(operation)
                    expression=f'pixel_json_access(c,a,b,{index},{value_types["str[]"]})'
                elif file_operation:
                    if name not in effects:lines += ['pixel_fail(c,"External effects are forbidden inside a pure function");']
                    if operation!='readText':
                        value_type={'readBytes':'int','writeText':'str','writeBytes':'int[]'}[operation]
                        lines += ['b=pixel_pop(c,base);',check('b',value_type)]
                    else:lines += ['b=pixel_value(PIXEL_INT,0);']
                    lines += ['a=pixel_pop(c,base);',check('a','str')]
                    index=('readText','readBytes','writeText','writeBytes').index(operation)
                    expression=f'pixel_file(c,a,b,{index},{value_types["int[]"]})'
                else:
                    if operation=='zlibDecompress':
                        lines += ['integer=pixel_typed(c,pixel_pop(c,base),PIXEL_INT);']
                    lines += ['a=pixel_pop(c,base);',check('a','str' if operation in ('parseInt','parseFloat64') else 'int[]')]
                    if operation in ('zlibCompress','zlibDecompress'):
                        limit='integer' if operation=='zlibDecompress' else '0'
                        expression=f'pixel_zlib(c,a,{value_types["int[]"]},{int(operation=="zlibDecompress")},{limit})'
                    elif operation in ('parseInt','parseFloat64'):
                        expression=f'pixel_parse_number(c,a,{int(operation=="parseFloat64")})'
                    else:
                        helper='pixel_utf8_decode' if operation=='utf8Decode' else 'pixel_float_from_bytes'
                        expression=f'{helper}(c,a)'
                lines += [f'PixelDecoded decoded={expression};',
                          'if (decoded.code) {',f'result={new_enum(typ,"Err")};',
                          f'result.array->data[0]=pixel_decode_error(c,decoded,"{operation}");',
                          '} else {',f'result={new_enum(typ,"Ok")};',
                          'result.array->data[0]=decoded.value; }',
                          ('pixel_release(c,decoded.path); pixel_release(c,decoded.actual_value); ' if op=='RESULT_DECODE' else '')+('pixel_release(c,b); ' if extra_argument else '')+'pixel_release(c,a); pixel_push(c,result); }']
            elif op == 'ITER_SNAPSHOT':
                lines += ['a=pixel_pop(c,base); if (a.kind==PIXEL_MAP) { result=pixel_map_keys(c,a);']
                if arg=='pair':lines += ['b=pixel_map_values(c,a); pixel_push(c,result); pixel_push(c,b);']
                else:lines += ['pixel_push(c,result);']
                lines += ['pixel_release(c,a); } else {',f'pixel_iteration(c,a,{int(arg == "pair")},{value_types["str[]"]},{value_types["int[]"]});','}']
            elif op=='BUILTIN' and arg in ('has','keys','lookup'):
                if arg=='keys':lines += ['a=pixel_pop(c,base); result=pixel_map_keys(c,a);']
                else:
                    lines += ['b=pixel_pop(c,base); a=pixel_pop(c,base); pixel_check_map_key(c,a,b);']
                    if arg=='has':lines += ['result=pixel_value(PIXEL_BOOL,pixel_map_find(c,a.array,b)!=SIZE_MAX);']
                    else:lines += ['result=pixel_map_lookup(c,a,b);']
                    lines += ['pixel_release(c,b);']
                lines += ['pixel_release(c,a); pixel_push(c,result);']
            elif op == 'BUILTIN' and arg in ('jsonNull','jsonFrom','jsonStringify','jsonKind'):
                if arg=='jsonNull':lines += ['pixel_push(c,pixel_json_node(c,PIXEL_JSON_NULL,0));']
                else:
                    lines += ['a=pixel_pop(c,base);']
                    if arg!='jsonFrom':lines += [check('a','json')]
                    expression={'jsonFrom':'pixel_json_from(c,a)','jsonStringify':'pixel_json_stringify(c,a)',
                                'jsonKind':'pixel_ascii(c,pixel_json_kind_name(a.array->tag))'}[arg]
                    lines += [f'result={expression}; pixel_release(c,a); pixel_push(c,result);']
            elif op == 'BUILTIN' and arg=='input':
                if name not in effects:lines += ['pixel_fail(c,"External effects are forbidden inside a pure function");']
                lines += ['pixel_push(c,pixel_input(c));']
            elif op == 'BUILTIN' and arg in ('float64Bytes','crc32','utf8Encode'):
                lines += ['a=pixel_pop(c,base);']
                if arg=='crc32':
                    lines += [check('a','int[]'), 'result=pixel_crc32(c,a);']
                else:
                    helper='pixel_float_bytes' if arg=='float64Bytes' else 'pixel_utf8_encode'
                    lines += [f'result={helper}(c,a,{value_types["int[]"]});']
                lines += ['pixel_release(c,a); pixel_push(c,result);']
            elif op == 'BUILTIN' and arg=='string':
                lines += ['a=pixel_pop(c,base);',
                          'if (a.kind==PIXEL_STRING) result=pixel_retain(a);',
                          'else if (a.kind==PIXEL_BOOL) result=pixel_ascii(c,a.integer ? "true" : "false");',
                          'else if (a.kind==PIXEL_INT || a.kind==PIXEL_FLOAT) result=pixel_decimal_spelling(c,a);',
                          'else pixel_fail(c,"string conversion expects a scalar");',
                          'pixel_release(c,a); pixel_push(c,result);']
            elif op == 'BUILTIN' and arg=='split':
                lines += ['b=pixel_pop(c,base); a=pixel_pop(c,base);',
                          f'result=pixel_split(c,a,b,{value_types["str[]"]});',
                          'pixel_release(c,a); pixel_release(c,b); pixel_push(c,result);']
            elif op == 'BUILTIN' and arg=='arrayRepeat':
                lines += ['integer=pixel_typed(c,pixel_pop(c,base),PIXEL_INT); a=pixel_pop(c,base);',
                          'result=pixel_array_repeat(c,a,integer); pixel_release(c,a); pixel_push(c,result);']
            elif op == 'BUILTIN' and arg=='unicodeCategory':
                lines += ['a=pixel_pop(c,base); result=pixel_unicode_category(c,a); pixel_release(c,a); pixel_push(c,result);']
            elif op == 'BUILTIN' and arg == 'join':
                lines += ['b=pixel_pop(c,base); a=pixel_pop(c,base);',
                          check('a','str[]'), 'result=pixel_join(c,a,b);',
                          'pixel_release(c,a); pixel_release(c,b); pixel_push(c,result);']
            elif op == 'BUILTIN' and arg == 'replace':
                lines += ['{ PixelValue replacement=pixel_pop(c,base);',
                          'b=pixel_pop(c,base); a=pixel_pop(c,base);',
                          'result=pixel_replace(c,a,b,replacement);',
                          'pixel_release(c,a); pixel_release(c,b); pixel_release(c,replacement); pixel_push(c,result); }']
            elif op == 'BUILTIN' and arg == 'trim':
                lines += ['a=pixel_pop(c,base); result=pixel_trim(c,a); pixel_release(c,a); pixel_push(c,result);']
            elif op == 'BUILTIN' and arg == 'assert':
                lines += ['a=pixel_pop(c,base); pixel_check_type(c,a,PIXEL_BOOL);',
                          'if (!a.integer) pixel_fail(c,"Assertion failed"); pixel_push(c,a);']
            elif op == 'BUILTIN' and arg == 'len':
                lines += ['a = pixel_pop(c,base); integer = (int64_t)pixel_length(c,a);',
                          'pixel_release(c,a); pixel_push(c,pixel_value(PIXEL_INT,integer));']
            elif op == 'BUILTIN' and arg == 'slice':
                lines += ['end = pixel_typed(c,pixel_pop(c,base),PIXEL_INT);',
                          'start = pixel_typed(c,pixel_pop(c,base),PIXEL_INT); a = pixel_pop(c,base);',
                          'result = pixel_slice(c,a,start,end); pixel_release(c,a); pixel_push(c,result);']
            elif op == 'NOT':
                lines += ['a = pixel_pop(c,base); pixel_push(c,pixel_value(PIXEL_BOOL,!pixel_typed(c,a,PIXEL_BOOL)));']
            elif op == 'NEG':
                lines += ['a = pixel_pop(c,base);',
                          'if (a.kind == PIXEL_FLOAT) pixel_push(c,pixel_float(c,-a.real)); else {',
                          'integer = pixel_typed(c,a,PIXEL_INT);',
                          'if (integer == INT64_MIN) pixel_fail(c,"Signed 64-bit integer overflow");',
                          'pixel_push(c,pixel_value(PIXEL_INT,-integer)); }']
            elif op == 'JUMP':
                lines += [f'goto p{arg};']
            elif op in ('JUMP_IF_TRUE', 'JUMP_IF_FALSE'):
                lines += [f'if (pixel_typed(c,pixel_pop(c,base),PIXEL_BOOL) == {int(op == "JUMP_IF_TRUE")}) goto p{arg};']
            elif op == 'CALL':
                if name not in effects and arg in effects:
                    lines += ['pixel_fail(c,"Pure function cannot call an effect fn");']
                count = len(functions[arg]['params'])
                lines += ['{', f'PixelValue call_args[{max(1,count)}];']
                for index in reversed(range(count)):
                    lines += [f'call_args[{index}] = pixel_pop(c,base);']
                lines += [f'a = pixel_fn_{ids[arg]}(c,call_args);',
                          f'c->function = {fid}; c->pc = {pc}; pixel_push(c,a);', '}']
            elif op == 'RETURN':
                lines += ['result = pixel_pop(c,base); goto pixel_return;']
            elif op == 'PRINT':
                if name not in effects:
                    lines += ['pixel_fail(c,"External effects are forbidden inside a pure function");']
                lines += ['a = pixel_pop(c,base); pixel_print(c,a); pixel_release(c,a);']
            elif op == 'STACK_DEPTH':
                lines += ['pixel_push(c,pixel_value(PIXEL_INT,(int64_t)(c->size-base)));']
            elif op == 'UNWIND':
                lines += [f'integer = pixel_typed(c,frame->bindings[{arg}].value,PIXEL_INT);',
                          'if (integer < 0 || (uint64_t)integer > c->size-base) pixel_fail(c,"Invalid operand unwind depth");',
                          'pixel_access_unwind(c,frame,base+(size_t)integer);',
                          'while (c->size > base+(size_t)integer) pixel_release(c,pixel_pop(c,base));']
            elif op == 'TRAP':
                lines += ['pixel_fail(c,"Function reached unreachable terminator");']
            if pc in releases:
                slot=releases[pc]
                lines += [f'pixel_release(c,frame->bindings[{slot}].value); frame->bindings[{slot}].value=(PixelValue){{0}};']
        lines += ['pixel_fail(c,"Function fell off instruction stream");', 'pixel_return:;']
        lines += ['if (result.kind==PIXEL_ACCESS) pixel_fail(c,"inout access cannot escape a call");']
        if fn['result']!='unit':
            lines += [check('result',fn['result'])]
        lines += ['while (c->size > base) pixel_release(c,pixel_pop(c,base));',
                  'pixel_frame_leave(c,frame); return result;', '}']
    lines += ['int main(int argc, char **argv) {', 'PixelContext context = {0};',
              'PixelContext *c = &context; c->limit = UINT64_C(100000000); c->output_remaining = 1000000;',
              'if (!pixel_io_options(c,argc,argv)) return 64;',
              'signal(SIGINT,pixel_cancel); signal(SIGTERM,pixel_cancel);',
              'if (c->buffered_output && setvbuf(stdout,NULL,_IOFBF,65536)) pixel_fail(c,"Output buffer setup failed");']
    if session is not None:
        lines += ['if (c->session) { PixelValue state={0}; uint64_t identity;',
                  'while (pixel_session_request(c,&identity)) {',
                  f'if (!state.kind) state=pixel_fn_{ids[session["initialize"]]}(c,NULL);',
                  'PixelValue argument=pixel_retain(state);',
                  f'PixelValue next=pixel_fn_{ids[session["advance"]]}(c,&argument);',
                  'pixel_release(c,state); state=next; pixel_session_done(c,identity);',
                  '} pixel_release(c,state); } else {']
    else:
        lines += ['if (c->session) return 64;']
    for entry in code['entry']:
        lines += [f'pixel_release(c,pixel_fn_{ids[entry]}(c,NULL));']
    if session is not None: lines += ['}']
    lines += ['if (fflush(stdout) == EOF) pixel_fail(c,"Output write failed");']
    lines += ['pixel_release(c,c->input); if (c->read_root>=0) close(c->read_root); if (c->write_root>=0) close(c->write_root);', 'if (c->string_bytes || c->array_bytes || c->frame_bytes || c->frame || c->depth || c->accesses || c->access_bytes) pixel_fail(c,"Native value ownership leak");', 'if (c->report_stats) fprintf(stderr, "{\\"nativeStats\\":1,\\"steps\\":%llu}\\n", (unsigned long long)c->steps);', 'free(c->stack); return 0;', '}']
    return '\n'.join(lines) + '\n'


def entry_session(code):
    """Verify an explicit session entry, following only transparent unit forwarders.

    Source recovery may introduce a main wrapper. Its complete instruction body
    must only call the next entry and discard its unit result; no work is skipped
    when the native session loop invokes initialize and advance directly.
    """
    if len(code['entry']) != 1:
        raise ValueError('Compiler session requires one entry')
    functions = code['functions']
    current = code['entry'][0]
    visited = set()
    while current not in visited:
        visited.add(current)
        entry = functions.get(current)
        if entry is None or entry['params'] or entry.get('captures') or entry['result'] != 'unit':
            break
        instructions = entry['instructions']
        ops = [ins['op'] for ins in instructions]
        # A unit epilogue pushes the canonical placeholder consumed by RETURN.
        if len(instructions) < 2 or ops[-2:] != ['PUSH', 'RETURN']:
            break
        constant = instructions[-2]['arg']
        if (type(constant) is not int or not 0 <= constant < len(code['constants'])
                or code['constants'][constant] != {'type': 'int', 'value': 0}):
            break
        if ops == ['CALL', 'POP', 'PUSH', 'RETURN'] and entry['locals'] == 0:
            current = instructions[0]['arg']
            continue
        if ops == ['CALL', 'BIND', 'LOAD', 'CALL', 'BIND', 'PUSH', 'RETURN']:
            state, loaded, result = (instructions[i]['arg'] for i in (1, 2, 4))
            if (entry['locals'] == 2 and type(state) is int and type(result) is int
                    and {state, result} == {0, 1} and loaded == state):
                return dict(initialize=instructions[0]['arg'], advance=instructions[3]['arg'])
        break
    raise ValueError('Compiler entry must only initialize and advance session state, optionally through unit forwarding entries')


def build_native(code, output, *, cc=None, optimization=2, max_depth=256, session=None):
    """Build a standalone executable and original-instruction source map."""
    from .bootstrap import normalized
    if type(optimization) is not int or not 0 <= optimization <= 3:
        raise ValueError('Native optimization level must be 0..3')
    source = emit_c(code, max_depth=max_depth, session=session)  # Reject unsupported programs before writing artifacts.
    compiler = cc or shutil.which('clang') or shutil.which('cc')
    if not compiler:
        raise RuntimeError('A C11 compiler is required for native code generation')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    source_path = output.with_name(output.name + '.c')
    source_path.write_text(source)
    with tempfile.TemporaryDirectory(prefix='.pixel-native-', dir=output.parent) as temporary:
        executable = Path(temporary)/'program'
        # O1 is the compiler-development profile: optimize temporary storage
        # without expensive expansion of the large generated translation unit.
        optimization_flags = ['-fno-inline-functions'] if optimization == 1 else []
        subprocess.run([compiler, '-std=c11', f'-O{optimization}', *optimization_flags, '-ffp-contract=off', str(source_path), '-lm', '-lz', '-o', str(executable)],
                       check=True, capture_output=True, text=True, timeout=120)
        executable.replace(output)
    mapping = {'format': 'pixellang-native-source-map/2',
               'bytecodeSha256': hashlib.sha256(normalized(code).encode('utf-8',errors='surrogatepass')).hexdigest(),
               'session': session,
               'optimizationLevel': optimization,
               'maxCallDepth': max_depth,
               'sourceSha256': hashlib.sha256(source.encode()).hexdigest(),
               'executableSha256': hashlib.sha256(output.read_bytes()).hexdigest(),
               # PC already indexes the checksum-bound bytecode. Store only the
               # native function-id mapping instead of duplicating every span.
               'functions': [{'name': name, 'instructionCount': len(fn['instructions'])}
                             for name, fn in code['functions'].items()]}
    output.with_name(output.name + '.map.json').write_text(json.dumps(mapping, ensure_ascii=True))
    return output
