#!/usr/bin/env python3
"""Verify native compiler generations and full PNG delivery without promotion."""
import argparse
from contextlib import ExitStack, closing
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from pixellang.bootstrap import normalized
from pixellang.compiler_service import CompilerService, Limits
from pixellang.native_codegen import build_native, entry_session
from pixellang.regression import fingerprint


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',type=Path,required=True)
    parser.add_argument('--native-compiler',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    before=fingerprint(ROOT)
    seed_inputs={str(p.resolve()):digest(p.read_bytes()) for p in (args.seed,args.native_compiler,
        args.native_compiler.with_name(args.native_compiler.name+'.map.json'))}
    runner=digest(Path(__file__).read_bytes())
    resources=ExitStack()
    files={p.name:p.read_text() for p in sorted((ROOT/'selfhost').glob('*.pxl'))}
    snapshot=json.dumps(files,sort_keys=True,ensure_ascii=False).encode()
    (args.output/'sources.json').write_bytes(snapshot)
    # Match the existing full-image gate's limits, not an expanded acceptance
    # budget. Native allocation ceilings remain enforced by its runtime.
    limits=Limits(seconds=1200,steps=3_000_000_000)
    code=json.loads(args.seed.read_text())
    executable=args.native_compiler
    report={'sourceSha256':digest(snapshot),'seedSha256':digest(normalized(code).encode()),
            'stageSecondsLimit':1200,'stageStepsLimit':3_000_000_000,
            'controlledBenchmark':False,'promoted':False,'stages':[],
            'inputs':seed_inputs,'runnerSha256':runner}
    (args.output/'manifest.json').write_text(json.dumps(before,indent=2)+'\n')
    def publish():
        (args.output/'status.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    def run(stage,service,operation,**parameters):
        report['activeStage']=stage
        publish()
        start=time.monotonic()
        response=service.request(dict(version=1,operation=operation,reuseArtifacts=False,**parameters))
        row=dict(stage=stage,seconds=time.monotonic()-start,status=response['status'],
                 metrics=response.get('metrics'),diagnostics=response.get('diagnostics'))
        row['nativeStatistics']=service.native.statistics
        if 'error' in response:row['error']=response['error']
        report['stages'].append(row)
        publish()
        print(json.dumps(row),flush=True)
        if response['status']!='ok':raise RuntimeError(f'{stage} failed; see status.json')
        return response['result'],row
    try:
        for generation in (2,3):
            service=resources.enter_context(closing(CompilerService(code,native_executable=executable,limits=limits)))
            result,row=run(f'generation-{generation}',service,'compile',files=files,
                           incremental=False)
            service.close()
            encoded=normalized(result['bytecode']).encode()
            (args.output/f'compiler-g{generation}.json').write_bytes(encoded)
            row.update(sha256=digest(encoded),equalPrevious=encoded==normalized(code).encode())
            publish()
            if not row['equalPrevious']:raise RuntimeError('Compiler generation is not a fixed point')
            code=result['bytecode']
            start=time.monotonic()
            executable=build_native(code,args.output/f'compiler-g{generation}',optimization=1,max_depth=1024,session=entry_session(code))
            row['nativeBuildSeconds']=time.monotonic()-start
            publish()
        service=resources.enter_context(closing(CompilerService(code,native_executable=executable,limits=limits)))
        result,row=run('compiler-png',service,'export-png',files=files,scale=4,incremental=False)
        image=result['image']
        (args.output/'compiler.png').write_bytes(image)
        row.update(bytes=len(image),sha256=digest(image))
        result,row=run('png-bytecode',service,'compile-png',image=image,incremental=False)
        service.close()
        recovered=result['bytecode']
        encoded=normalized(recovered).encode()
        (args.output/'recovered-compiler.json').write_bytes(encoded)
        row.update(sha256=digest(encoded),equalCompiler=encoded==normalized(code).encode())
        publish()
        # PNG source provenance can differ. Require the recovered compiler to
        # reproduce the canonical source compiler below, not identical spans.
        recovered_exe=build_native(recovered,args.output/'recovered-compiler',optimization=1,max_depth=1024,session=entry_session(recovered))
        recovered_service=resources.enter_context(closing(CompilerService(recovered,native_executable=recovered_exe,limits=limits)))
        result,row=run('recovered-selfcompile',recovered_service,'compile',files=files,
                       incremental=False)
        row['equalCompiler']=normalized(result['bytecode'])==normalized(code)
        if not row['equalCompiler']:raise RuntimeError('Recovered compiler does not reproduce itself')
        result,row=run('recovered-smoke',recovered_service,'compile',
                       files={'main.pxl':'effect fn main(){print(42)}'},incremental=False)
        recovered_service.close()
        smoke=build_native(result['bytecode'],args.output/'smoke')
        completed=subprocess.run([str(smoke)],capture_output=True,text=True,timeout=10)
        row.update(returncode=completed.returncode,stdout=completed.stdout,stderr=completed.stderr)
        if completed.returncode or completed.stdout!='42\n':raise RuntimeError('Recovered compiler smoke failed')
        current={p.name:p.read_text() for p in sorted((ROOT/'selfhost').glob('*.pxl'))}
        report['sourcesUnchanged']=current==files
        if not report['sourcesUnchanged']:raise RuntimeError('Compiler sources changed during acceptance')
        report['inputsUnchanged']=(fingerprint(ROOT)==before and digest(Path(__file__).read_bytes())==runner
            and all(digest(Path(name).read_bytes())==value for name,value in seed_inputs.items()))
        if not report['inputsUnchanged']:raise RuntimeError('Verification inputs changed during acceptance')
        report['status']='passed'
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        resources.close()
        publish()


if __name__=='__main__':
    main()
