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
    if (errorLabels[value?.error]) return errorLabels[value.error];
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
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) return {};
    try {
      return await response.json();
    } catch (error) {
      throw new Error("Сервер CADYX вернул некорректный ответ.", { cause: error });
    }
  }

  function responseError(value, status) {
    const error = new Error(errorMessage(value, status));
    error.status = status;
    error.code = value?.error;
    return error;
  }

  async function requestJson(path, options = {}, fetchImpl = window.fetch.bind(window)) {
    const response = await fetchResponse(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    }, fetchImpl);
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

  window.CadyxBrowserApi = Object.freeze({
    errorLabels,
    errorMessage,
    requestJson,
    requestArrayBuffer,
  });
})();
