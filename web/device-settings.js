(() => {
  const storageKey = 'cadyx-device-settings-v1';
  const allowedPeriods = new Set([18, 30, 45, 60, 90]);
  const defaults = { activeDevice: 'scanner', scannerDeviceId: '', turntableDeviceId: '', turntableRotationPeriodSeconds: 18 };
  const read = () => {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey) || '{}');
      return {
        activeDevice: value.activeDevice === 'turntable' ? 'turntable' : 'scanner',
        scannerDeviceId: typeof value.scannerDeviceId === 'string' ? value.scannerDeviceId : '',
        turntableDeviceId: typeof value.turntableDeviceId === 'string' ? value.turntableDeviceId : '',
        turntableRotationPeriodSeconds: allowedPeriods.has(Number(value.turntableRotationPeriodSeconds))
          ? Number(value.turntableRotationPeriodSeconds) : defaults.turntableRotationPeriodSeconds,
      };
    } catch {
      return { ...defaults };
    }
  };
  const dialog = document.querySelector('#device-settings-dialog');
  const open = document.querySelector('#device-settings-open');
  const close = document.querySelector('#device-settings-close');
  const save = document.querySelector('#device-settings-save');
  const refresh = document.querySelector('#device-settings-refresh');
  const scannerSelect = document.querySelector('#scanner-device-select');
  const tableSelect = document.querySelector('#turntable-device-select');
  const qFile = document.querySelector('#scanner-q-matrix');
  const depthScale = document.querySelector('#scanner-depth-scale');
  const period = document.querySelector('#turntable-rotation-period');
  const status = document.querySelector('#device-settings-status');
  if (!dialog || !open || !close || !save || !refresh || !scannerSelect || !tableSelect || !qFile || !depthScale || !period || !status) return;

  let devices = { scanners: [], turntables: [] };
  let activeDevice = read().activeDevice;
  const selectDevice = (name) => {
    activeDevice = name === 'turntable' ? 'turntable' : 'scanner';
    for (const tab of dialog.querySelectorAll('[data-device-tab]')) {
      const selected = tab.dataset.deviceTab === activeDevice;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }
    for (const panel of dialog.querySelectorAll('[data-device-panel]')) {
      panel.hidden = panel.dataset.devicePanel !== activeDevice;
    }
  };
  const options = (select, list, selectedId, placeholder) => {
    select.replaceChildren(new Option(placeholder, ''));
    const profileCounts = new Map();
    for (const device of list) {
      profileCounts.set(device.profile_name, (profileCounts.get(device.profile_name) || 0) + 1);
    }
    const profileOrdinals = new Map();
    for (const device of list) {
      const ordinal = (profileOrdinals.get(device.profile_name) || 0) + 1;
      profileOrdinals.set(device.profile_name, ordinal);
      const name = profileCounts.get(device.profile_name) > 1
        ? `${device.profile_name} ${ordinal}` : device.profile_name;
      select.add(new Option(name, device.device_id));
    }
    const preferred = list.find(item => item.device_id === selectedId) || (list.length === 1 ? list[0] : null);
    select.value = preferred?.device_id || '';
  };
  const updateCalibrationStatus = () => {
    const scanner = devices.scanners.find(item => item.device_id === scannerSelect.value);
    if (scanner?.calibrated) {
      status.textContent = 'Калибровка сканера настроена.';
    } else if (scanner) {
      status.textContent = 'Для этого сканера нужно добавить калибровку.';
    }
  };
  const discover = async () => {
    refresh.disabled = true;
    status.textContent = 'Идёт поиск USB-сканеров и Bluetooth-столов…';
    try {
      const response = await fetch('/api/v1/scanner/devices', { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || payload.error || 'Не удалось найти устройства.');
      devices = { scanners: payload.scanners || [], turntables: payload.turntables || [] };
      const saved = read();
      options(scannerSelect, devices.scanners, saved.scannerDeviceId, 'Выберите подключённый сканер');
      options(tableSelect, devices.turntables, saved.turntableDeviceId, 'Выберите подключённый стол');
      status.textContent = `Найдено: сканеров ${devices.scanners.length}, столов ${devices.turntables.length}.`;
      updateCalibrationStatus();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      refresh.disabled = false;
    }
  };
  for (const tab of dialog.querySelectorAll('[data-device-tab]')) {
    tab.addEventListener('click', () => selectDevice(tab.dataset.deviceTab));
  }
  scannerSelect.addEventListener('change', () => {
    qFile.value = '';
    depthScale.value = '';
    updateCalibrationStatus();
  });
  open.addEventListener('click', () => {
    const saved = read();
    selectDevice(saved.activeDevice);
    period.value = String(saved.turntableRotationPeriodSeconds);
    dialog.showModal();
    discover();
  });
  close.addEventListener('click', () => dialog.close());
  refresh.addEventListener('click', discover);
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  save.addEventListener('click', async () => {
    if (!scannerSelect.value || !tableSelect.value) {
      status.textContent = 'Выберите подключённый сканер и стол.';
      return;
    }
    const value = Number(period.value);
    if (!allowedPeriods.has(value)) {
      status.textContent = 'Выберите поддерживаемый период оборота.';
      return;
    }
    const scanner = devices.scanners.find(item => item.device_id === scannerSelect.value);
    const file = qFile.files?.[0];
    if (file || !scanner?.calibrated) {
      const scale = Number(depthScale.value);
      if (!file || file.size !== 64 || !Number.isFinite(scale) || scale <= 0) {
        status.textContent = 'Для нового сканера выберите Q.bin размером 64 байта и укажите масштаб глубины.';
        return;
      }
      save.disabled = true;
      try {
        const bytes = new Uint8Array(await file.arrayBuffer());
        let binary = '';
        for (const byte of bytes) binary += String.fromCharCode(byte);
        const response = await fetch('/api/v1/scanner/calibration', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scanner_device_id: scanner.device_id, q_matrix_base64: btoa(binary), depth_scale_mm_per_raw_unit: scale }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.message || result.error || 'Не удалось сохранить калибровку.');
        scanner.calibrated = true;
      } catch (error) {
        status.textContent = error.message;
        save.disabled = false;
        return;
      }
      save.disabled = false;
    }
    const settings = {
      activeDevice,
      scannerDeviceId: scannerSelect.value,
      turntableDeviceId: tableSelect.value,
      turntableRotationPeriodSeconds: value,
    };
    try {
      localStorage.setItem(storageKey, JSON.stringify(settings));
      status.textContent = 'Выбор устройств и настройки сохранены.';
    } catch {
      status.textContent = 'Браузер не разрешил сохранить выбор устройств.';
    }
  });
  selectDevice(activeDevice);

  window.CadyxDeviceSettings = Object.freeze({
    scannerDeviceId: () => read().scannerDeviceId || null,
    turntableDeviceId: () => read().turntableDeviceId || null,
    turntableRotationPeriodSeconds: () => read().turntableRotationPeriodSeconds,
  });
})();
