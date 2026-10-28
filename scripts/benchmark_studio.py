"""Measure interactive Studio compilation separately from optional projection/PNG.

Use a new output path and run without other CPU-heavy jobs for a final comparison.
This reports real project latency; it does not replace release correctness gates.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixellang.workstation import Workstation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True, help='Project directory containing main.pxl')
    parser.add_argument('--entry', default='main.pxl')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--power-condition', default='uncontrolled-development')
    parser.add_argument('--edited-project', type=Path,
                        help='Second source snapshot for measuring a real edit and subsequent cache reuse')
    parser.add_argument('--interactive-only', action='store_true',
                        help='Measure builds only; does not certify projection, PNG or replay')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new evidence file; previous measurements are never overwritten')
    project = args.project.resolve()
    files = {p.relative_to(project).as_posix(): p.read_text() for p in sorted(project.rglob('*.pxl'))}
    if args.entry not in files:
        parser.error('Entry must name a source file inside the project')
    edited = None
    if args.edited_project:
        edited_root = args.edited_project.resolve()
        edited = {p.relative_to(edited_root).as_posix(): p.read_text()
                  for p in sorted(edited_root.rglob('*.pxl'))}
        if args.entry not in edited or edited == files:
            parser.error('Edited project must contain the entry and differ from the initial snapshot')
    report = dict(scope='Studio project build, exact-input reuse, lazy views and replay restore',
                  project=str(project), entry=args.entry, powerCondition=args.power_condition,
                  platform=platform.platform(), python=sys.version, measurements=[], verified=False,
                  sourceSha256=hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                  compilerManifest=json.loads((ROOT/'pixellang/artifacts/compiler-manifest.json').read_text()))
    report['interactiveOnly'] = args.interactive_only
    if args.interactive_only:
        report['scope'] = 'Studio cold build and exact-input reuse; optional edited-source rebuild; excludes PNG, projection and replay'
    if edited is not None:
        report['editedProject'] = str(edited_root)
        report['editedSourceSha256'] = hashlib.sha256(json.dumps(edited, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        report['changedFiles'] = sorted(name for name in files.keys() | edited.keys()
                                       if files.get(name) != edited.get(name))
    report['artifactSha256'] = hashlib.sha256((ROOT/'pixellang/artifacts/compiler.json').read_bytes()).hexdigest()
    report['runtimeFiles'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted((ROOT/'pixellang').glob('*.py'))}
    report['stdlibFiles'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in sorted((ROOT/'pixellang/stdlib').glob('*.pxl'))}
    if sys.platform == 'darwin':
        report['powerSnapshot'] = subprocess.check_output(['pmset', '-g', 'batt'], text=True)
        report['powerSettings'] = subprocess.check_output(['pmset', '-g', 'custom'], text=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')

    studio = Workstation()
    original = studio.compiler.worker.request
    calls = []

    def observed(request, **kwargs):
        response = original(request, **kwargs)
        calls.append(dict(operation=request['operation'], status=response['status'], metrics=response.get('metrics', {})))
        return response

    studio.compiler.worker.request = observed

    def measure(name, method, arguments):
        calls.clear()
        started = time.perf_counter()
        try:
            result = studio.request(method, arguments)
        except Exception as error:
            report['measurements'].append(dict(name=name, seconds=time.perf_counter()-started, calls=list(calls), error=str(error)))
            save()
            raise
        report['measurements'].append(dict(name=name, seconds=time.perf_counter()-started, calls=list(calls)))
        save()
        print(json.dumps(report['measurements'][-1]), flush=True)
        return result

    try:
        cold = measure('cold-build', 'build', dict(files=files, entry=args.entry))
        assert cold['inspection'] == {'ir': None} and not cold['projection_ready'] and 'image' not in cold
        assert not cold['buildCacheHit']
        warm = measure('warm-build', 'build', dict(files=files, entry=args.entry))
        assert warm['buildCacheHit'] and not calls
        first_session = studio.sessions[cold['session']]
        warm_session = studio.sessions[warm['session']]
        assert warm_session.code_image is first_session.code_image
        assert warm_session.vm is not first_session.vm
        assert warm['inspection'] == {'ir': None}
        if edited is not None:
            changed = measure('edited-build', 'build', dict(files=edited, entry=args.entry))
            assert not changed['buildCacheHit']
            assert not changed['projection_ready'] and 'image' not in changed
            assert len(calls) == 1 and calls[0]['operation'] == 'debug-compile'
            assert not calls[0]['metrics']['artifactCacheHit']
            repeated = measure('warm-edited-build', 'build', dict(files=edited, entry=args.entry))
            assert repeated['buildCacheHit'] and not calls
            changed_session = studio.sessions[changed['session']]
            repeated_session = studio.sessions[repeated['session']]
            assert repeated_session.code_image is changed_session.code_image
            assert repeated_session.vm is not changed_session.vm
            assert repeated['inspection'] == {'ir': None}
        if args.interactive_only:
            report['verified'] = True
            save()
            return
        session = dict(session=warm['session'])
        recording = studio.request('recording', session)
        projected = measure('projection', 'inspect-projection', session)
        assert projected['projection_ready']
        assert studio.request('recording', session) == recording
        measure('warm-projection', 'inspect-projection', session)
        assert not calls
        image = measure('png-export', 'inspect-image', session)
        assert image['image']
        assert measure('warm-png', 'inspect-image', session) == image and not calls
        assert studio.request('recording', session) == recording
        restored = measure('restore', 'restore', dict(document=recording))
        assert not restored['projection_ready'] and 'image' not in restored
        assert studio.request('recording', dict(session=restored['session'])) == recording
        report['verified'] = True
        save()
    except Exception as error:
        report['error'] = str(error)
        save()
        raise
    finally:
        studio.close()


if __name__ == '__main__':
    main()
