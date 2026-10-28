"""Stage a verified native compiler bundle without modifying installed artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pixellang.native_bundle import load_bundle


def stage(acceptance,output):
    acceptance=Path(acceptance).resolve()
    report=json.loads((acceptance/'status.json').read_text())
    stages={item['stage']:item for item in report['stages']}
    if (report.get('status')!='passed' or report.get('sourcesUnchanged') is not True
        or not all(stages.get(f'generation-{n}',{}).get('equalPrevious') is True for n in (2,3))
        or stages.get('recovered-selfcompile',{}).get('equalCompiler') is not True
        or stages.get('recovered-smoke',{}).get('stdout')!='42\n'):
        raise ValueError('Complete native self-host and PNG acceptance is required')
    data=(acceptance/'compiler-g3.json').read_bytes()
    digest=hashlib.sha256(data).hexdigest()
    if digest!=stages['generation-3']['sha256']:
        raise ValueError('Accepted compiler bytecode was modified')
    executable=acceptance/'compiler-g3'
    mapping=json.loads(executable.with_name(executable.name+'.map.json').read_text())
    binary_hash=hashlib.sha256(executable.read_bytes()).hexdigest()
    if mapping.get('executableSha256')!=binary_hash or mapping.get('bytecodeSha256')!=digest:
        raise ValueError('Accepted native compiler identity does not match its source map')
    machine=platform.machine()
    if sys.platform=='darwin':
        commands=subprocess.check_output(['otool','-l',str(executable)],text=True)
        minimum=re.search(r'\bminos\s+(\d+)\.(\d+)',commands)
        if minimum is None:raise ValueError('Cannot determine native macOS deployment target')
        wheel_tag=f'macosx_{minimum[1]}_{minimum[2]}_{machine}'
    elif sys.platform.startswith('linux'):
        wheel_tag=f'linux_{machine}'
    else:
        raise ValueError('Native compiler packaging currently requires a POSIX build host')
    manifest=dict(protocolVersion=1,sha256=digest,sourceSha256=report['sourceSha256'],
        files=len(json.loads((acceptance/'sources.json').read_text())),
        seedSha256=report['seedSha256'],
        provenance='Development native compiler: self-host fixed point and full PNG recovery verified; distribution and Studio acceptance are separate gates.',
        acceptanceSha256=hashlib.sha256((acceptance/'status.json').read_bytes()).hexdigest(),
        native=dict(file='compiler-native',sha256=binary_hash,platform=sys.platform,machine=machine,wheelTag=wheel_tag))
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    shutil.copy2(acceptance/'compiler-g3.json',output/'compiler.json')
    shutil.copy2(executable,output/'compiler-native')
    shutil.copy2(executable.with_name(executable.name+'.map.json'),output/'compiler-native.map.json')
    (output/'compiler-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    load_bundle(output)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acceptance',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(stage(args.acceptance,args.output),indent=2))


if __name__=='__main__':main()
