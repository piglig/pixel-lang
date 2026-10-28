const vscode = require("vscode"),
  path = require("node:path"),
  fs = require("node:fs"),
  crypto = require("node:crypto");
const { Bridge } = require("./bridge");
const { PendingRequests, waitFor } = require("./pending-requests");
const { EditorWork } = require("./editor-work");
const { performance } = require("node:perf_hooks");
const { PixelDebug } = require("./debug");
const onboarding = require("./onboarding");
let api;
function activate(context) {
  const log = vscode.window.createOutputChannel("PixelLang");
  onboarding.register(vscode, context, log);
  const diagnostics = vscode.languages.createDiagnosticCollection("pixellang");
  const editorWork = new EditorWork();
  let spatialVisible = false;
  let bridge,
    panel,
    current,
    project,
    running = false,
    runEpoch = 0,
    buildAbort,
    irPending,
    imagePending,
    projectionPending,
    input = context.workspaceState.get("input", "");
  const workspaceRequests = new PendingRequests();
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
  const application = require('./application').registerApplication(vscode, context, service);
  context.subscriptions.push(vscode.workspace.onDidChangeConfiguration(event => {
    if (event.affectsConfiguration("pixellang.pythonPath")) {
      workspaceRequests.clear();
      bridge?.dispose();
      bridge = undefined;
      current = undefined;
    }
  }));
  async function collect(uri, {signal} = {}) {
    if (signal?.aborted) throw Object.assign(new Error("Compilation cancelled"), {name: "AbortError"});
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
        const loaded = await waitFor(workspaceRequests.run(manifestRoot, () => service().call("workspace", { root: manifestRoot })), signal);
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
      if (proj.snapshotOnly) throw new Error('Use the recorded event source snapshot');
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
      spatialVisible = false;
      clearTimeout(spatialTimer);
      irPending?.controller.abort();
      imagePending?.controller.abort();
      projectionPending?.controller.abort();
      panel = undefined;
      running = false;
      runEpoch++;
    });
    panel.webview.onDidReceiveMessage(async (message) => {
      try {
        if (message.type === "view") {
          spatialVisible = message.view === "spatial";
          clearTimeout(spatialTimer);
          return;
        }
        if (message.type === "ready") {
          panel?.webview.postMessage({ type: "input", value: input });
          if (current) publish(current);
        } else if (message.type === "inspect-projection") {
          if (message.session !== current?.session) return;
          try { await inspectProjection(); }
          catch (error) {
            panel?.webview.postMessage({ type: "projection-error", session: message.session, message: error.message });
          }
        } else if (message.type === "inspect-image") {
          if (message.session !== current?.session) return;
          try {
            const image = await inspectImage();
            panel?.webview.postMessage({ type: "image", session: message.session, image });
          } catch (error) {
            panel?.webview.postMessage({ type: "image-error", session: message.session, message: error.message });
          }
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
          imagePending?.controller.abort();
          projectionPending?.controller.abort();
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
  async function inspectProjection() {
    const session = current?.session;
    if (!session) throw new Error("Build the project before viewing its execution space.");
    if (current.projection_ready) return current;
    if (projectionPending?.session === session) return projectionPending.promise;
    const pending = { session, controller: new AbortController() };
    pending.promise = vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "PixelLang: Generating execution space", cancellable: true },
      async (_, token) => {
        const subscription = token.onCancellationRequested(() => pending.controller.abort());
        try {
          const result = await service().call("inspect-projection", { session }, { signal: pending.controller.signal });
          if (current?.session !== session) throw new Error("Source changed while generating execution space; rebuild the project.");
          publish(result);
          return current;
        } finally { subscription.dispose(); }
      }).finally(() => { if (projectionPending === pending) projectionPending = undefined; });
    projectionPending = pending;
    return pending.promise;
  }
  async function inspectImage() {
    const session = current?.session;
    if (!session) throw new Error("Build the project before viewing its PNG.");
    if (current.image) return current.image;
    if (imagePending?.session === session) return imagePending.promise;
    const pending = { session, controller: new AbortController() };
    pending.promise = vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "PixelLang: Generating PNG", cancellable: true },
      async (_, token) => {
        const subscription = token.onCancellationRequested(() => pending.controller.abort());
        try {
          const result = await service().call("inspect-image", { session }, { signal: pending.controller.signal });
          if (current?.session !== session) throw new Error("Source changed while generating PNG; rebuild the project.");
          current.image = result.image;
          return result.image;
        } finally { subscription.dispose(); }
      }).finally(() => { if (imagePending === pending) imagePending = undefined; });
    imagePending = pending;
    return pending.promise;
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
    if (proj) project = { ...proj };
    if (result.source_project && project) project = { ...project, ...result.source_project };
    current = { ...current, ...result };
    panel?.webview.postMessage({ type: "state", data: current });
    if (current.state?.error) {
      const error = new Error(current.state.error.message);
      error.diagnostic = current.state.error;
      report(error, project, true);
    }
  }
  context.subscriptions.push(vscode.window.onDidChangeTextEditorSelection(event => {
    if (!panel || !spatialVisible || vscode.debug.activeDebugSession ||
        ![vscode.TextEditorSelectionChangeKind.Keyboard, vscode.TextEditorSelectionChangeKind.Mouse].includes(event.kind) ||
        event.textEditor.document.languageId !== "pixellang") return;
    clearTimeout(spatialTimer);
    spatialTimer = setTimeout(async () => {
      if (!spatialVisible || vscode.debug.activeDebugSession) return;
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
    clearTimeout(checkTimer);
    editorWork.cancel();
    running = false;
    const epoch = ++runEpoch;
    irPending?.controller.abort();
    imagePending?.controller.abort();
    projectionPending?.controller.abort();
    buildAbort?.abort();
    const controller = new AbortController();
    buildAbort = controller;
    try {
      let snapshot;
      const buildInput = input;
      const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "PixelLang: Building", cancellable: true },
        async (progress, token) => {
          const subscription = token.onCancellationRequested(() => controller.abort());
          let timer;
          try {
            if (token.isCancellationRequested) controller.abort();
            progress.report({ message: "Reading project files" });
            const readStarted = performance.now();
            snapshot = await collect(uri, {signal: controller.signal});
            log.appendLine(`Build source collection: ${Math.round(performance.now() - readStarted)} ms, ${Object.keys(snapshot.files).length} files, entry ${snapshot.entry}`);
            if (controller.signal.aborted || epoch !== runEpoch) throw new Error("Build cancelled or source changed.");
            diagnostics.clear();
            const started = performance.now();
            const reportWait = () => progress.report({
              message: `Waiting for compiler · ${Math.floor((performance.now() - started) / 1000)} s`,
            });
            reportWait();
            timer = setInterval(reportWait, 1000);
            return await service().call("build", { ...snapshot, input: buildInput }, { signal: controller.signal });
          } finally {
            clearInterval(timer);
            subscription.dispose();
          }
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
    if (!current?.session) await build();
    const uri = await vscode.window.showSaveDialog({
      defaultUri: vscode.Uri.file(path.join(project.root, "program.png")),
      filters: { "Executable PNG": ["png"] },
    });
    if (uri) {
      const image = await inspectImage();
      await vscode.workspace.fs.writeFile(uri, Buffer.from(image, "base64"));
    }
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
  async function projectCommand(operation, uri, options = {}) {
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
      root: proj.root, max_steps: proj.max_steps, ...options,
    });
    if (options.list) return result;
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
    runTest: async (uri, testName) => {
      const listed = await projectCommand("test", uri, { list: true });
      const choices = listed.tests.map(test => ({ label: test.name || test.entry,
        description: test.entry + (test.input ? " · " + test.input : ""), id: test.id }));
      if (typeof testName === "string" && choices.filter(test => test.label === testName).length > 1)
        throw new Error("Multiple tests have this name; select the test from the picker.");
      const selected = typeof testName === "string" ? choices.find(test => test.label === testName) : await vscode.window.showQuickPick(
        choices,
        { title: "PixelLang: Run a Test", placeHolder: "Select a test to run" },
      );
      if (typeof testName === "string" && !selected) throw new Error("Test not found: " + testName);
      if (!selected) return;
      return projectCommand("test", uri, { test_id: selected.id });
    },
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
      vscode.commands.registerCommand("pixellang." + name, async (uri, ...args) => {
        try {
          return await fn(uri, ...args);
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
      if (!event.contentChanges.length) return;
      const rev = ++revision;
      irPending?.controller.abort();
      imagePending?.controller.abort();
      projectionPending?.controller.abort();
      buildAbort?.abort();
      checkAbort?.abort();
      const checkTicket = editorWork.beginCheck();
      checkAbort = checkTicket.controller;
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
            {signal},
          );
          if (signal.aborted || rev !== revision) return;
          await service().call("check", { ...proj, revision: rev }, { signal });
          if (rev === revision) diagnostics.clear();
        } catch (error) {
          if (rev === revision && !signal.aborted) await report(error, proj, true);
        } finally {
          editorWork.finishCheck(checkTicket);
        }
      }, 500);
    }),
  );
  context.subscriptions.push(
    vscode.languages.registerDocumentFormattingEditProvider("pixellang", {
      async provideDocumentFormattingEdits(doc, options, token) {
        if (doc.uri.scheme !== "file") return [];
        const version = doc.version;
        const text = doc.getText();
        const entry = path.basename(doc.uri.fsPath);
        const controller = new AbortController();
        const cancellation = token.onCancellationRequested(() => controller.abort());
        try {
          if (token.isCancellationRequested) return [];
          const formatted = await service().call("format", {
            entry, files: { [entry]: text }, revision: version,
          }, { signal: controller.signal });
          if (token.isCancellationRequested || doc.version !== version) return [];
          if (formatted.files[entry] === text) return [];
          return [
            vscode.TextEdit.replace(
              new vscode.Range(doc.positionAt(0), doc.positionAt(text.length)),
              formatted.files[entry],
            ),
          ];
        } finally {
          cancellation.dispose();
        }
      },
    }),
  );
  async function editorRequest(method, doc, fields, token) {
    const version = doc.version;
    const controller = new AbortController();
    const untrack = editorWork.track(controller);
    const subscription = token?.onCancellationRequested(() => controller.abort());
    if (token?.isCancellationRequested) controller.abort();
    try {
      if (method === "document-symbols") await editorWork.beforeOutline(controller.signal);
      const proj = await collect(doc.uri, {signal: controller.signal});
      if (doc.version !== version) controller.abort();
      const result = await service().call(method, {
        ...proj,
        source: proj.sourceName || path.relative(proj.root, doc.uri.fsPath).split(path.sep).join("/"),
        ...fields,
      }, {signal: controller.signal});
      if (doc.version !== version || controller.signal.aborted) throw new vscode.CancellationError();
      return {proj, result};
    } catch (error) {
      if (controller.signal.aborted || error.name === "AbortError") throw new vscode.CancellationError();
      throw error;
    } finally { untrack(); subscription?.dispose(); }
  }
  async function symbolLocations(method, doc, pos, includeDeclaration = true, token) {
    const {proj, result} = await editorRequest(method, doc, {
      offset: Array.from(doc.getText().slice(0, doc.offsetAt(pos))).length,
      include_declaration: includeDeclaration,
      with_sources: true,
    }, token);
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
      provideDefinition(doc, pos, token) {
        return symbolLocations("definition", doc, pos, true, token);
      },
    }),
    vscode.languages.registerReferenceProvider("pixellang", {
      provideReferences(doc, pos, options, token) {
        return symbolLocations("references", doc, pos, options.includeDeclaration, token);
      },
    }),
  );
  context.subscriptions.push(
    vscode.languages.registerRenameProvider("pixellang", {
      async provideRenameEdits(doc, pos, newName, token) {
        if (doc.uri.scheme !== "file") throw new Error("Snapshot source is read-only; rename from the workspace source.");
        const {proj, result: edits} = await editorRequest("rename", doc, {
          offset: Array.from(doc.getText().slice(0, doc.offsetAt(pos))).length,
          new_name: newName,
        }, token);
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
    vscode.languages.registerSignatureHelpProvider("pixellang", {
      async provideSignatureHelp(doc, pos, token) {
        const version = doc.version;
        const {result: result} = await editorRequest("signature-help", doc, {
          offset: Array.from(doc.getText().slice(0, doc.offsetAt(pos))).length,
        }, token);
        if (!result || doc.version !== version) return null;
        const signature = new vscode.SignatureInformation(result.label);
        signature.parameters = result.parameters.map(label => new vscode.ParameterInformation(label));
        const help = new vscode.SignatureHelp();
        help.signatures = [signature];
        help.activeSignature = 0;
        help.activeParameter = result.activeParameter;
        return help;
      },
    }, "(", ","),
    vscode.languages.registerHoverProvider("pixellang", {
      async provideHover(doc, pos, token) {
        const version = doc.version;
        const {result: result} = await editorRequest("hover", doc, {
          offset: Array.from(doc.getText().slice(0, doc.offsetAt(pos))).length,
        }, token);
        if (!result || doc.version !== version) return null;
        const content = new vscode.MarkdownString();
        content.appendCodeblock(result.label, "pixellang");
        return new vscode.Hover(content, new vscode.Range(
          position(doc, result.span.start), position(doc, result.span.end),
        ));
      },
    }),
    vscode.languages.registerDocumentSymbolProvider("pixellang", {
      async provideDocumentSymbols(doc, token) {
        const version = doc.version;
        const {result: result} = await editorRequest("document-symbols", doc, {}, token);
        if (doc.version !== version) return [];
        return result.map(item => new vscode.DocumentSymbol(
          item.name, "", vscode.SymbolKind[item.kind],
          new vscode.Range(position(doc, item.span.start), position(doc, item.span.end)),
          new vscode.Range(position(doc, item.selection.start), position(doc, item.selection.end)),
        ));
      },
    }),
    vscode.languages.registerCompletionItemProvider("pixellang", {
      async provideCompletionItems(doc, pos, token) {
        const {result: candidates} = await editorRequest("completion", doc, {
          offset: Array.from(doc.getText().slice(0, doc.offsetAt(pos))).length,
        }, token);
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
      editorWork.cancel();
      irPending?.controller.abort();
      imagePending?.controller.abort();
      projectionPending?.controller.abort();
      buildAbort?.abort();
      checkAbort?.abort();
      panel?.dispose();
    },
  });
  const nativeGraphs = require('./native-graphs').registerNativeGraphs(vscode, context, service, collect, fileUri);
  api = { application, nativeGraphs, service, collect, build, inspectIR, inspectImage, inspectProjection, run, sourceDocument, refreshSpatial, applySpatial, getCurrent: () => current };
  return api;
}
module.exports = { activate, deactivate() {}, getAPI: () => api };
