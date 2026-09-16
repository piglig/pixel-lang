"""Opt-in native VM loading, differential tests and compiler-workload measurement.

No production default changes. A native extension must match current vm.py and ABI.
"""
import argparse
import cProfile
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import resource
import subprocess
import sys
import sysconfig
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def load_native(manifest_path):
    manifest=json.loads(Path(manifest_path).read_text())
    extension=Path(manifest['extension'])
    if (manifest['sourceSha256']!=hashlib.sha256((ROOT/'pixellang/vm.py').read_bytes()).hexdigest()
        or manifest['extensionSha256']!=hashlib.sha256(extension.read_bytes()).hexdigest()
        or manifest['abi']!=sysconfig.get_config_var('SOABI')):
        raise ValueError('Native prototype source, binary or ABI mismatch')
    if 'pixellang.vm' in sys.modules:raise RuntimeError('Load prototype before importing VM consumers')
    import pixellang
    spec=importlib.util.spec_from_file_location('pixellang._native_vm',extension)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module;spec.loader.exec_module(module)
    sys.modules['pixellang.vm']=module;pixellang.vm=module
    return manifest


def digest(value):
    def encode(v):
        if isinstance(v,bytes):return {'binarySha256':hashlib.sha256(v).hexdigest()}
        raise TypeError(type(v).__name__)
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=encode).encode()).hexdigest()


def power():
    if sys.platform!='darwin':return None
    return {name:subprocess.run(['pmset','-g',name],capture_output=True,text=True,check=True).stdout
            for name in ('batt','custom')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--test',action='append')
    parser.add_argument('--functions',type=int,default=40)
    parser.add_argument('--self',action='store_true',dest='whole_compiler')
    parser.add_argument('--compile-only',action='store_true',help='Skip PNG stages; useful for a bounded full-compiler comparison')
    parser.add_argument('--profile',action='store_true')
    parser.add_argument('--compiler',type=Path,default=ROOT/'pixellang/artifacts/compiler.json')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    native=load_native(args.native) if args.native else None
    from pixellang.compiler_service import CompilerService,Limits
    from pixellang.vm import VM
    from pixellang.regression import fingerprint
    before=fingerprint(ROOT)
    (args.output/'input-files.json').write_text(json.dumps(before,indent=2))
    report=dict(variant='native-prototype' if native else 'python',native=native,python=sys.version,
                powerBefore=power(),platform=platform.platform(),verified=False,profiled=args.profile)
    report['runtimeSources']={name:before['pixellang/'+name] for name in ('vm.py','typesys.py','typeexpr.py','jsonvalue.py')}
    try:
        if args.test:
            suite=unittest.defaultTestLoader.loadTestsFromNames(args.test)
            with (args.output/'tests.log').open('w') as log:
                result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
            report.update(tests=result.testsRun,verified=result.wasSuccessful())
        else:
            code=json.loads(args.compiler.read_bytes())
            report['compilerSha256']=hashlib.sha256(args.compiler.read_bytes()).hexdigest()
            files=({p.name:p.read_text() for p in sorted((ROOT/'selfhost').glob('*.pxl'))} if args.whole_compiler else
                   {'main.pxl':'\n'.join(f'fn f{i}(n:int)->int=n+{i}' for i in range(args.functions))+'\nfn main(){print(f0(42))}'})
            report['workloadSha256']=digest(files);report['wholeCompiler']=args.whole_compiler;report['compileOnly']=args.compile_only
            (args.output/'workload.json').write_text(json.dumps(files,ensure_ascii=False,sort_keys=True))
            service=CompilerService(code,limits=Limits(seconds=1800,steps=1_000_000_000))
            records=[];report['measurements']=records
            def run(operation,**kw):
                start=time.perf_counter()
                response=service.request(dict(version=1,operation=operation,reuseArtifacts=False,**kw))
                if response['status']!='ok':raise RuntimeError(str(response))
                records.append(dict(operation=operation,seconds=time.perf_counter()-start,metrics=response['metrics'],resultSha256=digest(response['result'])))
                (args.output/'partial.json').write_text(json.dumps(report,indent=2))
                return response['result']
            profiler=cProfile.Profile() if args.profile else None
            if profiler:profiler.enable()
            try:
                compiled=run('compile',files=files)
                from pixellang.bootstrap import normalized
                (args.output/'compiler-output.json').write_text(normalized(compiled['bytecode']))
                if args.compile_only:
                    recovered=compiled
                else:
                    image=run('export-png',files=files,scale=4)
                    recovered=run('compile-png',image=image['image'])
            finally:
                if profiler:
                    profiler.disable();profiler.dump_stats(str(args.output/'runtime.prof'))
            if not args.whole_compiler:
                assert VM(compiled['bytecode']).run()==VM(recovered['bytecode']).run()==[42]
            else:
                # Full compiler bytecode accepts a fresh program through the service.
                recovered_service=CompilerService(recovered['bytecode'])
                smoke=recovered_service.request(dict(version=1,operation='compile',files={'main.pxl':'fn main(){print(42)}'}))
                assert smoke['status']=='ok',smoke
                assert VM(smoke['result']['bytecode']).run()==[42]
            report['verified']=True
    finally:
        report['inputsUnchanged']=fingerprint(ROOT)==before
        report['verified']=report['verified'] and report['inputsUnchanged']
        report['powerAfter']=power()
        report['peakRssBytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
        (args.output/'summary.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2));return 0 if report['verified'] else 1


if __name__=='__main__':sys.exit(main())
