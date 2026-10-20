(() => {
  const initialize = ({ cloudButton, meshButton, viewport, viewer, getModelGeometry = () => null, meshReady = false }, location = window.location, fetcher = fetch) => {
    let livePreviewOwned = false;
    let cloudPreviewOpen = false;
    let meshPreviewOpen = false;
    let cloudSource = null;
    let cloudOriginSource = null;
    let cloudTableFrame = null;
    let renderedCloudSource = null;
    let cloudRequest = 0;
    let liveRequest = 0;
    let liveTimer = null;
    let liveMtime = null;
    let liveCalibration = null;
    let liveTableFrame = null;
    let liveFitRadius = null;
    let modelGeometry = null;
    const setAttribute = (element, name, value) => {
      const current = element.getAttribute(name);
      if (current !== value) element.setAttribute(name, value);
    };
    const updateButton = () => {
      const cloudSelected = cloudPreviewOpen;
      const meshSelected = meshPreviewOpen;
      const previewVisible = String(livePreviewOwned);
      viewport.dataset.scanPreviewMode = livePreviewOwned ? 'live' : cloudSelected ? 'cloud' : meshSelected ? 'mesh' : '';
      if (viewport.dataset.scanPreview !== previewVisible) viewport.dataset.scanPreview = previewVisible;
      setAttribute(cloudButton, 'aria-label', 'Облако точек');
      setAttribute(cloudButton, 'aria-pressed', String(cloudSelected));
      const cloudTitle = cloudSelected ? 'Скрыть предпросмотр скана'
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
      updateButton();
      const geometry = getModelGeometry() || modelGeometry;
      if (geometry && viewer) viewer.load(geometry, { reset: false });
      modelGeometry = null;
    };
    const pointCloudUrl = scan => {
      const url = new URL('/api/v1/scanner/point-cloud', location.origin);
      const pageParams = new URLSearchParams(location.search);
      const folder = scan ? scan.preview_folder : pageParams.get('scan_folder');
      const file = scan ? scan.preview_file : pageParams.get('scan_file');
      if (!folder || !file) return null;
      url.searchParams.set('folder', folder);
      url.searchParams.set('file', file);
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
      return url.href;
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
      let best = null, bestCount = 0;
      let seed = 0x6d2b79f5;
      const randomIndex = () => {
        seed = (Math.imul(seed ^ seed >>> 15, 1 | seed) + 0x6d2b79f5) | 0;
        let value = Math.imul(seed ^ seed >>> 7, 61 | seed) ^ seed;
        return ((value ^ value >>> 14) >>> 0) % sample.length;
      };
      for (let attempt = 0; attempt < 96; attempt += 1) {
        const a = sample[randomIndex()], b = sample[randomIndex()], c = sample[randomIndex()];
        const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
        const ac = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
        let normal = [ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0]];
        const length = Math.hypot(...normal);
        if (length < 1e-6) continue;
        normal = normal.map(value => value / length);
        const d = -(normal[0] * a[0] + normal[1] * a[1] + normal[2] * a[2]);
        let count = 0;
        for (const point of sample) if (Math.abs(normal[0] * point[0] + normal[1] * point[1] + normal[2] * point[2] + d) <= 2.5) count += 1;
        if (count > bestCount) { bestCount = count; best = { normal, d }; }
      }
      if (!best || bestCount < Math.max(6, sample.length * 0.1)) throw new Error('Не удалось надёжно определить плоскость стола в лайв-кадре.');
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
    cloudSource = pointCloudUrl(null);
    cloudOriginSource = originUrl(null);
    const loadCloud = async ({ reset = true } = {}) => {
      if (!viewer) throw new Error('Не найден основной 3D-предпросмотр.');
      if (!cloudSource) { cloudButton.title = 'Ожидание первого кадра сканирования'; return; }
      const request = ++cloudRequest;
      if (!cloudPreviewOpen) modelGeometry = getModelGeometry();
      const cloud = await readPointCloud(cloudSource);
      let frame = cloudTableFrame;
      if (!frame) {
        const origin = cloudOriginSource && cloudOriginSource !== cloudSource
          ? await readPointCloud(cloudOriginSource) : cloud;
        if (!liveCalibration) {
          const response = await fetcher('/api/v1/scanner/calibration', { cache: 'no-store' });
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
      clearTimeout(liveTimer);
      liveTimer = null;
      if (open && !cloudPreviewOpen) modelGeometry = getModelGeometry();
      meshPreviewOpen = false;
      cloudPreviewOpen = open;
      if (!open) { restoreModel(); updateButton(); return; }
      updateButton();
      try { await loadCloud({ reset: renderedCloudSource !== cloudSource }); }
      catch (error) { cloudPreviewOpen = false; restoreModel(); updateButton(); cloudButton.title = error.message; }
    };
    const showMesh = (open = true) => {
      livePreviewOwned = false;
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
      if (!livePreviewOwned) modelGeometry = getModelGeometry();
      liveMtime = null;
      liveTableFrame = null;
      liveFitRadius = null;
      cloudRequest += 1;
      cloudPreviewOpen = false;
      meshPreviewOpen = false;
      livePreviewOwned = true;
      const request = ++liveRequest;
      updateButton();
      const pollFrame = async () => {
        if (request !== liveRequest || !livePreviewOwned) return;
        try {
          if (!liveCalibration) {
            const response = await fetcher('/api/v1/scanner/calibration', { cache: 'no-store' });
            if (!response.ok) throw new Error(`Калибровка сканера недоступна (HTTP ${response.status}).`);
            const calibration = await response.json();
            if (!Array.isArray(calibration.matrix) || calibration.matrix.length !== 16
                || !Number.isFinite(calibration.depth_scale_mm_per_raw_unit)
                || calibration.depth_scale_mm_per_raw_unit <= 0) throw new Error('Калибровка глубины сканера некорректна.');
            liveCalibration = calibration;
          }
          const response = await fetcher(`/api/v1/scanner/live-frame?ts=${Date.now()}`, { cache: 'no-store' });
          if (response.ok) {
            const mtime = response.headers.get('X-CADYX-Frame-Mtime');
            if (mtime && mtime !== liveMtime) {
              const raw = new Uint8Array(await response.arrayBuffer());
              if (raw.length !== 640 * 400 * 2) throw new Error('Размер кадра Y16 не совпадает.');
              const { matrix, depth_scale_mm_per_raw_unit: scale } = liveCalibration;
              const positions = new Float32Array(640 * 400 * 3 / 2);
              let count = 0;
              for (let pixel = 0; pixel < 640 * 400; pixel += 2) {
                const value = raw[pixel * 2] | raw[pixel * 2 + 1] << 8;
                const depth = value * scale;
                if (!value || !Number.isFinite(depth) || depth < 1) continue;
                const x = pixel % 640, y = Math.floor(pixel / 640);
                const px = matrix[0] * x + matrix[1] * y + matrix[2] * depth + matrix[3];
                const py = matrix[4] * x + matrix[5] * y + matrix[6] * depth + matrix[7];
                const pz = matrix[8] * x + matrix[9] * y + matrix[10] * depth + matrix[11];
                if (!Number.isFinite(pz) || Math.abs(pz) < 1e-12) continue;
                const point = [px * depth / pz, -py * depth / pz, -depth];
                if (!point.every(Number.isFinite)) continue;
                positions[count * 3] = point[0];
                positions[count * 3 + 1] = point[1];
                positions[count * 3 + 2] = point[2];
                count += 1;
              }
              if (count >= 3 && request === liveRequest && livePreviewOwned) {
                if (!liveTableFrame) {
                  const ray = centerRay(liveCalibration);
                  liveTableFrame = fitTableFrame(positions.subarray(0, count * 3), ray);
                  if (typeof viewer.setCamera === 'function' && typeof viewer.getCamera === 'function') {
                    viewer.setCamera({ yaw: 0, pitch: 0,
                      zoom: viewer.getCamera().zoom, panX: 0, panY: 0, projection: 'orthographic' });
                  }
                }
                const tableFrame = liveTableFrame;
                let frameRadius = 0;
                const transformedBounds = transformToTableFrame(positions.subarray(0, count * 3), tableFrame);
                for (let index = 0; index < count * 3; index += 3) frameRadius = Math.max(frameRadius, Math.hypot(positions[index], positions[index + 1], positions[index + 2]));
                liveFitRadius ||= Math.max(frameRadius, 0.001);
                viewer.load({ positions: positions.slice(0, count * 3), triangles: new Uint32Array(0), lines: null,
                  bounds: transformedBounds, orbitCenter: [0, 0, 0], fitRadius: liveFitRadius },
                  { reset: liveMtime === null });
                liveMtime = mtime;
              }
            }
          } else if (response.status !== 404) throw new Error(`Кадр сканера недоступен (HTTP ${response.status}).`);
        } catch (error) {
          cloudButton.title = error.message;
        } finally {
          if (request === liveRequest && livePreviewOwned) liveTimer = setTimeout(pollFrame, 350);
        }
      };
      void pollFrame();
    };
    const closeLivePreview = () => {
      if (livePreviewOwned) {
        livePreviewOwned = false;
        restoreModel();
        liveMtime = null;
        liveTableFrame = null;
        liveFitRadius = null;
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
      if (open) { void showCloud(true); return; }
    };
    cloudButton.addEventListener('click', () => {
      if (cloudPreviewOpen) void showCloud(false);
      else void showCloud(true);
    });
    meshButton.addEventListener('click', () => {
      if (!meshReady) return;
      if (meshPreviewOpen) showMesh(false);
      else setMode('mesh');
    });
    updateButton();
    if (new URLSearchParams(location.search).get('live_scan') === '1') setOpen(true);
    return { setOpen, setMode, showCapture, showLivePreview, closeLivePreview };
  };

  const initializeLivePreview = ({ button, view, captureButton }, location = window.location, fetcher = fetch) => {
    let state = 'idle';
    let pending = false;
    let pollTimer = null;
    let previouslyActive = false;
    const active = () => state === 'starting' || state === 'running';
    const updateButton = preview => {
      state = preview?.state || 'idle';
      const nowActive = active();
      if (nowActive && !previouslyActive) view.showLivePreview({ open: true });
      else if (!nowActive && previouslyActive) view.closeLivePreview();
      previouslyActive = nowActive;
      button.setAttribute('aria-pressed', String(active()));
      button.title = active() ? 'Остановить предпросмотр сканера'
        : state === 'failed' ? preview.message || 'Сканер остановил поток'
          : 'Показать сканер в реальном времени';
      if (captureButton && active() && !captureButton.disabled) {
        captureButton.disabled = true;
        captureButton.dataset.livePreviewLock = 'true';
      } else if (captureButton && !active() && captureButton.dataset.livePreviewLock === 'true') {
        captureButton.disabled = false;
        delete captureButton.dataset.livePreviewLock;
      }
      if (state === 'failed') view.closeLivePreview();
    };
    const poll = async () => {
      try {
        const response = await fetcher('/api/v1/scanner/live-preview', { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error?.message || payload.message || 'Не удалось получить состояние сканера');
        updateButton(payload.preview);
      } catch (error) {
        updateButton({ state: 'failed', message: error.message });
      }
      clearTimeout(pollTimer);
      pollTimer = setTimeout(poll, active() ? 900 : 3500);
    };
    button.addEventListener('click', async () => {
      if (pending) return;
      pending = true;
      button.disabled = true;
      try {
        const stopping = active();
        const response = await fetcher('/api/v1/scanner/live-preview', stopping
          ? { method: 'DELETE' }
          : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
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
    return { poll, dispose: () => clearTimeout(pollTimer) };
  };

  window.CadyxScanView = { initialize, initializeLivePreview };
})();
