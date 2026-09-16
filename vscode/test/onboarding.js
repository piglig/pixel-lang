const assert = require("node:assert/strict");
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const { invoke, pythonPath, register } = require("../src/onboarding");
async function main() {
  const runtime = path.resolve(__dirname, "../runtime");
  const python = process.env.PIXELLANG_TEST_PYTHON || path.resolve(__dirname, "../../.venv/bin/python");
  assert.equal(pythonPath(python, undefined), python);
  for (const workspaceFolders of [undefined, [{ uri: { fsPath: "/workspace" } }]]) {
    const actions = {}, updates = [];
    const vscode = {
      ConfigurationTarget: { Global: "global", Workspace: "workspace" },
      workspace: { workspaceFolders, getConfiguration: () => ({ update: async (...args) => updates.push(args) }) },
      window: { showOpenDialog: async () => [{ fsPath: python }] },
      commands: { registerCommand: (name, action) => { actions[name] = action; return { dispose() {} }; } },
    };
    register(vscode, { extensionPath: path.dirname(runtime), subscriptions: [] }, { appendLine() {}, show() {} });
    await actions["pixellang.selectPython"]();
    assert.deepEqual(updates, [["pythonPath", python, workspaceFolders ? "workspace" : "global"]]);
  }
  const report = await invoke(python, runtime, "doctor");
  assert.equal(report.passed, true, JSON.stringify(report));
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "pixel-onboarding-"));
  try {
    const project = path.join(parent, "hello-pixels");
    const created = await invoke(python, runtime, "init", [project]);
    assert.equal(created.passed, true);
    const duplicate = await invoke(python, runtime, "init", [project]);
    assert.equal(duplicate.passed, false);
    assert.match(duplicate.error, /empty directory/);
    await assert.rejects(invoke(path.join(parent, "absent-python"), runtime, "doctor"), /pixellang.pythonPath/);
  } finally { fs.rmSync(parent, { recursive: true, force: true }); }
  console.log("Studio onboarding bridge passed");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
