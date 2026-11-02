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
    const title = text('strong', 'scan-progress-title', data.detail || data.title);
    title.title = data.detail ? `${data.title} — ${data.detail}` : data.title;
    const row = (label, value, suffix) => {
      const section = document.createElement('div'); section.className = 'scan-progress-row';
      const heading = document.createElement('div'); heading.className = 'scan-progress-heading';
      heading.append(text('span', '', label), text('span', 'scan-progress-count', `${value} / ${data.total}`));
      const bar = document.createElement('progress'); bar.max = data.total || 1; bar.value = value;
      bar.setAttribute('aria-label', `${label}: ${value} из ${data.total}. ${suffix}`);
      section.title = `${label}: ${value} из ${data.total}. ${suffix}`;
      section.append(heading, bar); return section;
    };
    const remainingCapture = Math.max(0, data.total - data.captured);
    const remainingClean = Math.max(0, data.total - data.cleaned - data.failed);
    const items = [title, row('Съёмка', data.captured, `Осталось кадров: ${remainingCapture}`),
      row('Очистка', data.cleaned, `Осталось кадров: ${remainingClean}${data.failed ? ` · Ошибок: ${data.failed}` : ''}`)];
    container.replaceChildren(...items);
  }
  root.CadyxScanProgress = {summary, render};
  if (typeof module !== 'undefined') module.exports = {summary};
})(typeof window !== 'undefined' ? window : globalThis);
