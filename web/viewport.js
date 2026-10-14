(() => {
  const MAX_PREVIEW_POINTS = 24000;
  const MAX_CAMERA_PITCH = Math.PI / 2;
  function createViewer(canvas) {
    const context = canvas.getContext("2d", { alpha: true });
    let geometry = null;
    let yaw = 0.68;
    let pitch = 0.38;
    let zoom = 1;
    let drag = null;
    let camera = new Float64Array(0);
    let screenX = new Float64Array(0);
    let screenY = new Float64Array(0);
    let faceRecords = new Float64Array(0);
    let faceOrder = new Uint32Array(0);
    let vertexLight = new Float32Array(0);
    let transition = null;
    let transitionFrame = 0;
    let cameraFrame = 0;
    let sweepProgress = null;

    function scheduleCameraRender() {
      if (cameraFrame) return;
      cameraFrame = requestAnimationFrame(() => {
        cameraFrame = 0;
        render();
      });
    }

    function orbitCenterFor(value) {
      const axes = value.rotationAxes || [];
      const first = axes[0];
      if (!first || !Array.isArray(first.start) || !Array.isArray(first.end)
          || first.start.length !== 3 || first.end.length !== 3
          || ![...first.start, ...first.end].every(Number.isFinite)
          || Math.abs(first.start[0] - first.end[0]) > 1e-9
          || Math.abs(first.start[1] - first.end[1]) > 1e-9) return null;
      const [x, y] = first.start;
      if (!axes.every((axis) => Array.isArray(axis.start) && Array.isArray(axis.end)
          && axis.start.length === 3 && axis.end.length === 3
          && [...axis.start, ...axis.end].every(Number.isFinite)
          && Math.abs(axis.start[0] - x) <= 1e-9 && Math.abs(axis.start[1] - y) <= 1e-9
          && Math.abs(axis.start[0] - axis.end[0]) <= 1e-9
          && Math.abs(axis.start[1] - axis.end[1]) <= 1e-9)) return null;
      return [x, y, value.bounds.min[2] / 2 + value.bounds.max[2] / 2];
    }

    function fitRadiusFromBounds(bounds, center) {
      let radius = 0;
      for (const x of [bounds.min[0], bounds.max[0]]) {
        for (const y of [bounds.min[1], bounds.max[1]]) {
          for (const z of [bounds.min[2], bounds.max[2]]) {
            radius = Math.max(radius, Math.hypot(x - center[0], y - center[1], z - center[2]));
          }
        }
      }
      return Math.max(radius, 0.001);
    }

    function render() {
      const rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.round(rect.width * dpr);
      const height = Math.round(rect.height * dpr);
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      context.clearRect(0, 0, width, height);
      if (!geometry) return;

      let progress = 1;
      if (transition) {
        const elapsed = Math.max(0, performance.now() - transition.startedAt);
        const ratio = Math.min(1, elapsed / transition.durationMs);
        progress = ratio * ratio * (3 - 2 * ratio);
        if (ratio >= 1) transition = null;
        else if (!transitionFrame) {
          transitionFrame = requestAnimationFrame(() => {
            transitionFrame = 0;
            render();
          });
        }
      }

      const { positions, triangles, bounds } = geometry;
      const center = geometry.orbitCenter || bounds.min.map((value, axis) => value / 2 + bounds.max[axis] / 2);
      const extent = bounds.min.map((value, axis) => bounds.max[axis] - value);
      const radius = geometry.fitRadius || Math.max(Math.hypot(...extent) / 2, 0.001);
      const scale = (Math.min(width, height) * 0.43 * zoom) / radius;
      const cosYaw = Math.cos(yaw);
      const sinYaw = Math.sin(yaw);
      const cosPitch = Math.cos(pitch);
      const sinPitch = Math.sin(pitch);
      const project = (worldX, worldY, worldZ) => {
        const x = worldX - center[0];
        const y = worldY - center[1];
        const z = worldZ - center[2];
        const rotatedX = x * cosYaw - y * sinYaw;
        const rotatedDepth = x * sinYaw + y * cosYaw;
        const cameraY = z * cosPitch - rotatedDepth * sinPitch;
        return [width / 2 + rotatedX * scale, height / 2 - cameraY * scale];
      };
      for (let index = 0; index < positions.length; index += 3) {
        let worldX = positions[index];
        let worldY = positions[index + 1];
        let worldZ = positions[index + 2];
        const vertex = index / 3;
        if (transition?.vertexRange && vertex >= transition.vertexRange[0] && vertex < transition.vertexRange[1]) {
          if (transition.type === "revolve") {
            worldX *= progress;
            worldY *= progress;
          } else if (transition.type === "extrude") {
            const axis = transition.plane === "XZ" ? 1 : 2;
            if (axis === 1) worldY *= progress;
            else worldZ *= progress;
          }
        }
        const x = worldX - center[0];
        const y = worldY - center[1];
        const z = worldZ - center[2];
        // CAD model coordinates: XY is horizontal, Z is up.
        const rotatedX = x * cosYaw - y * sinYaw;
        const rotatedDepth = x * sinYaw + y * cosYaw;
        const cameraY = z * cosPitch - rotatedDepth * sinPitch;
        const cameraZ = z * sinPitch + rotatedDepth * cosPitch;
        camera[index] = rotatedX;
        camera[index + 1] = cameraY;
        camera[index + 2] = cameraZ;
        screenX[vertex] = width / 2 + rotatedX * scale;
        screenY[vertex] = height / 2 - cameraY * scale;
      }

      if (triangles.length) renderMesh(
        triangles, camera, screenX, screenY, progress, sweepProgress,
        geometry.vertexNormals, yaw, pitch,
      );
      else if (!geometry.lines) renderPoints(positions.length / 3, camera, screenX, screenY, scale);
      for (let index = 0; index < (geometry.lines || []).length; index += 2) {
        const a = geometry.lines[index], b = geometry.lines[index + 1];
        let segmentProgress = 1;
        if (transition?.lineRange && index >= transition.lineRange[0] && index < transition.lineRange[1]) {
          const segment = (index - transition.lineRange[0]) / 2;
          const segments = (transition.lineRange[1] - transition.lineRange[0]) / 2;
          segmentProgress = Math.max(0, Math.min(1, progress * segments - segment));
          if (segmentProgress <= 0) continue;
        }
        context.strokeStyle = segmentProgress < 1 ? "#b8f2ff" : "#8ed4ed";
        context.globalAlpha = segmentProgress < 1 ? 0.72 + 0.28 * segmentProgress : 1;
        context.lineWidth = segmentProgress < 1 ? 2.4 : 2;
        context.beginPath();
        context.moveTo(screenX[a], screenY[a]);
        context.lineTo(screenX[a] + (screenX[b] - screenX[a]) * segmentProgress,
          screenY[a] + (screenY[b] - screenY[a]) * segmentProgress);
        context.stroke();
      }
      context.globalAlpha = 1;
      context.fillStyle = "#c3ecff";
      for (const point of geometry.points || []) {
        const forming = transition?.pointIndex === point;
        const size = forming ? 4 * Math.max(0.05, progress) : 4;
        context.globalAlpha = forming ? 0.2 + 0.8 * progress : 1;
        context.fillRect(screenX[point] - size / 2, screenY[point] - size / 2, size, size);
      }
      context.globalAlpha = 1;
      drawRotationAxes(geometry.rotationAxes || [], project, dpr);
      drawCoordinateGizmo(width, height, dpr, cosYaw, sinYaw, cosPitch, sinPitch);
    }

    function drawRotationAxes(axes, project, dpr) {
      for (const axis of axes) {
        const start = project(...axis.start);
        const end = project(...axis.end);
        context.save();
        context.strokeStyle = "#e9bd6e";
        context.fillStyle = "#f0cf91";
        context.globalAlpha = 0.9;
        context.lineWidth = 1.4 * dpr;
        context.setLineDash([5 * dpr, 4 * dpr]);
        context.beginPath();
        context.moveTo(...start);
        context.lineTo(...end);
        context.stroke();
        context.setLineDash([]);
        context.font = `600 ${10 * dpr}px Segoe UI, Arial, sans-serif`;
        context.textBaseline = "middle";
        context.fillText("Z · ось вращения", end[0] + 6 * dpr, end[1] - 2 * dpr);
        context.restore();
      }
    }

    function drawCoordinateGizmo(width, height, dpr, cosYaw, sinYaw, cosPitch, sinPitch) {
      const left = 12 * dpr;
      const top = height - 70 * dpr;
      const originX = left + 31 * dpr;
      const originY = top + 40 * dpr;
      context.save();
      context.globalAlpha = 0.94;
      context.fillStyle = "rgba(17, 20, 23, 0.82)";
      context.fillRect(left, top, 62 * dpr, 58 * dpr);
      context.strokeStyle = "#30383e";
      context.lineWidth = dpr;
      context.strokeRect(left + 0.5 * dpr, top + 0.5 * dpr, 61 * dpr, 57 * dpr);
      context.font = `600 ${10 * dpr}px Segoe UI, Arial, sans-serif`;
      context.textBaseline = "middle";
      const axes = [
        { name: "X", color: "#ef827b", world: [1, 0, 0] },
        { name: "Y", color: "#71d7bb", world: [0, 1, 0] },
        { name: "Z", color: "#8ecbff", world: [0, 0, 1] },
      ];
      for (const axis of axes) {
        const [x, y, z] = axis.world;
        const screenX = x * cosYaw - y * sinYaw;
        const screenY = -(z * cosPitch - (x * sinYaw + y * cosYaw) * sinPitch);
        const magnitude = Math.hypot(screenX, screenY);
        const directionX = magnitude > 0.08 ? screenX / magnitude : 0;
        const directionY = magnitude > 0.08 ? screenY / magnitude : -1;
        const length = (magnitude > 0.08 ? 20 : 5) * dpr;
        const endX = originX + directionX * length;
        const endY = originY + directionY * length;
        context.strokeStyle = axis.color;
        context.fillStyle = axis.color;
        context.lineWidth = 2 * dpr;
        context.beginPath();
        context.moveTo(originX, originY);
        context.lineTo(endX, endY);
        context.stroke();
        context.fillText(axis.name, endX + (directionX >= 0 ? 3 : -9) * dpr,
          endY + (directionY >= 0 ? 7 : -4) * dpr);
      }
      context.restore();
    }

    function renderMesh(triangles, camera, screenX, screenY, progress, sweepProgress,
      vertexNormals, yaw, pitch) {
      const faceTotal = triangles.length / 3;
      const light = [0.34, 0.68, 0.65];
      const cosYaw = Math.cos(yaw), sinYaw = Math.sin(yaw);
      const cosPitch = Math.cos(pitch), sinPitch = Math.sin(pitch);
      const smooth = vertexNormals && (!transition || progress >= 1);
      if (smooth) {
        for (let vertex = 0; vertex < vertexLight.length; vertex += 1) {
          const worldX = vertexNormals[vertex * 3], worldY = vertexNormals[vertex * 3 + 1];
          const worldZ = vertexNormals[vertex * 3 + 2];
          const rotatedX = worldX * cosYaw - worldY * sinYaw;
          const rotatedDepth = worldX * sinYaw + worldY * cosYaw;
          const normalY = worldZ * cosPitch - rotatedDepth * sinPitch;
          const normalZ = worldZ * sinPitch + rotatedDepth * cosPitch;
          vertexLight[vertex] = 0.34 + 0.66 * Math.abs(
            rotatedX * light[0] + normalY * light[1] + normalZ * light[2],
          );
        }
      }
      let visibleCount = 0;

      for (let face = 0; face < faceTotal; face += 1) {
        const first = triangles[face * 3];
        const second = triangles[face * 3 + 1];
        const third = triangles[face * 3 + 2];
        if (sweepProgress && progress < 1
            && (sweepProgress[first] + sweepProgress[second] + sweepProgress[third]) / 3 > progress) continue;
        const a = first * 3;
        const b = second * 3;
        const c = third * 3;
        const az = camera[a + 2], bz = camera[b + 2], cz = camera[c + 2];
        let brightness;
        if (smooth) {
          brightness = (vertexLight[first] + vertexLight[second] + vertexLight[third]) / 3;
        } else {
          const ax = camera[a], ay = camera[a + 1], bx = camera[b], by = camera[b + 1];
          const cx = camera[c], cy = camera[c + 1];
          const ux = bx - ax, uy = by - ay, uz = bz - az;
          const vx = cx - ax, vy = cy - ay, vz = cz - az;
          const nx = uy * vz - uz * vy;
          const ny = uz * vx - ux * vz;
          const nz = ux * vy - uy * vx;
          const normalLength = Math.hypot(nx, ny, nz) || 1;
          brightness = 0.34 + 0.66 * Math.abs(
            (nx * light[0] + ny * light[1] + nz * light[2]) / normalLength,
          );
        }
        const offset = face * 8;
        faceRecords[offset] = screenX[first];
        faceRecords[offset + 1] = screenY[first];
        faceRecords[offset + 2] = screenX[second];
        faceRecords[offset + 3] = screenY[second];
        faceRecords[offset + 4] = screenX[third];
        faceRecords[offset + 5] = screenY[third];
        faceRecords[offset + 6] = (az + bz + cz) / 3;
        faceRecords[offset + 7] = brightness;
        faceOrder[visibleCount] = face;
        visibleCount += 1;
      }

      const visibleFaces = faceOrder.subarray(0, visibleCount);
      visibleFaces.sort((left, right) => faceRecords[left * 8 + 6] - faceRecords[right * 8 + 6]);
      for (const face of visibleFaces) {
        const offset = face * 8;
        const brightness = faceRecords[offset + 7];
        const first = triangles[face * 3];
        const second = triangles[face * 3 + 1];
        const third = triangles[face * 3 + 2];
        const green = Math.round(105 + brightness * 87);
        let red = Math.round(green * 0.69), blue = Math.round(green * 0.53), finalGreen = green;
        const forming = transition?.vertexRange
          && [first, second, third].every((vertex) => vertex >= transition.vertexRange[0] && vertex < transition.vertexRange[1]);
        if (forming && progress < 1) {
          const glow = (1 - progress) * 0.38;
          red = Math.round(red + (112 - red) * glow);
          finalGreen = Math.round(finalGreen + (218 - finalGreen) * glow);
          blue = Math.round(blue + (236 - blue) * glow);
        }
        context.fillStyle = `rgb(${red}, ${finalGreen}, ${blue})`;
        context.beginPath();
        context.moveTo(faceRecords[offset], faceRecords[offset + 1]);
        context.lineTo(faceRecords[offset + 2], faceRecords[offset + 3]);
        context.lineTo(faceRecords[offset + 4], faceRecords[offset + 5]);
        context.closePath();
        context.fill();
      }
    }

    function renderPoints(pointCount, camera, screenX, screenY, scale) {
      const step = Math.max(1, Math.ceil(pointCount / MAX_PREVIEW_POINTS));
      const pointSize = Math.max(1.2, Math.min(2.4, scale * 0.003));
      for (let point = 0; point < pointCount; point += step) {
        const depth = camera[point * 3 + 2];
        const shade = Math.max(0, Math.min(1, 0.5 + depth / (scale * 0.8)));
        const green = Math.round(133 + shade * 86);
        context.fillStyle = `rgba(${Math.round(green * 0.65)}, ${green}, ${Math.round(green * 0.7)}, 0.82)`;
        context.fillRect(screenX[point], screenY[point], pointSize, pointSize);
      }
    }

    canvas.addEventListener("pointerdown", (event) => {
      drag = { x: event.clientX, y: event.clientY };
      canvas.setPointerCapture(event.pointerId);
      canvas.classList.add("dragging");
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const sensitivity = (Math.PI * 2) / Math.max(canvas.clientHeight, 1);
      yaw -= (event.clientX - drag.x) * sensitivity;
      pitch = Math.max(-MAX_CAMERA_PITCH, Math.min(MAX_CAMERA_PITCH, pitch + (event.clientY - drag.y) * sensitivity));
      drag = { x: event.clientX, y: event.clientY };
      scheduleCameraRender();
    });
    const stopDrag = () => {
      drag = null;
      canvas.classList.remove("dragging");
    };
    canvas.addEventListener("pointerup", stopDrag);
    canvas.addEventListener("pointercancel", stopDrag);
    canvas.addEventListener("dblclick", () => {
      yaw = 0.68;
      pitch = 0.38;
      zoom = 1;
      render();
    });
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      zoom = Math.max(0.25, Math.min(5, zoom * Math.exp(-event.deltaY * 0.001)));
      scheduleCameraRender();
    }, { passive: false });
    new ResizeObserver(render).observe(canvas);

    function resetView() {
      yaw = 0.68;
      pitch = 0.38;
      zoom = 1;
    }

    return {
      fit() { resetView(); render(); },
      getCamera() { return { yaw, pitch, zoom, projection: "orthographic" }; },
      setCamera(value) {
        if (!value || ![value.yaw, value.pitch, value.zoom].every(Number.isFinite)
            || Math.abs(value.pitch) > MAX_CAMERA_PITCH || value.zoom < 0.25 || value.zoom > 5
            || value.projection !== "orthographic") {
          throw new Error("Invalid orthographic camera.");
        }
        ({ yaw, pitch, zoom } = value);
        render();
      },
      load(value, { reset = true, transition: nextTransition = null, smoothShading = false } = {}) {
        const orbitCenter = orbitCenterFor(value);
        geometry = {
          ...value,
          orbitCenter,
          fitRadius: orbitCenter ? fitRadiusFromBounds(value.bounds, orbitCenter) : null,
          vertexNormals: smoothShading ? buildVertexNormals(value.positions, value.triangles || []) : null,
        };
        vertexLight = geometry.vertexNormals ? new Float32Array(geometry.vertexNormals.length / 3) : new Float32Array(0);
        transition = nextTransition ? {
          ...nextTransition,
          startedAt: performance.now(),
          durationMs: Math.max(1, nextTransition.durationMs || 500),
        } : null;
        transitionFrame = 0;
        sweepProgress = transition?.type === "sweep" && transition.path?.length >= 2
          ? buildSweepProgress(value.positions, transition.path, transition.plane)
          : null;
        camera = new Float64Array(value.positions.length);
        screenX = new Float64Array(value.positions.length / 3);
        screenY = new Float64Array(value.positions.length / 3);
        faceRecords = new Float64Array((value.triangles.length / 3) * 8);
        faceOrder = new Uint32Array(value.triangles.length / 3);
        if (reset) resetView();
        render();
      },
      render,
    };

    function buildSweepProgress(positions, path, plane) {
      const worldPath = path.map((point) => plane === "XZ"
        ? [point.x, 0, point.y] : [point.x, point.y, 0]);
      const cumulative = [0];
      for (let i = 1; i < worldPath.length; i += 1) {
        cumulative.push(cumulative[i - 1] + Math.hypot(
          worldPath[i][0] - worldPath[i - 1][0],
          worldPath[i][1] - worldPath[i - 1][1],
          worldPath[i][2] - worldPath[i - 1][2],
        ));
      }
      const totalLength = cumulative.at(-1) || 1;
      const perVertex = new Float32Array(positions.length / 3);
      for (let vertex = 0; vertex < perVertex.length; vertex += 1) {
        const px = positions[vertex * 3], py = positions[vertex * 3 + 1], pz = positions[vertex * 3 + 2];
        let nearest = Infinity, nearestLength = 0;
        for (let segment = 0; segment + 1 < worldPath.length; segment += 1) {
          const a = worldPath[segment], b = worldPath[segment + 1];
          const dx = b[0] - a[0], dy = b[1] - a[1], dz = b[2] - a[2];
          const lengthSquared = dx * dx + dy * dy + dz * dz;
          const t = Math.max(0, Math.min(1, ((px - a[0]) * dx + (py - a[1]) * dy + (pz - a[2]) * dz) / lengthSquared));
          const qx = a[0] + dx * t, qy = a[1] + dy * t, qz = a[2] + dz * t;
          const distance = (px - qx) ** 2 + (py - qy) ** 2 + (pz - qz) ** 2;
          if (distance < nearest) {
            nearest = distance;
            nearestLength = cumulative[segment] + Math.sqrt(lengthSquared) * t;
          }
        }
        perVertex[vertex] = nearestLength / totalLength;
      }
      return perVertex;
    }

    function buildVertexNormals(positions, triangles) {
      if (!triangles.length) return null;
      const normals = new Float64Array(positions.length);
      for (let face = 0; face < triangles.length; face += 3) {
        const first = triangles[face] * 3, second = triangles[face + 1] * 3, third = triangles[face + 2] * 3;
        const ux = positions[second] - positions[first];
        const uy = positions[second + 1] - positions[first + 1];
        const uz = positions[second + 2] - positions[first + 2];
        const vx = positions[third] - positions[first];
        const vy = positions[third + 1] - positions[first + 1];
        const vz = positions[third + 2] - positions[first + 2];
        const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
        for (const index of [first, second, third]) {
          normals[index] += nx;
          normals[index + 1] += ny;
          normals[index + 2] += nz;
        }
      }
      for (let index = 0; index < normals.length; index += 3) {
        const length = Math.hypot(normals[index], normals[index + 1], normals[index + 2]) || 1;
        normals[index] /= length;
        normals[index + 1] /= length;
        normals[index + 2] /= length;
      }
      return normals;
    }
  }

  window.CadyxViewport = { createViewer };
})();
