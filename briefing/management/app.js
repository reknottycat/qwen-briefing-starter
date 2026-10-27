"use strict";

(() => {
  const API = "/briefing/v1/manage";
  const POLL_INTERVAL = 15000;
  const POLL_LIMIT = 40;
  const levels = [["simple", "简单"], ["standard", "一般"], ["detailed", "详细"]];
  const modes = [["processed", "整理后讲述"], ["raw", "直接复述原文"]];
  const categories = [["news", "资讯"], ["learning", "学习"], ["work", "工作日程"]];
  const groups = [["daily", "每日", "每天的固定内容"], ["weekly", "每周", "有周报时加入"], ["conditional", "有更新时", "收到新回复或新信息时加入"]];
  const pendingStates = new Set(["queued", "generating", "retry_wait"]);
  const $ = (id) => document.getElementById(id);
  const state = { authenticated: false, baseline: null, draft: [], workEnabled: false, saving: false, conflict: false, status: null, pollTimer: null, pollCount: 0, statusBusy: false, statusSerial: 0, epoch: 0, saved: false };
  // API keys stay only in the password input until the save request. Never copy
  // them into draft state, storage, rendered messages or returned settings.
  const modelState = { baseline: null, catalog: null, draft: null, loading: false, saving: false, conflict: false, serial: 0, destination: "" };
  const modelFieldNames = ["mode", "provider", "model", "base_url", "api"];

  class RequestError extends Error {
    constructor(status) { super("request_failed"); this.status = status; }
  }

  async function request(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(API + path, {
        ...options, credentials: "same-origin", cache: "no-store", signal: controller.signal,
        headers: { Accept: "application/json", ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}) }
      });
      if (!response.ok) throw new RequestError(response.status);
      if (response.status === 204) return null;
      return await response.json();
    } finally { window.clearTimeout(timeout); }
  }

  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function modelFields(data) { return Object.fromEntries(modelFieldNames.map((name) => [name, data[name]])); }
  function modelDestination(data) { return JSON.stringify([data.mode, data.provider, data.base_url.replace(/\/+$/, ""), data.api]); }

  function validateModelSettings(data) {
    if (!data || typeof data.enabled !== "boolean") throw new RequestError(502);
    if (!data.enabled) return { enabled: false };
    if (!Number.isInteger(data.revision) || data.revision < 0 || typeof data.configured !== "boolean" || typeof data.api_key_configured !== "boolean" || !["builtin", "custom"].includes(data.mode) || !modelFieldNames.every((key) => typeof data[key] === "string")) throw new RequestError(502);
    return { enabled: true, revision: data.revision, configured: data.configured, api_key_configured: data.api_key_configured, ...modelFields(data) };
  }

  function validateModelCatalog(data) {
    if (!data || !Array.isArray(data.providers) || !Array.isArray(data.custom_apis)) throw new RequestError(502);
    const ids = new Set();
    for (const provider of data.providers) {
      if (!provider || typeof provider.id !== "string" || !provider.id || ids.has(provider.id) || typeof provider.name !== "string" || !Array.isArray(provider.models) || (provider.credential_label !== undefined && typeof provider.credential_label !== "string") || (provider.notes !== undefined && typeof provider.notes !== "string")) throw new RequestError(502);
      ids.add(provider.id);
      const models = new Set();
      for (const model of provider.models) {
        if (!model || typeof model.id !== "string" || !model.id || models.has(model.id) || typeof model.name !== "string") throw new RequestError(502);
        models.add(model.id);
      }
    }
    if (!data.custom_apis.every((api) => api && typeof api.id === "string" && api.id && typeof api.name === "string")) throw new RequestError(502);
    return data;
  }

  function modelMessage(message, tone = "info") {
    $("model-message").textContent = message;
    $("model-message").dataset.tone = tone;
  }

  function hasModelEdits() {
    return Boolean(modelState.baseline?.enabled && modelState.draft &&
      (JSON.stringify(modelFields(modelState.baseline)) !== JSON.stringify(modelState.draft) || $("model-api-key").value));
  }

  function canRetainModelKey() {
    return Boolean(modelState.baseline?.api_key_configured && modelState.draft &&
      modelDestination(modelState.baseline) === modelDestination(modelState.draft));
  }

  function readModelFields() {
    const custom = $("model-mode-custom").checked;
    return { mode: custom ? "custom" : "builtin", provider: (custom ? $("model-custom-provider").value : $("model-provider").value).trim(),
      model: $("model-id").value.trim(), base_url: custom ? $("model-base-url").value.trim().replace(/\/+$/, "") : "", api: custom ? $("model-api").value : "" };
  }

  function refreshModelOptions() {
    const custom = $("model-mode-custom").checked;
    const provider = custom ? null : modelState.catalog?.providers.find((item) => item.id === $("model-provider").value);
    const credentialLabel = provider?.credential_label || "API Key";
    $("model-key-label").textContent = credentialLabel.toLowerCase() === "api key" ? "API Key" : credentialLabel;
    $("model-provider-notes").textContent = provider?.notes || "";
    $("model-provider-notes").hidden = !provider?.notes;
    $("model-builtin-fields").hidden = custom; $("model-builtin-fields").disabled = custom;
    $("model-custom-fields").hidden = !custom; $("model-custom-fields").disabled = !custom;
    $("model-options").replaceChildren();
    if (custom) $("model-id").removeAttribute("list");
    else {
      $("model-id").setAttribute("list", "model-options");
      for (const model of provider?.models || []) {
        const option = element("option", "", model.name); option.value = model.id; $("model-options").append(option);
      }
    }
    $("model-id-help").textContent = custom ? "填写该服务实际支持的模型 ID，与服务商文档保持一致。" : "可以输入搜索，从所选服务的目录中选择模型。";
  }

  function updateModelControls() {
    const busy = modelState.loading || modelState.saving;
    $("model-panel").setAttribute("aria-busy", String(busy));
    $("model-form").setAttribute("aria-busy", String(busy));
    $("model-controls").disabled = busy;
    $("model-save").disabled = busy || modelState.conflict || !state.authenticated || !modelState.baseline?.enabled;
    $("model-save").textContent = modelState.saving ? "正在保存模型…" : "保存模型设置";
    $("model-merge-latest").hidden = !modelState.conflict;
    $("model-merge-latest").disabled = busy;
    $("model-retry").disabled = busy;
    $("logout").disabled = state.saving || modelState.saving;
    const retained = canRetainModelKey();
    $("model-api-key").required = !retained;
    const credentialLabel = $("model-key-label").textContent;
    $("model-key-help").textContent = retained ? "已保存此服务的密钥。留空会保留；填写新值可替换，页面不会回显旧密钥。" : modelState.baseline?.api_key_configured ? `服务或接口已更换，请重新填写对应服务的 ${credentialLabel}。旧密钥不会用于新地址。` : `填写所选服务的 ${credentialLabel}。保存后不会在页面回显。`;
    $("model-summary").dataset.dirty = String(hasModelEdits());
    $("model-summary").textContent = modelState.saving ? "正在保存模型配置…" : hasModelEdits() ? "模型设置有未保存的修改。" : "保存后用于下一次整理，不会立即调用模型。";
  }

  function updateModelDraft({ resetModel = false } = {}) {
    if (resetModel) $("model-id").value = "";
    const draft = readModelFields();
    const destination = modelDestination(draft);
    if (modelState.destination && modelState.destination !== destination) $("model-api-key").value = "";
    modelState.draft = draft; modelState.destination = destination;
    refreshModelOptions(); updateModelControls();
  }

  function renderModelSettings(data, retained = null) {
    modelState.baseline = validateModelSettings(data); modelState.conflict = false;
    $("model-api-key").value = "";
    $("model-unavailable").hidden = modelState.baseline.enabled;
    $("model-form").hidden = !modelState.baseline.enabled;
    if (!modelState.baseline.enabled) {
      modelState.draft = null; modelState.destination = "";
      $("model-catalog-source").textContent = "";
      modelMessage("模型配置由当前部署方式管理。");
      return;
    }
    modelState.draft = retained || modelFields(modelState.baseline);
    const draft = modelState.draft;
    $("model-provider").replaceChildren();
    const placeholder = element("option", "", "请选择服务"); placeholder.value = ""; $("model-provider").append(placeholder);
    for (const provider of modelState.catalog.providers) {
      const option = element("option", "", provider.name === provider.id ? provider.name : `${provider.name} · ${provider.id}`);
      option.value = provider.id; $("model-provider").append(option);
    }
    if (draft.mode === "builtin" && draft.provider && !modelState.catalog.providers.some((item) => item.id === draft.provider)) {
      const missing = element("option", "", `${draft.provider}（当前目录未提供）`); missing.value = draft.provider; $("model-provider").append(missing);
    }
    $("model-api").replaceChildren();
    for (const api of modelState.catalog.custom_apis) { const option = element("option", "", api.name); option.value = api.id; $("model-api").append(option); }
    $("model-mode-builtin").checked = draft.mode === "builtin"; $("model-mode-custom").checked = draft.mode === "custom";
    $("model-provider").value = draft.mode === "builtin" ? draft.provider : "";
    $("model-custom-provider").value = draft.mode === "custom" ? draft.provider : "";
    $("model-base-url").value = draft.base_url; $("model-id").value = draft.model;
    $("model-api").value = draft.api || modelState.catalog.custom_apis[0]?.id || "";
    $("model-catalog-source").textContent = "服务与模型来自 Pi 目录";
    modelState.destination = modelDestination(draft);
    refreshModelOptions();
    modelMessage(retained ? "已读取最新配置，并保留你尚未保存的模型选项。请核对后再次保存。" : data.configured ? "当前模型配置已读取。" : "还没有配置整理模型。选择服务和模型，再填写 API Key。");
  }

  function clearModelPanel(preserveDraft) {
    modelState.serial += 1; modelState.loading = false; modelState.saving = false;
    $("model-api-key").value = ""; $("model-form").hidden = true;
    $("model-provider").replaceChildren(); $("model-options").replaceChildren(); $("model-api").replaceChildren();
    for (const id of ["model-custom-provider", "model-base-url", "model-id"]) $(id).value = "";
    $("model-catalog-source").textContent = ""; $("model-unavailable").hidden = true; $("model-retry").hidden = true;
    $("model-provider-notes").textContent = ""; $("model-provider-notes").hidden = true; $("model-key-label").textContent = "API Key";
    if (!preserveDraft) { modelState.baseline = null; modelState.draft = null; modelState.catalog = null; modelState.conflict = false; modelState.destination = ""; }
    modelMessage("解锁后读取模型设置。"); updateModelControls();
  }

  async function loadModelSettings({ preserveDraft = false } = {}) {
    if (!state.authenticated) return;
    const epoch = state.epoch; const serial = ++modelState.serial;
    const retained = preserveDraft && hasModelEdits() ? { ...modelState.draft } : null;
    modelState.loading = true; $("model-api-key").value = ""; $("model-retry").hidden = true;
    modelMessage("正在读取模型设置…"); updateModelControls();
    try {
      const data = validateModelSettings(await request("/model-settings"));
      let catalog = null;
      if (data.enabled) catalog = validateModelCatalog(await request("/model-catalog"));
      if (epoch !== state.epoch || serial !== modelState.serial || !state.authenticated) return;
      modelState.catalog = catalog; renderModelSettings(data, retained);
    } catch (error) {
      if (epoch !== state.epoch || serial !== modelState.serial) return;
      if (error.status === 401) showLock("登录已过期，请重新解锁。未保存的模型选项会保留，密钥请重新填写。");
      else { modelMessage("暂时无法读取模型设置或服务目录。请稍后重试；收听偏好仍可使用。", "error"); $("model-retry").hidden = false; }
    } finally { if (epoch === state.epoch && serial === modelState.serial) { modelState.loading = false; updateModelControls(); } }
  }

  function modelInputError() {
    const draft = modelState.draft;
    if (!draft || !draft.provider || !draft.model || draft.model.length > 200 || /\s/.test(draft.model)) return "请选择服务并填写准确的模型 ID。";
    if (draft.mode === "builtin") {
      const provider = modelState.catalog.providers.find((item) => item.id === draft.provider);
      if (!provider?.models.some((model) => model.id === draft.model)) return "请选择当前服务目录中实际提供的模型。";
    } else {
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(draft.provider)) return "服务标识请使用英文字母、数字、点、短横线或下划线，并以字母或数字开头。";
      let url;
      try { url = new URL(draft.base_url); } catch { return "请填写完整的 HTTPS 接口地址。"; }
      if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || /\s/.test(draft.base_url)) return "接口地址必须使用 HTTPS，不能含登录信息、密钥、查询参数或片段。";
      if (!modelState.catalog.custom_apis.some((api) => api.id === draft.api)) return "请选择此部署支持的接口协议。";
    }
    const key = $("model-api-key").value.trim();
    if (!key && !canRetainModelKey()) return `请填写这个服务的 ${$("model-key-label").textContent}。切换服务或接口后，需要重新填写对应的密钥。`;
    if (key && (key.startsWith("!") || /\s/.test(key))) return "请检查服务密钥，不能含空白字符或以感叹号开头。";
    return "";
  }

  async function saveModelSettings(event) {
    event.preventDefault();
    if (modelState.saving || modelState.loading || modelState.conflict || !state.authenticated || !modelState.baseline?.enabled) return;
    modelState.draft = readModelFields();
    const invalid = modelInputError();
    if (invalid) { modelMessage(invalid, "error"); $("model-message").focus(); return; }
    const epoch = state.epoch; const serial = ++modelState.serial;
    const payload = { revision: modelState.baseline.revision, ...modelState.draft };
    if ($("model-api-key").value.trim()) payload.api_key = $("model-api-key").value.trim();
    modelState.saving = true; updateModelControls(); modelMessage("正在保存模型设置…");
    let accepted = false;
    try {
      const result = await request("/model-settings", { method: "PUT", body: JSON.stringify(payload) });
      if (epoch !== state.epoch || serial !== modelState.serial || !state.authenticated) return;
      $("model-api-key").value = ""; accepted = true; renderModelSettings(result);
      const confirmed = await request("/model-settings");
      if (epoch !== state.epoch || serial !== modelState.serial || !state.authenticated) return;
      renderModelSettings(confirmed);
      modelMessage("模型设置已保存，将用于下一次整理。尚未验证服务连接或生成结果。");
    } catch (error) {
      if (epoch !== state.epoch || serial !== modelState.serial) return;
      if (error.status === 401) showLock(accepted ? "模型设置已保存，登录已过期。请重新解锁查看当前配置。" : "登录已过期，请重新解锁。模型选项会保留，密钥请重新填写。");
      else if (error.status === 409) {
        $("model-api-key").value = ""; modelState.conflict = true;
        modelMessage("模型配置已在其他页面更新，本次未保存。先读取最新配置并核对保留的选项；如需替换密钥，请重新填写。", "error");
      } else modelMessage(accepted ? "模型设置已保存，但暂时无法回读最新配置。请稍后重新读取确认。" : error.status === 422 ? "保存未完成。请核对服务、模型、接口地址和 API Key。" : error.status === 403 ? "保存被拒绝。请从原始早报设置地址打开本页后重试。" : error.status === 503 ? "暂时无法保存模型设置，请联系维护者检查配置和存储目录权限。" : "保存尚未确认。请检查网络后重试；当前模型选项会保留。", "error");
    } finally {
      delete payload.api_key;
      if (epoch === state.epoch && serial === modelState.serial) { modelState.saving = false; updateModelControls(); }
    }
  }

  function validatePreferences(data) {
    if (!data || !Number.isInteger(data.revision) || data.revision < 1 || data.format !== "mixed" || typeof data.work_enabled !== "boolean" || !Array.isArray(data.sources) || !data.sources.length) throw new RequestError(502);
    const names = new Set();
    for (const source of data.sources) {
      if (typeof source.name !== "string" || names.has(source.name) || !["raw", "processed", "podcast"].includes(source.mode) || !categories.some(([category]) => source.category === category) || !groups.some(([kind]) => source.kind === kind) || !Number.isInteger(source.order) || !levels.some(([value]) => value === source.narration_detail) || typeof source.note !== "string" || Array.from(source.note).length > 200) throw new RequestError(502);
      names.add(source.name);
    }
    return { revision: data.revision, format: data.format, work_enabled: data.work_enabled, sources: data.sources.map((source) => ({ ...source })).sort((a, b) => a.order - b.order) };
  }

  function edits() {
    const changes = new Map();
    if (!state.baseline) return changes;
    for (const source of state.draft) {
      const previous = state.baseline.sources.find((item) => item.name === source.name);
      if (!previous) continue;
      const fields = {};
      if (source.mode !== previous.mode) fields.mode = source.mode;
      if (source.category !== previous.category) fields.category = source.category;
      if (source.narration_detail !== previous.narration_detail) fields.narration_detail = source.narration_detail;
      if (source.note !== previous.note) fields.note = source.note;
      if (Object.keys(fields).length) changes.set(source.name, fields);
    }
    return changes;
  }

  function acceptPreferences(data, keepEdits = false) {
    const retained = keepEdits ? edits() : new Map();
    const retainWork = keepEdits && workChanged();
    state.baseline = validatePreferences(data);
    if (!retainWork) state.workEnabled = state.baseline.work_enabled;
    state.draft = state.baseline.sources.map((source) => ({ ...source, ...(retained.get(source.name) || {}) }));
    state.conflict = false;
    renderSources();
    updateSaveState();
  }

  function workChanged() { return Boolean(state.baseline && state.workEnabled !== state.baseline.work_enabled); }
  function hasEdits() { return edits().size > 0 || workChanged(); }

  function updateWorkState() {
    $("work-enabled").checked = state.workEnabled;
    $("work-state").textContent = state.workEnabled ? "已开启" : "已关闭";
  }

  function notice(message = "", tone = "info", conflict = false) {
    $("notice").hidden = !message;
    $("notice").dataset.tone = tone;
    $("notice-text").textContent = message;
    $("merge-latest").hidden = !conflict;
  }

  function updateSaveState() {
    const count = edits().size;
    $("save-summary").dataset.dirty = String(hasEdits());
    const dirtyText = count ? `${count} 个来源${workChanged() ? "及工作日程" : ""}有未保存的修改` : workChanged() ? "工作日程设置有未保存的修改" : "";
    $("save-summary").textContent = state.saving ? "正在保存设置…" : dirtyText || (state.saved ? "设置已保存" : "设置已读取");
    $("save-button").disabled = state.saving || state.conflict || !state.authenticated;
    $("source-controls").disabled = state.saving;
    $("work-enabled").disabled = state.saving;
    $("refresh-status").disabled = state.saving || state.statusBusy;
    $("logout").disabled = state.saving || modelState.saving;
    $("save-button").textContent = state.saving ? "正在保存…" : "保存并更新今天早报 →";
    $("preferences-form").setAttribute("aria-busy", String(state.saving));
  }

  function renderSource(source, index) {
    const row = element("div", "source-row");
    const main = element("div", "source-main");
    const heading = element("div", "source-heading");
    heading.append(element("span", "source-order", String(source.order + 1).padStart(2, "0")), element("p", "source-name", source.name));
    const choices = element("fieldset", "detail-choices");
    choices.append(element("legend", "sr-only", `${source.name}的讲述详略`));
    for (const [value, label] of levels) {
      const option = element("label", "detail-option");
      const input = element("input");
      input.type = "radio"; input.name = `detail-${index}`; input.value = value; input.checked = source.narration_detail === value;
      input.addEventListener("change", () => { source.narration_detail = value; updateSaveState(); });
      option.append(input, element("span", "", label));
      choices.append(option);
    }
    main.append(heading);
    const modeChoices = element("fieldset", "mode-choices");
    modeChoices.append(element("legend", "sr-only", `${source.name}的讲述方式`));
    for (const [value, label] of modes) {
      const option = element("label", "mode-option");
      const input = element("input");
      input.type = "radio"; input.name = `mode-${index}`; input.value = value; input.checked = source.mode === value;
      input.addEventListener("change", () => { source.mode = value; updateMode(); updateCategory(); updateSaveState(); });
      option.append(input, element("span", "", label)); modeChoices.append(option);
    }
    main.append(modeChoices);
    const categoryRow = element("div", "source-category");
    const categoryLabel = element("label", "", "内容归类"); categoryLabel.htmlFor = `category-${index}`;
    const categorySelect = element("select"); categorySelect.id = `category-${index}`; categorySelect.name = `category-${index}`;
    for (const [value, label] of categories) { const option = element("option", "", label); option.value = value; categorySelect.append(option); }
    categorySelect.value = source.category;
    const categoryHint = element("p", "category-hint muted");
    categoryHint.id = `category-hint-${index}`; categorySelect.setAttribute("aria-describedby", categoryHint.id);
    function updateCategory() {
      categoryHint.textContent = source.category === "learning" ? source.mode === "raw" ? "今日简报仅提示学习主题；学习入口直接读原文。" : "详略只影响学习正文（默认详细）；今日简报始终简述。" : source.category === "work" ? "工作日程关闭时，这类内容不在收听入口显示。" : "在“今日简报”中收听。";
    }
    categorySelect.addEventListener("change", () => { source.category = categorySelect.value; updateCategory(); updateSaveState(); });
    updateCategory(); categoryRow.append(categoryLabel, categorySelect, categoryHint);
    const detailRow = element("div", "detail-row");
    detailRow.append(element("span", "small muted", "讲述详略"), choices);
    const details = element("details", "source-note");
    const summary = element("summary", "", source.note ? "查看或修改偏好" : "补一句偏好");
    const label = element("label", "sr-only", `${source.name}的补充偏好`);
    label.htmlFor = `note-${index}`;
    const textarea = element("textarea");
    textarea.id = `note-${index}`; textarea.name = `note-${index}`; textarea.maxLength = 200; textarea.rows = 2; textarea.value = source.note;
    textarea.placeholder = "例如：先讲结论，再解释原因。";
    textarea.setAttribute("aria-describedby", `note-help-${index} note-count-${index}`);
    const meta = element("div", "note-meta");
    const help = element("span", "", "可留空。只补充讲述偏好。" ); help.id = `note-help-${index}`;
    const count = element("span", "note-count", `${Array.from(source.note).length}/200`); count.id = `note-count-${index}`;
    textarea.addEventListener("input", () => {
      source.note = textarea.value;
      count.textContent = `${Array.from(source.note).length}/200`;
      summary.textContent = source.note ? "查看或修改偏好" : "补一句偏好";
      updateSaveState();
    });
    const modeHelp = element("p", "mode-help small muted");
    function updateMode() {
      const raw = source.mode === "raw";
      choices.disabled = raw;
      textarea.disabled = raw;
      modeHelp.hidden = source.mode === "processed";
      modeHelp.textContent = raw ? "按原文直接讲述。下方详略和偏好会保留，但不应用。" : "当前使用其他讲述方式；选好上方方式后保存即可替换。";
      detailRow.classList.toggle("inactive", raw);
    }
    updateMode();
    meta.append(help, count); details.append(summary, label, textarea, meta); row.append(main, categoryRow, modeHelp, detailRow, details);
    return row;
  }

  function renderSources() {
    const fragment = document.createDocumentFragment();
    for (const [kind, label, description] of groups) {
      const sources = state.draft.filter((source) => source.kind === kind);
      if (!sources.length) continue;
      const section = element("section", "source-group");
      const heading = element("div", "group-heading");
      const title = element("h3", "", label); title.id = `group-${kind}`; section.setAttribute("aria-labelledby", title.id);
      heading.append(title, element("p", "", description)); section.append(heading);
      sources.forEach((source) => section.append(renderSource(source, state.draft.indexOf(source))));
      fragment.append(section);
    }
    $("source-groups").replaceChildren(fragment);
    $("source-total").textContent = `${state.draft.length} 个来源`;
    updateWorkState();
  }

  function stopPolling() { window.clearTimeout(state.pollTimer); state.pollTimer = null; }

  function showLock(message = "输入管理口令后继续。", preserveDraft = true) {
    stopPolling(); state.epoch += 1; state.statusSerial += 1; state.authenticated = false; state.status = null; state.statusBusy = false;
    clearModelPanel(preserveDraft);
    if (!preserveDraft) { state.baseline = null; state.draft = []; state.workEnabled = false; state.saved = false; state.conflict = false; }
    $("source-groups").replaceChildren(); $("source-total").textContent = "";
    $("work-enabled").checked = false; $("work-state").textContent = "已关闭";
    $("edition-state").textContent = "正在确认状态…"; $("edition-description").textContent = "正在读取今天的准备进度。";
    $("edition-sources").textContent = "—"; $("edition-date").textContent = ""; $("edition-time").textContent = "—"; $("edition-time-row").hidden = true;
    $("status-error").textContent = ""; $("status-error").hidden = true; $("refresh-status").disabled = false;
    $("settings-screen").hidden = true; $("logout").hidden = true; $("lock-screen").hidden = false;
    $("management-token").value = ""; $("login-message").textContent = message; $("login-message").dataset.tone = "";
    notice();
  }

  function showSettings() {
    state.authenticated = true;
    $("lock-screen").hidden = true; $("settings-screen").hidden = false; $("logout").hidden = false;
    updateSaveState();
  }

  function formatTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  }

  function renderStatus(data) {
    if (!data || !Number.isInteger(data.source_count) || typeof data.status !== "string") throw new RequestError(502);
    state.status = data;
    const hasEdition = Boolean(data.generated_at);
    const currentEdition = hasEdition ? "更新完成前，仍可收听现有早报。" : "准备完成后，就能收听今天的早报。";
    const messages = {
      no_sources: ["等待今天的报告", "偏好已保存。今天还没有收到来源报告，收到后会按这些设置准备早报。"],
      queued: ["已排队更新", `今天的报告已就绪，正在等待生成。${currentEdition}`],
      generating: ["正在准备播报", `正在按已保存的讲述方式和偏好准备内容。${currentEdition}`],
      retry_wait: ["稍后自动重试", `这次更新未能完成，系统会稍后重试。${currentEdition}`],
      blocked: ["更新暂未完成", `多次尝试仍未成功。可以再次保存并更新；若仍失败，请联系维护者。${hasEdition ? "现有早报仍可收听。" : "今天的早报尚未准备好。"}`],
      ready: ["已准备好", "今天的早报已准备好，可以到眼镜中收听。"]
    };
    const [title, description] = messages[data.status] || ["状态暂不可用", "请稍后刷新，确认今天早报的准备进度。"];
    $("edition-state").textContent = title; $("edition-description").textContent = description;
    $("edition-date").textContent = typeof data.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(data.date) ? data.date.slice(5).replace("-", "/") : "";
    $("edition-sources").textContent = `${data.source_count} 份报告`;
    const time = data.generated_at ? formatTime(data.generated_at) : null;
    $("edition-time-row").hidden = !time; $("edition-time").textContent = time ? `${time} 北京时间` : "—";
    $("status-error").hidden = true;
    if (data.error && ["retry_wait", "blocked"].includes(data.status)) {
      const errors = { timeout: "生成超时", codex_failed: "生成服务暂时失败", invalid_output: "生成内容未通过检查", input_too_large: "本次报告内容过长", publish_failed: "新稿暂未发布成功", worker_interrupted: "生成过程被中断" };
      $("status-error").textContent = `原因：${errors[data.error] || "生成暂时遇到问题"}。`;
      $("status-error").hidden = false;
    }
  }

  function schedulePoll() {
    stopPolling();
    if (!state.authenticated || state.saving || document.hidden || !pendingStates.has(state.status?.status)) return;
    if (state.pollCount >= POLL_LIMIT) {
      $("status-error").textContent = "自动检查已暂停。可点击“刷新状态”查看最新进度。"; $("status-error").hidden = false;
      return;
    }
    state.pollTimer = window.setTimeout(() => { state.pollCount += 1; refreshStatus(); }, POLL_INTERVAL);
  }

  async function refreshStatus(reset = false) {
    if (!state.authenticated || state.statusBusy) return;
    if (reset) state.pollCount = 0;
    const epoch = state.epoch; const serial = ++state.statusSerial; state.statusBusy = true; $("refresh-status").disabled = true;
    try {
      const data = await request("/status");
      if (epoch !== state.epoch || serial !== state.statusSerial || !state.authenticated) return;
      renderStatus(data);
    } catch (error) {
      if (epoch !== state.epoch || serial !== state.statusSerial) return;
      if (error.status === 401) showLock("登录已过期，请重新解锁。当前页面中的未保存修改会保留。");
      else { $("status-error").textContent = "暂时无法读取最新进度。请检查网络后刷新状态；下方设置仍可编辑。"; $("status-error").hidden = false; }
    } finally {
      if (epoch === state.epoch && serial === state.statusSerial) { state.statusBusy = false; $("refresh-status").disabled = state.saving; schedulePoll(); }
    }
  }

  function errorMessage(error, action) {
    if (error.status === 403) return `${action}被拒绝。请从原始早报设置地址打开本页后重试。`;
    if (error.status === 422) return `${action}未完成。请检查每个来源的详略和偏好，偏好最多 200 字。`;
    if (error.status === 503) return "管理功能暂不可用，请联系维护者检查管理入口设置。";
    if (error.status === 429) return "操作较频繁，请稍后重试。";
    return `${action}暂未确认。请检查网络后重试；当前页面中的修改会保留。`;
  }

  async function loadPreferences({ preserveDraft = false, focus = false } = {}) {
    const epoch = state.epoch;
    const data = await request("/preferences");
    if (epoch !== state.epoch) return;
    acceptPreferences(data, preserveDraft); showSettings();
    if (focus) $("sources-title").focus();
    await Promise.all([refreshStatus(true), loadModelSettings({ preserveDraft: true })]);
  }

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if ($("login-button").disabled) return;
    const token = $("management-token").value;
    if (!token) return;
    $("login-button").disabled = true; $("login-button").textContent = "正在解锁…";
    $("login-message").textContent = "正在验证口令…"; $("login-message").dataset.tone = "";
    const body = JSON.stringify({ token }); $("management-token").value = "";
    try {
      await request("/session", { method: "POST", body });
      const hasDraft = hasEdits();
      await loadPreferences({ preserveDraft: hasDraft, focus: true });
      if (hasDraft && state.authenticated) notice("已重新解锁，并保留你在本页的修改。请检查后保存。");
    } catch (error) {
      $("login-message").textContent = error.status === 401 ? "口令不正确或已失效，请重新输入管理口令。" : error.status === 422 ? "管理口令格式不正确，请检查后重新输入。" : errorMessage(error, "解锁");
      $("login-message").dataset.tone = "error"; $("management-token").focus();
    } finally { $("login-button").disabled = false; $("login-button").textContent = "解锁设置"; }
  });

  $("preferences-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.saving || state.conflict || !state.authenticated || !state.baseline) return;
    state.saving = true; state.statusSerial += 1; state.statusBusy = false; stopPolling(); updateSaveState(); notice();
    let accepted = false;
    try {
      const result = await request("/preferences", { method: "PUT", body: JSON.stringify({
        expected_revision: state.baseline.revision,
        work_enabled: state.workEnabled,
        sources: state.draft.map(({ name, mode, category, narration_detail, note }) => ({ name, mode, category, narration_detail, note }))
      }) });
      acceptPreferences(result); state.saved = true; accepted = true;
      if (result.generation) renderStatus(result.generation);
      const confirmed = await request("/preferences");
      acceptPreferences(confirmed);
      notice(result.generation?.status === "no_sources" ? "设置已保存。今天还没有收到来源报告，收到后会按这些偏好准备早报。" : "设置已保存，已请求更新今天的早报。进度会显示在“今天的早报”中。");
      await refreshStatus(true);
    } catch (error) {
      if (error.status === 401) showLock(accepted ? "设置已保存，登录已过期。请重新解锁以查看最新状态。" : "登录已过期，请重新解锁。当前页面中的未保存修改会保留。");
      else if (error.status === 409) {
        state.conflict = true;
        notice("设置已在其他页面更新，这次修改尚未保存。先读取最新设置并保留你的修改，检查后再次保存。", "error", true);
      } else notice(accepted ? "设置已保存，但暂时无法回读最新设置。请稍后重新打开本页确认。" : errorMessage(error, "保存"), "error");
    } finally { state.saving = false; updateSaveState(); schedulePoll(); }
  });

  $("merge-latest").addEventListener("click", async () => {
    const epoch = state.epoch;
    $("merge-latest").disabled = true;
    try {
      const data = await request("/preferences");
      if (epoch !== state.epoch || !state.authenticated) return;
      acceptPreferences(data, true);
      notice("已读取最新设置，并保留你修改过的内容。请检查后再次保存。");
    } catch (error) {
      if (epoch !== state.epoch) return;
      if (error.status === 401) showLock("登录已过期，请重新解锁。当前页面中的未保存修改会保留。");
      else notice(errorMessage(error, "读取最新设置"), "error", true);
    } finally { $("merge-latest").disabled = false; updateSaveState(); }
  });

  $("logout").addEventListener("click", async () => {
    $("model-api-key").value = ""; updateModelControls(); $("logout").disabled = true; stopPolling();
    try {
      await request("/session", { method: "DELETE", body: "{}" });
      showLock("已退出，重新输入管理口令即可查看设置。", false); $("management-token").focus();
    } catch (error) {
      if (error.status === 401) showLock("已退出，请重新输入管理口令。", false);
      else notice("退出未完成，请检查网络后重试。", "error");
    } finally { $("logout").disabled = false; schedulePoll(); }
  });

  $("refresh-status").addEventListener("click", () => refreshStatus(true));
  $("model-form").addEventListener("submit", saveModelSettings);
  for (const id of ["model-mode-builtin", "model-mode-custom", "model-provider"]) $(id).addEventListener("change", () => updateModelDraft({ resetModel: true }));
  for (const id of ["model-custom-provider", "model-base-url", "model-id"]) $(id).addEventListener("input", () => updateModelDraft());
  $("model-api").addEventListener("change", () => updateModelDraft());
  $("model-api-key").addEventListener("input", updateModelControls);
  $("model-merge-latest").addEventListener("click", () => loadModelSettings({ preserveDraft: true }));
  $("model-retry").addEventListener("click", () => loadModelSettings({ preserveDraft: true }));
  $("work-enabled").addEventListener("change", () => { state.workEnabled = $("work-enabled").checked; updateWorkState(); updateSaveState(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopPolling(); else schedulePoll(); });
  window.addEventListener("pagehide", () => { stopPolling(); $("model-api-key").value = ""; });
  window.addEventListener("beforeunload", (event) => { if (hasEdits() || hasModelEdits()) { event.preventDefault(); event.returnValue = ""; } });
  loadPreferences().catch((error) => {
    showLock(error.status === 401 ? "输入管理口令后继续。" : errorMessage(error, "读取设置"));
    if (error.status !== 401) $("login-message").dataset.tone = "error";
  }).finally(() => { $("login-button").disabled = false; });
})();
