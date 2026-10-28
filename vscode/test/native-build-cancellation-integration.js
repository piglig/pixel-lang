const vscode = require('vscode');
const assert = require('node:assert/strict');

exports.run = async () => {
  const api = await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
  await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
  const folder = vscode.workspace.workspaceFolders[0].uri;
  const uri = vscode.Uri.joinPath(folder, 'main.pxl');
  await vscode.workspace.fs.writeFile(vscode.Uri.joinPath(folder, 'pixel.toml'), Buffer.from('format = 1\n[project]\nname = "cancel-build"\nentry = "main.pxl"\n'));
  const document = await vscode.workspace.openTextDocument(uri);
  const editor = await vscode.window.showTextDocument(document);
  async function replace(value) {
    assert(await editor.edit(edit => edit.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), `effect fn main(){print(${value})}`)));
  }
  await replace(41);
  const bridge = api.service(), original = bridge.call.bind(bridge);
  // Delay real transport calls at known boundaries; edits must cancel the
  // corresponding build without publishing its now-obsolete result.
  for (const method of ['workspace', 'build']) {
    let entered, release;
    const ready = new Promise(resolve => { entered = resolve; });
    const gate = new Promise(resolve => { release = resolve; });
    let intercepted = false;
    bridge.call = async (name, ...args) => {
      if (name === method && !intercepted) {
        intercepted = true; entered(); await gate;
      }
      return original(name, ...args);
    };
    try {
      const building = api.build(uri);
      const rejected = assert.rejects(building, /cancelled|source changed/i);
      await Promise.race([ready, new Promise((_, reject) => {
        const timer = setTimeout(() => reject(new Error(`Build never entered ${method}`)), 10000);
        ready.finally(() => clearTimeout(timer));
      })]);
      await replace(method === 'workspace' ? 42 : 43);
      // A sent build completes cancellation after its service acknowledges it.
      release();
      await rejected;
      assert.equal(api.getCurrent(), undefined);
    } finally {
      release();
      bridge.call = original;
    }
  }
  const built = await api.build(uri);
  const result = await bridge.call('run', {session: built.session});
  assert.deepEqual(result.output, ['43']);
  const repeated = await api.build(uri);
  assert.equal(repeated.buildCacheHit, true);
  assert.notEqual(repeated.session, built.session);
  assert.equal(repeated.cursor, 0);
  assert.deepEqual(repeated.output, []);
  assert.deepEqual((await bridge.call('run', {session: repeated.session})).output, ['43']);
  console.log('PASS: source edits cancel builds during project collection and compiler waiting; the next build uses the latest source.');
};
