// Coordinates come from the native typed AST. No host parser or generated source.
function planCoordinateEdit(text, coordinates, values) {
  const points = [...text], offsets = [0];
  for (const point of points) offsets.push(offsets.at(-1) + point.length);
  if (!values || typeof values !== 'object' || Array.isArray(values) || !Object.keys(values).length) throw Error('Choose a coordinate to change.');
  const edits = [];
  for (const [name, value] of Object.entries(values)) {
    const axis = coordinates.axes.find(item => item.name === name);
    if (!axis || !axis.literal) throw Error('Computed coordinates must be edited as expressions in source.');
    if (typeof value !== 'number' || !Number.isFinite(value)) throw Error('Coordinates must be finite numbers.');
    const { start, end } = axis.range;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < coordinates.range.start || end > coordinates.range.end || end <= start || end > points.length) throw Error('Invalid coordinate source range.');
    let literal = Object.is(value, -0) ? '-0.0' : String(value);
    if (!/[.eE]/.test(literal)) literal += '.0';
    edits.push({ start: offsets[start], end: offsets[end], text: literal });
  }
  edits.sort((a, b) => b.start - a.start);
  for (let i = 1; i < edits.length; i++) if (edits[i].end > edits[i - 1].start) throw Error('Overlapping coordinate ranges.');
  return edits;
}
module.exports = { planCoordinateEdit };
