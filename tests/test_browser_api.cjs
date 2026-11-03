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
  { window, Headers }, { filename: 'browser-api.js' });
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

  // Empty JSON responses and empty error bodies must not trigger parsing failures.
  for (const status of [204, 205]) {
    const empty = response(status, 'application/json', null);
    empty.json = async () => { throw new Error('empty response has no JSON body'); };
    assert.equal(Object.keys(await api.requestJson('/empty', {}, async () => empty)).length, 0);
  }
  const emptyError = response(503, 'application/problem+json', null);
  emptyError.headers.get = name => name === 'content-length' ? '0' : 'application/problem+json';
  emptyError.json = async () => { throw new Error('empty error body'); };
  await assert.rejects(api.requestJson('/empty-error', {}, async () => emptyError),
    error => error.status === 503 && /Сервер CADYX/.test(error.message));

  // Fetch accepts Headers and header tuples; preserve caller MIME and credentials.
  for (const headers of [new Headers([['content-type', 'application/merge-patch+json'], ['X-Token', 'secret']]),
    [['content-type', 'application/merge-patch+json'], ['X-Token', 'secret']]]) {
    await api.requestJson('/headers', { headers }, async (_url, options) => {
      assert.equal(options.headers.get('content-type'), 'application/merge-patch+json');
      assert.equal(options.headers.get('x-token'), 'secret');
      return response(200, 'application/json', {});
    });
  }
  await api.requestJson('/default-headers', { method: 'POST', body: '{}' }, async (_url, options) => {
    assert.equal(options.headers.get('content-type'), 'application/json');
    return response(200, 'application/json', {});
  });
  // Bodyless reads should not send a JSON content type or trigger avoidable CORS preflights.
  await api.requestJson('/read', { method: 'GET' }, async (_url, options) => {
    assert.equal(options.headers.has('content-type'), false);
    return response(200, 'application/json', {});
  });
  await api.requestJson('/empty-post', { method: 'POST' }, async (_url, options) => {
    assert.equal(options.headers.has('content-type'), false);
    return response(200, 'application/json', {});
  });

  // Browser-generated multipart boundaries and binary MIME types must not be overwritten.
  for (const body of [new URLSearchParams('q=shape'), new Uint8Array([1, 2, 3])]) {
    await api.requestJson('/upload', { method: 'POST', body }, async (_url, options) => {
      assert.equal(options.headers.has('content-type'), false);
      assert.equal(options.body, body);
      return response(200, 'application/json', {});
    });
  }
  await api.requestJson('/upload', {
    method: 'POST', body: new Uint8Array([1]), headers: { 'Content-Type': 'application/octet-stream' },
  }, async (_url, options) => {
    assert.equal(options.headers.get('content-type'), 'application/octet-stream');
    return response(200, 'application/json', {});
  });

  for (const field of ['expectedRevision', 'baseVersion']) {
    const args = {projectId:'part', leaseId:'lease', expectedRevision:0, baseVersion:0,
      name:'Contour', sourceImageSha256:'digest', coordinateSystem:{}};
    args[field] = Number.MAX_SAFE_INTEGER + 1;
    assert.throws(() => new api.TraceEditorSession(args), /non-negative integer/);
  }

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
  // Invalid or stale success payloads cannot corrupt optimistic concurrency state.
  const oldState = [session.traceId, session.baseVersion, session.expectedRevision];
  for (const [traceId, traceVersion, revision] of [
    ['trace', oldState[1], oldState[2] + 1],
    ['other-trace', oldState[1] + 1, oldState[2] + 1],
    ['trace', oldState[1] + 1, oldState[2]],
    [{ invalid: true }, oldState[1] + 1, oldState[2] + 1],
  ]) {
    await assert.rejects(session.save(async () => response(200, 'application/json', {
      history_entry: { result: { trace_id: traceId, trace_version: traceVersion } },
      document: { revision },
    })), /неполный результат/);
    assert.deepEqual([session.traceId, session.baseVersion, session.expectedRevision], oldState);
  }
  console.log('Browser API: MIME handling, safe error labels and serialized trace saves passed.');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
