const assert = require('node:assert/strict');
const { planCoordinateEdit } = require('../src/native-coordinate-edits');
const text = '// 星 🛰️\r\nlet p = Point3::[World]{z: -3.0, /* keep */ x: 1.0, y: delta + 2.0}';
const range = fragment => { const start = [...text.slice(0,text.indexOf(fragment))].length; return {start,end:start+[...fragment].length}; };
const coordinates = { range:range('Point3::[World]{z: -3.0, /* keep */ x: 1.0, y: delta + 2.0}'), axes:[
 {name:'x',range:range('1.0'),literal:true}, {name:'y',range:range('delta + 2.0'),literal:false}, {name:'z',range:range('-3.0'),literal:true}
] };
let result = text;
for (const edit of planCoordinateEdit(text,coordinates,{z:7,x:-0})) result = result.slice(0,edit.start)+edit.text+result.slice(edit.end);
assert.equal(result,text.replace('-3.0','7.0').replace('1.0','-0.0'));
for (const values of [{y:0},{x:NaN},{z:Infinity},{w:1},{}]) assert.throws(()=>planCoordinateEdit(text,coordinates,values));
assert.equal(planCoordinateEdit(text,coordinates,{z:1e100})[0].text,'1e+100');
const computedText = 'let target = transformPoint(transform, base + offset)';
const computed = {range:{start:13,end:computedText.length},axes:[]};
for (const values of [{x:0},{y:1},{z:2}]) {
 assert.throws(()=>planCoordinateEdit(computedText,computed,values),/edited as expressions in source/);
}
console.log('Native coordinate edits: UTF-16 ranges, comments, signed zero, finite validation and computed-axis protection passed.');
