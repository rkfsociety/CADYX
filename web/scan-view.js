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

  const initialize = ({ cloudButton, meshButton, viewport, layer, frame, meshReady = false }, location = window.location) => {
    const updateButton = () => {
      const visible = !layer.hidden;
      const cloudSelected = visible && layer.dataset.mode === 'cloud';
      const meshSelected = visible && layer.dataset.mode === 'mesh';
      viewport.dataset.scanPreview = String(visible);
      cloudButton.setAttribute('aria-label', 'Облако точек');
      cloudButton.setAttribute('aria-pressed', String(cloudSelected));
      cloudButton.title = cloudSelected ? 'Скрыть предпросмотр скана'
        : meshSelected ? 'Переключить на облако точек' : 'Показать облако точек';
      meshButton.disabled = !meshReady;
      meshButton.setAttribute('aria-pressed', String(meshSelected));
      meshButton.title = meshReady
        ? meshSelected ? 'Скрыть предпросмотр скана' : cloudSelected ? 'Переключить на сетку' : 'Показать сетку скана'
        : 'Сетка станет доступна после обработки облака точек';
    };
    const setMode = mode => {
      if (mode !== 'cloud' && mode !== 'mesh') throw new TypeError(`Unknown scan view: ${mode}`);
      if (mode === 'mesh' && !meshReady) return;
      if (layer.hidden) layer.hidden = false;
      layer.dataset.mode = mode;
      frame.title = mode === 'cloud'
        ? 'Облако точек скана: перетаскивание вращает, колесо меняет масштаб'
        : 'Треугольная сетка скана: перетаскивание вращает, колесо меняет масштаб';
      if (mode === 'cloud' && !frame.getAttribute('src')) frame.src = scannerUrlFromPage(location).href;
      frame.contentWindow?.postMessage({ type: 'cadyx-scan-view', mode }, location.origin);
      updateButton();
    };
    const setOpen = visible => {
      layer.hidden = !visible;
      if (visible) setMode(layer.dataset.mode || 'cloud');
      else updateButton();
    };
    const showCapture = scan => {
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
        frame.src = previewUrl.href;
        layer.dataset.mode = 'cloud';
      }
      setOpen(true);
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
      { type: 'cadyx-scan-view', mode: layer.dataset.mode || 'cloud' }, location.origin));
    updateButton();
    if (new URLSearchParams(location.search).get('live_scan') === '1') setOpen(true);
    return { setOpen, setMode, showCapture };
  };

  window.CadyxScanView = { initialize };
})();
