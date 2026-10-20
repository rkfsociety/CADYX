(() => {
  const scannerUrlFromPage = location => {
    const pageParams = new URLSearchParams(location.search);
    const scannerUrl = new URL('/scanner', location.origin);
    scannerUrl.searchParams.set('embed', '1');
    const projectId = pageParams.get('project_id');
    if (projectId) scannerUrl.searchParams.set('project_id', projectId);
    const folder = pageParams.get('scan_folder'), file = pageParams.get('scan_file');
    if (folder && file) {
      scannerUrl.searchParams.set('cloud_folder', folder);
      scannerUrl.searchParams.set('cloud_file', file);
    }
    for (const [sources, target] of [[['scan_kind', 'cloud_kind'], 'cloud_kind'],
      [['scan_angle_deg', 'cloud_angle_deg'], 'cloud_angle_deg'],
      [['scan_frame_count', 'cloud_frame_count'], 'cloud_frame_count'],
      [['scan_cleanup', 'cloud_cleanup'], 'cloud_cleanup']]) {
      const value = sources.map(source => pageParams.get(source)).find(candidate => candidate !== null);
      if (value !== undefined) scannerUrl.searchParams.set(target, value);
    }
    return scannerUrl;
  };

  const liveScannerUrlFromPage = location => {
    const scannerUrl = new URL('/scanner', location.origin);
    scannerUrl.searchParams.set('embed', '1');
    scannerUrl.searchParams.set('live_only', '1');
    const projectId = new URLSearchParams(location.search).get('project_id');
    if (projectId) scannerUrl.searchParams.set('project_id', projectId);
    return scannerUrl;
  };

  const initialize = ({ cloudButton, meshButton, viewport, layer, frame, meshReady = false }, location = window.location) => {
    let livePreviewOwned = false;
    const setAttribute = (element, name, value) => {
      const current = element.getAttribute(name);
      if (current !== value) element.setAttribute(name, value);
    };
    const updateButton = () => {
      const visible = !layer.hidden;
      const cloudSelected = visible && layer.dataset.mode === 'cloud';
      const meshSelected = visible && layer.dataset.mode === 'mesh';
      const previewVisible = String(visible);
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
    const setMode = mode => {
      if (mode !== 'cloud' && mode !== 'mesh' && mode !== 'live') throw new TypeError(`Unknown scan view: ${mode}`);
      if (mode === 'mesh' && !meshReady) return;
      if (!layer.hidden && layer.dataset.mode === mode && frame.getAttribute('src')) {
        updateButton();
        return;
      }
      if (layer.hidden) layer.hidden = false;
      layer.dataset.mode = mode;
      frame.title = mode === 'live'
        ? 'Предпросмотр сканера в реальном времени'
        : mode === 'cloud' ? 'Облако точек скана: перетаскивание вращает, колесо меняет масштаб'
          : 'Треугольная сетка скана: перетаскивание вращает, колесо меняет масштаб';
      if (mode === 'live') frame.src = liveScannerUrlFromPage(location).href;
      else if (mode === 'cloud' && (!frame.getAttribute('src') || frame.src === 'about:blank'
        || new URL(frame.src).searchParams.has('live_only'))) {
        frame.src = scannerUrlFromPage(location).href;
      }
      frame.contentWindow?.postMessage({ type: 'cadyx-scan-view', mode: mode === 'mesh' ? 'mesh' : 'cloud' }, location.origin);
      updateButton();
    };
    const showLivePreview = ({ open = false } = {}) => {
      livePreviewOwned = true;
      if (frame.src !== liveScannerUrlFromPage(location).href) frame.src = liveScannerUrlFromPage(location).href;
      layer.dataset.mode = 'live';
      frame.title = 'Предпросмотр сканера в реальном времени';
      if (open && layer.hidden) layer.hidden = false;
      updateButton();
    };
    const closeLivePreview = () => {
      if (livePreviewOwned) {
        frame.src = 'about:blank';
        setOpen(false);
      }
      livePreviewOwned = false;
    };
    const setOpen = visible => {
      const nextHidden = !visible;
      if (layer.hidden !== nextHidden) layer.hidden = nextHidden;
      if (visible) setMode(layer.dataset.mode || 'cloud');
      else updateButton();
    };
    const showCapture = (scan, { open = false } = {}) => {
      if (livePreviewOwned) return;
      const previewUrl = new URL('/scanner', location.origin);
      previewUrl.searchParams.set('embed', '1');
      if (scan?.preview_file && scan?.preview_folder) {
        previewUrl.searchParams.set('cloud_folder', scan.preview_folder);
        previewUrl.searchParams.set('cloud_file', scan.preview_file);
        if (scan.preview_kind) previewUrl.searchParams.set('cloud_kind', scan.preview_kind);
        if (scan.preview_frame_count) previewUrl.searchParams.set('cloud_frame_count', String(scan.preview_frame_count));
      }
      const projectId = new URLSearchParams(location.search).get('project_id');
      if (projectId) previewUrl.searchParams.set('project_id', projectId);
      const previewChanged = frame.src !== previewUrl.href;
      if (previewChanged) {
        livePreviewOwned = false;
        frame.src = previewUrl.href;
        layer.dataset.mode = 'cloud';
        if (layer.hidden) updateButton();
        else setMode('cloud');
      }
      if (open) setOpen(true);
    };
    cloudButton.addEventListener('click', () => {
      if (layer.hidden) setMode('cloud');
      else if (layer.dataset.mode === 'cloud') setOpen(false);
      else setMode('cloud');
    });
    meshButton.addEventListener('click', () => {
      if (!meshReady) return;
      if (layer.hidden) setMode('mesh');
      else if (layer.dataset.mode === 'mesh') setOpen(false);
      else setMode('mesh');
    });
    frame.addEventListener('load', () => frame.contentWindow?.postMessage(
      { type: 'cadyx-scan-view', mode: layer.dataset.mode === 'mesh' ? 'mesh' : 'cloud' }, location.origin));
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
