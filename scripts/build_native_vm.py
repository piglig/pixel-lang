"""Build an opt-in native-compiled VM prototype outside the checkout.

Run with Cython and setuptools in an isolated build environment. This compiles
exactly vm.py with Python object semantics; it is not a Python-free runtime.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys
import sysconfig
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve()
    if output.is_relative_to(ROOT):parser.error('Prototype output must be outside the checkout')
    output.mkdir(parents=True,exist_ok=False)
    from Cython.Build import cythonize
    import Cython
    from setuptools import Extension, Distribution
    source=ROOT/'pixellang/vm.py';frozen=source.read_bytes()
    staged=output/'vm.py';staged.write_bytes(frozen)
    began=time.perf_counter()
    extensions=cythonize([Extension('pixellang._native_vm',[str(staged)])],
        compiler_directives={'language_level':3,'annotation_typing':False,'infer_types':False},
        build_dir=str(output/'generated'),annotate=True)
    distribution=Distribution({'name':'pixel-native-vm-prototype','ext_modules':extensions})
    build=distribution.get_command_obj('build_ext')
    build.build_lib=str(output/'lib');build.build_temp=str(output/'temp')
    distribution.run_command('build_ext')
    extension=next((output/'lib/pixellang').glob('_native_vm*'+sysconfig.get_config_var('SHLIB_SUFFIX')))
    if source.read_bytes()!=frozen:raise RuntimeError('VM source changed during build')
    manifest=dict(sourceSha256=hashlib.sha256(frozen).hexdigest(),extension=str(extension),
                  extensionSha256=hashlib.sha256(extension.read_bytes()).hexdigest(),
                  python=sys.version,abi=sysconfig.get_config_var('SOABI'),platform=platform.platform(),
                  cython=Cython.__version__,seconds=time.perf_counter()-began,
                  scope='Native-compiled dispatch with CPython objects, GIL and Python runtime dependencies; opt-in prototype')
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
