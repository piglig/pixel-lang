"""Bounded parsed-image transport shared by the native service and component tests."""
import json


def encode_chunks(stem, values):
    """Bound each typed JSON array independently of total module size."""
    chunks, chunk, size = {}, [], 2
    def flush():
        name = f'{stem}-{len(chunks)}.json'
        chunks[name] = ('['+','.join(chunk)+']').encode('utf-8')
    for value in values:
        encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        length = len(encoded.encode())+1
        if length > 900000:
            raise ValueError('Transport item exceeds bounded JSON input')
        if chunk and (size+length > 256000 or len(chunk)>=1000):
            flush(); chunk, size = [], 2
        chunk.append(encoded); size += length
    if chunk:
        flush()
    return chunks


def encode_parsed(key, parsed):
    """Immutable, content-addressed transport; cache hits need no JSON round trip."""
    name=f'parsed-{key}.json'
    tokens=encode_chunks(f'tokens-{key}',parsed['syntax']['tokens'])
    nodes=encode_chunks(f'nodes-{key}',parsed['syntax']['nodes'])
    header=json.dumps(dict(roots=parsed['syntax']['roots'], entries=parsed['entries'],
        diagnostics=parsed['diagnostics'], syntaxDiagnostics=parsed['syntax']['diagnostics'],
        tokenFiles=list(tokens),nodeFiles=list(nodes)),ensure_ascii=False).encode('utf-8')
    return name, {**tokens,**nodes,name:header}


def stage_parsed(root, key, parsed):
    name, files=encode_parsed(key,parsed)
    for path,data in files.items():
        (root/path).write_bytes(data)
    return name


def read_modules(events):
    sources, decoded, current = {}, {}, None
    for raw in events:
        item=json.loads(raw); kind=item['kind']
        if kind=='module':
            if current is not None or item['id'] in sources:
                raise ValueError('Duplicate or incomplete module stream')
            current=item['id']; sources[current]=item['source']
            if sources[current]['pixels']:
                raise ValueError('Module header carries pixels')
            decoded[current]=dict(tokens=[],diagnostics=[])
        elif current is None:
            raise ValueError('Module data without header')
        elif kind=='pixels':
            sources[current]['pixels'].extend(item['items'])
        elif kind=='decoded':
            decoded[current]['tokens'].extend(item['items'])
        elif kind=='module-end':
            if item['first']!=len(sources[current]['pixels']) or item['second']!=len(decoded[current]['tokens']):
                raise ValueError('Module stream counts differ')
            current=None
        else:
            raise ValueError('Unknown module stream event')
    if current is not None:
        raise ValueError('Incomplete module stream')
    return dict(sources=sources,decoded=decoded)


def read_parsed(events):
    parsed, ended = None, False
    for raw in events:
        item=json.loads(raw);kind=item['kind']
        if ended:
            raise ValueError('Data after parsed stream end')
        if parsed is None:
            if kind!='parsed':
                raise ValueError('Missing parsed header')
            parsed=dict(syntax=dict(tokens=[],nodes=[],roots=item['roots'],diagnostics=item['syntaxDiagnostics']),
                        entries=item['entries'],diagnostics=item['diagnostics'])
        elif kind in ('tokens','nodes'):
            parsed['syntax'][kind].extend(item['items'])
        elif kind=='parsed-end':
            if item['first']!=len(parsed['syntax']['tokens']) or item['second']!=len(parsed['syntax']['nodes']):
                raise ValueError('Parsed stream counts differ')
            ended=True
        else:
            raise ValueError('Unknown parsed stream event')
    if not ended:
        raise ValueError('Incomplete parsed stream')
    return parsed
