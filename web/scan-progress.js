(function (root) {
  function summary(scan) {
    if (!scan || scan.state === 'idle') return null;
    const total = Number.isInteger(scan.frame_count) && scan.frame_count > 0 ? scan.frame_count : 0;
    const count = value => Math.max(0, Math.min(total, Number.isInteger(value) ? value : 0));
    const captured = count(scan.completed_frames), cleaned = count(scan.preprocessed_frames);
    const failed = count(scan.preprocessing_failed_frames);
    const current = scan.preprocessing_current_frame;
    const processing = current && typeof current.source_file === 'string' ? current.source_file : null;
    const running = scan.state === 'running';
    const title = scan.state === 'failed' ? 'Сканирование остановлено с ошибкой' :
      scan.state === 'completed' ? 'Съёмка и очистка завершены' :
      scan.state === 'busy' ? 'Сканер занят' : captured < total ? 'Съёмка и обработка' : 'Обработка кадров';
    const detail = processing ? `Обрабатывается: ${processing}` :
      running && scan.preprocessing_status === 'running' ? 'Очистка кадров выполняется' :
      running && captured === total ? 'Завершение съёмки и обработки' :
      scan.state === 'failed' ? (scan.message || 'Не удалось завершить съёмку') : '';
    return {title, detail, total, captured, cleaned, failed, state: scan.state};
  }
  function render(container, scan) {
    const data = summary(scan);
    if (!data) { container.replaceChildren(); container.hidden = true; return; }
    container.hidden = false;
    container.className = 'scan-progress';
    container.dataset.state = data.state;
    const text = (tag, className, value) => { const node = document.createElement(tag); node.className = className; node.textContent = value; return node; };
    const stage = (number, label, value, total, state, note) => {
      const section = document.createElement('div'); section.className = 'scan-progress-stage';
      section.dataset.stageState = state;
      const badge = text('span', 'scan-progress-badge', String(number));
      const body = document.createElement('div'); body.className = 'scan-progress-body';
      const heading = document.createElement('div'); heading.className = 'scan-progress-heading';
      heading.append(text('strong', '', label), text('span', 'scan-progress-count', total ? `${value} / ${total}` : '—'));
      const bar = document.createElement('progress'); bar.max = total || 1; bar.value = value;
      bar.setAttribute('aria-label', `${label}: ${note}`);
      body.append(heading, bar, text('small', 'scan-progress-note', note));
      section.append(badge, body); return section;
    };
    const remainingCapture = Math.max(0, data.total - data.captured);
    const remainingClean = Math.max(0, data.total - data.cleaned - data.failed);
    const captureAvailable = Boolean(data.total) && data.state !== 'busy';
    const cleanAvailable = ['running', 'partial', 'completed', 'failed'].includes(scan.preprocessing_status);
    const captureState = !captureAvailable ? 'unknown' : !remainingCapture ? 'completed' : data.state === 'failed' ? 'failed' : 'active';
    const cleanState = !cleanAvailable ? 'unknown' : data.failed || scan.preprocessing_status === 'failed' ? 'failed' :
      data.total && !remainingClean ? 'completed' : scan.preprocessing_status === 'running' ? 'active' : 'waiting';
    const captureNote = captureState === 'unknown' ? 'Нет данных' : captureState === 'completed' ? 'Съёмка завершена' :
      captureState === 'failed' ? 'Съёмка остановлена' : `Осталось кадров: ${remainingCapture}`;
    const cleanNote = cleanState === 'unknown' ? 'Нет данных' : cleanState === 'failed' ? `Ошибок: ${data.failed || 'неизвестно'}` :
      cleanState === 'completed' ? 'Очистка завершена' : `Осталось кадров: ${remainingClean}`;
    const current = document.createElement('div'); current.className = 'scan-progress-current';
    const label = text('small', '', 'Текущий файл');
    const file = text('strong', 'scan-progress-file', scan.preprocessing_current_frame?.source_file || 'Нет данных');
    file.title = file.textContent;
    const detail = text('small', 'scan-progress-current-note', data.state === 'failed' ? (scan.message || data.title) :
      cleanState === 'active' ? `Осталось очистить: ${remainingClean}` : captureState === 'active' ? `Осталось снять: ${remainingCapture}` : data.title);
    current.append(label, file, detail);
    container.replaceChildren(
      stage(1, 'Съёмка', data.captured, captureAvailable ? data.total : 0, captureState, captureNote),
      stage(2, 'Очистка кадров', data.cleaned, cleanAvailable ? data.total : 0, cleanState, cleanNote),
      stage(3, 'Сшивка', 0, 0, 'unknown', 'Нет данных'),
      stage(4, 'Финальная очистка', 0, 0, 'unknown', 'Нет данных'), current);
  }
  root.CadyxScanProgress = {summary, render};
  if (typeof module !== 'undefined') module.exports = {summary, render};
})(typeof window !== 'undefined' ? window : globalThis);
