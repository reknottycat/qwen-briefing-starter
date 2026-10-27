/** In-process DOM behavior checks. Run with jsdom@26.1.0; no browser is launched. */
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const test = require('node:test');
const {JSDOM, VirtualConsole} = require('jsdom');

const ROOT = join(__dirname, '..');
const html = readFileSync(join(ROOT, 'briefing/management/index.html'), 'utf8');
const script = readFileSync(join(ROOT, 'briefing/management/app.js'), 'utf8');
const clone = (value) => JSON.parse(JSON.stringify(value));
const catalog = {
  version: 'fixture-1', source: 'Pi model directory',
  providers: [
    {id: 'provider-one', name: '目录服务甲', models: [{id: 'model-one', name: '模型甲'}, {id: 'model-two', name: '模型乙'}]},
    {id: 'provider-two', name: '目录服务乙', models: [{id: 'model-other', name: '另一模型'}]},
  ],
  custom_apis: [{id: 'openai-completions', name: 'OpenAI Chat Completions 兼容接口'}],
};
const settings = {
  enabled: true, revision: 1, configured: true, mode: 'builtin', provider: 'provider-one',
  model: 'model-one', base_url: '', api: '', api_key_configured: true, source: 'web',
};
const preferences = {
  revision: 1, format: 'mixed', work_enabled: false,
  sources: [{name: '每日课程', kind: 'daily', order: 0, mode: 'processed', category: 'learning', narration_detail: 'detailed', note: ''}],
};
const status = {status: 'ready', source_count: 1, date: '2026-10-27', generated_at: null};

async function settle(check, message = 'DOM did not reach expected state') {
  for (let index = 0; index < 50; index += 1) {
    if (check()) return;
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.ok(check(), message);
}

async function openPage(t, options = {}) {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', (error) => errors.push(error));
  const dom = new JSDOM(html, {url: 'https://briefing.example.com/briefing/v1/manage/', runScripts: 'outside-only', virtualConsole});
  t.after(() => { dom.window.close(); assert.deepEqual(errors.map((error) => error.message), []); });
  const requests = [];
  const backend = {settings: clone(options.settings || settings), catalog: clone(options.catalog || catalog),
                   preferences: clone(preferences), loggedIn: true, putStatus: 200};
  dom.window.fetch = async (url, request = {}) => {
    const path = url.replace('/briefing/v1/manage', '');
    const method = request.method || 'GET';
    requests.push({path, method, ...request});
    let value;
    let code = 200;
    if (options.intercept) {
      const intercepted = await options.intercept({path, method, request, backend});
      if (intercepted) return {ok: intercepted.status < 400, status: intercepted.status, json: async () => clone(intercepted.body || {})};
    }
    if (path === '/session' && method === 'DELETE') { backend.loggedIn = false; code = 204; }
    else if (path === '/session' && method === 'POST') { backend.loggedIn = true; code = 204; }
    else if (!backend.loggedIn) code = 401;
    else if (path === '/preferences') value = backend.preferences;
    else if (path === '/status') value = status;
    else if (path === '/model-catalog') value = backend.catalog;
    else if (path === '/model-settings' && method === 'GET') value = backend.settings;
    else if (path === '/model-settings' && method === 'PUT') {
      code = backend.putStatus;
      if (code === 200) {
        const body = JSON.parse(request.body);
        assert.equal(body.revision, backend.settings.revision);
        const {api_key, revision, ...config} = body;
        backend.settings = {...backend.settings, ...config, revision: revision + 1, configured: true,
                            api_key_configured: Boolean(api_key) || backend.settings.api_key_configured, source: 'web'};
        value = backend.settings;
      }
    } else throw new Error(`Unexpected test request ${method} ${path}`);
    return {ok: code < 400, status: code, json: async () => clone(value || {})};
  };
  dom.window.eval(script);
  const $ = (id) => dom.window.document.getElementById(id);
  await settle(() => !$('settings-screen').hidden);
  await settle(() => $('model-panel') && $('model-panel').getAttribute('aria-busy') === 'false', 'Model panel did not load');
  const change = (id, value, event = 'change') => {
    const control = $(id); assert.ok(control, `missing #${id}`);
    if (control.type === 'radio' || control.type === 'checkbox') control.checked = value;
    else control.value = value;
    control.dispatchEvent(new dom.window.Event(event, {bubbles: true}));
  };
  const submit = () => $('model-form').dispatchEvent(new dom.window.Event('submit', {bubbles: true, cancelable: true}));
  const saved = () => settle(() => !$('model-save').disabled && /已保存/.test($('model-message').textContent));
  return {dom, $, requests, backend, change, submit, saved};
}

test('model choices come from the returned directory and keep saved keys out of the DOM', async (t) => {
  const page = await openPage(t);
  assert.equal(page.$('model-form').hidden, false);
  assert.deepEqual([...page.$('model-provider').options].filter((item) => item.value).map((item) => item.value), ['provider-one', 'provider-two']);
  assert.deepEqual([...page.$('model-options').options].map((item) => item.value), ['model-one', 'model-two']);
  assert.equal(page.$('model-id').value, 'model-one');
  assert.equal(page.$('model-api-key').type, 'password');
  assert.equal(page.$('model-api-key').value, '');
  assert.match(page.$('model-key-help').textContent, /留空.*保留/);
});

test('changing only a model preserves the stored service key without sending it to the page', async (t) => {
  const page = await openPage(t);
  page.change('model-id', 'model-two', 'input');
  page.submit();
  await page.saved();
  const put = page.requests.find((request) => request.path === '/model-settings' && request.method === 'PUT');
  assert.equal(JSON.parse(put.body).model, 'model-two');
  assert.ok(!Object.hasOwn(JSON.parse(put.body), 'api_key'));
  assert.match(page.$('model-message').textContent, /下一次整理/);
  assert.match(page.$('model-message').textContent, /尚未验证/);
  assert.ok(!page.requests.some((request) => request.path === '/preferences' && request.method === 'PUT'));
});

test('switching service clears a typed key and requires the new destination key', async (t) => {
  const page = await openPage(t);
  page.change('model-api-key', 'synthetic-old-provider-key', 'input');
  page.change('model-provider', 'provider-two');
  assert.equal(page.$('model-api-key').value, '');
  assert.match(page.$('model-key-help').textContent, /重新填写|新.*密钥|对应.*API Key/);
  page.change('model-id', 'model-other', 'input');
  page.submit();
  assert.ok(!page.requests.some((request) => request.method === 'PUT'));
  page.change('model-api-key', 'synthetic-new-provider-key', 'input');
  page.submit();
  await page.saved();
  const put = page.requests.find((request) => request.method === 'PUT');
  assert.equal(JSON.parse(put.body).api_key, 'synthetic-new-provider-key');
  assert.equal(page.$('model-api-key').value, '');
  assert.ok(!page.dom.serialize().includes('synthetic-new-provider-key'));
  assert.equal(page.dom.window.localStorage.length, 0);
  assert.equal(page.dom.window.sessionStorage.length, 0);
});

test('custom services send explicit HTTPS endpoint, model and supported protocol', async (t) => {
  const page = await openPage(t);
  page.change('model-mode-custom', true);
  page.change('model-custom-provider', 'own-service', 'input');
  page.change('model-base-url', 'https://models.example.com/v1', 'input');
  page.change('model-id', 'custom-model', 'input');
  page.change('model-api-key', 'synthetic-custom-key', 'input');
  page.submit();
  await page.saved();
  const body = JSON.parse(page.requests.find((request) => request.method === 'PUT').body);
  assert.deepEqual(body, {revision: 1, mode: 'custom', provider: 'own-service', model: 'custom-model',
                         base_url: 'https://models.example.com/v1', api: 'openai-completions', api_key: 'synthetic-custom-key'});
});

test('a disabled model manager explains its state and does not fetch the catalog', async (t) => {
  const page = await openPage(t, {settings: {enabled: false}});
  assert.equal(page.$('model-form').hidden, true);
  assert.equal(page.$('model-unavailable').hidden, false);
  assert.match(page.$('model-unavailable').textContent, /Codex/);
  assert.ok(!page.requests.some((request) => request.path === '/model-catalog'));
  assert.equal(page.$('preferences-form').hidden, false);
});

test('conflict recovery preserves the chosen fields but clears the key and uses latest revision', async (t) => {
  const page = await openPage(t);
  page.change('model-id', 'model-two', 'input');
  page.change('model-api-key', 'synthetic-unsaved-key', 'input');
  page.backend.putStatus = 409;
  page.submit();
  await settle(() => !page.$('model-merge-latest').hidden);
  assert.equal(page.$('model-api-key').value, '');
  assert.equal(page.$('model-id').value, 'model-two');
  assert.equal(page.$('model-save').disabled, true);
  page.backend.settings.revision = 7;
  page.$('model-merge-latest').click();
  await settle(() => page.$('model-merge-latest').hidden && !page.$('model-save').disabled);
  assert.equal(page.$('model-id').value, 'model-two');
  page.backend.putStatus = 200;
  page.submit();
  await page.saved();
  const puts = page.requests.filter((request) => request.method === 'PUT');
  assert.equal(JSON.parse(puts[1].body).revision, 7);
});

test('an unconfigured service starts empty and requires a catalog model plus key', async (t) => {
  const page = await openPage(t, {settings: {...settings, revision: 0, configured: false, provider: '', model: '', api_key_configured: false, source: 'unconfigured'}});
  assert.equal(page.$('model-provider').value, '');
  assert.equal(page.$('model-api-key').required, true);
  assert.match(page.$('model-message').textContent, /还没有配置/);
  page.change('model-provider', 'provider-one');
  page.change('model-id', 'not-in-directory', 'input');
  page.change('model-api-key', 'synthetic-first-key', 'input');
  page.submit();
  assert.ok(!page.requests.some((request) => request.method === 'PUT'));
  assert.match(page.$('model-message').textContent, /目录/);
  page.change('model-id', 'model-one', 'input');
  page.submit();
  await page.saved();
  assert.equal(JSON.parse(page.requests.find((request) => request.method === 'PUT').body).revision, 0);
});

test('authentication expiry clears the key while retaining non-secret edits after re-login', async (t) => {
  const page = await openPage(t);
  page.change('model-id', 'model-two', 'input');
  page.change('model-api-key', 'synthetic-unsaved-private-key', 'input');
  page.backend.loggedIn = false;
  page.submit();
  await settle(() => !page.$('lock-screen').hidden);
  assert.equal(page.$('model-api-key').value, '');
  assert.equal(page.$('model-id').value, '');
  assert.equal(page.$('model-form').hidden, true);
  page.$('management-token').value = 'synthetic-management-token';
  page.$('login-form').dispatchEvent(new page.dom.window.Event('submit', {bubbles: true, cancelable: true}));
  await settle(() => !page.$('model-form').hidden && page.$('model-panel').getAttribute('aria-busy') === 'false');
  assert.equal(page.$('model-id').value, 'model-two');
  assert.equal(page.$('model-api-key').value, '');
});

test('logout clears the key even when the logout request fails', async (t) => {
  const page = await openPage(t, {intercept: ({path, method}) => path === '/session' && method === 'DELETE' ? {status: 500} : null});
  page.change('model-api-key', 'synthetic-private-key', 'input');
  page.$('logout').click();
  await settle(() => /退出未完成/.test(page.$('notice-text').textContent));
  assert.equal(page.$('model-api-key').value, '');
  assert.equal(page.dom.window.localStorage.length + page.dom.window.sessionStorage.length, 0);
});

test('leaving the page clears a typed provider key', async (t) => {
  const page = await openPage(t);
  page.change('model-api-key', 'synthetic-pagehide-key', 'input');
  page.dom.window.dispatchEvent(new page.dom.window.Event('pagehide'));
  assert.equal(page.$('model-api-key').value, '');
});

test('a catalog failure is retryable without blocking the existing preferences form', async (t) => {
  let failCatalog = true;
  const page = await openPage(t, {intercept: ({path}) => path === '/model-catalog' && failCatalog ? {status: 503} : null});
  assert.equal(page.$('model-form').hidden, true);
  assert.equal(page.$('model-retry').hidden, false);
  assert.equal(page.$('save-button').disabled, false);
  failCatalog = false;
  page.$('model-retry').click();
  await settle(() => !page.$('model-form').hidden && page.$('model-panel').getAttribute('aria-busy') === 'false');
  assert.equal(page.$('model-retry').hidden, true);
});

test('directory names are displayed as text, never executed as HTML', async (t) => {
  const malicious = clone(catalog);
  malicious.providers[0].name = '<img src=x onerror="throw new Error(1)">';
  malicious.providers[0].models[0].name = '<script>throw new Error(2)</script>';
  const page = await openPage(t, {catalog: malicious});
  assert.equal(page.dom.window.document.querySelectorAll('#model-panel img, #model-panel script').length, 0);
  assert.match(page.$('model-provider').options[1].textContent, /<img/);
});

test('provider-specific credential names and requirements come from the directory', async (t) => {
  const special = clone(catalog);
  special.providers[1].credential_label = 'Service access token';
  special.providers[1].notes = 'Use a service token; an ordinary account key cannot be substituted. <b>text only</b>';
  const page = await openPage(t, {catalog: special});
  page.change('model-provider', 'provider-two');
  assert.equal(page.$('model-key-label').textContent, 'Service access token');
  assert.equal(page.$('model-provider-notes').textContent, special.providers[1].notes);
  assert.equal(page.$('model-provider-notes').hidden, false);
  assert.equal(page.$('model-provider-notes').querySelector('b'), null);
  page.change('model-mode-custom', true);
  assert.equal(page.$('model-key-label').textContent, 'API Key');
  assert.equal(page.$('model-provider-notes').hidden, true);
});

for (const base of ['http://models.example.com/v1', 'https://user:private@models.example.com/v1',
                    'https://models.example.com/v1?api_key=private', 'https://models.example.com/v1#private']) {
  test(`custom endpoint validation rejects credential-bearing or non-HTTPS URL: ${new URL(base).protocol}`, async (t) => {
    const page = await openPage(t);
    page.change('model-mode-custom', true);
    page.change('model-custom-provider', 'own-service', 'input');
    page.change('model-base-url', base, 'input');
    page.change('model-id', 'custom-model', 'input');
    page.change('model-api-key', 'synthetic-key', 'input');
    page.submit();
    assert.ok(!page.requests.some((request) => request.method === 'PUT'));
    assert.ok(!page.$('model-message').textContent.includes(base));
  });
}

test('API validation failures never echo response diagnostics that may contain a key', async (t) => {
  const page = await openPage(t, {intercept: ({path, method}) => path === '/model-settings' && method === 'PUT'
    ? {status: 422, body: {detail: 'synthetic-secret-key-do-not-display'}} : null});
  page.change('model-api-key', 'synthetic-secret-key-do-not-display', 'input');
  page.submit();
  await settle(() => /保存未完成/.test(page.$('model-message').textContent));
  assert.ok(!page.dom.window.document.body.textContent.includes('synthetic-secret-key-do-not-display'));
});

test('an enabled model manager save failure does not claim the feature is disabled', async (t) => {
  const page = await openPage(t);
  page.backend.putStatus = 503;
  page.change('model-id', 'model-two', 'input');
  page.submit();
  await settle(() => !page.$('model-save').disabled);
  assert.match(page.$('model-message').textContent, /存储目录权限/);
  assert.ok(!page.$('model-message').textContent.includes('未启用'));
});

test('a model-save response cannot reopen settings after a concurrent authentication failure', async (t) => {
  let finishSave;
  const page = await openPage(t, {intercept: ({path, method}) => path === '/model-settings' && method === 'PUT'
    ? new Promise((resolve) => { finishSave = () => resolve({status: 200, body: {...settings, revision: 2}}); }) : null});
  page.change('model-api-key', 'synthetic-inflight-key', 'input');
  page.submit();
  await settle(() => Boolean(finishSave));
  page.backend.loggedIn = false;
  page.$('refresh-status').click();
  await settle(() => !page.$('lock-screen').hidden);
  finishSave();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(page.$('settings-screen').hidden, true);
  assert.equal(page.$('model-form').hidden, true);
  assert.equal(page.$('model-api-key').value, '');
});
