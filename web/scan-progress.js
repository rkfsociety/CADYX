(function (root) {
  function summary(scan) {
    if (!scan || scan.state === 'idle') return null;
    const total = Number.isSafeInteger(scan.frame_count) && scan.frame_count > 0 ? scan.frame_count : 0;
    const count = value => Math.max(0, Math.min(total, Number.isSafeInteger(value) ? value : 0));
    const captured = count(scan.completed_frames), cleaned = count(scan.preprocessed_frames);
    const failed = count(scan.preprocessing_failed_frames);
    const current = scan.preprocessing_current_frame;
    const processing = scan.preprocessing_status === 'running' && current && typeof current.source_file === 'string' ? current.source_file : null;
    const running = scan.state === 'running';
    const preprocessingFailed = scan.preprocessing_status === 'failed' || failed > 0;
    const cleanupComplete = !preprocessingFailed && !['running', 'partial'].includes(scan.preprocessing_status) &&
      ((total > 0 && cleaned === total) || (total === 0 && scan.preprocessing_status === 'completed'));
    const title = scan.state === 'failed' ? 'Сканирование остановлено с ошибкой' :
      scan.state === 'completed' && preprocessingFailed ? 'Съёмка завершена, очистка с ошибками' :
      scan.state === 'completed' && cleanupComplete ? 'Съёмка и очистка завершены' :
      scan.state === 'completed' && ['running', 'partial'].includes(scan.preprocessing_status) ? 'Съёмка завершена, очистка продолжается' :
      scan.state === 'completed' ? 'Съёмка завершена, очистка не подтверждена' :
      scan.state === 'busy' ? 'Сканер занят' : captured < total ? 'Съёмка и обработка' : 'Обработка кадров';
    const detail = scan.state === 'failed' ? (scan.message || 'Не удалось завершить съёмку') :
      processing ? `Обрабатывается: ${processing}` :
      (running || scan.state === 'completed') && scan.preprocessing_status === 'running' ? 'Очистка кадров выполняется' :
      running && captured === total ? 'Завершение съёмки и обработки' : '';
    return {title, detail, total, captured, cleaned, failed, state: scan.state};
  }
  function render(container, scan, fusion = null) {
    const hasFusion = fusion && ['running', 'completed', 'failed'].includes(fusion.state);
    const sameSeries = !hasFusion || !fusion.run_id || scan?.preview_subfolder === fusion.run_id;
    if (hasFusion && !sameSeries) scan = {state:'busy', frame_count:0};
    scan = scan && typeof scan === 'object' ? scan : hasFusion ? {state:'busy', frame_count:0} : null;
    const data = summary(scan) || (hasFusion ? summary({state:'busy', frame_count:0}) : null);
    if (!data) { container.replaceChildren(); container.hidden = true; return; }
    container.hidden = false;
    container.className = 'scan-progress';
    container.dataset.state = data.state;
    const text = (tag, className, value) => { const node = document.createElement(tag); node.className = className; node.textContent = value; return node; };
    const stage = (number, label, value, total, state, note) => {
      const section = document.createElement('div'); section.className = 'scan-progress-stage';
      section.dataset.stageState = state;
      section.setAttribute('role', 'group');
      section.setAttribute('aria-label', `${label}: ${note}`);
      const badge = text('span', 'scan-progress-badge', String(number));
      const body = document.createElement('div'); body.className = 'scan-progress-body';
      const heading = document.createElement('div'); heading.className = 'scan-progress-heading';
      const maximum = Number.isSafeInteger(total) && total > 0 ? total : 0;
      const progress = maximum && Number.isSafeInteger(value) ? Math.max(0, Math.min(maximum, value)) : 0;
      heading.append(text('strong', '', label), text('span', 'scan-progress-count', maximum ? `${progress} / ${maximum}` : '—'));
      const bar = document.createElement('progress'); bar.max = maximum || 1; bar.value = progress;
      bar.setAttribute('aria-label', `${label}: ${note}`);
      bar.setAttribute('aria-hidden', String(!maximum));
      body.append(heading, bar, text('small', 'scan-progress-note', note));
      section.append(badge, body); return section;
    };
    const remainingCapture = Math.max(0, data.total - data.captured);
    const remainingClean = Math.max(0, data.total - data.cleaned - data.failed);
    const captureAvailable = Boolean(data.total) && data.state !== 'busy';
    const cleanAvailable = ['running', 'partial', 'completed', 'failed'].includes(scan.preprocessing_status)
      || data.cleaned > 0 || data.failed > 0;
    const captureState = !captureAvailable ? 'unknown' : !remainingCapture ? 'completed' : data.state === 'failed' ? 'failed' : 'active';
    const cleanState = !cleanAvailable ? 'unknown' : data.failed || scan.preprocessing_status === 'failed' ? 'failed' :
      scan.preprocessing_status === 'running' ? 'active' :
      (data.total && !remainingClean) || (scan.preprocessing_status === 'completed' && !data.total) ? 'completed' : 'waiting';
    const captureNote = captureState === 'unknown' ? 'Нет данных' : captureState === 'completed' ? 'Съёмка завершена' :
      captureState === 'failed' ? 'Съёмка остановлена' : `Осталось кадров: ${remainingCapture}`;
    const cleanNote = cleanState === 'unknown' ? 'Нет данных' : cleanState === 'failed' ? `Ошибок: ${data.failed || 'неизвестно'}` :
      cleanState === 'completed' ? 'Очистка завершена' : `Осталось кадров: ${remainingClean}`;
    const registrationPhases = ['load', 'register', 'loops', 'optimize', 'merge'];
    const registrationDone = hasFusion && (fusion.state === 'completed' || ['cleanup', 'publishing', 'completed'].includes(fusion.phase));
    const needsReview = hasFusion && fusion.state === 'completed' && fusion.result?.quality === 'review_required';
    const registrationState = !hasFusion ? 'unknown' : registrationDone ? (needsReview ? 'warning' : 'completed') :
      fusion.state === 'failed' ? 'failed' : 'active';
    const publishingFailed = hasFusion && fusion.state === 'failed' && fusion.phase === 'publishing';
    const cleanupDone = hasFusion && fusion.state !== 'failed' &&
      (fusion.state === 'completed' || ['publishing', 'completed'].includes(fusion.phase));
    const finalState = !hasFusion ? 'unknown' : cleanupDone ? 'completed' :
      fusion.state === 'failed' && ['cleanup', 'publishing', 'completed'].includes(fusion.phase) ? 'failed' :
      fusion.phase === 'cleanup' ? 'active' : 'waiting';
    const phaseNames = {starting:'Запуск', load:'Подготовка кадров', register:'Совмещение кадров',
      loops:'Проверка замыкания', optimize:'Оптимизация', merge:'Объединение', cleanup:'Очистка облака',
      publishing:'Сохранение результатов', completed:'Завершено'};
    const registrationNote = !hasFusion ? 'Нет данных' : registrationState === 'failed' ? 'Сшивка остановлена' :
      needsReview ? 'Требует проверки' : registrationDone ? 'Сшивка завершена' : (phaseNames[fusion.phase] || 'Выполняется');
    const finalNote = !hasFusion ? 'Нет данных' : finalState === 'failed' ? (publishingFailed ? 'Сохранение результата остановлено' : 'Очистка остановлена') :
      cleanupDone ? 'Очистка завершена' : finalState === 'active' ? 'Очистка облака' : 'Ожидает';
    const regMeasured = hasFusion && registrationPhases.includes(fusion.phase) && Number.isSafeInteger(fusion.total) && fusion.total > 0;
    const cleanupMeasured = hasFusion && fusion.phase === 'cleanup' && Number.isSafeInteger(fusion.total) && fusion.total > 0;
    const current = document.createElement('div'); current.className = 'scan-progress-current';
    const label = text('small', '', 'Текущий файл');
    const scanFile = ['running', 'failed'].includes(scan.preprocessing_status) ? scan.preprocessing_current_frame?.source_file : null;
    const file = text('strong', 'scan-progress-file', (hasFusion ? fusion.file || fusion.result?.cleaned : scanFile) || 'Нет данных');
    file.title = file.textContent;
    const detail = text('small', 'scan-progress-current-note', hasFusion ? (fusion.state === 'failed' ? (fusion.next_action || fusion.error || 'Ошибка сшивки') : fusion.state === 'completed' ? (needsReview ? 'Внимание: проверьте совмещение' : 'Результат предварительный') : (phaseNames[fusion.phase] || 'Запуск')) : data.state === 'failed' ? (scan.message || data.title) :
      cleanState === 'active' ? `Осталось очистить: ${remainingClean}` : captureState === 'active' ? `Осталось снять: ${remainingCapture}` : data.title);
    current.append(label, file, detail);
    detail.title = detail.textContent;
    container.replaceChildren(
      stage(1, 'Съёмка', data.captured, captureAvailable ? data.total : 0, captureState, captureNote),
      stage(2, 'Очистка кадров', data.cleaned, cleanAvailable ? data.total : 0, cleanState, cleanNote),
      stage(3, 'Сшивка', registrationDone ? 1 : regMeasured ? fusion.done : 0, registrationDone ? 1 : regMeasured ? fusion.total : 0, registrationState, registrationNote),
      stage(4, 'Финальная очистка', cleanupDone ? 1 : cleanupMeasured ? fusion.done : 0, cleanupDone ? 1 : cleanupMeasured ? fusion.total : 0, finalState, finalNote), current);
  }
  root.CadyxScanProgress = {summary, render};
  if (typeof module !== 'undefined') module.exports = {summary, render};
})(typeof window !== 'undefined' ? window : globalThis);
