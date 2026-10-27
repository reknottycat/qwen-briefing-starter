const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const code = fs.readFileSync(path.join(__dirname, '..', 'cloud-gmail', 'Code.gs'), 'utf8');
const sourcePolicy = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'briefing', 'source_policy.json'), 'utf8'));
const defaultSubject = '[每日早报][' + sourcePolicy.sources[0].name + '][2026-10-26]';
const eventMarker = '早报事件类型：' + sourcePolicy.sources[0].event_type;
const completed = text => eventMarker + '\n' + text;
const NOW = Date.parse('2026-10-27T01:00:00Z');
const encode = text => Buffer.from(text).toString('base64url');

function mail(id, options = {}) {
  return {
    id, internalDate: String(NOW - 3600000), snippet: 'THIS IS ONLY A SNIPPET',
    payload: options.payload || {
      mimeType: 'text/plain',
      headers: [{name: 'Subject', value: options.subject || defaultSubject}],
      body: {data: encode(options.body ?? completed('  完整正文。保留开头空格与结尾换行。\n'))},
    },
  };
}

function setup(options = {}) {
  const values = {
    BRIEFING_BASE_URL: 'https://briefing.example.com/briefing/v1',
    BRIEFING_INGEST_TOKEN: 'test-ingest-token-no-real-secret',
    BRIEFING_SOURCE_POLICY: JSON.stringify(sourcePolicy),
    EXPECTED_ACCOUNT: 'you@example.com',
    ...(options.properties || {}),
  };
  const messages = options.messages || [mail('mail01')];
  const byId = Object.fromEntries(messages.map(message => [message.id, message]));
  const calls = {fetch: [], list: [], get: [], attachments: [], logs: [], releases: 0};
  const triggers = [...(options.triggers || [])];
  const properties = {
    getProperty: key => values[key] ?? null,
    setProperty: (key, value) => {
      if (Buffer.byteLength(String(value), 'utf8') > 9 * 1024) throw new Error('Property value exceeds 9 KB');
      values[key] = String(value);
    },
    setProperties: entries => Object.entries(entries).forEach(([key, value]) => properties.setProperty(key, value)),
    getProperties: () => ({...values}),
    deleteProperty: key => { delete values[key]; },
  };
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [NOW])); }
    static now() { return NOW; }
  }
  const context = vm.createContext({
    Date: Clock,
    console: {log: (...args) => calls.logs.push(args.join(' '))},
    LockService: {getScriptLock: () => ({tryLock: () => options.locked !== true, releaseLock: () => { calls.releases++; }})},
    PropertiesService: {getScriptProperties: () => properties},
    Utilities: {
      base64Decode: data => {
        if (options.decodeFailure) throw new Error('Underlying decoder failure');
        if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(data)) {
          throw new Error('Strict decoder requires standard alphabet and padding');
        }
        return Buffer.from(data, 'base64');
      },
      newBlob: input => ({getDataAsString: () => Buffer.from(input).toString('utf8'), getBytes: () => [...Buffer.from(input)]}),
    },
    Gmail: {Users: {
      getProfile: () => ({emailAddress: options.account || 'you@example.com'}),
      Messages: {
        list: (user, args) => {
          calls.list.push({user, ...args});
          if (options.pages) return options.pages[args.pageToken || 'first'];
          return {messages: messages.map(message => ({id: message.id}))};
        },
        get: (user, id, args) => { calls.get.push({user, id, ...args}); return byId[id]; },
        Attachments: {get: (user, id, attachmentId) => {
          calls.attachments.push({user, id, attachmentId});
          const body = options.attachments?.[attachmentId];
          if (body === undefined) throw new Error('Unknown MIME body attachment');
          return {data: encode(body), size: Buffer.byteLength(body)};
        }},
      },
    }},
    UrlFetchApp: {fetch: (url, request) => {
      calls.fetch.push({url, ...request, parsed: JSON.parse(request.payload)});
      if (options.fetchError) throw new Error('Network unavailable');
      const status = typeof options.httpStatus === 'function' ? options.httpStatus(JSON.parse(request.payload).messages[0]) : options.httpStatus || 200;
      return {getResponseCode: () => status, getContentText: () => JSON.stringify({status: 'accepted', accepted: 1})};
    }},
    ScriptApp: {
      getProjectTriggers: () => triggers,
      newTrigger: handler => ({timeBased: () => ({everyMinutes: minutes => ({create: () => {
        const trigger = {getHandlerFunction: () => handler, minutes};
        triggers.push(trigger);
        return trigger;
      }})})}),
      deleteTrigger: trigger => { triggers.splice(triggers.indexOf(trigger), 1); },
    },
  });
  vm.runInContext(code, context);
  return {context, calls, values, triggers};
}

test('sends full original plaintext, source identity, and safe HTTP options', () => {
  const original = completed('  完整的第一段。\n第二段包括金额100元，尚未确认。\n');
  const env = setup({messages: [mail('a01', {body: original})]});
  env.context.collectBriefings();
  assert.equal(env.calls.fetch.length, 1);
  const request = env.calls.fetch[0];
  const actual = request.parsed.messages[0];
  assert.equal(actual.body, original);
  assert.equal(actual.id, 'a01');
  assert.equal(actual.subject, defaultSubject);
  assert.equal(actual.received_at, '2026-10-27T00:00:00.000Z');
  assert.match(actual.source_url, /^https:\/\/mail\.google\.com\//);
  assert.equal(request.followRedirects, false);
  assert.equal(request.muteHttpExceptions, true);
  assert.equal(request.url, 'https://briefing.example.com/briefing/v1/ingest');
  assert.ok(!env.calls.logs.join('').includes(original));
  assert.equal(env.calls.releases, 1);
});

test('configured Gmail identity controls account validation and source links', () => {
  const env = setup({account: 'Different+Reports@Example.com',
    properties: {EXPECTED_ACCOUNT: ' Different+Reports@Example.com '}});
  env.context.collectBriefings();
  assert.equal(env.calls.fetch[0].parsed.messages[0].source_url,
    'https://mail.google.com/mail/u/?authuser=different%2Breports%40example.com#all/mail01');
});

test('missing or invalid expected account fails before reading any messages', () => {
  for (const account of [null, '', 'missing-domain', 'person @example.com']) {
    const env = setup({properties: {EXPECTED_ACCOUNT: account}});
    assert.throws(() => env.context.collectBriefings(), /EXPECTED_ACCOUNT/);
    assert.equal(env.calls.list.length, 0);
    assert.equal(env.calls.get.length, 0);
    assert.equal(env.calls.fetch.length, 0);
  }
});

test('only unified reports enter the collector and Internal Sample is excluded in either title format', () => {
  const env = setup({messages: [
    mail('valid'), mail('legacy', {subject: 'Internal Sample 项目日报｜2026-10-26'}),
    mail('legacy-ascii', {subject: 'Internal Sample 项目日报|2026-10-26'}),
    mail('excluded-unified', {subject: '[每日早报][Internal Sample 项目日报][2026-10-26]'}),
    mail('excluded-spaces', {subject: '[每日早报][ Internal Sample 项目日报 ][2026-10-26]'}),
    mail('excluded-case', {subject: '[每日早报][PERSONAL  EVOLUTOR\t项目日报][2026-10-26]'}),
    mail('unrelated', {subject: '旅行确认'}),
    mail('prefix', {subject: 'Re: ' + defaultSubject}),
    mail('suffix', {subject: defaultSubject + ' 其他'}),
    mail('unselected', {subject: '[每日早报][自复制 AI 开源复现追踪][2026-10-26]'}),
    mail('unknown', {subject: '[每日早报][未选择的任务][2026-10-26]'}),
  ]});
  assert.equal(env.context.collectBriefings().status, 'ok');
  assert.deepEqual(env.calls.fetch.map(call => call.parsed.messages[0].id), ['valid']);
  assert.equal(env.calls.list[0].q, 'newer_than:7d subject:"[每日早报]"');
  for (const id of ['legacy', 'legacy-ascii', 'excluded-unified', 'excluded-spaces', 'excluded-case']) {
    assert.equal(env.values['SEEN_' + id], undefined);
  }
});

test('all selected sources use the canonical policy and their mandatory event marker', () => {
  assert.equal(sourcePolicy.sources.length, 10);
  const messages = sourcePolicy.sources.map((source, index) => mail('selected' + index, {
    subject: '[每日早报][' + source.name + '][2026-10-26]',
    body: '早报事件类型：' + source.event_type + '\r\n合成的已整理正文。',
  }));
  const env = setup({messages});
  assert.equal(env.context.collectBriefings().status, 'ok');
  assert.deepEqual(env.calls.fetch.map(call => call.parsed.messages[0].id), messages.map(message => message.id));
});

test('canonical narration details keep standard defaults and the three selected overrides', () => {
  assert.deepEqual(Object.fromEntries(sourcePolicy.sources.map(source => [source.name, source.narration_detail])), {
    '每日资讯': 'standard', '市场观察': 'standard', '每日知识练习': 'detailed',
    '生活提示': 'simple', '外语练习': 'detailed',
    '技术周报': 'standard', '每周学习复盘': 'standard',
    '项目邮件回复': 'standard', '客户邮件回复': 'standard', '活动动态': 'standard',
  });
});

test('all narration details preserve the complete collected body and original report date', () => {
  const original = eventMarker + '\r\n  ' + '完整教学内容及必要限定不能在采集时删改。'.repeat(90) +
    '\r\nРусский оригинал: Как вы это понимаете?\r\n  最后一段保留空格与结尾换行。\r\n';
  const subject = '[每日早报][' + sourcePolicy.sources[0].name + '][2026-10-23]';
  for (const narration_detail of ['simple', 'standard', 'detailed']) {
    const policy = JSON.parse(JSON.stringify(sourcePolicy));
    policy.sources[0].narration_detail = narration_detail;
    const env = setup({properties: {BRIEFING_SOURCE_POLICY: JSON.stringify(policy)},
      messages: [mail('detail-' + narration_detail, {subject, body: original})]});
    const result = env.context.collectBriefings();
    assert.equal(result.status, 'ok');
    assert.equal(result.ingested, 1);
    assert.equal(env.calls.fetch.length, 1);
    const actual = env.calls.fetch[0].parsed.messages[0];
    assert.equal(actual.body, original);
    assert.equal(actual.subject, subject);
    assert.equal(actual.received_at, '2026-10-27T00:00:00.000Z');
  }
});

test('missing or invalid narration detail and extra source fields fail before mailbox access', () => {
  const mutations = [
    source => { delete source.narration_detail; },
    ...['', 'brief', 'Detailed', ' standard ', null, true, 1, [], {}].map(value =>
      source => { source.narration_detail = value; }),
    source => { source.narration_detail_override = 'simple'; },
  ];
  for (const mutate of mutations) {
    const policy = JSON.parse(JSON.stringify(sourcePolicy));
    mutate(policy.sources[policy.sources.length - 1]);
    const env = setup({properties: {BRIEFING_SOURCE_POLICY: JSON.stringify(policy)}});
    assert.throws(() => env.context.collectBriefings(), /BRIEFING_SOURCE_POLICY/);
    assert.equal(env.calls.list.length, 0);
    assert.equal(env.calls.get.length, 0);
    assert.equal(env.calls.fetch.length, 0);
    assert.equal(env.values.LAST_RUN_STATUS, 'failed');
  }
});

test('both selected weekly sources require completed scheduled results and preserve their report date', () => {
  const weeklySources = sourcePolicy.sources.filter(source => source.kind === 'weekly');
  assert.deepEqual(weeklySources.map(source => source.name), ['技术周报', '每周学习复盘']);
  const messages = [];
  for (const [index, source] of weeklySources.entries()) {
    const subject = '[每日早报][' + source.name + '][2026-10-23]';
    for (const [variant, body] of ['没有标记的周报。', '早报事件类型：新回复\n合成正文。',
      '早报事件类型：新信息\n合成正文。', '早报事件类型：定时结果\n \t'].entries()) {
      messages.push(mail('weekly-invalid-' + index + '-' + variant, {subject, body}));
    }
    messages.push(mail('weekly-valid-' + index, {subject, body: completed('已完成的合成周报全文。')}));
  }
  const env = setup({messages});
  const result = env.context.collectBriefings();
  assert.equal(result.status, 'partial');
  assert.equal(result.failed, 8);
  assert.deepEqual(env.calls.fetch.map(call => call.parsed.messages[0].id), ['weekly-valid-0', 'weekly-valid-1']);
  for (const call of env.calls.fetch) assert.ok(call.parsed.messages[0].subject.endsWith('[2026-10-23]'));
  for (const message of messages.filter(message => message.id.startsWith('weekly-invalid-'))) {
    assert.equal(env.values['SEEN_' + message.id], undefined);
  }
});

test('invalid weekly events and unknown frequencies fail before mailbox access', () => {
  for (const [kind, event_type] of [['weekly', '新回复'], ['weekly', '新信息'], ['weekly', ''],
    ['monthly', '定时结果'], ['conditional', '定时结果']]) {
    const invalidPolicy = JSON.parse(JSON.stringify(sourcePolicy));
    invalidPolicy.sources[0] = {...invalidPolicy.sources[0], kind, event_type};
    const env = setup({properties: {BRIEFING_SOURCE_POLICY: JSON.stringify(invalidPolicy)}});
    assert.throws(() => env.context.collectBriefings(), /BRIEFING_SOURCE_POLICY/);
    assert.equal(env.calls.list.length, 0);
    assert.equal(env.calls.get.length, 0);
    assert.equal(env.calls.fetch.length, 0);
    assert.equal(env.values.LAST_RUN_STATUS, 'failed');
  }
});

test('wrong or absent event markers cannot enter, and later valid messages still enter', () => {
  const invalid = ['没有标记的合成正文。', '早报事件类型:定时结果\n合成正文。', '\n' + completed('合成正文。'),
    '早报事件类型：新回复\n合成正文。', eventMarker, eventMarker + '\r\n \r\n\t', eventMarker + ' \n合成正文。'];
  const messages = invalid.map((body, index) => mail('invalid' + index, {body}));
  for (const [index, source] of sourcePolicy.sources.filter(source => source.kind === 'conditional').entries()) {
    messages.push(mail('conditional' + index, {subject: '[每日早报][' + source.name + '][2026-10-26]', body: completed('无更新的合成检查记录。')}));
  }
  messages.push(mail('good'));
  const env = setup({messages});
  const result = env.context.collectBriefings();
  assert.equal(result.status, 'partial');
  assert.equal(result.failed, messages.length - 1);
  assert.deepEqual(env.calls.fetch.map(call => call.parsed.messages[0].id), ['good']);
  for (const message of messages.slice(0, -1)) assert.equal(env.values['SEEN_' + message.id], undefined);
});

test('missing, malformed, or unsupported source policy prevents any mailbox read or delivery', () => {
  for (const raw of [null, '', '{bad json', JSON.stringify({...sourcePolicy, version: 2}),
    JSON.stringify({...sourcePolicy, sources: []}),
    JSON.stringify({...sourcePolicy, sources: [...sourcePolicy.sources, sourcePolicy.sources[0]]})]) {
    const env = setup({properties: {BRIEFING_SOURCE_POLICY: raw}});
    assert.throws(() => env.context.collectBriefings(), /BRIEFING_SOURCE_POLICY/);
    assert.equal(env.calls.list.length, 0);
    assert.equal(env.calls.get.length, 0);
    assert.equal(env.calls.fetch.length, 0);
    assert.equal(env.values.LAST_RUN_STATUS, 'failed');
  }
});

test('account mismatch and a held lock never send data', () => {
  const mismatch = setup({account: 'somebody-else@example.com'});
  assert.throws(() => mismatch.context.collectBriefings(), /Unexpected Gmail account/);
  assert.equal(mismatch.calls.fetch.length, 0);
  assert.equal(mismatch.calls.releases, 1);
  const held = setup({locked: true});
  held.context.collectBriefings();
  assert.equal(held.calls.list.length, 0);
  assert.equal(held.calls.fetch.length, 0);
});

test('repeated collection uses successful receipts to avoid another POST', () => {
  const env = setup();
  env.context.collectBriefings();
  env.context.collectBriefings();
  assert.equal(env.calls.fetch.length, 1);
  assert.equal(env.values.LAST_INGESTED_COUNT, '0');
});

for (const status of [401, 403, 500]) {
  test(`HTTP ${status} does not mark the message processed or the run successful`, () => {
    const env = setup({httpStatus: status, messages: [mail('mail01'), mail('later')]});
    assert.throws(() => env.context.collectBriefings(), new RegExp(`HTTP ${status}`));
    assert.equal(env.values.SEEN_mail01, undefined);
    assert.equal(env.values.LAST_SUCCESS_AT, undefined);
    assert.equal(env.values.LAST_RUN_STATUS, 'failed');
    assert.equal(env.calls.get.length, 1);
    assert.equal(env.calls.releases, 1);
  });
}

test('transport errors remain fatal and do not install a collector or skip ahead', () => {
  const env = setup({messages: [mail('first'), mail('later')], fetchError: true});
  assert.throws(() => env.context.installCollector(), /Network unavailable/);
  assert.equal(env.calls.get.length, 1);
  assert.equal(env.values.SEEN_first, undefined);
  assert.equal(env.values.LAST_RUN_STATUS, 'failed');
  assert.equal(env.triggers.length, 0);
  assert.equal(env.calls.releases, 1);
});

test('a successful earlier message is not redelivered after a later bad body', () => {
  const env = setup({messages: [mail('good'), mail('empty', {body: ''})]});
  assert.equal(env.context.collectBriefings().status, 'partial');
  assert.ok(env.values.SEEN_good);
  assert.equal(env.context.collectBriefings().status, 'partial');
  assert.equal(env.calls.fetch.length, 1);
});

test('oversize input fails explicitly rather than truncating or marking processed', () => {
  const env = setup({messages: [mail('long', {body: '字'.repeat(80001)})]});
  assert.equal(env.context.collectBriefings().status, 'partial');
  assert.deepEqual(JSON.parse(env.values.LAST_FAILED_MESSAGES), [{id: 'long', code: 'body_too_large'}]);
  assert.equal(env.calls.fetch.length, 0);
  assert.equal(env.values.SEEN_long, undefined);
});

test('HTML-only messages retain readable content and source links', () => {
  const source = mail('html', {payload: {mimeType: 'text/html', headers: [{name: 'Subject', value: defaultSubject}],
    body: {data: encode('<p>' + eventMarker + '</p><style>hidden style</style><p>价格：100&nbsp;元，尚待确认。</p><p><a href="https://example.com/news">原始来源</a></p>')}}});
  const env = setup({messages: [source]});
  env.context.collectBriefings();
  const body = env.calls.fetch[0].parsed.messages[0].body;
  assert.match(body, /100 元/);
  assert.match(body, /原始来源 \(https:\/\/example.com\/news\)/);
  assert.ok(!body.includes('hidden style'));
});

test('MIME alternatives prefer full plaintext and skip attached files', () => {
  const source = mail('mime', {payload: {mimeType: 'multipart/mixed', headers: [{name: 'Subject', value: defaultSubject}], parts: [
    {mimeType: 'multipart/alternative', parts: [{mimeType: 'text/plain', body: {data: encode(completed('完整原文'))}}, {mimeType: 'text/html', body: {data: encode('<p>同一原文</p>')}}]},
    {mimeType: 'text/plain', filename: 'notes.txt', body: {data: encode('附件内容不应收录')}},
  ]}});
  const env = setup({messages: [source]});
  env.context.collectBriefings();
  assert.equal(env.calls.fetch[0].parsed.messages[0].body, completed('完整原文'));
});

test('pages are followed and repeated installs create only one trigger', () => {
  const env = setup({messages: [mail('a'), mail('b')], pages: {
    first: {messages: [{id: 'a'}], nextPageToken: 'second'}, second: {messages: [{id: 'b'}]},
  }});
  env.context.installCollector();
  env.context.installCollector();
  assert.deepEqual(env.calls.fetch.map(call => call.parsed.messages[0].id), ['a', 'b']);
  assert.equal(env.triggers.length, 1);
  assert.equal(env.triggers[0].minutes, 15);
  assert.equal(env.calls.list[1].pageToken, 'second');
});

test('a run delivers at most 40 messages and resumes remaining messages next time', () => {
  const env = setup({messages: Array.from({length: 45}, (_, i) => mail('mail' + i))});
  env.context.collectBriefings();
  assert.equal(env.calls.fetch.length, 40);
  env.context.collectBriefings();
  assert.equal(env.calls.fetch.length, 45);
});

test('processed receipt storage stays below the 9 KB per-property limit', () => {
  const seen = Object.fromEntries(Array.from({length: 2000}, (_, i) => ['SEEN_id' + String(i).padStart(16, '0'), String(NOW - 1000 - i)]));
  const env = setup({properties: seen, messages: Array.from({length: 40}, (_, i) => mail('new' + String(i).padStart(16, '0')))});
  assert.doesNotThrow(() => env.context.collectBriefings());
  assert.equal(env.calls.fetch.length, 40);
  const receipts = Object.entries(env.values).filter(([key]) => key.startsWith('SEEN_'));
  assert.equal(receipts.length, 2000);
  assert.ok(receipts.every(([, value]) => Buffer.byteLength(value) <= 9 * 1024));
  assert.ok(env.values.SEEN_new0000000000000039);
});

test('expiry and capacity cleanup preserve unrelated properties and legacy state', () => {
  const env = setup({properties: {SEEN_expired: String(NOW - 9 * 86400000), SEEN_recent: String(NOW - 1000),
    SEEN_invalid: 'invalid', ANOTHER_PROGRAM: 'keep', PROCESSED_IDS: '{"legacy":1}'}});
  env.context.collectBriefings();
  assert.equal(env.values.SEEN_expired, undefined);
  assert.equal(env.values.SEEN_invalid, undefined);
  assert.equal(env.values.SEEN_recent, String(NOW - 1000));
  assert.equal(env.values.ANOTHER_PROGRAM, 'keep');
  assert.equal(env.values.PROCESSED_IDS, '{"legacy":1}');
  assert.equal(env.values.BRIEFING_INGEST_TOKEN, 'test-ingest-token-no-real-secret');
});

test('a MIME body stored by attachmentId is read completely, without file attachments', () => {
  const body = completed('完整长正文：' + '资料。'.repeat(100));
  const source = mail('longmime', {payload: {mimeType: 'text/plain', headers: [{name: 'Subject', value: defaultSubject}], body: {attachmentId: 'body-part', size: Buffer.byteLength(body)}}});
  const env = setup({messages: [source], attachments: {'body-part': body}});
  env.context.collectBriefings();
  assert.equal(env.calls.fetch[0].parsed.messages[0].body, body);
  assert.deepEqual(env.calls.attachments, [{user: 'me', id: 'longmime', attachmentId: 'body-part'}]);
});

test('strict MIME decoder supports both alphabets with optional complete padding', () => {
  const env = setup();
  // These UTF-8 strings cover both special alphabet characters and padding lengths.
  for (const original of ['a', 'ab', 'abc', '中文正文。\n', '😀🿿']) {
    const standard = Buffer.from(original).toString('base64');
    const websafe = standard.replace(/\+/g, '-').replace(/\//g, '_');
    for (const encoded of [standard, websafe, standard.replace(/=+$/, ''), websafe.replace(/=+$/, '')]) {
      assert.equal(env.context.decodeBodyData_(encoded, 'encoded-mail'), original);
    }
  }
  assert.equal(env.context.decodeBodyData_(' Y W\r\nJj\t ', 'wrapped-mail'), 'abc');
});

test('advanced-service signed or unsigned Byte arrays are already decoded, without body logging', () => {
  const original = completed('完整字节正文。emoji 😀\n');
  const unsigned = [...Buffer.from(original, 'utf8')];
  for (const bytes of [unsigned, unsigned.map(value => value > 127 ? value - 256 : value)]) {
    const source = mail('byte-array');
    source.payload.body.data = bytes;
    const env = setup({messages: [source], decodeFailure: true});
    env.context.collectBriefings();
    assert.equal(env.calls.fetch[0].parsed.messages[0].body, original);
    const diagnostic = JSON.parse(env.calls.logs[0].replace('MIME body representation: ', ''));
    assert.deepEqual(diagnostic, {type: 'object', is_array: true, object_type: '[object Array]', length: bytes.length});
    assert.ok(!env.calls.logs.join('').includes(original));
    assert.ok(env.values['SEEN_byte-array']);
  }
});

test('an empty Byte array plain alternative does not hide a complete HTML body', () => {
  const source = mail('empty-alternative', {payload: {mimeType: 'multipart/alternative',
    headers: [{name: 'Subject', value: defaultSubject}], parts: [
      {mimeType: 'text/plain', body: {data: []}},
      {mimeType: 'text/html', body: {data: [...Buffer.from('<p>' + eventMarker + '</p><p>完整HTML正文</p>')]}}
    ]}});
  const env = setup({messages: [source]});
  assert.equal(env.context.collectBriefings().status, 'ok');
  assert.equal(env.calls.fetch[0].parsed.messages[0].body, completed('完整HTML正文'));
});

test('malformed MIME encoding fails without delivering or acknowledging the message', () => {
  for (const encoded of ['a', 'YWJj$', 'Y=Q=', 'YQ===', 'YQ=', 'YWJj=', 'YQ==garbage', ['YQ=='], [256], [-129], [1.5], new Array(2), {length: 3}]) {
    const source = mail('invalid');
    source.payload.body.data = encoded;
    const env = setup({messages: [source]});
    assert.equal(env.context.collectBriefings().status, 'partial');
    assert.deepEqual(JSON.parse(env.values.LAST_FAILED_MESSAGES), [{id: 'invalid', code: 'invalid_encoding'}]);
    assert.equal(env.calls.fetch.length, 0);
    assert.equal(env.values.SEEN_invalid, undefined);
    assert.equal(env.values.LAST_SUCCESS_AT, undefined);
    assert.equal(env.calls.releases, 1);
  }
});

test('underlying MIME decoder errors remain explicit and never mark delivery complete', () => {
  const env = setup({decodeFailure: true});
  assert.equal(env.context.collectBriefings().status, 'partial');
  assert.deepEqual(JSON.parse(env.values.LAST_FAILED_MESSAGES), [{id: 'mail01', code: 'decode_failed'}]);
  assert.equal(env.calls.fetch.length, 0);
  assert.equal(env.values.SEEN_mail01, undefined);
  assert.equal(env.values.LAST_SUCCESS_AT, undefined);
});

test('bad bodies and dates at the front do not block good mail or trigger installation', () => {
  const invalidDate = mail('invalid-date');
  invalidDate.internalDate = 'not-a-date';
  const env = setup({messages: [mail('empty', {body: ''}), invalidDate, mail('good')]});
  env.context.installCollector();
  assert.deepEqual(env.calls.fetch.map(call => call.parsed.messages[0].id), ['good']);
  assert.ok(env.values.SEEN_good);
  assert.equal(env.values.SEEN_empty, undefined);
  assert.equal(env.values['SEEN_invalid-date'], undefined);
  assert.equal(env.values.LAST_RUN_STATUS, 'partial');
  assert.equal(env.values.LAST_FAILED_COUNT, '2');
  assert.equal(env.values.LAST_SUCCESS_AT, undefined);
  assert.equal(env.triggers.length, 1);
  assert.deepEqual(JSON.parse(env.values.LAST_FAILED_MESSAGES), [
    {id: 'empty', code: 'empty_body'}, {id: 'invalid-date', code: 'invalid_received_date'}]);
  env.context.collectorStatus();
  assert.equal(JSON.parse(env.calls.logs.at(-1)).last_run_status, 'partial');
  env.context.collectBriefings();
  assert.equal(env.calls.get.filter(call => call.id === 'empty').length, 2);
  assert.equal(env.calls.fetch.length, 1);
});

for (const status of [409, 422]) {
  test(`message HTTP ${status} is visible, retried later, and cannot block following mail`, () => {
    let repaired = false;
    const env = setup({messages: [mail('bad'), mail('good')], httpStatus: message => message.id === 'bad' && !repaired ? status : 200});
    assert.equal(env.context.collectBriefings().status, 'partial');
    assert.equal(env.values.SEEN_bad, undefined);
    assert.ok(env.values.SEEN_good);
    assert.equal(env.values.LAST_FAILED_COUNT, '1');
    assert.deepEqual(JSON.parse(env.values.LAST_FAILED_MESSAGES), [{id: 'bad', code: 'ingest_http_' + status}]);
    repaired = true;
    assert.equal(env.context.collectBriefings().status, 'ok');
    assert.ok(env.values.SEEN_bad);
    assert.deepEqual(env.calls.fetch.map(call => call.parsed.messages[0].id), ['bad', 'good', 'bad']);
    assert.equal(env.values.LAST_FAILED_COUNT, '0');
    assert.ok(env.values.LAST_SUCCESS_AT);
  });
}

test('repeated bad IDs and pagination are bounded, with capped failure diagnostics', () => {
  const messages = Array.from({length: 30}, (_, i) => mail('bad' + i, {body: ''}));
  const ids = messages.map(message => ({id: message.id}));
  const env = setup({messages, pages: {first: {messages: ids, nextPageToken: 'repeat'}, repeat: {messages: ids, nextPageToken: 'repeat'}}});
  const result = env.context.collectBriefings();
  assert.equal(result.status, 'partial');
  assert.equal(result.failed, 30);
  assert.equal(env.calls.get.length, 30);
  assert.equal(env.calls.list.length, 10);
  assert.equal(JSON.parse(env.values.LAST_FAILED_MESSAGES).length, 20);
  assert.equal(env.values.LAST_FAILURES_TRUNCATED, 'true');
  assert.ok(Buffer.byteLength(env.values.LAST_FAILED_MESSAGES) < 9000);
});

test('manifest asks only for readonly mail, outbound requests, and trigger control', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'cloud-gmail', 'appsscript.json'), 'utf8'));
  assert.deepEqual([...manifest.oauthScopes].sort(), [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/script.external_request',
    'https://www.googleapis.com/auth/script.scriptapp',
  ].sort());
  assert.equal(manifest.timeZone, 'Asia/Shanghai');
  assert.equal(manifest.dependencies.enabledAdvancedServices[0].serviceId, 'gmail');
});
