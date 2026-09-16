"""Development PNG compiler: isolated module workers and opt-in parsed-module cache.

All image interpretation, parsing and compilation execute in PixelLang. Python
only schedules VMs, hashes/serializes transport objects and manages cache files.
The ordinary bootstrap_image acceptance path never uses this cache.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

from .bootstrap import normalized, read_bytecode_stream
from .bootstrap_image import HOST_STAGES, PROFILE
from .fileaccess import FileAccess
from .regression import fingerprint
from .vm import VM

ROOT = Path(__file__).resolve().parents[1]
DRIVER = 'import "image_worker.pxl" as worker\nfn main(){worker.Run(input())}'


class Execution:
    """Reuse validated code, restoring an empty trusted VM checkpoint per job."""
    def __init__(self, code):
        self.vm = VM(code, **PROFILE)
        self.initial = self.vm.checkpoint()

    def reset(self, request, root):
        text = json.dumps(request, ensure_ascii=False)
        if len(text)>1000000:
            raise ValueError('Request exceeds bounded VM input; use chunk files')
        self.vm.restore_checkpoint(self.initial)
        self.vm.input_text = text
        self.vm.file_access = FileAccess(read_root=root,budget=64000000)
        return self.vm


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def execute(code, request, root, deadline):
    if time.monotonic() > deadline:
        raise TimeoutError("Image workspace deadline exceeded")
    vm = code.reset(request,root) if isinstance(code,Execution) else VM(
        code, **PROFILE, input_text=json.dumps(request, ensure_ascii=False),
        file_access=FileAccess(read_root=root, budget=64000000))
    start = time.monotonic()
    with ExitStack() as stack:
        for target in (*HOST_STAGES, 'pixellang.project.compile_project'):
            stack.enter_context(patch(target, side_effect=AssertionError('Host frontend/codec used')))
        while not vm.halted:
            vm.step(snapshot=False)
            if vm.steps % 100000 == 0 and time.monotonic() > deadline:
                raise TimeoutError('Image workspace VM deadline exceeded')
    if time.monotonic() > deadline:
        raise TimeoutError("Image workspace deadline exceeded")
    return vm.output, dict(seconds=time.monotonic()-start, steps=vm.steps)


def cached_parse(cache, key):
    if cache is None:
        return None
    try:
        path = cache / (key + '.json')
        if path.stat().st_size > 64000000:
            return None
        item = json.loads(path.read_text())
        if item['key'] != key or item['sha256'] != digest(item['parsed']):
            return None
        if item['parsed']['diagnostics']:
            return None
        if not isinstance(item['parsed']['entries'],list) or not all(
            isinstance(item['parsed']['syntax'][field],list)
            for field in ('tokens','nodes','roots','diagnostics')):
            return None
        return item['parsed']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_cache(cache, key, parsed):
    if cache is None or parsed['diagnostics']:
        return
    import os
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / (key + '.json')
    temporary = cache / (key + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(dict(key=key, sha256=digest(parsed), parsed=parsed)))
    temporary.replace(destination)


def write_chunks(root, stem, values):
    """Bound each typed JSON array independently of total module size."""
    paths, chunk, size = [], [], 2
    def flush():
        name = f'{stem}-{len(paths)}.json'
        (root/name).write_text('['+','.join(chunk)+']')
        paths.append(name)
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
    return paths


def stage_parse(root, key, source, decoded):
    if decoded['diagnostics']:
        raise ValueError('Cannot parse an invalid decoded module')
    name=f'parse-{key}.json'
    (root/name).write_text(json.dumps(dict(links=source['spatial']['links'],
        tokenFiles=write_chunks(root,f'decoded-{key}',decoded['tokens'])), ensure_ascii=False))
    return name


def stage_parsed(root, key, parsed):
    name=f'parsed-{key}.json'
    (root/name).write_text(json.dumps(dict(roots=parsed['syntax']['roots'], entries=parsed['entries'],
        diagnostics=parsed['diagnostics'], syntaxDiagnostics=parsed['syntax']['diagnostics'],
        tokenFiles=write_chunks(root,f'tokens-{key}',parsed['syntax']['tokens']),
        nodeFiles=write_chunks(root,f'nodes-{key}',parsed['syntax']['nodes'])), ensure_ascii=False))
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


def worker(job_path):
    job = json.loads(job_path.read_text())
    root = job_path.parent
    code = Execution(json.loads((root/'worker-compiler.json').read_text()))
    cache = Path(job['cache']) if job['cache'] else None
    output, decode_stats = execute(code, dict(operation='decode', file='program.png', selected=job['ids']), root, job['deadline'])
    pieces = read_modules(output)
    if set(pieces['sources']) != set(job['ids']) or set(pieces['decoded']) != set(job['ids']):
        raise ValueError('Worker module coverage mismatch')
    results, records = {}, []
    for key in job['ids']:
        source = pieces['sources'][key]
        cache_key = digest(dict(version=1, environment=job['environment'], moduleId=key, source=source))
        parsed = cached_parse(cache, cache_key)
        hit = parsed is not None
        stats = dict(seconds=0, steps=0)
        if not hit:
            filename=stage_parse(root,key,source,pieces['decoded'][key])
            output, stats = execute(code, dict(operation='parse', file=filename,
                moduleId=int(key)), root, job['deadline'])
            parsed = read_parsed(output)
            save_cache(cache, cache_key, parsed)
        results[key] = parsed
        records.append(dict(module=key, cacheKey=cache_key, cacheHit=hit, **stats))
    (root/job['result']).write_text(json.dumps(dict(parsed=results, decode=decode_stats, modules=records)))


def compile_image(image, output, *, workers=1, cache=None, timeout=300, compiler=None):
    if type(workers) is not int or not 1 <= workers <= 16 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Workers must be 1..16 and timeout finite and positive')
    image, output = Path(image).resolve(), Path(output).resolve()
    if image.stat().st_size > 64000000:
        raise ValueError('Image exceeds byte limit')
    cache = Path(cache).resolve() if cache else None
    before = fingerprint(ROOT)
    original_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(image, output/'program.png')
    sources = {p.name:p.read_text() for p in (ROOT/'selfhost').glob('*.pxl')}
    start = time.monotonic()
    if compiler:
        compiler = Path(compiler).resolve()
        compiler_input_hash = hashlib.sha256(compiler.read_bytes()).hexdigest()
        code = json.loads(compiler.read_text())
    else:
        from .project import compile_project
        code = compile_project(dict(sources, **{'main.pxl': DRIVER}))[1].bytecode
    artifact = normalized(code)
    (output/'worker-compiler.json').write_text(artifact)
    environment = dict(compilerSha256=hashlib.sha256(artifact.encode()).hexdigest(), profile=PROFILE,
                       runtime={k:v for k,v in before.items() if k.startswith('pixellang/') or k=='pyproject.toml'})
    manifest = dict(files=before, imageSha256=original_hash, workers=workers, cache=str(cache) if cache else None,
                    timeout=timeout, environment=environment,
                    compilerInputSha256=compiler_input_hash if compiler else None)
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2))
    deadline = time.monotonic()+timeout
    summary = dict(verified=False, cacheEnabled=cache is not None, stage0Seconds=time.monotonic()-start)
    processes, logs = [], []
    try:
        execution = Execution(code)
        values, summary['index'] = execute(execution, dict(operation='index', file='program.png'), output, deadline)
        ids = json.loads(values[0])
        if not ids or len(ids)!=len(set(ids)) or '0' not in ids:
            raise ValueError('Invalid module index')
        groups = [ids[i::workers] for i in range(min(workers,len(ids)))]
        print(json.dumps(dict(stage='modules', workers=len(groups), modules=len(ids))), flush=True)
        for i, group in enumerate(groups):
            job = output/f'job-{i}.json'
            job.write_text(json.dumps(dict(ids=group, cache=str(cache) if cache else None,
                deadline=deadline, environment=environment, result=f'result-{i}.json')))
            log = (output/f'worker-{i}.log').open('w'); logs.append(log)
            processes.append(subprocess.Popen([sys.executable,'-m','pixellang.image_workspace','--job',str(job)],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
        while any(p.poll() is None for p in processes):
            if any(p.poll() not in (None,0) for p in processes):
                raise RuntimeError('Module worker failed; see worker logs')
            if time.monotonic()>deadline:
                raise TimeoutError('Module workers exceeded deadline')
            time.sleep(.05)
        if any(p.returncode!=0 for p in processes):
            raise RuntimeError('Module worker failed; see worker logs')
        parsed, records = {}, []
        for i, group in enumerate(groups):
            result = json.loads((output/f'result-{i}.json').read_text())
            if set(result['parsed']) != set(group) or set(parsed)&set(group):
                raise ValueError('Parsed module coverage mismatch')
            parsed.update(result['parsed']); records.append(result)
        if set(parsed)!=set(ids):
            raise ValueError('Incomplete module set')
        summary['workers'] = [dict(decode=r['decode'], modules=r['modules']) for r in records]
        summary['cacheHits'] = sum(m['cacheHit'] for r in records for m in r['modules'])
        summary['modules'] = len(ids)
        print(json.dumps(dict(stage='assemble', cacheHits=summary['cacheHits'])), flush=True)
        parsed_files={key:stage_parsed(output,key,value) for key,value in parsed.items()}
        events, summary['assemble'] = execute(execution, dict(operation='assemble', parsedFiles=parsed_files), output, deadline)
        built = read_bytecode_stream(events)
        if built['diagnostics']:
            raise ValueError(json.dumps(built['diagnostics'], ensure_ascii=False))
        final = normalized(built['bytecode'])
        (output/'compiler.json').write_text(final)
        summary['artifactSha256'] = hashlib.sha256(final.encode()).hexdigest()
        summary['verified'] = True
    except BaseException as error:
        summary['error'] = repr(error)
        raise
    finally:
        for process in processes:
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        for process in processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
        for log in logs:
            log.close()
        try:
            summary['inputsUnchanged'] = (before==fingerprint(ROOT)
                and original_hash==hashlib.sha256(image.read_bytes()).hexdigest()
                and (not compiler or compiler_input_hash==hashlib.sha256(compiler.read_bytes()).hexdigest()))
        except OSError:
            summary['inputsUnchanged'] = False
        summary['verified'] = summary['verified'] and summary['inputsUnchanged']
        summary['seconds'] = time.monotonic()-start
        (output/'summary.json').write_text(json.dumps(summary, indent=2))
    if not summary['verified']:
        raise ValueError('Image workspace inputs changed')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--cache',type=Path)
    parser.add_argument('--timeout',type=float,default=300)
    parser.add_argument('--compiler',type=Path,help='Existing PixelLang image worker bytecode')
    parser.add_argument('--job',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.job:
        worker(args.job); return
    if not args.image or not args.output:
        parser.error('--image and --output required')
    print(json.dumps(compile_image(args.image,args.output,workers=args.workers,cache=args.cache,
        timeout=args.timeout,compiler=args.compiler)),flush=True)


if __name__=='__main__':
    main()
