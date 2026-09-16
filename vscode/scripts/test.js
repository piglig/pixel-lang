const fs = require("node:fs"),
  os = require("node:os"),
  path = require("node:path");
const { runTests, downloadAndUnzipVSCode } = require("@vscode/test-electron");
const testVersion = "1.137.0";
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pixellang-vscode-"));
fs.writeFileSync(
  path.join(dir, "main.pxl"),
  "fn answer(n: int) -> int = identity(n) + 2\nfn main() {\n    let x = 40; let nested = [[10, 20]]; var rate = 1.25; rate += 0.5; let boxed = Box[float64]{value: rate}\n    print(answer(x))\n}\nrecord Box[T] { value: T }\nfn identity[T](value: T) -> T = value\n",
);
async function main() {
  let executable = process.env.PIXELLANG_VSCODE || await downloadAndUnzipVSCode(testVersion);
  // Recent macOS distributions call the binary Code; older test-electron
  // releases still resolve Electron. Inspect the actual downloaded bundle.
  if (!fs.existsSync(executable) && process.platform === "darwin") {
    const code = path.join(path.dirname(executable), "Code");
    if (fs.existsSync(code)) executable = code;
  }
  if (!fs.existsSync(executable)) throw new Error(`Missing VS Code test executable: ${executable}`);
  console.log(JSON.stringify({ vscode: testVersion, executable, workspace: dir }));
  await runTests({
  vscodeExecutablePath: executable,
  extensionDevelopmentPath: process.env.PIXELLANG_EXTENSION_PATH || path.resolve(__dirname, ".."),
  extensionTestsPath: path.resolve(__dirname, "../test/integration.js"),
  extensionTestsEnv: {
    PIXELLANG_TEST_HOLD_MS: process.env.PIXELLANG_TEST_HOLD_MS || "0",
    PIXELLANG_TEST_LOGS: path.resolve(__dirname, "../../examples/log-analysis"),
    PIXELLANG_TEST_LEDGER: path.resolve(__dirname, "../../examples/ledger"),
    PIXELLANG_TEST_PYTHON: process.env.PIXELLANG_TEST_PYTHON || path.resolve(__dirname, "../../.venv/bin/python"),
  },
  launchArgs: [
    dir,
    "--disable-workspace-trust",
    "--user-data-dir",
    path.join(dir, "user-data"),
    "--extensions-dir",
    path.join(dir, "extensions"),
    "--skip-welcome",
    "--skip-release-notes",
  ],
  });
}
main().catch((e) => {
  console.error(e);
  process.exit(1);
});
