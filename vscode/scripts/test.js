const fs = require("node:fs"),
  os = require("node:os"),
  path = require("node:path");
const { runTests, downloadAndUnzipVSCode } = require("@vscode/test-electron");
const testVersion = "1.137.0";
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "px-vsc-"));
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pixellang-vscode-"));
fs.writeFileSync(
  path.join(dir, "main.pxl"),
  "fn answer(n: int) -> int = identity(n) + 2\neffect fn main() {\n    let x = 40; let nested = [[10, 20]]; var rate = 1.25; rate += 0.5; let boxed = Box::[float64]{value: rate}\n    print(answer(x))\n}\nrecord Box[T] { value: T }\nfn identity[T](value: T) -> T = value\n",
);
async function main() {
  const suites = {
    integration: '../test/integration.js',
    'native-build-cancellation': '../test/native-build-cancellation-integration.js',
    'native-lazy-debug': '../test/native-lazy-debug-integration.js',
    'native-diagnostics': '../test/native-diagnostics-integration.js',
    'native-debug-log': '../test/native-debug-log-integration.js',
    'native-undo': '../test/native-undo-integration.js',
    'native-format': '../test/native-format-integration.js',
    'native-pixel-edit': '../test/native-pixel-edit-integration.js',
    application: '../test/application-integration.js',
    'native-spatial-debug': '../test/native-spatial-debug-integration.js',
    'native-recovered': '../test/native-recovered-integration.js',
    'native-computed': '../test/native-computed-integration.js',
    'native-methods': '../test/native-methods-integration.js',
    'native-flow-methods': '../test/native-flow-methods-integration.js',
    'native-match-block': '../test/native-match-block-integration.js',
    'native-specialization': '../test/native-specialization-integration.js',
    'native-retained-state': '../test/native-retained-state-integration.js',
    'native-graphs': '../test/native-graphs-integration.js',
  };
  const suite = process.env.PIXELLANG_TEST_SUITE || 'integration';
  if (!Object.hasOwn(suites, suite)) throw new Error(`Unknown Studio test suite: ${suite}`);
  let executable = process.env.PIXELLANG_VSCODE || await downloadAndUnzipVSCode({
    version: testVersion, cachePath: path.resolve(__dirname, "../.vscode-test"),
  });
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
  extensionTestsPath: path.resolve(__dirname, suites[suite]),
  extensionTestsEnv: {
    PIXELLANG_RECOVERED_SOURCE: process.env.PIXELLANG_RECOVERED_SOURCE,
    PIXELLANG_TEST_HOLD_MS: process.env.PIXELLANG_TEST_HOLD_MS || "0",
    PIXELLANG_TEST_LOGS: path.resolve(__dirname, "../../examples/log-analysis"),
    PIXELLANG_TEST_LEDGER: path.resolve(__dirname, "../../examples/ledger"),
    PIXELLANG_TEST_PYTHON: process.env.PIXELLANG_TEST_PYTHON || path.resolve(__dirname, "../../.venv/bin/python"),
  },
  launchArgs: [
    dir,
    "--disable-workspace-trust",
    "--user-data-dir",
    path.join(profile, "user"),
    "--extensions-dir",
    path.join(profile, "extensions"),
    "--skip-welcome",
    "--skip-release-notes",
  ],
  });
}
main().catch((e) => {
  console.error(e);
  process.exit(1);
});
