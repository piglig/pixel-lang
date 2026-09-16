"""Build a service compiler with an existing PixelLang compiler; no host frontend."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pixellang.bootstrap import compile_with, normalized
from pixellang.compiler_service import PROTOCOL_VERSION


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=float,default=600)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    sources={p.name:p.read_text() for p in sorted((ROOT/'selfhost').glob('*.pxl'))}
    snapshot=json.dumps(sources,ensure_ascii=False,sort_keys=True)
    (args.output/'sources.json').write_text(snapshot)
    seed=args.seed.read_bytes()
    began=time.monotonic()
    code,steps=compile_with(json.loads(seed),sources,max_heap_items=64_000_000,max_heap_objects=2_000_000,
                            timeout=args.timeout,progress=lambda e:print(json.dumps(e),flush=True))
    artifact=normalized(code).encode()
    if sources!={p.name:p.read_text() for p in (ROOT/'selfhost').glob('*.pxl')} or seed!=args.seed.read_bytes():
        raise RuntimeError('Compiler source or seed changed during compilation')
    (args.output/'compiler.json').write_bytes(artifact)
    manifest=dict(protocolVersion=PROTOCOL_VERSION,sha256=hashlib.sha256(artifact).hexdigest(),
                  sourceSha256=hashlib.sha256(snapshot.encode()).hexdigest(),seedSha256=hashlib.sha256(seed).hexdigest(),
                  steps=steps,seconds=time.monotonic()-began,files=len(sources),
                  provenance='PixelLang-compiled development artifact; final fixed-point acceptance separate')
    (args.output/'compiler-manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(manifest),flush=True)


if __name__=='__main__':main()
