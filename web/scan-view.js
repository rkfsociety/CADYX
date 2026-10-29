(() => {
  const LIVE_PREVIEW_POINT_LIMIT = 8_000;
  const LIVE_TABLE_SURFACE_DISPLAY_HEIGHT = 2.5;
  const LIVE_MIN_OBJECT_PREVIEW_POINTS = 3;
  const MIN_SCAN_DEPTH_MM = 100;
  const MAX_SCAN_DEPTH_MM = 250;
  const TURNTABLE_DIAMETER_MM = 200;
  const TURNTABLE_THICKNESS_MM = 10;
  const TURNTABLE_RADIUS_MM = TURNTABLE_DIAMETER_MM / 2;
  const TURNTABLE_DEGREE_SCALE_ZERO_DEGREES = 90;
  const TURNTABLE_TILT_PIVOT_Z_MM = -25;
  const isScanDepthValid = depth => Number.isFinite(depth)
    && depth >= MIN_SCAN_DEPTH_MM && depth <= MAX_SCAN_DEPTH_MM;
  const shouldRunTableMapRansac = (priorMatchCount, frameSequence, lastSequence, mapPointCount = 4) =>
    lastSequence === 0 || frameSequence - lastSequence >= 8 && priorMatchCount < mapPointCount;
  const createMeshBuilder = () => ({ positions: [], triangles: [], triangleColors: [] });
  const addTriangle = (mesh, first, second, third, color) => {
    mesh.triangles.push(first, second, third);
    mesh.triangleColors.push(...color);
  };
  const addColoredBox = (mesh, min, max, color) => {
    const offset = mesh.positions.length / 3;
    mesh.positions.push(
      min[0], min[1], min[2], max[0], min[1], min[2], max[0], max[1], min[2], min[0], max[1], min[2],
      min[0], min[1], max[2], max[0], min[1], max[2], max[0], max[1], max[2], min[0], max[1], max[2],
    );
    for (const triangle of [
      [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
      [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
      [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5],
    ]) addTriangle(mesh, ...triangle.map(index => index + offset), color);
  };
  const addEllipticalCylinder = (mesh, radiusX, radiusY, bottomZ, topZ, segments, colors) => {
    const topCenter = mesh.positions.length / 3;
    mesh.positions.push(0, 0, topZ);
    const topRing = mesh.positions.length / 3;
    for (let index = 0; index < segments; index += 1) {
      const angle = 2 * Math.PI * index / segments;
      mesh.positions.push(radiusX * Math.cos(angle), radiusY * Math.sin(angle), topZ);
    }
    const bottomCenter = mesh.positions.length / 3;
    mesh.positions.push(0, 0, bottomZ);
    const bottomRing = mesh.positions.length / 3;
    for (let index = 0; index < segments; index += 1) {
      const angle = 2 * Math.PI * index / segments;
      mesh.positions.push(radiusX * Math.cos(angle), radiusY * Math.sin(angle), bottomZ);
    }
    for (let index = 0; index < segments; index += 1) {
      const next = (index + 1) % segments;
      addTriangle(mesh, topCenter, topRing + index, topRing + next, colors.top);
      addTriangle(mesh, bottomCenter, bottomRing + next, bottomRing + index, colors.bottom);
      addTriangle(mesh, topRing + index, bottomRing + index, bottomRing + next, colors.side);
      addTriangle(mesh, topRing + index, bottomRing + next, topRing + next, colors.side);
    }
  };
  const addCylinderAlongX = (mesh, length, radius, centerZ, segments, color) => {
    const leftCenter = mesh.positions.length / 3;
    mesh.positions.push(-length / 2, 0, centerZ);
    const leftRing = mesh.positions.length / 3;
    for (let index = 0; index < segments; index += 1) {
      const angle = 2 * Math.PI * index / segments;
      mesh.positions.push(-length / 2, radius * Math.cos(angle), centerZ + radius * Math.sin(angle));
    }
    const rightCenter = mesh.positions.length / 3;
    mesh.positions.push(length / 2, 0, centerZ);
    const rightRing = mesh.positions.length / 3;
    for (let index = 0; index < segments; index += 1) {
      const angle = 2 * Math.PI * index / segments;
      mesh.positions.push(length / 2, radius * Math.cos(angle), centerZ + radius * Math.sin(angle));
    }
    for (let index = 0; index < segments; index += 1) {
      const next = (index + 1) % segments;
      addTriangle(mesh, leftCenter, leftRing + next, leftRing + index, color);
      addTriangle(mesh, rightCenter, rightRing + index, rightRing + next, color);
      addTriangle(mesh, leftRing + index, leftRing + next, rightRing + next, color);
      addTriangle(mesh, leftRing + index, rightRing + next, rightRing + index, color);
    }
  };
  const finishMesh = mesh => {
    const positions = Float32Array.from(mesh.positions);
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    for (let index = 0; index < positions.length; index += 3) {
      for (let axis = 0; axis < 3; axis += 1) {
        min[axis] = Math.min(min[axis], positions[index + axis]);
        max[axis] = Math.max(max[axis], positions[index + axis]);
      }
    }
    return { positions, triangles: Uint32Array.from(mesh.triangles),
      triangleColors: Uint8Array.from(mesh.triangleColors), bounds: { min, max } };
  };
  const buildTurntableDisplayModel = () => {
    const fixed = createMeshBuilder(), moving = createMeshBuilder();
    const dark = { top: [68, 76, 81], side: [42, 49, 54], bottom: [33, 39, 43] };
    addEllipticalCylinder(fixed, 86, 86, -70, -55, 72,
      { top: [55, 63, 68], side: [31, 38, 42], bottom: [25, 31, 35] });
    addEllipticalCylinder(fixed, 43, 43, -55, -30, 56,
      { top: [54, 62, 67], side: [36, 43, 47], bottom: [31, 37, 41] });
    addColoredBox(fixed, [-55, -18, -55], [-38, 18, -25], [39, 46, 50]);
    addColoredBox(fixed, [38, -18, -55], [55, 18, -25], [39, 46, 50]);
    addCylinderAlongX(fixed, 110, 13, TURNTABLE_TILT_PIVOT_Z_MM, 48, [55, 63, 68]);
    addColoredBox(fixed, [-57, -6.5, -31.5], [-55, 6.5, -18.5], [92, 103, 108]);
    addColoredBox(fixed, [-58.5, -4, -29], [-57, 4, -21], [19, 26, 29]);
    addEllipticalCylinder(moving, 27, 27, -25, -10, 48,
      { top: [71, 80, 85], side: [46, 54, 59], bottom: [35, 42, 47] });
    addEllipticalCylinder(moving, TURNTABLE_RADIUS_MM, TURNTABLE_RADIUS_MM,
      -TURNTABLE_THICKNESS_MM, 0, 96, dark);
    const fixedModel = finishMesh(fixed);
    for (let index = 0; index < fixedModel.positions.length; index += 3) {
      const x = fixedModel.positions[index], y = fixedModel.positions[index + 1];
      fixedModel.positions[index] = y;
      fixedModel.positions[index + 1] = -x;
    }
    fixedModel.bounds = {
      min: [fixedModel.bounds.min[1], -fixedModel.bounds.max[0], fixedModel.bounds.min[2]],
      max: [fixedModel.bounds.max[1], -fixedModel.bounds.min[0], fixedModel.bounds.max[2]],
    };
    const degreeLabelPositions = [], degreeTextLabels = [];
    let degreeTickCount = 0;
    for (let degrees = 0; degrees < 360; degrees += 5) {
      const radians = (degrees + TURNTABLE_DEGREE_SCALE_ZERO_DEGREES) * Math.PI / 180;
      const cosine = Math.cos(radians), sine = Math.sin(radians);
      const major = degrees % 30 === 0;
      const innerRadius = major ? 93.5 : 97;
      const outerRadius = 99.2;
      const halfWidth = major ? 0.55 : 0.28;
      const z = 0.12;
      const point = (radius, tangent) => [
        radius * cosine - tangent * sine,
        radius * sine + tangent * cosine,
        z,
      ];
      const outerLeft = point(outerRadius, -halfWidth), outerRight = point(outerRadius, halfWidth);
      const innerLeft = point(innerRadius, -halfWidth), innerRight = point(innerRadius, halfWidth);
      const tickColor = major ? [220, 228, 232] : [142, 158, 164];
      addTriangle(moving, ...[outerLeft, outerRight, innerRight].map(vertex => {
        const index = moving.positions.length / 3;
        moving.positions.push(...vertex);
        return index;
      }), tickColor);
      addTriangle(moving, ...[outerLeft, innerRight, innerLeft].map(vertex => {
        const index = moving.positions.length / 3;
        moving.positions.push(...vertex);
        return index;
      }), tickColor);
      const sideHalfWidth = (major ? 0.58 : 0.28) / TURNTABLE_RADIUS_MM;
      const sideTop = major ? -2.1 : -4.1;
      const sideBottom = major ? -7.9 : -5.9;
      const sidePoint = (angle, z) => [
        (TURNTABLE_RADIUS_MM + 0.03) * Math.cos(angle),
        (TURNTABLE_RADIUS_MM + 0.03) * Math.sin(angle),
        z,
      ];
      const sideQuad = [
        sidePoint(radians - sideHalfWidth, sideTop), sidePoint(radians + sideHalfWidth, sideTop),
        sidePoint(radians + sideHalfWidth, sideBottom), sidePoint(radians - sideHalfWidth, sideBottom),
      ];
      addTriangle(moving, ...sideQuad.slice(0, 3).map(vertex => {
        const index = moving.positions.length / 3;
        moving.positions.push(...vertex);
        return index;
      }), tickColor);
      addTriangle(moving, ...[sideQuad[0], sideQuad[2], sideQuad[3]].map(vertex => {
        const index = moving.positions.length / 3;
        moving.positions.push(...vertex);
        return index;
      }), tickColor);
      degreeTickCount += 1;
      if (major) {
        const topPointIndex = degreeLabelPositions.length / 3;
        degreeLabelPositions.push(cosine * (TURNTABLE_RADIUS_MM + 10),
          sine * (TURNTABLE_RADIUS_MM + 10), 0.35);
        degreeTextLabels.push({ pointIndex: topPointIndex, text: `${degrees}°`, size: 8, color: '#f3f7f8',
          minViewDepthZ: 0.75 });
        const sidePointIndex = degreeLabelPositions.length / 3;
        degreeLabelPositions.push(cosine * (TURNTABLE_RADIUS_MM + 10),
          sine * (TURNTABLE_RADIUS_MM + 10), 3);
        degreeTextLabels.push({ pointIndex: sidePointIndex, text: `${degrees}°`, size: 8, color: '#f3f7f8',
          maxViewDepthZ: 0.75 });
      }
    }
    return {
      fixed: fixedModel,
      moving: finishMesh(moving),
      supportRotationDegrees: -90,
      degreeTickCount,
      degreeLabelPositions: Float32Array.from(degreeLabelPositions),
      degreeTextLabels,
      pivotZ: TURNTABLE_TILT_PIVOT_Z_MM,
      diameterMm: TURNTABLE_DIAMETER_MM,
      thicknessMm: TURNTABLE_THICKNESS_MM,
      fitRadius: 120,
    };
  };
  const turntableDisplayModel = buildTurntableDisplayModel();
  const transformTableDisplayPoint = (point, angleDegrees, tiltDegrees, pivotZ = TURNTABLE_TILT_PIVOT_Z_MM) => {
    const yaw = Number.isFinite(angleDegrees) ? angleDegrees * Math.PI / 180 : 0;
    const tilt = Number.isFinite(tiltDegrees) ? tiltDegrees * Math.PI / 180 : 0;
    const tiltCos = Math.cos(tilt), tiltSin = Math.sin(tilt);
    const yawCos = Math.cos(yaw), yawSin = Math.sin(yaw);
    const zFromPivot = point[2] - pivotZ;
    const yTilted = point[1] * tiltCos - zFromPivot * tiltSin;
    const zTilted = pivotZ + point[1] * tiltSin + zFromPivot * tiltCos;
    return [point[0] * yawCos - yTilted * yawSin,
      point[0] * yawSin + yTilted * yawCos, zTilted];
  };
  const transformTableDisplayPositions = (positions, angleDegrees, tiltDegrees) => {
    const transformed = new Float32Array(positions.length);
    for (let index = 0; index < positions.length; index += 3) {
      transformed.set(transformTableDisplayPoint(
        [positions[index], positions[index + 1], positions[index + 2]], angleDegrees, tiltDegrees), index);
    }
    return transformed;
  };
  const livePreviewSampleIndex = (x, y) => {
    if (!Number.isInteger(x) || !Number.isInteger(y) || x < 0 || x >= 640 || y < 0 || y >= 400) return -1;
    return y * 640 + x;
  };
  const hashLivePreviewPixel = pixel => {
    let value = pixel ^ 0x9e3779b9;
    value = Math.imul(value ^ value >>> 16, 0x7feb352d);
    value = Math.imul(value ^ value >>> 15, 0x846ca68b);
    return (value ^ value >>> 16) >>> 0;
  };
  const parseTableMarkerMap = buffer => {
    const view = new DataView(buffer);
    if (view.byteLength < 16) throw new Error('Карта стола короче заголовка MKF.');
    const version = view.getUint32(0, true), count = view.getUint32(12, true);
    const recordSize = 64;
    if (version !== 1 || count < 4 || count > 10000 || view.byteLength !== 16 + count * recordSize) {
      throw new Error('Файл карты стола имеет неверную структуру MKF.');
    }
    const points = [], normals = [];
    for (let index = 0; index < count; index += 1) {
      const offset = 16 + index * recordSize + 16;
      const point = [0, 1, 2].map(axis => view.getFloat64(offset + axis * 8, true));
      const normal = [0, 1, 2].map(axis => view.getFloat64(offset + (axis + 3) * 8, true));
      const length = Math.hypot(...normal);
      if (![...point, ...normal].every(Number.isFinite) || length < 0.5 || length > 1.5) {
        throw new Error(`В записи карты стола ${index + 1} некорректны координаты или нормаль.`);
      }
      points.push(point);
      normals.push(normal.map(value => value / length));
    }
    const mean = [0, 1, 2].map(axis => points.reduce((sum, point) => sum + point[axis] / points.length, 0));
    let normal = [0, 1, 2].map(axis => normals.reduce((sum, value) => sum + value[axis], 0));
    const normalLength = Math.hypot(...normal);
    if (normalLength < 1e-6) throw new Error('Нормали карты стола не задают одну плоскость.');
    normal = normal.map(value => value / normalLength);
    if (normals.some(value => value[0] * normal[0] + value[1] * normal[1] + value[2] * normal[2] < 0.5)) {
      throw new Error('Нормали маркеров карты стола противоречат друг другу.');
    }
    let xAxis = [1, 0, 0].map((value, axis) => value - normal[axis] * normal[0]);
    let xLength = Math.hypot(...xAxis);
    if (xLength < 1e-6) {
      xAxis = [0, 1, 0].map((value, axis) => value - normal[axis] * normal[1]);
      xLength = Math.hypot(...xAxis);
    }
    xAxis = xAxis.map(value => value / xLength);
    const yAxis = [normal[1] * xAxis[2] - normal[2] * xAxis[1],
      normal[2] * xAxis[0] - normal[0] * xAxis[2],
      normal[0] * xAxis[1] - normal[1] * xAxis[0]];
    const projectedMean = points.map(point => {
      const relative = point.map((value, axis) => value - mean[axis]);
      return [xAxis, yAxis].map(axis => axis[0] * relative[0] + axis[1] * relative[1] + axis[2] * relative[2]);
    });
    const centerIndex = projectedMean.reduce((best, point, index) =>
      Math.hypot(...point) < best.distance ? { index, distance: Math.hypot(...point) } : best,
    { index: -1, distance: Infinity }).index;
    if (centerIndex < 0) throw new Error('Не удалось найти центральную точку карты стола.');
    const center = points[centerIndex];
    return {
      count, center, centerIndex, normal, xAxis, yAxis,
      points: points.map(point => {
        const relative = point.map((value, axis) => value - center[axis]);
        return [xAxis, yAxis].map(axis => axis[0] * relative[0] + axis[1] * relative[1] + axis[2] * relative[2]);
      }),
    };
  };

  const transformFromPair = (sourceA, sourceB, targetA, targetB, reflection) => {
    const sx = sourceB[0] - sourceA[0], sy = reflection * (sourceB[1] - sourceA[1]);
    const tx = targetB[0] - targetA[0], ty = targetB[1] - targetA[1];
    const sourceLength = Math.hypot(sx, sy), targetLength = Math.hypot(tx, ty);
    if (sourceLength < 1e-8 || targetLength < 1e-8) return null;
    const scale = targetLength / sourceLength;
    const cosine = (sx * tx + sy * ty) / (sourceLength * targetLength);
    const sine = (sx * ty - sy * tx) / (sourceLength * targetLength);
    const a = scale * cosine, b = -scale * sine * reflection;
    const c = scale * sine, d = scale * cosine * reflection;
    return { a, b, c, d, tx: targetA[0] - a * sourceA[0] - b * sourceA[1],
      ty: targetA[1] - c * sourceA[0] - d * sourceA[1], scale, reflection };
  };

  const applyMapTransform = (point, transform) => [
    transform.a * point[0] + transform.b * point[1] + transform.tx,
    transform.c * point[0] + transform.d * point[1] + transform.ty,
  ];

  const buildTableMapOverlay = tableMap => {
    const positions = [];
    const markerPoints = [];
    const lines = [];
    for (const point of tableMap.points) {
      markerPoints.push(positions.length / 3);
      positions.push(point[0], point[1], 0);
    }
    const segments = 48;
    for (const [x, y] of tableMap.points) {
      const radius = Math.hypot(x, y);
      if (radius < 1e-6) continue;
      const start = positions.length / 3;
      for (let step = 0; step <= segments; step += 1) {
        const angle = 2 * Math.PI * step / segments;
        positions.push(radius * Math.cos(angle), radius * Math.sin(angle), 0);
      }
      for (let step = 0; step < segments; step += 1) lines.push(start + step, start + step + 1);
    }
    const maxRadius = tableMap.points.reduce((maximum, point) => Math.max(maximum, Math.hypot(...point)), 0);
    return { positions: new Float32Array(positions), markerPoints: new Uint32Array(markerPoints),
      lines: new Uint32Array(lines), maxRadius };
  };

  const buildPreviewCloudPointIndices = (count, pointStep, markerIndices = [], hideMarkers = false) => {
    const hiddenMarkers = hideMarkers ? new Set(markerIndices) : null;
    const indices = [];
    for (let index = 0; index < count; index += pointStep) {
      if (!hiddenMarkers?.has(index)) indices.push(index);
    }
    return Uint32Array.from(indices);
  };

  const buildMatchedMapMarkerPointIndices = (overlay, matchedMapIndices = []) => {
    if (!overlay?.markerPoints) return new Uint32Array(0);
    return Uint32Array.from(matchedMapIndices.flatMap(index =>
      Number.isInteger(index) && index >= 0 && index < overlay.markerPoints.length
        ? [overlay.markerPoints[index]] : []));
  };

  const mutualMarkerMatches = (source, target, transform, threshold) => {
    const projected = source.map(point => applyMapTransform(point, transform));
    const nearestTarget = projected.map(point => target.reduce((best, candidate, index) => {
      const distance = Math.hypot(point[0] - candidate[0], point[1] - candidate[1]);
      return distance < best.distance ? { index, distance } : best;
    }, { index: -1, distance: Infinity }));
    const nearestSource = target.map(point => projected.reduce((best, candidate, index) => {
      const distance = Math.hypot(point[0] - candidate[0], point[1] - candidate[1]);
      return distance < best.distance ? { index, distance } : best;
    }, { index: -1, distance: Infinity }));
    return nearestTarget.map((match, sourceIndex) => ({ sourceIndex, targetIndex: match.index, distance: match.distance }))
      .filter(match => match.distance <= threshold && nearestSource[match.targetIndex]?.index === match.sourceIndex);
  };

  const fitMarkerSimilarity = (source, target, pairs) => {
    let best = null;
    for (const reflection of [1, -1]) {
      const sourceCenter = [0, 0], targetCenter = [0, 0];
      for (const pair of pairs) for (let axis = 0; axis < 2; axis += 1) {
        sourceCenter[axis] += (axis ? reflection * source[pair.sourceIndex][axis] : source[pair.sourceIndex][axis]) / pairs.length;
        targetCenter[axis] += target[pair.targetIndex][axis] / pairs.length;
      }
      let dot = 0, cross = 0, norm = 0;
      for (const pair of pairs) {
        const sx = source[pair.sourceIndex][0] - sourceCenter[0];
        const sy = reflection * source[pair.sourceIndex][1] - sourceCenter[1];
        const tx = target[pair.targetIndex][0] - targetCenter[0];
        const ty = target[pair.targetIndex][1] - targetCenter[1];
        dot += sx * tx + sy * ty;
        cross += sx * ty - sy * tx;
        norm += sx * sx + sy * sy;
      }
      const magnitude = Math.hypot(dot, cross);
      if (norm < 1e-10 || magnitude < 1e-10) continue;
      const scale = magnitude / norm, cosine = dot / magnitude, sine = cross / magnitude;
      const a = scale * cosine, b = -scale * sine * reflection;
      const c = scale * sine, d = scale * cosine * reflection;
      const transform = { a, b, c, d, tx: targetCenter[0] - a * sourceCenter[0] - b * reflection * sourceCenter[1],
        ty: targetCenter[1] - c * sourceCenter[0] - d * reflection * sourceCenter[1], scale, reflection };
      const error = pairs.reduce((sum, pair) => {
        const predicted = applyMapTransform(source[pair.sourceIndex], transform);
        return sum + (predicted[0] - target[pair.targetIndex][0]) ** 2
          + (predicted[1] - target[pair.targetIndex][1]) ** 2;
      }, 0);
      if (!best || error < best.error) best = { transform, error };
    }
    return best?.transform || null;
  };

  const registerTableMarkers = (livePoints, tableMap, threshold = 5, priorPairs = null, allowRansac = true) => {
    if (!Array.isArray(livePoints) || livePoints.length < 4 || tableMap?.points?.length < 4) {
      throw new Error('Для привязки к карте стола нужны минимум четыре маркера.');
    }
    let best = null;
    if (Array.isArray(priorPairs) && priorPairs.length >= 4) {
      const transform = fitMarkerSimilarity(livePoints, tableMap.points, priorPairs);
      if (transform) {
        const anchorResiduals = priorPairs.map(pair => {
          const projected = applyMapTransform(livePoints[pair.sourceIndex], transform);
          const target = tableMap.points[pair.targetIndex];
          return Math.hypot(projected[0] - target[0], projected[1] - target[1]);
        });
        const pairs = mutualMarkerMatches(livePoints, tableMap.points, transform, threshold);
        const error = pairs.length ? pairs.reduce((sum, pair) => sum + pair.distance, 0) / pairs.length : Infinity;
        if (anchorResiduals.every(residual => residual <= threshold) && pairs.length >= 4) {
          best = { pairs, transform, error };
        }
      }
    }
    if (allowRansac && livePoints.length >= 4) {
      let seed = 0x51f15e5d;
      const randomIndex = length => {
        seed = (Math.imul(seed ^ seed >>> 15, 1 | seed) + 0x6d2b79f5) | 0;
        let value = Math.imul(seed ^ seed >>> 7, 61 | seed) ^ seed;
        return ((value ^ value >>> 14) >>> 0) % length;
      };
      for (let iteration = 0; iteration < 512; iteration += 1) {
        const sourceA = randomIndex(livePoints.length), sourceB = randomIndex(livePoints.length);
        const targetA = randomIndex(tableMap.points.length), targetB = randomIndex(tableMap.points.length);
        if (sourceA === sourceB || targetA === targetB) continue;
        for (const reflection of [1, -1]) {
          const transform = transformFromPair(livePoints[sourceA], livePoints[sourceB],
            tableMap.points[targetA], tableMap.points[targetB], reflection);
          if (!transform) continue;
          const pairs = mutualMarkerMatches(livePoints, tableMap.points, transform, threshold);
          const error = pairs.length ? pairs.reduce((sum, pair) => sum + pair.distance, 0) / pairs.length : Infinity;
          if (pairs.length >= 4 && (!best || pairs.length > best.pairs.length
              || pairs.length === best.pairs.length && error < best.error)) best = { pairs, transform, error };
        }
      }
    }
    if (!best) throw new Error('Карта стола не сопоставилась: нужны устойчивые соответствия минимум трёх маркеров.');
    let pairs = best.pairs, transform = best.transform;
    for (let iteration = 0; iteration < 5; iteration += 1) {
      const refined = fitMarkerSimilarity(livePoints, tableMap.points, pairs);
      if (!refined) break;
      const matches = mutualMarkerMatches(livePoints, tableMap.points, refined, threshold);
      if (matches.length < 4) break;
      transform = refined;
      if (matches.length === pairs.length && matches.every((pair, index) =>
        pair.sourceIndex === pairs[index].sourceIndex && pair.targetIndex === pairs[index].targetIndex)) {
        pairs = matches;
        break;
      }
      pairs = matches;
    }
    const residuals = pairs.map(pair => {
      const predicted = applyMapTransform(livePoints[pair.sourceIndex], transform);
      return Math.hypot(predicted[0] - tableMap.points[pair.targetIndex][0],
        predicted[1] - tableMap.points[pair.targetIndex][1]);
    }).sort((a, b) => a - b);
    const medianResidual = residuals[Math.floor(residuals.length / 2)];
    if (!Number.isFinite(medianResidual) || medianResidual > threshold) {
      throw new Error('Соответствие карте стола неоднозначно; предпросмотр оставлен без смещения.');
    }
    const matchedTarget = pairs.map(pair => tableMap.points[pair.targetIndex]);
    const centerX = matchedTarget.reduce((sum, point) => sum + point[0] / matchedTarget.length, 0);
    const centerY = matchedTarget.reduce((sum, point) => sum + point[1] / matchedTarget.length, 0);
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    let covarianceXX = 0, covarianceXY = 0, covarianceYY = 0;
    for (const point of matchedTarget) {
      const x = point[0] - centerX, y = point[1] - centerY;
      minX = Math.min(minX, point[0]); maxX = Math.max(maxX, point[0]);
      minY = Math.min(minY, point[1]); maxY = Math.max(maxY, point[1]);
      covarianceXX += x * x;
      covarianceXY += x * y;
      covarianceYY += y * y;
    }
    const targetSpread = Math.max(maxX - minX, maxY - minY);
    const eigenvalueDelta = Math.hypot(covarianceXX - covarianceYY, 2 * covarianceXY);
    const largestVariance = (covarianceXX + covarianceYY + eigenvalueDelta) / 2;
    const smallestVariance = (covarianceXX + covarianceYY - eigenvalueDelta) / 2;
    if (targetSpread < threshold * 2 || largestVariance < 1e-12
        || smallestVariance < largestVariance * 0.02) {
      throw new Error('Маркеры карты расположены слишком близко или почти на одной линии для устойчивой привязки.');
    }
    return { ...transform, matches: pairs, medianResidual };
  };

  const initialize = ({ cloudButton, meshButton, viewport, viewer, getModelGeometry = () => null,
    getProjectId = () => null,
    hasPersistentPointCloud = () => false, meshReady = false }, location = window.location, fetcher = fetch) => {
    const liveFpsIndicator = typeof document !== 'undefined' ? document.getElementById('scanner-preview-fps') : null;
    const liveMapAlignmentIndicator = typeof document !== 'undefined'
      ? document.getElementById('scanner-map-alignment') : null;
    let liveMapAlignmentSummary = null;
    const setMapAlignmentStatus = (message, state = 'waiting') => {
      if (!liveMapAlignmentIndicator) return;
      liveMapAlignmentIndicator.textContent = message;
      liveMapAlignmentIndicator.dataset.state = state;
    };
    let livePreviewOwned = false;
    let staticTurntableVisible = false;
    let serverPointCloudVisible = false;
    let cloudPreviewOpen = false;
    let meshPreviewOpen = false;
    let cloudSource = null;
    let cloudOriginSource = null;
    let cloudTableFrame = null;
    let renderedCloudSource = null;
    let capturePreviewRun = null;
    let capturePreviewAutoOpened = false;
    let cloudRequest = 0;
    let liveRequest = 0;
    let liveTimer = null;
    let liveFrameSequence = 0;
    let liveClientWindowStartedAt = 0;
    let liveClientWindowFrames = 0;
    let liveClientWindowSums = { request: 0, process: 0, render: 0, total: 0, points: 0 };
    let liveClientWindowMax = { request: 0, process: 0, render: 0, total: 0 };
    let liveCalibration = null;
    let liveTableFrame = null;
    let liveTableMap = null;
    let liveTableOverlay = null;
    let lastMapAlignedFrame = null;
    let liveMapTransform = null;
    let liveCameraRestored = false;
    let lastRenderedTurntableDisplayPose = null;
    let lastTurntableTelemetryAngle = null;
    let unwrappedTurntableAngle = 0;
    let turntableTelemetryAt = 0;
    let turntableDisplayAngularVelocity = 0;
    let turntableDisplayFrame = 0;
    let turntableDisplayLastFrameAt = 0;
    let liveMarkerTracks = [];
    let liveMarkerTrackSequence = 0;
    let lastMapRansacSequence = 0;
    let liveFitRadius = null;
    let liveRawCameraCenter = null;
    let liveRawFitRadius = null;
    let liveViewportInitialized = false;
    let emptyFramePollDelay = 25;
    let lastLiveFrameMtimeNs = 0;
    let liveFrameIntervalMs = 0;
    let modelGeometry = null;
    const liveFrameScratch = {
      depths: new Float32Array(640 * 400),
      positions: new Float32Array(LIVE_PREVIEW_POINT_LIMIT * 3),
      tablePositions: new Float32Array(LIVE_PREVIEW_POINT_LIMIT * 3),
      markerFlags: new Uint8Array(LIVE_PREVIEW_POINT_LIMIT),
      markerPixelIndices: new Int32Array(640 * 400),
      markerPixels: new Uint8Array(640 * 400),
      markerCenterPixels: new Uint8Array(640 * 400),
      seen: new Uint8Array(640 * 400),
      queue: new Int32Array(640 * 400),
    };
    let liveFrameWorker = null;
    let liveFrameWorkerRequestId = 0;
    const liveFrameWorkerRequests = new Map();
    const processLiveFrameInWorker = (raw, matrix, scale) => {
      if (typeof window.Worker !== 'function') return null;
      if (!liveFrameWorker) {
        liveFrameWorker = new window.Worker('/assets/scan-preview-worker.js');
        liveFrameWorker.onmessage = event => {
          const pending = liveFrameWorkerRequests.get(event.data?.requestId);
          if (!pending) return;
          liveFrameWorkerRequests.delete(event.data.requestId);
          if (event.data.error) pending.reject(new Error(event.data.error));
          else pending.resolve(event.data.result);
        };
        liveFrameWorker.onerror = event => {
          const error = new Error(event.message || 'Не удалось обработать кадр сканера в фоновом потоке.');
          for (const pending of liveFrameWorkerRequests.values()) pending.reject(error);
          liveFrameWorkerRequests.clear();
          liveFrameWorker?.terminate();
          liveFrameWorker = null;
        };
      }
      const requestId = ++liveFrameWorkerRequestId;
      return new Promise((resolve, reject) => {
        liveFrameWorkerRequests.set(requestId, { resolve, reject });
        try {
          liveFrameWorker.postMessage({ requestId, rawBuffer: raw.buffer, matrix, scale }, [raw.buffer]);
        } catch (error) {
          liveFrameWorkerRequests.delete(requestId);
          reject(error);
        }
      });
    };
    const setAttribute = (element, name, value) => {
      const current = element.getAttribute(name);
      if (current !== value) element.setAttribute(name, value);
    };
    const updateButton = () => {
      const cloudSelected = cloudPreviewOpen;
      const meshSelected = meshPreviewOpen;
      const previewVisible = String(livePreviewOwned);
      viewport.dataset.scanPreviewMode = livePreviewOwned ? 'live' : cloudSelected ? 'cloud'
        : meshSelected ? 'mesh' : staticTurntableVisible ? 'turntable' : '';
      if (viewport.dataset.scanPreview !== previewVisible) viewport.dataset.scanPreview = previewVisible;
      cloudButton.disabled = serverPointCloudVisible;
      setAttribute(cloudButton, 'aria-label', 'Облако точек');
      setAttribute(cloudButton, 'aria-pressed', String(cloudSelected));
      const cloudTitle = serverPointCloudVisible ? 'Объединённое облако уже показано как тело CAD-проекта'
        : cloudSelected ? 'Скрыть предпросмотр скана'
        : meshSelected ? 'Переключить на облако точек' : 'Показать облако точек';
      if (cloudButton.title !== cloudTitle) cloudButton.title = cloudTitle;
      const meshDisabled = !meshReady;
      if (meshButton.disabled !== meshDisabled) meshButton.disabled = meshDisabled;
      setAttribute(meshButton, 'aria-pressed', String(meshSelected));
      const meshTitle = meshReady
        ? meshSelected ? 'Скрыть предпросмотр скана' : cloudSelected ? 'Переключить на сетку' : 'Показать сетку скана'
        : 'Сетка станет доступна после обработки облака точек';
      if (meshButton.title !== meshTitle) meshButton.title = meshTitle;
    };
    const restoreModel = () => {
      cloudRequest += 1;
      liveRequest += 1;
      clearTimeout(liveTimer);
      liveTimer = null;
      livePreviewOwned = false;
      cloudPreviewOpen = false;
      meshPreviewOpen = false;
      staticTurntableVisible = false;
      updateButton();
      const geometry = getModelGeometry() || modelGeometry;
      if (geometry && viewer) viewer.load(geometry, { reset: false });
      modelGeometry = null;
      if (!serverPointCloudVisible && document.querySelector?.('#primary-grid')?.dataset.view === 'scanning') {
        showTurntable();
      }
    };
    const pointCloudUrl = scan => {
      const url = new URL('/api/v1/scanner/point-cloud', location.origin);
      const pageParams = new URLSearchParams(location.search);
      const folder = scan ? scan.preview_folder : pageParams.get('scan_folder');
      const file = scan ? scan.preview_file : pageParams.get('scan_file');
      if (!folder || !file) return null;
      url.searchParams.set('folder', folder);
      url.searchParams.set('file', file);
      if (scan?.preview_project_id && scan.preview_subfolder) {
        url.searchParams.set('project_id', scan.preview_project_id);
        url.searchParams.set('subfolder', scan.preview_subfolder);
      }
      return url.href;
    };
    const originUrl = scan => {
      const pageParams = new URLSearchParams(location.search);
      const folder = scan ? scan.preview_folder : pageParams.get('scan_folder');
      const file = scan ? scan.origin_file : pageParams.get('scan_origin_file');
      if (!folder || !file) return null;
      const url = new URL('/api/v1/scanner/point-cloud', location.origin);
      url.searchParams.set('folder', folder);
      url.searchParams.set('file', file);
      if (scan?.preview_project_id && scan.preview_subfolder) {
        url.searchParams.set('project_id', scan.preview_project_id);
        url.searchParams.set('subfolder', scan.preview_subfolder);
      }
      return url.href;
    };
    const projectPointCloudUrl = async () => {
      const projectId = getProjectId();
      if (!projectId) throw new Error('Сначала выберите проект с файлом облака точек.');
      const filesResponse = await fetcher(`/api/v1/projects/${encodeURIComponent(projectId)}/files`, { cache: 'no-store' });
      if (!filesResponse.ok) throw new Error(`Не удалось получить файлы проекта (HTTP ${filesResponse.status}).`);
      const filesPayload = await filesResponse.json();
      const plyFiles = (filesPayload.files || []).filter(file => typeof file.name === 'string' && /\.ply$/i.test(file.name));
      const pointCloudFiles = plyFiles.filter(file => {
        const stem = file.name.replace(/\.ply$/i, '');
        return !/(?:^|[_-])(?:mesh|surface|fused)(?:[_-]|$)/i.test(stem)
          && /(?:^|[_-])(?:pc|points?|cloud|pointcloud|point_cloud)(?:[_-]|$)/i.test(stem);
      });
      const candidates = pointCloudFiles.length ? pointCloudFiles : plyFiles.length === 1 ? plyFiles : [];
      if (candidates.length !== 1) {
        throw new Error(candidates.length
          ? 'В проекте несколько PLY-файлов облака точек.'
          : 'В проекте не найден однозначный PLY-файл облака точек.');
      }
      return {
        file: candidates[0].name,
        size: candidates[0].size,
        url: `/api/v1/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(candidates[0].name)}`,
      };
    };
    const loadProjectPointCloud = async () => {
      if (!viewer) throw new Error('Не найден основной 3D-предпросмотр.');
      const source = await projectPointCloudUrl();
      const response = await fetcher(source.url, { cache: 'no-store' });
      if (!response.ok) throw new Error(`Не удалось открыть ${source.file} (HTTP ${response.status}).`);
      if (!window.CadyxPlyPreview?.parseStream) throw new Error('Не найден потоковый PLY-парсер.');
      let lastProgress = -1;
      const geometry = await window.CadyxPlyPreview.parseStream(response.body, {
        maxPreviewPoints: 125_000,
        totalBytes: source.size,
        onProgress: (bytesRead, totalBytes) => {
          const total = Number(totalBytes);
          if (!Number.isFinite(total) || total <= 0) return;
          const percent = Math.min(99, Math.floor(bytesRead / total * 100));
          if (percent > lastProgress) {
            lastProgress = percent;
            cloudButton.title = `Чтение облака точек: ${percent}%`;
          }
        },
      });
      if (geometry.faceCount > 0) throw new Error(`${source.file} содержит сетку, а не облако точек.`);
      const count = geometry.previewVertexCount ?? geometry.positions.length / 3;
      return {
        source: source.url,
        geometry: {
          ...geometry,
          triangles: new Uint32Array(0),
          lines: [],
          points: Array.from({ length: count }, (_, index) => index),
          pointStyle: { size: 1.5, opacity: 0.78, color: '#ff8740' },
        },
      };
    };
    const readPointCloud = async url => {
      const response = await fetcher(url, { cache: 'no-store' });
      if (!response.ok) throw new Error(`Не удалось открыть облако точек (HTTP ${response.status}).`);
      const content = await response.text();
      const marker = content.indexOf('end_header');
      if (marker < 0) throw new Error('PLY не содержит end_header.');
      const header = content.slice(0, marker).split(/\r?\n/);
      let vertexCount = 0, inVertices = false;
      const properties = [];
      for (const line of header) {
        const fields = line.trim().split(/\s+/);
        if (fields[0] === 'format' && fields[1] !== 'ascii') throw new Error('Поддерживается только ASCII PLY.');
        if (fields[0] === 'element') {
          inVertices = fields[1] === 'vertex';
          if (fields[1] === 'face' && Number(fields[2]) !== 0) throw new Error('Для облака точек нужен PLY без граней.');
          if (inVertices) vertexCount = Number(fields[2]);
        } else if (fields[0] === 'property' && inVertices) properties.push(fields.at(-1));
      }
      if (!Number.isInteger(vertexCount) || vertexCount < 3 || vertexCount > 250000
          || !['x', 'y', 'z'].every(name => properties.includes(name))) {
        throw new Error('PLY должен содержать от 3 до 250 000 точек с координатами xyz.');
      }
      const columns = ['x', 'y', 'z'].map(name => properties.indexOf(name));
      const rows = content.slice(marker + 'end_header'.length).trim().split(/\r?\n/);
      const positions = new Float64Array(vertexCount * 3);
      const bounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
      let validCount = 0;
      for (let index = 0; index < vertexCount; index += 1) {
        const fields = rows[index]?.trim().split(/\s+/) || [];
        const point = columns.map(column => Number(fields[column]));
        if (!point.every(Number.isFinite)) continue;
        for (let axis = 0; axis < 3; axis += 1) {
          positions[validCount * 3 + axis] = point[axis];
          bounds.min[axis] = Math.min(bounds.min[axis], point[axis]);
          bounds.max[axis] = Math.max(bounds.max[axis], point[axis]);
        }
        validCount += 1;
      }
      if (validCount < 3) throw new Error('В PLY недостаточно корректных точек.');
      return { positions: validCount === vertexCount ? positions : positions.slice(0, validCount * 3), bounds, count: validCount };
    };
    const centerRay = calibration => {
      const matrix = calibration.matrix, depth = 100;
      const x = (calibration.width || 640) / 2, y = (calibration.height || 400) / 2;
      const px = matrix[0] * x + matrix[1] * y + matrix[2] * depth + matrix[3];
      const py = matrix[4] * x + matrix[5] * y + matrix[6] * depth + matrix[7];
      const pz = matrix[8] * x + matrix[9] * y + matrix[10] * depth + matrix[11];
      if (!Number.isFinite(pz) || Math.abs(pz) < 1e-12) throw new Error('Луч центра разрешения сканера некорректен.');
      const ray = [px * depth / pz, -py * depth / pz, -depth];
      const length = Math.hypot(...ray);
      return ray.map(value => value / length);
    };
    const smallestEigenvector = covariance => {
      const matrix = covariance.map(row => [...row]);
      const vectors = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
      for (let iteration = 0; iteration < 24; iteration += 1) {
        let p = 0, q = 1;
        if (Math.abs(matrix[0][2]) > Math.abs(matrix[p][q])) { p = 0; q = 2; }
        if (Math.abs(matrix[1][2]) > Math.abs(matrix[p][q])) { p = 1; q = 2; }
        if (Math.abs(matrix[p][q]) < 1e-12) break;
        const angle = 0.5 * Math.atan2(2 * matrix[p][q], matrix[q][q] - matrix[p][p]);
        const cosine = Math.cos(angle), sine = Math.sin(angle);
        for (let k = 0; k < 3; k += 1) {
          const mkp = matrix[k][p], mkq = matrix[k][q];
          matrix[k][p] = cosine * mkp - sine * mkq;
          matrix[k][q] = sine * mkp + cosine * mkq;
        }
        for (let k = 0; k < 3; k += 1) {
          const mpk = matrix[p][k], mqk = matrix[q][k];
          matrix[p][k] = cosine * mpk - sine * mqk;
          matrix[q][k] = sine * mpk + cosine * mqk;
        }
        for (let k = 0; k < 3; k += 1) {
          const vkp = vectors[k][p], vkq = vectors[k][q];
          vectors[k][p] = cosine * vkp - sine * vkq;
          vectors[k][q] = sine * vkp + cosine * vkq;
        }
      }
      const axis = matrix[1][1] < matrix[0][0] ? (matrix[2][2] < matrix[1][1] ? 2 : 1)
        : (matrix[2][2] < matrix[0][0] ? 2 : 0);
      const normal = [vectors[0][axis], vectors[1][axis], vectors[2][axis]];
      const length = Math.hypot(...normal);
      return normal.map(value => value / length);
    };
    const fitTableFrame = (positions, centerDirection) => {
      const sample = [];
      const sampleStride = Math.max(3, Math.ceil(positions.length / 15000) * 3);
      for (let index = 0; index < positions.length; index += sampleStride) {
        if (Number.isFinite(positions[index]) && Number.isFinite(positions[index + 1]) && Number.isFinite(positions[index + 2])) {
          sample.push([positions[index], positions[index + 1], positions[index + 2]]);
        }
      }
      if (sample.length < 12) throw new Error('Недостаточно точек для определения плоскости стола.');
      const planeSample = sample;
      let best = null, bestCount = 0;
      let seed = 0x6d2b79f5;
      const randomIndex = () => {
        seed = (Math.imul(seed ^ seed >>> 15, 1 | seed) + 0x6d2b79f5) | 0;
        let value = Math.imul(seed ^ seed >>> 7, 61 | seed) ^ seed;
        return ((value ^ value >>> 14) >>> 0) % planeSample.length;
      };
      for (let attempt = 0; attempt < 96; attempt += 1) {
        const a = planeSample[randomIndex()], b = planeSample[randomIndex()], c = planeSample[randomIndex()];
        const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
        const ac = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
        let normal = [ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0]];
        const length = Math.hypot(...normal);
        if (length < 1e-6) continue;
        normal = normal.map(value => value / length);
        const d = -(normal[0] * a[0] + normal[1] * a[1] + normal[2] * a[2]);
        let count = 0;
        const occupied = new Set();
        for (const point of planeSample) if (Math.abs(normal[0] * point[0] + normal[1] * point[1] + normal[2] * point[2] + d) <= 2.5) {
          count += 1;
          occupied.add(point.map(value => Math.floor(value / 10)).join(','));
        }
        // Distributed table markers must outweigh a dense patch on the object.
        const score = occupied.size;
        if (count >= 12 && (!best || score > best.score)) { bestCount = count; best = { normal, d, score }; }
      }
      if (!best || bestCount < Math.max(6, planeSample.length * 0.1)) throw new Error('Не удалось надёжно определить плоскость стола в лайв-кадре.');
      let inliers = [];
      for (let index = 0; index < positions.length; index += 3) {
        const distance = best.normal[0] * positions[index] + best.normal[1] * positions[index + 1]
          + best.normal[2] * positions[index + 2] + best.d;
        if (Math.abs(distance) <= 2.5) inliers.push([positions[index], positions[index + 1], positions[index + 2]]);
      }
      if (inliers.length < 12) throw new Error('В лайв-кадре мало точек плоскости стола.');
      const origin = [0, 0, 0];
      for (const point of inliers) for (let axis = 0; axis < 3; axis += 1) origin[axis] += point[axis] / inliers.length;
      const covariance = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
      for (const point of inliers) for (let row = 0; row < 3; row += 1) for (let column = 0; column < 3; column += 1) {
        covariance[row][column] += (point[row] - origin[row]) * (point[column] - origin[column]) / inliers.length;
      }
      let zAxis = smallestEigenvector(covariance);
      if (zAxis[0] * -centerDirection[0] + zAxis[1] * -centerDirection[1] + zAxis[2] * -centerDirection[2] < 0) zAxis = zAxis.map(value => -value);
      let xAxis = [1, 0, 0].map((value, axis) => value - zAxis[axis] * zAxis[0]);
      let xLength = Math.hypot(...xAxis);
      if (xLength < 1e-6) { xAxis = [0, 1, 0].map((value, axis) => value - zAxis[axis] * zAxis[1]); xLength = Math.hypot(...xAxis); }
      xAxis = xAxis.map(value => value / xLength);
      const yAxis = [zAxis[1] * xAxis[2] - zAxis[2] * xAxis[1], zAxis[2] * xAxis[0] - zAxis[0] * xAxis[2], zAxis[0] * xAxis[1] - zAxis[1] * xAxis[0]];
      const planeD = -(zAxis[0] * origin[0] + zAxis[1] * origin[1] + zAxis[2] * origin[2]);
      const denominator = zAxis[0] * centerDirection[0] + zAxis[1] * centerDirection[1] + zAxis[2] * centerDirection[2];
      if (Math.abs(denominator) < 1e-6) throw new Error('Центральный луч сканера почти параллелен плоскости стола.');
      const distance = -planeD / denominator;
      const tableCenter = centerDirection.map(value => value * distance);
      return { origin: tableCenter, xAxis, yAxis, zAxis, inlierCount: inliers.length };
    };
    const transformToTableFrame = (positions, frame) => {
      const bounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
      for (let index = 0; index < positions.length; index += 3) {
        const relative = [positions[index] - frame.origin[0], positions[index + 1] - frame.origin[1], positions[index + 2] - frame.origin[2]];
        const point = [frame.xAxis, frame.yAxis, frame.zAxis].map(axis => axis[0] * relative[0] + axis[1] * relative[1] + axis[2] * relative[2]);
        for (let axis = 0; axis < 3; axis += 1) {
          positions[index + axis] = point[axis];
          bounds.min[axis] = Math.min(bounds.min[axis], point[axis]);
          bounds.max[axis] = Math.max(bounds.max[axis], point[axis]);
        }
      }
      return bounds;
    };
    const transformLiveToTableFrame = (positions, frame, mapTransform, markerFlags) => {
      const bounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
      const markerIndices = [];
      let accepted = 0;
      for (let index = 0; index < positions.length; index += 3) {
        const relativeX = positions[index] - frame.origin[0];
        const relativeY = positions[index + 1] - frame.origin[1];
        const relativeZ = positions[index + 2] - frame.origin[2];
        const tableX = frame.xAxis[0] * relativeX + frame.xAxis[1] * relativeY + frame.xAxis[2] * relativeZ;
        const tableY = frame.yAxis[0] * relativeX + frame.yAxis[1] * relativeY + frame.yAxis[2] * relativeZ;
        let tableZ = (frame.zAxis[0] * relativeX + frame.zAxis[1] * relativeY + frame.zAxis[2] * relativeZ) * mapTransform.scale;
        if (tableZ < -2.5) continue;
        if (tableZ < 0) tableZ = 0;
        if (tableZ <= LIVE_TABLE_SURFACE_DISPLAY_HEIGHT) continue;
        const x = mapTransform.a * tableX + mapTransform.b * tableY + mapTransform.tx;
        const y = mapTransform.c * tableX + mapTransform.d * tableY + mapTransform.ty;
        positions[accepted * 3] = x;
        positions[accepted * 3 + 1] = y;
        positions[accepted * 3 + 2] = tableZ;
        bounds.min[0] = Math.min(bounds.min[0], x);
        bounds.min[1] = Math.min(bounds.min[1], y);
        bounds.min[2] = Math.min(bounds.min[2], tableZ);
        bounds.max[0] = Math.max(bounds.max[0], x);
        bounds.max[1] = Math.max(bounds.max[1], y);
        bounds.max[2] = Math.max(bounds.max[2], tableZ);
        if (markerFlags[index / 3]) markerIndices.push(accepted);
        accepted += 1;
      }
      return { bounds, markerIndices, count: accepted };
    };
    const trackTableMarkerMatches = (currentPixels, frameSequence) => {
    if (liveMarkerTracks.length < 4 || currentPixels.length < 4) return [];
      const frameGap = Math.max(1, frameSequence - liveMarkerTrackSequence);
      const maximumPixelDistance = Math.min(120, 35 * frameGap);
      const nearestCurrent = liveMarkerTracks.map(track => {
        let index = -1, distance = Infinity;
        for (let current = 0; current < currentPixels.length; current += 1) {
          const candidate = Math.hypot(track.pixel[0] - currentPixels[current][0],
            track.pixel[1] - currentPixels[current][1]);
          if (candidate < distance) { index = current; distance = candidate; }
        }
        return { index, distance };
      });
      const nearestTrack = currentPixels.map(pixel => {
        let index = -1, distance = Infinity;
        for (let previous = 0; previous < liveMarkerTracks.length; previous += 1) {
          const candidate = Math.hypot(liveMarkerTracks[previous].pixel[0] - pixel[0],
            liveMarkerTracks[previous].pixel[1] - pixel[1]);
          if (candidate < distance) { index = previous; distance = candidate; }
        }
        return { index, distance };
      });
      return nearestCurrent.flatMap((match, previous) =>
        match.index >= 0 && match.distance <= maximumPixelDistance
          && nearestTrack[match.index]?.index === previous
          ? [{ sourceIndex: match.index, targetIndex: liveMarkerTracks[previous].mapIndex }]
          : []);
    };
    const currentTurntableDisplayPose = () => {
      const readDegrees = id => {
        const value = typeof document === 'undefined' ? NaN
          : Number.parseFloat(document.getElementById?.(id)?.textContent || '');
        return Number.isFinite(value) ? value : 0;
      };
      const angle = readDegrees('turntable-angle');
      const now = globalThis.performance?.now?.() ?? Date.now();
      const spinning = typeof document !== 'undefined'
        && document.getElementById?.('turntable-spin')?.getAttribute('aria-pressed') === 'true';
      if (lastTurntableTelemetryAngle === null) {
        lastTurntableTelemetryAngle = angle;
        unwrappedTurntableAngle = angle;
        turntableTelemetryAt = now;
      } else if (Math.abs(angle - lastTurntableTelemetryAngle) > 0.01) {
        let delta = angle - lastTurntableTelemetryAngle;
        if (delta > 180) delta -= 360;
        else if (delta < -180) delta += 360;
        const elapsed = Math.max(0.001, (now - turntableTelemetryAt) / 1000);
        if (spinning && elapsed <= 5 && Math.abs(delta) > 0.05 && Math.abs(delta) <= 90) {
          turntableDisplayAngularVelocity = delta / elapsed;
          unwrappedTurntableAngle += delta;
        } else {
          turntableDisplayAngularVelocity = 0;
          unwrappedTurntableAngle = angle;
        }
        lastTurntableTelemetryAngle = angle;
        turntableTelemetryAt = now;
      } else if (!spinning) {
        turntableDisplayAngularVelocity = 0;
        unwrappedTurntableAngle = angle;
      }
      const telemetryAge = Math.max(0, (now - turntableTelemetryAt) / 1000);
      const displayAngle = spinning && Math.abs(turntableDisplayAngularVelocity) > 0.01 && telemetryAge < 2.5
        ? unwrappedTurntableAngle + turntableDisplayAngularVelocity * telemetryAge
        : angle;
      return { angle: displayAngle, tilt: readDegrees('turntable-tilt') };
    };
    const turntableDisplayPoseChanged = () => {
      const pose = currentTurntableDisplayPose();
      return !lastRenderedTurntableDisplayPose
        || Math.abs(pose.angle - lastRenderedTurntableDisplayPose.angle) > 0.1
        || Math.abs(pose.tilt - lastRenderedTurntableDisplayPose.tilt) > 0.1;
    };
    const renderTurntableScene = ({ positions = null, count = 0, markerIndices = [], matchedMapIndices = [],
      aligned = false, reset = false }) => {
      const pose = currentTurntableDisplayPose();
      const pointStep = positions ? Math.max(1, Math.ceil(count / LIVE_PREVIEW_POINT_LIMIT)) : 1;
      const overlay = staticTurntableVisible ? null : liveTableOverlay;
      const cloudPointIndices = positions
        ? buildPreviewCloudPointIndices(count, pointStep, markerIndices, Boolean(overlay)) : new Uint32Array(0);
      const cloudPointCount = cloudPointIndices.length;
      let extraMarkerCount = 0;
      if (!overlay) {
        for (const index of markerIndices) if (index % pointStep !== 0) extraMarkerCount += 1;
      }
      const cloudVertexCount = cloudPointCount + extraMarkerCount;
      const fixed = turntableDisplayModel.fixed;
      const moving = transformTableDisplayPositions(
        turntableDisplayModel.moving.positions, pose.angle, pose.tilt);
      const overlayPositions = overlay
        ? transformTableDisplayPositions(overlay.positions, pose.angle, pose.tilt) : new Float32Array(0);
      const degreeLabelPositions = transformTableDisplayPositions(
        turntableDisplayModel.degreeLabelPositions, pose.angle, pose.tilt);
      const fixedVertexCount = fixed.positions.length / 3;
      const movingOffset = fixedVertexCount + cloudVertexCount;
      const overlayOffset = movingOffset + moving.length / 3;
      const degreeLabelOffset = overlayOffset + overlayPositions.length / 3;
      const combinedPositions = new Float32Array(fixed.positions.length + cloudVertexCount * 3
        + moving.length + overlayPositions.length + degreeLabelPositions.length);
      combinedPositions.set(fixed.positions);
      let output = fixed.positions.length;
      const firstCloudVertex = fixedVertexCount;
      for (const index of cloudPointIndices) {
        const point = [positions[index * 3], positions[index * 3 + 1], positions[index * 3 + 2]];
        const transformed = aligned
          ? transformTableDisplayPoint(point, pose.angle, pose.tilt) : point;
        combinedPositions[output++] = transformed[0];
        combinedPositions[output++] = transformed[1];
        combinedPositions[output++] = transformed[2];
      }
      const previewMarkerPoints = new Uint32Array(overlay ? 0 : markerIndices.length);
      let extraMarkerIndex = cloudPointCount;
      for (let markerIndex = 0; !overlay && markerIndex < markerIndices.length; markerIndex += 1) {
        const index = markerIndices[markerIndex];
        if (index % pointStep === 0) previewMarkerPoints[markerIndex] = firstCloudVertex + index / pointStep;
        else {
          const point = [positions[index * 3], positions[index * 3 + 1], positions[index * 3 + 2]];
          const transformed = aligned
            ? transformTableDisplayPoint(point, pose.angle, pose.tilt) : point;
          const vertex = firstCloudVertex + extraMarkerIndex++;
          combinedPositions.set(transformed, vertex * 3);
          previewMarkerPoints[markerIndex] = vertex;
        }
      }
      combinedPositions.set(moving, movingOffset * 3);
      if (overlay) combinedPositions.set(overlayPositions, overlayOffset * 3);
      combinedPositions.set(degreeLabelPositions, degreeLabelOffset * 3);
      const triangles = new Uint32Array(fixed.triangles.length + turntableDisplayModel.moving.triangles.length);
      triangles.set(fixed.triangles);
      for (let index = 0; index < turntableDisplayModel.moving.triangles.length; index += 1) {
        triangles[fixed.triangles.length + index] = turntableDisplayModel.moving.triangles[index] + movingOffset;
      }
      const triangleColors = new Uint8Array(fixed.triangleColors.length
        + turntableDisplayModel.moving.triangleColors.length);
      triangleColors.set(fixed.triangleColors);
      triangleColors.set(turntableDisplayModel.moving.triangleColors, fixed.triangleColors.length);
      const mapMarkerPoints = overlay
        ? Uint32Array.from(overlay.markerPoints, index => index + overlayOffset) : new Uint32Array(0);
      const matchedMapMarkerPoints = overlay
        ? Uint32Array.from(buildMatchedMapMarkerPointIndices(overlay, matchedMapIndices), index => index + overlayOffset)
        : new Uint32Array(0);
      const degreeTextLabels = turntableDisplayModel.degreeTextLabels.map(label => ({
        ...label, pointIndex: degreeLabelOffset + label.pointIndex,
      }));
      const trajectoryLines = overlay
        ? Uint32Array.from(overlay.lines, index => index + overlayOffset) : new Uint32Array(0);
      const cloudPoints = Uint32Array.from({ length: cloudPointCount }, (_, index) => firstCloudVertex + index);
      const bounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
      for (let index = 0; index < combinedPositions.length; index += 3) {
        for (let axis = 0; axis < 3; axis += 1) {
          bounds.min[axis] = Math.min(bounds.min[axis], combinedPositions[index + axis]);
          bounds.max[axis] = Math.max(bounds.max[axis], combinedPositions[index + axis]);
        }
      }
      const pointLayers = [];
      if (cloudPoints.length) pointLayers.push({ points: cloudPoints,
        style: { size: 1.25, opacity: 0.78, color: '#ff9d54' } });
      if (overlay) pointLayers.push({ points: mapMarkerPoints,
        style: { size: 6, opacity: 1, color: '#d4f6ff', shape: 'circle' } });
      if (matchedMapMarkerPoints.length) pointLayers.push({ points: matchedMapMarkerPoints,
        style: { size: 12, opacity: 1, color: '#00d8ff', shape: 'circle' } });
      viewer.load({ positions: combinedPositions, triangles, triangleColors,
        lines: trajectoryLines, textLabels: degreeTextLabels, pointCloudVertexCount: cloudPointCount,
        interactiveRenderScale: 0.25,
        pointPreviewLimit: LIVE_PREVIEW_POINT_LIMIT,
        pointCloudVertexRange: [firstCloudVertex, firstCloudVertex + cloudPointCount],
        hideWorldAxes: true, hideWorldGrid: true,
        bounds, points: previewMarkerPoints,
        pointStyle: { size: 3.5, opacity: 1, color: '#59d9ff' }, pointLayers,
        lineStyle: overlay ? { color: '#668994', opacity: 0.16, width: 0.8, dash: [5, 6] } : undefined,
        orbitCenter: [0, 0, 0], fitRadius: turntableDisplayModel.fitRadius },
      { reset: reset || !liveViewportInitialized && !staticTurntableVisible });
      lastRenderedTurntableDisplayPose = pose;
      liveViewportInitialized = true;
    };
    const renderLiveFrame = options => renderTurntableScene(options);
    const turntableSpinIsActive = () => document.getElementById?.('turntable-spin')
      ?.getAttribute('aria-pressed') === 'true';
    const syncTurntableDisplayAnimation = () => {
      const scanningTabActive = document.querySelector?.('#primary-grid')?.dataset.view === 'scanning';
      if (!turntableSpinIsActive() || !scanningTabActive) {
        if (turntableDisplayFrame && typeof cancelAnimationFrame === 'function') {
          cancelAnimationFrame(turntableDisplayFrame);
        }
        turntableDisplayFrame = 0;
        turntableDisplayLastFrameAt = 0;
        turntableDisplayAngularVelocity = 0;
        return;
      }
      if (turntableDisplayFrame || typeof requestAnimationFrame !== 'function') return;
      const renderInterpolatedFrame = timestamp => {
        turntableDisplayFrame = 0;
        if (!turntableSpinIsActive()) { syncTurntableDisplayAnimation(); return; }
        if (timestamp - turntableDisplayLastFrameAt >= 100) {
          turntableDisplayLastFrameAt = timestamp;
          const frame = staticTurntableVisible ? { aligned: false, reset: false }
            : lastMapAlignedFrame ? { ...lastMapAlignedFrame, reset: false } : { aligned: false, reset: false };
          renderTurntableScene(frame);
        }
        turntableDisplayFrame = requestAnimationFrame(renderInterpolatedFrame);
      };
      turntableDisplayFrame = requestAnimationFrame(renderInterpolatedFrame);
    };
    const showTurntable = () => {
      if (livePreviewOwned || cloudPreviewOpen || meshPreviewOpen || serverPointCloudVisible
          || viewport.dataset.scanTreePreview === 'true'
          || document.querySelector?.('#primary-grid')?.dataset.view !== 'scanning') return false;
      if (staticTurntableVisible) return true;
      staticTurntableVisible = true;
      updateButton();
      renderTurntableScene({ aligned: false, reset: false });
      syncTurntableDisplayAnimation();
      return true;
    };
    const hideTurntable = () => {
      if (!staticTurntableVisible) return false;
      staticTurntableVisible = false;
      updateButton();
      const geometry = getModelGeometry() || modelGeometry;
      if (geometry && viewer) viewer.load(geometry, { reset: false });
      modelGeometry = null;
      syncTurntableDisplayAnimation();
      return true;
    };
    if (typeof MutationObserver === 'function') {
      const poseObserver = new MutationObserver(() => {
        if (staticTurntableVisible && turntableDisplayPoseChanged()) renderTurntableScene({ reset: false });
        syncTurntableDisplayAnimation();
      });
      for (const id of ['turntable-angle', 'turntable-tilt']) {
        const element = document.getElementById?.(id);
        if (element) poseObserver.observe(element, { childList: true, characterData: true, subtree: true });
      }
      const spinButton = document.getElementById?.('turntable-spin');
      if (spinButton) poseObserver.observe(spinButton, { attributes: true, attributeFilter: ['aria-pressed'] });
    }
    cloudSource = pointCloudUrl(null);
    cloudOriginSource = originUrl(null);
    const loadCloud = async ({ reset = true } = {}) => {
      if (!viewer) throw new Error('Не найден основной 3D-предпросмотр.');
      if (!cloudSource) {
        const request = ++cloudRequest;
        const loaded = await loadProjectPointCloud();
        if (request !== cloudRequest || !cloudPreviewOpen) return;
        viewer.load(loaded.geometry, { reset: renderedCloudSource !== loaded.source });
        renderedCloudSource = loaded.source;
        return;
      }
      const request = ++cloudRequest;
      if (!cloudPreviewOpen) modelGeometry = getModelGeometry();
      const cloud = await readPointCloud(cloudSource);
      const sourceFile = new URL(cloudSource, location.href).searchParams.get('file') || '';
      if (sourceFile.toLowerCase().endsWith('_table_aligned.ply')) {
        if (request !== cloudRequest || !cloudPreviewOpen) return;
        viewer.load({ positions: cloud.positions, triangles: new Uint32Array(0), lines: null, bounds: cloud.bounds }, { reset });
        renderedCloudSource = cloudSource;
        return;
      }
      let frame = cloudTableFrame;
      if (!frame) {
        const origin = cloudOriginSource && cloudOriginSource !== cloudSource
          ? await readPointCloud(cloudOriginSource) : cloud;
        if (!liveCalibration) {
          const scannerId = window.CadyxDeviceSettings?.scannerDeviceId?.();
          const response = await fetcher(`/api/v1/scanner/calibration${scannerId ? `?device_id=${encodeURIComponent(scannerId)}` : ''}`, { cache: 'no-store' });
          if (!response.ok) throw new Error(`Калибровка сканера недоступна (HTTP ${response.status}).`);
          liveCalibration = await response.json();
        }
        frame = fitTableFrame(origin.positions, centerRay(liveCalibration));
      }
      if (request !== cloudRequest || !cloudPreviewOpen) return;
      const bounds = transformToTableFrame(cloud.positions, frame);
      cloudTableFrame ||= frame;
      viewer.load({ positions: cloud.positions, triangles: new Uint32Array(0), lines: null, bounds }, { reset });
      renderedCloudSource = cloudSource;
    };
    const showCloud = async (open = true) => {
      livePreviewOwned = false;
      staticTurntableVisible = false;
      clearTimeout(liveTimer);
      liveTimer = null;
      if (open && !cloudPreviewOpen) modelGeometry = getModelGeometry();
      meshPreviewOpen = false;
      cloudPreviewOpen = open;
      if (!open) { restoreModel(); updateButton(); return; }
      updateButton();
      try {
        await loadCloud({ reset: renderedCloudSource !== cloudSource });
        updateButton();
      }
      catch (error) { cloudPreviewOpen = false; restoreModel(); updateButton(); cloudButton.title = error.message; }
    };
    const showMesh = (open = true) => {
      livePreviewOwned = false;
      staticTurntableVisible = false;
      clearTimeout(liveTimer);
      liveTimer = null;
      if (open && !meshPreviewOpen) modelGeometry = getModelGeometry();
      cloudRequest += 1;
      cloudPreviewOpen = false;
      meshPreviewOpen = open;
      updateButton();
      if (open && modelGeometry && viewer) viewer.load(modelGeometry, { reset: false });
      else if (!open) restoreModel();
    };
    const setMode = mode => {
      if (mode !== 'cloud' && mode !== 'mesh' && mode !== 'live') throw new TypeError(`Unknown scan view: ${mode}`);
      if (mode === 'mesh' && !meshReady) return;
      if (mode === 'cloud') { void showCloud(true); return; }
      if (mode === 'mesh') { showMesh(true); return; }
      if (cloudPreviewOpen || meshPreviewOpen) restoreModel();
      showLivePreview({ open: true });
    };
    const showLivePreview = ({ open = false } = {}) => {
      if (!open) return;
      staticTurntableVisible = false;
      if (!livePreviewOwned) modelGeometry = getModelGeometry();
      liveClientWindowStartedAt = 0;
      liveClientWindowFrames = 0;
      liveClientWindowSums = { request: 0, process: 0, render: 0, total: 0, points: 0 };
      liveClientWindowMax = { request: 0, process: 0, render: 0, total: 0 };
      liveMapAlignmentSummary = null;
      if (liveFpsIndicator) {
        liveFpsIndicator.textContent = 'FPS —';
        liveFpsIndicator.hidden = false;
      }
      if (liveMapAlignmentIndicator) {
        liveMapAlignmentIndicator.hidden = false;
        setMapAlignmentStatus('Карта: поиск маркеров');
      }
      liveFrameSequence = 0;
      liveCalibration = null;
      liveTableFrame = null;
      liveTableMap = null;
      liveTableOverlay = null;
      liveMapTransform = null;
      liveMarkerTracks = [];
      liveMarkerTrackSequence = 0;
      lastMapRansacSequence = 0;
      lastMapAlignedFrame = null;
      lastRenderedTurntableDisplayPose = null;
      liveFitRadius = null;
      liveRawCameraCenter = null;
      liveRawFitRadius = null;
      liveViewportInitialized = false;
      emptyFramePollDelay = 25;
      lastLiveFrameMtimeNs = 0;
      liveFrameIntervalMs = 0;
      cloudRequest += 1;
      cloudPreviewOpen = false;
      meshPreviewOpen = false;
      livePreviewOwned = true;
      syncTurntableDisplayAnimation();
      const request = ++liveRequest;
      updateButton();
      const pollFrame = async () => {
        if (request !== liveRequest || !livePreviewOwned) return;
        const clock = () => globalThis.performance?.now?.() ?? Date.now();
        let receivedFrame = false;
        let renderedFrame = false;
        try {
          if (!liveTableMap) {
            const tableId = window.CadyxDeviceSettings?.turntableDeviceId?.();
            const profileId = window.CadyxDeviceSettings?.turntableProfileId?.();
            const query = new URLSearchParams();
            if (tableId) query.set('device_id', tableId);
            if (profileId) query.set('profile_id', profileId);
            const queryString = query.toString();
            const mapResponse = await fetcher(`/api/v1/scanner/turntable/map${queryString ? `?${queryString}` : ''}`, { cache: 'no-store' });
            if (!mapResponse.ok) throw new Error(`Карта стола недоступна (HTTP ${mapResponse.status}).`);
            liveTableMap = parseTableMarkerMap(await mapResponse.arrayBuffer());
            liveTableOverlay = buildTableMapOverlay(liveTableMap);
            const cameraBeforeLiveScene = window.cadyxUiReloadCamera || viewer.getCamera?.();
            renderTurntableScene({ aligned: false, reset: !liveViewportInitialized });
            if (cameraBeforeLiveScene) {
              const cameraToRestore = cameraBeforeLiveScene;
              liveCameraRestored = true;
              viewer.setCamera(cameraToRestore);
              if (window.cadyxUiReloadCamera === cameraToRestore) {
                window.setTimeout(() => {
                  if (window.cadyxUiReloadCamera !== cameraToRestore || !livePreviewOwned) return;
                  viewer.setCamera(cameraToRestore);
                  window.cadyxUiReloadCamera = null;
                  window.cadyxUiReloadCameraAppliedToModel = false;
                }, 650);
              }
            }
          }
          if (!liveCalibration) {
            const scannerId = window.CadyxDeviceSettings?.scannerDeviceId?.();
            const response = await fetcher(`/api/v1/scanner/calibration${scannerId ? `?device_id=${encodeURIComponent(scannerId)}` : ''}`, { cache: 'no-store' });
            if (!response.ok) throw new Error(`Калибровка сканера недоступна (HTTP ${response.status}).`);
            const calibration = await response.json();
            if (!Array.isArray(calibration.matrix) || calibration.matrix.length !== 16
                || !Number.isFinite(calibration.depth_scale_mm_per_raw_unit)
                || calibration.depth_scale_mm_per_raw_unit <= 0) throw new Error('Калибровка глубины сканера некорректна.');
            liveCalibration = calibration;
          }
          const frameRequestStartedAt = clock();
          const response = await fetcher(`/api/v1/scanner/live-frame?latest=1&consume=1&ts=${Date.now()}`, { cache: 'no-store' });
          if (response.ok) {
            receivedFrame = true;
            emptyFramePollDelay = 25;
            const frameSequence = Number(response.headers.get('X-CADYX-Frame-Sequence'));
            if (!Number.isSafeInteger(frameSequence) || frameSequence <= liveFrameSequence) {
              throw new Error('Нарушена последовательность кадров предпросмотра.');
            }
            const frameMtimeNs = Number(response.headers.get('X-CADYX-Frame-Mtime'));
            if (Number.isFinite(frameMtimeNs) && frameMtimeNs > 0) {
              if (lastLiveFrameMtimeNs > 0 && frameMtimeNs > lastLiveFrameMtimeNs) {
                const sequenceDelta = Math.max(1, frameSequence - liveFrameSequence);
                const interval = (frameMtimeNs - lastLiveFrameMtimeNs) / sequenceDelta / 1e6;
                if (interval >= 1 && interval <= 5000) {
                  liveFrameIntervalMs = liveFrameIntervalMs > 0
                    ? liveFrameIntervalMs * 0.75 + interval * 0.25 : interval;
                  emptyFramePollDelay = Math.max(25, Math.min(100, Math.round(liveFrameIntervalMs * 0.5)));
                }
              }
              lastLiveFrameMtimeNs = frameMtimeNs;
            }
            const raw = new Uint8Array(await response.arrayBuffer());
            const requestMs = Math.max(0, clock() - frameRequestStartedAt);
            const processStartedAt = clock();
            if (raw.length !== 640 * 400 * 2) {
              liveFrameSequence = frameSequence;
              renderedFrame = true;
              throw new Error('Размер кадра Y16 не совпадает.');
            }
              const { matrix, depth_scale_mm_per_raw_unit: scale } = liveCalibration;
              let workerFrame = processLiveFrameInWorker(raw, matrix, scale);
              if (workerFrame) workerFrame = await workerFrame;
              let regions = 0, markerPositions = [], markerCenterPositions = [];
              let markerCenterPixelPositions = [], positions = null, markerFlags = null, count = 0;
              let rawBounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
              if (workerFrame) {
                ({ regions, markerPositions, markerCenterPositions, markerCenterPixelPositions,
                  positions, markerFlags, count, rawBounds } = workerFrame);
              } else {
              const { depths, markerPixelIndices, markerPixels, markerCenterPixels, seen, queue } = liveFrameScratch;
              positions = liveFrameScratch.positions;
              markerFlags = liveFrameScratch.markerFlags;
              markerPixels.fill(0);
              markerCenterPixels.fill(0);
              seen.fill(0);
              let currentValidPixels = 0;
              for (let pixel = 0; pixel < depths.length; pixel += 1) {
                const depth = (raw[pixel * 2] | raw[pixel * 2 + 1] << 8) * scale;
                depths[pixel] = isScanDepthValid(depth) ? depth : 0;
                if (depths[pixel] > 0) currentValidPixels += 1;
              }
              const valid = i => depths[i] > 0;
              regions = 0;
              let markerPixelCount = 0;
              for (let seed = 0; seed < seen.length; seed += 1) {
                if (seen[seed] || !valid(seed)) continue;
                queue[0] = seed;
                seen[seed] = 1;
                let tail = 1, sumX = 0, sumY = 0;
                let minX = 640, maxX = 0, minY = 400, maxY = 0;
                for (let cursor = 0; cursor < tail; cursor += 1) {
                  const i = queue[cursor], x = i % 640, y = Math.floor(i / 640);
                  sumX += x; sumY += y;
                  minX = Math.min(minX, x); maxX = Math.max(maxX, x);
                  minY = Math.min(minY, y); maxY = Math.max(maxY, y);
                  let next = x > 0 ? i - 1 : -1;
                  if (next >= 0 && !seen[next] && valid(next)) { seen[next] = 1; queue[tail++] = next; }
                  next = x < 639 ? i + 1 : -1;
                  if (next >= 0 && !seen[next] && valid(next)) { seen[next] = 1; queue[tail++] = next; }
                  next = y > 0 ? i - 640 : -1;
                  if (next >= 0 && !seen[next] && valid(next)) { seen[next] = 1; queue[tail++] = next; }
                  next = y < 399 ? i + 640 : -1;
                  if (next >= 0 && !seen[next] && valid(next)) { seen[next] = 1; queue[tail++] = next; }
                }
                const width = maxX - minX + 1, height = maxY - minY + 1;
                if (tail >= 20 && tail <= 1000 && width / height > 0.3
                    && width / height < 3 && tail / (width * height) > 0.45) {
                  regions += 1;
                  for (let cursor = 0; cursor < tail; cursor += 1) {
                    const i = queue[cursor];
                    markerPixels[i] = 1;
                    markerPixelIndices[markerPixelCount++] = i;
                  }
                  const centerX = sumX / tail, centerY = sumY / tail;
                  let nearest = -1, nearestDistance = Infinity;
                  for (let cursor = 0; cursor < tail; cursor += 1) {
                    const i = queue[cursor];
                    if (i % 2) continue;
                    const dx = i % 640 - centerX, dy = Math.floor(i / 640) - centerY;
                    const distance = dx * dx + dy * dy;
                    if (distance < nearestDistance) { nearest = i; nearestDistance = distance; }
                  }
                  if (nearest >= 0) markerCenterPixels[nearest] = 1;
                }
              }
              const displayDepthCount = currentValidPixels;
              markerPositions = [];
              markerCenterPositions = [];
              markerCenterPixelPositions = [];
              count = 0;
              rawBounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
              const projectedPoint = [0, 0, 0];
              const sampleBudget = LIVE_PREVIEW_POINT_LIMIT - 512;
              const selectedMarkerBudget = Math.min(markerPixelCount, sampleBudget);
              const markerSelectionThreshold = markerPixelCount > selectedMarkerBudget
                ? selectedMarkerBudget / markerPixelCount : 1;
              const regularPixelCount = Math.max(1, displayDepthCount - markerPixelCount);
              const regularSelectionThreshold = displayDepthCount > LIVE_PREVIEW_POINT_LIMIT
                ? Math.min(1, Math.max(0, sampleBudget - selectedMarkerBudget) / regularPixelCount) : 1;
              const projectPixel = (x, y, depth) => {
                const px = matrix[0] * x + matrix[1] * y + matrix[2] * depth + matrix[3];
                const py = matrix[4] * x + matrix[5] * y + matrix[6] * depth + matrix[7];
                const pz = matrix[8] * x + matrix[9] * y + matrix[10] * depth + matrix[11];
                if (!Number.isFinite(pz) || Math.abs(pz) < 1e-12) return false;
                const pointX = px * depth / pz, pointY = -py * depth / pz, pointZ = -depth;
                if (!Number.isFinite(pointX) || !Number.isFinite(pointY) || !Number.isFinite(pointZ)) return false;
                projectedPoint[0] = pointX;
                projectedPoint[1] = pointY;
                projectedPoint[2] = pointZ;
                return true;
              };
              for (let markerIndex = 0; markerIndex < markerPixelCount; markerIndex += 1) {
                const pixel = markerPixelIndices[markerIndex];
                if (!projectPixel(pixel % 640, Math.floor(pixel / 640), depths[pixel])) continue;
                markerPositions.push(projectedPoint[0], projectedPoint[1], projectedPoint[2]);
                if (markerCenterPixels[pixel]) {
                  markerCenterPositions.push(projectedPoint[0], projectedPoint[1], projectedPoint[2]);
                  markerCenterPixelPositions.push([pixel % 640, Math.floor(pixel / 640)]);
                }
              }
              for (let pixel = 0; pixel < depths.length && count < LIVE_PREVIEW_POINT_LIMIT; pixel += 1) {
                const depthValue = depths[pixel];
                if (depthValue <= 0) continue;
                const isMarker = markerPixels[pixel] === 1;
                if (displayDepthCount > LIVE_PREVIEW_POINT_LIMIT) {
                  const threshold = isMarker ? markerSelectionThreshold : regularSelectionThreshold;
                  if (hashLivePreviewPixel(pixel) / 0x1_0000_0000 >= threshold) continue;
                }
                const depth = depthValue;
                if (!projectPixel(pixel % 640, Math.floor(pixel / 640), depth)) continue;
                markerFlags[count] = isMarker ? 1 : 0;
                positions[count * 3] = projectedPoint[0];
                positions[count * 3 + 1] = projectedPoint[1];
                positions[count * 3 + 2] = projectedPoint[2];
                rawBounds.min[0] = Math.min(rawBounds.min[0], projectedPoint[0]);
                rawBounds.min[1] = Math.min(rawBounds.min[1], projectedPoint[1]);
                rawBounds.min[2] = Math.min(rawBounds.min[2], projectedPoint[2]);
                rawBounds.max[0] = Math.max(rawBounds.max[0], projectedPoint[0]);
                rawBounds.max[1] = Math.max(rawBounds.max[1], projectedPoint[1]);
                rawBounds.max[2] = Math.max(rawBounds.max[2], projectedPoint[2]);
                count += 1;
              }
              }
              if (request !== liveRequest || !livePreviewOwned) return;
              if (count >= 3) {
                const hadTableAlignment = Boolean(liveTableFrame && liveMapTransform);
                let tableAligned = false;
                let resetAfterAlignment = false;
                let matchedMapIndices = [];
                const priorMarkerMatches = trackTableMarkerMatches(markerCenterPixelPositions, frameSequence);
                const canRegisterFromTracks = hadTableAlignment && priorMarkerMatches.length >= 4;
                const canInitializeMap = regions >= 4;
                if (canRegisterFromTracks || canInitializeMap) {
                  try {
                    const tableFrame = fitTableFrame(markerPositions, centerRay(liveCalibration));
                    const markerCoordinates = [];
                    for (let index = 0; index < markerCenterPositions.length; index += 3) {
                      const relative = [markerCenterPositions[index] - tableFrame.origin[0],
                        markerCenterPositions[index + 1] - tableFrame.origin[1], markerCenterPositions[index + 2] - tableFrame.origin[2]];
                      markerCoordinates.push([tableFrame.xAxis, tableFrame.yAxis].map(axis =>
                        axis[0] * relative[0] + axis[1] * relative[1] + axis[2] * relative[2]));
                    }
                    const allowRansac = shouldRunTableMapRansac(
                      priorMarkerMatches.length, frameSequence, lastMapRansacSequence, liveTableMap.points.length);
                    if (allowRansac) lastMapRansacSequence = frameSequence;
                    const mapTransform = registerTableMarkers(markerCoordinates, liveTableMap, 5,
                      priorMarkerMatches, allowRansac);
                    liveTableFrame = tableFrame;
                    liveMapTransform = mapTransform;
                    liveMarkerTracks = mapTransform.matches.map(match => ({
                      pixel: markerCenterPixelPositions[match.sourceIndex], mapIndex: match.targetIndex,
                    }));
                    matchedMapIndices = mapTransform.matches.map(match => match.targetIndex);
                    liveMarkerTrackSequence = frameSequence;
                    tableAligned = true;
                    resetAfterAlignment = !hadTableAlignment && liveViewportInitialized && !liveCameraRestored;
                    liveMapAlignmentSummary = `Карта ${mapTransform.matches.length}/${liveTableMap.points.length}`
                      + ` · остаток ${mapTransform.medianResidual.toFixed(1)} ед.`;
                    setMapAlignmentStatus(liveMapAlignmentSummary, 'aligned');
                  } catch (error) {
                    cloudButton.title = `Ожидание привязки к карте стола: ${error.message}`;
                    setMapAlignmentStatus(hadTableAlignment && liveMapAlignmentSummary
                      ? `${liveMapAlignmentSummary} · нужна повторная привязка`
                      : 'Карта: соответствие неоднозначно', hadTableAlignment ? 'stale' : 'waiting');
                  }
                }
                if (!tableAligned && hadTableAlignment) {
                  liveFrameSequence = frameSequence;
                  cloudButton.title = 'Недостаточно совпавших маркеров; сохраняется последний кадр в координатах карты стола.';
                  setMapAlignmentStatus(liveMapAlignmentSummary
                    ? `${liveMapAlignmentSummary} · последний кадр`
                    : 'Карта: мало маркеров · последний кадр', 'stale');
                  if (lastMapAlignedFrame && turntableDisplayPoseChanged()) {
                    renderLiveFrame({ ...lastMapAlignedFrame, reset: false });
                  }
                  return;
                }
                if (!tableAligned && liveTableMap) {
                  setMapAlignmentStatus(`Карта: ${regions}/${liveTableMap.points.length} маркеров; нужно 4`, 'waiting');
                }
                let bounds = rawBounds;
                let markerIndices = [];
                let displayPositions = positions;
                let orbitCenter = null;
                let fitRadius = null;
                if (tableAligned) {
                  const tablePositions = liveFrameScratch.tablePositions;
                  tablePositions.set(positions.subarray(0, count * 3));
                  const transformed = transformLiveToTableFrame(tablePositions.subarray(0, count * 3), liveTableFrame,
                    liveMapTransform, markerFlags);
                  if (transformed.count >= LIVE_MIN_OBJECT_PREVIEW_POINTS || liveTableMap?.points?.length >= 4) {
                    count = transformed.count >= LIVE_MIN_OBJECT_PREVIEW_POINTS ? transformed.count : 0;
                    bounds = transformed.bounds;
                    markerIndices = count ? transformed.markerIndices : [];
                    displayPositions = tablePositions;
                    liveFitRadius ||= turntableDisplayModel.fitRadius;
                    orbitCenter = [0, 0, 0];
                    fitRadius = liveFitRadius;
                    lastMapAlignedFrame = { positions: new Float32Array(tablePositions.subarray(0, count * 3)),
                      count, markerIndices: [...markerIndices], matchedMapIndices: [...matchedMapIndices], aligned: true };
                    if (count === 0) {
                      setMapAlignmentStatus(`${liveMapAlignmentSummary} · только маркеры карты`, 'aligned');
                    }
                  } else {
                    liveFrameSequence = frameSequence;
                    cloudButton.title = 'В текущем кадре нет надёжных точек над плоскостью стола; сохраняется последний выровненный кадр.';
                    setMapAlignmentStatus(liveMapAlignmentSummary
                      ? `${liveMapAlignmentSummary} · последний кадр`
                      : 'Карта: мало точек · последний кадр', 'stale');
                    if (lastMapAlignedFrame && turntableDisplayPoseChanged()) {
                      renderLiveFrame({ ...lastMapAlignedFrame, reset: false });
                    }
                    return;
                  }
                }
                if (!tableAligned && !liveRawCameraCenter) {
                  liveRawCameraCenter = bounds.min.map((minimum, axis) =>
                    minimum / 2 + bounds.max[axis] / 2);
                  liveRawFitRadius = Math.max(Math.hypot(
                    bounds.max[0] - bounds.min[0],
                    bounds.max[1] - bounds.min[1],
                    bounds.max[2] - bounds.min[2],
                  ) / 2, 0.001);
                }
                if (!tableAligned) {
                  orbitCenter = liveRawCameraCenter;
                  fitRadius = liveRawFitRadius;
                }
                if (!tableAligned) {
                  for (let index = 0; index < count; index += 1) {
                    if (markerFlags[index]) markerIndices.push(index);
                  }
                }
                const processMs = Math.max(0, clock() - processStartedAt);
                const renderStartedAt = clock();
                renderLiveFrame({ positions: displayPositions, count, bounds, markerIndices, matchedMapIndices,
                  aligned: tableAligned,
                  orbitCenter, fitRadius, reset: resetAfterAlignment });
                const renderMs = Math.max(0, clock() - renderStartedAt);
                const now = clock();
                if (!liveClientWindowStartedAt) liveClientWindowStartedAt = now;
                liveClientWindowFrames += 1;
                liveClientWindowSums.request += requestMs;
                liveClientWindowSums.process += processMs;
                liveClientWindowSums.render += renderMs;
                liveClientWindowSums.total += requestMs + processMs + renderMs;
                liveClientWindowSums.points += count;
                liveClientWindowMax.request = Math.max(liveClientWindowMax.request, requestMs);
                liveClientWindowMax.process = Math.max(liveClientWindowMax.process, processMs);
                liveClientWindowMax.render = Math.max(liveClientWindowMax.render, renderMs);
                liveClientWindowMax.total = Math.max(liveClientWindowMax.total, requestMs + processMs + renderMs);
                const windowSeconds = (now - liveClientWindowStartedAt) / 1000;
                if (windowSeconds >= 1 && liveClientWindowFrames > 0) {
                  const frames = liveClientWindowFrames;
                  const averages = liveClientWindowSums;
                  const maxima = liveClientWindowMax;
                  liveClientWindowStartedAt = now;
                  liveClientWindowFrames = 0;
                  liveClientWindowSums = { request: 0, process: 0, render: 0, total: 0, points: 0 };
                  liveClientWindowMax = { request: 0, process: 0, render: 0, total: 0 };
                  void fetcher('/api/v1/scanner/live-preview/client-metrics', { method: 'POST',
                    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ metrics: {
                      window_seconds: windowSeconds, frames,
                      request_avg_ms: averages.request / frames, request_max_ms: maxima.request,
                      process_avg_ms: averages.process / frames, process_max_ms: maxima.process,
                      render_avg_ms: averages.render / frames, render_max_ms: maxima.render,
                      total_avg_ms: averages.total / frames, total_max_ms: maxima.total,
                      points_avg: averages.points / frames,
                    } }) }).catch(() => {});
                }
                liveFrameSequence = frameSequence;
                renderedFrame = true;
              } else {
                liveFrameSequence = frameSequence;
                renderedFrame = true;
                if (liveMapTransform) setMapAlignmentStatus(liveMapAlignmentSummary
                  ? `${liveMapAlignmentSummary} · пустой кадр, последний кадр сохранён`
                  : 'Карта: пустой кадр · последний', 'stale');
                if (lastMapAlignedFrame && turntableDisplayPoseChanged()) {
                  renderLiveFrame({ ...lastMapAlignedFrame, reset: false });
                }
                if (!liveViewportInitialized) cloudButton.title = 'Ожидание валидных точек глубины.';
              }
          } else if (response.status === 404) {
            // Keep the measured capture cadence as the next idle poll delay.
            if (lastMapAlignedFrame && turntableDisplayPoseChanged()) {
              renderLiveFrame({ ...lastMapAlignedFrame, reset: false });
            }
          } else {
            let message = `Кадр сканера недоступен (HTTP ${response.status}).`;
            try {
              const error = await response.json();
              message = error.error?.message || error.message || message;
            } catch { /* Keep the HTTP fallback for non-JSON failures. */ }
            throw new Error(message);
          }
        } catch (error) {
          cloudButton.title = error.message;
        } finally {
          if (request === liveRequest && livePreviewOwned) {
            liveTimer = setTimeout(pollFrame, renderedFrame ? 0 : receivedFrame ? 100 : emptyFramePollDelay);
          }
        }
      };
      void pollFrame();
    };
    const closeLivePreview = () => {
      if (livePreviewOwned) {
        livePreviewOwned = false;
        if (liveFpsIndicator) {
          liveFpsIndicator.textContent = 'FPS —';
          liveFpsIndicator.hidden = true;
        }
        if (liveMapAlignmentIndicator) {
          liveMapAlignmentIndicator.textContent = 'Карта: —';
          liveMapAlignmentIndicator.hidden = true;
        }
        restoreModel();
        liveFrameSequence = 0;
        liveTableFrame = null;
        liveTableMap = null;
        liveTableOverlay = null;
        lastMapAlignedFrame = null;
        liveMapTransform = null;
        liveMarkerTracks = [];
        liveMarkerTrackSequence = 0;
        lastMapRansacSequence = 0;
        lastRenderedTurntableDisplayPose = null;
        liveFitRadius = null;
        liveRawCameraCenter = null;
        liveRawFitRadius = null;
        lastLiveFrameMtimeNs = 0;
        liveFrameIntervalMs = 0;
        liveViewportInitialized = false;
        updateButton();
      }
    };
    const setOpen = visible => {
      if (!visible && (cloudPreviewOpen || meshPreviewOpen)) { restoreModel(); updateButton(); return; }
      if (!visible && livePreviewOwned) { closeLivePreview(); return; }
      if (visible) setMode('cloud');
      else updateButton();
    };
    const showCapture = (scan, { open = false } = {}) => {
      const captureRun = scan?.preview_subfolder || (scan?.state === 'running' ? scan.preview_file : null);
      if (scan?.state === 'running' && captureRun && captureRun !== capturePreviewRun) {
        capturePreviewRun = captureRun;
        capturePreviewAutoOpened = false;
      }
      const shouldOpenCapturePreview = ['running', 'completed'].includes(scan?.state)
        && Boolean(scan?.preview_file) && !capturePreviewAutoOpened;
      if (scan?.state === 'completed' && scan.preview_file && hasPersistentPointCloud(scan.preview_file, scan)) {
        serverPointCloudVisible = true;
        if (livePreviewOwned) closeLivePreview();
        else if (cloudPreviewOpen || meshPreviewOpen) { restoreModel(); updateButton(); }
        else updateButton();
        return;
      }
      if (serverPointCloudVisible) {
        serverPointCloudVisible = false;
        updateButton();
      }
      if (livePreviewOwned) return;
      const source = pointCloudUrl(scan);
      const nextOriginSource = originUrl(scan);
      const originChanged = nextOriginSource !== cloudOriginSource;
      const sourceChanged = source !== cloudSource;
      cloudOriginSource = nextOriginSource;
      if (sourceChanged) {
        cloudSource = source;
      }
      if (open) { cloudTableFrame = null; renderedCloudSource = null; }
      if ((sourceChanged || originChanged) && cloudPreviewOpen && source && !open) {
        if (originChanged) cloudTableFrame = null;
        const reset = renderedCloudSource === null;
        void loadCloud({ reset }).catch(error => { cloudButton.title = error.message; });
      }
      if (sourceChanged && cloudPreviewOpen && !source && !open) {
        cloudRequest += 1;
        const geometry = getModelGeometry() || modelGeometry;
        if (geometry && viewer) viewer.load(geometry, { reset: false });
        cloudTableFrame = null;
        renderedCloudSource = null;
        cloudButton.title = 'Ожидание первого кадра сканирования';
      }
      if (shouldOpenCapturePreview && source && !cloudPreviewOpen) {
        capturePreviewAutoOpened = true;
        cloudTableFrame = null;
        renderedCloudSource = null;
        void showCloud(true).catch(error => { cloudButton.title = error.message; });
        return;
      }
      if (open) { void showCloud(true); return; }
    };
    const updateLivePreviewStatus = preview => {
      const captureFps = preview?.metrics?.capture_fps;
      if (liveFpsIndicator) liveFpsIndicator.textContent = Number.isFinite(captureFps)
        ? `FPS ${captureFps.toFixed(1)}` : 'FPS —';
    };
    cloudButton.addEventListener('click', () => {
      if (serverPointCloudVisible) return;
      if (cloudPreviewOpen) void showCloud(false);
      else {
        if (capturePreviewRun) capturePreviewAutoOpened = true;
        void showCloud(true);
      }
    });
    meshButton.addEventListener('click', () => {
      if (!meshReady) return;
      if (meshPreviewOpen) showMesh(false);
      else setMode('mesh');
    });
    updateButton();
    if (new URLSearchParams(location.search).get('live_scan') === '1') setOpen(true);
    return { setOpen, setMode, showCapture, showLivePreview, closeLivePreview,
      showTurntable, hideTurntable, updateLivePreviewStatus };
  };

  const initializeLivePreview = ({ button, view, captureButton }, location = window.location, fetcher = fetch) => {
    if (button?.__cadyxLivePreviewController) return button.__cadyxLivePreviewController;
    let state = 'idle';
    let pending = false;
    let pollTimer = null;
    let previouslyVisible = false;
    let polling = false;
    let disposed = false;
    const active = () => state === 'starting' || state === 'running';
    const canShowPreview = () => {
      const grid = typeof document !== 'undefined' ? document.querySelector('#primary-grid') : null;
      return !grid || grid.dataset.view === 'scanning';
    };
    const syncCaptureButton = () => {
      if (!captureButton) return;
      const target = typeof document !== 'undefined'
        ? document.querySelector('#viewport-scan-360') || captureButton : captureButton;
      target.dataset.livePreviewLock = String(active());
      const locked = active() || target.dataset.scanCaptureBusy === 'true'
        || target.dataset.scanCapturePending === 'true';
      target.dataset.captureLocked = String(locked);
      target.setAttribute('aria-disabled', String(locked));
      window.cadyxSyncCaptureButton?.();
    };
    const updateButton = preview => {
      state = preview?.state || 'idle';
      view.updateLivePreviewStatus?.(preview);
      const nowActive = active();
      const nowVisible = nowActive && canShowPreview();
      if (nowVisible && !previouslyVisible) view.showLivePreview({ open: true });
      else if (!nowVisible && previouslyVisible) view.closeLivePreview();
      if (!nowActive && window.cadyxUiReloadCameraAppliedToModel) {
        window.cadyxUiReloadCamera = null;
        window.cadyxUiReloadCameraAppliedToModel = false;
      }
      previouslyVisible = nowVisible;
      button.setAttribute('aria-pressed', String(active()));
      button.title = active() ? 'Остановить предпросмотр сканера'
        : state === 'failed' ? preview.message || 'Сканер остановил поток'
          : 'Показать сканер в реальном времени';
      syncCaptureButton();
      if (state === 'failed') view.closeLivePreview();
    };
    const poll = async () => {
      if (polling || disposed) return;
      if (!canShowPreview() || (typeof document !== 'undefined' && document.visibilityState === 'hidden')) {
        if (previouslyVisible) view.closeLivePreview();
        previouslyVisible = false;
        clearTimeout(pollTimer);
        pollTimer = setTimeout(poll, 3500);
        return;
      }
      polling = true;
      try {
        const response = await fetcher('/api/v1/scanner/live-preview', { cache: 'no-store' });
        const payload = await response.json();
        if (disposed) return;
        if (!response.ok) throw new Error(payload.error?.message || payload.message || 'Не удалось получить состояние сканера');
        updateButton(payload.preview);
      } catch (error) {
        if (active()) {
          button.title = `Предпросмотр работает, статус временно недоступен: ${error.message}`;
          syncCaptureButton();
        } else updateButton({ state: 'failed', message: error.message });
      } finally { polling = false; }
      clearTimeout(pollTimer);
      if (!disposed) pollTimer = setTimeout(poll, active() ? 900 : 3500);
    };
    button.addEventListener('click', async () => {
      if (pending) return;
      pending = true;
      button.disabled = true;
      try {
        const stopping = active();
        const response = await fetcher('/api/v1/scanner/live-preview', stopping
          ? { method: 'DELETE' }
          : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scanner_device_id: window.CadyxDeviceSettings?.scannerDeviceId?.() }) });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error?.message || payload.message || 'Не удалось переключить предпросмотр сканера');
        updateButton(payload.preview);
      } catch (error) {
        updateButton({ state: 'failed', message: error.message });
      } finally {
        pending = false;
        button.disabled = false;
        clearTimeout(pollTimer);
        pollTimer = setTimeout(poll, active() ? 900 : 3500);
      }
    });
    poll();
    const resumePoll = () => { clearTimeout(pollTimer); void poll(); };
    const grid = typeof document !== 'undefined' ? document.querySelector('#primary-grid') : null;
    const viewObserver = grid && typeof MutationObserver !== 'undefined' ? new MutationObserver(resumePoll) : null;
    viewObserver?.observe(grid, { attributes: true, attributeFilter: ['data-view'] });
    if (typeof document !== 'undefined') document.addEventListener?.('visibilitychange', resumePoll);
    const controller = { poll, dispose: () => {
      disposed = true;
      clearTimeout(pollTimer);
      viewObserver?.disconnect();
      if (typeof document !== 'undefined') document.removeEventListener?.('visibilitychange', resumePoll);
      if (button?.__cadyxLivePreviewController === controller) {
        delete button.__cadyxLivePreviewController;
      }
    } };
    if (button) button.__cadyxLivePreviewController = controller;
    return controller;
  };

  window.CadyxScanView = { initialize, initializeLivePreview, parseTableMarkerMap, registerTableMarkers,
    shouldRunTableMapRansac, buildTurntableDisplayModel, transformTableDisplayPoint,
    livePreviewSampleIndex, isScanDepthValid, buildPreviewCloudPointIndices, buildMatchedMapMarkerPointIndices };
})();
