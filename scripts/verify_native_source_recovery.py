"""Recover compiler text from PNG and verify self-compilation using native executables."""
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
from pixellang.vm import VM


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_smoke(program, executable):
    """Check the recovered compiler's output against both execution backends."""
    reference = VM(program).run()
    if reference != [42]:
        raise ValueError('Recovered compiler reference smoke failed')
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    if result.returncode or result.stdout != '42\n':
        raise ValueError('Recovered compiler native smoke failed')
    return reference


def verify_canonical_compiler(expected, rebuilt):
    if normalized(rebuilt) != normalized(expected):
        raise ValueError('Recovered compiler does not reproduce the canonical source compiler')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('compiler','native-compiler','image','sources','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    paths=dict(compiler=args.compiler,native=args.native_compiler,
               mapping=args.native_compiler.with_name(args.native_compiler.name+'.map.json'),
               image=args.image,sources=args.sources)
    inputs={name:digest(path.read_bytes()) for name,path in paths.items()}
    sources=json.loads(args.sources.read_text())
    current={p.name:p.read_text() for p in (ROOT/'selfhost').glob('*.pxl')}
    if sources!=current:raise ValueError('Compiler source snapshot is historical')
    code=json.loads(args.compiler.read_text())
    limits=Limits(seconds=1200,steps=3_000_000_000)
    args.output.mkdir(parents=True,exist_ok=False)
    before=fingerprint(ROOT)
    runner=digest(Path(__file__).read_bytes())
    report=dict(verified=False,backend='native',inputs=inputs,runnerSha256=runner,
                stageSecondsLimit=1200,stageStepsLimit=3_000_000_000,stages=[])
    (args.output/'manifest.json').write_text(json.dumps(dict(files=before,**report),indent=2))

    def publish():
        (args.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')

    def request(stage,compiler,operation,**options):
        report['activeStage']=stage
        publish()
        started=time.monotonic()
        response=compiler.request(dict(version=1,operation=operation,incremental=False,reuseArtifacts=False,**options))
        row=dict(stage=stage,seconds=time.monotonic()-started,status=response['status'],
                 metrics=response.get('metrics'),diagnostics=response.get('diagnostics'),error=response.get('error'))
        report['stages'].append(row)
        publish()
        print(json.dumps(row),flush=True)
        if response['status']!='ok':raise RuntimeError(stage+' failed; see summary.json')
        return response['result']

    resources=ExitStack()
    try:
        service=resources.enter_context(closing(CompilerService(code,native_executable=args.native_compiler,limits=limits)))
        # A source snapshot may contain modules that the compiler entry never
        # imports. Discover the delivered closure using the native parser.
        pending=['main.pxl']
        reachable=set()
        while pending:
            path=pending.pop()
            if path in reachable:continue
            if path not in sources:raise ValueError('Compiler import is missing from its source snapshot: '+path)
            info=request('module-info:'+path,service,'module-info',files={path:sources[path]},entry=path)
            reachable.add(path)
            pending.extend(info['imports'])
        report['expectedModules']=sorted(reachable)
        recovered=request('recover-sources',service,'recover-png',image=args.image.read_bytes())
        (args.output/'recovered-sources.json').write_text(json.dumps(recovered,ensure_ascii=False,sort_keys=True))
        files,entry=recovered['files'],recovered['entry']
        report['recoveredModules']=len(files)
        if len(files)!=len(reachable) or entry not in files or not all(isinstance(v,str) for v in files.values()):
            raise ValueError('PNG did not recover the complete compiler source bundle')
        rebuilt=request('compile-recovered-sources',service,'compile',files=files,entry=entry)['bytecode']
        service.close()
        artifact=normalized(rebuilt)
        (args.output/'compiler.json').write_text(artifact)
        report['activeStage']='build-recovered-native-compiler'
        publish()
        executable=build_native(rebuilt,args.output/'compiler-native',optimization=1,max_depth=1024,session=entry_session(rebuilt))
        restored=resources.enter_context(closing(CompilerService(rebuilt,native_executable=executable,limits=limits)))
        repeated=request('recovered-compiler-self-compilation',restored,'compile',files=files,entry=entry)['bytecode']
        (args.output/'self-compiled.json').write_text(normalized(repeated))
        if normalized(repeated)!=artifact:raise ValueError('Recovered compiler does not reproduce itself')
        canonical = request('canonical-source-self-compilation', restored, 'compile',
                            files=sources, entry='main.pxl')['bytecode']
        (args.output/'canonical-compiler.json').write_text(normalized(canonical))
        verify_canonical_compiler(code, canonical)
        report['canonicalCompilerEqual'] = True
        publish()
        program=request('smoke',restored,'compile',files={'main.pxl':'effect fn main(){print(6*7)}'})['bytecode']
        restored.close()
        smoke=build_native(program,args.output/'smoke')
        reference_smoke = verify_smoke(program, smoke)
        unchanged=(fingerprint(ROOT)==before and digest(Path(__file__).read_bytes())==runner
                   and all(digest(path.read_bytes())==inputs[name] for name,path in paths.items()))
        report.update(inputsUnchanged=unchanged,recoveredModules=len(files),recoveredEntry=entry,
                      compilerSha256=digest(artifact.encode()),selfCompilationEqual=True,smokeOutput=[42],
                      referenceSmokeOutput=reference_smoke)
        if not unchanged:raise ValueError('Verification inputs changed')
        report.update(verified=True,status='passed')
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        resources.close()
        publish()


if __name__=='__main__':main()
