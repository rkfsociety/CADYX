(() => {
  const MAX_PREVIEW_POINTS = 24000;
  const meshEdgesCache = new WeakMap();
  const multiplyMatrices = (left, right) => [
    left[0] * right[0] + left[1] * right[3] + left[2] * right[6],
    left[0] * right[1] + left[1] * right[4] + left[2] * right[7],
    left[0] * right[2] + left[1] * right[5] + left[2] * right[8],
    left[3] * right[0] + left[4] * right[3] + left[5] * right[6],
    left[3] * right[1] + left[4] * right[4] + left[5] * right[7],
    left[3] * right[2] + left[4] * right[5] + left[5] * right[8],
    left[6] * right[0] + left[7] * right[3] + left[8] * right[6],
    left[6] * right[1] + left[7] * right[4] + left[8] * right[7],
    left[6] * right[2] + left[7] * right[5] + left[8] * right[8],
  ];
  const rotationX = (angle) => {
    const cosine = Math.cos(angle), sine = Math.sin(angle);
    return [1, 0, 0, 0, cosine, -sine, 0, sine, cosine];
  };
  const rotationZ = (angle) => {
    const cosine = Math.cos(angle), sine = Math.sin(angle);
    return [cosine, -sine, 0, sine, cosine, 0, 0, 0, 1];
  };
  function cameraMatrixFromYawPitch(yaw, pitch) {
    return multiplyMatrices(rotationX(-pitch), rotationZ(yaw));
  }
  function rotationBetween(from, to) {
    let axis = [
      from[1] * to[2] - from[2] * to[1],
      from[2] * to[0] - from[0] * to[2],
      from[0] * to[1] - from[1] * to[0],
    ];
    const cosine = Math.max(-1, Math.min(1,
      from[0] * to[0] + from[1] * to[1] + from[2] * to[2]));
    let sine = Math.hypot(...axis);
    if (sine < 1e-10) {
      if (cosine > 0) return [1, 0, 0, 0, 1, 0, 0, 0, 1];
      axis = Math.abs(from[0]) < 0.8
        ? [0, from[2], -from[1]] : [-from[2], 0, from[0]];
      sine = Math.hypot(...axis);
    }
    axis = axis.map((value) => value / sine);
    const angleSine = cosine < -0.999999 ? 0 : sine;
    const angleCosine = cosine < -0.999999 ? -1 : cosine;
    const [x, y, z] = axis;
    const complement = 1 - angleCosine;
    return [
      angleCosine + complement * x * x,
      complement * x * y - angleSine * z,
      complement * x * z + angleSine * y,
      complement * y * x + angleSine * z,
      angleCosine + complement * y * y,
      complement * y * z - angleSine * x,
      complement * z * x - angleSine * y,
      complement * z * y + angleSine * x,
      angleCosine + complement * z * z,
    ];
  }
  function trackballPoint(clientX, clientY, rect) {
    const radius = Math.max(1, Math.min(rect.width, rect.height) / 2);
    const x = (clientX - rect.left - rect.width / 2) / radius;
    const up = (rect.top + rect.height / 2 - clientY) / radius;
    const distance = Math.hypot(x, up);
    if (distance < 1e-10) return [0, 1, 0];
    const angle = Math.PI * distance;
    const radial = Math.sin(angle) / distance;
    return [x * radial, Math.cos(angle), up * radial];
  }
  function isRotationMatrix(matrix) {
    if (!matrix || matrix.length !== 9 || !Array.from(matrix).every(Number.isFinite)) return false;
    for (let row = 0; row < 3; row += 1) {
      for (let other = row; other < 3; other += 1) {
        const dot = matrix[row * 3] * matrix[other * 3]
          + matrix[row * 3 + 1] * matrix[other * 3 + 1]
          + matrix[row * 3 + 2] * matrix[other * 3 + 2];
        if (Math.abs(dot - (row === other ? 1 : 0)) > 1e-5) return false;
      }
    }
    const determinant = matrix[0] * (matrix[4] * matrix[8] - matrix[5] * matrix[7])
      - matrix[1] * (matrix[3] * matrix[8] - matrix[5] * matrix[6])
      + matrix[2] * (matrix[3] * matrix[7] - matrix[4] * matrix[6]);
    return Math.abs(determinant - 1) <= 1e-5;
  }
  function createViewer(canvas, {
    showViewCube = false,
    showCoordinateGizmo = true,
    showRotationAxes = true,
    wireframe = false,
    shaded = true,
    wireframeColor = "#69d9c4",
    wireframeOpacity = 0.46,
    onRender = null,
  } = {}) {
    const context = canvas.getContext("2d", { alpha: true });
    let geometry = null;
    let cameraOrientation = cameraMatrixFromYawPitch(0.68, 0.38);
    let zoom = 1;
    let drag = null;
    let camera = new Float64Array(0);
    let screenX = new Float64Array(0);
    let screenY = new Float64Array(0);
    let faceRecords = new Float64Array(0);
    let faceOrder = new Uint32Array(0);
    let faceFacing = new Int8Array(0);
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

    function fitRadiusFromPositions(positions, center) {
      let radius = 0;
      for (let index = 0; index < positions.length; index += 3) {
        radius = Math.max(radius, Math.hypot(
          positions[index] - center[0],
          positions[index + 1] - center[1],
          positions[index + 2] - center[2],
        ));
      }
      return Math.max(radius, 0.001);
    }

    function buildFeatureEdges(positions, triangles) {
      const vertexCount = positions.length / 3;
      const edges = new Map();
      const creaseCosine = Math.cos(Math.PI / 18);
      const addEdge = (start, end, normalX, normalY, normalZ, faceIndex) => {
        const low = Math.min(start, end), high = Math.max(start, end);
        const key = low * vertexCount + high;
        const edge = edges.get(key);
        if (!edge) {
          edges.set(key, {
            start: low, end: high, normalX, normalY, normalZ,
            firstFace: faceIndex, secondFace: -1, count: 1, crease: false,
          });
          return;
        }
        const cosine = edge.normalX * normalX + edge.normalY * normalY + edge.normalZ * normalZ;
        if (cosine < creaseCosine) edge.crease = true;
        edge.count += 1;
        if (edge.count === 2) edge.secondFace = faceIndex;
      };
      for (let face = 0; face + 2 < triangles.length; face += 3) {
        const a = triangles[face], b = triangles[face + 1], c = triangles[face + 2];
        const ai = a * 3, bi = b * 3, ci = c * 3;
        const ux = positions[bi] - positions[ai];
        const uy = positions[bi + 1] - positions[ai + 1];
        const uz = positions[bi + 2] - positions[ai + 2];
        const vx = positions[ci] - positions[ai];
        const vy = positions[ci + 1] - positions[ai + 1];
        const vz = positions[ci + 2] - positions[ai + 2];
        const nx = uy * vz - uz * vy;
        const ny = uz * vx - ux * vz;
        const nz = ux * vy - uy * vx;
        const length = Math.hypot(nx, ny, nz);
        if (!length) continue;
        const normalX = nx / length, normalY = ny / length, normalZ = nz / length;
        const faceIndex = face / 3;
        addEdge(a, b, normalX, normalY, normalZ, faceIndex);
        addEdge(b, c, normalX, normalY, normalZ, faceIndex);
        addEdge(c, a, normalX, normalY, normalZ, faceIndex);
      }
      const featureEdges = [];
      const silhouetteCandidates = [];
      for (const edge of edges.values()) {
        if (edge.count === 1 || edge.crease) featureEdges.push(edge.start, edge.end);
        else if (edge.count === 2) {
          silhouetteCandidates.push(edge.start, edge.end, edge.firstFace, edge.secondFace);
        }
      }
      return {
        featureEdges: Uint32Array.from(featureEdges),
        silhouetteCandidates: Uint32Array.from(silhouetteCandidates),
      };
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
      if (!geometry) {
        onRender?.();
        return;
      }

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
      const project = (worldX, worldY, worldZ) => {
        const x = worldX - center[0];
        const y = worldY - center[1];
        const z = worldZ - center[2];
        const right = cameraOrientation[0] * x + cameraOrientation[1] * y + cameraOrientation[2] * z;
        const up = cameraOrientation[6] * x + cameraOrientation[7] * y + cameraOrientation[8] * z;
        return [width / 2 + right * scale, height / 2 - up * scale];
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
          } else if (transition.type === "thread") {
            worldZ *= progress;
          }
        }
        const x = worldX - center[0];
        const y = worldY - center[1];
        const z = worldZ - center[2];
        const right = cameraOrientation[0] * x + cameraOrientation[1] * y + cameraOrientation[2] * z;
        const depth = cameraOrientation[3] * x + cameraOrientation[4] * y + cameraOrientation[5] * z;
        const up = cameraOrientation[6] * x + cameraOrientation[7] * y + cameraOrientation[8] * z;
        camera[index] = right;
        camera[index + 1] = up;
        camera[index + 2] = depth;
        screenX[vertex] = width / 2 + right * scale;
        screenY[vertex] = height / 2 - up * scale;
      }

      if (triangles.length) renderMesh(
        triangles, camera, screenX, screenY, progress, sweepProgress,
        geometry.vertexNormals, cameraOrientation, geometry.faceNormals, geometry.featureEdges,
        geometry.silhouetteCandidates, wireframe, shaded,
        wireframeColor, wireframeOpacity, dpr,
      );
      else if (!geometry.lines) renderPoints(positions.length / 3, camera, screenX, screenY, scale);
      for (let index = 0; index < (geometry.lines || []).length; index += 2) {
        const a = geometry.lines[index], b = geometry.lines[index + 1];
        let startX = screenX[a], startY = screenY[a];
        let endX = screenX[b], endY = screenY[b];
        const lineMode = geometry.line_modes?.[index / 2] || "segment";
        if (lineMode === "infinite") {
          const dx = endX - startX, dy = endY - startY;
          const length = Math.hypot(dx, dy);
          if (length > 1e-9) {
            const half = Math.hypot(width, height) * 1.5;
            const middleX = (startX + endX) / 2, middleY = (startY + endY) / 2;
            startX = middleX - dx / length * half;
            startY = middleY - dy / length * half;
            endX = middleX + dx / length * half;
            endY = middleY + dy / length * half;
          }
        }
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
        context.setLineDash(geometry.construction || lineMode === "centerline" ? [6, 5] : []);
        context.beginPath();
        context.moveTo(startX, startY);
        context.lineTo(startX + (endX - startX) * segmentProgress,
          startY + (endY - startY) * segmentProgress);
        context.stroke();
      }
      context.globalAlpha = 1;
      context.setLineDash([]);
      context.fillStyle = "#c3ecff";
      for (const point of geometry.points || []) {
        const forming = transition?.pointIndex === point;
        const size = forming ? 4 * Math.max(0.05, progress) : 4;
        context.globalAlpha = forming ? 0.2 + 0.8 * progress : 1;
        if (geometry.construction) {
          context.strokeStyle = "#c3ecff";
          context.lineWidth = 1.5;
          context.beginPath();
          context.arc(screenX[point], screenY[point], Math.max(2, size), 0, Math.PI * 2);
          context.stroke();
        } else {
          context.fillRect(screenX[point] - size / 2, screenY[point] - size / 2, size, size);
        }
      }
      context.globalAlpha = 1;
      if (showRotationAxes) drawRotationAxes(geometry.rotationAxes || [], project, dpr);
      if (showCoordinateGizmo) drawCoordinateGizmo(width, height, dpr, cameraOrientation);
      if (showViewCube) drawViewCube(width, dpr, cameraOrientation);
      onRender?.();
    }

    function createSnapshotBlob() {
      const snapshot = document.createElement("canvas");
      snapshot.width = canvas.width;
      snapshot.height = canvas.height;
      const context = snapshot.getContext("2d");
      if (!context || !snapshot.width || !snapshot.height) {
        return Promise.reject(new Error("Viewport canvas is not ready for a snapshot."));
      }
      const rect = canvas.getBoundingClientRect();
      const scaleX = snapshot.width / Math.max(rect.width, 1);
      const scaleY = snapshot.height / Math.max(rect.height, 1);
      const grid = 27 * scaleX;
      context.fillStyle = "#0b1114";
      context.fillRect(0, 0, snapshot.width, snapshot.height);
      context.fillStyle = "rgba(38, 50, 56, 0.3215686275)";
      for (let x = 0; x < snapshot.width; x += grid) {
        context.fillRect(Math.round(x), 0, Math.max(1, Math.round(scaleX)), snapshot.height);
      }
      for (let y = 0; y < snapshot.height; y += 27 * scaleY) {
        context.fillRect(0, Math.round(y), snapshot.width, Math.max(1, Math.round(scaleY)));
      }
      context.drawImage(canvas, 0, 0);
      context.fillStyle = "rgba(48, 56, 62, 0.2666666667)";
      context.fillRect(0, snapshot.height - Math.max(1, Math.round(scaleY)),
        snapshot.width, Math.max(1, Math.round(scaleY)));
      return new Promise((resolve, reject) => {
        snapshot.toBlob((blob) => {
          if (blob) resolve(blob);
          else reject(new Error("Browser could not encode the viewport snapshot."));
        }, "image/png");
      });
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

    function drawCoordinateGizmo(width, height, dpr, orientation) {
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
        const screenX = orientation[0] * x + orientation[1] * y + orientation[2] * z;
        const screenY = -(orientation[6] * x + orientation[7] * y + orientation[8] * z);
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

    function drawViewCube(width, dpr, orientation) {
      const centerX = width - 38 * dpr;
      const centerY = 42 * dpr;
      const halfSize = 16 * dpr;
      const transform = ([x, y, z]) => {
        const right = orientation[0] * x + orientation[1] * y + orientation[2] * z;
        const depth = orientation[3] * x + orientation[4] * y + orientation[5] * z;
        const up = orientation[6] * x + orientation[7] * y + orientation[8] * z;
        return { x: centerX + right * halfSize, y: centerY - up * halfSize, depth };
      };
      const faces = [
        { label: "X", normal: [1, 0, 0], points: [[1, -1, -1], [1, 1, -1], [1, 1, 1], [1, -1, 1]] },
        { label: "Y", normal: [0, 1, 0], points: [[-1, 1, -1], [1, 1, -1], [1, 1, 1], [-1, 1, 1]] },
        { label: "Z", normal: [0, 0, 1], points: [[-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]] },
        { label: "X", normal: [-1, 0, 0], points: [[-1, 1, -1], [-1, -1, -1], [-1, -1, 1], [-1, 1, 1]] },
        { label: "Y", normal: [0, -1, 0], points: [[1, -1, -1], [-1, -1, -1], [-1, -1, 1], [1, -1, 1]] },
        { label: "Z", normal: [0, 0, -1], points: [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1]] },
      ];
      const visibleFaces = faces.map((face) => ({
        ...face,
        transformedNormal: transform(face.normal),
        transformedPoints: face.points.map(transform),
      })).filter((face) => face.transformedNormal.depth > 0.02);
      visibleFaces.sort((left, right) => {
        const leftDepth = left.transformedPoints.reduce((total, point) => total + point.depth, 0);
        const rightDepth = right.transformedPoints.reduce((total, point) => total + point.depth, 0);
        return leftDepth - rightDepth;
      });
      context.save();
      context.lineWidth = dpr;
      context.font = `600 ${9 * dpr}px monospace`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      for (const face of visibleFaces) {
        const [first, ...rest] = face.transformedPoints;
        context.beginPath();
        context.moveTo(first.x, first.y);
        for (const point of rest) context.lineTo(point.x, point.y);
        context.closePath();
        context.fillStyle = "rgba(11, 19, 22, 0.91)";
        context.strokeStyle = "#70878d";
        context.fill();
        context.stroke();
        const labelX = face.transformedPoints.reduce((total, point) => total + point.x, 0) / 4;
        const labelY = face.transformedPoints.reduce((total, point) => total + point.y, 0) / 4;
        context.fillStyle = "#c6d4d8";
        context.fillText(face.label, labelX, labelY);
      }
      context.restore();
    }

    function renderMesh(triangles, camera, screenX, screenY, progress, sweepProgress,
      vertexNormals, orientation, faceNormals, featureEdges, silhouetteCandidates, showWireframe, shaded,
      wireframeColor, wireframeOpacity, dpr) {
      const faceTotal = triangles.length / 3;
      const light = [0.34, 0.68, 0.65];
      const smooth = vertexNormals && (!transition || progress >= 1);
      if (smooth) {
        for (let vertex = 0; vertex < vertexLight.length; vertex += 1) {
          const worldX = vertexNormals[vertex * 3], worldY = vertexNormals[vertex * 3 + 1];
          const worldZ = vertexNormals[vertex * 3 + 2];
          const right = orientation[0] * worldX + orientation[1] * worldY + orientation[2] * worldZ;
          const depth = orientation[3] * worldX + orientation[4] * worldY + orientation[5] * worldZ;
          const up = orientation[6] * worldX + orientation[7] * worldY + orientation[8] * worldZ;
          vertexLight[vertex] = 0.34 + 0.66 * Math.abs(
            right * light[0] + up * light[1] + depth * light[2],
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
        } else if (faceNormals && !transition) {
          const normal = face * 3;
          const worldX = faceNormals[normal], worldY = faceNormals[normal + 1];
          const worldZ = faceNormals[normal + 2];
          const right = orientation[0] * worldX + orientation[1] * worldY + orientation[2] * worldZ;
          const depth = orientation[3] * worldX + orientation[4] * worldY + orientation[5] * worldZ;
          const up = orientation[6] * worldX + orientation[7] * worldY + orientation[8] * worldZ;
          brightness = 0.34 + 0.66 * Math.abs(
            right * light[0] + up * light[1] + depth * light[2],
          );
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
      if (shaded) {
        for (const face of visibleFaces) {
          const offset = face * 8;
          const brightness = faceRecords[offset + 7];
          const first = triangles[face * 3];
          const second = triangles[face * 3 + 1];
          const third = triangles[face * 3 + 2];
          const steel = Math.round(78 + brightness * 85);
          let red = Math.round(steel * 0.94), blue = Math.round(steel * 1.08), finalGreen = steel;
          const vertexRange = transition?.vertexRange;
          const forming = vertexRange
            && first >= vertexRange[0] && first < vertexRange[1]
            && second >= vertexRange[0] && second < vertexRange[1]
            && third >= vertexRange[0] && third < vertexRange[1];
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
      if (showWireframe && featureEdges?.length) {
        context.save();
        context.strokeStyle = wireframeColor;
        context.globalAlpha = wireframeOpacity;
        context.lineWidth = Math.max(0.65, 0.72 * dpr);
        const edgesPerPath = 3500;
        for (let start = 0; start < featureEdges.length; start += edgesPerPath * 2) {
          context.beginPath();
          const end = Math.min(featureEdges.length, start + edgesPerPath * 2);
          for (let index = start; index < end; index += 2) {
            const a = featureEdges[index], b = featureEdges[index + 1];
            context.moveTo(screenX[a], screenY[a]);
            context.lineTo(screenX[b], screenY[b]);
          }
          context.stroke();
        }
        context.restore();
      }
      if (showWireframe && silhouetteCandidates?.length) {
        if (faceFacing.length < faceTotal) faceFacing = new Int8Array(faceTotal);
        for (let face = 0; face < faceTotal; face += 1) {
          const first = triangles[face * 3] * 3;
          const second = triangles[face * 3 + 1] * 3;
          const third = triangles[face * 3 + 2] * 3;
          const projectedArea = (camera[second] - camera[first]) * (camera[third + 1] - camera[first + 1])
            - (camera[second + 1] - camera[first + 1]) * (camera[third] - camera[first]);
          faceFacing[face] = Math.abs(projectedArea) <= 1e-10 ? 0 : Math.sign(projectedArea);
        }
        context.save();
        context.strokeStyle = wireframeColor;
        context.globalAlpha = wireframeOpacity;
        context.lineWidth = Math.max(0.65, 0.72 * dpr);
        context.beginPath();
        for (let index = 0; index < silhouetteCandidates.length; index += 4) {
          const firstFace = faceFacing[silhouetteCandidates[index + 2]];
          const secondFace = faceFacing[silhouetteCandidates[index + 3]];
          if (firstFace === secondFace || firstFace === 0 && secondFace === 0) continue;
          const start = silhouetteCandidates[index], end = silhouetteCandidates[index + 1];
          context.moveTo(screenX[start], screenY[start]);
          context.lineTo(screenX[end], screenY[end]);
        }
        context.stroke();
        context.restore();
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
      drag = { point: trackballPoint(event.clientX, event.clientY, canvas.getBoundingClientRect()) };
      canvas.setPointerCapture(event.pointerId);
      canvas.classList.add("dragging");
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const nextPoint = trackballPoint(event.clientX, event.clientY, canvas.getBoundingClientRect());
      cameraOrientation = multiplyMatrices(rotationBetween(drag.point, nextPoint), cameraOrientation);
      drag = { point: nextPoint };
      scheduleCameraRender();
    });
    const stopDrag = () => {
      drag = null;
      canvas.classList.remove("dragging");
    };
    canvas.addEventListener("pointerup", stopDrag);
    canvas.addEventListener("pointercancel", stopDrag);
    canvas.addEventListener("dblclick", () => {
      resetView();
      render();
    });
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      zoom = Math.max(0.25, Math.min(5, zoom * Math.exp(-event.deltaY * 0.001)));
      scheduleCameraRender();
    }, { passive: false });
    new ResizeObserver(render).observe(canvas);

    function resetView() {
      cameraOrientation = cameraMatrixFromYawPitch(0.68, 0.38);
      zoom = 1;
    }

    return {
      fit() { resetView(); render(); },
      createSnapshotBlob,
      getCamera() { return { orientation: cameraOrientation.slice(), zoom, projection: "orthographic" }; },
      setCamera(value) {
        if (!value || !Number.isFinite(value.zoom) || value.zoom < 0.25 || value.zoom > 5
            || value.projection !== "orthographic") {
          throw new Error("Invalid orthographic camera.");
        }
        if (value.orientation !== undefined) {
          if (!isRotationMatrix(value.orientation)) throw new Error("Invalid orthographic camera.");
          cameraOrientation = Array.from(value.orientation);
        } else {
          if (![value.yaw, value.pitch].every(Number.isFinite)) throw new Error("Invalid orthographic camera.");
          cameraOrientation = cameraMatrixFromYawPitch(value.yaw, value.pitch);
        }
        zoom = value.zoom;
        render();
      },
      orbitBy(yawDelta, pitchDelta, zoomFactor = 1) {
        if (![yawDelta, pitchDelta, zoomFactor].every(Number.isFinite) || zoomFactor <= 0) {
          throw new Error("Invalid orthographic camera change.");
        }
        cameraOrientation = multiplyMatrices(
          rotationX(-pitchDelta),
          multiplyMatrices(cameraOrientation, rotationZ(yawDelta)),
        );
        zoom = Math.max(0.25, Math.min(5, zoom * zoomFactor));
        render();
      },
      setWireframe(value) {
        wireframe = Boolean(value);
        render();
      },
      load(value, { reset = true, transition: nextTransition = null, smoothShading = false } = {}) {
        const orbitCenter = orbitCenterFor(value);
        const fitCenter = orbitCenter || value.bounds.min.map((min, axis) => min / 2 + value.bounds.max[axis] / 2);
        const sourceTriangles = value.triangles || [];
        let meshEdges = meshEdgesCache.get(sourceTriangles);
        if (!meshEdges) {
          meshEdges = buildFeatureEdges(value.positions, sourceTriangles);
          meshEdgesCache.set(sourceTriangles, meshEdges);
        }
        geometry = {
          ...value,
          orbitCenter,
          fitRadius: fitRadiusFromPositions(value.positions, fitCenter),
          ...meshEdges,
          vertexNormals: smoothShading ? buildVertexNormals(value.positions, value.triangles || []) : null,
          faceNormals: smoothShading ? null : buildFaceNormals(value.positions, value.triangles || []),
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
      const segments = [];
      let totalLength = 0;
      for (let index = 0; index + 1 < worldPath.length; index += 1) {
        const a = worldPath[index], b = worldPath[index + 1];
        const dx = b[0] - a[0], dy = b[1] - a[1], dz = b[2] - a[2];
        const lengthSquared = dx * dx + dy * dy + dz * dz;
        if (!lengthSquared) continue;
        const length = Math.sqrt(lengthSquared);
        segments.push({
          index,
          ax: a[0], ay: a[1], az: a[2], dx, dy, dz,
          lengthSquared, length, cumulativeLength: totalLength,
          minX: Math.min(a[0], b[0]), maxX: Math.max(a[0], b[0]),
          minY: Math.min(a[1], b[1]), maxY: Math.max(a[1], b[1]),
          minZ: Math.min(a[2], b[2]), maxZ: Math.max(a[2], b[2]),
          centerX: (a[0] + b[0]) / 2,
          centerY: (a[1] + b[1]) / 2,
          centerZ: (a[2] + b[2]) / 2,
        });
        totalLength += length;
      }
      totalLength ||= 1;
      const perVertex = new Float32Array(positions.length / 3);
      if (!segments.length) return perVertex;

      const tree = buildSweepSegmentTree(segments);
      const best = { distance: Infinity, length: 0, segmentIndex: Infinity };
      function distanceToBoundsSquared(node, px, py, pz) {
        const dx = px < node.minX ? node.minX - px : px > node.maxX ? px - node.maxX : 0;
        const dy = py < node.minY ? node.minY - py : py > node.maxY ? py - node.maxY : 0;
        const dz = pz < node.minZ ? node.minZ - pz : pz > node.maxZ ? pz - node.maxZ : 0;
        return dx * dx + dy * dy + dz * dz;
      }
      function searchTree(node, px, py, pz, boundsDistance) {
        if (boundsDistance > best.distance) return;
        if (node.segments) {
          for (const segment of node.segments) {
            const t = Math.max(0, Math.min(1, ((px - segment.ax) * segment.dx
              + (py - segment.ay) * segment.dy + (pz - segment.az) * segment.dz)
              / segment.lengthSquared));
            const qx = segment.ax + segment.dx * t;
            const qy = segment.ay + segment.dy * t;
            const qz = segment.az + segment.dz * t;
            const dx = px - qx, dy = py - qy, dz = pz - qz;
            const distance = dx * dx + dy * dy + dz * dz;
            if (distance < best.distance || (distance === best.distance && segment.index < best.segmentIndex)) {
              best.distance = distance;
              best.length = segment.cumulativeLength + segment.length * t;
              best.segmentIndex = segment.index;
            }
          }
          return;
        }
        const leftDistance = distanceToBoundsSquared(node.left, px, py, pz);
        const rightDistance = distanceToBoundsSquared(node.right, px, py, pz);
        if (leftDistance <= rightDistance) {
          searchTree(node.left, px, py, pz, leftDistance);
          searchTree(node.right, px, py, pz, rightDistance);
        } else {
          searchTree(node.right, px, py, pz, rightDistance);
          searchTree(node.left, px, py, pz, leftDistance);
        }
      }

      for (let vertex = 0; vertex < perVertex.length; vertex += 1) {
        const px = positions[vertex * 3], py = positions[vertex * 3 + 1], pz = positions[vertex * 3 + 2];
        best.distance = Infinity;
        best.length = 0;
        best.segmentIndex = Infinity;
        searchTree(tree, px, py, pz, distanceToBoundsSquared(tree, px, py, pz));
        perVertex[vertex] = best.length / totalLength;
      }
      return perVertex;
    }

    function buildSweepSegmentTree(segments) {
      const bounds = {
        minX: Infinity, maxX: -Infinity,
        minY: Infinity, maxY: -Infinity,
        minZ: Infinity, maxZ: -Infinity,
      };
      for (const segment of segments) {
        bounds.minX = Math.min(bounds.minX, segment.minX);
        bounds.maxX = Math.max(bounds.maxX, segment.maxX);
        bounds.minY = Math.min(bounds.minY, segment.minY);
        bounds.maxY = Math.max(bounds.maxY, segment.maxY);
        bounds.minZ = Math.min(bounds.minZ, segment.minZ);
        bounds.maxZ = Math.max(bounds.maxZ, segment.maxZ);
      }
      if (segments.length <= 8) return { ...bounds, segments };

      const spans = [bounds.maxX - bounds.minX, bounds.maxY - bounds.minY, bounds.maxZ - bounds.minZ];
      const axis = spans.indexOf(Math.max(...spans));
      const centerKey = ["centerX", "centerY", "centerZ"][axis];
      const ordered = segments.slice().sort((left, right) => left[centerKey] - right[centerKey]);
      const middle = Math.floor(ordered.length / 2);
      return {
        ...bounds,
        left: buildSweepSegmentTree(ordered.slice(0, middle)),
        right: buildSweepSegmentTree(ordered.slice(middle)),
      };
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
        normals[first] += nx;
        normals[first + 1] += ny;
        normals[first + 2] += nz;
        normals[second] += nx;
        normals[second + 1] += ny;
        normals[second + 2] += nz;
        normals[third] += nx;
        normals[third + 1] += ny;
        normals[third + 2] += nz;
      }
      for (let index = 0; index < normals.length; index += 3) {
        const length = Math.hypot(normals[index], normals[index + 1], normals[index + 2]) || 1;
        normals[index] /= length;
        normals[index + 1] /= length;
        normals[index + 2] /= length;
      }
      return normals;
    }

    function buildFaceNormals(positions, triangles) {
      if (!triangles.length) return null;
      const normals = new Float64Array(triangles.length);
      for (let face = 0; face < triangles.length; face += 3) {
        const first = triangles[face] * 3, second = triangles[face + 1] * 3, third = triangles[face + 2] * 3;
        const ux = positions[second] - positions[first];
        const uy = positions[second + 1] - positions[first + 1];
        const uz = positions[second + 2] - positions[first + 2];
        const vx = positions[third] - positions[first];
        const vy = positions[third + 1] - positions[first + 1];
        const vz = positions[third + 2] - positions[first + 2];
        const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
        const length = Math.hypot(nx, ny, nz) || 1;
        normals[face] = nx / length;
        normals[face + 1] = ny / length;
        normals[face + 2] = nz / length;
      }
      return normals;
    }
  }

  window.CadyxViewport = { createViewer };
})();
