(() => {
  const CAMERA_PRESETS = Object.freeze({
    isometric: Object.freeze({ yaw: Math.PI / 4, pitch: Math.atan(1 / Math.sqrt(2)) }),
    front: Object.freeze({ yaw: 0, pitch: 0 }),
    right: Object.freeze({ yaw: Math.PI / 2, pitch: 0 }),
    top: Object.freeze({ yaw: 0, pitch: Math.PI / 2 }),
  });

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

  function buildFeatureEdges(positions, triangles) {
    const vertexCount = positions.length / 3;
    const canonicalVertices = new Uint32Array(vertexCount);
    const canonicalByPosition = new Map();
    for (let vertex = 0; vertex < vertexCount; vertex += 1) {
      const offset = vertex * 3;
      const key = `${positions[offset]},${positions[offset + 1]},${positions[offset + 2]}`;
      let canonical = canonicalByPosition.get(key);
      if (canonical === undefined) {
        canonical = canonicalByPosition.size;
        canonicalByPosition.set(key, canonical);
      }
      canonicalVertices[vertex] = canonical;
    }
    const canonicalVertexCount = canonicalByPosition.size;
    const edges = new Map();
    const creaseCosine = Math.cos(Math.PI / 6);
    const addEdge = (start, end, normalX, normalY, normalZ, faceIndex) => {
      const canonicalStart = canonicalVertices[start], canonicalEnd = canonicalVertices[end];
      const low = Math.min(canonicalStart, canonicalEnd), high = Math.max(canonicalStart, canonicalEnd);
      const key = low * canonicalVertexCount + high;
      const edge = edges.get(key);
      if (!edge) {
        edges.set(key, { start, end, normalX, normalY, normalZ,
          firstFace: faceIndex, secondFace: -1, count: 1, crease: false });
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

  function rasterTriangleSetup(x0, y0, x1, y1, x2, y2, width, height) {
    const area = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0);
    if (Math.abs(area) < 1e-9) return null;
    const minX = Math.max(0, Math.floor(Math.min(x0, x1, x2)));
    const maxX = Math.min(width - 1, Math.ceil(Math.max(x0, x1, x2)));
    const minY = Math.max(0, Math.floor(Math.min(y0, y1, y2)));
    const maxY = Math.min(height - 1, Math.ceil(Math.max(y0, y1, y2)));
    if (minX > maxX || minY > maxY) return null;
    const inverseArea = 1 / area;
    const sampleX = minX + 0.5, sampleY = minY + 0.5;
    const rowW0 = ((x2 - x1) * (sampleY - y1) - (y2 - y1) * (sampleX - x1)) * inverseArea;
    const rowW1 = ((x0 - x2) * (sampleY - y2) - (y0 - y2) * (sampleX - x2)) * inverseArea;
    const rowW2 = 1 - rowW0 - rowW1;
    const w0StepX = -(y2 - y1) * inverseArea;
    const w1StepX = -(y0 - y2) * inverseArea;
    const w0StepY = (x2 - x1) * inverseArea;
    const w1StepY = (x0 - x2) * inverseArea;
    return {
      minX, maxX, minY, maxY, rowW0, rowW1, rowW2,
      w0StepX, w1StepX, w2StepX: -w0StepX - w1StepX,
      w0StepY, w1StepY, w2StepY: -w0StepY - w1StepY,
    };
  }

  window.CadyxViewportHelpers = Object.freeze({
    CAMERA_PRESETS,
    multiplyMatrices,
    cameraMatrixFromYawPitch,
    rotationBetween,
    trackballPoint,
    isRotationMatrix,
    buildFeatureEdges,
    rasterTriangleSetup,
  });
})();
