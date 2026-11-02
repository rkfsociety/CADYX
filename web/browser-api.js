(() => {
  const errorLabels = Object.freeze({
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
  });

  function errorMessage(value, status) {
    if (typeof value?.error === "string" && Object.prototype.hasOwnProperty.call(errorLabels, value.error)) {
      return errorLabels[value.error];
    }
    if (status >= 500) return "Сервер CADYX не смог выполнить запрос.";
    if (status === 404) return "Запрошенные данные не найдены.";
    return "Запрос отклонён. Проверьте данные и повторите попытку.";
  }

  async function fetchResponse(path, options = {}, fetchImpl = window.fetch.bind(window)) {
    try {
      return await fetchImpl(path, options);
    } catch (error) {
      if (error instanceof TypeError) {
        throw new Error("Нет связи с сервером CADYX.", { cause: error });
      }
      throw error;
    }
  }

  async function readJsonResponse(response) {
    const contentType = (response.headers.get("content-type") || "").split(";", 1)[0].trim().toLowerCase();
    if ([204, 205, 304].includes(response.status) || response.headers.get("content-length") === "0") return {};
    if (contentType !== "application/json"
        && !(contentType.startsWith("application/") && contentType.endsWith("+json"))) return {};
    try {
      return await response.json();
    } catch (error) {
      throw new Error("Сервер CADYX вернул некорректный ответ.", { cause: error });
    }
  }

  function responseError(value, status) {
    const error = new Error(errorMessage(value, status));
    error.status = status;
    error.code = typeof value?.error === "string" ? value.error : undefined;
    return error;
  }

  async function requestJson(path, options = {}, fetchImpl = window.fetch.bind(window)) {
    const headers = new Headers(options.headers);
    if (!headers.has("content-type")) headers.set("Content-Type", "application/json");
    const response = await fetchResponse(path, { ...options, headers }, fetchImpl);
    const value = await readJsonResponse(response);
    if (!response.ok) throw responseError(value, response.status);
    return value;
  }

  async function requestArrayBuffer(path, options = {}, fetchImpl = window.fetch.bind(window)) {
    const response = await fetchResponse(path, options, fetchImpl);
    if (!response.ok) {
      const value = await readJsonResponse(response);
      throw responseError(value, response.status);
    }
    return response.arrayBuffer();
  }

  const traceDraftApi = window.CadyxTraceDraft;
  if (!traceDraftApi?.TraceDraft || !traceDraftApi?.cloneOwned) {
    throw new Error("CADYX trace-draft primitive must be loaded before browser-api.js");
  }
  const { TraceDraft, cloneOwned: cloneTraceValue } = traceDraftApi;

  class TraceEditorSession {
    constructor({
      projectId,
      leaseId,
      expectedRevision,
      name,
      sourceImageSha256,
      coordinateSystem,
      plane = "XY",
      closed = false,
      traceId = null,
      baseVersion = 0,
      segments = [],
    }) {
      if (!projectId || !leaseId) throw new TypeError("projectId and leaseId are required");
      if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 0) {
        throw new TypeError("expectedRevision must be a non-negative integer");
      }
      if (!Number.isSafeInteger(baseVersion) || baseVersion < 0) {
        throw new TypeError("baseVersion must be a non-negative integer");
      }
      this.projectId = projectId;
      this.leaseId = leaseId;
      this.expectedRevision = expectedRevision;
      this.name = name;
      this.sourceImageSha256 = sourceImageSha256;
      this.coordinateSystem = cloneTraceValue(coordinateSystem);
      this.plane = plane;
      this.closed = closed;
      this.traceId = traceId;
      this.baseVersion = baseVersion;
      this.draft = new TraceDraft({ segments });
      this._saving = false;
    }

    command() {
      const command = {
        type: "save_traced_curve_version",
        name: this.name,
        source_image_sha256: this.sourceImageSha256,
        coordinate_system: cloneTraceValue(this.coordinateSystem),
        segments: this.draft.snapshot().segments,
        plane: this.plane,
        closed: this.closed,
        base_version: this.baseVersion,
      };
      if (this.traceId) command.trace_id = this.traceId;
      return command;
    }

    async save(fetchImpl = window.fetch.bind(window)) {
      if (this._saving) throw new Error("Сохранение трассировки уже выполняется.");
      this._saving = true;
      try {
        const response = await requestJson(
          `/api/v1/projects/${encodeURIComponent(this.projectId)}/commands`,
          {
            method: "POST",
            body: JSON.stringify({
              lease_id: this.leaseId,
              expected_revision: this.expectedRevision,
              command: this.command(),
            }),
          },
          fetchImpl,
        );
        const result = response?.history_entry?.result;
        const revision = response?.document?.revision;
        if (!result || typeof result.trace_id !== "string" || !result.trace_id.trim()
            || (this.traceId && result.trace_id !== this.traceId)
            || !Number.isSafeInteger(result.trace_version) || result.trace_version <= this.baseVersion
            || !Number.isSafeInteger(revision) || revision <= this.expectedRevision) {
          throw new Error("Сервер CADYX вернул неполный результат сохранения трассировки.");
        }
        this.traceId = result.trace_id;
        this.baseVersion = result.trace_version;
        this.expectedRevision = revision;
        return cloneTraceValue(response);
      } finally {
        this._saving = false;
      }
    }
  }

  window.CadyxBrowserApi = Object.freeze({
    errorLabels,
    errorMessage,
    requestJson,
    requestArrayBuffer,
    TraceDraft,
    TraceEditorSession,
  });
})();