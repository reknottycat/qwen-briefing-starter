/** Cloud-only Gmail collector. Credentials live in Script Properties, not source. */
const BRIEFING_QUERY = 'newer_than:7d subject:"[每日早报]"';
const ALLOWED_SUBJECT = /^\[每日早报\]\[([^\[\]\r\n]{1,80})\]\[\d{4}-\d{2}-\d{2}\]$/;

function collectBriefings() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  let props;
  let processed = 0;
  let failed = 0;
  const failures = [];
  try {
    props = PropertiesService.getScriptProperties();
    const base = props.getProperty('BRIEFING_BASE_URL');
    const token = props.getProperty('BRIEFING_INGEST_TOKEN');
    if (!base || !base.startsWith('https://') || !token) throw new Error('Missing HTTPS endpoint or ingest credential');
    const sourceRules = sourcePolicy_(props);
    const expectedAccount = (props.getProperty('EXPECTED_ACCOUNT') || '').trim().toLowerCase();
    if (!/^[^@\s]+@[^@\s]+$/.test(expectedAccount)) throw new Error('Missing or invalid EXPECTED_ACCOUNT');
    if (Gmail.Users.getProfile('me').emailAddress.toLowerCase() !== expectedAccount) throw new Error('Unexpected Gmail account');
    const seen = {};
    const cutoff = Date.now() - 8 * 86400000;
    const saved = props.getProperties();
    Object.keys(saved).filter(key => key.startsWith('SEEN_')).forEach(key => {
      const timestamp = Number(saved[key]);
      if (!Number.isFinite(timestamp) || timestamp < cutoff) props.deleteProperty(key);
      else seen[key.slice(5)] = timestamp;
    });
    pruneSeen_(props, seen);
    let pageToken;
    let pages = 0;
    const attempted = new Set();
    do {
      const result = Gmail.Users.Messages.list('me', {q: BRIEFING_QUERY, maxResults: 100, pageToken: pageToken});
      for (const item of (result.messages || [])) {
        if (seen[item.id] || attempted.has(item.id)) continue;
        attempted.add(item.id);
        const msg = Gmail.Users.Messages.get('me', item.id, {format: 'full'});
        const subject = header_(msg.payload, 'Subject');
        const subjectMatch = ALLOWED_SUBJECT.exec(subject);
        if (!subjectMatch || !sourceRules.has(subjectMatch[1])) continue;
        try {
          const body = body_(msg.payload, item.id);
          if (!body.trim()) throw messageFailure_(item.id, 'empty_body');
          if (body.length > 80000) throw messageFailure_(item.id, 'body_too_large');
          const lines = body.split(/\r\n|\r|\n/);
          if (lines[0] !== '早报事件类型：' + sourceRules.get(subjectMatch[1]).event_type) {
            throw messageFailure_(item.id, 'event_marker_required');
          }
          if (!lines.slice(1).some(line => line.trim())) throw messageFailure_(item.id, 'event_body_required');
          const received = new Date(Number(msg.internalDate));
          if (!/^\d+$/.test(String(msg.internalDate)) || !Number.isFinite(received.getTime())) {
            throw messageFailure_(item.id, 'invalid_received_date');
          }
          const message = {id: item.id, subject: subject, received_at: received.toISOString(), body: body,
            source_url: 'https://mail.google.com/mail/u/?authuser=' + encodeURIComponent(expectedAccount) + '#all/' + item.id};
          const response = UrlFetchApp.fetch(base.replace(/\/$/, '') + '/ingest', {
            method: 'post', contentType: 'application/json', headers: {Authorization: 'Bearer ' + token},
            payload: JSON.stringify({messages: [message]}), muteHttpExceptions: true, followRedirects: false
          });
          const status = response.getResponseCode();
          if (status === 409 || status === 422) throw messageFailure_(item.id, 'ingest_http_' + status);
          if (status < 200 || status >= 300) throw new Error('Briefing ingest HTTP ' + status);
          seen[item.id] = Date.now();
          // Each receipt is tiny; an ever-growing JSON property hits Apps Script's 9 KB limit.
          props.setProperty('SEEN_' + item.id, String(seen[item.id]));
          pruneSeen_(props, seen);
          processed += 1;
        } catch (error) {
          if (!error.messageFailure) throw error;
          failed += 1;
          if (failures.length < 20) failures.push(error.messageFailure);
          console.log('Briefing message failed: ' + JSON.stringify(error.messageFailure));
        }
        if (processed >= 40) break;
      }
      pageToken = result.nextPageToken;
      pages += 1;
    } while (pageToken && pages < 10 && processed < 40);
    const status = failed ? 'partial' : 'ok';
    recordCollectorRun_(props, status, processed, failed, failures);
    console.log('Briefing collection ' + status + '; newly delivered: ' + processed + '; failed: ' + failed);
    return {status: status, ingested: processed, failed: failed};
  } catch (error) {
    if (props) {
      try { recordCollectorRun_(props, 'failed', processed, failed, failures); }
      catch (recordError) { /* Preserve the original operational error. */ }
    }
    throw error;
  } finally {
    lock.releaseLock();
  }
}

function sourcePolicy_(props) {
  let policy;
  try { policy = JSON.parse(props.getProperty('BRIEFING_SOURCE_POLICY')); }
  catch (error) { throw new Error('Invalid BRIEFING_SOURCE_POLICY JSON'); }
  if (!policy || Array.isArray(policy) || Object.keys(policy).sort().join(',') !== 'sources,version' || policy.version !== 1 ||
      !Array.isArray(policy.sources) || !policy.sources.length) {
    throw new Error('Missing or unsupported BRIEFING_SOURCE_POLICY');
  }
  const rules = new Map();
  for (const source of policy.sources) {
    if (!source || Array.isArray(source) || Object.keys(source).sort().join(',') !== 'event_type,kind,name,narration_detail' ||
        typeof source.name !== 'string' || !source.name.length || source.name.length > 80 || source.name !== source.name.trim() ||
        /[\[\]\r\n]/.test(source.name) || rules.has(source.name) ||
        !['simple', 'standard', 'detailed'].includes(source.narration_detail) ||
        !((['daily', 'weekly'].includes(source.kind) && source.event_type === '定时结果') ||
          (source.kind === 'conditional' && ['新回复', '新信息'].includes(source.event_type)))) {
      throw new Error('Invalid BRIEFING_SOURCE_POLICY source');
    }
    rules.set(source.name, source);
  }
  return rules;
}

function installCollector() {
  collectBriefings();
  const triggers = ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'collectBriefings');
  if (!triggers.length) ScriptApp.newTrigger('collectBriefings').timeBased().everyMinutes(15).create();
  console.log('Cloud collector installed. Runs every 15 minutes; no desktop required.');
}

function collectorStatus() {
  const props = PropertiesService.getScriptProperties();
  console.log(JSON.stringify({last_run_status: props.getProperty('LAST_RUN_STATUS'), last_run_at: props.getProperty('LAST_RUN_AT'),
    last_success_at: props.getProperty('LAST_SUCCESS_AT'), last_ingested_count: props.getProperty('LAST_INGESTED_COUNT'),
    failed_count: props.getProperty('LAST_FAILED_COUNT'), failed_messages: JSON.parse(props.getProperty('LAST_FAILED_MESSAGES') || '[]'),
    failures_truncated: props.getProperty('LAST_FAILURES_TRUNCATED') === 'true',
    trigger_count: ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'collectBriefings').length}));
}

function messageFailure_(id, code) {
  const error = new Error('Matching message failed (' + code + '): ' + id);
  error.messageFailure = {id: id, code: code};
  return error;
}

function recordCollectorRun_(props, status, processed, failed, failures) {
  const now = new Date().toISOString();
  const values = {LAST_RUN_STATUS: status, LAST_RUN_AT: now, LAST_INGESTED_COUNT: String(processed),
    LAST_FAILED_COUNT: String(failed), LAST_FAILED_MESSAGES: JSON.stringify(failures), LAST_FAILURES_TRUNCATED: String(failed > failures.length)};
  if (status === 'ok') values.LAST_SUCCESS_AT = now;
  props.setProperties(values);
}

function header_(payload, name) {
  const h = (payload.headers || []).find(x => x.name.toLowerCase() === name.toLowerCase());
  return h ? h.value.trim() : '';
}

function pruneSeen_(props, seen) {
  const ids = Object.keys(seen).sort((a, b) => seen[a] - seen[b]);
  while (ids.length > 2000) {
    const id = ids.shift();
    props.deleteProperty('SEEN_' + id);
    delete seen[id];
  }
}

function body_(payload, messageId) {
  const plain = [];
  const html = [];
  function visit(p) {
    if (p.filename) return; // Attachments are outside the source-mail contract.
    if (p.body && (p.mimeType === 'text/plain' || p.mimeType === 'text/html')) {
      let data = p.body.data;
      if ((!data || data.length === 0) && p.body.attachmentId) {
        data = Gmail.Users.Messages.Attachments.get('me', messageId, p.body.attachmentId).data;
        if (!data) throw messageFailure_(messageId, 'incomplete_body');
      }
      if (!data || data.length === 0) return;
      const text = decodeBodyData_(data, messageId);
      if (p.mimeType === 'text/plain') plain.push(text);
      if (p.mimeType === 'text/html') html.push(text);
    }
    (p.parts || []).forEach(visit);
  }
  visit(payload);
  if (plain.length) return plain.join('\n\n');
  return html.join('\n\n')
    .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, '')
    .replace(/<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi, '$2 ($1)')
    .replace(/<\/(p|div|li|h[1-6]|tr)>|<br\s*\/?>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
    .replace(/&#(\d+);/g, (_, n) => {
      const value = Number(n);
      if (!Number.isInteger(value) || value > 0x10ffff) throw messageFailure_(messageId, 'invalid_html_entity');
      return String.fromCodePoint(value);
    })
    .replace(/\n[ \t]+/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

function decodeBodyData_(data, messageId) {
  if (typeof data !== 'string') {
    console.log('MIME body representation: ' + JSON.stringify({type: typeof data, is_array: Array.isArray(data),
      object_type: Object.prototype.toString.call(data), length: data == null ? null : data.length}));
    if (!Array.isArray(data)) throw messageFailure_(messageId, 'invalid_encoding');
    const bytes = Array.from(data);
    if (!bytes.every(value => Number.isInteger(value) && value >= -128 && value <= 255)) {
      throw messageFailure_(messageId, 'invalid_encoding');
    }
    // Advanced services may expose a bytes field as raw Byte[] instead of REST base64url.
    return Utilities.newBlob(bytes.map(value => value > 127 ? value - 256 : value)).getDataAsString('UTF-8');
  }
  // Gmail uses base64url; normalize its alphabet and optional padding for Utilities.
  const normalized = data.replace(/[\t\r\n ]/g, '').replace(/-/g, '+').replace(/_/g, '/');
  const unpadded = normalized.replace(/=+$/, '');
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(normalized) || unpadded.length % 4 === 1 ||
      (normalized.length !== unpadded.length && normalized.length % 4 !== 0)) {
    throw messageFailure_(messageId, 'invalid_encoding');
  }
  const padded = unpadded + '='.repeat((4 - unpadded.length % 4) % 4);
  try {
    return Utilities.newBlob(Utilities.base64Decode(padded)).getDataAsString('UTF-8');
  } catch (error) {
    throw messageFailure_(messageId, 'decode_failed');
  }
}
