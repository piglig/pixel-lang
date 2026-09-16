"""Run: python tests/clean_install.py /absolute/path/to/pixellang.whl

Creates an isolated temporary installation; never imports the repository runtime.
The retained temporary directory can be inspected from the printed report.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(
    description="Clean-install project/image toolchain acceptance check"
)
parser.add_argument("wheel", type=Path)
parser.add_argument("--report", type=Path)
args = parser.parse_args()
if args.report and args.report.exists():parser.error('Report already exists')
repo = Path(__file__).resolve().parents[1]
root = Path(tempfile.mkdtemp(prefix="pixel-clean-toolchain-")).resolve()
env = dict(os.environ)
env.pop("PYTHONPATH", None)
env.pop("VIRTUAL_ENV", None)


def run(args):
    result = subprocess.run(
        [str(a) for a in args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {args}\n{result.stdout}\n{result.stderr}")
    return result.stdout


run(["uv", "venv", root / "env"])
run(["uv", "pip", "install", "--python", root / "env/bin/python", args.wheel.resolve()])
identity=json.loads(run([root/'env/bin/python','-c',
    'import json,pathlib,hashlib,pixellang; p=pathlib.Path(pixellang.__file__).parent; print(json.dumps(dict(package=str(p),version=pixellang.__version__,manifest=json.loads((p/"artifacts/compiler-manifest.json").read_text()),compilerSha256=hashlib.sha256((p/"artifacts/compiler.json").read_bytes()).hexdigest())))']))
assert Path(identity['package']).is_relative_to(root)
assert identity['compilerSha256']==identity['manifest']['sha256']
assert identity['manifest']==json.loads((repo/'pixellang/artifacts/compiler-manifest.json').read_text())
shutil.copytree(
    repo / "examples/data-report",
    root / "project",
    ignore=shutil.ignore_patterns("dist", "program.png"),
)
pixel = root / "env/bin/pixel"
tests = json.loads(run([pixel, "test", root / "project"]))
build = json.loads(run([pixel, "build", root / "project"]))
original = json.loads(
    run(
        [
            pixel,
            "run",
            root / "project",
            "--input",
            root / "project/measurements.csv",
            "--json",
        ]
    )
)
shutil.copy(build["image"], root / "deliverable.png")
shutil.rmtree(root / "project")
(root / "changed.csv").write_text("8,6,2\n")
changed = json.loads(
    run(
        [
            pixel,
            "run",
            root / "deliverable.png",
            "--input",
            root / "changed.csv",
            "--json",
        ]
    )
)
assert tests["passed"] and original["output"][2] == [1, 2, 3, 4, 8]
assert changed["output"][2] == [2, 6, 8]
probe = root / "stdlib-probe"
probe.mkdir()
(probe / "pixel.toml").write_text('format = 1\n[project]\nname = "stdlib-probe"\n')
(probe / "main.pxl").write_text(
    'import "std/csv.pxl" as csv\nimport "std/stats.pxl" as stats\nfn main() { print(csv.Parse(input()))\nprint(stats.Summary([2, 4])) }'
)
probe_build = json.loads(run([pixel, "build", probe]))
shutil.copy(probe_build["image"], root / "stdlib.png")
shutil.rmtree(probe)
(root / "quoted.csv").write_text('a,"b,c"')
stdlib = json.loads(
    run([pixel, "run", root / "stdlib.png", "--input", root / "quoted.csv", "--json"])
)
assert stdlib["output"] == [
    [["a", "b,c"]],
    {"count": 2, "total": 6, "min": 2, "max": 4, "mean": 3},
]

print(
    json.dumps(
        {
            "clean_environment": str(root),
            "tests": tests,
            "bundled_stdlib_output": stdlib["output"],
            "image_sha256": build["sha256"],
            "source_free_changed_input_output": changed["output"],
        },
        indent=2,
    )
)

# The actual release-target application, not only the small toolchain probe.
ledger = root / "ledger"
shutil.copytree(
    repo / "examples/ledger", ledger, ignore=shutil.ignore_patterns("dist", ".vscode")
)
ledger_tests = json.loads(run([pixel, "test", ledger]))
assert ledger_tests["passed"], ledger_tests
ledger_build = json.loads(run([pixel, "build", ledger]))
shutil.copy(ledger_build["image"], root / "ledger.png")
inputs, outputs = root / "inputs", root / "outputs"
inputs.mkdir()
outputs.mkdir()
shutil.copy(ledger / "fixtures/changed.csv", inputs / "changed.csv")
shutil.copy(ledger / "fixtures/changed-config.json", root / "ledger-config.json")
expected = json.loads((ledger / "fixtures/changed-expected.json").read_text())
shutil.rmtree(ledger)
# Build the JSONL project before removing installed library sources.
logs = root / "log-analysis"
shutil.copytree(
    repo / "examples/log-analysis", logs, ignore=shutil.ignore_patterns("dist")
)
log_tests = json.loads(run([pixel, "test", logs]))
assert log_tests["passed"], log_tests
log_build = json.loads(run([pixel, "build", logs]))
shutil.copy(log_build["image"], root / "logs.png")
log_inputs = root / "log-inputs"
shutil.copytree(logs / "fixtures", log_inputs)
log_expected = json.loads((log_inputs / "expected.json").read_text())
shutil.rmtree(logs)
# Build the new generic/closure pipeline with only the installed runtime.
callbacks = root / "callback-analysis"
shutil.copytree(
    repo / "examples/callback-analysis",
    callbacks,
    ignore=shutil.ignore_patterns("dist"),
)
callback_tests = json.loads(run([pixel, "test", callbacks]))
assert callback_tests["passed"], callback_tests
callback_build = json.loads(run([pixel, "build", callbacks]))
shutil.copy(callback_build["image"], root / "callbacks.png")
shutil.copy(callbacks / "input.json", root / "callback-input.json")
callback_expected = json.loads((callbacks / "expected.json").read_text())
shutil.rmtree(callbacks)
# Remove installed stdlib source in this disposable environment: the PNG must carry it.
package_path = Path(
    run(
        [
            root / "env/bin/python",
            "-c",
            "import pathlib,pixellang; print(pathlib.Path(pixellang.__file__).parent)",
        ]
    ).strip()
)
shutil.rmtree(package_path / "stdlib")
ledger_run = json.loads(
    run(
        [
            pixel,
            "run",
            root / "ledger.png",
            "--input",
            root / "ledger-config.json",
            "--read-root",
            inputs,
            "--write-root",
            outputs,
            "--json",
        ]
    )
)
assert ledger_run["output"] == expected
assert json.loads((outputs / "report.json").read_text()) == expected[0]
print(
    json.dumps(
        {
            "ledger_tests": ledger_tests,
            "ledger_image_sha256": ledger_build["sha256"],
            "ledger_source_and_stdlib_removed": True,
            "ledger_changed_input_output": ledger_run["output"],
        },
        indent=2,
    )
)

log_run = json.loads(
    run(
        [
            pixel,
            "run",
            root / "logs.png",
            "--input",
            log_inputs / "config.json",
            "--read-root",
            log_inputs,
            "--write-root",
            outputs,
            "--json",
        ]
    )
)
assert log_run["output"] == log_expected
assert json.loads((outputs / "report.json").read_text()) == log_expected[0]
print(
    json.dumps(
        {
            "log_tests": log_tests,
            "log_image_sha256": log_build["sha256"],
            "log_source_and_stdlib_removed": True,
            "log_output": log_run["output"],
        },
        indent=2,
    )
)

callback_run = json.loads(
    run(
        [
            pixel,
            "run",
            root / "callbacks.png",
            "--input",
            root / "callback-input.json",
            "--json",
        ]
    )
)
assert callback_run["output"] == callback_expected
recovered=root/'recovered-callbacks'
restored=json.loads(run([root/'env/bin/pixelunpack',root/'callbacks.png','-o',recovered,'--json']))
restored_run=json.loads(run([pixel,'run',recovered/restored['entry'],'--input',root/'callback-input.json','--json']))
assert restored_run['output']==callback_expected
print(
    json.dumps(
        {
            "callback_tests": callback_tests,
            "callback_image_sha256": callback_build["sha256"],
            "callback_source_and_stdlib_removed": True,
            "callback_output": callback_run["output"],
        },
        indent=2,
    )
)

report=dict(verified=True,cleanEnvironment=str(root),installed=identity,
    wheelSha256=hashlib.sha256(args.wheel.read_bytes()).hexdigest(),
    dataTests=tests,ledgerTests=ledger_tests,logTests=log_tests,callbackTests=callback_tests,
    originalSourcesRemoved=True,installedStdlibRemoved=True,
    recoveredProject=restored,sourceFreeOutputsVerified=True,recoveredOutputVerified=True)
if args.report:
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
