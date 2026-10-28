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
    const livePreviewButton = document.querySelector('#viewport-live-camera');
    const scanButtonTitle = scanButton?.title || '';
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
      if (status.textContent !== message) status.textContent = message;
      if (status.dataset.state !== state) status.dataset.state = state;
    };
    const spinActive = () => spinState === 'starting' || spinState === 'running' || spinState === 'stopping';
    const livePreviewReady = () => livePreviewButton?.getAttribute('aria-pressed') === 'true';
    const setBusy = () => {
      const spinning = spinActive();
      for (const selector of ['#turntable-control-refresh', '#turntable-step', '#turntable-speed',
        '#turntable-rotate-left', '#turntable-rotate-right', '#turntable-tilt-target',
        '#turntable-tilt-apply', '#turntable-zero', '#turntable-spin']) {
        const control = panel.querySelector(selector);
        if (control) {
          const disabled = pending || scanActive || spinning && control !== spinButton;
          if (control.disabled !== disabled) control.disabled = disabled;
        }
      }
      const stopButton = panel.querySelector('#turntable-stop');
      if (stopButton.disabled !== pending) stopButton.disabled = pending;
      const spinDisabled = pending || scanActive && !spinning
        || !spinning && (!tiltAtZero || !livePreviewReady());
      if (spinButton.disabled !== spinDisabled) spinButton.disabled = spinDisabled;
      if (spinButton.getAttribute('aria-pressed') !== String(spinning)) {
        spinButton.setAttribute('aria-pressed', String(spinning));
      }
      const spinLabel = spinState === 'starting' ? 'ПОДКЛЮЧЕНИЕ…'
        : spinState === 'stopping' ? 'ОСТАНОВКА…'
          : spinning ? '■ ОСТАНОВИТЬ ВРАЩЕНИЕ' : '▶ ПОСТОЯННОЕ ВРАЩЕНИЕ';
      if (spinButton.textContent !== spinLabel) spinButton.textContent = spinLabel;
      const spinTitle = !spinning && !livePreviewReady()
        ? 'Сначала включите предпросмотр сканера' : '';
      if (spinButton.title !== spinTitle) spinButton.title = spinTitle;
      if (scanButton) {
        const busyValue = String(spinning);
        if (scanButton.dataset.turntableBusy !== busyValue) scanButton.dataset.turntableBusy = busyValue;
        const captureLocked = String(scanButton.dataset.livePreviewLock === 'true' || spinning
          || scanButton.dataset.scanCaptureBusy === 'true' || scanButton.dataset.scanCapturePending === 'true');
        const lockChanged = scanButton.dataset.captureLocked !== captureLocked
          || scanButton.getAttribute('aria-disabled') !== captureLocked;
        if (scanButton.dataset.captureLocked !== captureLocked) scanButton.dataset.captureLocked = captureLocked;
        if (scanButton.getAttribute('aria-disabled') !== captureLocked) {
          scanButton.setAttribute('aria-disabled', captureLocked);
        }
        const captureTitle = spinning ? 'Остановите постоянное вращение перед 360°-сканированием' : scanButtonTitle;
        if (scanButton.title !== captureTitle) scanButton.title = captureTitle;
        if (lockChanged) window.cadyxSyncCaptureButton?.();
      }
    };
    const readPosition = position => {
      if (!position) return;
      if (Number.isFinite(position.angle_degrees)) {
        const nextAngle = finiteAngle(position.angle_degrees);
        if (angle.textContent !== nextAngle) angle.textContent = nextAngle;
      }
      if (Number.isFinite(position.tilt_degrees)) {
        tiltAtZero = Math.abs(position.tilt_degrees) <= 0.5;
        const nextTilt = finiteAngle(position.tilt_degrees);
        if (tilt.textContent !== nextTilt) tilt.textContent = nextTilt;
        const nextTarget = String(Math.max(-10, Math.min(10, Math.round(position.tilt_degrees))));
        if (tiltTarget.value !== nextTarget) tiltTarget.value = nextTarget;
        const nextTargetLabel = `${nextTarget}°`;
        if (tiltTargetValue.textContent !== nextTargetLabel) tiltTargetValue.textContent = nextTargetLabel;
      }
      if (Number.isFinite(position.rotation_period_seconds)) {
        const presets = [18, 30, 45, 60, 90];
        const nextSpeed = String(presets.reduce((nearest, preset) =>
          Math.abs(preset - position.rotation_period_seconds) < Math.abs(nearest - position.rotation_period_seconds)
            ? preset : nearest, presets[0]));
        if (speed.value !== nextSpeed) speed.value = nextSpeed;
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
        else if (spinState === 'starting') setStatus(control.spin?.message || 'Подключаюсь к столу…', 'running');
        else if (spinState === 'running') setStatus('Стол вращается · нажмите «Стоп»', 'running');
        else if (spinState === 'stopping') setStatus('Остановка вращения…', 'running');
        else if (spinState === 'failed') setStatus(control.spin.message || 'Драйвер остановил вращение с ошибкой.', 'error');
        else if (!tiltAtZero) setStatus('Для постоянного вращения установите наклон 0°');
        else if (Number.isFinite(control.position?.age_seconds) && control.position.age_seconds > 6)
          setStatus('Положение устарело · нажмите ↻ для обновления');
        else if (control.position?.command === 'query') setStatus('Стол подключён');
        else if (!control.position) setStatus('Нажмите ↻ для чтения положения стола');
        else setStatus('Положение стола недоступно', 'error');
        setBusy();
      } catch (error) {
        if (!quiet) setStatus(error.message, 'error');
      } finally {
        clearTimeout(pollTimer);
        const liveScanning = document.querySelector('.primary-grid')?.dataset.view === 'scanning';
        pollTimer = setTimeout(() => void refresh({ quiet: true }),
          spinState === 'starting' ? 600 : spinState === 'running' || spinState === 'stopping'
            ? 1500 : liveScanning ? 3000 : 15000);
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
        if (action === 'spin-start') spinState = result.state || 'failed';
        if (action === 'spin-stop') spinState = result.state || 'idle';
        if (action === 'spin-start' && spinState === 'starting') setStatus('Подключаюсь к столу…', 'running');
        else if (action === 'spin-start' && spinState === 'running') setStatus('Стол вращается · нажмите «Стоп»', 'running');
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

    panel.querySelector('#turntable-control-refresh').addEventListener('click', () => void command('query'));
    panel.querySelector('#turntable-rotate-left').addEventListener('click', () => void command('rotate', -Number(step.value)));
    panel.querySelector('#turntable-rotate-right').addEventListener('click', () => void command('rotate', Number(step.value)));
    speed.value = String(window.CadyxDeviceSettings?.turntableRotationPeriodSeconds?.() || 18);
    speed.addEventListener('change', () => void command('speed', Number(speed.value)));
    tiltTarget.addEventListener('input', () => { tiltTargetValue.textContent = `${tiltTarget.value}°`; });
    panel.querySelector('#turntable-tilt-apply').addEventListener('click', () => void command('tilt', Number(tiltTarget.value)));
    panel.querySelector('#turntable-zero').addEventListener('click', () => void command('zero'));
    panel.querySelector('#turntable-stop').addEventListener('click', () => {
      void command(spinActive() ? 'spin-stop' : 'stop');
    });
    spinButton.addEventListener('click', () => {
      void command(spinActive() ? 'spin-stop' : 'spin-start');
    });
    void command('query');
  };

  window.CadyxTurntableControls = { initialize };
})();
