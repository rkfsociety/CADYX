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
const toast = $("#toast");
const numberFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
const dateFormat = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const heldLeases = new Map();
const activeAgents = new Map();
const modelStates = new Map();
const modelStateTimers = new Map();
const documentRevisions = new Map();
let projects = [];
let folders = [];
let selectedProjectId = readSelectedProject();
let selectedBodyId = null;
let currentObjects = [];
let toastTimer;
let refreshTimer;
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

function formatDate(value) {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime()) ? dateFormat.format(date) : "—";
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

function stateLabel(state) {
  if (state === "FREE") return "Свободен";
  if (state === "AI_LOCKED") return "Агент";
  if (state === "HUMAN_LOCKED") return "Пользователь";
  if (state === "SYSTEM_LOCKED") return "Система";
  return state;
}

function lockLabel(lock) {
  if (lock.recovery_required) return "ТРЕБУЕТСЯ ПРОВЕРКА";
  return ({
    FREE: "ДОСТУП СВОБОДЕН",
    AI_LOCKED: "ДОСТУП У АГЕНТА",
    HUMAN_LOCKED: "ДОСТУП У ПОЛЬЗОВАТЕЛЯ",
    SYSTEM_LOCKED: "СИСТЕМНАЯ БЛОКИРОВКА",
  })[lock.state] || lock.state;
}

function lockClass(lock) {
  const owner = lock.lease?.owner_type;
  const variant = owner === "agent" ? "agent" : owner === "human" ? "human" : owner === "system" ? "system" : "free";
  return `lock-badge ${variant}${lock.recovery_required ? " expired" : ""}`;
}

function setConnection(state) {
  const label = ({
    connecting: "Подключение",
    connected: "События подключены",
    reconnecting: "Переподключение",
    offline: "События недоступны",
  })[state] || state;
  const indicator = $("#connection-status");
  indicator.dataset.state = state;
  $("#connection-label").textContent = label;
}

function setModelState(projectId, state, label) {
  clearTimeout(modelStateTimers.get(projectId));
  const timestamp = new Date().toISOString();
  modelStates.set(projectId, { state, label, timestamp });

  if (projectId === selectedProjectId) {
    const root = $("#model-live-state");
    root.dataset.state = state;
    $("#model-live-label").textContent = label;
    const preview = $("#model-preview");
    preview.classList.remove("changed");
    if (state === "updated") requestAnimationFrame(() => {
      if (projectId === selectedProjectId && modelStates.get(projectId)?.timestamp === timestamp) {
        preview.classList.add("changed");
      }
    });
  }

  if (state === "updated") {
    modelStateTimers.set(projectId, setTimeout(() => {
      const current = modelStates.get(projectId);
      if (current?.timestamp !== timestamp) return;
      modelStateTimers.delete(projectId);
      modelStates.set(projectId, { state: "idle", label: "Актуальная модель", timestamp });
      if (projectId === selectedProjectId) {
        $("#model-live-state").dataset.state = "idle";
        $("#model-live-label").textContent = "Актуальная модель";
        $("#model-preview").classList.remove("changed");
      }
    }, 2400));
  }
}

function renderProjectList() {
  const entries = [
    ...projects.map((item) => ({ kind: "project", name: item.project.name, item })),
    ...folders.map((name) => ({ kind: "folder", name })),
  ].sort((left, right) => left.name.localeCompare(right.name, "ru", { sensitivity: "base" }));
  $("#project-count").textContent = String(entries.length);
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
      const detail = document.createElement("small");
      detail.textContent = "Папка проекта";
      copy.append(name, detail);
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
    marker.dataset.state = item.lock.state;
    marker.setAttribute("aria-hidden", "true");

    const copy = document.createElement("span");
    copy.className = "project-copy";
    const name = document.createElement("strong");
    name.className = "project-name";
    name.textContent = project.name;
    const detail = document.createElement("small");
    detail.textContent = `${stateLabel(item.lock.state)} · изм. ${formatDate(project.modified_at)}`;
    copy.append(name, detail);

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
  selectedBodyId = null;
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
  projectDetail.hidden = true;
  noSelection.hidden = true;
  $("#folder-detail").hidden = false;
  $("#topbar-project-name").textContent = name;
  $("#folder-detail-name").textContent = name;
  $("#folder-detail-path").textContent = `projects/${name}`;
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
    selectedBodyId = null;
    saveSelectedProject(selectedProjectId);
  }

  renderProjectList();
  const selectedFolder = getSelectedFolder();
  if (selectedFolder) showFolderDetail(selectedFolder);
  else if (selectedProjectId && getProject(selectedProjectId)) await refreshDetail();
  else {
    projectDetail.hidden = true;
    $("#folder-detail").hidden = true;
    noSelection.hidden = false;
    $("#topbar-project-name").textContent = "Не выбран";
  }
}

function scheduleWorkspaceRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refreshWorkspace().catch((error) => {
      setConnection("offline");
      notify(error.message, true);
    });
  }, 60);
}

async function refreshDetail() {
  const projectId = selectedProjectId;
  if (!projectId) return;
  const info = await api(`/api/v1/projects/${encodeURIComponent(projectId)}`);
  if (projectId !== selectedProjectId) return;

  $("#folder-detail").hidden = true;
  projectDetail.hidden = false;
  noSelection.hidden = true;
  $("#topbar-project-name").textContent = info.project.name;
  $("#detail-name").textContent = info.project.name;
  $("#detail-id").textContent = info.project.id;

  const lock = info.lock;
  const badge = $("#lock-badge");
  badge.textContent = lockLabel(lock);
  badge.className = lockClass(lock);
  updateAgentState(projectId, lock);
  updateManualControls(projectId, lock);

  const previousRevision = documentRevisions.get(projectId);
  const objects = info.document.objects;
  const bodies = objects.filter((item) => item.type === "body");
  const features = objects.filter((item) => item.type === "feature");
  if (selectedBodyId && !bodies.some((item) => item.id === selectedBodyId)) selectedBodyId = null;
  if (!selectedBodyId && bodies.length) selectedBodyId = bodies[bodies.length - 1].id;
  if (previousRevision === undefined || info.document.revision !== previousRevision) {
    documentRevisions.set(projectId, info.document.revision);
    if (!modelStates.has(projectId)) {
      const label = info.document.revision ? "Актуальная версия" : "Без изменений";
      modelStates.set(projectId, { state: "idle", label, timestamp: null });
    }
  }

  currentObjects = objects;
  $("#document-revision").textContent = String(info.document.revision);
  $("#object-count").textContent = `${bodies.length} / ${features.length}`;
  $("#last-modified").textContent = info.project.modified_at
    ? `Изменено ${formatDate(info.project.modified_at)}`
    : "Изменений пока нет";

  const visualState = modelStates.get(projectId);
  if (visualState) {
    $("#model-live-state").dataset.state = visualState.state;
    $("#model-live-label").textContent = visualState.label;
  }
  renderViewport(bodies);
  renderObjectTree(bodies);
  renderProperties(bodies);
}

function updateAgentState(projectId, lock) {
  const active = lock.lease;
  const running = activeAgents.get(projectId);
  const status = $("#agent-state");
  const detail = $("#current-agent");

  if (running) {
    status.textContent = "Вносит изменения";
    detail.textContent = running;
  } else if (active?.owner_type === "agent") {
    status.textContent = "Доступ у агента";
    detail.textContent = active.owner_id;
  } else if (active?.owner_type === "human") {
    status.textContent = "Пауза для агента";
    detail.textContent = `Проект редактирует пользователь · ${active.owner_id}`;
  } else {
    status.textContent = "Ожидание";
    detail.textContent = "Состояние синхронизируется в реальном времени";
  }
}

function updateManualControls(projectId, lock) {
  const held = heldLeases.get(projectId);
  const available = !lock.lease && !lock.recovery_required;
  $("#acquire-human").disabled = !available;
  $("#acquire-human").hidden = Boolean(held);
  $("#release-lease").hidden = !held;
  $("#create-box-form").hidden = held?.ownerType !== "human";
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
}

function renderViewport(bodies) {
  const empty = $("#viewport-empty");
  const preview = $("#model-preview");
  const meta = $("#viewport-meta");
  const faces = $("#model-faces");
  const edges = $("#model-edges");
  const body = bodies.find((item) => item.id === selectedBodyId) || bodies[bodies.length - 1];

  if (!body || !body.geometry?.dimensions) {
    selectedBodyId = null;
    empty.hidden = false;
    preview.hidden = true;
    meta.hidden = true;
    $("#viewport-label").textContent = "Нет тел";
    $("#properties-empty").hidden = false;
    $("#properties-list").hidden = true;
    faces.replaceChildren();
    edges.replaceChildren();
    return;
  }

  selectedBodyId = body.id;
  empty.hidden = true;
  preview.hidden = false;
  meta.hidden = false;
  $("#viewport-label").textContent = `${bodies.length} ${bodies.length === 1 ? "тело" : bodies.length < 5 ? "тела" : "тел"}`;

  const { width, height, depth } = body.geometry.dimensions;
  const scale = 106 / Math.max(width, height, depth);
  const point = (x, y, z) => [
    300 + (x - y) * scale,
    255 + (x + y) * scale * 0.36 - z * scale * 0.78,
  ];
  const points = [
    point(0, 0, 0), point(width, 0, 0), point(width, depth, 0), point(0, depth, 0),
    point(0, 0, height), point(width, 0, height), point(width, depth, height), point(0, depth, height),
  ];
  const pointString = (indexes) => indexes.map((index) => points[index].map((value) => value.toFixed(1)).join(",")).join(" ");

  faces.replaceChildren(
    svgElement("polygon", { points: pointString([0, 1, 5, 4]), class: "face-front" }),
    svgElement("polygon", { points: pointString([1, 2, 6, 5]), class: "face-side" }),
    svgElement("polygon", { points: pointString([4, 5, 6, 7]), class: "face-top" }),
  );
  const path = [
    `M ${points[0].join(" ")} L ${points[1].join(" ")} L ${points[2].join(" ")} L ${points[3].join(" ")} Z`,
    `M ${points[4].join(" ")} L ${points[5].join(" ")} L ${points[6].join(" ")} L ${points[7].join(" ")} Z`,
    `M ${points[0].join(" ")} L ${points[4].join(" ")}`,
    `M ${points[1].join(" ")} L ${points[5].join(" ")}`,
    `M ${points[2].join(" ")} L ${points[6].join(" ")}`,
    `M ${points[3].join(" ")} L ${points[7].join(" ")}`,
  ].join(" ");
  edges.replaceChildren(svgElement("path", { d: path, class: "model-outline" }));

  $("#selected-object-name").textContent = body.name || "Тело";
  $("#selected-object-dimensions").textContent = `${formatNumber(width)} × ${formatNumber(height)} × ${formatNumber(depth)}`;
  $("#selected-object-volume").textContent = `V ${formatNumber(body.geometry.volume)}`;
}

function renderObjectTree(bodies) {
  const root = $("#object-tree");
  root.replaceChildren();
  $("#tree-count").textContent = String(bodies.length);
  $("#objects-empty").hidden = bodies.length > 0;

  for (const body of bodies) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `object-row${body.id === selectedBodyId ? " selected" : ""}`;
    button.setAttribute("aria-pressed", String(body.id === selectedBodyId));
    const marker = document.createElement("span");
    marker.className = "object-symbol";
    marker.setAttribute("aria-hidden", "true");
    marker.textContent = "▰";
    const copy = document.createElement("span");
    copy.className = "object-copy";
    const name = document.createElement("strong");
    name.textContent = body.name || "Тело";
    const detail = document.createElement("small");
    detail.textContent = dimensionsText(body.geometry?.dimensions);
    copy.append(name, detail);
    button.append(marker, copy);
    button.addEventListener("click", () => {
      selectedBodyId = body.id;
      renderViewport([body, ...currentObjects.filter((item) => item.type === "body" && item.id !== body.id)]);
      renderObjectTree(currentObjects.filter((item) => item.type === "body"));
      renderProperties([body]);
    });
    root.append(button);
  }
}

function dimensionsText(dimensions) {
  if (!dimensions) return "";
  return `${formatNumber(dimensions.width)} × ${formatNumber(dimensions.height)} × ${formatNumber(dimensions.depth)}`;
}

function renderProperties(bodies) {
  const body = bodies.find((item) => item.id === selectedBodyId) || bodies[bodies.length - 1];
  if (!body?.geometry?.dimensions) {
    $("#properties-empty").hidden = false;
    $("#properties-list").hidden = true;
    return;
  }
  const dimensions = body.geometry.dimensions;
  $("#properties-empty").hidden = true;
  $("#properties-list").hidden = false;
  $("#property-name").textContent = body.name || "Тело";
  $("#property-width").textContent = formatNumber(dimensions.width);
  $("#property-height").textContent = formatNumber(dimensions.height);
  $("#property-depth").textContent = formatNumber(dimensions.depth);
  $("#property-volume").textContent = formatNumber(body.geometry.volume);
}

function selectBodyFromResult(projectId, result) {
  let bodyId = result?.body_id;
  if (!bodyId && result?.feature_id) {
    bodyId = currentObjects.find((item) => item.id === result.feature_id)?.body_id;
  }
  if (projectId === selectedProjectId && bodyId) selectedBodyId = bodyId;
}

function handleStreamEvent(type, event) {
  const projectId = event.project_id;
  if (!projectId) return;
  const data = event.data || {};

  if (type === "command.started") {
    activeAgents.set(projectId, data.actor_id || "Агент");
    setModelState(projectId, "working", "Агент вносит изменения");
  } else if (type === "command.completed") {
    activeAgents.delete(projectId);
    selectBodyFromResult(projectId, data.result);
    setModelState(projectId, "updated", "Модель обновлена");
    scheduleWorkspaceRefresh();
  } else if (type === "command.failed") {
    activeAgents.delete(projectId);
    setModelState(projectId, "failed", "Не удалось обновить модель");
  } else if (type === "document.changed") {
    scheduleWorkspaceRefresh();
  } else if (type === "project.lock.changed" || type === "project.created") {
    scheduleWorkspaceRefresh();
  }

  if (type === "project.lock.changed" && data.lease?.owner_type === "agent") {
    activeAgents.set(projectId, data.lease.owner_id);
  } else if (type === "project.lock.changed" && !data.lease) {
    activeAgents.delete(projectId);
  }

  if (projectId === selectedProjectId) {
    const project = getProject(projectId);
    if (project) updateAgentState(projectId, project.lock);
  }
}

function withSelected(action) {
  if (!selectedProjectId) return;
  return action(selectedProjectId).catch((error) => notify(error.message, true));
}

$("#create-project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = event.currentTarget.elements.name;
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  try {
    const response = await api("/api/v1/projects", { method: "POST", body: JSON.stringify({ name: input.value }) });
    input.value = "";
    selectedProjectId = response.project.id;
    selectedBodyId = null;
    saveSelectedProject(selectedProjectId);
    await refreshWorkspace();
    notify("Проект создан");
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
});

projectList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-project-id]");
  if (button) {
    selectProject(button.dataset.projectId);
    return;
  }
  const folder = event.target.closest("button[data-folder-name]");
  if (folder) selectProject(folderSelectionId(folder.dataset.folderName));
});

$("#acquire-human").addEventListener("click", () => withSelected(async (id) => {
  const response = await api(`/api/v1/projects/${encodeURIComponent(id)}/leases`, {
    method: "POST",
    body: JSON.stringify({ owner_type: "human", owner_id: "web-user" }),
  });
  heldLeases.set(id, { leaseId: response.lease.lease_id, ownerType: "human" });
  await refreshWorkspace();
  notify("Доступ получен");
}));

$("#release-lease").addEventListener("click", () => withSelected(async (id) => {
  const held = heldLeases.get(id);
  if (!held) return;
  await api(`/api/v1/projects/${encodeURIComponent(id)}/leases/${encodeURIComponent(held.leaseId)}/release`, {
    method: "POST",
    body: "{}",
  });
  heldLeases.delete(id);
  await refreshWorkspace();
  notify("Доступ освобождён");
}));

$("#create-box-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedProjectId) return;
  const id = selectedProjectId;
  const form = new FormData(event.currentTarget);
  const held = heldLeases.get(id);
  if (!held) {
    notify("Сначала захватите доступ к проекту", true);
    return;
  }
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const response = await api(`/api/v1/projects/${encodeURIComponent(id)}/commands`, {
      method: "POST",
      body: JSON.stringify({
        lease_id: held.leaseId,
        command: {
          type: "create_box",
          name: form.get("name"),
          width: Number(form.get("width")),
          height: Number(form.get("height")),
          depth: Number(form.get("depth")),
        },
      }),
    });
    selectBodyFromResult(id, response.history_entry?.result);
    setModelState(id, "updated", "Модель обновлена");
    await refreshWorkspace();
    notify("Тело добавлено в модель");
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
});

const stream = new EventSource("/api/v1/events");
const eventTypes = ["project.created", "project.lock.changed", "command.started", "command.completed", "command.failed", "document.changed"];
for (const type of eventTypes) {
  stream.addEventListener(type, (message) => {
    try { handleStreamEvent(type, JSON.parse(message.data)); }
    catch { setModelState(selectedProjectId, "failed", "Ошибка обновления"); }
  });
}

stream.addEventListener("open", () => {
  setConnection("connected");
  scheduleWorkspaceRefresh();
});
stream.addEventListener("error", () => {
  setConnection(stream.readyState === EventSource.CLOSED ? "offline" : "reconnecting");
});

setInterval(() => {
  for (const [projectId, held] of heldLeases) {
    api(`/api/v1/projects/${encodeURIComponent(projectId)}/leases/${encodeURIComponent(held.leaseId)}/heartbeat`, {
      method: "POST",
      body: "{}",
    }).catch(() => {
      heldLeases.delete(projectId);
      if (projectId === selectedProjectId) refreshWorkspace().catch((error) => notify(error.message, true));
    });
  }
}, 15_000);

$("#server-address").textContent = location.host;
setConnection("connecting");
refreshWorkspace().catch((error) => {
  setConnection("offline");
  notify(error.message, true);
});
