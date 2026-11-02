(() => {
  const stepOptions = [30, 20, 18, 15, 12, 10, 9, 8, 6, 5, 4, 3, 2, 1];
  const dialog = document.querySelector('#scan-capture-settings-dialog');
  const closeButton = document.querySelector('#scan-capture-settings-close');
  const cancelButton = document.querySelector('#scan-capture-settings-cancel');
  const startButton = document.querySelector('#scan-capture-settings-start');
  const devicesNote = document.querySelector('#scan-capture-devices');
  const stepSelect = document.querySelector('#scan-capture-step');
  const tiltInput = document.querySelector('#scan-capture-tilt');
  const tiltValue = document.querySelector('#scan-capture-tilt-value');
  const status = document.querySelector('#scan-capture-settings-status');
  if (!dialog || !closeButton || !cancelButton || !startButton || !devicesNote
      || !stepSelect || !tiltInput || !tiltValue || !status) return;

  let resolvePending = null;
  let requestId = 0;

  const setStatus = (message = '', isError = false) => {
    if (status.textContent !== message) status.textContent = message;
    status.dataset.state = isError ? 'error' : 'idle';
  };
  const deviceSelection = () => ({
    scanner_device_id: window.CadyxDeviceSettings?.scannerDeviceId?.() || '',
    turntable_device_id: window.CadyxDeviceSettings?.turntableDeviceId?.() || '',
    profile_id: window.CadyxDeviceSettings?.turntableProfileId?.() || '',
  });
  const syncTiltDisplay = () => {
    const label = `${Number(tiltInput.value).toFixed(1).replace(/\.0$/, '')}°`;
    if (tiltValue.textContent !== label) tiltValue.textContent = label;
  };
  const fillSteps = selected => {
    stepSelect.replaceChildren(...stepOptions.map(step => {
      const frames = 360 / step;
      const option = new Option(`${step}° · ${frames} кадров`, String(step));
      option.selected = step === selected;
      return option;
    }));
  };
  const loadDeviceNames = async selection => {
    try {
      const response = await fetch('/api/v1/scanner/devices', { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message || payload.message || 'Не удалось загрузить устройства.');
      const scanner = (payload.scanners || []).find(item => item.device_id === selection.scanner_device_id);
      const table = (payload.turntables || []).find(item => item.device_id === selection.turntable_device_id);
      return { scannerName: scanner?.profile_name || selection.scanner_device_id,
               tableName: table?.profile_name || selection.turntable_device_id };
    } catch {
      return { scannerName: selection.scanner_device_id, tableName: selection.turntable_device_id };
    }
  };
  const loadSettings = async selection => {
    const query = new URLSearchParams({
      scanner_device_id: selection.scanner_device_id,
      turntable_device_id: selection.turntable_device_id,
    });
    if (selection.profile_id) query.set('profile_id', selection.profile_id);
    const response = await fetch(`/api/v1/scanner/scan-capture-settings?${query}`, { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error?.message || payload.message || 'Не удалось загрузить настройки съёмки.');
    return payload;
  };

  const open = () => {
    if (dialog.open) return Promise.resolve(null);
    const request = ++requestId;
    const selection = deviceSelection();
    setStatus();
    startButton.disabled = true;
    fillSteps(30);
    tiltInput.value = '0';
    syncTiltDisplay();
    if (!selection.scanner_device_id || !selection.turntable_device_id) {
      devicesNote.textContent = 'Сканер и стол не выбраны. Откройте «Устройства» и выберите подключённую пару.';
      setStatus('Выберите сканер и стол в настройках устройств.', true);
      dialog.showModal();
      return Promise.resolve(null);
    }
    devicesNote.textContent = 'Загружаю настройки для выбранной пары…';
    dialog.showModal();
    void loadDeviceNames(selection).then(async names => {
      if (request !== requestId || !dialog.open) return;
      devicesNote.textContent = `Сканер: ${names.scannerName} · Стол: ${names.tableName}`;
      try {
        const saved = await loadSettings(selection);
        if (request !== requestId || !dialog.open) return;
        if (Number.isInteger(saved.step_degrees)) fillSteps(saved.step_degrees);
        if (Number.isFinite(Number(saved.tilt_degrees))) {
          tiltInput.value = String(Math.max(-10, Math.min(10, Number(saved.tilt_degrees))));
        }
        syncTiltDisplay();
        setStatus(saved.configured ? 'Загружены сохранённые настройки пары.' : '');
        startButton.disabled = false;
      } catch (error) {
        if (request !== requestId || !dialog.open) return;
        setStatus(error.message, true);
        startButton.disabled = false;
      }
    });
    return new Promise(resolve => { resolvePending = resolve; });
  };
  const settle = value => {
    const resolve = resolvePending;
    resolvePending = null;
    requestId += 1;
    dialog.close();
    resolve?.(value);
  };

  tiltInput.addEventListener('input', syncTiltDisplay);
  closeButton.addEventListener('click', () => settle(null));
  cancelButton.addEventListener('click', () => settle(null));
  dialog.addEventListener('click', event => { if (event.target === dialog) settle(null); });
  dialog.addEventListener('cancel', () => settle(null));
  startButton.addEventListener('click', async () => {
    if (startButton.disabled) return;
    const selection = deviceSelection();
    if (!selection.scanner_device_id || !selection.turntable_device_id) {
      setStatus('Выберите сканер и стол в настройках устройств.', true);
      return;
    }
    startButton.disabled = true;
    setStatus('Сохраняю настройки…');
    try {
      const response = await fetch('/api/v1/scanner/scan-capture-settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...selection, step_degrees: Number(stepSelect.value),
                               tilt_degrees: Number(tiltInput.value) }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message || payload.message || 'Не удалось сохранить настройки съёмки.');
      settle({ step_degrees: Number(stepSelect.value), tilt_degrees: Number(tiltInput.value) });
    } catch (error) {
      setStatus(error.message, true);
      startButton.disabled = false;
    }
  });

  window.CadyxScanCaptureSettings = Object.freeze({ open });
})();
