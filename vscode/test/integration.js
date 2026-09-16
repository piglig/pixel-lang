const vscode = require("vscode"),
  assert = require("node:assert/strict");
async function undoDocument(document, expected) {
  // Undo is routed by editor focus and its document change can arrive after the
  // command promise. Issue it once, then observe the real restored text.
  await vscode.commands.executeCommand("workbench.action.focusWindow");
  const focusDeadline = Date.now() + 5000;
  while (!vscode.window.state.focused && Date.now() < focusDeadline)
    await new Promise(resolve => setTimeout(resolve, 25));
  assert(vscode.window.state.focused, "Undo acceptance requires the test window to have focus");
  await vscode.window.showTextDocument(document, { preview: false, preserveFocus: false });
  await vscode.commands.executeCommand("workbench.action.focusActiveEditorGroup");
  await vscode.commands.executeCommand("undo");
  const deadline = Date.now() + 5000;
  while (document.getText() !== expected && Date.now() < deadline)
    await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(document.getText(), expected);
}

async function run() {
  const extension = vscode.extensions.getExtension(
    "pixellang-local.pixellang-studio",
  );
  assert(extension, "Extension discovered");
  const api = await extension.activate();
  const commands = await vscode.commands.getCommands(true);
  for (const command of ["pixellang.createProject", "pixellang.doctor", "pixellang.selectPython"])
    assert(commands.includes(command), `Onboarding command registered: ${command}`);
  const root = vscode.workspace.workspaceFolders[0].uri;
  const uri = vscode.Uri.joinPath(root, "main.pxl");
  let doc = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(doc);
  await vscode.workspace
    .getConfiguration("pixellang")
    .update(
      "pythonPath",
      process.env.PIXELLANG_TEST_PYTHON,
      vscode.ConfigurationTarget.Workspace,
    );
  const definitions = await vscode.commands.executeCommand(
    "vscode.executeDefinitionProvider", uri, new vscode.Position(3, 12),
  );
  assert.equal(definitions.length, 1);
  assert.equal(definitions[0].range.start.line, 0);
  assert.equal(definitions[0].range.start.character, 3);
  const references = await vscode.commands.executeCommand(
    "vscode.executeReferenceProvider", uri, new vscode.Position(2, 8),
  );
  assert.equal(references.length, 2);
  const renamed = await vscode.commands.executeCommand(
    "vscode.executeDocumentRenameProvider", uri, new vscode.Position(2, 8), "amount",
  );
  assert.equal(renamed.get(uri).length, 2);
  assert(renamed.get(uri).every(edit => edit.newText === "amount"));
  const originalText = doc.getText();
  const partialText = 'record Row { value: int }\nfn main() {\n let row = Row{value: 1}\n print(row.value)\n let unfinished =\n row.';
  const partialEdit = new vscode.WorkspaceEdit();
  partialEdit.replace(uri, new vscode.Range(doc.positionAt(0), doc.positionAt(originalText.length)), partialText);
  assert(await vscode.workspace.applyEdit(partialEdit));
  const snapshot = await api.sourceDocument("main.pxl", { root: root.fsPath, files: { "main.pxl": originalText } });
  assert.equal(snapshot.uri.scheme, "pixellang-source");
  assert.equal(snapshot.getText(), originalText);
  const completion = await vscode.commands.executeCommand(
    "vscode.executeCompletionItemProvider", uri, doc.positionAt(partialText.length), ".",
  );
  assert(completion.items.some(item => item.label === "value" && item.detail === "int"));
  const partialDefinitions = await vscode.commands.executeCommand(
    "vscode.executeDefinitionProvider", uri, doc.positionAt(partialText.indexOf("print(row") + 6),
  );
  assert.equal(partialDefinitions.length, 1);
  assert.equal(partialDefinitions[0].range.start.line, 2);

  const genericText = 'record Box[T] { value: T }\nfn identity[T](value: T) -> T = value\nfn main() { let box = identity(Box[int]{value: 1}); print(box.) }';
  const genericEdit = new vscode.WorkspaceEdit();
  genericEdit.replace(uri, new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length)), genericText);
  assert(await vscode.workspace.applyEdit(genericEdit));
  const genericCompletion = await vscode.commands.executeCommand(
    "vscode.executeCompletionItemProvider", uri, doc.positionAt(genericText.indexOf("box.") + 4), ".",
  );
  assert(genericCompletion.items.some(item => item.label === "value" && item.detail === "int"));

  const enumText = 'enum Event[T] { Stop, Data(value: T) }\nfn main() { let event = Event[int].Data(42); print(match event { Event.Stop => 0, Event.Data(value) => value }) }';
  const enumEdit = new vscode.WorkspaceEdit();
  enumEdit.replace(uri, new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length)), enumText);
  assert(await vscode.workspace.applyEdit(enumEdit));
  const enumDefinitions = await vscode.commands.executeCommand(
    "vscode.executeDefinitionProvider", uri, doc.positionAt(enumText.indexOf("Data(42)")),
  );
  assert.equal(enumDefinitions.length, 1);
  assert.equal(enumDefinitions[0].range.start.line, 0);
  const enumRename = await vscode.commands.executeCommand(
    "vscode.executeDocumentRenameProvider", uri, doc.positionAt(enumText.indexOf("Data(42)")), "Payload",
  );
  assert.equal(enumRename.get(uri).length, 3);
  const enumCompletion = await vscode.commands.executeCommand(
    "vscode.executeCompletionItemProvider", uri, doc.positionAt(enumText.indexOf("Data(42)") + 1),
  );
  assert(enumCompletion.items.some(item => item.label === "Data" && item.detail === "Data(value: int)"));

  const closureText = 'fn main() {\n var count = 0\n let next = fn() -> int { count += 1; return count }\n print(next()); print(next())\n}';
  const closureEdit = new vscode.WorkspaceEdit();
  closureEdit.replace(uri, new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length)), closureText);
  assert(await vscode.workspace.applyEdit(closureEdit));
  const captureDefinitions = await vscode.commands.executeCommand(
    "vscode.executeDefinitionProvider", uri, doc.positionAt(closureText.indexOf("count +=")),
  );
  assert.equal(captureDefinitions.length, 1);
  assert.equal(captureDefinitions[0].range.start.line, 1);
  const captureRename = await vscode.commands.executeCommand(
    "vscode.executeDocumentRenameProvider", uri, doc.positionAt(closureText.indexOf("count +=")), "total",
  );
  assert.equal(captureRename.get(uri).length, 3);
  const closureBuilt = await api.service().call("build", { files: { "main.pxl": closureText } });
  let closureState = closureBuilt;
  for (let i = 0; i < 1000 && !closureState.state.halted; i++)
    closureState = await api.service().call("step", { session: closureBuilt.session });
  assert(closureState.state.halted);
  assert.deepEqual(closureState.state.output, [1, 2]);

  const errorText = 'fn main() { try { fail("bad") } catch error { print(error.) } }';
  const errorEdit = new vscode.WorkspaceEdit();
  errorEdit.replace(uri, new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length)), errorText);
  assert(await vscode.workspace.applyEdit(errorEdit));
  const errorCompletion = await vscode.commands.executeCommand(
    "vscode.executeCompletionItemProvider", uri, doc.positionAt(errorText.indexOf("error.") + 6), ".",
  );
  for (const field of ["kind", "code", "message", "path", "operation", "expected", "actual"])
    assert(errorCompletion.items.some(item => item.label === field && item.detail === "string"));

  const libraryText = 'import "std/stats.pxl" as stats\nfn main() { print(stats.Summary([1, 2])) }';
  const libraryEdit = new vscode.WorkspaceEdit();
  libraryEdit.replace(uri, new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length)), libraryText);
  assert(await vscode.workspace.applyEdit(libraryEdit));
  const libraryDefinitions = await vscode.commands.executeCommand(
    "vscode.executeDefinitionProvider", uri, doc.positionAt(libraryText.indexOf("Summary")),
  );
  assert.equal(libraryDefinitions.length, 1);
  assert.equal(libraryDefinitions[0].uri.scheme, "pixellang-source");
  const libraryDoc = await vscode.workspace.openTextDocument(libraryDefinitions[0].uri);
  assert(libraryDoc.getText().includes("fn Summary"));
  const libraryRefs = await vscode.commands.executeCommand(
    "vscode.executeReferenceProvider", libraryDoc.uri, libraryDefinitions[0].range.start,
  );
  assert.equal(libraryRefs.length, 2);
  const restoreEdit = new vscode.WorkspaceEdit();
  restoreEdit.replace(uri, new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length)), originalText);
  assert(await vscode.workspace.applyEdit(restoreEdit));
  const manifestDir = vscode.Uri.joinPath(root, "manifest-app");
  await vscode.workspace.fs.createDirectory(manifestDir);
  await vscode.workspace.fs.writeFile(vscode.Uri.joinPath(manifestDir, "pixel.toml"),
    Buffer.from('format = 1\n[project]\nname = "Studio test"\nentry = "entry.pxl"\n'));
  await vscode.workspace.fs.writeFile(vscode.Uri.joinPath(manifestDir, "entry.pxl"), Buffer.from('fn main() { print(42) }'));
  const moduleUri = vscode.Uri.joinPath(manifestDir, "helper.pxl");
  await vscode.workspace.fs.writeFile(moduleUri, Buffer.from('fn Helper() -> int = 1'));
  const manifestProject = await api.collect(moduleUri);
  assert.equal(manifestProject.entry, "entry.pxl");
  assert.equal(manifestProject.sourceName, "helper.pxl");
  assert(manifestProject.paths["helper.pxl"].endsWith("helper.pxl"));
  const testsDir = vscode.Uri.joinPath(manifestDir, "tests");
  await vscode.workspace.fs.createDirectory(testsDir);
  await vscode.workspace.fs.writeFile(vscode.Uri.joinPath(testsDir, "basic_test.pxl"), Buffer.from('fn main() { assert(2 + 2 == 4) }'));
  const testResult = await vscode.commands.executeCommand("pixellang.testProject", moduleUri);
  assert(testResult.passed);
  assert.equal(testResult.tests.length, 1);
  const packageResult = await vscode.commands.executeCommand("pixellang.buildProject", moduleUri);
  assert.equal(packageResult.sha256.length, 64);
  assert((await vscode.workspace.fs.stat(vscode.Uri.file(packageResult.image))).size > 0);
  const lockResult = await vscode.commands.executeCommand("pixellang.lockProject", moduleUri);
  assert.equal(lockResult.dependencies, 0);
  await vscode.workspace.fs.delete(manifestDir, { recursive: true });
  const built = await api.build(uri);
  assert.equal(built.inspection.ir, null);
  assert((await api.inspectIR()).functions);
  assert.strictEqual(await api.inspectIR(), api.getCurrent().inspection.ir);
  assert(built.image);
  assert.equal(built.cursor, 0);
  await api.run(uri);
  assert.deepEqual(api.getCurrent().output, ["42"]);
  assert(api.getCurrent().volume.voxels.length > 0);
  const restored = await api
    .service()
    .call("recover", { image: api.getCurrent().image });
  assert.match(restored.files["main.pxl"], /fn answer/);
  const formatted = await vscode.commands.executeCommand(
    "vscode.executeFormatDocumentProvider",
    uri,
    {},
  );
  assert.equal(formatted.length, 0, "Already formatted source needs no edits");
  const cancellable = await api.service().call("build", {
    files: { "main.pxl": "fn main() { while true { let i = 1 } }" },
    checkpoint_interval: 64,
  });
  await api.service().call("run", { session: cancellable.session });
  const cancelled = await api.service().call("cancel", {
    session: cancellable.session,
  });
  assert(cancelled.state.halted);
  assert.match(cancelled.state.error.message, /cancelled/);
  const recording = await api.service().call("recording", {
    session: cancellable.session,
  });
  const replayed = await api.service().call("restore", { document: recording });
  assert(replayed.state.halted);
  const past = await api.service().call("seek", { session: replayed.session, z: 10 });
  assert(!past.state.halted);
  const dataBuild = await api.service().call("build", {
    files: { "main.pxl": 'import "std/csv.pxl" as csv\nimport "std/stats.pxl" as stats\nfn main() { print(csv.Parse(input()))\nprint(stats.Summary([2, 4])) }' },
    input: 'a,"b,c"',
  });
  let dataState = dataBuild;
  while (!dataState.state.halted)
    dataState = await api.service().call("run", { session: dataBuild.session });
  assert(!dataState.state.error);
  assert.deepEqual(dataState.state.output, [[["a", "b,c"]], { count: 2, total: 6, min: 2, max: 4, mean: 3 }]);
  const recordBuild = await api.service().call("build", {
    input: '{"amount":42,"name":"cat"}',
    files: { "main.pxl": 'record Row { amount: int, name: string }\nfn main() { let value = jsonDecode[Row](input())\nprint(jsonStringify(jsonFrom(value))) }' },
  });
  let recordState = recordBuild;
  while (!recordState.state.halted)
    recordState = await api.service().call("run", { session: recordBuild.session });
  assert.deepEqual(recordState.state.output.map(JSON.parse), [{ amount: 42, name: "cat" }]);
  const numericBuild = await api.service().call("build", {
    files: { "main.pxl": 'record Row { amount: float64 }\nfn main() { let row = Row{amount: 1.25}; row.amount += 0.5; print(row.amount); try { print(jsonDecode[Row]("{}")) } catch error { print(error.code); print(error.path) } }' },
  });
  let numericState = numericBuild;
  while (!numericState.state.halted)
    numericState = await api.service().call("run", { session: numericBuild.session });
  assert.deepEqual(numericState.state.output, [1.75, "json.missing_field", "$.amount"]);
  const edit = new vscode.WorkspaceEdit();
  edit.replace(
    uri,
    new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length)),
    "fn main() { let bad: int = true }",
  );
  await vscode.workspace.applyEdit(edit);
  const until = Date.now() + 10000;
  while (Date.now() < until && !vscode.languages.getDiagnostics(uri).length)
    await new Promise((r) => setTimeout(r, 100));
  assert(
    vscode.languages
      .getDiagnostics(uri)
      .some((d) => d.message === "Type mismatch"
        && d.severity === vscode.DiagnosticSeverity.Error
        && d.range.start.line === 0 && d.range.start.character === 12
        && d.range.end.character === 31),
  );
  await vscode.window.showTextDocument(doc);
  await vscode.commands.executeCommand("workbench.action.files.revert");
  const stops = [];
  const tracker = vscode.debug.registerDebugAdapterTrackerFactory("pixellang", {
    createDebugAdapterTracker() {
      return {
        onDidSendMessage(m) {
          if (m.type === "event" && m.event === "stopped") stops.push(m);
        },
      };
    },
  });
  async function waitStop(previous) {
    const deadline = Date.now() + 10000;
    while (stops.length <= previous && Date.now() < deadline)
      await new Promise((r) => setTimeout(r, 30));
    assert(stops.length > previous, "Debugger reached stopped event");
  }
  const debugStarted = await vscode.debug.startDebugging(
    vscode.workspace.workspaceFolders[0],
    {
      type: "pixellang",
      request: "launch",
      name: "Integration",
      program: uri.fsPath,
      stopOnEntry: true,
    },
  );
  assert(debugStarted);
  const waitUntil = Date.now() + 10000;
  while (Date.now() < waitUntil && !vscode.debug.activeDebugSession)
    await new Promise((r) => setTimeout(r, 50));
  const session = vscode.debug.activeDebugSession;
  assert(session);
  await waitStop(0);
  const threads = await session.customRequest("threads");
  assert.equal(threads.threads[0].name, "PixelVM");
  const stack = await session.customRequest("stackTrace", { threadId: 1 });
  assert(stack.stackFrames.length);
  const validatedBreakpoints = await session.customRequest("setBreakpoints", {
    source: { path: uri.fsPath },
    breakpoints: [{ line: 4 }, { line: 999 }],
  });
  assert(validatedBreakpoints.breakpoints[0].verified);
  assert(!validatedBreakpoints.breakpoints[1].verified);
  let previous = stops.length;
  await session.customRequest("continue", { threadId: 1 });
  await waitStop(previous);
  let frames = await session.customRequest("stackTrace", { threadId: 1 });
  assert.equal(frames.stackFrames[0].line, 4);
  const locals = await session.customRequest("variables", {
    variablesReference: 100,
  });
  assert(locals.variables.some((v) => v.name === "x" && v.value === "40"));
  assert(locals.variables.some((v) => v.name === "rate" && v.type === "float64" && v.value === "1.75"));
  const boxed = locals.variables.find(v => v.name === "boxed");
  assert.equal(boxed.type, "Box[float64]");
  assert(boxed.variablesReference > 0);
  const boxedFields = await session.customRequest("variables", { variablesReference: boxed.variablesReference });
  assert(boxedFields.variables.some(v => v.name === "value" && v.type === "float64" && v.value === "1.75"));
  const nested = locals.variables.find(v => v.name === "nested");
  assert.equal(nested.type, "[[int]]");
  assert(nested.variablesReference > 0);
  const outer = await session.customRequest("variables", { variablesReference: nested.variablesReference });
  assert(outer.variables[0].variablesReference > 0);
  const inner = await session.customRequest("variables", {
    variablesReference: outer.variables[0].variablesReference, start: 1, count: 1,
  });
  assert.deepEqual(inner.variables.map(v => v.value), ["20"]);
  const genericBreakpoints = await session.customRequest("setBreakpoints", {
    source: { path: uri.fsPath }, breakpoints: [{ line: 7 }],
  });
  assert(genericBreakpoints.breakpoints[0].verified);
  previous = stops.length;
  await session.customRequest("continue", { threadId: 1 });
  await waitStop(previous);
  const genericFrames = await session.customRequest("stackTrace", { threadId: 1 });
  assert.equal(genericFrames.stackFrames[0].name, "identity[int]");
  const genericLocals = await session.customRequest("variables", { variablesReference: 100 });
  assert(genericLocals.variables.some(v => v.name === "value" && v.value === "40"));
  previous = stops.length;
  await session.customRequest("continue", { threadId: 1 });
  await waitStop(previous);
  await assert.rejects(session.customRequest("variables", { variablesReference: nested.variablesReference }));
  assert.deepEqual(api.getCurrent().output, ["42"]);
  assert(api.getCurrent().state.halted);
  previous = stops.length;
  await session.customRequest("stepBack", { threadId: 1 });
  await waitStop(previous);
  assert(!api.getCurrent().state.halted);
  const fs = require("node:fs"), path = require("node:path");
  const ledgerRoot = path.join(root.fsPath, "ledger-acceptance");
  fs.cpSync(process.env.PIXELLANG_TEST_LEDGER, ledgerRoot, { recursive: true });
  const ledgerUri = vscode.Uri.file(path.join(ledgerRoot, "main.pxl"));
  const configUri = vscode.Uri.file(path.join(ledgerRoot, "config.pxl"));
  const configDoc = await vscode.workspace.openTextDocument(configUri);
  const modelsUri = vscode.Uri.file(path.join(ledgerRoot, "models.pxl"));
  const modelsDoc = await vscode.workspace.openTextDocument(modelsUri);
  await vscode.window.showTextDocument(modelsDoc);
  const originalModels = modelsDoc.getText();
  const brokenModels = originalModels.replace("maximum: Option[int]", "maximum: Option[string]");
  assert.notEqual(brokenModels, originalModels);
  const modelChange = new vscode.WorkspaceEdit();
  modelChange.replace(modelsUri, new vscode.Range(modelsDoc.positionAt(0), modelsDoc.positionAt(originalModels.length)), brokenModels);
  assert(await vscode.workspace.applyEdit(modelChange));
  const modelDeadline = Date.now() + 10000;
  while (Date.now() < modelDeadline && !vscode.languages.getDiagnostics(configUri).some(d => d.message === "Type shapes do not match at field.maximum.0" && d.severity === vscode.DiagnosticSeverity.Error && configDoc.getText(d.range).includes("maximum: requested.maximum_minor")))
    await new Promise(resolve => setTimeout(resolve, 100));
  assert(vscode.languages.getDiagnostics(configUri).some(d => d.message === "Type shapes do not match at field.maximum.0" && d.severity === vscode.DiagnosticSeverity.Error && configDoc.getText(d.range).includes("maximum: requested.maximum_minor")));
  await undoDocument(modelsDoc, originalModels);
  const beforeRenameConfig = configDoc.getText();
  const fieldRename = await vscode.commands.executeCommand("vscode.executeDocumentRenameProvider", modelsUri,
    modelsDoc.positionAt(originalModels.indexOf("maximum:")), "ceiling");
  assert(fieldRename.get(modelsUri).length > 0);
  assert(fieldRename.get(configUri).length > 0);
  assert(await vscode.workspace.applyEdit(fieldRename));
  assert(modelsDoc.getText().includes("ceiling: Option[int]"));
  assert(configDoc.getText().includes("config.ceiling"));
  await api.service().call("check", await api.collect(configUri));
  await undoDocument(modelsDoc, originalModels);
  assert.equal(configDoc.getText(), beforeRenameConfig);
  await vscode.window.showTextDocument(configDoc);
  const configBeforeSpatial = configDoc.getText();
  let spatial = await api.refreshSpatial("config.pxl", configUri);
  assert(spatial.regions.length > 0);
  let literalTarget = Object.values(spatial.targets).find(t => t.kind === "literal" && t.text === "0");
  assert(literalTarget);
  assert(spatial.regions.some(r => r.tokens.some(t => t.targets.includes(literalTarget.id))));
  await assert.rejects(api.applySpatial({ revision: spatial.revision, target: literalTarget.id,
    action: "literal", value: '"invalid amount"' }));
  assert.equal(configDoc.getText(), configBeforeSpatial);
  await api.applySpatial({ revision: spatial.revision, target: literalTarget.id, action: "literal", value: "1" });
  assert.notEqual(configDoc.getText(), configBeforeSpatial);
  await undoDocument(configDoc, configBeforeSpatial);
  spatial = await api.refreshSpatial("config.pxl", configUri);
  const operatorTarget = Object.values(spatial.targets).find(t => t.kind === "binary" && t.text === "sale.amount < config.minimum");
  assert(operatorTarget);
  await api.applySpatial({ revision: spatial.revision, target: operatorTarget.id, action: "operator", value: "<=" });
  assert(configDoc.getText().includes("(sale.amount) <= (config.minimum)"));
  await undoDocument(configDoc, configBeforeSpatial);
  spatial = await api.refreshSpatial("config.pxl", configUri);
  const moveTarget = Object.values(spatial.targets).find(t => t.kind === "let" && t.text.trim().startsWith("let minimum ="));
  assert(moveTarget?.actions.includes("up"));
  await api.applySpatial({ revision: spatial.revision, target: moveTarget.id, action: "up" });
  assert(configDoc.getText().indexOf("let minimum") < configDoc.getText().indexOf("let categories"));
  await undoDocument(configDoc, configBeforeSpatial);
  await vscode.commands.executeCommand("workbench.action.focusSecondEditorGroup");
  const spatialPanelProject = await api.collect();
  assert.equal(spatialPanelProject.root, fs.realpathSync(ledgerRoot));
  const spatialPanelBuild = await api.build();
  assert(spatialPanelBuild.source_project.files["config.pxl"].includes("maximum_minor"));
  await vscode.window.showTextDocument(configDoc);
  const ledgerTests = await vscode.commands.executeCommand("pixellang.testProject", ledgerUri);
  assert(ledgerTests.passed);
  assert.equal(ledgerTests.tests.length, 4);
  const ledgerPackage = await vscode.commands.executeCommand("pixellang.buildProject", ledgerUri);
  assert(ledgerPackage.sha256.length === 64);
  const ledgerProject = await api.collect(ledgerUri);
  const fixtureDir = path.join(ledgerRoot, "fixtures");
  const outputDir = path.join(ledgerRoot, "output");
  fs.mkdirSync(outputDir, { recursive: true });
  const ledgerBuild = await api.service().call("build", { ...ledgerProject,
    input: fs.readFileSync(path.join(fixtureDir, "config.json"), "utf8"),
    read_root: fixtureDir, write_root: outputDir,
  });
  let ledgerState = ledgerBuild;
  while (!ledgerState.state.halted)
    ledgerState = await api.service().call("run", { session: ledgerBuild.session });
  assert(!ledgerState.state.error);
  const expectedLedger = JSON.parse(fs.readFileSync(path.join(fixtureDir, "expected.json"), "utf8"));
  assert.deepEqual(ledgerState.state.output, expectedLedger);
  const ledgerRecording = await api.service().call("recording", { session: ledgerBuild.session });
  fs.writeFileSync(path.join(outputDir, "report.json"), "unchanged during replay");
  const ledgerRestored = await api.service().call("restore", { document: ledgerRecording });
  assert.deepEqual(ledgerRestored.state.output, expectedLedger);
  assert.equal(fs.readFileSync(path.join(outputDir, "report.json"), "utf8"), "unchanged during replay");
  const historical = await api.service().call("seek", { session: ledgerRestored.session, z: 100 });
  assert.equal(historical.cursor, 100);
  assert(!historical.state.halted);
  const logsRoot = path.join(root.fsPath, "log-analysis-acceptance");
  fs.cpSync(process.env.PIXELLANG_TEST_LOGS, logsRoot, { recursive: true });
  const logsUri = vscode.Uri.file(path.join(logsRoot, "main.pxl"));
  const logsTests = await vscode.commands.executeCommand("pixellang.testProject", logsUri);
  assert(logsTests.passed);
  assert.equal(logsTests.tests.length, 3);
  const logsPackage = await vscode.commands.executeCommand("pixellang.buildProject", logsUri);
  assert.equal(logsPackage.sha256.length, 64);
  await vscode.debug.stopDebugging(session);
  await vscode.workspace.getConfiguration("pixellang").update("readRoot", fixtureDir, vscode.ConfigurationTarget.Workspace);
  await vscode.workspace.getConfiguration("pixellang").update("writeRoot", outputDir, vscode.ConfigurationTarget.Workspace);
  previous = stops.length;
  assert(await vscode.debug.startDebugging(vscode.workspace.workspaceFolders[0], {
    type: "pixellang", request: "launch", name: "Ledger multi-file acceptance",
    program: ledgerUri.fsPath, stopOnEntry: true,
    input: fs.readFileSync(path.join(fixtureDir, "bounded-config.json"), "utf8"),
  }));
  await waitStop(previous);
  const ledgerDebug = vscode.debug.activeDebugSession;
  assert(ledgerDebug && ledgerDebug.id !== session.id);
  const predicateLine = configDoc.positionAt(configDoc.getText().indexOf("if sale.amount <")).line + 1;
  const ledgerBreaks = await ledgerDebug.customRequest("setBreakpoints", {
    source: { path: configUri.fsPath }, breakpoints: [{ line: predicateLine }],
  });
  assert(ledgerBreaks.breakpoints[0].verified);
  previous = stops.length;
  await ledgerDebug.customRequest("continue", { threadId: 1 });
  await waitStop(previous);
  const ledgerFrames = await ledgerDebug.customRequest("stackTrace", { threadId: 1 });
  assert.equal(ledgerFrames.stackFrames[0].line, predicateLine);
  assert.equal(fs.realpathSync(ledgerFrames.stackFrames[0].source.path), fs.realpathSync(configUri.fsPath));
  assert(ledgerFrames.stackFrames.some(f => path.basename(f.source.path) === "main.pxl"));
  const ledgerVariables = await ledgerDebug.customRequest("variables", { variablesReference: 100 });
  assert(ledgerVariables.variables.some(v => v.name === "sale" && v.type === "models.Sale"));
  assert(ledgerVariables.variables.some(v => v.name === "config" && v.type === "models.Config"));
  const ledgerZ = api.getCurrent().cursor;
  previous = stops.length;
  await ledgerDebug.customRequest("stepBack", { threadId: 1 });
  await waitStop(previous);
  assert(api.getCurrent().cursor < ledgerZ);
  await ledgerDebug.customRequest("setBreakpoints", { source: { path: configUri.fsPath }, breakpoints: [] });
  previous = stops.length;
  await ledgerDebug.customRequest("continue", { threadId: 1 });
  await waitStop(previous);
  assert(api.getCurrent().state.halted && !api.getCurrent().state.error);
  assert.deepEqual(api.getCurrent().state.output,
    JSON.parse(fs.readFileSync(path.join(fixtureDir, "bounded-expected.json"), "utf8")));
  await vscode.debug.stopDebugging(ledgerDebug);
  const logsModelsUri = vscode.Uri.file(path.join(logsRoot, "models.pxl"));
  const analyzeUri = vscode.Uri.file(path.join(logsRoot, "analyze.pxl"));
  const logsModelsDoc = await vscode.workspace.openTextDocument(logsModelsUri);
  const analyzeDoc = await vscode.workspace.openTextDocument(analyzeUri);
  await vscode.window.showTextDocument(logsModelsDoc);
  const logsOriginal = logsModelsDoc.getText();
  const wrongSeverity = new vscode.WorkspaceEdit();
  wrongSeverity.replace(logsModelsUri, new vscode.Range(logsModelsDoc.positionAt(0), logsModelsDoc.positionAt(logsOriginal.length)),
    logsOriginal.replace("minimum_level: Option[string]", "minimum_level: Option[int]"));
  assert(await vscode.workspace.applyEdit(wrongSeverity));
  const logsDiagnosticDeadline = Date.now() + 10000;
  while (Date.now() < logsDiagnosticDeadline && !vscode.languages.getDiagnostics(analyzeUri).some(d => d.message === "Type shapes do not match at argument.0.0" && d.severity === vscode.DiagnosticSeverity.Error && analyzeDoc.getText(d.range) === "severity.Minimum(config.minimum_level)"))
    await new Promise(resolve => setTimeout(resolve, 100));
  assert(vscode.languages.getDiagnostics(analyzeUri).some(d => d.message === "Type shapes do not match at argument.0.0" && d.severity === vscode.DiagnosticSeverity.Error && analyzeDoc.getText(d.range) === "severity.Minimum(config.minimum_level)"));
  await undoDocument(logsModelsDoc, logsOriginal);
  const logsRename = await vscode.commands.executeCommand("vscode.executeDocumentRenameProvider", logsModelsUri,
    logsModelsDoc.positionAt(logsOriginal.indexOf("minimum_level:")), "threshold");
  assert(logsRename.get(logsModelsUri).length && logsRename.get(analyzeUri).length);
  assert(await vscode.workspace.applyEdit(logsRename));
  assert(analyzeDoc.getText().includes("config.threshold"));
  await api.service().call("check", await api.collect(analyzeUri));
  await undoDocument(logsModelsDoc, logsOriginal);
  assert(analyzeDoc.getText().includes("config.minimum_level"));
  const analyzeEditor = await vscode.window.showTextDocument(analyzeDoc);
  const analyzeOriginal = analyzeDoc.getText();
  const predicateOffset = analyzeOriginal.indexOf("rank < minimum");
  analyzeEditor.selection = new vscode.Selection(analyzeDoc.positionAt(predicateOffset), analyzeDoc.positionAt(predicateOffset + "rank < minimum".length));
  const logsSpatial = await api.refreshSpatial("analyze.pxl", analyzeUri);
  assert.deepEqual(logsSpatial.selection, { offset: predicateOffset, end: predicateOffset + "rank < minimum".length });
  const severityTarget = Object.values(logsSpatial.targets).find(t => t.kind === "binary" && t.text === "rank < minimum");
  assert(severityTarget);
  await api.applySpatial({ revision: logsSpatial.revision, target: severityTarget.id, action: "operator", value: "<=" });
  assert(analyzeDoc.getText().includes("(rank) <= (minimum)"));
  await vscode.window.showTextDocument(analyzeDoc);
  await undoDocument(analyzeDoc, analyzeOriginal);
  const logsFixtureDir = path.join(logsRoot, "fixtures"), logsOutputDir = path.join(logsRoot, "output");
  fs.mkdirSync(logsOutputDir, { recursive: true });
  await vscode.workspace.getConfiguration("pixellang").update("readRoot", logsFixtureDir, vscode.ConfigurationTarget.Workspace);
  await vscode.workspace.getConfiguration("pixellang").update("writeRoot", logsOutputDir, vscode.ConfigurationTarget.Workspace);
  previous = stops.length;
  assert(await vscode.debug.startDebugging(vscode.workspace.workspaceFolders[0], {
    type: "pixellang", request: "launch", name: "Log severity acceptance", program: logsUri.fsPath,
    stopOnEntry: true, input: fs.readFileSync(path.join(logsFixtureDir, "severity-config.json"), "utf8"),
  }));
  await waitStop(previous);
  const logsDebug = vscode.debug.activeDebugSession;
  const severityLine = analyzeDoc.positionAt(analyzeOriginal.indexOf("if rank < minimum")).line + 1;
  assert((await logsDebug.customRequest("setBreakpoints", { source: { path: analyzeUri.fsPath },
    breakpoints: [{ line: severityLine }] })).breakpoints[0].verified);
  previous = stops.length;
  await logsDebug.customRequest("continue", { threadId: 1 });
  await waitStop(previous);
  const logsFrames = await logsDebug.customRequest("stackTrace", { threadId: 1 });
  assert.equal(logsFrames.stackFrames[0].line, severityLine);
  assert.equal(fs.realpathSync(logsFrames.stackFrames[0].source.path), fs.realpathSync(analyzeUri.fsPath));
  const logsVars = await logsDebug.customRequest("variables", { variablesReference: 100 });
  assert(logsVars.variables.some(v => v.name === "minimum" && v.value === "1"));
  assert(logsVars.variables.some(v => v.name === "event" && v.type === "models.Event"));
  const logsZ = api.getCurrent().cursor;
  previous = stops.length;
  await logsDebug.customRequest("stepBack", { threadId: 1 });
  await waitStop(previous);
  assert(api.getCurrent().cursor < logsZ);
  await logsDebug.customRequest("setBreakpoints", { source: { path: analyzeUri.fsPath }, breakpoints: [] });
  previous = stops.length;
  await logsDebug.customRequest("continue", { threadId: 1 });
  await waitStop(previous);
  const severityExpected = JSON.parse(fs.readFileSync(path.join(logsFixtureDir, "severity-expected.json"), "utf8"));
  assert.deepEqual(api.getCurrent().state.output, severityExpected);
  assert(!api.getCurrent().state.error);
  const logsRecording = await api.service().call("recording", { session: api.getCurrent().session });
  fs.writeFileSync(path.join(logsOutputDir, "report.json"), "do not rewrite");
  const logsRestored = await api.service().call("restore", { document: logsRecording });
  assert.deepEqual(logsRestored.state.output, severityExpected);
  assert.equal(fs.readFileSync(path.join(logsOutputDir, "report.json"), "utf8"), "do not rewrite");
  await vscode.debug.stopDebugging(logsDebug);
  tracker.dispose();
  if (Number(process.env.PIXELLANG_TEST_HOLD_MS) > 0) {
    await vscode.workspace.getConfiguration("pixellang").update("readRoot", "fixtures", vscode.ConfigurationTarget.Workspace);
    await vscode.workspace.getConfiguration("pixellang").update("writeRoot", "output", vscode.ConfigurationTarget.Workspace);
    await vscode.window.showTextDocument(analyzeDoc);
    await api.refreshSpatial("analyze.pxl", analyzeUri);
    console.log("READY: isolated log severity UI inspection");
    await new Promise(resolve => setTimeout(resolve, Number(process.env.PIXELLANG_TEST_HOLD_MS)));
  }
  console.log(
    "PASS: real VS Code activation, build/run, image recovery, formatter, live diagnostics and debug adapter",
  );
}
module.exports = { run };
