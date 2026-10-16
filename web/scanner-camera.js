"use strict";

function rotateScannerPoint(dx, dy, dz, yaw, pitch) {
  const cyaw = Math.cos(yaw), syaw = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);

  // Camera-space orbit: yaw around vertical Y, then pitch around horizontal X.
  const x = dx * cyaw - dz * syaw;
  const yawDepth = dx * syaw + dz * cyaw;
  const y = dy * cp - yawDepth * sp;
  const z = dy * sp + yawDepth * cp;
  return [x, y, z];
}

if (typeof window !== "undefined") window.rotateScannerPoint = rotateScannerPoint;
if (typeof module !== "undefined" && module.exports) module.exports = { rotateScannerPoint };
