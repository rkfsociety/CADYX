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

const $ = (selector) => document.querySelector(selector);
const projectDetail = $("#project-detail");
const noSelection = $("#no-selection");
const folderFileList = $("#folder-file-list");
const folderPreviewCanvas = $("#ply-preview-canvas");
const folderPreviewPlaceholder = $("#folder-preview-placeholder");
const plyViewer = window.CadyxViewport.createViewer(folderPreviewCanvas);
const modelViewer = window.CadyxViewport.createViewer($("#model-preview"), {
  showViewCube: true, wireframe: true, onRender: queueViewportSnapshot,
});
const emptyViewportGeometry = {
  positions: new Float64Array(0), triangles: new Uint32Array(0), lines: [], points: [],
  bounds: { min: [0, 0, 0], max: [0, 0, 0] },
};
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
    notify("UUID проекта скопирован.");
  } catch {
    notify("Не удалось скопировать UUID проекта.", true);
  }
});
const numberFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
const integerFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const projectDateFormat = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });
let projects = [];
let projectSort = "name";
let projectGroups = [];
let folders = [];
let selectedProjectId = readSelectedProject();
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
let folderListRequest = 0;
let previewRequest = 0;

function queueViewportSnapshot() {
  window.dispatchEvent(new Event("cadyx:viewport-rendered"));
  if (!selectedProjectId || selectedProjectId.startsWith(FOLDER_SELECTION_PREFIX)
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
  const projectId = selectedProjectId;
  const revision = renderedSceneRevision;
  viewportSnapshotDirty = false;
  viewportSnapshotInFlight = true;
  try {
    const image = await modelViewer.createSnapshotBlob();
    if (projectId !== selectedProjectId || projectId !== renderedSceneProjectId
        || revision !== renderedSceneRevision) {
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
const AGENT_WORK_LOCK_KEY = "cadyx.agent-work-lock";
const savedAgentWorkLock = readSavedAgentWorkLock();
let agentWorkActive = savedAgentWorkLock.active;
let agentWorkRevision = savedAgentWorkLock.revision;
let agentWorkProjectIds = savedAgentWorkLock.projectIds;
let agentWorkStatusConnected = false;
const FOLDER_SELECTION_PREFIX = "folder:";
const initialAddress = new URLSearchParams(window.location.search);
let projectAddressPinned = (initialAddress.has("project_id") || initialAddress.has("folder"))
  && initialAddress.get("view") !== "projects";
const projectsChooserPinned = initialAddress.get("view") === "projects";
let workspaceSelectionRestoreAttempted = projectAddressPinned || initialAddress.get("view") === "projects";
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

function readSavedAgentWorkLock() {
  try {
    const saved = JSON.parse(localStorage.getItem(AGENT_WORK_LOCK_KEY) || "null");
    if (saved && typeof saved.active === "boolean" && Number.isInteger(saved.revision) && saved.revision >= 0) {
      return {
        ...saved,
        projectIds: Array.isArray(saved.projectIds) ? saved.projectIds.filter((id) => typeof id === "string") : [],
      };
    }
  } catch { /* A fresh lock state is safe before the API snapshot arrives. */ }
  return { active: false, revision: 0, projectIds: [] };
}

function applyAgentWorkLock(active, revision, projectIds = null) {
  if (typeof active !== "boolean" || !Number.isInteger(revision) || revision < agentWorkRevision) return;
  if (active) {
    agentWorkActive = true;
    agentWorkRevision = revision;
    if (Array.isArray(projectIds)) {
      agentWorkProjectIds = projectIds.filter((id) => typeof id === "string");
    } else if (!agentWorkProjectIds.length) {
      agentWorkProjectIds = [];
    }
  } else if (!agentWorkActive || revision > agentWorkRevision) {
    agentWorkActive = false;
    agentWorkRevision = revision;
    agentWorkProjectIds = [];
  }
  try {
    localStorage.setItem(AGENT_WORK_LOCK_KEY, JSON.stringify({
      active: agentWorkActive,
      revision: agentWorkRevision,
      projectIds: agentWorkProjectIds,
    }));
  } catch { /* The server snapshot remains authoritative when browser storage is unavailable. */ }
}

function dashboardHasUnfinishedWork(dashboard) {
  const workflow = dashboard?.workflow;
  return Boolean(workflow && (
    Number(workflow.progress_percent) < 100
    || workflow.phases.some((phase) => phase.state !== "completed")
  )) || (dashboard?.tasks || []).some((task) => task.state !== "completed");
}

function lockAvailabilityKey(lock) {
  return JSON.stringify([lock?.state || null, Boolean(lock?.recovery_required)]);
}

function workspaceControlLockReason() {
  if (!projectAddressPinned) return null;
  if (!agentWorkStatusConnected) return "ГЛОБАЛЬНОЕ СОСТОЯНИЕ РАБОТЫ НЕ ПОДТВЕРЖДЕНО · УПРАВЛЕНИЕ ЗАБЛОКИРОВАНО";
  if (agentWorkActive && (!selectedProjectId || !agentWorkProjectIds.length || agentWorkProjectIds.includes(selectedProjectId))) {
    return "ИИ-АГЕНТ РАБОТАЕТ С ЭТОЙ МОДЕЛЬЮ · УПРАВЛЕНИЕ ЗАБЛОКИРОВАНО";
  }
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
    button.disabled = (!projectsNavigation && !available) || filesUnavailable;
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
  } else if (view === "manufacturing") {
    $("#manufacturing-panel").focus({ preventScroll: true });
  } else if (view === "activity") {
    $("#workflow-panel").focus({ preventScroll: true });
  } else if (view === "files") {
    const projectId = selectedProjectId;
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
  return projectId || (folderName ? `folder:${folderName}` : null);
}

function notify(message, isError = false) {
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 3000);
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

function formatEta(seconds) {
  if (!Number.isInteger(seconds) || seconds < 0) return "ОСТАЛОСЬ —";
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    return `ОСТАЛОСЬ ${hours} ч ${minutes % 60} мин`;
  }
  return minutes ? `ОСТАЛОСЬ ${minutes} мин ${remainingSeconds} с` : `ОСТАЛОСЬ ${remainingSeconds} с`;
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
    $("#workflow-eta").textContent = formatEta(workflow.eta_seconds);
  } else {
    $("#workflow-name").textContent = "РАБОТА НЕ НАЧАТА";
    $("#workflow-percent").textContent = "—";
    $("#workflow-progress").dataset.available = "false";
    $("#workflow-progress").setAttribute("aria-valuenow", "0");
    $("#workflow-progress-bar").style.width = "0%";
    $("#workflow-eta").textContent = "ОСТАЛОСЬ —";
  }

  renderCurrentProgress(
    dashboard,
    activitiesByProject.get(selectedProjectId) || [],
    agentLocksByProject.get(selectedProjectId),
  );
  renderWorkflowHistory(activitiesByProject.get(selectedProjectId) || []);
  if (dashboardHasUnfinishedWork(dashboard)) {
    applyAgentWorkLock(true, agentWorkRevision);
    updateWorkspaceControls();
  }
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
  const isWorking = Boolean(runningActivity || agentLeaseActive || runningTask || activePhase);
  const state = isWorking ? "running"
    : activity?.state === "failed" ? "failed"
      : activity?.state === "completed" || completedWorkflowWithoutJournal ? "completed" : "idle";
  const action = runningActivity?.action || (agentLeaseActive ? "Выполняет команду" : activity?.action)
    || (completedWorkflowWithoutJournal ? workflow.title : "Действий пока нет");
  const shownTask = runningTask || activePhase || progressTask;

  $("#workflow-current").dataset.state = state;
  const agentState = $("#agent-state");
  agentState.dataset.state = state;
  agentState.textContent = state === "running" ? "В РАБОТЕ"
    : state === "failed" ? "ОШИБКА" : state === "completed" ? "ЗАВЕРШЕНО" : "ОЖИДАЕТ";
  $("#workflow-action-label").textContent = runningActivity || agentLeaseActive ? "ДЕЙСТВИЕ СЕЙЧАС"
    : activity?.state === "failed" ? "ПОСЛЕДНЕЕ ДЕЙСТВИЕ · ОШИБКА"
      : completedWorkflowWithoutJournal ? "ЗАВЕРШЁННЫЙ ОТЧЁТ" : "ПОСЛЕДНЕЕ ДЕЙСТВИЕ";
  const actionElement = $("#activity-title");
  actionElement.textContent = action;
  actionElement.title = action;

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

function canSelectProject(id) {
  const lock = agentLocksByProject.get(id) || getProject(id)?.lock;
  const agentIsActive = (activitiesByProject.get(id) || []).some((item) => item.state === "running");
  return lock?.state === "FREE" && !lock.recovery_required && !agentIsActive;
}

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
  card.dataset.active = String(active);
  card.setAttribute("role", "listitem");

  const state = document.createElement("span");
  state.className = "workspace-project-state";
  state.textContent = active ? "Текущий проект" : projectAvailable ? "Готов к открытию" : "Панель занята";

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
  const open = document.createElement("a");
  open.className = "button workspace-project-open";
  open.href = `/?project_id=${encodeURIComponent(project.id)}`;
  open.target = "_blank";
  open.rel = "noopener";
  open.setAttribute("aria-label", `Открыть проект «${project.name}» в новой вкладке`);
  open.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 7h6l2 2h9v10h-17V7Zm0 0V5h6l2 2"/></svg><span>Открыть проект</span>';

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
  actions.append(open, folderMenu);
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

function resetFileList(contextKey) {
  fileListContextKey = contextKey;
  folderFiles = [];
  folderFilesSignature = null;
  selectedFolderFileName = null;
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
  return selectedProjectId === context.id && primaryGrid.dataset.view === "files" && canOpenFilesView(context.id);
}

function showFolderLoadError(name, error) {
  showFileLoadError({ type: "folder", name }, error);
}

function showProjectFilesLoadError(projectId, error) {
  showFileLoadError({ type: "project", id: projectId }, error);
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
  return loadFiles({ type: "project", id: projectId });
}

async function loadFiles(context) {
  const contextKey = context.type === "folder" ? `folder:${context.name}` : `project:${context.id}`;
  if (fileListContextKey !== contextKey) resetFileList(contextKey);
  const request = ++folderListRequest;
  const response = await api(fileContextPath(context));
  if (request !== folderListRequest || !isCurrentFileContext(context)) return;
  const previousSelection = folderFiles.find((file) => file.name === selectedFolderFileName);
  const signature = JSON.stringify(response.files.map((file) => [file.name, file.size, file.modified_at_ns]));
  if (signature === folderFilesSignature) return;
  folderFilesSignature = signature;
  folderFiles = response.files;
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
  const firstPreview = folderFiles.find((file) => file.name.toLowerCase().endsWith(".ply")) || folderFiles[0];
  if (firstPreview) previewResourceFile(context, firstPreview);
  else {
    selectedFolderFileName = null;
    updateFolderInspector();
    $("#folder-preview-title").textContent = "Файлов нет";
    $("#folder-preview-info").textContent = "";
    folderPreviewPlaceholder.hidden = false;
    folderPreviewPlaceholder.textContent = context.type === "folder"
      ? "В этой папке пока нет файлов." : "В этом проекте пока нет файлов.";
  }
}

function renderFolderFiles() {
  folderFileList.replaceChildren();
  for (const file of folderFiles) {
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
  $("#folder-inspector-count").textContent = `ФАЙЛОВ: ${folderFiles.length}`;
  $("#folder-model-file-count").textContent = String(
    folderFiles.filter((item) => item.name.toLowerCase().endsWith(".ply")).length,
  );
  $("#folder-inspector-file-name").textContent = file?.name || "Не выбран";
  $("#folder-inspector-file-size").textContent = file ? formatFileSize(file.size) : "—";
  const extension = file?.name.match(/\.([^.]+)$/)?.[1];
  $("#folder-inspector-file-type").textContent = extension ? extension.toUpperCase() : "—";
}

async function previewFolderFile(folderName, file) {
  return previewResourceFile({ type: "folder", name: folderName }, file);
}

async function previewProjectFile(projectId, file) {
  return previewResourceFile({ type: "project", id: projectId }, file);
}

async function previewResourceFile(context, file) {
  if (!isCurrentFileContext(context)) return;
  selectedFolderFileName = file.name;
  updateFolderInspector(file);
  renderFolderFiles();
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
    const geometry = window.CadyxPlyPreview.parse(await response.arrayBuffer());
    if (request !== previewRequest || !isCurrentFileContext(context)) return;
    plyViewer.load(geometry, { smoothShading: Boolean(geometry.vertexNormals) });
    folderPreviewCanvas.hidden = false;
    folderPreviewPlaceholder.hidden = true;
    $("#folder-preview-info").textContent = geometry.faceCount
      ? `${formatNumber(geometry.vertexCount)} вершин · ${formatNumber(geometry.faceCount)} граней · в сцене ${formatNumber(geometry.previewFaceCount)}`
      : `${formatNumber(geometry.vertexCount)} точек · отображается ${formatNumber(geometry.previewVertexCount)}`;
  } catch (error) {
    if (request !== previewRequest || !isCurrentFileContext(context)) return;
    folderPreviewPlaceholder.hidden = false;
    folderPreviewPlaceholder.textContent = error.message;
    $("#folder-preview-info").textContent = "";
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
  if (new URLSearchParams(window.location.search).get("view") === "projects") {
    projectDetail.hidden = false;
    noSelection.hidden = true;
    setWorkspaceNavigation("projects");
  }
  const selectionChanged = selectedProjectId !== previousSelection;
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
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
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
    if (typeof current.agent_work_active === "boolean" && Number.isInteger(current.agent_work_revision)) {
      applyAgentWorkLock(current.agent_work_active, current.agent_work_revision, current.agent_work_active_project_ids);
      agentWorkStatusConnected = true;
    }
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
  const activity = await refreshActivitySnapshot(projectId);
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
    api(`/api/v1/projects/${encodedProjectId}/dashboard`),
  ]);
  if (projectId !== selectedProjectId || request !== detailRequest) return;
  if (scene.revision !== info.document.revision) { scheduleWorkspaceRefresh(); return; }
  agentLocksByProject.set(projectId, info.lock);
  renderAgentActivity(activitiesByProject.get(projectId) || [], info.lock);
  const dashboardRevision = Number.isInteger(dashboard.revision) ? dashboard.revision : 0;
  const knownDashboardRevision = dashboardRevisionsByProject.get(projectId);
  if (knownDashboardRevision === undefined || dashboardRevision >= knownDashboardRevision) {
    dashboardRevisionsByProject.set(projectId, dashboardRevision);
    dashboardsByProject.set(projectId, dashboard);
    renderAgentDashboard(dashboard);
  } else {
    renderAgentDashboard(dashboardsByProject.get(projectId));
  }
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
  if (transition?.featureId) {
    const feature = objectsById.get(transition.featureId);
    transition.bodyId = feature?.body_id;
  }
  if (renderedSceneKey !== key) {
    const positions = [], bodyPositions = [], triangles = [], lines = [], points = [], rotationAxes = [];
    const bodyLineRanges = [], bodyTriangleRanges = [], featureTriangleRanges = [], overlayTriangleRanges = [];
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
    const viewportGeometry = { positions, triangles, lines, points, bounds, rotationAxes,
      bodyLineRanges, bodyTriangleRanges, featureTriangleRanges, overlayTriangleRanges };
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
      await api(`/api/v1/projects/${encoded}/commands`, { method: "POST", body: JSON.stringify({ lease_id: leaseId, expected_revision: expectedRevision, command: { type: "set_parameter", feature_id: featureId, name: input.dataset.parameter, value: Number(input.value) } }) });
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
  if (type === "workspace.agent-work") {
    const data = event.data || {};
    applyAgentWorkLock(data.active, data.revision, data.project_ids);
    agentWorkStatusConnected = true;
    updateWorkspaceControls();
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
    if (projectId === selectedProjectId) renderAgentActivity(activities, agentLocksByProject.get(projectId));
    else if (primaryGrid.dataset.view === "projects") renderProjects();
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
    const historyEntry = data.history_entry || data;
    const command = historyEntry.command || data.command || {};
    const result = historyEntry.result || data.result || {};
    if (projectId === selectedProjectId) {
      if (command.type === "create_sketch") {
        activeSketchId = result.sketch_id || null;
        pendingViewportTransition = null;
      } else if (command.type === "add_sketch_element") {
        activeSketchId = command.sketch_id || activeSketchId;
        pendingViewportTransition = {
          projectId, objectId: result.object_id,
          type: command.element_type === "point" ? "sketch-point" : "sketch-lines",
          durationMs: command.element_type === "circle" ? 700 : 460,
        };
      } else if (["add_sketch_point", "add_sketch_construction_point"].includes(command.type)) {
        activeSketchId = command.sketch_id || activeSketchId;
        pendingViewportTransition = { projectId, objectId: result.point_id, type: "sketch-point", durationMs: 460 };
      } else if ([
        "add_sketch_segment", "add_sketch_line", "add_sketch_infinite_line", "add_sketch_centerline",
        "add_sketch_construction_line", "add_sketch_polyline", "add_sketch_line_chain", "add_sketch_rectangle",
        "add_sketch_rectangle_center", "add_sketch_rectangle_three_points", "add_sketch_rotated_rectangle",
        "add_sketch_parallelogram", "add_sketch_regular_polygon", "add_sketch_inscribed_polygon",
        "add_sketch_circumscribed_polygon", "add_sketch_polygon_from_center", "add_sketch_rounded_rectangle",
        "add_sketch_circle_diameter",
        "add_sketch_ellipse", "add_sketch_ellipse_center", "add_sketch_ellipse_three_points",
        "add_sketch_elliptical_arc",
        "add_sketch_spline",
        "add_sketch_spline_control_points",
        "add_sketch_bspline", "add_sketch_bezier_curve",
        "add_sketch_spiral", "add_sketch_parametric_curve", "add_sketch_coordinate_curve",
        "add_sketch_circle_two_points", "add_sketch_circle_three_points", "add_sketch_circle_tangent_to_line",
        "add_sketch_circle_tangent_to_two_lines", "add_sketch_circle_tangent_to_three_lines",
        "add_sketch_arc_center", "add_sketch_arc_three_points", "add_sketch_arc_start_center_end",
        "add_sketch_arc_radius", "add_sketch_arc_tangent", "add_sketch_involute_gear",
        "add_sketch_slot", "add_sketch_slot_by_centers", "add_sketch_slot_total_length",
        "add_sketch_slot_centered",
        "add_sketch_arc_slot_three_points", "add_sketch_arc_slot_center", "add_sketch_arc_slot_tangent",
        "mirror_sketch_elements", "pattern_sketch_elements",
      ].includes(command.type)) {
        activeSketchId = command.sketch_id || activeSketchId;
        const transitionSegments = (result.line_ids?.length || 0) + (result.circle_ids?.length || 0) * 16;
        pendingViewportTransition = activeSketchId ? {
          projectId, sketchId: activeSketchId, allSketchLines: true,
          type: "sketch-lines", durationMs: Math.min(1800, Math.max(460, 250 + transitionSegments * 20)),
        } : null;
      } else if (command.type === "add_sketch_circle") {
        activeSketchId = command.sketch_id || activeSketchId;
        pendingViewportTransition = {
          projectId, objectId: result.circle_id, type: "sketch-lines", durationMs: 700,
        };
      } else if (command.type === "add_sketch_constraint") {
        activeSketchId = command.sketch_id || activeSketchId;
        pendingViewportTransition = null;
      } else if (command.type === "update_sketch_elements") {
        const edited = command.updates?.[0]?.object_id;
        const source = currentObjects.find((item) => item.id === edited);
        activeSketchId = source?.sketch_id || activeSketchId;
        pendingViewportTransition = activeSketchId ? {
          projectId, sketchId: activeSketchId, allSketchLines: true,
          type: "sketch-lines", durationMs: 700,
        } : null;
      } else if (command.type === "replace_sketch_profile") {
        activeSketchId = command.sketch_id || null;
        pendingViewportTransition = activeSketchId ? {
          projectId, sketchId: activeSketchId, allSketchLines: true,
          type: "sketch-lines", durationMs: 1600,
        } : null;
      } else if (command.type === "replace_sketch_path") {
        activeSketchId = command.sketch_id || null;
        pendingViewportTransition = activeSketchId ? {
          projectId, sketchId: activeSketchId, allSketchLines: true,
          type: "sketch-lines", durationMs: 1800,
        } : null;
      } else if (command.type === "create_sketch_from_section") {
        const sketches = Array.isArray(result.sketches) ? result.sketches : [];
        const largest = sketches.reduce((selected, sketch) =>
          !selected || (sketch.sample_count || 0) > (selected.sample_count || 0) ? sketch : selected, null);
        activeSketchId = largest?.sketch_id || null;
        pendingViewportTransition = activeSketchId ? {
          projectId, sketchId: activeSketchId, allSketchLines: true,
          type: "sketch-lines", durationMs: 1600,
        } : null;
      } else if (command.type === "remove_sketch_object") {
        if (command.object_id === activeSketchId) activeSketchId = null;
        pendingViewportTransition = null;
      } else if (["extrude_sketch", "revolve_sketch"].includes(command.type)) {
        activeSketchId = null;
        pendingViewportTransition = {
          projectId, bodyId: result.body_id,
          type: command.type === "extrude_sketch" ? "extrude" : "revolve",
          plane: result.geometry?.plane || "XZ", durationMs: 1400,
        };
      } else if (command.type === "loft_sketches") {
        activeSketchId = null;
        pendingViewportTransition = {
          projectId, bodyId: result.body_id, type: "loft",
          sectionOffsets: result.geometry?.section_offsets || [], durationMs: 1800,
        };
      } else if (command.type === "create_ruled_shell") {
        activeSketchId = null;
        pendingViewportTransition = { projectId, bodyId: result.body_id, type: "brep", durationMs: 1500 };
      } else if (command.type === "remove_body") {
        activeSketchId = null;
        pendingViewportTransition = null;
        if (selectedObjectId === command.body_id) selectedObjectId = null;
      } else if (command.type === "create_metric_thread") {
        activeSketchId = null;
        pendingViewportTransition = { projectId, bodyId: result.body_id, type: "thread", durationMs: 1800 };
      } else if (command.type === "fuse_bodies") {
        activeSketchId = null;
        pendingViewportTransition = {
          projectId, bodyId: result.body_id, type: "fuse", durationMs: 1200,
        };
      } else if (command.type === "sweep_sketch") {
        activeSketchId = null;
        pendingViewportTransition = {
          projectId, bodyId: result.body_id, type: "sweep", plane: result.geometry?.plane || "XZ",
          path: result.geometry?.path || [], durationMs: 1800,
        };
      } else if (command.type === "fillet_edges") {
        activeSketchId = null;
        pendingViewportTransition = { projectId, bodyId: result.body_id, type: "fillet", durationMs: 1500 };
      } else if (["resize_imported_thread", "transform_imported_brep"].includes(command.type)) {
        activeSketchId = null;
        pendingViewportTransition = { projectId, bodyId: result.body_id, type: "brep", durationMs: 1500 };
      } else if (command.type === "set_parameter" && result.geometry?.kind) {
        const transitionType = result.geometry.kind === "revolved_profile" ? "revolve"
          : result.geometry.kind === "extruded_profile" ? "extrude"
          : result.geometry.kind === "swept_pipe" ? "sweep"
          : result.geometry.kind === "lofted_profiles" ? "loft"
          : result.geometry.kind === "metric_thread" ? "thread"
          : result.geometry.kind === "filleted_solid" ? "fillet"
          : ["fused_profiles", "fused_solids"].includes(result.geometry.kind) ? "fuse" : null;
        pendingViewportTransition = transitionType ? {
          projectId, featureId: result.feature_id, type: transitionType,
          plane: result.geometry.plane || "XZ", path: result.geometry.path || [], durationMs: 1400,
        } : null;
        activeSketchId = null;
      }
    }
    selectObjectFromResult(projectId, result);
    scheduleWorkspaceRefresh();
  } else if (type === "document.changed") {
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
  const button = event.target.closest("button[data-file-name]");
  const file = folderFiles.find((item) => item.name === button?.dataset.fileName);
  if (!button || !file) return;
  const folderName = getSelectedFolder();
  if (folderName) previewFolderFile(folderName, file);
  else if (primaryGrid.dataset.view === "files" && canOpenFilesView()) previewProjectFile(selectedProjectId, file);
});

const stream = new EventSource("/api/v1/events");
const eventTypes = ["project.created", "project.deleted", "workspace.selection.changed", "workspace.agent-work", "project.lock.changed", "agent.activity", "agent.dashboard", "command.completed", "document.changed", "viewport.camera"];
for (const type of eventTypes) {
  stream.addEventListener(type, (message) => {
    try { handleStreamEvent(type, JSON.parse(message.data)); }
    catch { if (selectedProjectId) notify("Не удалось обновить деталь", true); }
  });
}

stream.addEventListener("open", () => {
  activitiesByProject.clear();
  activityRevisionsByProject.clear();
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
checkAutomaticUpdates();
if (selectedProjectId && !selectedProjectId.startsWith(FOLDER_SELECTION_PREFIX)) {
  showProjectLoading("Восстанавливаю последний проект…");
}
refreshWorkspace().catch((error) => {
  handleRefreshError(error);
});
