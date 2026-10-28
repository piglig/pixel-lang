"""Editor projections from native PixelLang compiler arenas.

This module does not parse, bind or type-check PixelLang. Symbol/type identities,
syntax boundaries and token ranges are supplied by the self-hosted compiler.
"""
from bisect import bisect_right
from .model import PixelError


class NativeSymbolIndex:
    def __init__(self, files, entry, analysis, *, editable_files=None):
        self.files = dict(files)
        self.editable_files = set(files if editable_files is None else editable_files)
        self.entry = entry
        self.analysis = analysis
        resolved = analysis['resolved']
        self.bound = resolved['bound']
        self.modules = self.bound['project']['modules']
        self.types = resolved['types']
        self.member_views = analysis['memberViews']
        self.paths = {module['path']: index for index, module in enumerate(self.modules)}
        self.symbols = {}
        self.occurrences = {}
        self.implicit_aliases = {}
        self.shorthands = {}
        self.diagnostics = analysis['diagnostics']
        self.project = {'sources': self.files}
        self.lines = {path: [0] + [i + 1 for i, char in enumerate(text) if char == '\n']
                      for path, text in self.files.items()}
        self.expressions = {(item['module'], item['node']): item['typeId']
                            for item in analysis['expressions']}
        self.definitions = {(item['module'], item['node']): item['typeId']
                            for item in resolved['definitions']}
        self.type_symbols = {}
        for identity, symbol in enumerate(self.bound['symbols']):
            if symbol['module'] < 0 or symbol['node'] < 0:
                continue
            mid, node_id = symbol['module'], symbol['node']
            node = self.node(mid, node_id)
            span = self.name_span(mid, node_id, symbol['name'], declaration=True)
            if symbol['kind'] == 'module' and span is None:
                literal = next((token for token in self.modules[mid]['syntax']['tokens']
                                if node['start'] <= token['start'] < node['end']
                                and token['kind'] == 'str'), None)
                if literal is not None:
                    span = self.span(mid, literal['start'], literal['end'])
                    self.implicit_aliases[identity] = literal['end']
            if span is None:
                continue
            self.symbols[identity] = dict(symbol, span=span, anchor=(mid, node_id))
            self.use(identity, span, True)
            if symbol['kind'] in ('record', 'enum', 'frame', 'type_parameter'):
                self.type_symbols[(mid, node_id)] = identity
        # Nominal generic parameters belong to native type definitions rather
        # than the value binder. Keep their original declaration identities.
        for type_id, typ in enumerate(self.types):
            if typ['kind'] != 'type_parameter' or typ['module'] < 0:
                continue
            mid = typ['module']
            for child in self.node(mid, typ['declaration'])['children']:
                parameter = self.node(mid, child)
                if parameter['kind'] != 'type_parameter' or parameter['text'] != typ['name']:
                    continue
                if (mid, child) not in self.type_symbols:
                    identity = ('type_parameter', mid, child)
                    span = self.name_span(mid, child, typ['name'])
                    self.symbols[identity] = dict(name=typ['name'], kind='type_parameter', module=mid,
                        node=child, typeId=type_id, span=span, anchor=(mid, child))
                    self.type_symbols[(mid, child)] = identity
                    self.use(identity, span, True)
        for reference in self.bound['references']:
            identity = reference['symbol']
            if identity not in self.symbols:
                continue
            span = self.name_span(reference['module'], reference['node'],
                                  self.symbols[identity]['name'])
            self.use(identity, span)
        # Type references, including nested frame and payload arguments, retain
        # the native resolver's nominal declaration identity.
        for annotation in resolved['annotations']:
            syntax = self.node(annotation['module'], annotation['node'])
            if syntax['kind'] == 'type' and '.' in syntax['text']:
                self.use_module_alias(annotation['module'], annotation['node'], syntax['text'].split('.')[0])
            typ = self.type_info(annotation['typeId'])
            if typ is None:
                continue
            identity = self.type_symbols.get((typ['module'], typ['declaration']))
            if typ['kind'] == 'type_parameter' and typ['module'] >= 0:
                owner = self.node(typ['module'], typ['declaration'])
                identity = next((self.type_symbols.get((typ['module'], child))
                                 for child in owner['children']
                                 if self.node(typ['module'], child)['kind'] == 'type_parameter'
                                 and self.node(typ['module'], child)['text'] == typ['name']), None)
            if identity is not None:
                self.use(identity, self.name_span(annotation['module'], annotation['node'],
                                                  self.symbols[identity]['name']))

        # Calls identify imported functions through native call
        # resolution, without reconstructing module/name lookup in the host.
        for call in analysis['calls']:
            if call['symbol'] in self.symbols:
                node = self.node(call['module'], call['node'])
                callee = node['children'][0] if node['kind'] == 'call' else call['node']
                self.use(call['symbol'], self.name_span(call['module'], callee,
                                                       self.symbols[call['symbol']]['name']))
        self.add_fields()
        self.add_variants()

    def expression_type(self, module, node):
        identity = self.expressions.get((module, node), -1)
        if identity < 0:
            reference = next((ref for ref in self.bound['references']
                              if ref['module'] == module and ref['node'] == node), None)
            if reference:
                identity = self.bound['symbols'][reference['symbol']]['typeId']
        return identity

    def add_variants(self):
        for type_id, typ in enumerate(self.types):
            if typ['kind'] != 'enum' or typ['module'] < 0:
                continue
            mid, declaration = typ['module'], typ['declaration']
            for child in self.node(mid, declaration)['children']:
                node = self.node(mid, child)
                if node['kind'] != 'variant_decl':
                    continue
                identity = ('variant', mid, declaration, node['text'])
                if identity in self.symbols:
                    continue
                span = self.name_span(mid, child, node['text'])
                self.symbols[identity] = dict(name=node['text'], kind='variant', module=mid,
                    node=child, typeId=type_id, span=span, anchor=(mid, child))
                self.use(identity, span, True)
        for mid, module in enumerate(self.modules):
            for node_id, node in enumerate(module['syntax']['nodes']):
                if node['kind'] == 'context_variant':
                    type_id = self.expression_type(mid, node_id)
                    typ = self.type_info(type_id)
                    if typ and typ['kind'] == 'function' and typ['arguments']:
                        type_id = typ['arguments'][-1]
                    typ = self.member_info(type_id)
                    if typ and typ['kind'] == 'enum':
                        identity = ('variant', typ['module'], typ['declaration'], node['text'])
                        if identity in self.symbols:
                            self.use(identity, self.name_span(mid, node_id, node['text']))
                if node['kind'] == 'field':
                    typ = self.member_info(self.expression_type(mid, node['children'][0]))
                    if typ and typ['kind'] == 'enum':
                        identity = ('variant', typ['module'], typ['declaration'], node['text'])
                        if identity in self.symbols:
                            self.use(identity, self.name_span(mid, node_id, node['text']))
                if node['kind'] == 'variant_pattern':
                    # Every recursive pattern has its own checked subject type.
                    typ = self.member_info(self.expression_type(mid, node_id))
                    if not typ or typ['kind'] != 'enum':
                        continue
                    identity = ('variant', typ['module'], typ['declaration'], node['text'])
                    if identity in self.symbols:
                        self.use(identity, self.name_span(mid, node_id, node['text']))
                    if not node['children']:
                        continue
                    qualifier = self.node(mid, node['children'][0])
                    if qualifier['kind'] != 'pattern_qualifier':
                        continue
                    type_node = qualifier['children'][0]
                    identity = self.type_symbols.get((typ['module'], typ['declaration']))
                    if identity is not None:
                        self.use(identity, self.name_span(mid, type_node, typ['name']))
                    parts = self.node(mid, type_node)['text'].split('.')
                    if len(parts) > 1:
                        self.use_module_alias(mid, type_node, parts[0])

    def add_fields(self):
        for typ in self.types:
            if typ['kind'] != 'record' or typ['module'] < 0:
                continue
            mid, declaration = typ['module'], typ['declaration']
            root = self.node(mid, declaration)
            schema = {field['name']: field['typeId'] for field in typ['fields']}
            for child in root['children']:
                node = self.node(mid, child)
                if node['kind'] != 'field_decl':
                    continue
                key = ('field', mid, declaration, node['text'])
                if key in self.symbols:
                    continue
                span = self.name_span(mid, child, node['text'])
                if span is not None:
                    self.symbols[key] = dict(name=node['text'], kind='field', module=mid, node=child,
                        typeId=schema.get(node['text'], -1), span=span, anchor=(mid, child))
                    self.use(key, span, True)
        for mid, module in enumerate(self.modules):
            for node_id, node in enumerate(module['syntax']['nodes']):
                if node['kind'] == 'field':
                    typ = self.member_info(self.expressions.get((mid, node['children'][0]), -1))
                    if typ:
                        key = ('field', typ['module'], typ['declaration'], node['text'])
                        if key in self.symbols:
                            self.use(key, self.name_span(mid, node_id, node['text']))
                if node['kind'] == 'construct':
                    typ = self.member_info(self.expressions.get((mid, node_id), -1))
                    if not typ:
                        continue
                    for child in node['children']:
                        item = self.node(mid, child)
                        if item['kind'] == 'initializer':
                            key = ('field', typ['module'], typ['declaration'], item['text'])
                            if key in self.symbols:
                                span = self.name_span(mid, child, item['text'])
                                self.use(key, span)
                                if not item['children'] and span is not None:
                                    variable = next((ref['symbol'] for ref in self.bound['references']
                                                     if ref['module'] == mid and ref['node'] == child), None)
                                    if variable in self.symbols:
                                        self.shorthands[self.span_key(span)] = (key, variable)

    def node(self, module, node):
        return self.modules[module]['syntax']['nodes'][node]

    def use_module_alias(self, module, node, name):
        identity = next((key for key, symbol in self.symbols.items()
                         if symbol['module'] == module and symbol['kind'] == 'module'
                         and symbol['name'] == name), None)
        if identity is not None:
            start = self.node(module, node)['start']
            token = next((token for token in self.modules[module]['syntax']['tokens']
                          if token['start'] == start and token['text'] == name), None)
            if token:
                self.use(identity, self.span(module, token['start'], token['end']))

    def span(self, module, start, end):
        path = self.modules[module]['path']
        lines = self.lines[path]
        def point(offset):
            row = bisect_right(lines, offset) - 1
            return dict(offset=offset, line=row + 1, column=offset - lines[row] + 1)
        return dict(source=path, kind='text', start=point(start), end=point(end),
                    min=[0, 0], max=[0, 0], points=[])

    def name_span(self, module, node_id, name, declaration=False):
        node = self.node(module, node_id)
        candidates = [token for token in self.modules[module]['syntax']['tokens']
                      if node['start'] <= token['start'] < node['end'] and token['text'] == name]
        if declaration and node['kind'] in ('fn', 'flow', 'record', 'enum', 'frame', 'let', 'var', 'flow_value', 'flow_instance', 'flow_state'):
            candidates = [token for token in candidates if token['start'] > node['start']]
        if node['kind'] == 'type':
            boundary = min((self.node(module, child)['start'] for child in node['children']), default=node['end'])
            candidates = [token for token in candidates if token['end'] <= boundary]
            if '.' in node['text']:
                candidates = candidates[-1:]
        if node['kind'] == 'variant_pattern':
            payloads = [self.node(module, child) for child in node['children']
                        if self.node(module, child)['kind'] != 'pattern_qualifier']
            boundary = min((child['start'] for child in payloads), default=node['end'])
            candidates = [token for token in candidates if token['end'] <= boundary]
        if not candidates:
            return None
        token = candidates[-1] if node['kind'] in ('field', 'qualified_type', 'variant_pattern') else candidates[0]
        return self.span(module, token['start'], token['end'])

    def use(self, identity, span, declaration=False):
        if span is not None:
            key = (span['source'], span['start']['offset'], span['end']['offset'], identity)
            previous = self.occurrences.get(key)
            self.occurrences[key] = dict(symbol=identity, span=span,
                                        declaration=declaration or bool(previous and previous['declaration']))

    def at(self, source, offset):
        hits = [item for item in self.occurrences.values()
                if item['span']['source'] == source
                and item['span']['start']['offset'] <= offset < item['span']['end']['offset']]
        if not hits:
            hits = [item for item in self.occurrences.values()
                    if item['span']['source'] == source and item['span']['end']['offset'] == offset]
        return min(hits, key=lambda item: item['span']['end']['offset'] - item['span']['start']['offset']) if hits else None

    def definition(self, source, offset):
        hit = self.at(source, offset)
        return self.symbols[hit['symbol']]['span'] if hit else None

    def references(self, source, offset, include_declaration=True):
        hit = self.at(source, offset)
        if not hit:
            return []
        spans = [item['span'] for item in self.occurrences.values()
                 if item['symbol'] == hit['symbol'] and (include_declaration or not item['declaration'])]
        return sorted(spans, key=lambda span: (span['source'], span['start']['offset']))

    @staticmethod
    def span_key(span):
        return span['source'], span['start']['offset'], span['end']['offset']

    def declaration_anchor(self, identity):
        symbol = self.symbols[identity]
        return (self.modules[symbol['module']]['path'],
                self.node(symbol['module'], symbol['node'])['start'], symbol['kind'])

    def integrity_references(self):
        for item in self.occurrences.values():
            if item['declaration'] and item['symbol'] in self.implicit_aliases:
                continue
            yield item['span'], item['symbol'], self.declaration_anchor(item['symbol'])
        # Builtins have no source declaration, but their bindings must also
        # survive rename (including captures of otherwise untouched names).
        for reference in self.bound['references']:
            symbol = self.bound['symbols'][reference['symbol']]
            if symbol['module'] < 0:
                span = self.name_span(reference['module'], reference['node'], symbol['name'])
                if span is not None:
                    yield span, None, ('<builtin>', symbol['name'], symbol['kind'])

    def rename(self, source, offset, new_name, *, analyze):
        """Return edits only after native reanalysis proves binding stability."""
        if not isinstance(new_name, str) or not new_name or len(new_name) > 128:
            raise PixelError('editor', 'Rename requires an identifier of at most 128 characters')
        if self.diagnostics:
            raise PixelError('editor', 'Resolve project diagnostics before renaming')
        hit = self.at(source, offset)
        if hit is None:
            raise PixelError('editor', 'No symbol at this position')
        key = hit['symbol']
        edits = []
        for item in self.occurrences.values():
            if item['symbol'] != key:
                continue
            span = item['span']
            if span['source'] not in self.editable_files:
                raise PixelError('editor', 'Cannot rename a bundled library symbol', span)
            if item['declaration'] and key in self.implicit_aliases:
                end = self.implicit_aliases[key]
                edits.append(dict(span=self.span(self.paths[span['source']], end, end), text=' as ' + new_name))
                continue
            pair = self.shorthands.get(self.span_key(span))
            if pair:
                field, variable = pair
                replacement = ((new_name if key == field else self.symbols[field]['name']) + ': '
                               + (new_name if key == variable else self.symbols[variable]['name']))
            else:
                replacement = new_name
            edits.append(dict(span=span, text=replacement))
        edits.sort(key=lambda edit: self.span_key(edit['span']))
        changed = dict(self.files)
        for edit in reversed(edits):
            path, start, end = self.span_key(edit['span'])
            changed[path] = changed[path][:start] + edit['text'] + changed[path][end:]
        after = analyze(changed)
        if after.diagnostics:
            raise PixelError('editor', 'Rename would produce an invalid program', hit['span'])

        def translated(path, position):
            return position + sum(len(edit['text']) - (end - start)
                for edit in edits for name, start, end in [self.span_key(edit['span'])]
                if name == path and end <= position and start < position)

        # Syntax-node IDs change after expanding a shorthand. Compare native
        # declaration source anchors, translated through the exact edits.
        resolved = {}
        for span, identity, anchor in after.integrity_references():
            resolved.setdefault((span['source'], span['start']['offset']), set()).add(anchor)
        for span, identity, anchor in self.integrity_references():
            path, start, _ = self.span_key(span)
            position = translated(path, start)
            pair = self.shorthands.get(self.span_key(span))
            if pair and key in pair and identity == pair[1]:
                position += len(new_name if key == pair[0] else self.symbols[pair[0]]['name']) + 2
            expected = anchor if anchor[0] == '<builtin>' else (
                anchor[0], translated(anchor[0], anchor[1]), anchor[2])
            if expected not in resolved.get((path, position), set()):
                raise PixelError('editor', "Rename would change a reference's binding", span)
        # Let the native lexer, including its Unicode rules and keywords,
        # decide whether the replacement is exactly one identifier.
        anchor = self.declaration_anchor(key)
        renamed = next((s for identity, s in after.symbols.items()
                        if after.declaration_anchor(identity) == (
                            anchor[0], translated(anchor[0], anchor[1]), anchor[2])), None)
        if renamed is None or renamed['name'] != new_name:
            raise PixelError('editor', 'Rename requires a non-reserved identifier')
        name_span = renamed['span']
        valid = any(token['kind'] == 'name' and token['text'] == new_name
                    and token['start'] == name_span['start']['offset']
                    and token['end'] == name_span['end']['offset']
                    for token in after.modules[renamed['module']]['syntax']['tokens'])
        if not valid:
            raise PixelError('editor', 'Rename requires a non-reserved identifier')
        return edits

    def type_info(self, identity):
        return self.types[identity] if 0 <= identity < len(self.types) else None

    def member_info(self, identity):
        """Use instantiated member types supplied by the native compiler."""
        view = self.member_views.get(str(identity))
        if view is None:
            return None
        typ = self.type_info(view['storage'])
        return dict(typ, fields=view['fields'], variants=view['variants'], methods=view['methods'])

    def display_type(self, identity, seen=()):
        typ = self.type_info(identity)
        if typ is None:
            return '?'
        if identity in seen:
            return typ['name'] or '?'
        args = [self.display_type(arg, (*seen, identity)) for arg in typ['arguments']]
        kind = typ['kind']
        if kind == 'array':
            return '[' + args[0] + ']'
        if kind == 'map':
            return 'map[' + ', '.join(args) + ']'
        if kind == 'inout':
            return 'inout ' + args[0]
        if kind == 'function':
            prefix = 'effect fn' if typ['name'] == 'effect' else 'fn'
            return prefix + '(' + ', '.join(args[:-1]) + ') -> ' + args[-1]
        return typ['name'] + ('[' + ', '.join(args) + ']' if args else '')

    def label(self, identity):
        symbol = self.symbols[identity]
        kind, name = symbol['kind'], symbol['name']
        if kind in ('fn', 'flow'):
            node = self.node(symbol['module'], symbol['node'])
            children = [self.node(symbol['module'], child) for child in node['children']]
            parameters = []
            callable_type = self.type_info(symbol['typeId'])
            initial = []
            generic = []
            for child_id, child in zip(node['children'], children):
                if child['kind'] in ('parameter', 'initial_parameter'):
                    parameter = next((value for value in self.bound['symbols']
                                      if value['module'] == symbol['module'] and value['node'] == child_id), None)
                    destination = initial if child['kind'] == 'initial_parameter' else parameters
                    type_id = parameter['typeId'] if parameter else -1
                    if kind == 'fn' and callable_type and len(parameters) < len(callable_type['arguments']) - 1:
                        type_id = callable_type['arguments'][len(parameters)]
                    destination.append(child['text'] + ': ' + self.display_type(type_id))
                if child['kind'] == 'type_parameter':
                    generic.append(child['text'])
            label = kind + ' ' + name + ('[' + ', '.join(generic) + ']' if generic else '')
            if kind == 'flow':
                label += '(' + ', '.join(initial) + ') step'
            label += '(' + ', '.join(parameters) + ')'
            typ = self.type_info(symbol['typeId'])
            if kind == 'fn' and typ and typ['name'] == 'effect':
                label = 'effect ' + label
            result = self.display_type(typ['arguments'][-1]) if typ and typ['arguments'] else '?'
            return label + (' -> ' + result if result != 'unit' else '')
        if kind in ('frame', 'record', 'enum', 'module', 'type_parameter'):
            return kind + ' ' + name
        if kind == 'variant':
            typ = self.type_info(symbol['typeId'])
            variant = next((item for item in typ['variants'] if item['name'] == name), None)
            parameters = [field['name'] + ': ' + self.display_type(field['typeId'])
                          for field in variant['fields']] if variant else []
            return typ['name'] + '.' + name + ('(' + ', '.join(parameters) + ')' if parameters else '')
        return name + ': ' + self.display_type(symbol['typeId'])

    def method_signature(self, method):
        parameters = [field['name'] + ': ' + self.display_type(field['typeId'])
                      for field in method['parameters']]
        generic = '[' + ', '.join(method['generics']) + ']' if method['generics'] else ''
        result = self.display_type(method['result'])
        label = ('effect fn ' if method['effect'] else 'fn ') + method['name'] + generic + '(' + ', '.join(parameters) + ')'
        if result != 'unit':
            label += ' -> ' + result
        return dict(label=label, parameters=parameters)

    def method_at(self, source, offset):
        mid = self.paths.get(source)
        if mid is None:
            return None
        for node_id, node in enumerate(self.modules[mid]['syntax']['nodes']):
            if node['kind'] != 'field' or not node['start'] <= offset < node['end']:
                continue
            span = self.name_span(mid, node_id, node['text'])
            if not span or not span['start']['offset'] <= offset < span['end']['offset']:
                continue
            typ = self.member_info(self.expression_type(mid, node['children'][0]))
            if typ:
                method = next((item for item in typ['methods'] if item['name'] == node['text']), None)
                if method:
                    return dict(**self.method_signature(method), span=span)
        return None

    def hover(self, source, offset):
        method = self.method_at(source, offset)
        if method:
            return dict(label=method['label'], span=method['span'])
        hit = self.at(source, offset)
        mid = self.paths.get(source)
        if mid is not None:
            for node_id, node in enumerate(self.modules[mid]['syntax']['nodes']):
                if node['kind'] != 'field' or not node['start'] <= offset <= node['end']:
                    continue
                span = self.name_span(mid, node_id, node['text'])
                typ = self.expressions.get((mid, node_id), -1)
                if (span and span['start']['offset'] <= offset < span['end']['offset'] and typ >= 0
                    and (hit is None or self.symbols[hit['symbol']]['kind'] == 'field')):
                    return dict(label=node['text'] + ': ' + self.display_type(typ), span=span)
        return dict(label=self.label(hit['symbol']), span=hit['span']) if hit else None

    def signature_help(self, source, offset):
        if not 0 <= offset <= len(self.files[source]):
            raise PixelError('editor', 'Signature offset is outside source')
        mid = self.paths.get(source)
        if mid is None:
            return None
        tokens = [token for token in self.modules[mid]['syntax']['tokens']
                  if token['end'] <= offset and token['kind'] not in ('separator', 'eof')]
        stack = []
        pairs = {')': '(', ']': '[', '}': '{'}
        for index, token in enumerate(tokens):
            if token['kind'] == 'str':
                continue
            value = token['value']
            if value in ('(', '[', '{'):
                stack.append([value, index, 0])
            elif value in pairs:
                if stack and stack[-1][0] == pairs[value]:
                    stack.pop()
            elif value == ',' and stack:
                stack[-1][2] += 1
        for delimiter, opening, active in reversed(stack):
            if delimiter != '(' or opening == 0:
                continue
            callee = opening - 1
            if tokens[callee]['value'] == ']':
                depth = 1
                callee -= 1
                while callee >= 0 and depth:
                    if tokens[callee]['value'] == ']':
                        depth += 1
                    elif tokens[callee]['value'] == '[':
                        depth -= 1
                    callee -= 1
                if callee >= 0 and tokens[callee]['value'] == '::':
                    callee -= 1
            if callee < 0 or tokens[callee]['kind'] != 'name':
                continue
            method = self.method_at(source, tokens[callee]['start'])
            if method:
                return dict(label=method['label'], parameters=method['parameters'],
                            activeParameter=min(active, max(0, len(method['parameters']) - 1)))
            hit = self.at(source, tokens[callee]['start'])
            if hit is None:
                continue
            symbol = self.symbols[hit['symbol']]
            typ = self.type_info(symbol['typeId'])
            if typ is None or typ['kind'] != 'function':
                continue
            node = self.node(symbol['module'], symbol['node'])
            parameters = []
            if symbol['kind'] in ('fn', 'flow'):
                parameter_kind = 'initial_parameter' if symbol['kind'] == 'flow' else 'parameter'
                for child in node['children']:
                    parameter = self.node(symbol['module'], child)
                    if parameter['kind'] == parameter_kind:
                        if symbol['kind'] == 'flow':
                            binding = next((value for value in self.bound['symbols']
                                            if value['module'] == symbol['module'] and value['node'] == child), None)
                            type_id = binding['typeId'] if binding else -1
                        else:
                            type_id = typ['arguments'][len(parameters)]
                        parameters.append(parameter['text'] + ': ' + self.display_type(type_id))
                label = self.label(hit['symbol'])
                if symbol['kind'] == 'flow':
                    label = symbol['name'] + '(' + ', '.join(parameters) + ') -> Flow[' + self.display_type(symbol['typeId']) + ']'
            else:
                parameters = ['arg' + str(i + 1) + ': ' + self.display_type(arg)
                              for i, arg in enumerate(typ['arguments'][:-1])]
                result = self.display_type(typ['arguments'][-1])
                label = ('effect fn ' if typ['name'] == 'effect' else 'fn ') + symbol['name'] + '(' + ', '.join(parameters) + ')'
                if result != 'unit':
                    label += ' -> ' + result
            return dict(label=label, parameters=parameters,
                        activeParameter=min(active, max(0, len(parameters) - 1)))
        return None

    def document_symbols(self, source):
        mid = self.paths.get(source)
        if mid is None:
            return []
        result = []
        declarations = {item['node'] for item in self.modules[mid]['declarations']}
        kinds = dict(fn='Function', flow='Function', record='Struct', enum='Enum', frame='Namespace')
        for symbol in self.symbols.values():
            if symbol['module'] != mid or symbol['node'] not in declarations:
                continue
            node = self.node(mid, symbol['node'])
            result.append(dict(name=symbol['name'], kind=kinds.get(symbol['kind'], 'Variable'),
                               span=self.span(mid, node['start'], node['end']), selection=symbol['span']))
        return result

    def complete(self, source, offset, *, analyze):
        text = self.files[source]
        if not 0 <= offset <= len(text):
            raise PixelError('editor', 'Completion offset is outside source')
        mid = self.paths.get(source)
        if mid is None:
            return []
        tokens = self.modules[mid]['syntax']['tokens']
        start = end = offset
        for token in tokens:
            if token['kind'] in ('name', 'keyword') and token['start'] <= offset <= token['end']:
                start, end = token['start'], token['end']
                break
            if token['kind'] == 'str' and token['start'] < offset < token['end']:
                return []
        prefix = text[start:offset]
        sentinel = '__pixel_editor_cursor__'
        while sentinel in text:
            sentinel += '_'
        patched = text[:start] + sentinel + text[end:]
        # Native tokens define delimiter boundaries; comments and literal
        # contents never participate in balancing an unfinished document.
        stack = []
        pairs = {'(': ')', '[': ']', '{': '}'}
        for token in tokens:
            if token['kind'] == 'str':
                continue
            value = token['value']
            if value in pairs:
                stack.append(pairs[value])
            elif value in (')', ']', '}') and stack and stack[-1] == value:
                stack.pop()
        patched += '\n' + ''.join(reversed(stack))
        probe = analyze({**self.files, source: patched})
        pmid = probe.paths.get(source)
        if pmid is None:
            return []
        syntax = probe.modules[pmid]['syntax']
        if not any(token['kind'] == 'name' and token['text'] == sentinel
                   and token['start'] == start for token in syntax['tokens']):
            return []  # Cursor lies in a native comment or invalid token.
        nodes = [(i, node) for i, node in enumerate(syntax['nodes'])
                 if node['start'] <= start < node['end'] and sentinel in node['text']]
        if not nodes:
            return []
        node_id, cursor = min(nodes, key=lambda pair: pair[1]['end'] - pair[1]['start'])
        type_context = cursor['kind'] == 'type'
        candidates = []
        pattern_type = None
        for match in syntax['nodes']:
            if match['kind'] not in ('match', 'match_stmt'):
                continue
            subjects = probe.node(pmid, match['children'][0])['children']
            for arm_id in match['children'][1:]:
                patterns = probe.node(pmid, probe.node(pmid, arm_id)['children'][0])['children']
                for position, pattern_id in enumerate(patterns):
                    pattern = probe.node(pmid, pattern_id)
                    if not pattern['start'] <= start < pattern['end']:
                        continue
                    # Recursive patterns carry checked subject types; prefer the
                    # narrowest one containing the cursor. A damaged top-level
                    # pattern can still use its corresponding match subject.
                    typed = []
                    for nested_id, nested in enumerate(syntax['nodes']):
                        if nested['kind'] not in ('variant_pattern', 'binding', 'wildcard_pattern'):
                            continue
                        if pattern['start'] <= nested['start'] <= start < nested['end'] <= pattern['end']:
                            typ = probe.member_info(probe.expression_type(pmid, nested_id))
                            if typ and typ['kind'] == 'enum':
                                typed.append((nested['end'] - nested['start'], typ))
                    if typed:
                        pattern_type = min(typed, key=lambda item: item[0])[1]
                    elif position < len(subjects):
                        pattern_type = probe.member_info(probe.expression_type(pmid, subjects[position]))
        if pattern_type:
            # A bare pattern name binds a value. Only a selector introduces an
            # enum variant, so completion must not insert invalid bare variants.
            if text[:start].rstrip().endswith('.'):
                candidates = probe.variant_completions(pattern_type)
        elif cursor['kind'] == 'context_variant':
            typ = probe.member_info(probe.expression_type(pmid, node_id))
            if typ and typ['kind'] == 'enum':
                candidates = probe.variant_completions(typ)
        elif cursor['kind'] == 'field':
            receiver = cursor['children'][0]
            reference = next((ref for ref in probe.bound['references']
                              if ref['module'] == pmid and ref['node'] == receiver), None)
            symbol = probe.bound['symbols'][reference['symbol']] if reference else None
            if symbol and symbol['kind'] == 'module':
                candidates = probe.module_completions(pmid, symbol['node'], False)
            else:
                typ = probe.member_info(probe.expression_type(pmid, receiver))
                if typ:
                    candidates = [dict(label=field['name'], kind='Field', detail=probe.display_type(field['typeId']))
                                  for field in typ['fields']]
                    candidates.extend(dict(label=method['name'], kind='Method',
                                           detail=probe.method_signature(method)['label'])
                                      for method in typ['methods'])
                    if typ['kind'] == 'enum':
                        candidates = probe.variant_completions(typ)
        elif type_context and '.' in cursor['text']:
            alias = cursor['text'].split('.')[0]
            imported = next((item for item in probe.modules[pmid]['imports'] if item['alias'] == alias), None)
            if imported:
                candidates = probe.module_completions(pmid, imported['node'], True)
        else:
            scopes = probe.bound['scopes']
            visible = []
            for identity, scope in enumerate(scopes):
                if scope['module'] != pmid:
                    continue
                if scope['node'] >= 0:
                    region = probe.node(pmid, scope['node'])
                    if not region['start'] <= start <= region['end']:
                        continue
                    if region['kind'] == 'flow':
                        creation = any(
                            probe.node(pmid, member)['kind'] in ('flow_state', 'flow_instance')
                            and probe.node(pmid, probe.node(pmid, member)['children'][-1])['start'] <= start
                            <= probe.node(pmid, probe.node(pmid, member)['children'][-1])['end']
                            for body in region['children'] if probe.node(pmid, body)['kind'] == 'flow_body'
                            for member in probe.node(pmid, body)['children'])
                        siblings = [(sid, candidate) for sid, candidate in enumerate(scopes)
                                    if candidate['module'] == pmid and candidate['node'] == scope['node']
                                    and candidate['parent'] == scope['parent']]
                        step_scopes = {sid for sid, candidate in siblings if any(
                            probe.bound['symbols'][symbol]['kind'] in
                            ('parameter', 'flow_state', 'flow_value', 'flow_instance')
                            for symbol in candidate['symbols'].values())}
                        if creation and identity in step_scopes:
                            continue
                        # Creation parameters are readonly configuration in step
                        # scopes too; their presence does not identify a creation scope.
                        if not creation and step_scopes and identity not in step_scopes:
                            continue
                depth, parent = 0, scope['parent']
                while parent >= 0:
                    depth += 1
                    parent = scopes[parent]['parent']
                visible.append((depth, identity))
            current = max(visible)[1] if visible else -1
            seen = set()
            while current >= 0:
                scope = scopes[current]
                for name, identity in scope['symbols'].items():
                    symbol = probe.bound['symbols'][identity]
                    if name in seen:
                        continue
                    if symbol['module'] >= 0 and scope['owner'] >= 0 and symbol['kind'] in ('let', 'var', 'fn'):
                        declaration = probe.node(symbol['module'], symbol['node'])
                        boundary = declaration['start'] if symbol['kind'] == 'fn' else declaration['end']
                        if boundary > start:
                            continue
                    if type_context and symbol['kind'] not in ('record', 'enum', 'frame', 'type_parameter', 'module'):
                        continue
                    seen.add(name)
                    candidates.append(probe.completion_item(identity))
                current = scope['parent']
            if type_context:
                candidates.extend(dict(label=name, kind='Keyword', detail='type')
                                  for name in probe.analysis['typeNames'] if name not in seen)
                for typ in probe.types:
                    if typ['kind'] != 'type_parameter' or typ['module'] != pmid:
                        continue
                    owner = probe.node(pmid, typ['declaration'])
                    if owner['start'] <= start <= owner['end'] and typ['name'] not in seen:
                        candidates.append(dict(label=typ['name'], kind='TypeParameter', detail='type parameter'))
                        seen.add(typ['name'])
        return sorted((item for item in candidates if item['label'].startswith(prefix)), key=lambda item: item['label'])

    def variant_completions(self, typ):
        return [dict(label=variant['name'], kind='EnumMember',
            detail=variant['name'] + ('(' + ', '.join(
                field['name'] + ': ' + self.display_type(field['typeId']) for field in variant['fields']) + ')'
                if variant['fields'] else '')) for variant in typ['variants']]

    def completion_item(self, identity):
        symbol = self.bound['symbols'][identity]
        kind = symbol['kind']
        kinds = dict(fn='Function', flow='Function', record='Struct', enum='Enum',
                     frame='Namespace', module='Module', type_parameter='TypeParameter', builtin='Function')
        if kind == 'builtin':
            detail = 'PixelLang builtin'
        elif identity in self.symbols and kind in ('fn', 'flow', 'record', 'enum', 'frame'):
            detail = self.label(identity)
        elif kind in ('module', 'type_parameter'):
            detail = 'module' if kind == 'module' else 'type parameter'
        else:
            detail = self.display_type(symbol['typeId'])
        return dict(label=symbol['name'], kind=kinds.get(kind, 'Variable'), detail=detail)

    def module_completions(self, mid, import_node, types_only):
        imported = next(item for item in self.modules[mid]['imports'] if item['node'] == import_node)
        target = self.paths.get(imported['target'])
        return [self.completion_item(identity) for identity, symbol in enumerate(self.bound['symbols'])
                if symbol['module'] == target and symbol['exported']
                and (not types_only or symbol['kind'] in ('record', 'enum', 'frame'))]
