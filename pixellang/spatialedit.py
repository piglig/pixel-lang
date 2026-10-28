"""Source edits projected from the native compiler's AST and pixel source maps.

The host chooses text ranges; it never parses, binds or type-checks PixelLang.
"""
import hashlib
import json
from .compiler_client import CompilerClient
from .model import PixelError


def revision(files, entry):
    return hashlib.sha256(json.dumps([entry, files], sort_keys=True).encode()).hexdigest()


class SpatialProject:
    def __init__(self, files, entry='main.pxl', *, compiler=None, cancelled=lambda: False, submitted_at=None):
        self.files, self.entry = dict(files), entry
        self.revision = revision(self.files, entry)
        self.compiler = compiler
        self.cancelled, self.submitted_at = cancelled, submitted_at
        self.nodes, self.siblings, self.syntax = {}, {}, {}
        if compiler is None:
            with CompilerClient() as client:
                self._load(client)
        else:
            self._load(compiler)

    def _check_cancelled(self):
        if self.cancelled():
            raise PixelError('service', 'Editor request cancelled', code='service.cancelled')

    def _request(self, compiler, operation, files):
        self._check_cancelled()
        result = compiler.request(operation, files=files, entry=self.entry,
                                  cancelled=self.cancelled, submitted_at=self.submitted_at)
        self._check_cancelled()
        return result

    def _load(self, compiler):
        self._request(compiler, 'check', self.files)
        analysis = self._request(compiler, 'editor-index', self.files)['result']['index']
        source = self._request(compiler, 'debug-pixels', self.files)['result']['document']
        self.documents = {doc['metadata']['filename']: doc for doc in
                          [source, *source.get('bundle', {}).get('modules', {}).values()]
                          if doc['metadata']['filename'] in self.files}
        for module in analysis['resolved']['bound']['project']['modules']:
            path = module['path']
            if path not in self.documents:
                continue
            syntax = module['syntax']
            self.syntax[path] = syntax
            for index, node in enumerate(syntax['nodes']):
                key = self.key(path, index)
                self.nodes[key] = (path, index, node)
                if node['kind'] == 'block':
                    for child in node['children']:
                        self.siblings[self.key(path, child)] = node['children']

    @staticmethod
    def key(source, index):
        return f'{source}:{index}'

    def kind(self, source, node):
        if node['kind'].startswith('literal_'):
            return 'literal'
        nodes = self.syntax[source]['nodes']
        if node['kind'] == 'expression_stmt' and node['children']:
            call = nodes[node['children'][0]]
            if call['kind'] == 'call' and call['children']:
                target = nodes[call['children'][0]]
                if target['kind'] == 'name' and target['text'] == 'print':
                    return 'print'
        return node['kind']

    def span(self, source, start, end):
        text = self.files[source]
        def point(offset):
            prefix = text[:offset]
            return dict(offset=offset, line=prefix.count('\n') + 1,
                        column=len(prefix.rsplit('\n', 1)[-1]) + 1)
        return dict(source=source, kind='text', start=point(start), end=point(end))

    def describe(self, source):
        self._check_cancelled()
        if source not in self.documents:
            raise PixelError('studio', 'Source module is not part of this executable project')
        targets = {}
        for key, (path, index, node) in self.nodes.items():
            self._check_cancelled()
            if path != source or node['kind'] == 'block':
                continue
            kind = self.kind(path, node)
            actions = ['literal'] if kind == 'literal' else ['operator'] if kind == 'binary' else []
            siblings = self.siblings.get(key)
            if siblings:
                position = siblings.index(index)
                if position: actions.append('up')
                if position + 1 < len(siblings): actions.append('down')
            explanation = (f"Produces a constant {dict(str='string', keyword='bool').get(node['kind'][8:], node['kind'][8:])} value."
                           if kind == 'literal' else f"{kind.replace('_', ' ').capitalize()} in the selected program structure.")
            targets[key] = dict(id=key, kind=kind, explanation=explanation,
                                span=self.span(path, node['start'], node['end']),
                                text=self.files[path][node['start']:node['end']], actions=actions)
        # Rows are a view of compiler-emitted pixels, not a second pixel decoder.
        document = self.documents[source]
        mapping = document['metadata']['source_map']
        rows, mapped_targets = {}, {}
        for pixel in document['pixels']:
            self._check_cancelled()
            position = pixel['position']
            origin = mapping.get(','.join(map(str, position)))
            bounds = (origin['start']['offset'], origin['end']['offset']) if origin else None
            if bounds is not None and bounds not in mapped_targets:
                mapped_targets[bounds] = sorted((key for key, target in targets.items()
                    if bounds[0] <= target['span']['start']['offset']
                    and target['span']['end']['offset'] <= bounds[1]),
                    key=lambda key: targets[key]['span']['end']['offset'] - targets[key]['span']['start']['offset'])
            keys = mapped_targets.get(bounds, [])
            token = dict(position=position, color='#' + ''.join(f'{c:02x}' for c in pixel['rgba'][:3]),
                         kind='source region' if origin else 'pixel',
                         label=self.files[source][bounds[0]:bounds[1]] if origin else '', targets=keys)
            rows.setdefault(position[1], []).append(token)
        regions = [dict(tokens=sorted(tokens, key=lambda token: token['position'][0]))
                   for _, tokens in sorted(rows.items())]
        return dict(revision=self.revision, source=source, sources=sorted(self.documents),
                    regions=regions, targets=targets)

    def edit(self, args):
        self._check_cancelled()
        if args.get('revision') != self.revision:
            raise PixelError('studio', 'Source changed; refresh the spatial view before editing')
        key, action = args.get('target'), args.get('action')
        if key not in self.nodes:
            raise PixelError('studio', 'Unknown spatial target')
        source, index, node = self.nodes[key]
        if action not in self.describe(source)['targets'].get(key, {}).get('actions', []):
            raise PixelError('studio', 'This operation is not available for the selected region')
        text = self.files[source]
        start, end = node['start'], node['end']
        if action == 'literal':
            replacement = args.get('value')
            if not isinstance(replacement, str) or len(replacement) > 100_000:
                raise PixelError('studio', 'Enter a PixelLang literal')
            replacement = replacement.strip()
        elif action == 'operator':
            operator = args.get('value')
            if operator not in ('+', '-', '*', '/', '%', '==', '!=', '<', '<=', '>', '>=', '&&', '||'):
                raise PixelError('studio', 'Choose a supported binary operator')
            nodes = self.syntax[source]['nodes']
            left, right = [nodes[child] for child in node['children']]
            token = next(t for t in self.syntax[source]['tokens']
                         if left['end'] <= t['start'] < right['start'] and t['value'] == node['text'])
            gap = text[left['end']:token['start']] + operator + text[token['end']:right['start']]
            replacement = ('(' + text[start:left['start']] + '(' + text[left['start']:left['end']]
                           + ')' + gap + '(' + text[right['start']:right['end']] + ')'
                           + text[right['end']:end] + ')')
        else:
            siblings = self.siblings[key]
            other = self.syntax[source]['nodes'][siblings[siblings.index(index) + (-1 if action == 'up' else 1)]]
            first, second = sorted((node, other), key=lambda n: n['start'])
            start, end = first['start'], second['end']
            replacement = text[second['start']:second['end']] + text[first['end']:second['start']] + text[first['start']:first['end']]
        proposed = {**self.files, source: text[:start] + replacement + text[end:]}
        if self.compiler is None:
            with CompilerClient() as compiler:
                self._validate(compiler, proposed, source, start, replacement, action)
        else:
            self._validate(self.compiler, proposed, source, start, replacement, action)
        self._check_cancelled()
        return dict(revision=self.revision, source=source, start=start, end=end, text=replacement, files=proposed)

    def _validate(self, compiler, proposed, source, start, replacement, action):
        self._request(compiler, 'check', proposed)
        if action != 'literal':
            return
        analysis = self._request(compiler, 'editor-index', proposed)['result']['index']
        module = next(m for m in analysis['resolved']['bound']['project']['modules'] if m['path'] == source)
        nodes = module['syntax']['nodes']
        for node in nodes:
            if node['start'] != start or node['end'] != start + len(replacement):
                continue
            if node['kind'].startswith('literal_') or (node['kind'] == 'unary' and node['text'] == '-'
                    and len(node['children']) == 1 and nodes[node['children'][0]]['kind'] in ('literal_int', 'literal_float64')):
                return
        raise PixelError('studio', 'Replacement must be a single literal')
