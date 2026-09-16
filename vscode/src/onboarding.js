const fs = require("node:fs"), path = require("node:path");
const { execFile } = require("node:child_process");

function pythonPath(configured, root) {
  const candidate = root && path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  return configured || (candidate && fs.existsSync(candidate) ? candidate : process.platform === "win32" ? "python" : "python3");
}

function invoke(python, runtime, command, args = []) {
  return new Promise((resolve, reject) => {
    execFile(python, ["-m", "pixellang", command, ...args, "--json"], {
      cwd: runtime,
      env: { ...process.env, PYTHONPATH: runtime, PYTHONIOENCODING: "utf-8" },
      timeout: 30000,
      maxBuffer: 1024 * 1024,
    }, (error, stdout, stderr) => {
      if (error && error.code !== 1) return reject(new Error(`Cannot run ${python}: ${error.message}. Set pixellang.pythonPath to Python 3.11+ with Pillow.`));
      try {
        const result = JSON.parse(stdout);
        if (typeof result.passed !== "boolean") throw new Error("Missing diagnostic status");
        resolve(result);
      } catch (_) {
        reject(new Error(stderr || stdout || "PixelLang returned no diagnostic result."));
      }
    });
  });
}

function register(vscode, context, log) {
  const runtime = path.join(context.extensionPath, "runtime");
  const python = () => pythonPath(vscode.workspace.getConfiguration("pixellang").get("pythonPath"), vscode.workspace.workspaceFolders?.[0]?.uri.fsPath);
  async function configure() {
    const selected = await vscode.window.showOpenDialog({ canSelectMany: false, canSelectFiles: true, canSelectFolders: false, openLabel: "Select Python interpreter" });
    if (selected?.length) await vscode.workspace.getConfiguration("pixellang").update("pythonPath", selected[0].fsPath,
      vscode.workspace.workspaceFolders?.length ? vscode.ConfigurationTarget.Workspace : vscode.ConfigurationTarget.Global);
  }
  async function doctor() {
    if (!vscode.workspace.isTrusted) throw new Error("Trust this workspace before running PixelLang.");
    let result;
    try { result = await invoke(python(), runtime, "doctor"); }
    catch (error) { result = { passed: false, error: error.message }; }
    log.appendLine(JSON.stringify(result, null, 2));
    log.show(true);
    if (result.passed) await vscode.window.showInformationMessage("PixelLang environment is ready.");
    else {
      const choice = await vscode.window.showErrorMessage("PixelLang environment needs attention. See the PixelLang output for fixes.", "Select Python Interpreter");
      if (choice) await configure();
    }
    return result;
  }
  async function createProject() {
    if (!vscode.workspace.isTrusted) throw new Error("Trust this workspace before running PixelLang.");
    const folders = await vscode.window.showOpenDialog({ canSelectFolders: true, canSelectFiles: false, canSelectMany: false, openLabel: "Choose parent folder" });
    if (!folders?.length) return;
    const name = await vscode.window.showInputBox({ prompt: "New PixelLang project name", value: "hello-pixels", validateInput: value => /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(value) ? undefined : "Use a letter followed by letters, digits, '-' or '_' (up to 64 characters)." });
    if (!name) return;
    const result = await invoke(python(), runtime, "init", [path.join(folders[0].fsPath, name), "--name", name]);
    if (!result.passed) throw new Error(result.error);
    await vscode.commands.executeCommand("vscode.openFolder", vscode.Uri.file(result.root), { forceNewWindow: true });
    return result;
  }
  for (const [name, action] of Object.entries({ doctor, createProject, selectPython: configure })) {
    context.subscriptions.push(vscode.commands.registerCommand(`pixellang.${name}`, async () => {
      try { return await action(); }
      catch (error) { log.appendLine(error.message); log.show(true); await vscode.window.showErrorMessage(error.message); }
    }));
  }
}
module.exports = { pythonPath, invoke, register };
