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

  const initialize = ({ button, layer, frame, cloudTab, meshTab }, location = window.location) => {
    const setMode = mode => {
      if (mode !== 'cloud' && mode !== 'mesh') throw new TypeError(`Unknown scan view: ${mode}`);
      layer.dataset.mode = mode;
      cloudTab?.setAttribute('aria-selected', String(mode === 'cloud'));
      meshTab?.setAttribute('aria-selected', String(mode === 'mesh'));
      if (cloudTab) cloudTab.tabIndex = mode === 'cloud' ? 0 : -1;
      if (meshTab) meshTab.tabIndex = mode === 'mesh' ? 0 : -1;
      frame.title = mode === 'cloud'
        ? 'Облако точек скана: перетаскивание вращает, колесо меняет масштаб'
        : 'Треугольная сетка скана: перетаскивание вращает, колесо меняет масштаб';
      if (mode === 'cloud' && !frame.getAttribute('src')) frame.src = scannerUrlFromPage(location).href;
      frame.contentWindow?.postMessage({ type: 'cadyx-scan-view', mode }, location.origin);
    };
    const setOpen = visible => {
      layer.hidden = !visible;
      button.setAttribute('aria-pressed', String(visible));
      button.title = visible ? 'Скрыть скан' : 'Показать облако точек или сетку';
      if (visible) setMode(layer.dataset.mode || 'cloud');
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
    button.addEventListener('click', () => setOpen(layer.hidden));
    frame.addEventListener('load', () => frame.contentWindow?.postMessage(
      { type: 'cadyx-scan-view', mode: layer.dataset.mode || 'cloud' }, location.origin));
    cloudTab?.addEventListener('click', () => setMode('cloud'));
    meshTab?.addEventListener('click', () => setMode('mesh'));
    if (new URLSearchParams(location.search).get('live_scan') === '1') setOpen(true);
    return { setOpen, setMode, showCapture };
  };

  window.CadyxScanView = { initialize };
})();
