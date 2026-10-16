const assert = require("node:assert/strict");
const { rotateScannerPoint } = require("../web/scanner-camera.js");

const closeTo = (actual, expected, message) => {
  assert.ok(Math.abs(actual - expected) < 1e-12, `${message}: expected ${expected}, got ${actual}`);
};

assert.deepEqual(rotateScannerPoint(2, -3, 4, 0, 0), [2, -3, 4], "zero orbit preserves camera-space coordinates");

const yawed = rotateScannerPoint(0, 1, 0, Math.PI / 2, 0);
closeTo(yawed[0], 0, "yaw leaves the vertical axis horizontal coordinate unchanged");
closeTo(yawed[1], 1, "yaw preserves the vertical axis");
closeTo(yawed[2], 0, "yaw leaves a vertical-axis point on the vertical axis");

const horizontal = rotateScannerPoint(0, 0, 1, Math.PI / 2, 0);
closeTo(horizontal[0], -1, "yaw rotates depth into horizontal view");
closeTo(horizontal[1], 0, "yaw does not roll depth into vertical view");

const pitched = rotateScannerPoint(0, 0, 1, 0, Math.PI / 2);
closeTo(pitched[0], 0, "pitch leaves the horizontal coordinate unchanged");
closeTo(pitched[1], -1, "pitch rotates depth around the horizontal axis");

console.log("Scanner camera orbit: yaw around Y and pitch around X passed.");
