const vscode = require("vscode"),
  path = require("node:path"),
  fs = require("node:fs"),
  crypto = require("node:crypto");
const { Bridge } = require("./bridge");
const { PixelDebug } = require("./debug");
const onboarding = require("./onboarding");
let api;
function activate(context) {
  const log = vscode.window.createOutputChannel("PixelLang");
  onboarding.register(vscode, context, log);
  const diagnostics = vscode.languages.createDiagnosticCollection("pixellang");
  let bridge,
    panel,
    current,
    project,
    running = false,
    runEpoch = 0,
    buildAbort,
    irPending,
    input = context.workspaceState.get("input", "");
  let spatialState, spatialProject, spatialEpoch = 0, spatialTimer, spatialPending;
  const realPath = filename => fs.existsSync(filename) ? fs.realpathSync(filename) : path.resolve(filename);
  function fileUri(filename) {
    const canonical = realPath(filename);
    const opened = vscode.workspace.textDocuments.find(doc => doc.uri.scheme === "file" && realPath(doc.uri.fsPath) === canonical);
    if (opened) return opened.uri;
    for (const folder of vscode.workspace.workspaceFolders || []) {
      const relative = path.relative(realPath(folder.uri.fsPath), canonical);
      if (relative !== ".." && !relative.startsWith(".." + path.sep) && !path.isAbsolute(relative))
        return vscode.Uri.file(path.join(folder.uri.fsPath, relative));
    }
    return vscode.Uri.file(filename);
  }
  const changedSources = (a, b) => [...new Set([...Object.keys(a || {}), ...Object.keys(b || {})])].filter(name => a?.[name] !== b?.[name]);
  const status = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    10,
  );
  status.text = "$(symbol-color) PixelLang Studio";
  status.command = "pixellang.studio";
  status.show();
  function service() {
    if (!vscode.workspace.isTrusted)
      throw new Error("Trust this workspace before running PixelLang.");
    if (!bridge) {
      const config = vscode.workspace.getConfiguration("pixellang"),
        root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      const python = onboarding.pythonPath(config.get("pythonPath"), root);
      bridge = new Bridge(
        python,
        path.join(context.extensionPath, "runtime"),
        log,
      );
      context.subscriptions.push(bridge);
    }
    return bridge;
  }
  context.subscriptions.push(vscode.workspace.onDidChangeConfiguration(event => {
    if (event.affectsConfiguration("pixellang.pythonPath")) {
      bridge?.dispose();
      bridge = undefined;
      current = undefined;
    }
  }));
  async function collect(uri) {
    if (uri?.scheme === "pixellang-source") {
      const context = sourceContexts.get(uri.toString());
      if (!context) throw new Error("Source snapshot context expired.");
      return { ...context.project, sourceName: context.filename };
    }
    const active = vscode.window.activeTextEditor?.document;
    const authoringProject = spatialProject || project;
    const selected =
      uri ||
      (active?.languageId === "pixellang" ? active.uri : undefined) ||
      (authoringProject && fileUri(authoringProject.paths?.[authoringProject.entry] || path.join(authoringProject.root, authoringProject.entry)));
    const folder =
      (selected && vscode.workspace.getWorkspaceFolder(selected)) ||
      vscode.workspace.workspaceFolders?.[0];
    if (!selected && !folder)
      throw new Error("Open a folder or a .pxl file first.");
    const root = realPath(folder?.uri.fsPath || path.dirname(selected.fsPath));
    const mountedSource = selected && Object.values(project?.paths || {}).includes(
      fs.existsSync(selected.fsPath) ? fs.realpathSync(selected.fsPath) : selected.fsPath,
    );
    let manifestRoot = mountedSource ? realPath(project.root) : selected ? path.dirname(realPath(selected.fsPath)) : root;
    while (manifestRoot === root || !path.relative(root, manifestRoot).startsWith("..")) {
      if (fs.existsSync(path.join(manifestRoot, "pixel.toml"))) {
        const loaded = await service().call("workspace", { root: manifestRoot });
        const files = { ...loaded.files };
        let lockError = loaded.lock_error;
        for (const filename of loaded.manifests || []) {
          const manifest = await vscode.workspace.openTextDocument(fileUri(filename));
          if (manifest.isDirty) lockError = "Project configuration has unsaved changes; save pixel.toml before building.";
        }
        for (const [name, filename] of Object.entries(loaded.paths)) {
          const document = await vscode.workspace.openTextDocument(fileUri(filename));
          files[name] = document.getText();
          if (name.startsWith("deps/") && files[name] !== loaded.files[name])
            lockError = "Dependency has unsaved changes; save, review and run pixel lock before building.";
        }
        const settings = vscode.workspace.getConfiguration("pixellang", folder?.uri);
        return { ...loaded, files, lock_error: lockError,
          sourceName: Object.keys(loaded.paths).find(name => loaded.paths[name] === (selected && fs.existsSync(selected.fsPath) ? fs.realpathSync(selected.fsPath) : selected?.fsPath)),
          read_root: settings.get("readRoot") ? path.resolve(loaded.root, settings.get("readRoot")) : undefined,
          write_root: settings.get("writeRoot") ? path.resolve(loaded.root, settings.get("writeRoot")) : undefined,
          max_steps: settings.get("maxSteps", 100000),
        };
      }
      const parent = path.dirname(manifestRoot);
      if (parent === manifestRoot || manifestRoot === root) break;
      manifestRoot = parent;
    }
    const configured = vscode.workspace
      .getConfiguration("pixellang")
      .get("entry");
    const target =
      uri ||
      (configured ? vscode.Uri.file(path.resolve(root, configured)) : selected);
    if (!target || path.extname(target.fsPath) !== ".pxl")
      throw new Error("Select a .pxl entry file or set pixellang.entry.");
    const files = {};
    const found = await vscode.workspace.findFiles(
      new vscode.RelativePattern(root, "**/*.pxl"),
      "**/{node_modules,.venv,.git,runtime}/**",
      129,
    );
    if (found.length > 128)
      throw new Error(
        "Project exceeds 128 source files. Open a smaller project folder.",
      );
    for (const file of found) {
      const doc = await vscode.workspace.openTextDocument(fileUri(file.fsPath));
      files[path.relative(root, realPath(file.fsPath)).split(path.sep).join("/")] =
        doc.getText();
    }
    const entry = path.relative(root, realPath(target.fsPath)).split(path.sep).join("/");
    files[entry] = (await vscode.workspace.openTextDocument(target)).getText();
    const settings = vscode.workspace.getConfiguration("pixellang", folder?.uri);
    return {
      root, entry, files, sourceName: selected ? path.relative(root, realPath(selected.fsPath)).split(path.sep).join("/") : entry,
      read_root: settings.get("readRoot") ? path.resolve(root, settings.get("readRoot")) : undefined,
      write_root: settings.get("writeRoot") ? path.resolve(root, settings.get("writeRoot")) : undefined,
      max_steps: settings.get("maxSteps", 100000),
    };
  }
  function textSpan(error) {
    const s = error.diagnostic?.span;
    return s?.text || (s?.kind === "text" ? s : null);
  }
  function position(doc, p) {
    const line = Math.max(0, Math.min(doc.lineCount - 1, p.line - 1));
    const content = doc.lineAt(line).text;
    return new vscode.Position(
      line,
      Array.from(content)
        .slice(0, p.column - 1)
        .join("").length,
    );
  }
  async function report(error, proj, quiet = false) {
    log.appendLine(error.message);
    const span = textSpan(error);
    if (span && proj) {
      const uri = fileUri(proj.paths?.[span.source] || path.resolve(proj.root, span.source));
      try {
        const doc = await vscode.workspace.openTextDocument(uri);
        diagnostics.set(uri, [
          new vscode.Diagnostic(
            new vscode.Range(position(doc, span.start), position(doc, span.end)),
            error.message,
            vscode.DiagnosticSeverity.Error,
          ),
        ]);
      } catch {
        log.appendLine(`Source is not available on disk: ${span.source}`);
      }
    }
    if (!quiet) vscode.window.showErrorMessage(error.message);
    panel?.webview.postMessage({ type: "error", message: error.message });
  }
  const sourceSnapshots = new Map();
  const sourceContexts = new Map();
  context.subscriptions.push(vscode.workspace.registerTextDocumentContentProvider("pixellang-source", {
    provideTextDocumentContent(uri) { return sourceSnapshots.get(uri.toString()) || ""; },
  }));
  async function sourceDocument(filename, proj = project) {
    if (proj?.sourceUnavailable) throw new Error("This older recording has no exact text snapshot; use its spatial view.");
    const expected = proj?.files?.[filename];
    if (expected === undefined) throw new Error("Source text is unavailable for this recording.");
    const file = fileUri(proj.paths?.[filename] || path.resolve(proj.root, filename));
    try {
      const doc = await vscode.workspace.openTextDocument(file);
      if (doc.getText() === expected) return doc;
    } catch (_) {}
    const digest = crypto.createHash("sha256").update(JSON.stringify([proj.root, proj.entry, proj.files])).digest("hex");
    const uri = vscode.Uri.from({ scheme: "pixellang-source", authority: digest, path: "/" + filename });
    sourceSnapshots.set(uri.toString(), expected);
    sourceContexts.set(uri.toString(), { filename, project: { ...proj, files: { ...proj.files } } });
    return vscode.workspace.openTextDocument(uri);
  }
  async function reveal(span, proj = project) {
    if (!span || !proj) return;
    const doc = await sourceDocument(span.source, proj);
    const editor = await vscode.window.showTextDocument(doc, {
      viewColumn: vscode.ViewColumn.One,
      preserveFocus: true,
    });
    const range = new vscode.Range(
      position(doc, span.start),
      position(doc, span.end),
    );
    editor.selection = new vscode.Selection(range.start, range.end);
    editor.revealRange(
      range,
      vscode.TextEditorRevealType.InCenterIfOutsideViewport,
    );
  }
  async function refreshSpatial(source, uri) {
    const proj = await collect(uri);
    source ||= proj.sourceName || proj.entry;
    const key = JSON.stringify([proj.root, proj.entry, source, Object.entries(proj.files).sort(([a], [b]) => a.localeCompare(b))]);
    if (spatialPending?.key === key) return spatialPending.promise;
    const epoch = ++spatialEpoch;
    const promise = (async () => {
      const result = await service().call("spatial-inspect", { ...proj, source });
      if (epoch === spatialEpoch) {
        const sourcePath = path.resolve(proj.root, source);
        const editor = [vscode.window.activeTextEditor, ...vscode.window.visibleTextEditors]
          .find(editor => editor && realPath(editor.document.uri.fsPath) === realPath(sourcePath));
        if (editor) {
          const text = editor.document.getText();
          result.selection = {
            offset: Array.from(text.slice(0, editor.document.offsetAt(editor.selection.start))).length,
            end: Array.from(text.slice(0, editor.document.offsetAt(editor.selection.end))).length,
          };
        }
        spatialState = result;
        spatialProject = proj;
        panel?.webview.postMessage({ type: "spatial-state", data: result });
      }
      return result;
    })();
    spatialPending = { key, promise };
    try { return await promise; }
    finally { if (spatialPending?.promise === promise) spatialPending = undefined; }
  }
  async function applySpatial(message) {
    if (!spatialState || !spatialProject || message.revision !== spatialState.revision)
      throw new Error("Refresh the spatial view before editing.");
    const snapshot = spatialProject;
    const result = await service().call("spatial-edit", { ...snapshot, ...message });
    const uri = fileUri(snapshot.paths?.[result.source] || path.resolve(snapshot.root, result.source));
    const live = await collect(uri);
    const changed = changedSources(live.files, snapshot.files);
    if (changed.length)
      throw new Error(`Source changed during validation (${changed.join(", ")}); refresh the spatial view.`);
    const doc = await vscode.workspace.openTextDocument(uri);
    if (doc.getText() !== snapshot.files[result.source])
      throw new Error("Source changed during validation; refresh the spatial view.");
    const offset = n => Array.from(doc.getText()).slice(0, n).join("").length;
    const edit = new vscode.WorkspaceEdit();
    edit.replace(uri, new vscode.Range(doc.positionAt(offset(result.start)), doc.positionAt(offset(result.end))), result.text);
    if (!await vscode.workspace.applyEdit(edit)) throw new Error("Spatial edit could not be applied.");
    await refreshSpatial(result.source, uri);
    return result;
  }
  function show() {
    if (panel) {
      panel.reveal(panel.viewColumn || vscode.ViewColumn.Beside, true);
      return;
    }
    panel = vscode.window.createWebviewPanel(
      "pixellang.studio",
      "PixelLang Studio",
      vscode.ViewColumn.Beside,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [
          vscode.Uri.joinPath(context.extensionUri, "media"),
        ],
      },
    );
    const nonce = crypto.randomBytes(16).toString("hex"),
      script = panel.webview.asWebviewUri(
        vscode.Uri.joinPath(context.extensionUri, "media/studio.js"),
      ),
      css = panel.webview.asWebviewUri(
        vscode.Uri.joinPath(context.extensionUri, "media/studio.css"),
      );
    panel.webview.html = fs
      .readFileSync(
        path.join(context.extensionPath, "media/studio.html"),
        "utf8",
      )
      .replaceAll("{{csp}}", panel.webview.cspSource)
      .replaceAll("{{nonce}}", nonce)
      .replace("{{script}}", script)
      .replace("{{css}}", css);
    panel.onDidDispose(() => {
      irPending?.controller.abort();
      panel = undefined;
      running = false;
      runEpoch++;
    });
    panel.webview.onDidReceiveMessage(async (message) => {
      try {
        if (message.type === "ready") {
          panel?.webview.postMessage({ type: "input", value: input });
          if (current) publish(current);
        } else if (message.type === "inspect-ir") {
          if (message.session !== current?.session) return;
          try {
            const ir = await inspectIR();
            panel?.webview.postMessage({ type: "ir", session: message.session, ir });
          } catch (error) {
            panel?.webview.postMessage({ type: "ir-error", session: message.session, message: error.message });
          }
        } else if (message.type === "input") {
          input = String(message.value).slice(0, 1_000_000);
          await context.workspaceState.update("input", input);
          irPending?.controller.abort();
          current = undefined;
          running = false;
          runEpoch++;
          panel?.webview.postMessage({ type: "stale" });
        } else if (message.type === "spatial-open") await refreshSpatial(message.source);
        else if (message.type === "spatial-select") {
          const target = spatialState?.targets[message.target];
          if (target) await reveal(target.span, spatialProject);
        } else if (message.type === "spatial-edit") await applySpatial(message);
        else if (message.type === "build") await build();
        else if (message.type === "run") await run();
        else if (message.type === "pause") {
          running = false;
          runEpoch++;
        } else if (message.type === "cancel" && current) {
          running = false;
          const epoch = ++runEpoch;
          const session = current.session;
          const result = await service().call("cancel", { session });
          if (epoch === runEpoch) publish({ ...result, session });
        } else if (message.type === "step") {
          if (!current) await build();
          publish({
            ...(await service().call("step", { session: current.session })),
            session: current.session,
          });
        } else if (message.type === "seek" && current) {
          running = false;
          runEpoch++;
          publish({
            ...(await service().call("seek", {
              session: current.session,
              z: message.z,
            })),
            session: current.session,
          });
          await reveal(current.event?.span?.text);
        } else if (message.type === "export") await exportImage();
        else if (message.type === "record" && current) {
          const uri = await vscode.window.showSaveDialog({
            filters: { "PixelLang Timeline": ["pixeltime"] },
          });
          if (uri)
            await vscode.workspace.fs.writeFile(
              uri,
              Buffer.from(
                JSON.stringify(
                  await service().call("recording", {
                    session: current.session,
                  }),
                ),
              ),
            );
        } else if (message.type === "reveal")
          await reveal(current?.event?.span?.text || current?.location);
      } catch (error) {
        await report(error, project);
      }
    });
  }
  async function inspectIR() {
    const session = current?.session;
    if (!session) throw new Error("Build the project before inspecting IR.");
    if (current.inspection?.ir) return current.inspection.ir;
    if (irPending?.session === session) return irPending.promise;
    const pending = { session, controller: new AbortController() };
    pending.promise = (async () => {
      const result = await service().call("inspect-ir", { session }, { signal: pending.controller.signal });
      if (current?.session !== session) throw new Error("Source changed during IR inspection; rebuild the project.");
      current.inspection = { ...current.inspection, ir: result.ir };
      return result.ir;
    })().finally(() => { if (irPending === pending) irPending = undefined; });
    irPending = pending;
    return pending.promise;
  }
  function publish(result, proj) {
    if (proj) project = proj;
    if (result.source_project && project) Object.assign(project, result.source_project);
    current = { ...current, ...result };
    panel?.webview.postMessage({ type: "state", data: current });
    if (current.state?.error) {
      const error = new Error(current.state.error.message);
      error.diagnostic = current.state.error;
      report(error, project, true);
    }
  }
  context.subscriptions.push(vscode.window.onDidChangeTextEditorSelection(event => {
    if (!panel || event.textEditor.document.languageId !== "pixellang") return;
    clearTimeout(spatialTimer);
    spatialTimer = setTimeout(async () => {
      try {
        const doc = event.textEditor.document;
        const proj = await collect(doc.uri);
        const source = proj.sourceName || path.relative(proj.root, doc.uri.fsPath).split(path.sep).join("/");
        if (spatialState?.source !== source || changedSources(proj.files, spatialProject?.files).length)
          await refreshSpatial(source, doc.uri);
        const offset = Array.from(doc.getText().slice(0, doc.offsetAt(event.selections[0].start))).length;
        const end = Array.from(doc.getText().slice(0, doc.offsetAt(event.selections[0].end))).length;
        panel?.webview.postMessage({ type: "spatial-selection", source, offset, end });
      } catch (error) {
        panel?.webview.postMessage({ type: "spatial-error", message: error.message });
      }
    }, 180);
  }));
  async function build(uri) {
    running = false;
    const epoch = ++runEpoch;
    irPending?.controller.abort();
    buildAbort?.abort();
    const controller = new AbortController();
    buildAbort = controller;
    try {
      const snapshot = await collect(uri);
      const buildInput = input;
      if (controller.signal.aborted || epoch !== runEpoch) throw new Error("Build cancelled or source changed.");
      diagnostics.clear();
      const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "PixelLang: Building", cancellable: true },
        async (_, token) => {
          const subscription = token.onCancellationRequested(() => controller.abort());
          try { return await service().call("build", { ...snapshot, input: buildInput }, { signal: controller.signal }); }
          finally { subscription.dispose(); }
        });
      if (epoch !== runEpoch) throw new Error("Source changed during build; build again to use the latest edits.");
      project = snapshot;
      current = result;
      show();
      publish(result);
      return result;
    } finally {
      if (buildAbort === controller) buildAbort = undefined;
    }
  }
  async function run(uri) {
    await build(uri);
    running = true;
    const epoch = ++runEpoch;
    panel?.webview.postMessage({ type: "running", value: true });
    try {
      while (running && epoch === runEpoch && !current.state.halted) {
        const result = await service().call("run", {
          session: current.session,
        });
        if (epoch !== runEpoch) break;
        publish(result);
        await new Promise((r) => setTimeout(r, 0));
      }
    } finally {
      if (epoch === runEpoch) running = false;
      panel?.webview.postMessage({ type: "running", value: false });
    }
  }
  async function exportImage() {
    if (!current?.image) await build();
    const uri = await vscode.window.showSaveDialog({
      defaultUri: vscode.Uri.file(path.join(project.root, "program.png")),
      filters: { "Executable PNG": ["png"] },
    });
    if (uri)
      await vscode.workspace.fs.writeFile(
        uri,
        Buffer.from(current.image, "base64"),
      );
  }
  async function recover() {
    const selected = await vscode.window.showOpenDialog({
      canSelectMany: false,
      filters: { "Executable PNG": ["png"] },
    });
    if (!selected) return;
    const bytes = await vscode.workspace.fs.readFile(selected[0]);
    const result = await vscode.window.withProgress({
      location: vscode.ProgressLocation.Notification,
      title: "Recovering PixelLang source", cancellable: true,
    }, async (_, token) => {
      const controller = new AbortController();
      const subscription = token.onCancellationRequested(() => controller.abort());
      try {
        if (token.isCancellationRequested) controller.abort();
        return await service().call("recover", {
          image: Buffer.from(bytes).toString("base64"),
        }, { signal: controller.signal });
      } finally { subscription.dispose(); }
    });
    const folders = await vscode.window.showOpenDialog({
      canSelectFolders: true,
      canSelectFiles: false,
      canSelectMany: false,
      openLabel: "Recover into folder",
    });
    if (!folders) return;
    const root = folders[0].fsPath;
    for (const name of Object.keys(result.files)) {
      const target = path.resolve(root, name);
      if (path.relative(root, target).startsWith(".."))
        throw new Error("Invalid recovered path");
      if (fs.existsSync(target))
        throw new Error(`Choose an empty destination: ${name} already exists.`);
    }
    for (const [name, text] of Object.entries(result.files)) {
      const target = path.resolve(root, name);
      await vscode.workspace.fs.createDirectory(
        vscode.Uri.file(path.dirname(target)),
      );
      await vscode.workspace.fs.writeFile(
        vscode.Uri.file(target),
        Buffer.from(text),
      );
    }
    await vscode.window.showTextDocument(
      vscode.Uri.file(path.join(root, result.entry)),
    );
  }
  async function openTimeline() {
    const selected = await vscode.window.showOpenDialog({
      canSelectMany: false,
      filters: { "PixelLang Timeline": ["pixeltime"] },
    });
    if (!selected) return;
    const document = JSON.parse(
      Buffer.from(await vscode.workspace.fs.readFile(selected[0])).toString(
        "utf8",
      ),
    );
    const result = await service().call("restore", { document });
    project = { root: path.dirname(selected[0].fsPath), ...result.project, sourceUnavailable: !result.source_project };
    input = document.input;
    running = false;
    runEpoch++;
    current = result;
    show();
    publish(result);
  }
  async function projectCommand(operation, uri) {
    const proj = await collect(uri);
    if (!proj.paths) throw new Error("Open a project containing pixel.toml first.");
    const relevant = new Set([...Object.values(proj.paths), ...(proj.manifests || [])]);
    for (const doc of vscode.workspace.textDocuments) {
      if (doc.uri.scheme !== "file" || !doc.isDirty) continue;
      const filename = fs.existsSync(doc.uri.fsPath) ? fs.realpathSync(doc.uri.fsPath) : doc.uri.fsPath;
      if (relevant.has(filename) || !path.relative(proj.root, filename).startsWith("..")) {
        if (!await doc.save()) throw new Error("Save failed: " + filename);
      }
    }
    const result = await service().call("project-" + operation, {
      root: proj.root, max_steps: proj.max_steps,
    });
    log.show(true);
    if (operation === "test") {
      for (const test of result.tests)
        log.appendLine(`${test.passed ? "PASS" : "FAIL"} ${test.name}${test.error ? ": " + test.error : ""}`);
      vscode.window.showInformationMessage(result.passed ? "PixelLang project tests passed." : "PixelLang project tests failed; see output.");
    } else if (operation === "build") {
      log.appendLine(`Built ${result.image}\nSHA-256 ${result.sha256}`);
      vscode.window.showInformationMessage("PixelLang executable PNG built: " + result.image);
    } else log.appendLine(`Updated ${result.lock} (${result.dependencies} dependencies)`);
    return result;
  }
  for (const [name, fn] of Object.entries({
    testProject: uri => projectCommand("test", uri),
    buildProject: uri => projectCommand("build", uri),
    lockProject: uri => projectCommand("lock", uri),
    openTimeline,
    studio: async () => {
      await build();
    },
    run,
    export: exportImage,
    import: recover,
  }))
    context.subscriptions.push(
      vscode.commands.registerCommand("pixellang." + name, async (uri) => {
        try {
          return await fn(uri);
        } catch (error) {
          await report(error, project);
        }
      }),
    );
  let checkTimer, checkAbort,
    revision = 0;
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument((event) => {
      if (event.document.languageId !== "pixellang" || event.document.uri.scheme !== "file") return;
      const rev = ++revision;
      irPending?.controller.abort();
      buildAbort?.abort();
      checkAbort?.abort();
      checkAbort = new AbortController();
      const signal = checkAbort.signal;
      clearTimeout(checkTimer);
      clearTimeout(spatialTimer);
      current = undefined;
      running = false;
      runEpoch++;
      panel?.webview.postMessage({ type: "stale" });
      checkTimer = setTimeout(async () => {
        let proj;
        try {
          const entry = vscode.workspace
            .getConfiguration("pixellang")
            .get("entry");
          const root = vscode.workspace.getWorkspaceFolder(event.document.uri)
            ?.uri.fsPath;
          proj = await collect(
            entry && root
              ? vscode.Uri.file(path.resolve(root, entry))
              : event.document.uri,
          );
          if (signal.aborted || rev !== revision) return;
          await service().call("check", { ...proj, revision: rev }, { signal });
          if (rev === revision) diagnostics.clear();
        } catch (error) {
          if (rev === revision) await report(error, proj, true);
        }
      }, 500);
    }),
  );
  context.subscriptions.push(
    vscode.languages.registerDocumentFormattingEditProvider("pixellang", {
      async provideDocumentFormattingEdits(doc) {
        if (doc.uri.scheme !== "file") return [];
        const proj = await collect(doc.uri);
        const formatted = await service().call("format", proj);
        return [
          vscode.TextEdit.replace(
            new vscode.Range(
              doc.positionAt(0),
              doc.positionAt(doc.getText().length),
            ),
            formatted.files[proj.sourceName || proj.entry],
          ),
        ];
      },
    }),
  );
  async function symbolLocations(method, doc, pos, includeDeclaration = true) {
    const proj = await collect(doc.uri);
    const result = await service().call(method, {
      ...proj,
      source: proj.sourceName || path.relative(proj.root, doc.uri.fsPath).split(path.sep).join("/"),
      offset: Array.from(doc.getText().slice(0, doc.offsetAt(pos))).length,
      include_declaration: includeDeclaration,
      with_sources: true,
    });
    const navigationProject = { ...proj, files: result.files };
    const spans = Array.isArray(result.result) ? result.result : result.result ? [result.result] : [];
    return Promise.all(spans.map(async span => {
      const target = await sourceDocument(span.source, navigationProject);
      return new vscode.Location(target.uri, new vscode.Range(
        position(target, span.start), position(target, span.end),
      ));
    }));
  }
  context.subscriptions.push(
    vscode.languages.registerDefinitionProvider("pixellang", {
      provideDefinition(doc, pos) {
        return symbolLocations("definition", doc, pos);
      },
    }),
    vscode.languages.registerReferenceProvider("pixellang", {
      provideReferences(doc, pos, options) {
        return symbolLocations("references", doc, pos, options.includeDeclaration);
      },
    }),
  );
  context.subscriptions.push(
    vscode.languages.registerRenameProvider("pixellang", {
      async provideRenameEdits(doc, pos, newName) {
        if (doc.uri.scheme !== "file") throw new Error("Snapshot source is read-only; rename from the workspace source.");
        const proj = await collect(doc.uri);
        const edits = await service().call("rename", {
          ...proj,
          source: proj.sourceName || path.relative(proj.root, doc.uri.fsPath).split(path.sep).join("/"),
          offset: Array.from(doc.getText().slice(0, doc.offsetAt(pos))).length,
          new_name: newName,
        });
        // Binding validation includes unchanged references, so all analyzed
        // documents must still match the snapshot, not only edited documents.
        for (const [filename, text] of Object.entries(proj.files)) {
          const target = await vscode.workspace.openTextDocument(
            fileUri(proj.paths?.[filename] || path.resolve(proj.root, filename)),
          );
          if (target.getText() !== text)
            throw new Error("Source changed during rename; please retry.");
        }
        const result = new vscode.WorkspaceEdit();
        for (const edit of edits) {
          const uri = fileUri(proj.paths?.[edit.span.source] || path.resolve(proj.root, edit.span.source));
          const target = await vscode.workspace.openTextDocument(uri);
          // Discard a result if typing changed any source while analysis ran.
          const filename = edit.span.source;
          if (target.getText() !== proj.files[filename])
            throw new Error("Source changed during rename; please retry.");
          result.replace(uri, new vscode.Range(
            position(target, edit.span.start), position(target, edit.span.end),
          ), edit.text);
        }
        return result;
      },
    }),
  );
  context.subscriptions.push(
    vscode.languages.registerCompletionItemProvider("pixellang", {
      async provideCompletionItems(doc, pos) {
        const proj = await collect(doc.uri);
        const candidates = await service().call("completion", {
          ...proj,
          source: proj.sourceName || path.relative(proj.root, doc.uri.fsPath).split(path.sep).join("/"),
          offset: Array.from(doc.getText().slice(0, doc.offsetAt(pos))).length,
        });
        return candidates.map(candidate => {
          const item = new vscode.CompletionItem(
            candidate.label, vscode.CompletionItemKind[candidate.kind],
          );
          item.detail = candidate.detail;
          return item;
        });
      },
    }, "."),
  );
  context.subscriptions.push(
    vscode.debug.registerDebugAdapterDescriptorFactory("pixellang", {
      createDebugAdapterDescriptor() {
        return new vscode.DebugAdapterInlineImplementation(
          new PixelDebug(vscode, { service, collect, publish, show, reveal, sourceDocument }),
        );
      },
    }),
  );
  context.subscriptions.push(log, diagnostics, status, {
    dispose() {
      clearTimeout(checkTimer);
      irPending?.controller.abort();
      buildAbort?.abort();
      checkAbort?.abort();
      panel?.dispose();
    },
  });
  api = { service, collect, build, inspectIR, run, sourceDocument, refreshSpatial, applySpatial, getCurrent: () => current };
  return api;
}
module.exports = { activate, deactivate() {}, getAPI: () => api };
