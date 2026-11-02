(() => {
  function selectedResourceFromSearch(search) {
    const address = new URLSearchParams(search || "");
    const projectId = address.get("project_id");
    const folderName = address.get("folder");
    return projectId || (folderName ? `folder:${folderName}` : null);
  }

  function dashboardHasUnfinishedWork(dashboard) {
    const workflow = dashboard?.workflow;
    const phases = Array.isArray(workflow?.phases) ? workflow.phases : [];
    const tasks = Array.isArray(dashboard?.tasks) ? dashboard.tasks : [];
    return Boolean(workflow && (
      Number(workflow.progress_percent) < 100
      || phases.some((phase) => phase?.state !== "completed")
    )) || tasks.some((task) => task?.state !== "completed");
  }

  function currentProgressWork(dashboard, value, lock = null) {
    const activities = (Array.isArray(value) ? value : value ? [value] : [])
      .filter((item) => item && typeof item === "object");
    const runningActivity = activities.slice().reverse().find((item) => item.state === "running") || null;
    const activity = runningActivity || activities[activities.length - 1] || null;
    const agentLeaseActive = lock?.state === "AI_LOCKED" && lock.lease?.owner_type === "agent"
      && !lock.recovery_required;
    const tasks = Array.isArray(dashboard?.tasks) ? dashboard.tasks : [];
    const phases = Array.isArray(dashboard?.workflow?.phases) ? dashboard.workflow.phases : [];
    const runningTask = tasks.find((task) => task?.state === "running") || null;
    const activePhase = phases.find((phase) => phase?.state === "in_progress") || null;
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
    let hasSent = false;
    let retryCount = 0;
    let retryNotBefore = 0;
    const maxRetries = 3;
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
      && Number.isSafeInteger(context.revision)
      && context.revision >= 0,
    );

    function schedule() {
      if (timer || inFlight || !dirty) return;
      const delay = Math.max(0, hasSent ? minimumIntervalMs - (now() - lastSentAt) : 0,
        retryNotBefore - now());
      timer = setTimer(() => {
        timer = 0;
        void flush();
      }, delay);
    }

    function retryTransientFailure() {
      if (retryCount >= maxRetries) return;
      retryCount += 1;
      retryNotBefore = now() + Math.min(8000, 500 * (2 ** (retryCount - 1)));
      dirty = true;
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
        if (!sameContext(context, getContext())) {
          dirty = true;
          retryCount = 0;
          retryNotBefore = 0;
          return;
        }
        if (!response.ok) {
          if (response.status !== 409 && !errorShown) {
            onFailure("publish", response.status);
            errorShown = true;
          }
          if ([408, 429].includes(response.status) || response.status >= 500) {
            retryTransientFailure();
          }
          return;
        }
        if (typeof response.consume === "function") await response.consume();
        lastSentAt = now();
        hasSent = true;
        retryCount = 0;
        retryNotBefore = 0;
        errorShown = false;
      } catch (error) {
        if (!sameContext(context, getContext())) {
          dirty = true;
          retryCount = 0;
          retryNotBefore = 0;
          return;
        }
        if (!errorShown) {
          onFailure("sync", null, error);
          errorShown = true;
        }
        retryTransientFailure();
      } finally {
        inFlight = false;
        if (dirty) schedule();
      }
    }

    function markDirty() {
      if (!dirty && !inFlight) {
        retryCount = 0;
        retryNotBefore = 0;
      }
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
