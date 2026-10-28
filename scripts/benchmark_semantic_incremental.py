"""Compare function-check cache reuse with fresh checking on compiler sources.

This is a correctness/performance probe, not a release acceptance receipt.
Both modes use the same candidate and edit; fresh builds retain no query snapshots.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pixellang.bootstrap import normalized
from pixellang.compiler_service import CompilerService,Limits


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler',type=Path,required=True)
    parser.add_argument('--native-compiler',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--trials',type=int,default=3)
    args=parser.parse_args()
    if not 1<=args.trials<=20:parser.error('--trials must be in 1..20')
    code=json.loads(args.compiler.read_text())
    sources={p.name:p.read_text() for p in sorted((ROOT/'selfhost').glob('*.pxl'))}
    original='string(module) + ":" + string(node)'
    replacement='join([string(module), string(node)], ":")'
    if sources['checker_state.pxl'].count(original)!=1:
        raise RuntimeError('Benchmark edit no longer matches exactly one function')
    rows=[]
    expected={}
    for trial in range(args.trials):
        for reuse in ((False,True) if trial%2==0 else (True,False)):
            service=CompilerService(code,native_executable=args.native_compiler,
                                    limits=Limits(seconds=1200,steps=3_000_000_000))
            files=dict(sources)
            for label in ('cold','edit'):
                if label=='edit':files['checker_state.pxl']=files['checker_state.pxl'].replace(original,replacement)
                result=service.request(dict(version=1,operation='compile',files=files,
                                            reuseArtifacts=False,incremental=reuse))
                if result['status']!='ok':raise RuntimeError(result)
                digest=hashlib.sha256(normalized(result['result']['bytecode']).encode()).hexdigest()
                if label in expected and expected[label]!=digest:
                    raise RuntimeError('Cached and fresh compiler outputs differ')
                expected[label]=digest
                if reuse and label=='edit':
                    metrics=result['metrics']
                    if any(metrics[name]==0 for name in ('bindingCacheHits','typeCacheHits','semanticCacheHits')):
                        raise RuntimeError('Candidate did not reuse declaration and function queries')
                    if metrics['bindingCheckedDeclarations']!=1 or metrics['typeCheckedDeclarations']!=1:
                        raise RuntimeError('Single-function edit reanalyzed unrelated declarations')
                row=dict(trial=trial,incremental=reuse,phase=label,bytecodeSha256=digest,metrics=result['metrics'])
                rows.append(row)
                print(json.dumps(row),flush=True)
            service.close()
    if sources!={p.name:p.read_text() for p in (ROOT/'selfhost').glob('*.pxl')}:
        raise RuntimeError('Compiler sources changed during the comparison')
    report=dict(verified=True,compilerSha256=service.compiler_sha256,
                sourceSha256=hashlib.sha256(json.dumps(sources,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                rows=rows)
    args.output.write_text(json.dumps(report,indent=2))


if __name__=='__main__':main()
