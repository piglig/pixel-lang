// Native AST ranges are Unicode code-point offsets. VS Code edits use UTF-16.
// The source document is authoritative; this module never maintains a second graph.
const identifier = /^[\p{L}_][\p{L}\p{N}_]*$/u;
const reserved = new Set('fn let var if else for in while break continue return import export as record enum match true false with inout'.split(' '));
function name(value) {
  if (typeof value !== 'string' || !identifier.test(value) || reserved.has(value)) throw Error('Expected a PixelLang identifier.');
  return value;
}
function planEdit(text, graph, action) {
  if (typeof text !== 'string' || !graph || !action) throw Error('Missing native graph snapshot.');
  const points = [...text], offsets = [0];
  for (const point of points) offsets.push(offsets.at(-1) + point.length);
  const edits = [];
  const slice = (start, end) => points.slice(start, end).join('');
  function range(value) {
    if (!value || !Number.isInteger(value.start) || !Number.isInteger(value.end) ||
        value.start < 0 || value.end < value.start || value.end > points.length) throw Error('Invalid native source range.');
    return value;
  }
  const body = range(graph.body);
  if (points[body.start] !== '{' || points[body.end - 1] !== '}') throw Error('Complete the flow body syntax before editing its graph.');
  function replace(value, replacement) {
    const selected = range(value);
    if (selected.start <= body.start || selected.end >= body.end) throw Error('Edit escapes the selected flow body.');
    edits.push({ start: selected.start, end: selected.end, text: replacement });
  }
  function lineStart(offset) {
    while (offset > 0 && points[offset - 1] !== '\n') offset--;
    return offset;
  }
  function remove(item) {
    const selected = range(item.range);
    let start = selected.start, end = selected.end;
    const first = lineStart(start);
    let last = end;
    while (last < points.length && points[last] !== '\n') last++;
    if (/^[\t ]*$/.test(slice(first, start)) && /^[\t ;\r]*$/.test(slice(end, last))) {
      start = first;
      end = last < points.length ? last + 1 : last;
    } else {
      let semicolon = end;
      while (points[semicolon] === ' ' || points[semicolon] === '\t') semicolon++;
      if (points[semicolon] === ';') end = semicolon + 1;
    }
    replace({ start, end }, '');
  }
  function append(statement) {
    const close = body.end - 1, first = lineStart(close);
    const newline = text.includes('\r\n') ? '\r\n' : '\n';
    const closingIndent = /^[\t ]*/.exec(slice(lineStart(graph.range.start), graph.range.start))[0];
    const statements = [...graph.values, ...graph.children, ...graph.states, ...graph.updates, ...graph.outputs]
      .sort((a, b) => a.range.start - b.range.start);
    const existingIndent = statements.length ? slice(lineStart(statements[0].range.start), statements[0].range.start) : '';
    const indent = existingIndent && /^[\t ]+$/.test(existingIndent) ? existingIndent : closingIndent + '    ';
    if (/^[\t ]*$/.test(slice(first, close)) && first > body.start) {
      replace({ start: first, end: first }, indent + statement + newline);
    } else {
      replace({ start: close, end: close }, newline + indent + statement + newline + closingIndent);
    }
  }
  function find(items, id) {
    const selected = items.find(item => item.node === id);
    if (!selected) throw Error('Graph selection expired; refresh before editing.');
    return selected;
  }
  const bindings = [...graph.initialInputs, ...graph.inputs, ...graph.states, ...graph.values, ...graph.children];
  function newName(value) {
    const result = name(value);
    if (bindings.some(item => item.name === result)) throw Error('That flow name is already in use.');
    return result;
  }
  function expression(value) {
    if (typeof value !== 'string' || !value.trim()) throw Error('Provide a PixelLang expression.');
    return value.trim();
  }
  function argumentsFor(target) {
    const values = action.initializers || [];
    if (!Array.isArray(values) || values.length !== target.initialInputs.length) throw Error('Provide one expression for each creation parameter.');
    return values.map(expression);
  }
  if (action.kind === 'add-value') {
    append(`let ${newName(action.name)} = ${expression(action.expression)}`);
  } else if (action.kind === 'add-instance') {
    const childName = newName(action.name);
    const target = graph.palette.find(item => item.target === action.target);
    if (!target || target.kind !== 'flow' || target.generic) throw Error('Choose an accessible flow with a concrete signature.');
    append(`instance ${childName} = ${target.target}(${argumentsFor(target).join(', ')})`);
  } else if (action.kind === 'set-initializers') {
    const child = find(graph.children, action.node);
    const target = graph.palette.find(item => item.target === child.target);
    if (!target || target.kind !== 'flow' || target.generic) throw Error('Choose an accessible flow with a concrete signature.');
    const values = argumentsFor(target);
    if (values.length !== child.initializers.length) throw Error('Repair the creation argument count in source before editing these values.');
    child.initializers.forEach((span, index) => {
      if (slice(span.start, span.end) !== values[index]) replace(span, values[index]);
    });
  } else if (action.kind === 'add-state') {
    const stateName = newName(action.name), initializer = expression(action.initializer);
    const typ = typeof action.type === 'string' ? action.type.trim() : '';
    append(`state ${stateName}${typ ? ': ' + typ : ''} = ${initializer}`);
  } else if (['remove-value', 'remove-instance', 'remove-state', 'remove-next'].includes(action.kind)) {
    const field = { 'remove-value': 'values', 'remove-instance': 'children', 'remove-state': 'states', 'remove-next': 'updates' }[action.kind];
    // Keep uses visible so native diagnostics identify the work still needed.
    remove(find(graph[field], action.node));
  } else if (action.kind === 'set-expression') {
    const items = [...graph.values, ...graph.children, ...graph.states, ...graph.updates, ...graph.outputs];
    const item = find(items, action.node);
    replace(item.expression?.range || item.constructor?.range || item.initializer, expression(action.expression));
  } else if (action.kind === 'set-output' || action.kind === 'set-next') {
    const value = expression(action.expression);
    let previous = graph.outputs, statement = `output ${value}`;
    if (action.kind === 'set-next') {
      const stateName = name(action.state);
      if (!graph.states.some(item => item.name === stateName)) throw Error('Unknown state cell.');
      previous = graph.updates.filter(item => item.state === stateName);
      statement = `next ${stateName} = ${value}`;
    }
    if (previous.length > 1) throw Error('Remove duplicate updates or outputs in source before editing this expression.');
    if (previous.length) replace(previous[0].expression.range, value);
    else append(statement);
  } else throw Error('Unknown expression flow edit.');
  edits.sort((a, b) => b.start - a.start || b.end - a.end);
  for (let i = 1; i < edits.length; i++) if (edits[i].end > edits[i - 1].start) throw Error('Overlapping graph edits.');
  return edits.map(edit => ({ ...edit, start: offsets[edit.start], end: offsets[edit.end] }));
}
// Validate the native reparse before a webview expression reaches the document.
// A text field may contain an expression, never additional declarations.
function validateGraphEdit(before, after, action, original, candidate) {
  if (!after || before.name !== after.name || before.source !== after.source || before.range.start !== after.range.start) throw Error('The edit must preserve the selected flow declaration.');
  const fields = ['values', 'children', 'states', 'updates', 'outputs'];
  let field, index, adding = false, removing = false, expected, span;
  const selected = fields.find(key => before[key].some(item => item.node === action.node));
  const addFields = { 'add-value': 'values', 'add-instance': 'children', 'add-state': 'states' };
  const removeFields = { 'remove-value': 'values', 'remove-instance': 'children', 'remove-state': 'states', 'remove-next': 'updates' };
  if (Object.hasOwn(addFields, action.kind)) {
    field = addFields[action.kind]; index = before[field].length; adding = true;
  } else if (Object.hasOwn(removeFields, action.kind)) {
    field = removeFields[action.kind]; index = before[field].findIndex(item => item.node === action.node); removing = true;
  } else if (action.kind === 'set-output' || action.kind === 'set-next') {
    field = action.kind === 'set-output' ? 'outputs' : 'updates';
    index = action.kind === 'set-output' ? 0 : before.updates.findIndex(item => item.state === action.state);
    adding = action.kind === 'set-output' ? before.outputs.length === 0 : index < 0;
    if (adding) index = before[field].length;
  } else { field = selected; index = field ? before[field].findIndex(item => item.node === action.node) : -1; }
  if (!field || index < 0) throw Error('The edited declaration is unavailable.');
  const oldPoints = [...original], points = [...candidate];
  const text = (list, range) => list.slice(range.start, range.end).join('');
  for (const key of fields) {
    const difference = key === field ? (adding ? 1 : removing ? -1 : 0) : 0;
    if (after[key].length !== before[key].length + difference) throw Error('Enter one expression or declaration, without additional statements.');
    const oldItems = before[key].filter((_, i) => key !== field || i !== index || adding);
    const newItems = after[key].filter((_, i) => key !== field || i !== index || removing);
    if (oldItems.length !== newItems.length || oldItems.some((item, i) => text(oldPoints, item.range) !== text(points, newItems[i].range))) throw Error('The edit changes another flow member.');
  }
  if (removing) return;
  const item = after[field][index];
  if (action.kind === 'add-value') expected = `let ${action.name} = ${action.expression.trim()}`;
  if (action.kind === 'add-state') expected = `state ${action.name}${action.type?.trim() ? ': ' + action.type.trim() : ''} = ${action.initializer.trim()}`;
  if (action.kind === 'add-instance') expected = `instance ${action.name} = ${action.target}(${(action.initializers || []).map(value => value.trim()).join(', ')})`;
  if (expected !== undefined && text(points, item.range) !== expected) throw Error('Enter exactly one flow declaration.');
  if (action.kind === 'set-initializers') {
    const values = (action.initializers || []).map(value => value.trim());
    if (item.initializers.length !== values.length || item.initializers.some((range, i) => text(points, range) !== values[i])) throw Error('Enter one expression for each creation parameter.');
  } else if (!adding) {
    span = item.expression?.range || item.constructor?.range || item.initializer;
    if (!span || text(points, span) !== action.expression.trim()) throw Error('Enter one complete PixelLang expression.');
  } else if (action.kind === 'set-next' || action.kind === 'set-output') {
    if (text(points, item.expression.range) !== action.expression.trim()) throw Error('Enter one complete PixelLang expression.');
  }
}
async function applyGraphEdit(vscode, editor, snapshot, action) {
  const document = editor.document;
  if (document.uri.toString() !== snapshot.uri || document.version !== snapshot.version || document.getText() !== snapshot.text) {
    throw Error('Source changed; refresh the graph before editing.');
  }
  const edits = planEdit(snapshot.text, snapshot.graph, action);
  const applied = await editor.edit(builder => {
    for (const edit of edits) builder.replace(new vscode.Range(document.positionAt(edit.start), document.positionAt(edit.end)), edit.text);
  }, { undoStopBefore: true, undoStopAfter: true });
  if (!applied) throw Error('Source changed before the graph edit could be applied.');
  return edits;
}
module.exports = { planEdit, validateGraphEdit, applyGraphEdit };
