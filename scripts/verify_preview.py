"""Layered preview checks with immutable inputs and durable per-command evidence.

related: explicit selected tests only.
merge: host language/project regression, generated compiler regression, Studio
bridge checks and the installed-service performance budget.
release: merge plus three generations, uncached complete PNG bootstrap and
regressions of the PNG-recovered compiler. Packaging/UI acceptance is separate.
"""
import argparse
import hashlib
import json
import os
import platform
import importlib.metadata
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pixellang.regression import fingerprint
from pixellang.gate_cache import StageReuse, hashes, command_key


def inputs():
    result=fingerprint(ROOT)
    for directory in ('scripts','vscode/src','vscode/test','vscode/syntaxes','vscode/media','vscode/scripts'):
        for path in (ROOT/directory).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                result[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ('vscode/package.json','vscode/package-lock.json','config/service-performance-limits.json','uv.lock'):
        path=ROOT/name
        result[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def command(argv,log,timeout):
    with log.open('w') as output:
        process=subprocess.Popen(argv,cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            # Python regression supervisors catch SIGINT and reap their own
            # separately grouped workers before this outer process exits.
            os.killpg(process.pid,signal.SIGINT)
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait()
            raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('level',choices=('related','merge','release'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--test',action='append',default=[])
    parser.add_argument('--resume-from',type=Path,help='Reuse completed exact-input stages in a new evidence directory')
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--timings-from',type=Path,help='Previous gate directory used only for test-shard scheduling, never for passing results')
    args=parser.parse_args()
    if not 1<=args.workers<=16:parser.error('workers must be 1..16')
    if bool(args.test)!=(args.level=='related'):parser.error('--test is required for related and forbidden for broader gates')
    output=args.output.resolve()
    if output.is_relative_to(ROOT):parser.error('Evidence directory must be outside the source tree')
    output.mkdir(parents=True,exist_ok=False)
    before=inputs()
    manifest={'level':args.level,'tests':args.test,'workers':args.workers,'files':before,
              'environment':{'python':sys.version,'executable':sys.executable,'platform':platform.platform(),
                             'packages':sorted([d.metadata['Name'],d.version] for d in importlib.metadata.distributions()),
                             'environmentSha256':hashlib.sha256(json.dumps({k:v for k,v in os.environ.items() if k not in ('_','SHLVL','PWD','OLDPWD')},sort_keys=True).encode()).hexdigest(),
                             'node':subprocess.run(['node','--version'],capture_output=True,text=True,check=True).stdout.strip() if args.level!='related' else None}}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    reuse=StageReuse(args.resume_from,manifest) if args.resume_from else None
    sources={p.name:p.read_text() for p in sorted((ROOT/'selfhost').glob('*.pxl'))}
    snapshot=output/'sources.json';snapshot.write_text(json.dumps(sources,ensure_ascii=False,sort_keys=True))
    py=sys.executable
    result={'level':args.level,'fresh':True,'verified':False,'commands':[],'limitations':'Packaging, clean installation and real VS Code UI acceptance are separate requirements.'}
    def scheduling(stage):
        if args.timings_from is None:return []
        directory=args.timings_from.resolve()/stage
        if not (directory/'events.jsonl').is_file():return []
        return ['--timings',str(directory)]
    def run(name,argv,evidence=None,required='verified',timeout=1200):
        if reuse is not None and name!='performance':
            restored=reuse.restore(name,argv,output)
            if restored is not None:
                if evidence is not None:
                    report=json.loads(evidence.read_text())
                    if report.get(required) not in (True,'passed'):raise RuntimeError(name+' reused evidence did not prove '+required)
                    restored['evidence']=str(evidence)
                result['commands'].append(restored);result['fresh']=False
                (output/'summary.json').write_text(json.dumps(result,indent=2))
                print('Reused verified exact-input stage '+name,flush=True)
                return
        started=time.monotonic()
        prior=hashes(output)
        print('Starting '+name,flush=True)
        entry={'name':name,'argv':argv,'commandKey':command_key(argv,output),'completed':False,'log':str(output/(name+'.log'))}
        result['commands'].append(entry)
        try:
            entry['exitCode']=command(argv,Path(entry['log']),timeout)
            if entry['exitCode']!=0:raise RuntimeError(name+' failed; see '+entry['log'])
            if evidence is not None:
                report=json.loads(evidence.read_text())
                if report.get(required) not in (True,'passed'):raise RuntimeError(name+' did not prove '+required)
                entry['evidence']=str(evidence)
            if inputs()!=before:raise RuntimeError('Inputs changed during '+name)
            entry['artifacts']={p:h for p,h in hashes(output).items() if prior.get(p)!=h and p!='summary.json'}
            entry['completed']=True
        finally:
            entry['seconds']=time.monotonic()-started
            (output/'summary.json').write_text(json.dumps(result,indent=2))
    try:
        if args.level=='related':
            dest=output/'related'
            run('related',[py,'-m','pixellang.regression','--output',str(dest),
                           '--workers',str(args.workers),*scheduling('related'),
                           *[part for test in args.test for part in ('--test',test)]],dest/'summary.json')
            result['verified']=True
        else:
            manifest=json.loads((ROOT/'pixellang/artifacts/compiler-manifest.json').read_text())
            artifact=ROOT/'pixellang/artifacts/compiler.json'
            if hashlib.sha256(artifact.read_bytes()).hexdigest()!=manifest['sha256'] or hashlib.sha256(snapshot.read_bytes()).hexdigest()!=manifest['sourceSha256']:
                raise RuntimeError('Installed compiler must match current frozen source and artifact hashes')
            dest=output/'language-project'
            run('language-project',[py,'-m','pixellang.regression','--scope','host','--workers',str(args.workers),'--timeout','1200','--output',str(dest),*scheduling('language-project')],dest/'summary.json')
            def generated(name,compiler):
                dest=output/name
                run(name,[py,'scripts/verify_generated_compiler.py','--compiler',str(compiler),'--sources',str(snapshot),
                          '--workers',str(args.workers),'--timeout','1200','--output',str(dest),*scheduling(name)],dest/'summary.json',timeout=2400)
            generated('generated',artifact)
            run('bridge',['node','vscode/test/bridge-service.js'],timeout=120)
            run('dap',['node','vscode/test/debug-launch.js'],timeout=30)
            perf=output/'performance.json'
            run('performance',[py,'scripts/benchmark_service.py','--output',str(perf),'--limits','config/service-performance-limits.json'],perf,required='gate',timeout=120)
            if args.level=='release':
                dest=output/'bootstrap'
                run('bootstrap',[py,'-m','pixellang.bootstrap','--generations','3','--heap-items','64000000','--heap-objects','2000000','--stage-timeout','600','--output',str(dest)],dest/'summary.json',required='fixedPointVerified',timeout=2400)
                if (dest/'stage3.json').read_bytes()!=artifact.read_bytes():raise RuntimeError('Installed artifact differs from accepted fixed point')
                image=output/'image'
                run('image',[py,'-m','pixellang.bootstrap_image','--stage-timeout','1200','--output',str(image)],image/'summary.json',timeout=3600)
                recovered=output/'image-generated'
                run('image-generated',[py,'scripts/verify_compiler_image.py','--compiler',str(dest/'stage3.json'),'--sources',str(snapshot),'--image',str(image/'compiler.png'),'--output',str(recovered)],recovered/'summary.json',timeout=1800)
                generated('recovered-regression',recovered/'compiler.json')
            result['verified']=True
    except Exception as error:
        result['error']=str(error)
    finally:
        result['inputsUnchanged']=inputs()==before
        result['verified']=result['verified'] and result['inputsUnchanged']
        (output/'summary.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2),flush=True)
    return 0 if result['verified'] else 1


if __name__=='__main__':sys.exit(main())
