(() => {
  function selectedResourceFromSearch(search) {
    const address = new URLSearchParams(search || "");
    const projectId = address.get("project_id");
    const folderName = address.get("folder");
    return projectId || (folderName ? `folder:${folderName}` : null);
  }

  function dashboardHasUnfinishedWork(dashboard) {
    const workflow = dashboard?.workflow;
    return Boolean(workflow && (
      Number(workflow.progress_percent) < 100
      || workflow.phases.some((phase) => phase.state !== "completed")
    )) || (dashboard?.tasks || []).some((task) => task.state !== "completed");
  }

  function currentProgressWork(dashboard, value, lock = null) {
    const activities = Array.isArray(value) ? value : value ? [value] : [];
    const runningActivity = activities.slice().reverse().find((item) => item.state === "running") || null;
    const activity = runningActivity || activities[activities.length - 1] || null;
    const agentLeaseActive = lock?.state === "AI_LOCKED" && lock.lease?.owner_type === "agent"
      && !lock.recovery_required;
    const runningTask = (dashboard?.tasks || []).find((task) => task.state === "running") || null;
    const activePhase = (dashboard?.workflow?.phases || []).find((phase) => phase.state === "in_progress") || null;
    const dashboardWork = runningTask || activePhase;
    return {
      activities, runningActivity, activity, agentLeaseActive,
      runningTask, activePhase, dashboardWork,
      isWorking: Boolean(runningActivity || agentLeaseActive || dashboardWork),
    };
  }

  function lockAvailabilityKey(lock) {
    return JSON.stringify([lock?.state || null, Boolean(lock?.recovery_required)]);
  }

  function reduceAgentWorkLock(current, active, revision, projectIds = null) {
    if (typeof active !== "boolean" || !Number.isInteger(revision) || revision < current.revision) {
      return current;
    }
    if (active) {
      return {
        active: true,
        revision,
        projectIds: Array.isArray(projectIds)
          ? projectIds.filter((id) => typeof id === "string")
          : current.projectIds.length ? current.projectIds.slice() : [],
      };
    }
    if (!current.active || revision > current.revision) {
      return { active: false, revision, projectIds: [] };
    }
    return current;
  }

  function createViewportSnapshotSynchronizer({
    minimumIntervalMs = 400,
    now = () => performance.now(),
    setTimer = (callback, delay) => window.setTimeout(callback, delay),
    capture,
    getContext,
    publish,
    onFailure = () => {},
  }) {
    let timer = 0;
    let lastSentAt = 0;
    let inFlight = false;
    let dirty = false;
    let errorShown = false;

    const sameContext = (left, right) => Boolean(
      left && right
      && left.projectId === right.projectId
      && left.renderedProjectId === right.renderedProjectId
      && left.revision === right.revision,
    );

    const validContext = (context) => Boolean(
      context?.projectId
      && !String(context.projectId).startsWith("folder:")
      && context.renderedProjectId === context.projectId
      && Number.isInteger(context.revision),
    );

    function schedule() {
      if (timer || inFlight || !dirty) return;
      const delay = Math.max(0, minimumIntervalMs - (now() - lastSentAt));
      timer = setTimer(() => {
        timer = 0;
        void flush();
      }, delay);
    }

    async function flush() {
      if (inFlight || !dirty) return;
      const context = getContext();
      if (!validContext(context)) {
        dirty = false;
        return;
      }
      dirty = false;
      inFlight = true;
      try {
        const image = await capture();
        const current = getContext();
        if (!sameContext(context, current)) {
          dirty = true;
          return;
        }
        const response = await publish({
          projectId: context.projectId,
          revision: context.revision,
          image,
        });
        if (!response.ok) {
          if (response.status !== 409 && !errorShown) {
            onFailure("publish", response.status);
            errorShown = true;
          }
          return;
        }
        if (typeof response.consume === "function") await response.consume();
        lastSentAt = now();
        errorShown = false;
      } catch (error) {
        if (!errorShown) {
          onFailure("sync", null, error);
          errorShown = true;
        }
      } finally {
        inFlight = false;
        if (dirty) schedule();
      }
    }

    function markDirty() {
      dirty = true;
      schedule();
    }

    function state() {
      return Object.freeze({ timerPending: Boolean(timer), lastSentAt, inFlight, dirty, errorShown });
    }

    return Object.freeze({ markDirty, flush, state });
  }

  window.CadyxBrowserState = Object.freeze({
    selectedResourceFromSearch,
    dashboardHasUnfinishedWork,
    currentProgressWork,
    lockAvailabilityKey,
    reduceAgentWorkLock,
    createViewportSnapshotSynchronizer,
  });
})();
