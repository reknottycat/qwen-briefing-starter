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
    $("logout").disabled = state.saving;
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
    await refreshStatus(true);
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
    $("logout").disabled = true; stopPolling();
    try {
      await request("/session", { method: "DELETE", body: "{}" });
      showLock("已退出，重新输入管理口令即可查看设置。", false); $("management-token").focus();
    } catch (error) {
      if (error.status === 401) showLock("已退出，请重新输入管理口令。", false);
      else notice("退出未完成，请检查网络后重试。", "error");
    } finally { $("logout").disabled = false; schedulePoll(); }
  });

  $("refresh-status").addEventListener("click", () => refreshStatus(true));
  $("work-enabled").addEventListener("change", () => { state.workEnabled = $("work-enabled").checked; updateWorkState(); updateSaveState(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopPolling(); else schedulePoll(); });
  window.addEventListener("pagehide", stopPolling);
  window.addEventListener("beforeunload", (event) => { if (hasEdits()) { event.preventDefault(); event.returnValue = ""; } });
  loadPreferences().catch((error) => {
    showLock(error.status === 401 ? "输入管理口令后继续。" : errorMessage(error, "读取设置"));
    if (error.status !== 401) $("login-message").dataset.tone = "error";
  }).finally(() => { $("login-button").disabled = false; });
})();
