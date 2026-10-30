const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const messages = [];
const sandbox = { ArrayBuffer, self: { postMessage(message, transfer = []) { messages.push({ message, transfer }); } } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/scan-preview-worker.js"), "utf8"), sandbox);

const width = 640, height = 400;
const raw = new Uint8Array(width * height * 2);
const setDepth = (x, y, depth) => {
  const offset = (y * width + x) * 2;
  raw[offset] = depth & 0xff;
  raw[offset + 1] = depth >>> 8;
};
for (const [left, top] of [[40, 40], [250, 110], [540, 300]]) {
  for (let y = top; y < top + 20; y += 1) {
    for (let x = left; x < left + 20; x += 1) setDepth(x, y, 150);
  }
}
sandbox.self.onmessage({ data: {
  requestId: 17,
  rawBuffer: raw.buffer,
  matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
  scale: 1,
} });

const { message, transfer } = messages[0];
assert.equal(message.requestId, 17);
assert.equal(message.error, undefined);
assert.equal(message.result.regions, 3, "worker keeps spatially separate marker components distinct");
assert.equal(message.result.count, 3 * 20 * 20);
assert.equal(message.result.markerPositions.length, 3 * 20 * 20 * 3);
assert.equal(message.result.markerCenterPositions.length, 9);
assert.equal(message.result.markerCenterPixelPositions.length, 3);
assert.ok(message.result.markerFlags.every(flag => flag === 1));
assert.equal(transfer.length, 4, "large numeric results transfer their buffers back without cloning");
assert.deepEqual(Array.from(message.result.rawBounds.min), [40, -319, -150]);
assert.deepEqual(Array.from(message.result.rawBounds.max), [559, -40, -150]);

messages.length = 0;
sandbox.self.onmessage({ data: {
  requestId: 18,
  rawBuffer: raw.buffer,
  matrix: [0, 0, 0, 1e308, 0, 0, 0, 1e308, 0, 0, 0, 1e308, 0, 0, 0, 1],
  scale: 1,
} });
const scaledProjection = messages[0].message;
assert.equal(scaledProjection.error, undefined);
assert.equal(scaledProjection.result.count, 3 * 20 * 20,
  "homogeneously scaled calibration retains valid projected points");
assert.deepEqual(Array.from(scaledProjection.result.positions.slice(0, 3)), [150, -150, -150]);
assert.equal(scaledProjection.result.markerPositions.length, 3 * 20 * 20 * 3);
assert.ok(scaledProjection.result.positions.every(Number.isFinite));

messages.length = 0;
sandbox.self.onmessage({ data: {
  requestId: 19,
  rawBuffer: raw.buffer,
  matrix: [0, 0, 0, 1e40, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1],
  scale: 1,
} });
const unrenderableProjection = messages[0].message;
assert.equal(unrenderableProjection.error, undefined);
assert.equal(unrenderableProjection.result.count, 0,
  "points outside Float32 range must not reach the GPU");
assert.equal(unrenderableProjection.result.markerPositions.length, 0,
  "unrenderable marker points must not enter table fitting");

console.log("Scan preview worker: depth filtering, marker regions and calibrated projection passed.");
