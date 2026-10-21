const apiErrorLabels = {
  CADYX_ERROR: "Внутренняя ошибка CADYX.",
  VALIDATION_ERROR: "Параметры запроса не прошли проверку.",
  NOT_FOUND: "Проект, файл или объект не найден.",
  CONFLICT: "Не удалось применить изменение из-за конфликта состояния.",
  EDIT_LOCK_REQUIRED: "Для изменения проекта требуется блокировка редактирования.",
  PROJECT_BUSY: "Проект занят другой операцией. Повторите попытку позже.",
  REVISION_CONFLICT: "Проект изменился. Обновите данные и повторите действие.",
  AGENT_STOPPING: "Агент завершает работу и не принимает новые команды.",
  PROJECT_LOCKED: "Проект заблокирован другой операцией.",
  LEASE_EXPIRED: "Срок блокировки проекта истёк.",
  LEASE_RECOVERY_REQUIRED: "Для продолжения требуется восстановить блокировку.",
  LEASE_NOT_EXPIRED: "Блокировка проекта ещё активна.",
  DUPLICATE_PROJECT_ID: "Проект с таким идентификатором уже существует.",
  PROJECT_FOLDER_CONFLICT: "Не удалось подобрать имя папки для проекта.",
  ARTIFACT_CONFLICT: "Файл результата с таким именем уже существует.",
  PROJECT_GROUP_EXISTS: "Папка с таким именем уже существует.",
  SCAN_OPERATION_FAILED: "Не удалось импортировать PLY как облако точек. Нужен point-only PLY минимум с тремя точками.",
};

function apiErrorMessage(value, status) {
  if (apiErrorLabels[value?.error]) return apiErrorLabels[value.error];
  if (status >= 500) return "Сервер CADYX не смог выполнить запрос.";
  if (status === 404) return "Запрошенные данные не найдены.";
  return "Запрос отклонён. Проверьте данные и повторите попытку.";
}

const api = async (path, options = {}) => {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
  } catch (error) {
    if (error instanceof TypeError) throw new Error("Нет связи с сервером CADYX.", { cause: error });
    throw error;
  }
  const contentType = response.headers.get("content-type") || "";
  let value = {};
  if (contentType.includes("application/json")) {
    try { value = await response.json(); }
    catch (error) { throw new Error("Сервер CADYX вернул некорректный ответ.", { cause: error }); }
  }
  if (!response.ok) {
    const error = new Error(apiErrorMessage(value, response.status));
    error.status = response.status;
    error.code = value.error;
    throw error;
  }
  return value;
};
const loadedUiVersion = document.querySelector('meta[name="cadyx-ui-version"]')?.content || null;

const $ = (selector) => document.querySelector(selector);
const projectDetail = $("#project-detail");
const noSelection = $("#no-selection");
const folderFileList = $("#folder-file-list");
const folderPreviewCanvas = $("#ply-preview-canvas");
const folderPreviewPlaceholder = $("#folder-preview-placeholder");
const folderImportModelFileButton = $("#folder-import-model-file");
const plyViewer = window.CadyxViewport.createViewer(folderPreviewCanvas);
const modelViewer = window.CadyxViewport.createViewer($("#model-preview"), {
  showViewCube: true, wireframe: true, onRender: queueViewportSnapshot,
});
const emptyViewportGeometry = {
  positions: new Float64Array(0), triangles: new Uint32Array(0), lines: [], points: [],
  bounds: { min: [0, 0, 0], max: [0, 0, 0] },
};
let currentModelViewportGeometry = emptyViewportGeometry;
const orthographicViewers = [
  { viewer: window.CadyxViewport.createViewer($("#ortho-front"), { showCoordinateGizmo: false, showRotationAxes: false, showWorldAxes: false, wireframe: true, shaded: false, wireframeColor: "#c7d5d8", wireframeOpacity: 0.86 }), camera: { yaw: 0, pitch: 0 } },
  { viewer: window.CadyxViewport.createViewer($("#ortho-right"), { showCoordinateGizmo: false, showRotationAxes: false, showWorldAxes: false, wireframe: true, shaded: false, wireframeColor: "#c7d5d8", wireframeOpacity: 0.86 }), camera: { yaw: Math.PI / 2, pitch: 0 } },
  { viewer: window.CadyxViewport.createViewer($("#ortho-top"), { showCoordinateGizmo: false, showRotationAxes: false, showWorldAxes: false, wireframe: true, shaded: false, wireframeColor: "#c7d5d8", wireframeOpacity: 0.86 }), camera: { yaw: 0, pitch: Math.PI / 2 } },
];
orthographicViewers.forEach((view) => {
  view.viewer.setCamera({ ...view.camera, zoom: 1, projection: "orthographic" });
});
const cameraPresetButtons = [...document.querySelectorAll("[data-camera-preset]")];
const cameraPresets = {
  isometric: { yaw: 0.68, pitch: 0.38 },
  front: { yaw: 0, pitch: 0 },
  right: { yaw: Math.PI / 2, pitch: 0 },
  top: { yaw: 0, pitch: Math.PI / 2 },
};
function setCameraPresetState(selectedPreset) {
  cameraPresetButtons.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.cameraPreset === selectedPreset));
  });
}
$("#viewport-camera-presets").addEventListener("click", (event) => {
  const button = event.target.closest("[data-camera-preset]");
  const preset = button?.dataset.cameraPreset;
  if (!preset || !cameraPresets[preset]) return;
  modelViewer.setCamera({ ...cameraPresets[preset], zoom: modelViewer.getCamera().zoom, projection: "orthographic" });
  setCameraPresetState(preset);
});
$("#model-preview").addEventListener("pointerdown", () => setCameraPresetState(null));
$("#model-preview").addEventListener("wheel", () => setCameraPresetState(null), { passive: true });
let cameraAnimationId = 0;
let renderedSceneKey = null;
let renderedSceneProjectId = null;
let renderedSceneRevision = null;
let viewportSnapshotTimer = 0;
let viewportSnapshotLastSentAt = 0;
let viewportSnapshotInFlight = false;
let viewportSnapshotDirty = false;
let viewportSnapshotErrorShown = false;
let detailRequest = 0;
let detailedProjectId = null;
let detailedProjectModifiedAt = null;
$("#viewport-fit").addEventListener("click", () => {
  modelViewer.fit();
  setCameraPresetState("isometric");
});
const toast = $("#toast");
const copyProjectIdButton = $("#copy-project-id");
copyProjectIdButton.addEventListener("click", async () => {
  if (!selectedProjectId || selectedProjectId.startsWith(FOLDER_SELECTION_PREFIX)) return;
  try {
    await navigator.clipboard.writeText(selectedProjectId);
    notify(`UUID проекта скопирован: ${selectedProjectId}`, false, 10000);
  } catch {
    notify("Не удалось скопировать UUID проекта.", true);
  }
});
const numberFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
const integerFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const projectDateFormat = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });
let projects = [];
let projectRouteSlugSource = null;
let projectRouteSlugCache = new Map();
let projectSort = "modified-desc";
let projectsRenderSignature = null;
let projectGroups = [];
let folders = [];
let selectedProjectId = readSelectedProject();
window.cadyxSelectedProjectId = () => selectedProjectId;
let selectedObjectId = null;
let currentObjects = [];
let activeSketchId = null;
let pendingViewportTransition = null;
let toastTimer;
let refreshTimer;
let workspaceRefreshInFlight = false;
let workspaceRefreshPending = false;
let workspaceRefreshRetryDelayMs = 250;
let activeFolderName = null;
let folderFiles = [];
let folderFilesSignature = null;
let fileListContextKey = null;
let selectedFolderFileName = null;
let selectedProjectScanRunId = null;
let selectedFolderFileGeometry = null;
let projectModelFileImportInFlight = false;
let folderListRequest = 0;
let previewRequest = 0;

function queueViewportSnapshot() {
  window.dispatchEvent(new Event("cadyx:viewport-rendered"));
  if (!selectedProjectId || selectedProjectId.startsWith(FOLDER_SELECTION_PREFIX)
      || $("#viewport")?.dataset.scanPreviewMode
      || renderedSceneProjectId !== selectedProjectId
      || !Number.isInteger(renderedSceneRevision)) return;
  viewportSnapshotDirty = true;
  if (viewportSnapshotTimer || viewportSnapshotInFlight) return;
  const delay = Math.max(0, 400 - (performance.now() - viewportSnapshotLastSentAt));
  viewportSnapshotTimer = window.setTimeout(() => {
    viewportSnapshotTimer = 0;
    void publishViewportSnapshot();
  }, delay);
}

async function publishViewportSnapshot() {
  if (viewportSnapshotInFlight || !viewportSnapshotDirty) return;
  if ($("#viewport")?.dataset.scanPreviewMode) { viewportSnapshotDirty = false; return; }
  const projectId = selectedProjectId;
  const revision = renderedSceneRevision;
  viewportSnapshotDirty = false;
  viewportSnapshotInFlight = true;
  try {
    const image = await modelViewer.createSnapshotBlob();
    if (projectId !== selectedProjectId || projectId !== renderedSceneProjectId
        || revision !== renderedSceneRevision || $("#viewport")?.dataset.scanPreviewMode) {
      viewportSnapshotDirty = true;
      return;
    }
    const response = await fetch(
      `/api/v1/agent/projects/${encodeURIComponent(projectId)}/viewport/snapshot`,
      {
        method: "POST",
        headers: {
          "Content-Type": "image/png",
          "X-CADYX-Document-Revision": String(revision),
        },
        body: image,
      },
    );
    if (!response.ok) {
      if (response.status !== 409 && !viewportSnapshotErrorShown) {
        notify("Не удалось передать точный снимок viewport в API.", true);
        viewportSnapshotErrorShown = true;
      }
      return;
    }
    await response.json();
    viewportSnapshotLastSentAt = performance.now();
    viewportSnapshotErrorShown = false;
  } catch {
    if (!viewportSnapshotErrorShown) {
      notify("Не удалось синхронизировать изображение viewport с API.", true);
      viewportSnapshotErrorShown = true;
    }
  } finally {
    viewportSnapshotInFlight = false;
    if (viewportSnapshotDirty) queueViewportSnapshot();
  }
}

let automaticUpdateSnapshot = null;
let automaticUpdateInFlight = false;
let automaticUpdatesUnavailable = false;
let workspaceAvailabilityConnected = false;
let filesAvailabilityConnected = false;
let validationRecordsRequest = 0;
const activitiesByProject = new Map();
const activityRevisionsByProject = new Map();
const agentLocksByProject = new Map();
const dashboardsByProject = new Map();
const dashboardRevisionsByProject = new Map();
const FOLDER_SELECTION_PREFIX = "folder:";
const initialAddress = new URLSearchParams(window.location.search);
const initialProjectPath = /^\/p\/[^/]+\/?$/.test(window.location.pathname);
let projectAddressPinned = (initialAddress.has("project_id") || initialAddress.has("folder") || initialProjectPath)
  && initialAddress.get("view") !== "projects";
const projectsChooserPinned = !projectAddressPinned;
let projectCreationPending = false;
let workspaceSelectionRestoreAttempted = !projectAddressPinned || initialAddress.get("view") === "projects";
const primaryGrid = $("#primary-grid");
const folderDetail = $("#folder-detail");
const folderInspector = $("#folder-inspector");
const projectsPanel = $("#projects-panel");
function canOpenFilesView(projectId = selectedProjectId) {
  if (!projectId || getSelectedFolder()) return false;
  const lock = agentLocksByProject.get(projectId);
  const activityKnown = activityRevisionsByProject.has(projectId);
  const agentIsActive = (activitiesByProject.get(projectId) || []).some((item) => item.state === "running");
  return filesAvailabilityConnected && activityKnown && lock?.state === "FREE"
    && !lock.recovery_required && !agentIsActive;
}

function lockAvailabilityKey(lock) {
  return JSON.stringify([lock?.state || null, Boolean(lock?.recovery_required)]);
}

function workspaceControlLockReason() {
  if (!projectAddressPinned) return null;
  if (!workspaceAvailabilityConnected) return "СОСТОЯНИЕ ПАНЕЛИ НЕ ПОДТВЕРЖДЕНО · УПРАВЛЕНИЕ ЗАБЛОКИРОВАНО";
  if (!selectedProjectId || getSelectedFolder()) return null;
  const lock = agentLocksByProject.get(selectedProjectId);
  if (lock?.recovery_required) return "ТРЕБУЕТСЯ ВОССТАНОВЛЕНИЕ · УПРАВЛЕНИЕ ЗАБЛОКИРОВАНО";
  const agentIsActive = (activitiesByProject.get(selectedProjectId) || []).some((item) => item.state === "running");
  if (lock?.state === "AI_LOCKED" || agentIsActive) return "АГЕНТ РАБОТАЕТ · УПРАВЛЕНИЕ ЗАБЛОКИРОВАНО";
  if (!activityRevisionsByProject.has(selectedProjectId) || !lock) {
    return "СОСТОЯНИЕ ПАНЕЛИ НЕ ПОДТВЕРЖДЕНО · УПРАВЛЕНИЕ ЗАБЛОКИРОВАНО";
  }
  if (lock.state !== "FREE") return "ПАНЕЛЬ ЗАНЯТА · УПРАВЛЕНИЕ ЗАБЛОКИРОВАНО";
  return null;
}

function updateWorkspaceControls() {
  const lockReason = workspaceControlLockReason();
  const available = lockReason === null;
  projectDetail.dataset.controlsLocked = String(!available);
  const status = $("#workspace-lock-status");
  status.hidden = available;
  status.textContent = lockReason || "";

  const brand = $("#brand-link");
  if (available) {
    brand.href = "/";
    brand.removeAttribute("aria-disabled");
    brand.removeAttribute("tabindex");
  } else {
    brand.removeAttribute("href");
    brand.setAttribute("aria-disabled", "true");
    brand.tabIndex = -1;
  }

  document.querySelectorAll("#project-detail button").forEach((button) => {
    const filesUnavailable = button.dataset.workspaceView === "files" && !canOpenFilesView();
    const projectsNavigation = button.dataset.workspaceView === "projects";
    const projectNavigation = button.dataset.projectNavigation === "true";
    button.disabled = (!projectsNavigation && !projectNavigation && !available) || filesUnavailable;
    if (button.dataset.workspaceView) {
      button.title = projectsNavigation ? ""
        : lockReason || (filesUnavailable
        ? "Доступно, когда агент освободит панель и завершит текущую работу" : "");
    }
  });
  if (!available && primaryGrid.dataset.view === "files") {
    setWorkspaceNavigation("overview");
  }
}

$("#workspace-navigation").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-workspace-view]");
  if (!button || button.disabled) return;
  const view = button.dataset.workspaceView;
  setWorkspaceNavigation(view);
  if (view === "overview") {
    $("#viewport-fit").focus({ preventScroll: true });
  } else if (view === "geometry") {
    $("#geometry-rows").focus({ preventScroll: true });
  } else if (view === "analysis") {
    $("#comparison-panel").focus({ preventScroll: true });
    refreshValidationRecords().catch(showValidationRecordsError);
    if (selectedProjectId && !getSelectedFolder()) refreshDetail().catch(handleRefreshError);
  } else if (view === "manufacturing") {
    $("#manufacturing-panel").focus({ preventScroll: true });
  } else if (view === "activity") {
    $("#workflow-panel").focus({ preventScroll: true });
  } else if (view === "files") {
    const projectId = selectedProjectId;
    selectedProjectScanRunId = null;
    $("#folder-files-title").textContent = "Файлы проекта";
    folderInspector.hidden = true;
    loadProjectFiles(projectId).catch((error) => showProjectFilesLoadError(projectId, error));
    folderFileList.focus({ preventScroll: true });
  } else if (view === "projects") {
    $("#projects-list").focus({ preventScroll: true });
  }
});

function setWorkspaceNavigation(view) {
  const views = new Set(["overview", "geometry", "analysis", "manufacturing", "activity", "files", "projects"]);
  const activeView = views.has(view)
    && (view === "overview" || view === "projects" || workspaceControlLockReason() === null)
    && (view !== "files" || canOpenFilesView()) ? view : "overview";
  const address = new URL(window.location.href);
  if (activeView === "projects") address.searchParams.set("view", "projects");
  else address.searchParams.delete("view");
  window.history.replaceState(null, "", `${address.pathname}${address.search}${address.hash}`);
  primaryGrid.dataset.view = activeView;
  folderDetail.hidden = !getSelectedFolder() && activeView !== "files";
  $("#comparison-panel").hidden = activeView !== "analysis";
  $("#manufacturing-panel").hidden = activeView !== "manufacturing";
  projectsPanel.hidden = activeView !== "projects";
  $("#object-panel").hidden = activeView !== "geometry" || !selectedProjectId || Boolean(getSelectedFolder());
  document.querySelectorAll(".nav-item[data-workspace-view]").forEach((item) => {
    if (item.dataset.workspaceView === activeView) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
}

setWorkspaceNavigation(projectsChooserPinned ? "projects" : "overview");

$("#open-projects").addEventListener("click", () => {
  projectDetail.hidden = false;
  noSelection.hidden = true;
  renderProjects();
  setWorkspaceNavigation("projects");
});

window.addEventListener("popstate", () => {
  const requestedView = new URLSearchParams(window.location.search).get("view");
  if (requestedView === "projects") {
    projectDetail.hidden = false;
    noSelection.hidden = true;
    renderProjects();
    setWorkspaceNavigation("projects");
  } else {
    setWorkspaceNavigation("overview");
  }
});

function readSelectedProject() {
  const address = new URLSearchParams(window.location.search);
  const projectId = address.get("project_id");
  const folderName = address.get("folder");
  if (projectId || folderName) return projectId || `folder:${folderName}`;
  const projectPathMatch = /^\/p\/([^/]+)\/?$/.exec(window.location.pathname);
  if (!projectPathMatch) return null;
  try {
    const requestedSlug = decodeURIComponent(projectPathMatch[1]);
    for (const [id, slug] of projectRouteSlugs()) {
      if (slug === requestedSlug) return id;
    }
  } catch { /* Malformed path escapes do not resolve to a project. */ }
  return null;
}

function notify(message, isError = false, durationMs = 3000) {
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), durationMs);
}

function formatNumber(value) {
  return Number.isFinite(Number(value)) ? numberFormat.format(Number(value)) : "—";
}

const validationKindLabels = {
  body_dimensions: "Габариты тела",
  body_clearance: "Зазор между телами",
  body_preservation: "Сохранность объёма",
  body_thickness: "Толщина стенки",
};
const validationStatusLabels = { pass: "ПРОЙДЕНО", fail: "ОБНАРУЖЕНО", untested: "НЕ ДОКАЗАНО" };

function validationRecordSummary(record) {
  const result = record?.result || {};
  const unit = "мод. ед.";
  if (record?.kind === "body_dimensions") {
    return Object.entries(result.checks || {}).map(([axis, check]) =>
      `${axis.toUpperCase()} ${formatNumber(check.actual)} / ${formatNumber(check.expected)} ${unit}`
    ).join(" · ") || "Размеры не указаны";
  }
  if (record?.kind === "body_clearance") {
    return `Зазор ${formatNumber(result.measured_distance)} ${unit} · требуется ${formatNumber(result.minimum_clearance)} ${unit} · пересечение ${formatNumber(result.intersection_volume)} ${unit}³`;
  }
  if (record?.kind === "body_preservation") {
    return `Потеряно ${formatNumber(result.lost_volume)} ${unit}³ · допустимо ${formatNumber(result.maximum_lost_volume)} ${unit}³`;
  }
  if (record?.kind === "body_thickness") {
    return `Минимум ${formatNumber(result.minimum_sampled_thickness)} ${unit} · P10 ${formatNumber(result.p10_sampled_thickness)} ${unit} · граней ${formatNumber(result.sampled_face_count)}/${formatNumber(result.face_count)} · нарушений ${formatNumber(result.violating_sample_count)} из ${formatNumber(result.hit_count)}`;
  }
  return "Сводка результата отсутствует";
}

function renderValidationRecords(records) {
  const list = $("#validation-records-list");
  const empty = $("#validation-records-empty");
  const currentRevisionText = $("#document-revision").textContent.replace(/\s/g, "");
  const currentRevision = /^\d+$/.test(currentRevisionText) ? Number(currentRevisionText) : null;
  const safeRecords = Array.isArray(records) ? records : [];
  list.replaceChildren(empty);
  $("#validation-records-count").textContent = integerFormat.format(safeRecords.length);
  empty.hidden = safeRecords.length > 0;
  for (const record of safeRecords) {
    const result = record?.result || {};
    const revision = Number.isInteger(result.revision) ? result.revision : record.revision;
    const stale = currentRevision !== null && Number.isInteger(revision) && revision !== currentRevision;
    const status = ["pass", "fail", "untested"].includes(result.status) ? result.status : "untested";
    const article = document.createElement("article");
    article.className = "validation-record";
    article.dataset.status = status;
    article.dataset.stale = String(stale);
    article.setAttribute("role", "listitem");

    const title = document.createElement("div");
    title.className = "validation-record-title";
    const name = document.createElement("strong");
    name.textContent = validationKindLabels[record?.kind] || "Инженерная проверка";
    const details = document.createElement("span");
    details.textContent = `РЕВИЗИЯ ${Number.isInteger(revision) ? integerFormat.format(revision) : "—"}`;
    title.append(name, details);

    const summary = document.createElement("div");
    summary.className = "validation-record-summary";
    summary.textContent = validationRecordSummary(record);

    const time = document.createElement("time");
    time.className = "validation-record-time";
    const createdAt = new Date(record?.created_at || "");
    if (Number.isFinite(createdAt.getTime())) {
      time.dateTime = createdAt.toISOString();
      time.textContent = formatActivityTimestamp(record.created_at);
      time.title = createdAt.toLocaleString("ru-RU");
    } else {
      time.textContent = "Время неизвестно";
    }

    const badge = document.createElement("span");
    badge.className = "validation-record-status";
    badge.textContent = validationStatusLabels[status];
    title.append(badge);
    article.append(title, summary, time);
    list.append(article);
  }
}

async function refreshValidationRecords(projectId = selectedProjectId) {
  if (!projectId || projectId.startsWith(FOLDER_SELECTION_PREFIX)) return;
  const request = ++validationRecordsRequest;
  const response = await api(`/api/v1/agent/projects/${encodeURIComponent(projectId)}/validations?limit=50`, { cache: "no-store" });
  if (request !== validationRecordsRequest || projectId !== selectedProjectId) return;
  renderValidationRecords(response.records);
  $("#validation-records-status").textContent = "";
}

function showValidationRecordsError() {
  $("#validation-records-status").textContent = "Не удалось обновить";
}

function setApiConnectionStatus(online) {
  const label = online ? "Сервер CADYX на связи" : "Нет связи с сервером CADYX";
  const indicator = $("#api-light");
  indicator.classList.toggle("active", online);
  indicator.setAttribute("aria-label", label);
  indicator.title = label;
  $("#api-status-label").textContent = online ? "СЕРВЕР НА СВЯЗИ" : "НЕТ СВЯЗИ С СЕРВЕРОМ";
  if (online) {
    $("#api-sync-time").textContent = new Intl.DateTimeFormat("ru-RU", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).format(new Date());
  }
}

function formatElapsed(dashboard, confirmedActive = false) {
  let seconds = dashboard?.elapsed_seconds;
  if (!Number.isInteger(seconds) || seconds < 0) {
    if (!confirmedActive) return "ПРОШЛО —";
    const startedAt = Date.parse(dashboard?.started_at || "");
    if (!Number.isFinite(startedAt)) return "ПРОШЛО —";
    seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  }
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = seconds % 60;
  return `ПРОШЛО ${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
}

function updateWorkflowElapsed() {
  const output = $("#workflow-eta");
  if (output) output.textContent = formatElapsed(
    dashboardsByProject.get(selectedProjectId), hasConfirmedAgentWork(selectedProjectId),
  );
}

function hasConfirmedAgentWork(projectId) {
  const lock = agentLocksByProject.get(projectId);
  const runningActivity = (activitiesByProject.get(projectId) || []).some((item) => item.state === "running");
  return runningActivity || (lock?.state === "AI_LOCKED" && lock.lease?.owner_type === "agent"
    && !lock.recovery_required);
}

const phaseStateLabel = (state) => ({
  completed: "ЗАВЕРШЕНО", in_progress: "В РАБОТЕ", pending: "ОЖИДАЕТ", failed: "ОШИБКА",
})[state] || "НЕИЗВЕСТНО";
const taskStateLabel = (state) => ({
  running: "В РАБОТЕ", pending: "ОЖИДАЕТ", completed: "ЗАВЕРШЕНО", failed: "ОШИБКА",
})[state] || "НЕИЗВЕСТНО";
function formatActivityTimestamp(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).format(date).replace(", ", " ");
}

function renderAgentDashboard(dashboard) {
  const workflow = dashboard?.workflow;
  const workflowPanel = $("#workflow-panel");
  workflowPanel.dataset.available = String(Boolean(workflow));
  if (workflow) {
    const progress = Math.max(0, Math.min(100, Number(workflow.progress_percent) || 0));
    $("#workflow-name").textContent = workflow.title;
    $("#workflow-percent").textContent = `${Math.round(progress)}%`;
    $("#workflow-progress").dataset.available = "true";
    $("#workflow-progress").setAttribute("aria-valuenow", String(progress));
    $("#workflow-progress-bar").style.width = `${progress}%`;
  } else {
    $("#workflow-name").textContent = "РАБОТА НЕ НАЧАТА";
    $("#workflow-percent").textContent = "—";
    $("#workflow-progress").dataset.available = "false";
    $("#workflow-progress").setAttribute("aria-valuenow", "0");
    $("#workflow-progress-bar").style.width = "0%";
  }
  $("#workflow-eta").textContent = formatElapsed(dashboard, hasConfirmedAgentWork(selectedProjectId));

  renderCurrentProgress(
    dashboard,
    activitiesByProject.get(selectedProjectId) || [],
    agentLocksByProject.get(selectedProjectId),
  );
  renderWorkflowHistory(activitiesByProject.get(selectedProjectId) || []);
}

function renderCurrentProgress(dashboard, value, lock = null) {
  const activities = Array.isArray(value) ? value : value ? [value] : [];
  const runningActivity = activities.slice().reverse().find((item) => item.state === "running") || null;
  const activity = runningActivity || activities[activities.length - 1] || null;
  const agentLeaseActive = lock?.state === "AI_LOCKED" && lock.lease?.owner_type === "agent"
    && !lock.recovery_required;
  const tasks = dashboard?.tasks || [];
  const runningTask = tasks.find((task) => task.state === "running") || null;
  const workflow = dashboard?.workflow || null;
  const phases = workflow?.phases || [];
  const activePhase = phases.find((phase) => phase.state === "in_progress") || null;
  const completedWorkflowWithoutJournal = Boolean(
    !activity && workflow && Number(workflow.progress_percent) === 100
      && phases.length > 0 && phases.every((phase) => phase.state === "completed")
      && tasks.every((task) => task.state === "completed"),
  );
  const progressPercent = Number(runningActivity?.progress_percent);
  const progressPhaseLabels = {
    measure_distance: "Измеряет минимальный зазор",
    classify_interference: "Классифицирует пересечение",
    triangulate_surface: "Строит сетку поверхности",
    prepare_samples: "Подготавливает выборку толщины",
    sample_surface_triangles: "Измеряет толщину стенки",
  };
  const progressTask = Number.isFinite(progressPercent) && progressPhaseLabels[runningActivity?.phase]
    ? { title: progressPhaseLabels[runningActivity.phase], state: "in_progress" }
    : null;
  const isWorking = Boolean(runningActivity || agentLeaseActive);
  const state = isWorking ? "running"
    : activity?.state === "failed" ? "failed"
      : activity?.state === "completed" || completedWorkflowWithoutJournal ? "completed" : "idle";
  const action = runningActivity?.action || (agentLeaseActive ? "Выполняет команду" : activity?.action)
    || (completedWorkflowWithoutJournal ? workflow.title : "Действий пока нет");
  const actionText = !runningActivity && !agentLeaseActive && activity?.state === "completed" && action
    ? `${action} · завершено`
    : action;
  const shownTask = isWorking ? runningTask || activePhase || progressTask : null;

  $("#workflow-current").dataset.state = state;
  const agentState = $("#agent-state");
  agentState.dataset.state = state;
  agentState.textContent = state === "running" ? "В РАБОТЕ"
    : state === "failed" ? "ОШИБКА" : state === "completed" ? "ЗАВЕРШЕНО" : "ОЖИДАЕТ";
  $("#workflow-action-label").textContent = runningActivity || agentLeaseActive ? "ДЕЙСТВИЕ СЕЙЧАС"
    : activity?.state === "failed" ? "ПОСЛЕДНЕЕ ДЕЙСТВИЕ · ОШИБКА"
      : completedWorkflowWithoutJournal ? "ЗАВЕРШЁННЫЙ ОТЧЁТ" : "ПОСЛЕДНЕЕ ДЕЙСТВИЕ";
  const actionElement = $("#activity-title");
  actionElement.textContent = actionText;
  actionElement.title = actionText;

  const capabilityDetail = $("#workflow-capability-detail");
  const capability = activity?.detail;
  if (capability && typeof capability === "object" && capability.capability_id) {
    const summary = [];
    if (capability.title) summary.push(`Функция: ${capability.title} · ${capability.capability_id}`);
    if (capability.reason) summary.push(`Зачем: ${capability.reason}`);
    if (capability.test_result) {
      const tests = capability.test_result;
      summary.push(tests.status === "passed"
        ? `Проверка геометрии: успешно · ${tests.steps_passed} команд · ${tests.bodies_validated} тел`
        : `Проверка геометрии: ${tests.status || "не пройдена"}`);
    }
    capabilityDetail.textContent = summary.join("\n");
    capabilityDetail.dataset.state = capability.test_result?.status === "passed" ? "completed"
      : capability.phase === "capability_missing" ? "failed" : "running";
    capabilityDetail.hidden = summary.length === 0;
  } else {
    capabilityDetail.textContent = "";
    capabilityDetail.hidden = true;
    delete capabilityDetail.dataset.state;
  }

  const taskRow = $("#workflow-task-row");
  const taskElement = $("#workflow-current-task");
  const taskState = $("#workflow-current-task-state");
  if (shownTask) {
    taskElement.textContent = shownTask.title;
    taskElement.title = shownTask.title;
    const isTask = tasks.includes(shownTask);
    const isProgress = shownTask === progressTask;
    const shownState = shownTask.state;
    taskState.textContent = isProgress ? `${Math.round(progressPercent)}%`
      : isTask ? taskStateLabel(shownState) : phaseStateLabel(shownState);
    taskState.dataset.state = shownState;
    $("#workflow-task-label").textContent = isTask
      ? shownState === "running" ? "ТЕКУЩАЯ ЗАДАЧА" : "ПОСЛЕДНЯЯ ЗАДАЧА"
      : shownState === "in_progress" ? "ТЕКУЩИЙ ЭТАП" : "ПОСЛЕДНИЙ ЭТАП";
    const normalizedAction = action.replace(/\s+/g, " ").trim().toLocaleLowerCase("ru");
    const normalizedTask = shownTask.title.replace(/\s+/g, " ").trim().toLocaleLowerCase("ru");
    taskRow.hidden = normalizedTask === normalizedAction;
  } else {
    taskElement.textContent = isWorking ? "Текущая задача не указана" : "Нет выполняемой задачи";
    taskElement.title = "";
    taskState.textContent = "";
    taskState.dataset.state = "idle";
    $("#workflow-task-label").textContent = "ТЕКУЩАЯ ЗАДАЧА";
    taskRow.hidden = true;
  }
}

function renderWorkflowHistory(value) {
  const activities = Array.isArray(value) ? value : value ? [value] : [];
  const completedActivities = activities
    .filter((activity) => (activity.state === "completed" || activity.state === "failed") && activity.action)
    .slice()
    .reverse();
  const history = completedActivities.map((activity) => ({
    state: activity.state, title: activity.action,
    completedAt: activity.completed_at || activity.updated_at,
  }));
  const list = $("#workflow-history-list");
  list.replaceChildren();
  list.scrollTop = 0;
  $("#workflow-history-count").textContent = String(history.length);
  $("#workflow-history-empty").hidden = history.length > 0;
  for (const item of history) {
    const row = document.createElement("li");
    row.dataset.state = item.state;
    const state = document.createElement("span");
    state.className = "workflow-history-state";
    state.textContent = taskStateLabel(item.state);
    const title = document.createElement("span");
    title.className = "workflow-history-title";
    title.textContent = item.title;
    title.title = item.title;
    const time = document.createElement("time");
    time.className = "workflow-history-time";
    if (item.completedAt) {
      const completedAt = new Date(item.completedAt);
      if (Number.isFinite(completedAt.getTime())) {
        time.dateTime = completedAt.toISOString();
        time.textContent = formatActivityTimestamp(item.completedAt);
        time.title = completedAt.toLocaleString("ru-RU", {
          year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
          hourCycle: "h23",
        });
      }
    }
    row.append(state, title, time);
    list.append(row);
  }
}

function renderSceneMetrics(scene, objectsById, visibleSketchId) {
  const bodyMeshes = scene.objects.filter((item) => objectsById.get(item.object_id)?.type === "body");
  const visibleObjects = scene.objects.filter((item) => {
    const source = objectsById.get(item.object_id);
    return source?.type === "body" || source?.sketch_id === visibleSketchId;
  });
  const vertexCount = bodyMeshes.reduce((total, item) => total + item.positions.length / 3, 0);
  const faceCount = bodyMeshes.reduce((total, item) => total + item.triangles.length / 3, 0);
  const vertexCountText = integerFormat.format(vertexCount);
  const faceCountText = integerFormat.format(faceCount);
  $("#viewport-model-name").textContent = getProject(selectedProjectId)?.project.name || "—";
  $("#viewport-object-count").textContent = integerFormat.format(visibleObjects.length);
  $("#viewport-vertex-count").textContent = vertexCountText;
  $("#viewport-face-count").textContent = faceCountText;
  $("#document-revision").textContent = integerFormat.format(scene.revision);
  $("#viewport-revision").textContent = integerFormat.format(scene.revision);
}

function getProject(id) {
  return projects.find((item) => item.project.id === id);
}

function projectSlugBase(name) {
  return String(name || "").normalize("NFKC").toLowerCase()
    .replace(/[^\p{L}\p{N}_-]+/gu, "-")
    .replace(/-+/g, "-").replace(/^[-_]+|[-_]+$/g, "") || "project";
}

function projectRouteSlugs() {
  if (projectRouteSlugSource === projects) return projectRouteSlugCache;
  const entries = projects.map(({ project }) => ({ project, base: project.url_slug || projectSlugBase(project.name) }));
  const baseCounts = new Map();
  entries.forEach(({ base }) => baseCounts.set(base, (baseCounts.get(base) || 0) + 1));
  const used = new Set();
  projectRouteSlugCache = new Map(entries.map(({ project, base }) => {
    const shortId = project.id.replace(/[^a-z0-9]/gi, "").slice(0, 8).toLowerCase();
    let slug = baseCounts.get(base) > 1 ? `${base}-${shortId}` : base;
    if (used.has(slug) || (slug !== base && baseCounts.has(slug))) slug = `${base}-${project.id.toLowerCase()}`;
    while (used.has(slug) || (slug !== base && baseCounts.has(slug))) slug = `${slug}-${shortId}`;
    used.add(slug);
    return [project.id, slug];
  }));
  projectRouteSlugSource = projects;
  return projectRouteSlugCache;
}

function projectPath(projectId) {
  const slug = projectRouteSlugs().get(projectId);
  return slug ? `/p/${encodeURIComponent(slug)}` : null;
}

function canSelectProject(id) {
  const lock = agentLocksByProject.get(id) || getProject(id)?.lock;
  const agentIsActive = (activitiesByProject.get(id) || []).some((item) => item.state === "running");
  return lock?.state === "FREE" && !lock.recovery_required && !agentIsActive;
}

async function navigateToWorkspaceProject(projectId, projectName) {
  const address = new URL(window.location.href);
  address.pathname = projectPath(projectId) || "/";
  address.searchParams.delete("project_id");
  address.searchParams.delete("folder");
  address.searchParams.delete("view");
  window.history.replaceState(null, "", `${address.pathname}${address.search}${address.hash}`);
  projectAddressPinned = true;
  try {
    await refreshWorkspace();
    notify(`Открыт проект «${projectName}».`);
  } catch (error) {
    notify(`Не удалось открыть проект: ${error.message}`, true);
  }
}

$("#create-project").addEventListener("click", () => {
  $("#new-project-name").value = "";
  $("#create-project-error").hidden = true;
  $("#create-project-dialog").showModal();
  $("#new-project-name").focus();
});
$("#cancel-create-project").addEventListener("click", () => $("#create-project-dialog").close());
$("#create-project-dialog").addEventListener("cancel", (event) => {
  if (projectCreationPending) event.preventDefault();
});
$("#create-project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("#new-project-name").value.trim();
  if (!name || projectCreationPending) return;
  projectCreationPending = true;
  const controls = [$("#new-project-name"), $("#cancel-create-project"), $("#submit-create-project")];
  controls.forEach((control) => { control.disabled = true; });
  $("#create-project-error").hidden = true;
  try {
    const result = await api("/api/v1/projects", { method: "POST", body: JSON.stringify({ name }) });
    window.location.assign(`/?project_id=${encodeURIComponent(result.project.id)}`);
  } catch (error) {
    $("#create-project-error").textContent = `Не удалось создать проект: ${error.message}`;
    $("#create-project-error").hidden = false;
  } finally {
    projectCreationPending = false;
    controls.forEach((control) => { control.disabled = false; });
  }
});

$("#create-project-group").addEventListener("click", () => {
  const dialog = $("#project-group-dialog");
  $("#project-group-name").value = "";
  dialog.showModal();
  $("#project-group-name").focus();
});
$("#cancel-project-group").addEventListener("click", () => $("#project-group-dialog").close());
$("#project-group-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("#project-group-name").value.trim();
  if (!name) return;
  try {
    await api("/api/v1/project-groups", { method: "POST", body: JSON.stringify({ name }) });
    $("#project-group-dialog").close();
    await refreshWorkspace();
  } catch (error) {
    notify(`Не удалось создать папку: ${error.message}`, true);
  }
});

async function setProjectGroup(projectId, groupId) {
  try {
    await api(`/api/v1/projects/${encodeURIComponent(projectId)}/group`, {
      method: "POST", body: JSON.stringify({ group_id: groupId || null }),
    });
  } catch (error) {
    notify(`Не удалось переместить проект: ${error.message}`, true);
  }
  await refreshWorkspace();
}

async function deleteProject(projectId, projectName) {
  const confirmed = window.confirm(
    `Безвозвратно удалить проект «${projectName}»?\n\n` +
    "Будут удалены модель, история, файлы и сканы проекта. Это действие нельзя отменить.",
  );
  if (!confirmed) return;
  try {
    const result = await api(`/api/v1/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
    if (result.cleanup_pending) notify("Проект удалён из списка; очистка файлов завершится после закрытия открытых ресурсов.");
    if (projectAddressPinned && selectedProjectId === projectId) {
      window.location.assign("/");
      return;
    }
    await refreshWorkspace();
  } catch (error) {
    notify(`Не удалось удалить проект: ${error.message}`, true);
  }
}

function compareProjects(left, right) {
  const nameOrder = left.project.name.localeCompare(right.project.name, "ru", { sensitivity: "base" })
    || left.project.id.localeCompare(right.project.id);
  if (projectSort === "name") return nameOrder;
  const leftModified = Date.parse(left.project.modified_at || "") || 0;
  const rightModified = Date.parse(right.project.modified_at || "") || 0;
  const direction = projectSort === "modified-asc" ? 1 : -1;
  return direction * (leftModified - rightModified) || nameOrder;
}

function renderProjectCard(entry) {
  const project = entry.project;
  const active = project.id === selectedProjectId;
  const projectAvailable = canSelectProject(project.id);
  const card = document.createElement("article");
  card.className = "workspace-project-card";
  card.dataset.projectId = project.id;
  card.dataset.active = String(active);
  card.setAttribute("role", "listitem");

  const state = document.createElement("span");
  state.className = "workspace-project-state";
  state.textContent = active ? "Текущий проект" : projectAvailable ? "Готов к просмотру" : "Занят · просмотр доступен";

  const thumbnail = document.createElement("div");
  thumbnail.className = "workspace-project-thumbnail";
  const image = document.createElement("img");
  image.src = `/api/v1/projects/${encodeURIComponent(project.id)}/viewport.png?thumbnail=1`;
  image.alt = `Миниатюра проекта «${project.name}»`;
  image.loading = "lazy";
  image.decoding = "async";
  image.addEventListener("error", () => {
    image.remove();
    thumbnail.classList.add("unavailable");
  }, { once: true });
  thumbnail.append(image, state);

  const heading = document.createElement("div");
  heading.className = "workspace-project-card-heading";
  const name = document.createElement("h3");
  name.textContent = project.name;
  name.title = project.name;
  heading.append(name);

  const more = document.createElement("details");
  more.className = "workspace-project-menu";
  const moreButton = document.createElement("summary");
  moreButton.textContent = "⋮";
  moreButton.setAttribute("aria-label", `Действия с проектом «${project.name}»`);
  moreButton.title = "Действия с проектом";
  const morePopover = document.createElement("div");
  morePopover.className = "workspace-project-menu-popover";
  const remove = document.createElement("button");
  remove.className = "workspace-project-delete";
  remove.type = "button";
  remove.textContent = "Удалить проект";
  remove.disabled = !projectAvailable;
  remove.setAttribute("aria-label", `Удалить проект «${project.name}»`);
  remove.addEventListener("click", () => deleteProject(project.id, project.name));
  morePopover.append(remove);
  more.append(moreButton, morePopover);
  heading.append(more);

  const meta = document.createElement("div");
  meta.className = "workspace-project-meta";
  const modified = document.createElement("span");
  modified.className = "workspace-project-modified";
  modified.setAttribute("aria-label", "Дата изменения");
  modified.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3v3m10-3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13H4V6a1 1 0 0 1 1-1Z"/></svg>';
  const modifiedAt = Date.parse(project.modified_at || "");
  const modifiedValue = document.createElement("time");
  if (Number.isFinite(modifiedAt)) modifiedValue.dateTime = new Date(modifiedAt).toISOString();
  modifiedValue.textContent = Number.isFinite(modifiedAt) ? projectDateFormat.format(new Date(modifiedAt)) : "—";
  modified.append(modifiedValue);
  const projectId = document.createElement("span");
  projectId.className = "workspace-project-id";
  projectId.append(document.createTextNode("ID "));
  const projectIdValue = document.createElement("strong");
  projectIdValue.textContent = project.id.slice(0, 8).toUpperCase();
  projectId.append(projectIdValue);
  meta.append(modified, projectId);

  const actions = document.createElement("div");
  actions.className = "workspace-project-actions";
  const select = document.createElement("button");
  select.className = "button workspace-project-open";
  select.type = "button";
  select.dataset.projectNavigation = "true";
  select.textContent = active ? "Текущий проект" : "Выбрать проект";
  select.disabled = active;
  select.setAttribute("aria-label", active
    ? `Проект «${project.name}» уже выбран`
    : `Выбрать проект «${project.name}»`);
  select.addEventListener("click", () => navigateToWorkspaceProject(project.id, project.name));

  const open = document.createElement("a");
  open.className = "button workspace-project-open workspace-project-open-new-tab";
  open.href = projectPath(project.id) || `/?project_id=${encodeURIComponent(project.id)}`;
  open.target = "_blank";
  open.rel = "noopener";
  open.setAttribute("aria-label", `Открыть проект «${project.name}» в новой вкладке`);
  open.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 7h6l2 2h9v10h-17V7Zm0 0V5h6l2 2"/></svg><span>Новая вкладка</span>';

  const folderMenu = document.createElement("details");
  folderMenu.className = "workspace-project-folder-menu";
  const folderButton = document.createElement("summary");
  folderButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
  folderButton.setAttribute("aria-label", `Переместить проект «${project.name}» в папку`);
  folderButton.title = "Папка проекта";
  const folderPopover = document.createElement("div");
  folderPopover.className = "workspace-project-folder-popover";
  const folderLabel = document.createElement("label");
  folderLabel.textContent = "Папка проекта";
  const groupSelect = document.createElement("select");
  groupSelect.className = "workspace-project-group-select";
  groupSelect.setAttribute("aria-label", `Папка проекта «${project.name}»`);
  groupSelect.disabled = !projectAvailable;
  const ungrouped = document.createElement("option");
  ungrouped.value = "";
  ungrouped.textContent = "Без папки";
  groupSelect.append(ungrouped);
  for (const group of projectGroups) {
    const option = document.createElement("option");
    option.value = group.id;
    option.textContent = group.name;
    groupSelect.append(option);
  }
  groupSelect.value = entry.group_id || "";
  groupSelect.addEventListener("change", () => setProjectGroup(project.id, groupSelect.value));
  folderPopover.append(folderLabel, groupSelect);
  folderMenu.append(folderButton, folderPopover);
  actions.append(select, open, folderMenu);
  card.append(thumbnail, heading, meta, actions);
  return card;
}

function renderProjectGroup(title, groupId, entries) {
  const section = document.createElement("section");
  section.className = "project-group";
  if (groupId) section.dataset.groupId = groupId;
  const heading = document.createElement("h3");
  heading.className = "project-group-heading";
  heading.textContent = title;
  const count = document.createElement("span");
  count.textContent = integerFormat.format(entries.length);
  heading.append(count);
  const cards = document.createElement("div");
  cards.className = "project-group-cards";
  for (const entry of entries.sort(compareProjects)) cards.append(renderProjectCard(entry));
  section.append(heading, cards);
  $("#projects-list").append(section);
}

$("#projects-sort").addEventListener("change", (event) => {
  projectSort = event.currentTarget.value;
  renderProjects();
});
$("#projects-search").addEventListener("input", renderProjects);
function setProjectsLayout(layout) {
  const gridLayout = layout === "grid";
  projectsPanel.dataset.layout = gridLayout ? "grid" : "list";
  $("#projects-grid-view").setAttribute("aria-pressed", String(gridLayout));
  $("#projects-list-view").setAttribute("aria-pressed", String(!gridLayout));
}
$("#projects-grid-view").addEventListener("click", () => setProjectsLayout("grid"));
$("#projects-list-view").addEventListener("click", () => setProjectsLayout("list"));

function renderProjects() {
  const list = $("#projects-list");
  const signature = JSON.stringify([
    projectSort,
    $("#projects-search").value.trim().toLocaleLowerCase("ru-RU"),
    projectGroups.map(({ id, name }) => [id, name]),
    projects.map((entry) => [entry.project.id, entry.project.name, entry.project.modified_at, entry.group_id,
      entry.project.id === selectedProjectId, canSelectProject(entry.project.id)]),
  ]);
  if (signature === projectsRenderSignature) {
    for (const entry of projects) {
      const timestamp = $("#projects-list").querySelector(
        `[data-project-id="${CSS.escape(entry.project.id)}"] time`,
      );
      const modifiedAt = Date.parse(entry.project.modified_at || "");
      if (timestamp) {
        timestamp.dateTime = Number.isFinite(modifiedAt) ? new Date(modifiedAt).toISOString() : "";
        timestamp.textContent = Number.isFinite(modifiedAt) ? projectDateFormat.format(new Date(modifiedAt)) : "—";
      }
    }
    return;
  }
  projectsRenderSignature = signature;
  const scrollTop = list.scrollTop;
  list.replaceChildren();
  $("#projects-count").textContent = integerFormat.format(projects.length);
  const query = $("#projects-search").value.trim().toLocaleLowerCase("ru-RU");
  const visibleProjects = projects.filter((entry) => !query
    || entry.project.name.toLocaleLowerCase("ru-RU").includes(query));
  const empty = $("#projects-empty");
  empty.hidden = visibleProjects.length > 0;
  empty.textContent = projects.length && query
    ? "По запросу ничего не найдено."
    : "В рабочей области пока нет сохранённых проектов.";
  const grouped = new Map(projectGroups.map((group) => [group.id, { group, entries: [] }]));
  const ungrouped = [];
  for (const entry of visibleProjects) {
    const section = grouped.get(entry.group_id);
    if (section) section.entries.push(entry);
    else ungrouped.push(entry);
  }
  if (ungrouped.length) renderProjectGroup("Без папки", null, ungrouped);
  const sortedGroups = [...grouped.values()].sort((left, right) =>
    left.group.name.localeCompare(right.group.name, "ru", { sensitivity: "base" }));
  for (const { group, entries } of sortedGroups) renderProjectGroup(group.name, group.id, entries);
  list.scrollTop = scrollTop;
}

function showProjectLoading(name) {
  activeFolderName = null;
  folderDetail.hidden = true;
  folderInspector.hidden = true;
  primaryGrid.classList.remove("folder-mode");
  projectDetail.hidden = false;
  noSelection.hidden = true;
  $("#object-panel").hidden = true;
  $("#geometry-rows").replaceChildren();
  $("#objects-empty").textContent = "Загружаю дерево модели…";
  $("#objects-empty").hidden = false;
  setWorkspaceNavigation("overview");
  renderAgentActivity([], null);
  renderAgentDashboard(null);
  $("#document-title").textContent = name;
  $("#document-kind").textContent = "ПРОЕКТ";
  $("#project-light").classList.add("active");
  $("#sidebar-project-name").textContent = name;
  $("#project-short-id").textContent = selectedProjectId?.slice(0, 8).toUpperCase() || "—";
  copyProjectIdButton.disabled = !selectedProjectId;
  $("#document-revision").textContent = "—";
  $("#viewport-revision").textContent = "—";
  $("#comparison-description").hidden = true;
  $("#comparison-empty").hidden = false;
  $("#comparison-preview").hidden = true;
  $("#comparison-panel").dataset.available = "false";
  validationRecordsRequest += 1;
  renderValidationRecords([]);
  $("#validation-records-status").textContent = "";
  if (!renderedSceneKey?.startsWith(`${selectedProjectId}:`)) {
    renderedSceneProjectId = null;
    renderedSceneRevision = null;
    currentModelViewportGeometry = emptyViewportGeometry;
    modelViewer.load(emptyViewportGeometry, { reset: true });
  }
}

function workspaceSelectionExists(id) {
  if (projects.some((item) => item.project.id === id)) return true;
  return id?.startsWith(FOLDER_SELECTION_PREFIX)
    && folders.includes(id.slice(FOLDER_SELECTION_PREFIX.length));
}

async function restoreCurrentWorkspaceSelection() {
  if (workspaceSelectionRestoreAttempted) return;
  workspaceSelectionRestoreAttempted = true;

  let selection;
  try {
    ({ selection } = await api("/api/v1/agent/workspace/selection"));
  } catch (error) {
    workspaceSelectionRestoreAttempted = false;
    console.warn("Unable to restore the active CADYX workspace selection.", error);
    return;
  }

  let selectedResource = null;
  if (selection?.type === "project" && typeof selection.project_id === "string") {
    selectedResource = selection.project_id;
  } else if (selection?.type === "folder" && typeof selection.folder_name === "string") {
    selectedResource = `${FOLDER_SELECTION_PREFIX}${selection.folder_name}`;
  }
  if (!selectedResource || !workspaceSelectionExists(selectedResource)) return;

  const address = new URL(window.location.href);
  if (selectedResource.startsWith(FOLDER_SELECTION_PREFIX)) {
    address.searchParams.delete("project_id");
    address.searchParams.set("folder", selectedResource.slice(FOLDER_SELECTION_PREFIX.length));
  } else {
    address.searchParams.set("project_id", selectedResource);
    address.searchParams.delete("folder");
  }
  address.searchParams.delete("view");
  window.history.replaceState(null, "", `${address.pathname}${address.search}${address.hash}`);
  projectAddressPinned = true;
}

function getSelectedFolder() {
  if (!selectedProjectId?.startsWith(FOLDER_SELECTION_PREFIX)) return null;
  const name = selectedProjectId.slice(FOLDER_SELECTION_PREFIX.length);
  return folders.includes(name) ? name : null;
}

function showFolderDetail(name) {
  selectedProjectScanRunId = null;
  const fileContextKey = `folder:${name}`;
  const folderChanged = fileListContextKey !== fileContextKey;
  activeFolderName = name;
  projectDetail.hidden = false;
  primaryGrid.classList.add("folder-mode");
  folderDetail.hidden = false;
  folderInspector.hidden = false;
  $("#object-panel").hidden = true;
  noSelection.hidden = true;
  setWorkspaceNavigation("overview");
  renderAgentActivity([], null);
  renderAgentDashboard(null);
  $("#document-title").textContent = name;
  $("#document-kind").textContent = "ПАПКА";
  $("#project-short-id").textContent = "РАБОЧАЯ ПАПКА";
  copyProjectIdButton.disabled = true;
  $("#sidebar-project-name").textContent = name;
  $("#project-light").classList.remove("active");
  $("#document-revision").textContent = "—";
  $("#folder-files-title").textContent = "Содержимое папки";
  if (folderChanged) {
    resetFileList(fileContextKey);
    loadFolderFiles(name).catch((error) => showFolderLoadError(name, error));
  }
}

function formatFileSize(size) {
  if (size < 1024) return `${size} Б`;
  if (size < 1024 * 1024) return `${numberFormat.format(size / 1024)} КБ`;
  return `${numberFormat.format(size / (1024 * 1024))} МБ`;
}

function is3dModelFile(file) {
  return /\.(ply|stl|step|stp)$/i.test(file.name);
}

function scanRunIdForFile(fileName) {
  return /^(revopoint_\d{8}T\d{6}Z)_/i.exec(fileName)?.[1] || null;
}

function scanRunLabel(runId) {
  const match = /^revopoint_(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/i.exec(runId);
  if (!match) return "Серия сканирования";
  const [, year, month, day, hour, minute, second] = match;
  const capturedAt = new Date(Date.UTC(+year, +month - 1, +day, +hour, +minute, +second));
  return `Сканирование · ${new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(capturedAt)}`;
}

function filesForProjectScanRun(files, scanRunId) {
  return files.filter((file) => scanRunIdForFile(file.name) === scanRunId);
}

function resetFileList(contextKey) {
  fileListContextKey = contextKey;
  folderFiles = [];
  folderFilesSignature = null;
  selectedFolderFileName = null;
  selectedFolderFileGeometry = null;
  folderListRequest++;
  previewRequest++;
  updateFolderInspector();
  folderFileList.replaceChildren();
  $("#folder-file-count").textContent = "0";
  folderPreviewCanvas.hidden = true;
  folderPreviewPlaceholder.hidden = false;
  folderPreviewPlaceholder.textContent = "Загружаю список файлов…";
  $("#folder-preview-title").textContent = "Предпросмотр";
  $("#folder-preview-info").textContent = "";
  updateProjectModelFileImportButton();
}

function fileContextPath(context) {
  return context.type === "folder"
    ? `/api/v1/workspace/folders/${encodeURIComponent(context.name)}/files`
    : `/api/v1/projects/${encodeURIComponent(context.id)}/files`;
}

function isCurrentFileContext(context) {
  if (context.type === "folder") {
    return activeFolderName === context.name && getSelectedFolder() === context.name;
  }
  return selectedProjectId === context.id && context.scanRunId === selectedProjectScanRunId
    && primaryGrid.dataset.view === "files" && canOpenFilesView(context.id);
}

function showFolderLoadError(name, error) {
  showFileLoadError({ type: "folder", name }, error);
}

function showProjectFilesLoadError(projectId, error) {
  showFileLoadError({ type: "project", id: projectId, scanRunId: selectedProjectScanRunId }, error);
}

function showFileLoadError(context, error) {
  if (!isCurrentFileContext(context)) return;
  folderFileList.replaceChildren();
  folderPreviewPlaceholder.hidden = false;
  folderPreviewPlaceholder.textContent = error.message;
  $("#folder-preview-title").textContent = context.type === "folder"
    ? "Не удалось открыть папку" : "Не удалось открыть файлы проекта";
  $("#folder-preview-info").textContent = "";
}

async function loadFolderFiles(name) {
  return loadFiles({ type: "folder", name });
}

async function loadProjectFiles(projectId) {
  return loadFiles({ type: "project", id: projectId, scanRunId: selectedProjectScanRunId });
}

async function loadFiles(context) {
  const contextKey = context.type === "folder" ? `folder:${context.name}`
    : `project:${context.id}${context.scanRunId ? `:scan:${context.scanRunId}` : ""}`;
  if (fileListContextKey !== contextKey) resetFileList(contextKey);
  const request = ++folderListRequest;
  const response = await api(fileContextPath(context));
  if (request !== folderListRequest || !isCurrentFileContext(context)) return;
  const modelFiles = response.files.filter(is3dModelFile);
  const previousSelection = folderFiles.find((file) => file.name === selectedFolderFileName);
  const signature = JSON.stringify(modelFiles.map((file) => [file.name, file.size, file.modified_at_ns]));
  if (signature === folderFilesSignature) return;
  folderFilesSignature = signature;
  folderFiles = modelFiles;
  $("#folder-file-count").textContent = String(folderFiles.length);
  updateFolderInspector();
  renderFolderFiles();
  const selected = folderFiles.find((file) => file.name === selectedFolderFileName);
  if (selected) {
    if (!previousSelection || previousSelection.size !== selected.size ||
        previousSelection.modified_at_ns !== selected.modified_at_ns) {
      previewResourceFile(context, selected);
    }
    return;
  }
  const visibleFiles = context.type === "project"
    ? context.scanRunId ? filesForProjectScanRun(folderFiles, context.scanRunId)
      : folderFiles.filter((file) => !scanRunIdForFile(file.name))
    : folderFiles;
  const firstPreview = visibleFiles.find((file) => file.name.toLowerCase().endsWith(".ply")) || visibleFiles[0];
  if (firstPreview) previewResourceFile(context, firstPreview);
  else {
    selectedFolderFileName = null;
    updateFolderInspector();
    const hasScanRuns = context.type === "project" && !context.scanRunId
      && folderFiles.some((file) => scanRunIdForFile(file.name));
    $("#folder-preview-title").textContent = hasScanRuns ? "Выберите серию сканирования" : "3D-файлов нет";
    $("#folder-preview-info").textContent = "";
    folderPreviewPlaceholder.hidden = false;
    folderPreviewPlaceholder.textContent = hasScanRuns ? "Откройте папку серии в списке файлов, чтобы просмотреть её кадры."
      : context.type === "folder" ? "В этой папке пока нет файлов 3D-моделей."
        : "В этом проекте пока нет файлов 3D-моделей.";
  }
}

function renderFolderFiles() {
  folderFileList.replaceChildren();
  const isProjectFileList = !getSelectedFolder() && primaryGrid.dataset.view === "files";
  if (isProjectFileList && selectedProjectScanRunId) {
    const back = document.createElement("button");
    back.type = "button";
    back.className = "folder-file-item folder-scan-back";
    back.dataset.scanRunBack = "true";
    back.innerHTML = `<span aria-hidden="true">‹</span><strong>Все файлы проекта</strong>`;
    folderFileList.append(back);
  }
  const visibleFiles = isProjectFileList
    ? selectedProjectScanRunId ? filesForProjectScanRun(folderFiles, selectedProjectScanRunId)
      : folderFiles.filter((file) => !scanRunIdForFile(file.name))
    : folderFiles;
  let folderCount = 0;
  if (isProjectFileList && !selectedProjectScanRunId) {
    const scanRuns = new Map();
    for (const file of folderFiles) {
      const runId = scanRunIdForFile(file.name);
      if (runId) scanRuns.set(runId, (scanRuns.get(runId) || 0) + 1);
    }
    const sortedRuns = [...scanRuns].sort(([left], [right]) => right.localeCompare(left));
    folderCount = sortedRuns.length;
    for (const [runId, frameCount] of sortedRuns) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "folder-file-item folder-scan-run";
      button.dataset.scanRunId = runId;
      button.setAttribute("aria-label", `${scanRunLabel(runId)}, ${frameCount} кадров`);
      const icon = document.createElement("span");
      icon.className = "folder-scan-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "▰";
      const name = document.createElement("strong");
      name.textContent = scanRunLabel(runId);
      const count = document.createElement("small");
      count.textContent = `${frameCount} кадров`;
      button.append(icon, name, count);
      folderFileList.append(button);
    }
  }
  $("#folder-file-count").textContent = String(visibleFiles.length + folderCount);
  for (const file of visibleFiles) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "folder-file-item";
    button.classList.toggle("selected", file.name === selectedFolderFileName);
    button.dataset.fileName = file.name;
    button.setAttribute("aria-pressed", file.name === selectedFolderFileName ? "true" : "false");
    const name = document.createElement("strong");
    name.textContent = file.name;
    const size = document.createElement("small");
    size.textContent = formatFileSize(file.size);
    button.append(name, size);
    folderFileList.append(button);
  }
}

function updateFolderInspector(file = folderFiles.find((item) => item.name === selectedFolderFileName) || null) {
  $("#folder-inspector-count").textContent = `3D-ФАЙЛОВ: ${folderFiles.length}`;
  $("#folder-model-file-count").textContent = String(folderFiles.length);
  $("#folder-inspector-file-name").textContent = file?.name || "Не выбран";
  $("#folder-inspector-file-size").textContent = file ? formatFileSize(file.size) : "—";
  const extension = file?.name.match(/\.([^.]+)$/)?.[1];
  $("#folder-inspector-file-type").textContent = extension ? extension.toUpperCase() : "—";
}

function updateProjectModelFileImportButton() {
  const button = folderImportModelFileButton;
  const file = folderFiles.find((item) => item.name === selectedFolderFileName) || null;
  const extension = file?.name.match(/\.([^.]+)$/)?.[1]?.toLowerCase();
  const supported = ["ply", "stl", "step", "stp"].includes(extension);
  const isProjectImport = Boolean(selectedProjectId && !getSelectedFolder()
    && primaryGrid.dataset.view === "files" && supported);
  button.hidden = !isProjectImport;
  if (!isProjectImport) return;

  const alreadyImported = currentObjects.some((item) => item.type === "feature"
    && ["import_point_cloud", "reconstruct_scan", "import_stl", "import_step"].includes(item.operation)
    && (item.source?.project_file === file.name || item.source?.file === file.name));
  const geometry = selectedFolderFileGeometry;
  const needsPlyGeometry = extension === "ply";
  const eligible = !needsPlyGeometry || (geometry && geometry.vertexCount >= 3);
  button.disabled = projectModelFileImportInFlight || alreadyImported || !eligible
    || !canOpenFilesView(selectedProjectId) || renderedSceneProjectId !== selectedProjectId
    || !Number.isInteger(renderedSceneRevision);

  if (projectModelFileImportInFlight) {
    button.textContent = "Импортирую…";
    button.title = "Добавляю 3D-файл в дерево модели и историю проекта.";
  } else if (alreadyImported) {
    button.textContent = "Уже в дереве";
    button.title = "Этот файл уже импортирован в дерево модели.";
  } else {
    button.textContent = "Добавить в дерево";
    button.title = !eligible ? "Дождитесь загрузки PLY для определения типа геометрии."
      : geometry && geometry.vertexCount < 3 ? "В PLY должно быть не менее 3 вершин."
      : !canOpenFilesView(selectedProjectId) ? "Дождитесь освобождения проекта."
      : "Исходный файл сохранится; импорт появится в дереве модели и истории.";
  }
}

async function importSelectedProjectModelFile() {
  const projectId = selectedProjectId;
  const file = folderFiles.find((item) => item.name === selectedFolderFileName) || null;
  updateProjectModelFileImportButton();
  if (!file || folderImportModelFileButton.disabled || !projectId) return;

  projectModelFileImportInFlight = true;
  updateProjectModelFileImportButton();
  let leaseId = null;
  let imported = false;
  try {
    const encoded = encodeURIComponent(projectId);
    const info = await api(`/api/v1/projects/${encoded}`);
    if (info.lock?.state !== "FREE" || info.lock?.recovery_required
        || projectId !== selectedProjectId || !canOpenFilesView(projectId)) {
      throw new Error("Проект занят; дождитесь завершения текущей операции.");
    }
    const lease = await api(`/api/v1/projects/${encoded}/leases`, {
      method: "POST",
      body: JSON.stringify({ owner_id: `files-ui:${crypto.randomUUID()}` }),
    });
    leaseId = lease.lease?.lease_id;
    if (!leaseId) throw new Error("Сервер не выдал блокировку редактирования.");

    const extension = file.name.match(/\.([^.]+)$/)?.[1]?.toLowerCase();
    const commandType = extension === "step" || extension === "stp" ? "import_step"
      : extension === "stl" ? "import_stl"
        : selectedFolderFileGeometry?.faceCount > 0 ? "import_project_scan" : "import_project_point_cloud";
    const response = await api(`/api/v1/projects/${encoded}/commands`, {
      method: "POST",
      body: JSON.stringify({
        lease_id: leaseId,
        expected_revision: info.document.revision,
        command: {
          type: commandType,
          file: file.name,
          name: file.name.replace(/\.[^.]+$/, ""),
          ...(commandType === "import_step" ? { show_in_preview: true } : {}),
        },
      }),
    });
    const result = response.history_entry?.result;
    if (!result?.body_id) throw new Error("Сервер вернул неполный результат импорта.");
    selectedObjectId = result.body_id;
    imported = true;
  } catch (error) {
    notify(`Не удалось добавить файл в дерево: ${error.message}`, true);
  } finally {
    if (leaseId) {
      try {
        await api(`/api/v1/projects/${encodeURIComponent(projectId)}/leases/${encodeURIComponent(leaseId)}/release`, {
          method: "POST", body: "{}",
        });
      } catch (error) {
        scheduleWorkspaceRefresh();
        if (imported) notify(`Файл импортирован, но блокировка не освобождена: ${error.message}`, true);
      }
    }
    projectModelFileImportInFlight = false;
    if (projectId === selectedProjectId) {
      await refreshDetail().catch(() => scheduleWorkspaceRefresh());
      if (imported && workspaceControlLockReason() === null) setWorkspaceNavigation("geometry");
    }
    updateProjectModelFileImportButton();
  }
  if (imported) notify("Файл добавлен в дерево модели и историю проекта.");
}

async function previewFolderFile(folderName, file) {
  return previewResourceFile({ type: "folder", name: folderName }, file);
}

async function previewProjectFile(projectId, file) {
  return previewResourceFile({ type: "project", id: projectId, scanRunId: selectedProjectScanRunId }, file);
}

async function previewResourceFile(context, file) {
  if (!isCurrentFileContext(context)) return;
  selectedFolderFileName = file.name;
  selectedFolderFileGeometry = null;
  updateFolderInspector(file);
  renderFolderFiles();
  updateProjectModelFileImportButton();
  const request = ++previewRequest;
  $("#folder-preview-title").textContent = file.name;
  $("#folder-preview-info").textContent = formatFileSize(file.size);
  folderPreviewCanvas.hidden = true;
  folderPreviewPlaceholder.hidden = false;

  if (!file.name.toLowerCase().endsWith(".ply")) {
    folderPreviewPlaceholder.textContent = "Предпросмотр этого формата пока не поддерживается.";
    return;
  }

  folderPreviewPlaceholder.textContent = "Загружаю 3D-модель…";
  try {
    const basePath = fileContextPath(context);
    const url = `${basePath}/${encodeURIComponent(file.name)}`;
    let response;
    try { response = await fetch(url); }
    catch (error) {
      if (error instanceof TypeError) throw new Error("Нет связи с сервером CADYX.", { cause: error });
      throw error;
    }
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(apiErrorMessage(error, response.status));
    }
    let geometry;
    try {
      geometry = await window.CadyxPlyPreview.parseStream(response.body, {
        maxPreviewPoints: 125_000,
        totalBytes: file.size,
      });
    } catch (streamError) {
      if (!streamError.message.includes("без граней")) throw streamError;
      const meshResponse = await fetch(url);
      if (!meshResponse.ok) throw streamError;
      geometry = window.CadyxPlyPreview.parse(await meshResponse.arrayBuffer());
    }
    if (request !== previewRequest || !isCurrentFileContext(context)) return;
    selectedFolderFileGeometry = geometry;
    plyViewer.load(geometry, { smoothShading: Boolean(geometry.vertexNormals) });
    folderPreviewCanvas.hidden = false;
    folderPreviewPlaceholder.hidden = true;
    $("#folder-preview-info").textContent = geometry.faceCount
      ? `${formatNumber(geometry.vertexCount)} вершин · ${formatNumber(geometry.faceCount)} граней · в сцене ${formatNumber(geometry.previewFaceCount)}`
    : `${formatNumber(geometry.vertexCount)} точек · отображается ${formatNumber(geometry.previewVertexCount)}`;
    updateProjectModelFileImportButton();
  } catch (error) {
    if (request !== previewRequest || !isCurrentFileContext(context)) return;
    folderPreviewPlaceholder.hidden = false;
    folderPreviewPlaceholder.textContent = error.message;
    $("#folder-preview-info").textContent = "";
    updateProjectModelFileImportButton();
  }
}

async function refreshWorkspace() {
  if (workspaceRefreshInFlight) {
    workspaceRefreshPending = true;
    return;
  }
  workspaceRefreshInFlight = true;
  try {
    do {
      workspaceRefreshPending = false;
      await refreshWorkspacePass();
      workspaceRefreshRetryDelayMs = 250;
    } while (workspaceRefreshPending);
  } catch (error) {
    if (error.code === "PROJECT_BUSY") {
      const delay = workspaceRefreshRetryDelayMs;
      workspaceRefreshRetryDelayMs = Math.min(delay * 2, 4000);
      workspaceRefreshPending = false;
      scheduleWorkspaceRefresh(delay);
      return;
    }
    throw error;
  } finally {
    workspaceRefreshInFlight = false;
    if (workspaceRefreshPending) scheduleWorkspaceRefresh();
  }
}

async function refreshWorkspacePass() {
  const response = await api("/api/v1/projects");
  projects = response.projects;
  projectGroups = response.groups || [];
  folders = response.folders || [];

  await restoreCurrentWorkspaceSelection();

  const previousSelection = selectedProjectId;
  const requestedSelection = projectAddressPinned
    ? [readSelectedProject(), selectedProjectId].find((id) => id && workspaceSelectionExists(id)) : null;
  selectedProjectId = requestedSelection || null;
  if (selectedProjectId && !getSelectedFolder()) {
    const canonicalPath = projectPath(selectedProjectId);
    if (canonicalPath && window.location.pathname !== canonicalPath) {
      const address = new URL(window.location.href);
      address.pathname = canonicalPath;
      address.searchParams.delete("project_id");
      address.searchParams.delete("folder");
      address.searchParams.delete("view");
      window.history.replaceState(null, "", `${address.pathname}${address.search}${address.hash}`);
      projectAddressPinned = true;
    }
  }
  if (new URLSearchParams(window.location.search).get("view") === "projects") {
    projectDetail.hidden = false;
    noSelection.hidden = true;
    setWorkspaceNavigation("projects");
  }
  const selectionChanged = selectedProjectId !== previousSelection;
  if (selectionChanged) selectedProjectScanRunId = null;
  renderProjects();
  if (selectionChanged) {
    workspaceAvailabilityConnected = false;
    filesAvailabilityConnected = false;
  }
  if (selectionChanged && selectedProjectId && !getSelectedFolder()) {
    const selected = getProject(selectedProjectId);
    if (selected?.lock) agentLocksByProject.set(selectedProjectId, selected.lock);
    activitiesByProject.delete(selectedProjectId);
    activityRevisionsByProject.delete(selectedProjectId);
  }
  if (selectionChanged) updateWorkspaceControls();

  const selectedFolder = getSelectedFolder();
  if (selectedFolder) showFolderDetail(selectedFolder);
  else if (selectedProjectId && getProject(selectedProjectId)) {
    const selected = getProject(selectedProjectId).project;
    if (primaryGrid.classList.contains("folder-mode") || detailedProjectId !== selectedProjectId
        || !renderedSceneKey?.startsWith(`${selectedProjectId}:`)) {
      showProjectLoading(selected.name);
    }
    const modifiedAt = selected.modified_at;
    if (detailedProjectId !== selectedProjectId || detailedProjectModifiedAt !== modifiedAt || !renderedSceneKey) {
      await refreshDetail();
    } else if (selectionChanged) {
      await refreshActivitySnapshot(selectedProjectId);
    }
  }
  else {
    if (!projectAddressPinned) setWorkspaceNavigation("projects");
    const browsingProjects = !projectAddressPinned && primaryGrid.dataset.view === "projects";
    projectDetail.hidden = !browsingProjects;
    $("#object-panel").hidden = true;
    $("#folder-detail").hidden = true;
    noSelection.hidden = browsingProjects;
    $("#document-title").textContent = "CADYX";
    $("#document-kind").textContent = "ПРОЕКТ НЕ ВЫБРАН";
    $("#project-short-id").textContent = "—";
    copyProjectIdButton.disabled = true;
    $("#project-light").classList.remove("active");
    $("#document-revision").textContent = "—";
  }
}

function handleRefreshError(error) {
  if (error.code === "PROJECT_BUSY") {
    scheduleWorkspaceRefresh(workspaceRefreshRetryDelayMs);
    workspaceRefreshRetryDelayMs = Math.min(workspaceRefreshRetryDelayMs * 2, 4000);
    return;
  }
  notify(error.message, true);
}

function scheduleWorkspaceRefresh(delayMs = 60) {
  // Keep the first refresh deadline; a burst of SSE notifications must not postpone scene sync forever.
  if (refreshTimer) return;
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    refreshWorkspace().catch(handleRefreshError);
  }, delayMs);
}

async function checkAutomaticUpdates() {
  if (document.visibilityState === "hidden" || automaticUpdateInFlight) return;
  automaticUpdateInFlight = true;
  try {
    const selectionAtRequest = selectedProjectId;
    const query = new URLSearchParams();
    const folder = selectionAtRequest?.startsWith(FOLDER_SELECTION_PREFIX)
      ? selectionAtRequest.slice(FOLDER_SELECTION_PREFIX.length) : null;
    if (folder) query.set("folder", folder);
    else if (selectionAtRequest) query.set("project_id", selectionAtRequest);
    const current = await api(`/api/v1/updates?${query.toString()}`, { cache: "no-store" });
    setApiConnectionStatus(true);
    if (selectionAtRequest !== selectedProjectId) return;
    workspaceAvailabilityConnected = true;
    const previous = automaticUpdateSnapshot;
    automaticUpdateSnapshot = current;
    if (current.project_lock && Number.isInteger(current.activity_revision)) {
      filesAvailabilityConnected = true;
    }
    if (automaticUpdatesUnavailable) {
      automaticUpdatesUnavailable = false;
      notify("Связь восстановлена, синхронизирую панель.");
      scheduleWorkspaceRefresh();
    }
    if (current.project_lock && selectedProjectId && !folder) {
      const previousLock = agentLocksByProject.get(selectedProjectId);
      agentLocksByProject.set(selectedProjectId, current.project_lock);
      if (lockAvailabilityKey(current.project_lock) !== lockAvailabilityKey(previousLock)) {
        renderAgentActivity(activitiesByProject.get(selectedProjectId) || [], current.project_lock);
      }
    }
    if (!previous) {
      if (loadedUiVersion && current.ui_version !== loadedUiVersion) {
        window.location.reload();
        return;
      }
      updateWorkspaceControls();
      return;
    }
    if (current.activity_revision !== null && previous.activity_revision !== null &&
        current.activity_revision < previous.activity_revision && selectedProjectId) {
      activitiesByProject.delete(selectedProjectId);
      activityRevisionsByProject.delete(selectedProjectId);
      updateWorkspaceControls();
    }
    if (current.dashboard_revision !== null && previous.dashboard_revision !== null &&
        current.dashboard_revision < previous.dashboard_revision && selectedProjectId) {
      dashboardsByProject.delete(selectedProjectId);
      dashboardRevisionsByProject.delete(selectedProjectId);
    }
    if (current.ui_version !== previous.ui_version) {
      window.location.reload();
      return;
    }
    if (current.folder_revision !== previous.folder_revision && folder) {
      loadFolderFiles(folder).catch((error) => notify(`Не удалось обновить список файлов: ${error.message}`, true));
    }
    const workspaceChanged = current.workspace_revision !== previous.workspace_revision;
    const projectChanged = current.project_revision !== previous.project_revision;
    const activityChanged = current.activity_revision !== previous.activity_revision;
    const dashboardChanged = current.dashboard_revision !== previous.dashboard_revision;
    if (workspaceChanged || projectChanged) {
      scheduleWorkspaceRefresh();
    } else if (activityChanged && selectedProjectId && !folder) {
      activityRevisionsByProject.delete(selectedProjectId);
      updateWorkspaceControls();
      refreshActivitySnapshot(selectedProjectId)
        .catch((error) => notify(`Не удалось обновить состояние агента: ${error.message}`, true));
    }
    if (dashboardChanged && selectedProjectId && !folder) {
      refreshAgentDashboardSnapshot(selectedProjectId)
        .catch((error) => notify(`Не удалось обновить очередь агента: ${error.message}`, true));
    }
    if (selectedProjectId && !folder && primaryGrid.dataset.view === "analysis") {
      await refreshValidationRecords(selectedProjectId).catch(showValidationRecordsError);
    }
    updateWorkspaceControls();
  } catch (error) {
    setApiConnectionStatus(false);
    workspaceAvailabilityConnected = false;
    filesAvailabilityConnected = false;
    updateWorkspaceControls();
    if (error.status === 404) {
      scheduleWorkspaceRefresh();
      return;
    }
    if (!automaticUpdatesUnavailable) {
      notify("Автоматическая синхронизация временно недоступна.", true);
    }
    automaticUpdatesUnavailable = true;
  } finally {
    automaticUpdateInFlight = false;
  }
}

async function refreshDetail() {
  const projectId = selectedProjectId;
  if (!projectId) return;
  const request = ++detailRequest;
  const refreshAuxiliary = !renderedSceneKey?.startsWith(`${projectId}:`)
    || detailedProjectModifiedAt === null
    || !activityRevisionsByProject.has(projectId)
    || !dashboardsByProject.has(projectId);
  const detailRequests = window.CadyxViewport.detailRefreshPlan(refreshAuxiliary, primaryGrid.dataset.view);
  const activity = detailRequests.activity
    ? await refreshActivitySnapshot(projectId)
    : (activitiesByProject.get(projectId) || []).slice(-1)[0] || null;
  if (projectId !== selectedProjectId || request !== detailRequest) return;
  if (activity?.state === "running") {
    $("#folder-detail").hidden = true;
    projectDetail.hidden = false;
    noSelection.hidden = true;
    const selected = getProject(projectId);
    $("#document-title").textContent = selected?.project.name || "CADYX · работа агента";
  }
  const encodedProjectId = encodeURIComponent(projectId);
  const [info, scene, dashboard] = await Promise.all([
    api(`/api/v1/projects/${encodedProjectId}`),
    api(`/api/v1/projects/${encodedProjectId}/viewport`),
    detailRequests.dashboard
      ? api(`/api/v1/projects/${encodedProjectId}/dashboard`)
      : Promise.resolve(null),
  ]);
  if (projectId !== selectedProjectId || request !== detailRequest) return;
  if (scene.revision !== info.document.revision) { scheduleWorkspaceRefresh(); return; }
  agentLocksByProject.set(projectId, info.lock);
  renderAgentActivity(activitiesByProject.get(projectId) || [], info.lock);
  if (dashboard) {
    const dashboardRevision = Number.isInteger(dashboard.revision) ? dashboard.revision : 0;
    const knownDashboardRevision = dashboardRevisionsByProject.get(projectId);
    if (knownDashboardRevision === undefined || dashboardRevision >= knownDashboardRevision) {
      dashboardRevisionsByProject.set(projectId, dashboardRevision);
      dashboardsByProject.set(projectId, dashboard);
      renderAgentDashboard(dashboard);
    }
  }
  if (detailRequests.comparison) {
    const comparison = await api(`/api/v1/projects/${encodeURIComponent(projectId)}/comparison`);
    if (projectId !== selectedProjectId || request !== detailRequest) return;
    if (comparison.revision !== scene.revision) { scheduleWorkspaceRefresh(); return; }
    $("#comparison-description").hidden = !comparison.available;
    $("#comparison-empty").hidden = comparison.available;
    $("#comparison-empty").textContent = "Для этого проекта пока нет данных сравнения.";
    $("#comparison-preview").hidden = !comparison.available;
    $("#comparison-panel").dataset.available = String(comparison.available);
    if (comparison.available) {
      const original = comparison.original, modified = comparison.modified;
      const positions = [...modified.positions, ...original.positions];
      const offset = modified.positions.length / 3;
      const lines = [];
      const edges = new Set();
      for (let i = 0; i < original.triangles.length; i += 3) {
        const triangle = original.triangles.slice(i, i + 3);
        for (let j = 0; j < 3; j++) {
          const a = triangle[j], b = triangle[(j + 1) % 3];
          const key = a < b ? `${a}:${b}` : `${b}:${a}`;
          if (!edges.has(key)) { edges.add(key); lines.push(a + offset, b + offset); }
        }
      }
      const canvas = $("#comparison-preview");
      canvas.comparisonViewer ||= window.CadyxViewport.createViewer(canvas);
      const comparisonKey = `${projectId}:${comparison.revision}`;
      if (canvas.comparisonKey !== comparisonKey) {
        const bounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
        for (let i = 0; i < positions.length; i++) {
          bounds.min[i % 3] = Math.min(bounds.min[i % 3], positions[i]);
          bounds.max[i % 3] = Math.max(bounds.max[i % 3], positions[i]);
        }
        canvas.comparisonViewer.load({ positions, triangles: modified.triangles, lines, points: [], bounds }, { reset: true });
        canvas.comparisonKey = comparisonKey;
      }
      $("#comparison-metrics").textContent = `Объём: ${formatNumber(comparison.original_volume)} → ${formatNumber(comparison.modified_volume)} мм³ · Δ ${formatNumber(comparison.volume_delta)} мм³`;
    }
  }
  detailedProjectId = projectId;
  detailedProjectModifiedAt = info.project.modified_at;
  $("#project-short-id").textContent = projectId.slice(0, 8).toUpperCase();
  copyProjectIdButton.disabled = false;
  const objects = info.document.objects;
  const objectsById = new Map(objects.map((item) => [item.id, item]));
  const visibleSketchId = activeSketchId
    && objectsById.get(activeSketchId)?.type === "sketch" ? activeSketchId : null;
  const key = `${projectId}:${scene.revision}:${visibleSketchId || "no-sketch"}`;
  const visibleObjects = objects.filter((item) => {
    if (item.type === "sketch") return item.id === visibleSketchId;
    if (item.sketch_id) return item.sketch_id === visibleSketchId;
    return true;
  });
  let transition = pendingViewportTransition?.projectId === projectId
    ? { ...pendingViewportTransition } : null;
  transition = window.CadyxViewport.transitionForRevision(transition, scene.revision);
  if (transition?.featureId) {
    const feature = objectsById.get(transition.featureId);
    transition.bodyId = feature?.body_id;
    const sourceBody = feature?.source_body_id && objectsById.get(feature.source_body_id);
    if (transition.type === "pad") {
      const sketchId = feature?.parameters?.sketch_id;
      const sketch = sketchId ? objectsById.get(sketchId) : null;
      if (sketch?.type === "sketch") {
        const profileObjects = objects.filter((object) => object.sketch_id === sketchId
          && object.construction !== true);
        const minimums = profileObjects.flatMap((object) => {
          if (object.type === "point" && Number.isFinite(object.y)) return [object.y];
          if (object.type === "circle" && Number.isFinite(object.radius)) {
            const center = objectsById.get(object.center_id);
            return Number.isFinite(center?.y) ? [center.y - object.radius] : [];
          }
          if (object.type === "ellipse" && Number.isFinite(object.minor_radius)
              && Number.isFinite(object.center_y)) return [object.center_y - object.minor_radius];
          return [];
        });
        const profileMinimum = minimums.length ? Math.min(...minimums) : null;
        Object.assign(transition, window.CadyxViewport.padGrowthFrame(
          sketch.plane, sketch.section_normal_axis, sketch.section_offset, profileMinimum));
      }
    } else if (transition.type === "fuse" && sourceBody?.geometry?.plane === "XY"
        && Number.isFinite(sourceBody.geometry.depth)) {
      transition.growAxis = 2;
      transition.growOrigin = sourceBody.geometry.depth;
    }
  }
  if (renderedSceneKey !== key) {
    const positions = [], bodyPositions = [], triangles = [], lines = [], points = [], rotationAxes = [];
    const bodyLineRanges = [], bodyTriangleRanges = [], featureTriangleRanges = [], overlayTriangleRanges = [], surfaceTriangleRanges = [];
    let targetFound = false;
    for (const object of scene.objects) {
      const source = objectsById.get(object.object_id);
      if (source?.type !== "body" && object.type !== "feature_overlay" && source?.sketch_id !== visibleSketchId) continue;
      const offset = positions.length / 3;
      const lineOffset = lines.length;
      const triangleStart = triangles.length / 3;
      const pointOffset = points.length;
      for (const value of object.positions) {
        positions.push(value);
        if (source?.type === "body" && object.type === "body") bodyPositions.push(value);
      }
      for (const index of object.triangles) triangles.push(index + offset);
      if (source?.geometry?.kind === "triangle_mesh" && object.triangles.length) {
        surfaceTriangleRanges.push({ start: triangleStart, end: triangles.length / 3 });
      }
      if (source?.type === "body" && object.type === "body" && object.triangles.length) {
        bodyTriangleRanges.push({ objectId: object.object_id, start: triangleStart, end: triangles.length / 3 });
      }
      if (object.type === "feature_overlay" && object.triangles.length) {
        overlayTriangleRanges.push({ start: triangleStart, end: triangles.length / 3 });
      }
      if (object.triangles.length) {
        for (const range of object.feature_face_ranges || []) {
          featureTriangleRanges.push({ featureId: range.feature_id, start: triangleStart + range.start,
            end: triangleStart + range.end });
        }
      }
      for (const index of object.lines) lines.push(index + offset);
      if (source?.type === "body" && object.lines.length) {
        bodyLineRanges.push({ start: lineOffset, end: lineOffset + object.lines.length });
      }
      for (const index of object.points) points.push(index + offset);
      if (source?.type === "body" && containsRevolvedProfile(source.geometry)) {
        let minZ = Infinity, maxZ = -Infinity;
        for (let index = 2; index < object.positions.length; index += 3) {
          minZ = Math.min(minZ, object.positions[index]);
          maxZ = Math.max(maxZ, object.positions[index]);
        }
        const axisMargin = Math.max((maxZ - minZ) * 0.06, 0.5);
        rotationAxes.push({ start: [0, 0, minZ - axisMargin], end: [0, 0, maxZ + axisMargin] });
      }
      if (transition?.bodyId === object.object_id) {
        transition.vertexRange = [offset, offset + object.positions.length / 3];
        if (transition.type === "brep") {
          transition.center = [0, 1, 2].map((axis) => {
            let min = Infinity, max = -Infinity;
            for (let index = axis; index < object.positions.length; index += 3) {
              min = Math.min(min, object.positions[index]);
              max = Math.max(max, object.positions[index]);
            }
            return min / 2 + max / 2;
          });
        }
        targetFound = true;
      } else if (transition?.type === "sketch-point" && transition.objectId === object.object_id && object.points.length) {
        transition.pointIndex = pointOffset + object.points[0];
        targetFound = true;
      } else if (transition?.type === "sketch-lines"
        && (transition.objectId === object.object_id || transition.allSketchLines && source?.sketch_id === transition.sketchId)
        && object.lines.length) {
        if (!transition.lineRange) transition.lineRange = [lineOffset, lineOffset + object.lines.length];
        else transition.lineRange[1] += object.lines.length;
        targetFound = true;
      }
    }
    const boundsPositions = visibleSketchId && bodyPositions.length ? bodyPositions : positions;
    const bounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
    for (let index = 0; index < boundsPositions.length; index += 3) {
      for (let axis = 0; axis < 3; axis += 1) {
        bounds.min[axis] = Math.min(bounds.min[axis], boundsPositions[index + axis]);
        bounds.max[axis] = Math.max(bounds.max[axis], boundsPositions[index + axis]);
      }
    }
    if (!positions.length) {
      bounds.min = [0, 0, 0];
      bounds.max = [0, 0, 0];
    }
    if (!targetFound) transition = null;
    const hasPointCloud = visibleObjects.some((item) => item.type === "feature"
      && item.operation === "import_point_cloud");
    const viewportGeometry = { positions, triangles, lines, points, bounds, rotationAxes,
      bodyLineRanges, bodyTriangleRanges, featureTriangleRanges, overlayTriangleRanges, surfaceTriangleRanges,
      ...(hasPointCloud ? { pointStyle: { size: 1.5, opacity: 0.94, color: "#ff7417" } } : {}) };
    currentModelViewportGeometry = viewportGeometry;
    modelViewer.load(viewportGeometry, {
      reset: !renderedSceneKey?.startsWith(`${projectId}:`), transition, smoothShading: true,
    });
    for (const view of orthographicViewers) {
      view.viewer.load(viewportGeometry, { reset: false });
    }
    if (pendingViewportTransition?.projectId === projectId) pendingViewportTransition = null;
    renderedSceneKey = key;
  }
  renderedSceneProjectId = projectId;
  renderedSceneRevision = scene.revision;
  modelViewer.render();
  renderSceneMetrics(scene, objectsById, visibleSketchId);
  $("#sidebar-project-name").textContent = info.project.name;

  $("#folder-detail").hidden = true;
  $("#object-panel").hidden = false;
  projectDetail.hidden = false;
  noSelection.hidden = true;
  $("#document-title").textContent = info.project.name;
  if (selectedObjectId && !visibleObjects.some((item) => item.id === selectedObjectId)) selectedObjectId = null;
  if (!selectedObjectId) {
    const topLevel = visibleObjects.filter((item) => item.type === "body" || item.type === "sketch");
    selectedObjectId = topLevel[topLevel.length - 1]?.id || null;
  }
  currentObjects = visibleObjects;
  renderObjectTree(visibleObjects);
  window.dispatchEvent(new CustomEvent("cadyx:model-tree-updated", { detail: { objects: visibleObjects } }));
  updateProjectModelFileImportButton();
  refreshValidationRecords(projectId).catch(showValidationRecordsError);
}

async function refreshActivitySnapshot(projectId) {
  const response = await api(`/api/v1/projects/${encodeURIComponent(projectId)}/activity`);
  if (projectId !== selectedProjectId) return null;
  const revision = Number.isInteger(response.revision) ? response.revision : 0;
  const knownRevision = activityRevisionsByProject.get(projectId);
  if (knownRevision !== undefined && revision < knownRevision) {
    const cached = activitiesByProject.get(projectId) || [];
    return cached.slice().reverse().find((item) => item.state === "running") || cached[cached.length - 1] || null;
  }
  const activities = response.activities || [];
  activityRevisionsByProject.set(projectId, revision);
  activitiesByProject.set(projectId, activities);
  renderAgentActivity(activities, agentLocksByProject.get(projectId));
  const current = activities.slice().reverse().find((item) => item.state === "running") || activities[activities.length - 1] || null;
  if (current?.state === "running") {
    $("#folder-detail").hidden = true;
    projectDetail.hidden = false;
    noSelection.hidden = true;
    $("#document-title").textContent = getProject(projectId)?.project.name || "CADYX · работа агента";
  }
  return current;
}

async function refreshAgentDashboardSnapshot(projectId) {
  const dashboard = await api(`/api/v1/projects/${encodeURIComponent(projectId)}/dashboard`);
  if (projectId !== selectedProjectId) return null;
  const revision = Number.isInteger(dashboard.revision) ? dashboard.revision : 0;
  const knownRevision = dashboardRevisionsByProject.get(projectId);
  if (knownRevision !== undefined && revision < knownRevision) {
    renderAgentDashboard(dashboardsByProject.get(projectId));
    return dashboardsByProject.get(projectId) || null;
  }
  dashboardRevisionsByProject.set(projectId, revision);
  dashboardsByProject.set(projectId, dashboard);
  renderAgentDashboard(dashboard);
  return dashboard;
}

function renderAgentActivity(value, lock = null) {
  const activities = Array.isArray(value) ? value : value ? [value] : [];
  const dashboard = dashboardsByProject.get(selectedProjectId);
  renderCurrentProgress(
    dashboard,
    activities,
    lock,
  );
  renderWorkflowHistory(activities);
  updateWorkspaceControls();
}

function objectTypeLabel(item) {
  return ({ body: "Тело", feature: "Операция", sketch: "Эскиз", point: "Точка", line: "Отрезок", circle: "Окружность", ellipse: "Эллипс", constraint: "Ограничение" })[item?.type] || "Объект";
}

function constraintLabel(kind) {
  return ({ horizontal: "Горизонтальность", vertical: "Вертикальность", coincident: "Совпадение", distance: "Расстояние", radius: "Радиус" })[kind] || "Ограничение";
}

function displayObjectName(name) {
  const commonNames = {
    Box: "Параллелепипед",
    Sketch: "Эскиз",
    Extrusion: "Выдавливание",
    Revolution: "Вращение",
  };
  if (commonNames[name]) return commonNames[name];
  const thread = /^M([\d.]+)x([\d.]+) (right|left)-hand thread$/i.exec(name || "");
  if (thread) return `Резьба M${thread[1]}×${thread[2]} (${thread[3].toLowerCase() === "right" ? "правая" : "левая"})`;
  return name;
}

const featureOperationLabels = {
  create_box: "Построение параллелепипеда",
  metric_thread: "Построение метрической резьбы",
  reconstruct_scan: "Восстановление модели по скану",
  import_step: "Импорт STEP-модели",
  extrude_sketch: "Выдавливание профиля",
  revolve_sketch: "Вращение профиля",
  loft_sketches: "Переход по сечениям",
  create_ruled_shell: "Линейчатая оболочка",
  cut_bodies: "Вырезание тел",
  fuse_bodies: "Объединение тел",
  resize_imported_thread: "Изменение диаметра резьбы",
  transform_imported_brep: "Преобразование импортированной модели",
  sweep_sketch: "Протяжка профиля по траектории",
  fillet_edges: "Скругление рёбер",
};
function objectSummary(item, objects = currentObjects) {
  if (item.type === "body") return dimensionsText(item.geometry?.dimensions);
  if (item.type === "feature") {
    const operation = item.operation;
    const parameters = item.parameters || {};
    const label = featureOperationLabels[operation] || "Операция";
    if (operation === "revolve_sketch" && Number.isFinite(parameters.outer_diameter) && Number.isFinite(parameters.height)) {
      return `Ø${formatNumber(parameters.outer_diameter)} × ${formatNumber(parameters.height)} мм`;
    }
    if (operation === "resize_imported_thread") return `${formatNumber(parameters.major_diameter)} → ${formatNumber(parameters.major_diameter + parameters.diameter_delta)} мм`;
    if (operation === "transform_imported_brep") return `${formatNumber(parameters.angle_degrees)}° · ${parameters.translation?.map(formatNumber).join(", ") || ""} мм`;
    return `${label} · ${dimensionsText(parameters)}`;
  }
  if (item.type === "sketch") return `${item.plane} · ${objects.filter((child) => child.sketch_id === item.id).length} объектов`;
  if (item.type === "point") return `(${formatNumber(item.x)}, ${formatNumber(item.y)})`;
  if (item.type === "line") {
    const start = objects.find((child) => child.id === item.start_id);
    const end = objects.find((child) => child.id === item.end_id);
    const pointLabel = (point) => point ? `(${formatNumber(point.x)}, ${formatNumber(point.y)})` : "?";
    return `${pointLabel(start)} → ${pointLabel(end)}`;
  }
  if (item.type === "circle") return `R ${formatNumber(item.radius)}`;
  if (item.type === "ellipse") return `R ${formatNumber(item.major_radius)} × ${formatNumber(item.minor_radius)}${Number.isFinite(item.sweep_radians) ? ` · ${formatNumber(item.sweep_radians * 180 / Math.PI)}°` : ""}`;
  if (item.type === "spline") return `Сплайн · ${item.point_ids.length} точек`;
  if (item.type === "constraint") return constraintLabel(item.kind);
  return "";
}

function containsRevolvedProfile(geometry) {
  return geometry?.kind === "revolved_profile"
    || geometry?.kind === "filleted_solid" && containsRevolvedProfile(geometry.base)
    || ["fused_profiles", "fused_solids"].includes(geometry?.kind)
      && (containsRevolvedProfile(geometry.base) || containsRevolvedProfile(geometry.tool));
}

const editableDimensionLabels = { width: "Ширина", height: "Высота", depth: "Глубина", outer_diameter: "Наружный диаметр", major_diameter: "Диаметр", pitch: "Шаг", length: "Длина", angle_degrees: "Угол", radius: "Радиус" };

function editableDimensions(feature) {
  if (!feature || feature.type !== "feature") return null;
  const fields = ({
    create_box: ["width", "height", "depth"],
    metric_thread: ["major_diameter", "pitch", "length"],
    extrude_sketch: ["depth"],
    revolve_sketch: ["outer_diameter", "height"],
    sweep_sketch: ["radius"],
    fillet_edges: ["radius"],
  })[feature.operation];
  if (!fields) return null;
  return fields.filter((name) => Number.isFinite(feature.parameters?.[name]))
    .map((name) => ({ name, value: feature.parameters[name], label: editableDimensionLabels[name] }));
}

function renderObjectTree(objects) {
  const root = $("#geometry-rows");
  root.replaceChildren();
  const bodies = objects.filter((item) => item.type === "body");
  const consumedBodies = new Set(objects
    .filter((item) => item.type === "feature")
    .flatMap((item) => item.operation === "fuse_bodies" ? [item.base_body_id, item.tool_body_id]
      : ["fillet_edges", "resize_imported_thread", "transform_imported_brep"].includes(item.operation) ? [item.source_body_id] : []));
  for (const body of bodies) {
    if (consumedBodies.has(body.id)) continue;
    const feature = [...objects].reverse().find((item) => item.type === "feature" && item.body_id === body.id);
    const tr = document.createElement("tr");
    const nameCell = document.createElement("th");
    nameCell.scope = "row";
    nameCell.textContent = displayObjectName(feature?.name) || displayObjectName(body.name) || featureOperationLabels[feature?.operation] || "Тело модели";
    const currentCell = document.createElement("td");
    currentCell.textContent = dimensionsText(body.geometry?.dimensions) || objectSummary(feature, objects) || "Размеры не определены";
    const inputCell = document.createElement("td");
    const dimensions = editableDimensions(feature);
    if (dimensions?.length) {
      const fields = document.createElement("div");
      fields.className = "geometry-edit-fields";
      for (const field of dimensions) {
        const label = document.createElement("label");
        label.className = "sr-only";
        label.textContent = `${field.label}: ${nameCell.textContent}`;
        const input = document.createElement("input");
        input.type = "number";
        input.min = "0.000001";
        input.step = "any";
        input.value = field.value;
        input.dataset.featureId = feature.id;
        input.dataset.parameter = field.name;
        input.dataset.originalValue = String(field.value);
        input.title = field.label;
        label.append(input);
        fields.append(label);
      }
      const apply = document.createElement("button");
      apply.type = "button";
      apply.className = "button geometry-apply";
      apply.textContent = "Применить";
      apply.addEventListener("click", () => applyGeometryDimensions(feature.id, fields));
      fields.append(apply);
      inputCell.append(fields);
    } else {
      inputCell.textContent = "Параметры этой геометрии нельзя изменить здесь";
      inputCell.className = "geometry-not-editable";
    }
    tr.append(nameCell, currentCell, inputCell);
    tr.title = body.id;
    root.append(tr);
  }
  const hasBodies = root.children.length > 0;
  $("#objects-empty").textContent = hasBodies ? "" : "В этой модели пока нет тел геометрии.";
  $("#objects-empty").hidden = hasBodies;
}

async function applyGeometryDimensions(featureId, fields) {
  const status = $("#geometry-status");
  if (!selectedProjectId || workspaceControlLockReason() !== null) {
    status.textContent = "Изменение недоступно: проект сейчас занят или его состояние не подтверждено.";
    status.hidden = false;
    return;
  }
  const inputs = [...fields.querySelectorAll("input")];
  if (inputs.some((input) => !input.value.trim() || !Number.isFinite(Number(input.value)) || Number(input.value) <= 0)) {
    status.textContent = "Введите для каждого размера положительное число.";
    status.hidden = false;
    return;
  }
  const changed = inputs.filter((input) => Number(input.value) !== Number(input.dataset.originalValue));
  if (!changed.length) return;
  const projectId = selectedProjectId;
  status.textContent = "Перестраиваю геометрию…";
  status.hidden = false;
  fields.querySelectorAll("button, input").forEach((element) => { element.disabled = true; });
  let leaseId;
  try {
    const encoded = encodeURIComponent(projectId);
    const info = await api(`/api/v1/projects/${encoded}`);
    if (info.lock?.state !== "FREE" || workspaceControlLockReason() !== null || projectId !== selectedProjectId) {
      throw new Error("Проект занят; дождитесь завершения текущей операции.");
    }
    const lease = await api(`/api/v1/projects/${encoded}/leases`, { method: "POST", body: JSON.stringify({ owner_id: `geometry-ui:${crypto.randomUUID()}` }) });
    leaseId = lease.lease.lease_id;
    let expectedRevision = info.document.revision;
    for (const input of changed) {
      const command = input.dataset.modelParameter === "true"
        ? { type: "set_model_parameter", name: input.dataset.parameter, value: Number(input.value) }
        : { type: "set_parameter", feature_id: featureId, name: input.dataset.parameter, value: Number(input.value) };
      await api(`/api/v1/projects/${encoded}/commands`, { method: "POST", body: JSON.stringify({ lease_id: leaseId, expected_revision: expectedRevision, command }) });
      expectedRevision += 1;
      input.dataset.originalValue = input.value;
    }
    status.textContent = "Геометрия перестроена.";
    await refreshDetail();
  } catch (error) {
    status.textContent = error.message || "Не удалось перестроить геометрию.";
    notify(status.textContent, true);
    if (projectId === selectedProjectId) await refreshDetail().catch(() => {});
  } finally {
    if (leaseId) {
      try { await api(`/api/v1/projects/${encodeURIComponent(projectId)}/leases/${encodeURIComponent(leaseId)}/release`, { method: "POST", body: "{}" }); }
      catch { scheduleWorkspaceRefresh(); }
    }
    fields.querySelectorAll("button, input").forEach((element) => { element.disabled = false; });
  }
}

function dimensionsText(dimensions) {
  if (!dimensions) return "";
  return `${formatNumber(dimensions.width)} × ${formatNumber(dimensions.height)} × ${formatNumber(dimensions.depth)}`;
}

function selectObjectFromResult(projectId, result) {
  const objectId = result?.body_id || result?.sketch_id || result?.object_id || result?.constraint_id || result?.feature_id;
  if (projectId === selectedProjectId && objectId) selectedObjectId = objectId;
}

function animateModelCamera(change) {
  setCameraPresetState(null);
  const duration = change.duration_ms;
  const zoomFactor = Number.isFinite(change.zoom_factor) ? change.zoom_factor : 1;
  const token = ++cameraAnimationId;
  if (duration === 0) {
    modelViewer.orbitBy(change.yaw_delta, change.pitch_delta, zoomFactor);
    return;
  }
  const startedAt = performance.now();
  let previousEased = 0;
  const tick = (now) => {
    if (token !== cameraAnimationId) return;
    const ratio = Math.min(1, (now - startedAt) / duration);
    const eased = ratio < 0.5 ? 2 * ratio * ratio : 1 - ((-2 * ratio + 2) ** 2) / 2;
    const step = eased - previousEased;
    modelViewer.orbitBy(change.yaw_delta * step, change.pitch_delta * step, zoomFactor ** step);
    previousEased = eased;
    if (ratio < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function handleStreamEvent(type, event) {
  if (type === "project.created" && projectCreationPending) return;
  if (type === "workspace.selection.changed" && (projectAddressPinned || projectsChooserPinned)) return;
  const projectId = event.project_id;
  if (type === "workspace.selection.changed" || type === "project.created") {
    const selection = type === "project.created"
      ? { type: "project", project_id: projectId }
      : event.data || {};
    const address = new URL(window.location.href);
    if (selection.type === "project" && typeof selection.project_id === "string") {
      address.searchParams.set("project_id", selection.project_id);
      address.searchParams.delete("folder");
    } else if (selection.type === "folder" && typeof selection.folder_name === "string") {
      address.searchParams.delete("project_id");
      address.searchParams.set("folder", selection.folder_name);
    } else {
      return;
    }
    address.searchParams.delete("view");
    window.location.assign(`${address.pathname}${address.search}${address.hash}`);
    return;
  }
  if (type === "viewport.body-motion") {
    if (projectId === selectedProjectId) {
      modelViewer.animateBodyMotion(event.data?.tracks, event.data?.duration_ms);
      orthographicViewers.forEach((view) => view.viewer.animateBodyMotion(event.data?.tracks, event.data?.duration_ms));
    }
    return;
  }
  if (type === "project.deleted") {
    if (projectAddressPinned && projectId === selectedProjectId) window.location.assign("/");
    else scheduleWorkspaceRefresh();
    return;
  }
  if (!projectId) return;
  const data = event.data || {};

  if (type === "agent.activity") {
    const incomingRevision = Number.isInteger(data.revision) ? data.revision : null;
    const knownRevision = activityRevisionsByProject.get(projectId);
    if (incomingRevision !== null && knownRevision !== undefined && incomingRevision < knownRevision) return;
    if (incomingRevision !== null) activityRevisionsByProject.set(projectId, incomingRevision);
    const activities = activitiesByProject.get(projectId) || [];
    const existing = activities.findIndex((item) => item.operation_id === data.operation_id);
    if (existing < 0) activities.push(data);
    else activities[existing] = data;
    if (activities.length > 128) activities.splice(0, activities.length - 128);
    activitiesByProject.set(projectId, activities);
    const viewportUpdate = window.CadyxViewport.viewportUpdateForAgentActivity(
      projectId, selectedProjectId, data, activeSketchId,
    );
    if (viewportUpdate) {
      activeSketchId = viewportUpdate.activeSketchId;
      pendingViewportTransition = viewportUpdate.transition
        ? { ...viewportUpdate.transition, expectedRevision: renderedSceneRevision + 1 } : null;
      selectObjectFromResult(projectId, data.result);
    }
    if (projectId === selectedProjectId) renderAgentActivity(activities, agentLocksByProject.get(projectId));
    if (projectId === selectedProjectId && data.state === "running") {
      $("#folder-detail").hidden = true;
      projectDetail.hidden = false;
      noSelection.hidden = true;
      $("#document-title").textContent = getProject(projectId)?.project.name || "CADYX · работа агента";
    }
  } else if (type === "agent.dashboard") {
    const incomingRevision = Number.isInteger(data.revision) ? data.revision : null;
    const knownRevision = dashboardRevisionsByProject.get(projectId);
    if (incomingRevision !== null && knownRevision !== undefined && incomingRevision < knownRevision) return;
    if (incomingRevision !== null) dashboardRevisionsByProject.set(projectId, incomingRevision);
    dashboardsByProject.set(projectId, data);
    if (projectId === selectedProjectId) renderAgentDashboard(data);
  } else if (type === "command.completed") {
    // command.completed is a bounded receipt; the full result and animation plan arrive in agent.activity.
    if (projectId === selectedProjectId) scheduleWorkspaceRefresh();
  } else if (type === "document.changed") {
    if (projectId === selectedProjectId && Number.isInteger(data.revision))
      pendingViewportTransition = window.CadyxViewport.transitionForRevision(pendingViewportTransition, data.revision);
    scheduleWorkspaceRefresh();
  } else if (type === "viewport.camera") {
    if (projectId === selectedProjectId) animateModelCamera(data);
  } else if (type === "project.lock.changed") {
    agentLocksByProject.set(projectId, data);
    if (projectId === selectedProjectId) {
      renderAgentActivity(activitiesByProject.get(projectId) || [], data);
    } else if (primaryGrid.dataset.view === "projects") {
      renderProjects();
      updateWorkspaceControls();
    }
  }
}

folderFileList.addEventListener("click", (event) => {
  const backButton = event.target.closest("button[data-scan-run-back]");
  if (backButton && selectedProjectScanRunId) {
    selectedProjectScanRunId = null;
    $("#folder-files-title").textContent = "Файлы проекта";
    loadProjectFiles(selectedProjectId).catch((error) => showProjectFilesLoadError(selectedProjectId, error));
    return;
  }
  const scanRunButton = event.target.closest("button[data-scan-run-id]");
  if (scanRunButton && primaryGrid.dataset.view === "files" && canOpenFilesView()) {
    selectedProjectScanRunId = scanRunButton.dataset.scanRunId;
    $("#folder-files-title").textContent = scanRunLabel(selectedProjectScanRunId);
    loadProjectFiles(selectedProjectId).catch((error) => showProjectFilesLoadError(selectedProjectId, error));
    return;
  }
  const button = event.target.closest("button[data-file-name]");
  const file = folderFiles.find((item) => item.name === button?.dataset.fileName);
  if (!button || !file) return;
  const folderName = getSelectedFolder();
  if (folderName) previewFolderFile(folderName, file);
  else if (primaryGrid.dataset.view === "files" && canOpenFilesView()) previewProjectFile(selectedProjectId, file);
});

folderImportModelFileButton.addEventListener("click", () => {
  importSelectedProjectModelFile();
});

const stream = new EventSource("/api/v1/events");
// A gap means the bounded replay was incomplete; fetch authoritative state.
stream.addEventListener("stream.gap", () => {
  activitiesByProject.clear();
  activityRevisionsByProject.clear();
  pendingViewportTransition = null;
  activeSketchId = null;
  selectedObjectId = null;
  detailedProjectModifiedAt = null;
  scheduleWorkspaceRefresh(0);
  checkAutomaticUpdates();
});
const eventTypes = ["project.created", "project.deleted", "workspace.selection.changed", "project.lock.changed", "agent.activity", "agent.dashboard", "command.completed", "document.changed", "viewport.camera", "viewport.body-motion"];
for (const type of eventTypes) {
  stream.addEventListener(type, (message) => {
    try { handleStreamEvent(type, JSON.parse(message.data)); }
    catch { if (selectedProjectId) notify("Не удалось обновить деталь", true); }
  });
}

stream.addEventListener("open", () => {
  activitiesByProject.clear();
  activityRevisionsByProject.clear();
  // Refresh authoritative state as a fallback even with bounded replay.
  pendingViewportTransition = null;
  activeSketchId = null;
  selectedObjectId = null;
  detailedProjectModifiedAt = null;
  scheduleWorkspaceRefresh(0);
  checkAutomaticUpdates();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    checkAutomaticUpdates();
  }
});
window.addEventListener("online", () => {
  checkAutomaticUpdates();
});
window.addEventListener("pageshow", checkAutomaticUpdates);
setInterval(checkAutomaticUpdates, 5000);
setInterval(updateWorkflowElapsed, 1000);
checkAutomaticUpdates();
if (selectedProjectId && !selectedProjectId.startsWith(FOLDER_SELECTION_PREFIX)) {
  showProjectLoading("Восстанавливаю последний проект…");
}
refreshWorkspace().catch((error) => {
  handleRefreshError(error);
});
