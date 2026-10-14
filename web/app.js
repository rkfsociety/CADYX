const api = (path, options = {}) => fetch(path, {
  ...options,
  headers: { "Content-Type": "application/json", ...(options.headers || {}) },
}).then(async (response) => {
  const contentType = response.headers.get("content-type") || "";
  const value = contentType.includes("application/json") ? await response.json() : {};
  if (!response.ok) {
    const error = new Error(value.message || value.error || "Ошибка запроса");
    error.status = response.status;
    throw error;
  }
  return value;
});

const $ = (selector) => document.querySelector(selector);
const projectList = $("#project-list");
const projectDetail = $("#project-detail");
const noSelection = $("#no-selection");
const folderFileList = $("#folder-file-list");
const folderPreviewCanvas = $("#ply-preview-canvas");
const folderPreviewPlaceholder = $("#folder-preview-placeholder");
const plyViewer = window.CadyxViewport.createViewer(folderPreviewCanvas);
const modelViewer = window.CadyxViewport.createViewer($("#model-preview"));
let renderedSceneKey = null;
let detailRequest = 0;
$("#viewport-fit").addEventListener("click", () => modelViewer.fit());
const toast = $("#toast");
const numberFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
let projects = [];
let folders = [];
let selectedProjectId = readSelectedProject();
let selectedObjectId = null;
let currentObjects = [];
let toastTimer;
let refreshTimer;
let activeFolderName = null;
let folderFiles = [];
let folderFilesSignature = null;
let selectedFolderFileName = null;
let folderListRequest = 0;
let previewRequest = 0;
let automaticUpdateSnapshot = null;
let automaticUpdateInFlight = false;
let automaticUpdatesUnavailable = false;
const activitiesByProject = new Map();
const activityRevisionsByProject = new Map();
const activityPhaseLabels = {
  starting: "Подготовка операции",
  observe: "Чтение модели",
  validate_source: "Проверка файла",
  hash_source: "Фиксация источника",
  prepare_parameters: "Подготовка параметров",
  read_source: "Чтение скана",
  mesh_cleanup: "Очистка сетки",
  voxel_cleanup: "Упрощение облака",
  outlier_filter: "Удаление шума",
  estimate_normals: "Расчёт нормалей",
  reconstruct_surface: "Восстановление поверхности",
  simplify_mesh: "Сокращение полигонов",
  serialize_mesh: "Подготовка сетки",
  verify_source: "Проверка исходника",
  validate_result: "Контроль результата",
  write_output: "Запись PLY рядом",
  validate_document: "Проверка модели",
  save_document: "Сохранение проекта",
};
const meshActivityPhases = [
  "validate_source", "hash_source", "prepare_parameters", "read_source", "mesh_cleanup", "simplify_mesh",
  "serialize_mesh", "verify_source", "validate_result", "write_output", "validate_document", "save_document",
];
const cloudActivityPhases = [
  "validate_source", "hash_source", "prepare_parameters", "read_source", "voxel_cleanup", "outlier_filter",
  "estimate_normals", "reconstruct_surface", "simplify_mesh", "serialize_mesh",
  "verify_source", "validate_result", "write_output", "validate_document", "save_document",
];
const genericActivityPhases = ["starting", "validate_document", "save_document"];
const observationActivityPhases = ["starting", "observe"];
const genericActivityLabels = {
  starting: "Выполнение операции",
  validate_document: "Проверка модели",
  save_document: "Сохранение проекта",
};
const observationActivityLabels = {
  starting: "Подготовка чтения",
  observe: "Чтение модели",
};
const FOLDER_SELECTION_PREFIX = "folder:";
const dockMenus = [...document.querySelectorAll(".dock-control")];
dockMenus.forEach((menu) => menu.addEventListener("toggle", () => {
  if (menu.open) dockMenus.forEach((other) => { if (other !== menu) other.open = false; });
}));

function readSelectedProject() {
  try { return localStorage.getItem("cadyx.selected-project"); }
  catch { return null; }
}

function saveSelectedProject(id) {
  try {
    if (id) localStorage.setItem("cadyx.selected-project", id);
    else localStorage.removeItem("cadyx.selected-project");
  } catch { /* Selection still works when browser storage is unavailable. */ }
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

function getProject(id) {
  return projects.find((item) => item.project.id === id);
}

function folderSelectionId(name) {
  return `${FOLDER_SELECTION_PREFIX}${name}`;
}

function getSelectedFolder() {
  if (!selectedProjectId?.startsWith(FOLDER_SELECTION_PREFIX)) return null;
  const name = selectedProjectId.slice(FOLDER_SELECTION_PREFIX.length);
  return folders.includes(name) ? name : null;
}

function indicateModelChanged(projectId) {
  if (projectId !== selectedProjectId) return;
  const preview = $("#model-preview");
  preview.classList.remove("changed");
  requestAnimationFrame(() => preview.classList.add("changed"));
}

function renderProjectList() {
  const entries = [
    ...projects.map((item) => ({ kind: "project", name: item.project.name, item })),
    ...folders.map((name) => ({ kind: "folder", name })),
  ].sort((left, right) => left.name.localeCompare(right.name, "ru", { sensitivity: "base" }));
  $("#empty-projects").hidden = entries.length > 0;
  projectList.replaceChildren();

  for (const entry of entries) {
    if (entry.kind === "folder") {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "project-item project-folder";
      row.classList.toggle("selected", selectedProjectId === folderSelectionId(entry.name));
      row.dataset.folderName = entry.name;
      row.setAttribute("aria-current", selectedProjectId === folderSelectionId(entry.name) ? "page" : "false");
      const marker = document.createElement("span");
      marker.className = "project-marker";
      marker.setAttribute("aria-hidden", "true");
      const copy = document.createElement("span");
      copy.className = "project-copy";
      const name = document.createElement("strong");
      name.className = "project-name";
      name.textContent = entry.name;
      copy.append(name);
      const arrow = document.createElement("span");
      arrow.className = "project-arrow";
      arrow.setAttribute("aria-hidden", "true");
      arrow.textContent = "›";
      row.append(marker, copy, arrow);
      projectList.append(row);
      continue;
    }

    const item = entry.item;
    const project = item.project;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `project-item${project.id === selectedProjectId ? " selected" : ""}`;
    button.dataset.projectId = project.id;
    button.setAttribute("aria-current", project.id === selectedProjectId ? "page" : "false");

    const marker = document.createElement("span");
    marker.className = "project-marker";
    marker.setAttribute("aria-hidden", "true");

    const copy = document.createElement("span");
    copy.className = "project-copy";
    const name = document.createElement("strong");
    name.className = "project-name";
    name.textContent = project.name;
    copy.append(name);

    const arrow = document.createElement("span");
    arrow.className = "project-arrow";
    arrow.setAttribute("aria-hidden", "true");
    arrow.textContent = "›";
    button.append(marker, copy, arrow);
    projectList.append(button);
  }
}

function selectProject(id) {
  selectedProjectId = id;
  selectedObjectId = null;
  saveSelectedProject(id);
  renderProjectList();
  const folder = getSelectedFolder();
  if (folder) {
    showFolderDetail(folder);
    return;
  }
  refreshDetail().catch((error) => notify(error.message, true));
}

function showFolderDetail(name) {
  const folderChanged = activeFolderName !== name;
  activeFolderName = name;
  projectDetail.hidden = true;
  $("#object-panel").hidden = true;
  noSelection.hidden = true;
  $("#folder-detail").hidden = false;
  $("#document-title").textContent = name;
  if (folderChanged) {
    folderFiles = [];
    folderFilesSignature = null;
    selectedFolderFileName = null;
    folderFileList.replaceChildren();
    $("#folder-file-count").textContent = "0";
    folderPreviewCanvas.hidden = true;
    folderPreviewPlaceholder.hidden = false;
    folderPreviewPlaceholder.textContent = "Загружаю список файлов…";
    $("#folder-preview-title").textContent = "Предпросмотр";
    $("#folder-preview-info").textContent = "";
    loadFolderFiles(name).catch((error) => showFolderLoadError(name, error));
  }
}

function formatFileSize(size) {
  if (size < 1024) return `${size} Б`;
  if (size < 1024 * 1024) return `${numberFormat.format(size / 1024)} КБ`;
  return `${numberFormat.format(size / (1024 * 1024))} МБ`;
}

function showFolderLoadError(name, error) {
  if (activeFolderName !== name) return;
  folderFileList.replaceChildren();
  folderPreviewPlaceholder.hidden = false;
  folderPreviewPlaceholder.textContent = error.message;
  $("#folder-preview-title").textContent = "Не удалось открыть папку";
  $("#folder-preview-info").textContent = "";
}

async function loadFolderFiles(name) {
  const request = ++folderListRequest;
  const response = await api(`/api/v1/workspace/folders/${encodeURIComponent(name)}/files`);
  if (request !== folderListRequest || activeFolderName !== name) return;
  const previousSelection = folderFiles.find((file) => file.name === selectedFolderFileName);
  const signature = JSON.stringify(response.files.map((file) => [file.name, file.size, file.modified_at_ns]));
  if (signature === folderFilesSignature) return;
  folderFilesSignature = signature;
  folderFiles = response.files;
  $("#folder-file-count").textContent = String(folderFiles.length);
  renderFolderFiles();
  const selected = folderFiles.find((file) => file.name === selectedFolderFileName);
  if (selected) {
    if (!previousSelection || previousSelection.size !== selected.size ||
        previousSelection.modified_at_ns !== selected.modified_at_ns) {
      previewFolderFile(name, selected);
    }
    return;
  }
  const firstPreview = folderFiles.find((file) => file.name.toLowerCase().endsWith(".ply")) || folderFiles[0];
  if (firstPreview) previewFolderFile(name, firstPreview);
  else {
    selectedFolderFileName = null;
    $("#folder-preview-title").textContent = "Файлов нет";
    $("#folder-preview-info").textContent = "";
    folderPreviewPlaceholder.hidden = false;
    folderPreviewPlaceholder.textContent = "В этой папке пока нет файлов.";
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

async function previewFolderFile(folderName, file) {
  selectedFolderFileName = file.name;
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

  folderPreviewPlaceholder.textContent = "Загружаю 3D модель…";
  try {
    const url = `/api/v1/workspace/folders/${encodeURIComponent(folderName)}/files/${encodeURIComponent(file.name)}`;
    const response = await fetch(url);
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.message || "Не удалось загрузить файл.");
    }
    const geometry = window.CadyxPlyPreview.parse(await response.arrayBuffer());
    if (request !== previewRequest || activeFolderName !== folderName) return;
    plyViewer.load(geometry);
    folderPreviewCanvas.hidden = false;
    folderPreviewPlaceholder.hidden = true;
    $("#folder-preview-info").textContent = geometry.faceCount
      ? `${formatNumber(geometry.vertexCount)} вершин · ${formatNumber(geometry.faceCount)} граней · в сцене ${formatNumber(geometry.previewFaceCount)}`
      : `${formatNumber(geometry.vertexCount)} точек · отображается до 24 000`;
  } catch (error) {
    if (request !== previewRequest || activeFolderName !== folderName) return;
    folderPreviewPlaceholder.hidden = false;
    folderPreviewPlaceholder.textContent = error.message;
    $("#folder-preview-info").textContent = "";
  }
}

async function refreshWorkspace() {
  const response = await api("/api/v1/projects");
  projects = response.projects;
  folders = response.folders || [];

  const selectionExists = projects.some((item) => item.project.id === selectedProjectId)
    || folders.includes(selectedProjectId?.startsWith(FOLDER_SELECTION_PREFIX)
      ? selectedProjectId.slice(FOLDER_SELECTION_PREFIX.length)
      : "");
  if (!selectionExists) {
    const saved = readSelectedProject();
    const savedFolder = saved?.startsWith(FOLDER_SELECTION_PREFIX)
      ? saved.slice(FOLDER_SELECTION_PREFIX.length)
      : null;
    selectedProjectId = projects.some((item) => item.project.id === saved)
      ? saved
      : savedFolder && folders.includes(savedFolder)
        ? saved
        : projects[0]?.project.id || (folders[0] ? folderSelectionId(folders[0]) : null);
    selectedObjectId = null;
    saveSelectedProject(selectedProjectId);
  }

  renderProjectList();
  const selectedFolder = getSelectedFolder();
  if (selectedFolder) showFolderDetail(selectedFolder);
  else if (selectedProjectId && getProject(selectedProjectId)) await refreshDetail();
  else {
    projectDetail.hidden = true;
    $("#object-panel").hidden = true;
    $("#folder-detail").hidden = true;
    noSelection.hidden = false;
    $("#document-title").textContent = "CADYX";
  }
}

function scheduleWorkspaceRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refreshWorkspace().catch((error) => {
      notify(error.message, true);
    });
  }, 60);
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
    if (selectionAtRequest !== selectedProjectId) return;
    const previous = automaticUpdateSnapshot;
    automaticUpdateSnapshot = current;
    if (automaticUpdatesUnavailable) {
      automaticUpdatesUnavailable = false;
      notify("Связь восстановлена, синхронизирую панель.");
      scheduleWorkspaceRefresh();
    }
    if (!previous) return;
    if (current.activity_revision !== null && previous.activity_revision !== null &&
        current.activity_revision < previous.activity_revision && selectedProjectId) {
      activitiesByProject.delete(selectedProjectId);
      activityRevisionsByProject.delete(selectedProjectId);
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
    if (workspaceChanged || projectChanged) {
      scheduleWorkspaceRefresh();
    } else if (activityChanged && selectedProjectId && !folder) {
      refreshActivitySnapshot(selectedProjectId)
        .catch((error) => notify(`Не удалось обновить состояние агента: ${error.message}`, true));
    }
  } catch (error) {
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
    return;
  }
  const info = await api(`/api/v1/projects/${encodeURIComponent(projectId)}`);
  const scene = await api(`/api/v1/projects/${encodeURIComponent(projectId)}/viewport`);
  if (projectId !== selectedProjectId || request !== detailRequest) return;
  if (scene.revision !== info.document.revision) { scheduleWorkspaceRefresh(); return; }
  const key = `${projectId}:${scene.revision}`;
  if (renderedSceneKey !== key) {
    const positions = [], triangles = [], lines = [], points = [];
    for (const object of scene.objects) {
      const offset = positions.length / 3;
      for (const value of object.positions) positions.push(value);
      for (const index of object.triangles) triangles.push(index + offset);
      for (const index of object.lines) lines.push(index + offset);
      for (const index of object.points) points.push(index + offset);
    }
    if (scene.bounds) modelViewer.load({ positions, triangles, lines, points, bounds: scene.bounds },
      { reset: !renderedSceneKey?.startsWith(`${projectId}:`) });
    renderedSceneKey = key;
  }
  $("#viewport-empty").hidden = scene.objects.length > 0;
  $("#model-preview").hidden = scene.objects.length === 0;
  modelViewer.render();

  $("#folder-detail").hidden = true;
  $("#object-panel").hidden = false;
  projectDetail.hidden = false;
  noSelection.hidden = true;
  $("#document-title").textContent = info.project.name;
  const objects = info.document.objects;
  if (selectedObjectId && !objects.some((item) => item.id === selectedObjectId)) selectedObjectId = null;
  if (!selectedObjectId) {
    const topLevel = objects.filter((item) => item.type === "body" || item.type === "sketch");
    selectedObjectId = topLevel.at(-1)?.id || null;
  }
  currentObjects = objects;
  renderObjectTree(objects);
  renderProperties(objects);
}

async function refreshActivitySnapshot(projectId) {
  const response = await api(`/api/v1/projects/${encodeURIComponent(projectId)}/activity`);
  if (projectId !== selectedProjectId) return null;
  const revision = Number.isInteger(response.revision) ? response.revision : 0;
  const knownRevision = activityRevisionsByProject.get(projectId);
  if (knownRevision !== undefined && revision < knownRevision) {
    const cached = activitiesByProject.get(projectId) || [];
    return cached.slice().reverse().find((item) => item.state === "running") || cached.at(-1) || null;
  }
  const activities = response.activities || [];
  activityRevisionsByProject.set(projectId, revision);
  activitiesByProject.set(projectId, activities);
  renderAgentActivity(activities);
  const current = activities.slice().reverse().find((item) => item.state === "running") || activities.at(-1) || null;
  if (current?.state === "running") {
    $("#folder-detail").hidden = true;
    projectDetail.hidden = false;
    noSelection.hidden = true;
    $("#document-title").textContent = getProject(projectId)?.project.name || "CADYX · работа агента";
  }
  return current;
}

function renderAgentActivity(value) {
  const activities = Array.isArray(value) ? value : value ? [value] : [];
  const currentActivity = activities.slice().reverse().find((item) => item.state === "running") || activities.at(-1) || null;
  const historyWrap = $("#activity-history-wrap");
  const historyList = $("#activity-history");
  historyList.replaceChildren();
  historyWrap.hidden = activities.length === 0;
  for (const item of activities.slice().reverse()) {
    const row = document.createElement("li");
    row.className = "activity-history-item";
    row.dataset.state = item.state || "running";
    const dot = document.createElement("span");
    dot.className = "activity-history-dot";
    dot.setAttribute("aria-hidden", "true");
    const copy = document.createElement("span");
    copy.className = "activity-history-copy";
    const action = document.createElement("strong");
    action.textContent = item.action || "Операция агента";
    const file = item.source?.file;
    const lastPhase = item.steps?.at(-1)?.phase;
    const detail = document.createElement("small");
    detail.textContent = file
      ? `${file} → ${item.result?.artifact?.file || "сетка CADYX"}`
      : activityPhaseLabels[lastPhase] || "Модель CADYX";
    detail.title = detail.textContent;
    const time = document.createElement("time");
    time.className = "activity-history-time";
    const updatedAt = Date.parse(item.updated_at || "");
    time.textContent = Number.isFinite(updatedAt)
      ? new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(updatedAt)
      : "";
    const phases = document.createElement("span");
    phases.className = "activity-history-phases";
    phases.setAttribute("aria-label", `${item.steps?.length || 0} этапов`);
    for (const step of item.steps || []) {
      const marker = document.createElement("span");
      marker.className = "activity-history-phase";
      marker.dataset.state = step.state;
      marker.title = activityPhaseLabels[step.phase] || "Операция";
      phases.append(marker);
    }
    row.title = (item.steps || []).map((step) => activityPhaseLabels[step.phase] || step.phase).join(" · ");
    copy.append(action, detail);
    row.append(dot, copy, time, phases);
    historyList.append(row);
  }

  const activity = currentActivity;
  const panel = $("#agent-activity");
  const title = $("#activity-title");
  const stateBadge = $("#activity-state");
  const route = $("#activity-route");
  const result = $("#activity-result");
  const parameters = $("#activity-parameters");
  const list = $("#activity-steps");
  const state = activity?.state || "idle";
  panel.dataset.state = state;
  list.replaceChildren();
  parameters.replaceChildren();
  result.hidden = true;
  result.classList.remove("failed");

  if (!activity) {
    title.textContent = "Ожидание операции";
    stateBadge.textContent = "Ожидание";
    route.textContent = "CADYX готов к построению модели.";
    return;
  }

  const scanOperation = ["import_scan", "set_scan_parameter"].includes(activity.command);
  const phaseIds = scanOperation
    ? activity.source_kind === "triangle_mesh" ? meshActivityPhases
      : activity.source_kind === "point_cloud" ? cloudActivityPhases
        : ["validate_source", "hash_source", "prepare_parameters", "read_source", "simplify_mesh", "serialize_mesh", "verify_source", "validate_result", "write_output", "validate_document", "save_document"]
    : activity.kind === "observation" ? observationActivityPhases : genericActivityPhases;
  const labels = scanOperation
    ? activityPhaseLabels
    : activity.kind === "observation" ? observationActivityLabels
      : { ...genericActivityLabels, starting: activity.action || "Изменение модели" };
  const phase = activity.phase || "starting";
  const phaseIndex = phaseIds.indexOf(phase);
  const currentLabel = labels[phase] || activity.action || "Операция";
  title.textContent = state === "completed" ? `${activity.action} · готово`
    : state === "failed" ? `${activity.action} · ошибка`
      : `${activity.action} · ${currentLabel.toLocaleLowerCase("ru")}`;
  stateBadge.textContent = state === "running" ? "В работе"
    : state === "completed" ? "Завершено"
      : state === "failed" ? "Ошибка"
        : "Ожидание";

  const source = activity.source;
  const artifact = activity.result?.artifact;
  if (source) {
    const sourcePath = `projects/${source.folder}/${source.file}`;
    const outputPath = artifact ? `projects/${artifact.folder}/${artifact.file}` : "редактируемая сетка CADYX";
    route.textContent = `${sourcePath}  →  ${outputPath}`;
    route.title = `${sourcePath}  →  ${outputPath}`;
  } else {
    route.textContent = "Изменения модели проходят через Core и сохраняются в проекте.";
    route.title = route.textContent;
  }

  for (let index = 0; index < phaseIds.length; index++) {
    const phaseId = phaseIds[index];
    const item = document.createElement("li");
    item.className = "activity-step";
    let status = "pending";
    if (state === "completed" || (phaseIndex >= 0 && index < phaseIndex)) status = "complete";
    if (state === "running" && phaseId === phase) status = "active";
    if (state === "failed" && phaseId === phase) status = "failed";
    item.dataset.status = status;
    const node = document.createElement("span");
    node.className = "activity-step-node";
    node.setAttribute("aria-hidden", "true");
    node.textContent = status === "complete" ? "✓" : status === "active" ? "•" : status === "failed" ? "!" : "";
    const label = document.createElement("span");
    label.className = "activity-step-label";
    label.textContent = labels[phaseId] || "Операция";
    item.title = label.textContent;
    item.append(node, label);
    list.append(item);
  }

  if (scanOperation && activity.parameters) {
    const parameterLabels = {
      voxel_size: "Воксель",
      target_triangles: "Треугольники",
      outlier_neighbors: "Соседи",
      outlier_std_ratio: "Порог шума",
      poisson_depth: "Глубина Poisson",
    };
    for (const [key, value] of Object.entries(activity.parameters)) {
      if (activity.source_kind === "triangle_mesh" && ["outlier_neighbors", "outlier_std_ratio", "poisson_depth"].includes(key)) continue;
      const chip = document.createElement("span");
      chip.className = "activity-parameter";
      chip.textContent = `${parameterLabels[key] || key}: ${formatNumber(value)}`;
      parameters.append(chip);
    }
  }

  const stats = activity.result;
  if (state === "failed" && activity.error) {
    result.textContent = activity.error;
    result.title = activity.error;
    result.classList.add("failed");
    result.hidden = false;
  } else if (stats && (stats.output_triangles || artifact)) {
    const meshStats = stats.output_triangles
      ? `${formatNumber(stats.output_vertices)} вершин · ${formatNumber(stats.output_triangles)} треугольников`
      : "Сетка сохранена";
    result.textContent = artifact
      ? `${meshStats} · ${formatFileSize(artifact.size)} · ${artifact.created ? "файл создан" : "файл уже существует"}`
      : meshStats;
    result.title = result.textContent;
    result.hidden = false;
  }
}

function objectTypeLabel(item) {
  return ({ body: "Тело", feature: "Операция", sketch: "Эскиз", point: "Точка", line: "Отрезок", circle: "Окружность", constraint: "Ограничение" })[item?.type] || "Объект";
}

function constraintLabel(kind) {
  return ({ horizontal: "Горизонтальность", vertical: "Вертикальность", coincident: "Совпадение", distance: "Расстояние", radius: "Радиус" })[kind] || kind || "Ограничение";
}

function objectSummary(item, objects = currentObjects) {
  if (item.type === "body") return dimensionsText(item.geometry?.dimensions);
  if (item.type === "feature") return `${item.operation || "Операция"} · ${dimensionsText(item.parameters)}`;
  if (item.type === "sketch") return `${item.plane} · ${objects.filter((child) => child.sketch_id === item.id).length} объектов`;
  if (item.type === "point") return `(${formatNumber(item.x)}, ${formatNumber(item.y)})`;
  if (item.type === "line") {
    const start = objects.find((child) => child.id === item.start_id);
    const end = objects.find((child) => child.id === item.end_id);
    const pointLabel = (point) => point ? `(${formatNumber(point.x)}, ${formatNumber(point.y)})` : "?";
    return `${pointLabel(start)} → ${pointLabel(end)}`;
  }
  if (item.type === "circle") return `R ${formatNumber(item.radius)}`;
  if (item.type === "constraint") return constraintLabel(item.kind);
  return "";
}

function renderObjectTree(objects) {
  const root = $("#object-tree");
  root.replaceChildren();
  $("#objects-empty").hidden = objects.length > 0;

  const appendRow = (parent, item, nested = false) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `object-row${nested ? " nested" : ""}${item.id === selectedObjectId ? " selected" : ""}`;
    button.dataset.objectId = item.id;
    button.title = item.id;
    button.setAttribute("aria-pressed", String(item.id === selectedObjectId));
    const marker = document.createElement("span");
    marker.className = "object-symbol";
    marker.setAttribute("aria-hidden", "true");
    marker.textContent = ({ body: "▰", feature: "↳", sketch: "⌗", point: "·", line: "／", circle: "○", constraint: "⊙" })[item.type] || "•";
    const copy = document.createElement("span");
    copy.className = "object-copy";
    const name = document.createElement("strong");
    name.textContent = item.name || (item.type === "feature" ? "Построение коробки" : objectTypeLabel(item));
    const detail = document.createElement("small");
    detail.textContent = objectSummary(item, objects);
    copy.append(name, detail);
    button.append(marker, copy);
    button.addEventListener("click", () => {
      selectedObjectId = item.id;
      renderViewport(currentObjects);
      renderObjectTree(currentObjects);
      renderProperties(currentObjects);
    });
    parent.append(button);
  };

  const bodies = objects.filter((item) => item.type === "body");
  const sketches = objects.filter((item) => item.type === "sketch");
  for (const body of bodies) {
    const group = document.createElement("div");
    group.className = "object-group";
    appendRow(group, body);
    const children = objects.filter((item) => item.type === "feature" && item.body_id === body.id);
    if (children.length) {
      const nested = document.createElement("div");
      nested.className = "object-children";
      for (const child of children) appendRow(nested, child, true);
      group.append(nested);
    }
    root.append(group);
  }
  for (const sketch of sketches) {
    const group = document.createElement("div");
    group.className = "object-group";
    appendRow(group, sketch);
    const children = objects.filter((item) => item.sketch_id === sketch.id);
    if (children.length) {
      const nested = document.createElement("div");
      nested.className = "object-children";
      for (const child of children) appendRow(nested, child, true);
      group.append(nested);
    }
    root.append(group);
  }
}

function dimensionsText(dimensions) {
  if (!dimensions) return "";
  return `${formatNumber(dimensions.width)} × ${formatNumber(dimensions.height)} × ${formatNumber(dimensions.depth)}`;
}

function referenceText(id, objects) {
  const target = objects.find((item) => item.id === id);
  if (!target) return "Объект не найден";
  if (target.name) return target.name;
  const summary = objectSummary(target, objects);
  return summary ? `${objectTypeLabel(target)} · ${summary}` : objectTypeLabel(target);
}

function propertyRows(item, objects) {
  if (item.type === "body") return [
    ["Имя", item.name || "Тело"], ["Геометрия", item.geometry?.kind || "—"],
    ["Ширина", formatNumber(item.geometry?.dimensions?.width)],
    ["Высота", formatNumber(item.geometry?.dimensions?.height)],
    ["Глубина", formatNumber(item.geometry?.dimensions?.depth)],
    ["Объём", formatNumber(item.geometry?.volume)], ["Операций", String(item.feature_ids?.length || 0)],
  ];
  if (item.type === "feature") return [
    ["Тип", item.operation], ["Тело", referenceText(item.body_id, objects)],
    ...Object.entries(item.parameters || {}).map(([key, value]) => [key, formatNumber(value)]),
  ];
  if (item.type === "sketch") return [
    ["Имя", item.name], ["Плоскость", item.plane],
    ["Объектов", String(objects.filter((child) => child.sketch_id === item.id).length)],
  ];
  if (item.type === "point") return [
    ["Эскиз", referenceText(item.sketch_id, objects)], ["X", formatNumber(item.x)], ["Y", formatNumber(item.y)],
  ];
  if (item.type === "line") return [
    ["Эскиз", referenceText(item.sketch_id, objects)], ["Начало", referenceText(item.start_id, objects)],
    ["Конец", referenceText(item.end_id, objects)],
  ];
  if (item.type === "circle") return [
    ["Эскиз", referenceText(item.sketch_id, objects)], ["Центр", referenceText(item.center_id, objects)],
    ["Радиус", formatNumber(item.radius)],
  ];
  return [
    ["Эскиз", referenceText(item.sketch_id, objects)], ["Тип", constraintLabel(item.kind)],
    ["Ссылки", (item.references || []).map((id) => referenceText(id, objects)).join("; ") || "—"],
    ...(item.value === undefined ? [] : [["Значение", formatNumber(item.value)]]),
  ];
}

function renderProperties(objects) {
  const item = objects.find((object) => object.id === selectedObjectId);
  if (!item) {
    $("#properties-empty").hidden = false;
    $("#properties-list").hidden = true;
    return;
  }
  $("#properties-empty").hidden = true;
  $("#properties-list").hidden = false;
  const list = $("#properties-list");
  list.replaceChildren();
  for (const [name, value] of propertyRows(item, objects)) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = name;
    const description = document.createElement("dd");
    description.textContent = String(value ?? "—");
    description.title = String(value ?? "");
    row.append(term, description);
    list.append(row);
  }
}

function selectObjectFromResult(projectId, result) {
  const objectId = result?.body_id || result?.sketch_id || result?.object_id || result?.constraint_id || result?.feature_id;
  if (projectId === selectedProjectId && objectId) selectedObjectId = objectId;
}

function handleStreamEvent(type, event) {
  const projectId = event.project_id;
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
    if (projectId === selectedProjectId) renderAgentActivity(activities);
    if (projectId === selectedProjectId && data.state === "running") {
      $("#folder-detail").hidden = true;
      projectDetail.hidden = false;
      noSelection.hidden = true;
      $("#document-title").textContent = getProject(projectId)?.project.name || "CADYX · работа агента";
    }
    if (data.state === "completed") indicateModelChanged(projectId);
  } else if (type === "command.completed") {
    selectObjectFromResult(projectId, data.result || data.history_entry?.result);
    indicateModelChanged(projectId);
    scheduleWorkspaceRefresh();
  } else if (type === "document.changed") {
    scheduleWorkspaceRefresh();
  } else if (type === "project.created") {
    selectedProjectId = projectId;
    selectedObjectId = null;
    saveSelectedProject(projectId);
    scheduleWorkspaceRefresh();
  } else if (type === "project.lock.changed") {
    scheduleWorkspaceRefresh();
  }
}

projectList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-project-id]");
  if (button) {
    selectProject(button.dataset.projectId);
    return;
  }
  const folder = event.target.closest("button[data-folder-name]");
  if (folder) selectProject(folderSelectionId(folder.dataset.folderName));
});

folderFileList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-file-name]");
  const file = folderFiles.find((item) => item.name === button?.dataset.fileName);
  if (button && file && activeFolderName) previewFolderFile(activeFolderName, file);
});

const stream = new EventSource("/api/v1/events");
const eventTypes = ["project.created", "project.lock.changed", "agent.activity", "command.completed", "document.changed"];
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
  scheduleWorkspaceRefresh();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    checkAutomaticUpdates();
    scheduleWorkspaceRefresh();
  }
});
window.addEventListener("online", () => {
  checkAutomaticUpdates();
  scheduleWorkspaceRefresh();
});
window.addEventListener("pageshow", checkAutomaticUpdates);
setInterval(checkAutomaticUpdates, 5000);
checkAutomaticUpdates();
refreshWorkspace().catch((error) => {
  notify(error.message, true);
});
