(() => {
  const finiteAngle = value => Number.isFinite(value) ? `${value.toFixed(1)}°` : '—';

  const initialize = () => {
    const panel = document.querySelector('#turntable-control-panel');
    if (!panel || panel.__cadyxTurntableControls) return;
    panel.__cadyxTurntableControls = true;
    const angle = document.querySelector('#turntable-angle');
    const tilt = document.querySelector('#turntable-tilt');
    const step = document.querySelector('#turntable-step');
    const speed = document.querySelector('#turntable-speed');
    const tiltTarget = document.querySelector('#turntable-tilt-target');
    const tiltTargetValue = document.querySelector('#turntable-tilt-target-value');
    const status = document.querySelector('#turntable-control-status');
    const spinButton = document.querySelector('#turntable-spin');
    const scanButton = document.querySelector('#viewport-scan-360');
    let scanActive = false;
    let spinState = 'idle';
    let tiltAtZero = false;
    let pending = false;
    let pollTimer = null;

    const selection = () => ({
      device_id: window.CadyxDeviceSettings?.turntableDeviceId?.() || undefined,
      profile_id: window.CadyxDeviceSettings?.turntableProfileId?.() || undefined,
    });
    const setStatus = (message, state = 'idle') => {
      status.textContent = message;
      status.dataset.state = state;
    };
    const setBusy = () => {
      const spinning = spinState === 'running' || spinState === 'stopping';
      for (const selector of ['#turntable-control-refresh', '#turntable-step', '#turntable-speed',
        '#turntable-rotate-left', '#turntable-rotate-right', '#turntable-tilt-target',
        '#turntable-tilt-apply', '#turntable-zero', '#turntable-spin']) {
        const control = panel.querySelector(selector);
        if (control) control.disabled = pending || scanActive || spinning && control !== spinButton;
      }
      panel.querySelector('#turntable-stop').disabled = pending;
      spinButton.disabled = pending || scanActive && !spinning || !spinning && !tiltAtZero;
      spinButton.setAttribute('aria-pressed', String(spinning));
      spinButton.textContent = spinState === 'stopping' ? 'ОСТАНОВКА…'
        : spinning ? '■ ОСТАНОВИТЬ ВРАЩЕНИЕ' : '▶ ПОСТОЯННОЕ ВРАЩЕНИЕ';
      if (scanButton) {
        scanButton.dataset.turntableBusy = String(spinning);
        scanButton.dataset.captureLocked = String(scanButton.dataset.livePreviewLock === 'true' || spinning
          || scanButton.dataset.scanCaptureBusy === 'true' || scanButton.dataset.scanCapturePending === 'true');
        scanButton.setAttribute('aria-disabled', scanButton.dataset.captureLocked);
        if (spinning) scanButton.title = 'Остановите постоянное вращение перед 360°-сканированием';
        window.cadyxSyncCaptureButton?.();
      }
    };
    const readPosition = position => {
      if (!position) return;
      if (Number.isFinite(position.angle_degrees)) angle.textContent = finiteAngle(position.angle_degrees);
      if (Number.isFinite(position.tilt_degrees)) {
        tiltAtZero = Math.abs(position.tilt_degrees) <= 0.5;
        tilt.textContent = finiteAngle(position.tilt_degrees);
        tiltTarget.value = String(Math.max(-10, Math.min(10, Math.round(position.tilt_degrees))));
        tiltTargetValue.textContent = `${tiltTarget.value}°`;
      }
      if (Number.isFinite(position.rotation_period_seconds)) {
        const presets = [18, 30, 45, 60, 90];
        speed.value = String(presets.reduce((nearest, preset) =>
          Math.abs(preset - position.rotation_period_seconds) < Math.abs(nearest - position.rotation_period_seconds)
            ? preset : nearest, presets[0]));
      }
    };
    const fetchJson = async (url, options) => {
      const response = await fetch(url, options);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message || payload.message || 'Команда стола не выполнена.');
      return payload;
    };
    const refresh = async ({ quiet = false } = {}) => {
      if (pending) return;
      const workspaceView = document.querySelector('.primary-grid')?.dataset.view;
      if (workspaceView !== 'scanning' && spinState !== 'running' && spinState !== 'stopping') {
        clearTimeout(pollTimer);
        pollTimer = setTimeout(() => void refresh({ quiet: true }), 6000);
        return;
      }
      try {
        const query = new URLSearchParams();
        const selected = selection();
        if (selected.device_id) query.set('device_id', selected.device_id);
        if (selected.profile_id) query.set('profile_id', selected.profile_id);
        const payload = await fetchJson(`/api/v1/scanner/turntable/control${query.size ? `?${query}` : ''}`, { cache: 'no-store' });
        const control = payload.control || {};
        scanActive = Boolean(control.spin?.scan_active);
        spinState = control.spin?.state || 'idle';
        readPosition(control.spin);
        readPosition(control.position);
        if (scanActive) setStatus('Сканирование активно · управление заблокировано', 'error');
        else if (spinState === 'running') setStatus('Стол вращается · нажмите «Стоп»', 'running');
        else if (spinState === 'stopping') setStatus('Остановка вращения…', 'running');
        else if (spinState === 'failed') setStatus(control.spin.message || 'Драйвер остановил вращение с ошибкой.', 'error');
        else if (!tiltAtZero) setStatus('Для постоянного вращения установите наклон 0°');
        else if (control.position?.command === 'query') setStatus('Стол подключён');
        else setStatus('Положение стола недоступно', 'error');
        setBusy();
      } catch (error) {
        if (!quiet) setStatus(error.message, 'error');
      } finally {
        clearTimeout(pollTimer);
        const liveScanning = document.querySelector('.primary-grid')?.dataset.view === 'scanning'
          && document.querySelector('#viewport-live-camera')?.checked;
        pollTimer = setTimeout(() => void refresh({ quiet: true }),
          spinState === 'running' || spinState === 'stopping' ? 1500 : liveScanning ? 3000 : 15000);
      }
    };
    const command = async (action, value) => {
      if (pending) return null;
      pending = true;
      setBusy();
      setStatus(action === 'spin-start' ? 'Запускаю постоянное вращение…' : 'Выполняю команду…');
      try {
        const payload = await fetchJson('/api/v1/scanner/turntable/control', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: action, ...selection(), ...(value === undefined ? {} : { value }) }),
        });
        const result = payload.result || {};
        if (action === 'speed') {
          window.CadyxDeviceSettings?.rememberTurntableRotationPeriodSeconds?.(Number(value));
          const selected = selection();
          if (selected.device_id && selected.profile_id) {
            await fetchJson('/api/v1/scanner/turntable/settings', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ turntable_device_id: result.turntable_device_id || selected.device_id,
                profile_id: result.profile_id || selected.profile_id, rotation_period_seconds: Number(value) }),
            });
          }
        }
        readPosition(result);
        if (action === 'spin-start') spinState = result.state === 'running' ? 'running' : result.state;
        if (action === 'spin-stop') spinState = result.state || 'idle';
        if (action === 'spin-start' && spinState === 'running') setStatus('Стол вращается · нажмите «Стоп»', 'running');
        else if (action === 'spin-stop' && spinState === 'stopping') setStatus('Остановка вращения…', 'running');
        else if (action === 'spin-stop') setStatus('Вращение остановлено');
        else if (result.command === 'query') setStatus('Стол подключён');
        else if (action === 'speed') setStatus(`Скорость задана: один оборот за ${value} с`);
        else setStatus(result.output?.split(/\r?\n/).filter(Boolean).at(-1) || 'Команда выполнена');
        return result;
      } catch (error) {
        setStatus(error.message, 'error');
        return null;
      } finally {
        pending = false;
        setBusy();
        clearTimeout(pollTimer);
        pollTimer = setTimeout(() => void refresh({ quiet: true }), 250);
      }
    };

    panel.querySelector('#turntable-control-refresh').addEventListener('click', () => void refresh());
    panel.querySelector('#turntable-rotate-left').addEventListener('click', () => void command('rotate', -Number(step.value)));
    panel.querySelector('#turntable-rotate-right').addEventListener('click', () => void command('rotate', Number(step.value)));
    speed.value = String(window.CadyxDeviceSettings?.turntableRotationPeriodSeconds?.() || 18);
    speed.addEventListener('change', () => void command('speed', Number(speed.value)));
    tiltTarget.addEventListener('input', () => { tiltTargetValue.textContent = `${tiltTarget.value}°`; });
    panel.querySelector('#turntable-tilt-apply').addEventListener('click', () => void command('tilt', Number(tiltTarget.value)));
    panel.querySelector('#turntable-zero').addEventListener('click', () => void command('zero'));
    panel.querySelector('#turntable-stop').addEventListener('click', () => {
      void command(spinState === 'running' || spinState === 'stopping' ? 'spin-stop' : 'stop');
    });
    spinButton.addEventListener('click', () => {
      void command(spinState === 'running' || spinState === 'stopping' ? 'spin-stop' : 'spin-start');
    });
    void refresh();
  };

  window.CadyxTurntableControls = { initialize };
})();
