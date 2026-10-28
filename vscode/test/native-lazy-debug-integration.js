const vscode = require('vscode'), assert = require('node:assert/strict');
async function run() {
  const api = await vscode.extensions.getExtension('pixellang-local.pixellang-studio').activate();
  await vscode.workspace.getConfiguration('pixellang').update('pythonPath', process.env.PIXELLANG_TEST_PYTHON, vscode.ConfigurationTarget.Workspace);
  const uri = vscode.Uri.joinPath(vscode.workspace.workspaceFolders[0].uri, 'main.pxl');
  const document = await vscode.workspace.openTextDocument(uri);
  const editor = await vscode.window.showTextDocument(document);
  async function replace(value) {
    assert(await editor.edit(edit => edit.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), `effect fn main() { print(${value}) }`)));
  }
  await replace(42);
  const built = await api.build(uri);
  assert.equal(built.image, undefined);
  assert.deepEqual(built.inspection, {ir: null});
  assert(Object.values(built.timings).every(value => value >= 0));
  assert.equal(built.projection_ready, false);
  const artifact = await api.service().call('inspect-artifact', {session: built.session});
  assert(Object.keys(artifact.bytecode.functions).length > 0);
  assert(Object.keys(artifact.debug.functions).length > 0);
  const bridge = api.service(), original = bridge.call.bind(bridge), counts = {};
  bridge.call = (method, ...args) => { counts[method] = (counts[method] || 0) + 1; return original(method, ...args); };
  try {
    editor.selection = new vscode.Selection(0, 1, 0, 2);
    await new Promise(resolve => setTimeout(resolve, 400));
    assert.equal(counts['spatial-inspect'] || 0, 0, 'Programmatic selection in Debug must not compile a spatial view');
    const manifest = vscode.Uri.joinPath(vscode.workspace.workspaceFolders[0].uri, 'pixel.toml');
    await vscode.workspace.fs.writeFile(manifest, Buffer.from('format = 1\n[project]\nname = "lazy-debug"\nentry = "main.pxl"\n'));
    const readsBefore = counts.workspace || 0;
    const snapshots = await Promise.all(Array.from({length:16}, () => api.collect(uri)));
    assert.equal((counts.workspace || 0) - readsBefore, 1);
    assert(snapshots.every(snapshot => snapshot.files['main.pxl'] === document.getText()));
    const run = await bridge.call('run', {session: built.session});
    assert.deepEqual(run.output, ['42']);
    const before = await bridge.call('recording', {session: built.session});
    const projected = await Promise.all([api.inspectProjection(), api.inspectProjection()]);
    assert(projected[0].volume.voxels.length > 0);
    assert.equal(counts['inspect-projection'], 1);
    assert.deepEqual(await bridge.call('recording', {session: built.session}), before);
    const images = await Promise.all([api.inspectImage(), api.inspectImage()]);
    assert.equal(images[0], images[1]);
    assert.equal(counts['inspect-image'], 1);
    assert.equal(await api.inspectImage(), images[0]);
    assert.equal(counts['inspect-image'], 1);
    const recovered = await bridge.call('recover', {image: images[0]});
    assert.match(recovered.files['main.pxl'], /42/);
    const restored = await bridge.call('restore', {document: before});
    assert.equal(restored.projection_ready, false);
    assert.equal(restored.image, undefined);
    assert.deepEqual(restored.output, ['42']);
    await replace(7);
    const next = await api.build(uri);
    assert.notEqual(next.session, built.session);
    assert.equal(next.projection_ready, false);
    assert.equal(next.image, undefined);
    const nextImage = await api.inspectImage();
    assert.notEqual(nextImage, images[0]);
    assert.match((await bridge.call('recover', {image: nextImage})).files['main.pxl'], /7/);
  } finally { bridge.call = original; }
  console.log('PASS: real VS Code text-only build, on-demand projection/PNG, coalesced requests, replay identity and new-session image isolation.');
}
module.exports = {run};
