const api = (path, options = {}) => fetch(path, {
  ...options,
  headers: { "Content-Type": "application/json", ...(options.headers || {}) },
}).then(async (response) => {
  const value = await response.json();
  if (!response.ok) throw new Error(value.message || value.error || "Request failed");
  return value;
});

const projectList = document.querySelector("#project-list");
const emptyProjects = document.querySelector("#empty-projects");
const noSelection = document.querySelector("#no-selection");
const projectDetail = document.querySelector("#project-detail");
const toast = document.querySelector("#toast");
let projects = [];
let selectedProjectId = null;
const heldLeases = new Map();
let toastTimer;

function notify(message, isError = false) {
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 2800);
}

function getProject(id) {
  return projects.find((item) => item.project.id === id);
}

async function refreshProjects() {
  const response = await api("/api/v1/projects");
  projects = response.projects;
  document.querySelector("#project-count").textContent = String(projects.length);
  emptyProjects.hidden = projects.length > 0;
  projectList.replaceChildren();
  for (const item of projects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `project-item${item.project.id === selectedProjectId ? " selected" : ""}`;
    const name = document.createElement("span");
    name.className = "project-name";
    name.textContent = item.project.name;
    const state = document.createElement("span");
    state.className = `mini-state${item.lock.state === "FREE" ? "" : " locked"}`;
    state.textContent = item.lock.state.replace("_LOCKED", "");
    button.append(name, state);
    button.addEventListener("click", () => selectProject(item.project.id));
    projectList.append(button);
  }
  if (selectedProjectId && getProject(selectedProjectId)) await refreshDetail();
  else if (selectedProjectId) {
    selectedProjectId = null;
    projectDetail.hidden = true;
    noSelection.hidden = false;
  }
}

async function selectProject(id) {
  selectedProjectId = id;
  await refreshProjects();
  await refreshDetail();
}

async function refreshDetail() {
  if (!selectedProjectId) return;
  const info = await api(`/api/v1/projects/${encodeURIComponent(selectedProjectId)}`);
  const project = info.project;
  const lock = info.lock;
  noSelection.hidden = true;
  projectDetail.hidden = false;
  document.querySelector("#detail-name").textContent = project.name;
  document.querySelector("#detail-id").textContent = project.id;
  const badge = document.querySelector("#lock-badge");
  badge.textContent = lock.recovery_required ? `${lock.state} · RECOVERY REQUIRED` : lock.state;
  badge.className = `lock-badge${lock.state === "AI_LOCKED" ? " ai" : lock.state === "HUMAN_LOCKED" ? " human" : lock.state === "SYSTEM_LOCKED" ? " system" : ""}${lock.recovery_required ? " expired" : ""}`;
  const owner = lock.lease;
  document.querySelector("#lock-owner").textContent = owner ? `${owner.owner_type}: ${owner.owner_id}` : "Free";
  document.querySelector("#current-agent").textContent = owner?.owner_type === "agent" ? owner.owner_id : "None";
  document.querySelector("#document-revision").textContent = String(info.document.revision);
  document.querySelector("#object-count").textContent = String(info.document.objects.length);

  const held = heldLeases.get(project.id);
  const controlsAvailable = !owner && !lock.recovery_required;
  document.querySelector("#acquire-human").disabled = !controlsAvailable;
  document.querySelector("#acquire-agent").disabled = !controlsAvailable;
  const release = document.querySelector("#release-lease");
  release.hidden = !held;
  document.querySelector("#demo-command-panel").hidden = held?.ownerType !== "human";
  renderHistory(info.history);
}

function renderHistory(history) {
  const root = document.querySelector("#history-list");
  root.replaceChildren();
  document.querySelector("#history-count").textContent = `${history.length} ${history.length === 1 ? "entry" : "entries"}`;
  if (!history.length) {
    const empty = document.createElement("p");
    empty.className = "history-empty";
    empty.textContent = "No commands have been committed to this document.";
    root.append(empty);
    return;
  }
  for (const entry of [...history].reverse()) {
    const row = document.createElement("div");
    row.className = "history-entry";
    const time = document.createElement("time");
    time.className = "history-time";
    time.dateTime = entry.timestamp;
    time.textContent = new Date(entry.timestamp).toLocaleString();
    const actor = document.createElement("span");
    actor.className = "history-actor";
    actor.textContent = `${entry.actor_type} · ${entry.actor_id}`;
    const command = document.createElement("span");
    command.className = "history-command";
    command.textContent = `${entry.command.type} · revision ${entry.revision}`;
    row.append(time, actor, command);
    root.append(row);
  }
}

async function withSelected(action) {
  if (!selectedProjectId) return;
  try { await action(selectedProjectId); }
  catch (error) { notify(error.message, true); }
}

document.querySelector("#create-project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = event.currentTarget.elements.name;
  try {
    const response = await api("/api/v1/projects", { method: "POST", body: JSON.stringify({ name: input.value }) });
    input.value = "";
    await refreshProjects();
    await selectProject(response.project.id);
    notify("Project created.");
  } catch (error) { notify(error.message, true); }
});

document.querySelector("#acquire-human").addEventListener("click", () => withSelected(async (id) => {
  const response = await api(`/api/v1/projects/${id}/leases`, {
    method: "POST", body: JSON.stringify({ owner_type: "human", owner_id: "web-user" }),
  });
  heldLeases.set(id, { leaseId: response.lease.lease_id, ownerType: "human" });
  await refreshProjects();
  notify("Human edit lease acquired.");
}));

document.querySelector("#acquire-agent").addEventListener("click", () => withSelected(async (id) => {
  const response = await api(`/api/v1/agent/projects/${id}/leases`, {
    method: "POST", body: JSON.stringify({ owner_id: "demo-agent" }),
  });
  heldLeases.set(id, { leaseId: response.lease.lease_id, ownerType: "agent" });
  await refreshProjects();
  notify("Demo agent lease acquired.");
}));

document.querySelector("#release-lease").addEventListener("click", () => withSelected(async (id) => {
  const held = heldLeases.get(id);
  if (!held) return;
  const leaseId = held.leaseId;
  await api(`/api/v1/projects/${id}/leases/${leaseId}/release`, { method: "POST", body: "{}" });
  heldLeases.delete(id);
  await refreshProjects();
  notify("Edit lease released.");
}));

document.querySelector("#create-box-form").addEventListener("submit", (event) => {
  event.preventDefault();
  withSelected(async (id) => {
    const form = new FormData(event.currentTarget);
    const leaseId = heldLeases.get(id)?.leaseId;
    await api(`/api/v1/projects/${id}/commands`, {
      method: "POST",
      body: JSON.stringify({
        lease_id: leaseId,
        command: {
          type: "create_box",
          name: form.get("name"),
          width: Number(form.get("width")),
          height: Number(form.get("height")),
          depth: Number(form.get("depth")),
        },
      }),
    });
    await refreshProjects();
    notify("Command saved to project history.");
  });
});

const events = new EventSource("/api/v1/events");
for (const name of ["project.created", "project.lock.changed", "command.started", "command.completed", "command.failed", "document.changed"]) {
  events.addEventListener(name, () => {
    refreshProjects().catch((error) => notify(error.message, true));
  });
}

setInterval(() => {
  for (const [projectId, held] of heldLeases) {
    api(`/api/v1/projects/${projectId}/leases/${held.leaseId}/heartbeat`, { method: "POST", body: "{}" })
      .catch(() => heldLeases.delete(projectId));
  }
}, 15_000);

refreshProjects().catch((error) => notify(error.message, true));
