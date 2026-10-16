const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const sandbox = {
  window: {},
  TextDecoder,
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/ply-parser.js"), "utf8"), sandbox);
const parse = sandbox.window.CadyxPlyPreview.parse;

function mesh(faceCount = 3) {
  const header = Buffer.from(`ply\nformat binary_little_endian 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\nelement face ${faceCount}\nproperty list uchar uint vertex_indices\nproperty list uchar float texcoord\nproperty uchar material\nend_header\n`);
  const body = Buffer.alloc(36 + faceCount * 23);
  [0, 0, 0, 1, 0, 0, 0, 1, 0].forEach((value, index) => body.writeFloatLE(value, index * 4));
  for (let face = 0; face < faceCount; face++) {
    const start = 36 + face * 23;
    body[start] = 3;
    [0, 1, 2].forEach((value, index) => body.writeUInt32LE(value, start + 1 + index * 4));
    body[start + 13] = 2;
    body.writeFloatLE(0.25, start + 14);
    body.writeFloatLE(0.75, start + 18);
    body[start + 22] = 7;
  }
  const bytes = Buffer.concat([header, body]);
  return { buffer: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), headerSize: header.length };
}

const small = mesh();
const result = parse(small.buffer);
assert.deepEqual(Array.from(result.triangles), [0, 1, 2, 0, 1, 2, 0, 1, 2]);
assert.deepEqual(Array.from(result.positions), [0, 0, 0, 1, 0, 0, 0, 1, 0]);
const large = mesh(36_002);
const sampled = parse(large.buffer);
assert.equal(sampled.faceCount, 36_002);
assert.equal(sampled.previewFaceCount, 18_001);
assert.ok(Array.from(sampled.triangles).every((index, offset) => index === offset % 3));
// Last face is skipped by downsampling: its payload still must fit the input.
assert.throws(() => parse(large.buffer.slice(0, -2)), /обрезаны/);
assert.throws(() => parse(small.buffer.slice(0, small.headerSize + 35)), /обрезаны/);
const invalid = small.buffer.slice(0);
new DataView(invalid).setUint32(small.headerSize + 37, 99, true);
assert.throws(() => parse(invalid), /отсутствующую вершину/);
console.log("PLY preview: sampling, extra properties, bounds and invalid indices passed.");
