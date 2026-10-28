const assert = require('node:assert/strict');
const { project, pick, dragCoordinate, linkPaths } = require('../media/native-space-renderer');
const view = { entities: [
  { id: 0, position: [0, 0, 0] }, { id: 1, position: [0, 0, 2] },
  { id: 2, position: [0, 2, 0] }, { id: 3, position: [2, 0, 0] },
] };
const before = structuredClone(view);
const a = project(view, 900, 600, -.5, .4), b = project(view, 900, 600, .8, .4);
assert.notEqual(a.items.find(e => e.id === 1).x, b.items.find(e => e.id === 1).x);
assert(a.items.find(e => e.id === 2).y < a.items.find(e => e.id === 0).y);
assert.notEqual(a.items.find(e => e.id === 1).x, a.items.find(e => e.id === 0).x);
for (const item of a.items) assert.equal(pick(a.items, item.x, item.y).id, item.id);
assert.equal(pick(a.items, -100, -100), undefined);
assert.deepEqual(view, before);
const links = [{id:0,source:0,target:1},{id:1,source:1,target:0},{id:2,source:0,target:0},{id:3,source:0,target:0},{id:4,source:0,target:99}];
const savedLinks = structuredClone(links), paths = linkPaths(a.items, links);
assert.deepEqual(paths.map(edge => edge.id), [0,1,2,3]);
assert.notDeepEqual(paths[0].c1, paths[1].c2, 'Opposite directed links need distinct curves');
assert.notDeepEqual(paths[2].c1, paths[3].c1, 'Self-links need distinct loops');
for (const edge of paths) for (const point of [edge.from,edge.to,edge.c1,edge.c2]) assert(Number.isFinite(point.x) && Number.isFinite(point.y));
assert.deepEqual(links,savedLinks);
assert.deepEqual(linkPaths([],links),[]);
assert.deepEqual(project({entities:[]}, 900, 600).items, []);
for (const positions of [[[1e308, 1e308, 1e308], [-1e308, -1e308, -1e308]], [[0,0,0],[0,0,1e-300]], [[1,1,1],[1,1,1]]]) {
  const items = project({entities:positions.map((position,id) => ({id,position}))}, 900, 600).items;
  assert(items.every(item => Number.isFinite(item.x) && Number.isFinite(item.y) && Number.isFinite(item.depth)));
}
assert.equal(pick([{ id: 0, x: 1, y: 1 }, { id: 1, x: 1, y: 1 }], 1, 1).id, 1);
console.log('Native space canvas: XYZ projection, Y up, camera isolation, picking, empty pages and finite extremes passed.');

for (const axis of ['x','y','z']) {
  const geometry = project(view, 900, 600, -.5, .4, 1), i = ['x','y','z'].indexOf(axis);
  const direction = geometry.axes[i], delta = 1.25;
  const pixels = delta / geometry.magnitude / geometry.extent * geometry.scale;
  const value = dragCoordinate(view, 900, 600, -.5, .4, 1, 0, axis, pixels * direction.x, pixels * direction.y);
  assert(Math.abs(value - delta) < 1e-12);
  const preview = {entities:view.entities.map(item=>item.id!==0?item:{...item,position:item.position.map((n,j)=>j===i?value:n)})};
  const projected = project(preview,900,600,-.5,.4,1,view).items.find(item=>item.id===0);
  const original = geometry.items.find(item=>item.id===0);
  assert(Math.abs(projected.x-original.x-pixels*direction.x)<1e-10);
  assert(Math.abs(projected.y-original.y-pixels*direction.y)<1e-10);
}
assert.deepEqual(view,before);
assert.throws(()=>dragCoordinate(view,900,600,0,0,1,0,'z',10,10),/Rotate/);
assert.throws(()=>dragCoordinate(view,900,600,0,0,1,0,'x',Infinity,0),/Invalid/);
console.log('Native entity dragging: axis projection inversion, stable preview framing and degenerate-view rejection passed.');
