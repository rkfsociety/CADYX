const api = (path, options = {}) => fetch(path, {
  ...options,
  headers: { "Content-Type": "application/json", ...(options.headers || {}) },
}).then(async (response) => {
  const contentType = response.headers.get("content-type") || "";
  const value = contentType.includes("application/json") ? await response.json() : {};
  if (!response.ok) throw new Error(value.message || value.error || "Ошибка запроса");
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
let selectedFolderFileName = null;
let folderListRequest = 0;
let previewRequest = 0;
const FOLDER_SELECTION_PREFIX = "folder:";

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
  $("#folder-detail-name").textContent = name;
  $("#folder-detail-path").textContent = `projects/${name}`;
  if (folderChanged) {
    folderFiles = [];
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
  folderFiles = response.files;
  $("#folder-file-count").textContent = String(folderFiles.length);
  renderFolderFiles();
  const firstPreview = folderFiles.find((file) => file.name.toLowerCase().endsWith(".ply")) || folderFiles[0];
  if (firstPreview) previewFolderFile(name, firstPreview);
  else {
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

async function refreshDetail() {
  const projectId = selectedProjectId;
  if (!projectId) return;
  const request = ++detailRequest;
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

  if (type === "command.completed") {
    selectObjectFromResult(projectId, data.result);
    indicateModelChanged(projectId);
    scheduleWorkspaceRefresh();
  } else if (type === "command.failed") {
    if (projectId === selectedProjectId) notify("Не удалось обновить деталь", true);
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
const eventTypes = ["project.created", "project.lock.changed", "command.completed", "command.failed", "document.changed"];
for (const type of eventTypes) {
  stream.addEventListener(type, (message) => {
    try { handleStreamEvent(type, JSON.parse(message.data)); }
    catch { if (selectedProjectId) notify("Не удалось обновить деталь", true); }
  });
}

stream.addEventListener("open", scheduleWorkspaceRefresh);
refreshWorkspace().catch((error) => {
  notify(error.message, true);
});
