const api = (path, options = {}) => fetch(path, {
  ...options,
  headers: { "Content-Type": "application/json", ...(options.headers || {}) },
}).then(async (response) => {
  const contentType = response.headers.get("content-type") || "";
  const value = contentType.includes("application/json") ? await response.json() : {};
  if (!response.ok) {
    const error = new Error(value.message || value.error || "Ошибка запроса");
    error.status = response.status;
    error.code = value.error;
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
let cameraAnimationId = 0;
let renderedSceneKey = null;
let detailRequest = 0;
let detailedProjectId = null;
let detailedProjectModifiedAt = null;
$("#viewport-fit").addEventListener("click", () => modelViewer.fit());
const toast = $("#toast");
const numberFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
let projects = [];
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
let selectedFolderFileName = null;
let folderListRequest = 0;
let previewRequest = 0;
let automaticUpdateSnapshot = null;
let automaticUpdateInFlight = false;
let automaticUpdatesUnavailable = false;
let activityStatusTimer = 0;
let activityStatusOperationId = null;
const activitiesByProject = new Map();
const activityRevisionsByProject = new Map();
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

function showProjectLoading(name) {
  activeFolderName = null;
  $("#folder-detail").hidden = true;
  projectDetail.hidden = false;
  noSelection.hidden = true;
  $("#object-panel").hidden = true;
  $("#document-title").textContent = name;
  if (!renderedSceneKey?.startsWith(`${selectedProjectId}:`)) {
    $("#model-preview").hidden = true;
    $("#viewport-empty").hidden = false;
    $("#viewport-empty").querySelector("p").textContent = "Загружаю последнюю сохранённую модель…";
  }
}

function folderSelectionId(name) {
  return `${FOLDER_SELECTION_PREFIX}${name}`;
}

function getSelectedFolder() {
  if (!selectedProjectId?.startsWith(FOLDER_SELECTION_PREFIX)) return null;
  const name = selectedProjectId.slice(FOLDER_SELECTION_PREFIX.length);
  return folders.includes(name) ? name : null;
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
  activeSketchId = null;
  pendingViewportTransition = null;
  saveSelectedProject(id);
  renderProjectList();
  const folder = getSelectedFolder();
  if (folder) {
    showFolderDetail(folder);
    return;
  }
  showProjectLoading(getProject(id)?.project.name || "Загружаю проект…");
  refreshDetail().catch(handleRefreshError);
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
  else if (selectedProjectId && getProject(selectedProjectId)) {
    const selected = getProject(selectedProjectId).project;
    if (!renderedSceneKey?.startsWith(`${selectedProjectId}:`)) showProjectLoading(selected.name);
    const modifiedAt = selected.modified_at;
    if (detailedProjectId !== selectedProjectId || detailedProjectModifiedAt !== modifiedAt || !renderedSceneKey) {
      await refreshDetail();
    }
  }
  else {
    projectDetail.hidden = true;
    $("#object-panel").hidden = true;
    $("#folder-detail").hidden = true;
    noSelection.hidden = false;
    $("#document-title").textContent = "CADYX";
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
  }
  const info = await api(`/api/v1/projects/${encodeURIComponent(projectId)}`);
  const scene = await api(`/api/v1/projects/${encodeURIComponent(projectId)}/viewport`);
  if (projectId !== selectedProjectId || request !== detailRequest) return;
  if (scene.revision !== info.document.revision) { scheduleWorkspaceRefresh(); return; }
  detailedProjectId = projectId;
  detailedProjectModifiedAt = info.project.modified_at;
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
    let targetFound = false;
    for (const object of scene.objects) {
      const source = objectsById.get(object.object_id);
      if (source?.type !== "body" && source?.sketch_id !== visibleSketchId) continue;
      const offset = positions.length / 3;
      const lineOffset = lines.length;
      const pointOffset = points.length;
      for (const value of object.positions) {
        positions.push(value);
        if (source?.type === "body") bodyPositions.push(value);
      }
      for (const index of object.triangles) triangles.push(index + offset);
      for (const index of object.lines) lines.push(index + offset);
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
    modelViewer.load({ positions, triangles, lines, points, bounds, rotationAxes }, {
      reset: !renderedSceneKey?.startsWith(`${projectId}:`), transition, smoothShading: true,
    });
    if (pendingViewportTransition?.projectId === projectId) pendingViewportTransition = null;
    renderedSceneKey = key;
  }
  const hasVisibleGeometry = visibleObjects.some((item) => item.type === "body")
    || visibleSketchId !== null && visibleObjects.some((item) => item.sketch_id === visibleSketchId);
  if (!hasVisibleGeometry) {
    $("#viewport-empty").querySelector("p").textContent = "Модель ещё не построена";
  }
  $("#viewport-empty").hidden = hasVisibleGeometry;
  $("#model-preview").hidden = !hasVisibleGeometry;
  modelViewer.render();

  $("#folder-detail").hidden = true;
  $("#object-panel").hidden = false;
  projectDetail.hidden = false;
  noSelection.hidden = true;
  $("#document-title").textContent = info.project.name;
  if (selectedObjectId && !visibleObjects.some((item) => item.id === selectedObjectId)) selectedObjectId = null;
  if (!selectedObjectId) {
    const topLevel = visibleObjects.filter((item) => item.type === "body" || item.type === "sketch");
    selectedObjectId = topLevel.at(-1)?.id || null;
  }
  currentObjects = visibleObjects;
  renderObjectTree(visibleObjects);
  renderProperties(visibleObjects);
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
  const activity = activities.slice().reverse().find((item) => item.state === "running") || activities.at(-1) || null;
  const panel = $("#agent-activity");
  const title = $("#activity-title");
  const completedAt = Date.parse(activity?.updated_at || "");
  const age = Number.isFinite(completedAt) ? Date.now() - completedAt : Infinity;
  const recentCompletion = activity?.state === "completed" && age >= 0 && age < 2200;
  const state = activity?.state === "running" || activity?.state === "failed"
    ? activity.state : recentCompletion ? "completed" : "idle";
  panel.dataset.state = state;
  const action = activity?.action || "Работает";
  title.textContent = state === "running" ? action
    : state === "failed" ? `${action} · ошибка`
      : recentCompletion ? `${action} · готово` : "Думает";
  if (recentCompletion && activityStatusOperationId !== activity.operation_id) {
    clearTimeout(activityStatusTimer);
    activityStatusOperationId = activity.operation_id;
    activityStatusTimer = setTimeout(() => {
      if (selectedProjectId) renderAgentActivity(activitiesByProject.get(selectedProjectId) || []);
    }, Math.max(1, 2200 - age));
  } else if (!recentCompletion) {
    clearTimeout(activityStatusTimer);
    activityStatusTimer = 0;
    activityStatusOperationId = null;
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
  if (item.type === "feature") return `${({ fuse_bodies: "Объединение тел", sweep_sketch: "Трубчатое выдавливание", fillet_edges: "Скругление рёбер" })[item.operation] || item.operation || "Операция"} · ${dimensionsText(item.parameters)}`;
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

function containsRevolvedProfile(geometry) {
  return geometry?.kind === "revolved_profile"
    || geometry?.kind === "filleted_solid" && containsRevolvedProfile(geometry.base)
    || ["fused_profiles", "fused_solids"].includes(geometry?.kind)
      && (containsRevolvedProfile(geometry.base) || containsRevolvedProfile(geometry.tool));
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
  const consumedBodies = new Set(objects
    .filter((item) => item.type === "feature")
    .flatMap((item) => item.operation === "fuse_bodies" ? [item.base_body_id, item.tool_body_id]
      : item.operation === "fillet_edges" ? [item.source_body_id] : []));
  const sketches = objects.filter((item) => item.type === "sketch");
  for (const body of bodies) {
    if (consumedBodies.has(body.id)) continue;
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
    if (sketch.id !== activeSketchId) continue;
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
    ...Object.entries(item.parameters || {}).map(([key, value]) => [
      key === "centered" ? "Симметричное выдавливание" : key,
      typeof value === "boolean" ? value ? "Да" : "Нет" : formatNumber(value),
    ]),
    ...(item.operation === "fuse_bodies" ? [
      ["Основание", referenceText(item.base_body_id, objects)],
      ["Объединяемое тело", referenceText(item.tool_body_id, objects)],
    ] : item.operation === "fillet_edges" ? [
      ["Исходное тело", referenceText(item.source_body_id, objects)],
      ["Рёбра", item.edge_indices.join(", ")],
    ] : item.operation === "sweep_sketch" ? [
      ["Траектория", referenceText(item.source_sketch_id, objects)],
    ] : []),
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

function animateModelCamera(change) {
  const start = modelViewer.getCamera();
  const duration = change.duration_ms;
  const target = {
    yaw: start.yaw + change.yaw_delta,
    pitch: Math.max(-Math.PI / 2, Math.min(Math.PI / 2, start.pitch + change.pitch_delta)),
    zoom: Math.max(0.25, Math.min(5, start.zoom * change.zoom_factor)),
    projection: "orthographic",
  };
  const token = ++cameraAnimationId;
  if (duration === 0) {
    modelViewer.setCamera(target);
    return;
  }
  const startedAt = performance.now();
  const tick = (now) => {
    if (token !== cameraAnimationId) return;
    const ratio = Math.min(1, (now - startedAt) / duration);
    const eased = ratio < 0.5 ? 2 * ratio * ratio : 1 - ((-2 * ratio + 2) ** 2) / 2;
    modelViewer.setCamera({
      yaw: start.yaw + (target.yaw - start.yaw) * eased,
      pitch: start.pitch + (target.pitch - start.pitch) * eased,
      zoom: start.zoom + (target.zoom - start.zoom) * eased,
      projection: "orthographic",
    });
    if (ratio < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
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
      } else if (["add_sketch_polyline", "add_sketch_rectangle", "add_sketch_involute_gear"].includes(command.type)) {
        activeSketchId = command.sketch_id || activeSketchId;
        pendingViewportTransition = activeSketchId ? {
          projectId, sketchId: activeSketchId, allSketchLines: true,
          type: "sketch-lines", durationMs: Math.min(1800, Math.max(460, 250 + (result.line_ids?.length || 0) * 3)),
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
      } else if (command.type === "set_parameter" && result.geometry?.kind) {
        const transitionType = result.geometry.kind === "revolved_profile" ? "revolve"
          : result.geometry.kind === "extruded_profile" ? "extrude"
          : result.geometry.kind === "swept_pipe" ? "sweep"
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
  } else if (type === "project.created") {
    selectedProjectId = projectId;
    selectedObjectId = null;
    activeSketchId = null;
    pendingViewportTransition = null;
    saveSelectedProject(projectId);
    scheduleWorkspaceRefresh();
  } else if (type === "project.lock.changed") {
    // Lease changes do not alter the document or viewport. Re-rendering the
    // kernel scene here can contend with the agent's next modeling command.
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
const eventTypes = ["project.created", "project.lock.changed", "agent.activity", "command.completed", "document.changed", "viewport.camera"];
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
