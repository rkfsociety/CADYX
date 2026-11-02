const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const window = {
  CadyxTraceDraft: {
    TraceDraft: class {
      constructor({ segments }) { this.segments = segments; }
      snapshot() { return { segments: this.segments }; }
    },
    cloneOwned: (value) => JSON.parse(JSON.stringify(value)),
  },
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/browser-api.js'), 'utf8'),
  { window }, { filename: 'browser-api.js' });
const api = window.CadyxBrowserApi;
const response = (status, type, payload) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: { get: () => type },
  json: async () => payload,
  arrayBuffer: async () => new ArrayBuffer(4),
});

(async () => {
  // JSON MIME variants must preserve API error codes and localized user feedback.
  for (const [type, code, expected] of [
    ['application/problem+json; charset=utf-8', 'REVISION_CONFLICT', 'Проект изменился'],
    ['Application/JSON; charset=UTF-8', 'PROJECT_BUSY', 'Проект занят'],
    ['application/vnd.api+json', 'ARTIFACT_CONFLICT', 'Файл результата'],
  ]) {
    await assert.rejects(
      api.requestJson('/test', {}, async () => response(409, type, { error: code })),
      (error) => error.status === 409 && error.code === code && error.message.includes(expected),
    );
  }
  // Untrusted error names must not resolve Object.prototype properties as labels.
  for (const code of ['constructor', '__proto__', 'toString']) {
    await assert.rejects(
      api.requestJson('/test', {}, async () => response(400, 'application/json', { error: code })),
      (error) => error.message === 'Запрос отклонён. Проверьте данные и повторите попытку.',
    );
  }
  await assert.rejects(
    api.requestArrayBuffer('/cloud', {}, async () =>
      response(409, 'application/problem+json', { error: 'REVISION_CONFLICT' })),
    (error) => error.code === 'REVISION_CONFLICT' && error.message.includes('Проект изменился'),
  );
  assert.equal(await api.requestJson('/test', {}, async () =>
    response(200, 'application/json', null)), null);
  // Non-JSON success responses are treated as an empty object.
  assert.equal(Object.keys(await api.requestJson('/test', {}, async () =>
    response(200, 'text/html', '<html>'))).length, 0);

  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const calls = [];
  const session = new api.TraceEditorSession({
    projectId: 'part', leaseId: 'lease', expectedRevision: 2,
    name: 'Contour', sourceImageSha256: 'digest', coordinateSystem: {},
  });
  const saveFetch = async (_url, options) => {
    const body = JSON.parse(options.body);
    calls.push(body);
    if (calls.length === 1) await gate;
    return response(200, 'application/json', {
      history_entry: { result: { trace_id: 'trace', trace_version: calls.length } },
      document: { revision: 2 + calls.length },
    });
  };
  const first = session.save(saveFetch);
  await assert.rejects(session.save(saveFetch), /уже выполняется/);
  assert.equal(calls.length, 1, 'concurrent save must not duplicate a mutation');
  release();
  await first;
  await session.save(saveFetch);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].expected_revision, 3);
  assert.equal(calls[1].command.base_version, 1);
  assert.equal(session.expectedRevision, 4);

  // Failed saves must release the in-flight guard for an explicit retry.
  await assert.rejects(session.save(async () => {
    throw new Error('offline');
  }), /offline/);
  await session.save(saveFetch);
  assert.equal(calls.length, 3);
  console.log('Browser API: MIME handling, safe error labels and serialized trace saves passed.');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
