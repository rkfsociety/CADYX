(() => {
  const WIDTH = 640;
  const HEIGHT = 400;
  const PIXEL_COUNT = WIDTH * HEIGHT;
  const MIN_DEPTH_MM = 100;
  const MAX_DEPTH_MM = 250;
  const PREVIEW_POINT_LIMIT = 8_000;

  const isScanDepthValid = depth => Number.isFinite(depth)
    && depth >= MIN_DEPTH_MM && depth <= MAX_DEPTH_MM;
  const hashPixel = pixel => {
    let value = pixel ^ 0x9e3779b9;
    value = Math.imul(value ^ value >>> 16, 0x7feb352d);
    value = Math.imul(value ^ value >>> 15, 0x846ca68b);
    return (value ^ value >>> 16) >>> 0;
  };

  function processFrame(rawBuffer, matrix, scale) {
    if (!(rawBuffer instanceof ArrayBuffer) || rawBuffer.byteLength !== PIXEL_COUNT * 2
        || !matrix || matrix.length !== 16 || !Array.from(matrix).every(Number.isFinite)
        || !Number.isFinite(scale) || scale <= 0) {
      throw new Error('Некорректный кадр или калибровка предпросмотра.');
    }
    const raw = new Uint8Array(rawBuffer);
    const depths = new Float32Array(PIXEL_COUNT);
    const markerPixels = new Uint8Array(PIXEL_COUNT);
    const markerCenterPixels = new Uint8Array(PIXEL_COUNT);
    const seen = new Uint8Array(PIXEL_COUNT);
    const queue = new Uint32Array(PIXEL_COUNT);
    let currentValidPixels = 0;
    for (let pixel = 0; pixel < PIXEL_COUNT; pixel += 1) {
      const depth = (raw[pixel * 2] | raw[pixel * 2 + 1] << 8) * scale;
      depths[pixel] = isScanDepthValid(depth) ? depth : 0;
      if (depths[pixel] > 0) currentValidPixels += 1;
    }

    let regions = 0;
    let markerPixelCount = 0;
    const markerPixelIndices = new Uint32Array(PIXEL_COUNT);
    for (let seed = 0; seed < PIXEL_COUNT; seed += 1) {
      if (seen[seed] || depths[seed] <= 0) continue;
      queue[0] = seed;
      seen[seed] = 1;
      let tail = 1, sumX = 0, sumY = 0;
      let minX = WIDTH, maxX = 0, minY = HEIGHT, maxY = 0;
      for (let cursor = 0; cursor < tail; cursor += 1) {
        const index = queue[cursor], x = index % WIDTH, y = Math.floor(index / WIDTH);
        sumX += x; sumY += y;
        minX = Math.min(minX, x); maxX = Math.max(maxX, x);
        minY = Math.min(minY, y); maxY = Math.max(maxY, y);
        let next = x > 0 ? index - 1 : -1;
        if (next >= 0 && !seen[next] && depths[next] > 0) { seen[next] = 1; queue[tail++] = next; }
        next = x < WIDTH - 1 ? index + 1 : -1;
        if (next >= 0 && !seen[next] && depths[next] > 0) { seen[next] = 1; queue[tail++] = next; }
        next = y > 0 ? index - WIDTH : -1;
        if (next >= 0 && !seen[next] && depths[next] > 0) { seen[next] = 1; queue[tail++] = next; }
        next = y < HEIGHT - 1 ? index + WIDTH : -1;
        if (next >= 0 && !seen[next] && depths[next] > 0) { seen[next] = 1; queue[tail++] = next; }
      }
      const regionWidth = maxX - minX + 1, regionHeight = maxY - minY + 1;
      if (tail < 20 || tail > 1000 || regionWidth / regionHeight <= 0.3
          || regionWidth / regionHeight >= 3 || tail / (regionWidth * regionHeight) <= 0.45) continue;
      regions += 1;
      for (let cursor = 0; cursor < tail; cursor += 1) {
        const index = queue[cursor];
        markerPixels[index] = 1;
        markerPixelIndices[markerPixelCount++] = index;
      }
      const centerX = sumX / tail, centerY = sumY / tail;
      let nearest = -1, nearestDistance = Infinity;
      for (let cursor = 0; cursor < tail; cursor += 1) {
        const index = queue[cursor];
        if (index % 2) continue;
        const dx = index % WIDTH - centerX, dy = Math.floor(index / WIDTH) - centerY;
        const distance = dx * dx + dy * dy;
        if (distance < nearestDistance) { nearest = index; nearestDistance = distance; }
      }
      if (nearest >= 0) markerCenterPixels[nearest] = 1;
    }

    const markerPositions = [];
    const markerCenterPositions = [];
    const markerCenterPixelPositions = [];
    const positions = new Float32Array(PREVIEW_POINT_LIMIT * 3);
    const markerFlags = new Uint8Array(PREVIEW_POINT_LIMIT);
    let count = 0;
    const rawBounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
    const projectedPoint = [0, 0, 0];
    const sampleBudget = PREVIEW_POINT_LIMIT - 512;
    const selectedMarkerBudget = Math.min(markerPixelCount, sampleBudget);
    const markerSelectionThreshold = markerPixelCount > selectedMarkerBudget
      ? selectedMarkerBudget / markerPixelCount : 1;
    const regularPixelCount = Math.max(1, currentValidPixels - markerPixelCount);
    const regularSelectionThreshold = currentValidPixels > PREVIEW_POINT_LIMIT
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
    for (let index = 0; index < markerPixelCount; index += 1) {
      const pixel = markerPixelIndices[index];
      if (!projectPixel(pixel % WIDTH, Math.floor(pixel / WIDTH), depths[pixel])) continue;
      markerPositions.push(projectedPoint[0], projectedPoint[1], projectedPoint[2]);
      if (markerCenterPixels[pixel]) {
        markerCenterPositions.push(projectedPoint[0], projectedPoint[1], projectedPoint[2]);
        markerCenterPixelPositions.push([pixel % WIDTH, Math.floor(pixel / WIDTH)]);
      }
    }
    for (let pixel = 0; pixel < PIXEL_COUNT && count < PREVIEW_POINT_LIMIT; pixel += 1) {
      const depth = depths[pixel];
      if (depth <= 0) continue;
      const isMarker = markerPixels[pixel] === 1;
      if (currentValidPixels > PREVIEW_POINT_LIMIT) {
        const threshold = isMarker ? markerSelectionThreshold : regularSelectionThreshold;
        if (hashPixel(pixel) / 0x1_0000_0000 >= threshold) continue;
      }
      if (!projectPixel(pixel % WIDTH, Math.floor(pixel / WIDTH), depth)) continue;
      markerFlags[count] = isMarker ? 1 : 0;
      positions[count * 3] = projectedPoint[0];
      positions[count * 3 + 1] = projectedPoint[1];
      positions[count * 3 + 2] = projectedPoint[2];
      for (let axis = 0; axis < 3; axis += 1) {
        rawBounds.min[axis] = Math.min(rawBounds.min[axis], projectedPoint[axis]);
        rawBounds.max[axis] = Math.max(rawBounds.max[axis], projectedPoint[axis]);
      }
      count += 1;
    }
    const compactPositions = positions.slice(0, count * 3);
    const compactMarkerFlags = markerFlags.slice(0, count);
    const compactMarkerPositions = Float64Array.from(markerPositions);
    const compactMarkerCenterPositions = Float64Array.from(markerCenterPositions);
    return {
      regions, positions: compactPositions, markerFlags: compactMarkerFlags, count, rawBounds,
      markerPositions: compactMarkerPositions, markerCenterPositions: compactMarkerCenterPositions,
      markerCenterPixelPositions,
    };
  }

  self.onmessage = event => {
    const { requestId, rawBuffer, matrix, scale } = event.data || {};
    try {
      const result = processFrame(rawBuffer, matrix, scale);
      const transfer = [result.positions.buffer, result.markerFlags.buffer, result.markerPositions.buffer,
        result.markerCenterPositions.buffer];
      self.postMessage({ requestId, result }, transfer);
    } catch (error) {
      self.postMessage({ requestId, error: error instanceof Error ? error.message : String(error) });
    }
  };
})();
