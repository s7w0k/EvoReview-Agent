const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const titles = {
  overview: "运行总览",
  review: "发起审查",
  tasks: "任务中心",
  chat: "报告对话",
  trace: "Trace 回放",
  skills: "Skill 注册中心",
  evolution: "演进实验室",
};

const stateLabels = {
  PENDING: "等待中",
  PLANNING: "规划中",
  EXECUTING: "执行中",
  REVIEWING: "汇总中",
  SUCCESS: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
};

let selectedTask = null;
let selectedTaskData = null;
let accessToken = localStorage.getItem("evoagent_token") || "";
let toastTimer = null;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = value ?? "";
  return node.innerHTML;
}

function formatTime(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      }).format(date);
}

function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("json") ? await response.json() : await response.text();

  if (response.status === 401) {
    $("#login-overlay").classList.remove("hidden");
    $("#logout").classList.add("hidden");
  }
  if (!response.ok) {
    const plainText = typeof data === "string" && !/<[a-z][\s\S]*>/i.test(data) ? data.trim() : "";
    const message = typeof data === "object"
      ? data.error || data.detail
      : plainText || `请求失败 (${response.status})`;
    throw new Error(message || response.statusText || "请求失败");
  }
  return data;
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("show"), 2600);
}

function setButtonBusy(button, busy, busyText) {
  if (!button) return;
  button.setAttribute("aria-busy", String(busy));
  if (busy) {
    button.dataset.label = button.innerHTML;
    button.disabled = true;
    button.textContent = busyText;
  } else {
    button.disabled = false;
    if (button.dataset.label) button.innerHTML = button.dataset.label;
  }
}

function show(view, updateHash = true) {
  if (!titles[view]) {
    view = "overview";
    history.replaceState(null, "", "#overview");
  }
  $$(".view").forEach((element) => element.classList.remove("active"));
  $$(".nav-item").forEach((element) => {
    const active = element.dataset.view === view;
    element.classList.toggle("active", active);
    element.setAttribute("aria-current", active ? "page" : "false");
  });
  $(`#view-${view}`).classList.add("active");
  $("#page-title").textContent = titles[view];
  document.title = `${titles[view]} · EvoAgent`;
  if (updateHash) history.replaceState(null, "", `#${view}`);

  if (view === "tasks") loadTasks();
  if (view === "chat") loadChat();
  if (view === "trace") loadTraceTasks();
  if (view === "skills") loadSkills();
  if (view === "evolution") loadFailures();
  window.scrollTo({ top: 0, behavior: reduceMotion.matches ? "auto" : "smooth" });
}

$$(".nav-item").forEach((button) => button.addEventListener("click", () => show(button.dataset.view)));
$$("[data-jump]").forEach((button) => button.addEventListener("click", () => show(button.dataset.jump)));
window.addEventListener("hashchange", () => show(location.hash.slice(1), false));

function taskRows(tasks) {
  if (!tasks?.length) {
    return '<div class="empty-state"><span><b>还没有审查任务</b>提交一个 Diff 开始首次审查</span></div>';
  }
  return tasks.map((task) => {
    const state = String(task.state || "PENDING").toUpperCase();
    const repository = escapeHtml(task.repository || "未命名仓库");
    const pr = task.pull_request ? `PR #${escapeHtml(task.pull_request)}` : "手动审查";
    return `
      <button class="task-row" data-task="${escapeHtml(task.id)}" type="button">
        <span class="task-main">
          <span class="task-glyph">PR</span>
          <span class="task-copy">
            <span class="task-name">${repository}</span>
            <span class="task-meta"><span>${pr}</span><span>${escapeHtml(formatTime(task.created_at))}</span></span>
          </span>
        </span>
        <span class="status state-${state.toLowerCase()}">${stateLabels[state] || escapeHtml(state)}</span>
      </button>`;
  }).join("");
}

function bindTasks(root) {
  $$("[data-task]", root).forEach((row) => row.addEventListener("click", () => openTask(row.dataset.task)));
}

function statCard(label, value, note, style, icon) {
  return `<article class="stat ${style}">
    <div class="stat-head"><span>${label}</span><i>${icon}</i></div>
    <b>${value}</b><small>${note}</small>
  </article>`;
}

function renderLlmRuntime(llm = {}) {
  const enabled = Boolean(llm.enabled);
  const failed = Boolean(llm.error);
  const provider = String(llm.provider || "local");
  const model = String(llm.model || "");
  const detail = failed
    ? "暂时无法读取模型配置"
    : enabled
      ? `${provider} / ${model || "默认模型"}，参与上下文审查与风险判断`
      : "未配置模型，当前由确定性本地规则 Agent 兜底";
  const state = failed ? "读取失败" : enabled ? "已启用" : "待配置";
  const runtime = failed
    ? "运行时状态未知"
    : enabled
      ? `${provider} / ${model || "模型已配置"}`
      : "Local rules fallback";

  const step = $("#llm-agent-step");
  step.classList.remove("is-pending");
  step.classList.toggle("is-active", enabled);
  step.classList.toggle("is-disabled", !enabled && !failed);
  step.classList.toggle("is-error", failed);
  $("#llm-agent-detail").textContent = detail;
  $("#llm-agent-state").textContent = state;

  const status = $("#llm-runtime-status");
  status.className = `runtime-status ${failed ? "is-error" : enabled ? "is-active" : "is-disabled"}`;
  status.textContent = state;
  const capability = $("#llm-capability");
  capability.classList.toggle("is-active", enabled);
  capability.classList.toggle("is-disabled", !enabled && !failed);
  capability.classList.toggle("is-error", failed);
  $("#llm-capability-detail").textContent = detail;
  $("#llm-runtime-model").textContent = runtime;
}

async function loadDashboard() {
  try {
    const data = await api("/api/dashboard");
    renderLlmRuntime(data.llm);
    if (data.chat) chat.status = { enabled: false, insights_enabled: false, feedback_enabled: false, model_configured: false, ...data.chat };
    chat.statusLoaded = true;
    $("#system-status").textContent = `${data.queue} · ${data.orchestrator}`;
    const stats = data.stats || {};
    const rate = Math.round(Number(stats.success_rate || 0) * 100);
    $("#stats").innerHTML = [
      statCard("总任务", stats.tasks_total ?? 0, "累计审查任务", "", "ALL"),
      statCard("已完成", stats.tasks_success ?? 0, "通过质量门禁", "success", "OK"),
      statCard("失败", stats.tasks_failed ?? 0, "需要进一步处理", "failed", "ERR"),
      statCard("成功率", `${rate}%`, "全部任务成功率", "rate", "RATE"),
      statCard("待处理案例", stats.unresolved_failure_cases ?? 0, "未解决反馈", "feedback", "OPEN"),
      statCard("活跃 Skills", stats.active_skill_versions ?? 0, "当前生效版本", "skills", "SK"),
    ].join("");
    $("#recent-tasks").innerHTML = taskRows((data.tasks || []).slice(0, 5));
    bindTasks($("#recent-tasks"));
  } catch (error) {
    renderLlmRuntime({ error: true });
    $("#system-status").textContent = "服务连接异常";
    $("#stats").innerHTML = '<div class="empty-state"><span><b>暂时无法读取数据</b>请检查服务状态后重试</span></div>';
    $("#recent-tasks").innerHTML = '<div class="empty-state"><span>数据加载失败</span></div>';
    toast(error.message);
  }
}

async function loadTasks() {
  const root = $("#all-tasks");
  root.innerHTML = '<div class="list-loading"></div><div class="list-loading"></div>';
  try {
    const data = await api("/api/tasks");
    root.innerHTML = taskRows(data.tasks || []);
    bindTasks(root);
  } catch (error) {
    root.innerHTML = '<div class="empty-state"><span>任务加载失败</span></div>';
    toast(error.message);
  }
}

async function openTask(id) {
  show("tasks");
  $("#task-report").textContent = "正在加载任务报告…";
  $("#feedback-panel").classList.add("hidden");
  try {
    const task = await api(`/v1/tasks/${encodeURIComponent(id)}`);
    selectedTask = id;
    selectedTaskData = task;
    $("#task-report").textContent = formatJson(task);
    $("#create-fix").classList.toggle("hidden", !(task.report && task.pull_request));
    const chatReady = task.state === "SUCCESS" && task.report;
    $("#chat-analyze").classList.toggle("hidden", !chatReady);
    const feedbackReady = task.state === "SUCCESS" && task.report;
    $("#feedback-panel").classList.toggle("hidden", !feedbackReady);
    if (feedbackReady) {
      populateFeedbackFindings(task.report.findings || []);
      await loadTaskFeedback(id);
    }
  } catch (error) {
    $("#task-report").textContent = error.message;
    selectedTaskData = null;
  }
}

const feedbackLabels = {
  false_positive: "误报",
  missed_issue: "漏报",
  bad_fix: "坏修复",
  accepted: "已接受",
};

/* ---- Trace 回放 ---- */

async function loadTraceTasks() {
  const select = $("#trace-task-select");
  try {
    const data = await api("/api/tasks");
    const tasks = data.tasks || [];
    const previous = select.value;
    select.innerHTML = tasks.length
      ? '<option value="">选择任务…</option>' + tasks.map((task) => `
          <option value="${escapeHtml(task.id)}">${escapeHtml(task.repository || "未命名仓库")}${task.pull_request ? ` · PR #${escapeHtml(task.pull_request)}` : ""} · ${stateLabels[String(task.state).toUpperCase()] || escapeHtml(task.state)}</option>`).join("")
      : '<option value="">暂无任务</option>';
    if (previous && tasks.some((task) => task.id === previous)) select.value = previous;
    else if (tasks.length && !select.value) select.value = tasks[0].id;
    if (select.value) await openTrace(select.value);
    else resetTraceView();
  } catch (error) {
    resetTraceView();
    $("#trace-timeline").innerHTML = `<div class="empty-state"><span>任务加载失败：${escapeHtml(error.message)}</span></div>`;
  }
}

function resetTraceView() {
  $("#trace-state").textContent = "—";
  $("#trace-state").className = "status status-online";
  $("#trace-overview").textContent = "请选择上方任务查看执行链路。";
  $("#trace-agents").innerHTML = "";
  $("#trace-sources").innerHTML = "";
  $("#trace-meta").textContent = "";
}

async function openTrace(id) {
  if (!id) {
    resetTraceView();
    return;
  }
  try {
    const task = await api(`/v1/tasks/${encodeURIComponent(id)}`);
    renderTrace(task);
  } catch (error) {
    $("#trace-timeline").innerHTML = `<div class="empty-state"><span>${escapeHtml(error.message)}</span></div>`;
  }
}

function renderTrace(task) {
  const state = String(task.state || "PENDING").toUpperCase();
  const collaboration = (task.report && task.report.collaboration) || {};
  const events = (task.trace || []).map((item) => ({ time: item.created_at, order: 0, type: "event", data: item }));
  const messages = (task.collaboration || []).map((item, index) => ({ time: item.created_at, order: 1 + index / 1000, type: "message", data: item }));
  const timeline = [...events, ...messages]
    .filter((item) => item.time)
    .sort((a, b) => new Date(a.time) - new Date(b.time) || a.order - b.order);

  $("#trace-state").textContent = stateLabels[state] || state;
  $("#trace-state").className = `status state-${state.toLowerCase()}`;
  $("#trace-overview").innerHTML = `
    <div class="trace-overview-row"><span>仓库</span><b>${escapeHtml(task.repository || "未命名仓库")}</b></div>
    <div class="trace-overview-row"><span>PR</span><b>${task.pull_request ? `#${escapeHtml(task.pull_request)}` : "手动审查"}</b></div>
    <div class="trace-overview-row"><span>协议</span><b>${escapeHtml(collaboration.protocol || "—")}</b></div>
    <div class="trace-overview-row"><span>计划分配</span><b>${collaboration.planned_assignments ?? "—"}</b></div>
    <div class="trace-overview-row"><span>对话轮次</span><b>${collaboration.dialogue_rounds ?? "—"}</b></div>
    <div class="trace-overview-row"><span>消息数</span><b>${collaboration.messages ?? messages.length}</b></div>
    <div class="trace-overview-row"><span>重试 / 转派</span><b>${collaboration.retries ?? 0} / ${collaboration.handoffs ?? 0}</b></div>
    <div class="trace-overview-row"><span>结论</span><b>${collaboration.approved_findings ?? 0} 通过 / ${collaboration.rejected_findings ?? 0} 拒绝</b></div>`;

  const agents = collaboration.agents || [];
  $("#trace-agents").innerHTML = agents.length
    ? `<p class="list-section-label">Agent 状态</p>` + agents.map((agent) => {
        const status = String(agent.status || "unknown").toUpperCase();
        const done = status === "COMPLETED" || status === "SUCCESS";
        return `<div class="trace-agent">
          <span class="trace-agent-name">${escapeHtml(agent.agent)}</span>
          <span class="status ${done ? "state-success" : "state-pending"}">${done ? "完成" : escapeHtml(agent.status)}</span>
          <small>尝试 ${agent.attempts ?? 1}${agent.substituted_for ? ` · 替代 ${escapeHtml(agent.substituted_for)}` : ""}${agent.loop_steps ? ` · loop ${agent.loop_steps} 步` : ""}${agent.memories_recalled ? ` · 记忆 ${agent.memories_recalled} 条` : ""}</small>
        </div>`;
      }).join("")
    : "";

  const sources = collaboration.finding_sources || {};
  $("#trace-sources").innerHTML = Object.keys(sources).length
    ? Object.entries(sources).map(([key, agentsList]) => `
        <div class="trace-source"><code>${escapeHtml(key)}</code><span>${escapeHtml((agentsList || []).join("、"))}</span></div>`).join("")
    : '<p class="feedback-empty">无 Finding 来源记录</p>';

  $("#trace-meta").textContent = `共 ${timeline.length} 条记录`;
  $("#trace-timeline").innerHTML = timeline.length
    ? timeline.map((item) => item.type === "event" ? renderTraceEvent(item) : renderTraceMessage(item)).join("")
    : '<div class="empty-state"><span>该任务暂无链路记录</span></div>';
}

function renderTraceEvent(item) {
  const event = item.data;
  const state = String(event.state || "").toUpperCase();
  return `<div class="trace-item trace-event">
    <span class="trace-time">${escapeHtml(formatTime(event.created_at))}</span>
    <span class="status state-${state.toLowerCase()}">${stateLabels[state] || escapeHtml(event.state)}</span>
    <span class="trace-body"><b>${escapeHtml(event.message || "")}</b><small>step ${event.step ?? "?"}</small></span>
  </div>`;
}

function renderTraceMessage(item) {
  const message = item.data;
  const content = message.content || {};
  return `<details class="trace-item trace-message">
    <summary>
      <span class="trace-time">${escapeHtml(formatTime(message.created_at))}</span>
      <span class="trace-flow"><b>${escapeHtml(message.sender || "?")}</b><em>→</em><b>${escapeHtml(message.recipient || "?")}</b></span>
      <span class="trace-kind">${escapeHtml(message.kind || "message")}</span>
      <span class="trace-preview">${escapeHtml(summarizeTraceContent(content))}</span>
    </summary>
    <pre class="trace-content">${escapeHtml(formatJson(content))}</pre>
  </details>`;
}

function summarizeTraceContent(content) {
  if (!content || typeof content !== "object") return String(content ?? "");
  const picked = [];
  for (const key of ["title", "summary", "message", "text", "objective", "agent", "status"]) {
    if (content[key]) picked.push(String(content[key]));
  }
  if (!picked.length) {
    const keys = Object.keys(content);
    if (keys.length) {
      const first = content[keys[0]];
      picked.push(typeof first === "object" ? JSON.stringify(first) : String(first));
    }
  }
  const text = picked.join(" · ").replace(/\s+/g, " ").trim();
  return text.length > 90 ? `${text.slice(0, 90)}…` : text || "—";
}

$("#trace-task-select").addEventListener("change", (event) => openTrace(event.target.value));

function populateFeedbackFindings(findings) {
  const select = $("#feedback-finding");
  select.innerHTML = '<option value="">不关联已有结论</option>' + findings.map((finding, index) => {
    const identity = `${finding.rule_id || "未命名规则"} · ${finding.path || "未知文件"}:${finding.line || "?"}`;
    return `<option value="${index}">${escapeHtml(identity)}</option>`;
  }).join("");
  $("#feedback-result").textContent = "";
}

function renderTaskFeedback(cases) {
  const root = $("#task-feedback-history");
  if (!cases.length) {
    root.innerHTML = '<p class="feedback-empty">尚无反馈。提交后，它会在这里保留并进入后续评测。</p>';
    return;
  }
  root.innerHTML = `<p class="list-section-label">本任务反馈</p>${cases.map((item) => {
    const payload = item.payload || {};
    const finding = payload.finding || {};
    const reference = finding.rule_id
      ? `${finding.rule_id}${finding.path ? ` · ${finding.path}:${finding.line || "?"}` : ""}`
      : "未关联审查结论";
    return `<div class="feedback-case">
      <span class="feedback-case-type">${escapeHtml(feedbackLabels[item.category] || item.category)}</span>
      <span class="feedback-case-copy"><b>${escapeHtml(reference)}</b><small>${escapeHtml(payload.note || "未填写说明")}</small></span>
      <span class="status ${item.resolved ? "state-success" : "state-pending"}">${item.resolved ? "已解决" : "待评测"}</span>
    </div>`;
  }).join("")}`;
}

async function loadTaskFeedback(taskId) {
  const root = $("#task-feedback-history");
  root.innerHTML = '<p class="feedback-empty">正在读取本任务反馈…</p>';
  try {
    const data = await api(`/v1/tasks/${encodeURIComponent(taskId)}/feedback`);
    if (selectedTask === taskId) renderTaskFeedback(data.cases || []);
  } catch (error) {
    root.innerHTML = `<p class="feedback-empty">无法读取反馈历史：${escapeHtml(error.message)}</p>`;
  }
}

async function loadSkills() {
  const root = $("#skill-list");
  root.innerHTML = '<div class="skill-card loading"></div><div class="skill-card loading"></div>';
  try {
    const data = await api("/api/skills");
    renderLlmRuntime(data.llm);
    const skills = (data.skills || []).filter((skill) => skill.name !== "llm-review");
    root.innerHTML = skills.length ? skills.map((skill) => `
      <article class="skill-card">
        <span class="skill-label">${skill.sandboxed ? "SANDBOXED SKILL" : "ACTIVE SKILL"}</span>
        <h3>${escapeHtml(skill.name)}</h3>
        <p>${escapeHtml(skill.description || "暂无能力描述")}</p>
        <span class="skill-meta">v${escapeHtml(skill.version)} · ${escapeHtml(skill.source)}</span>
      </article>`).join("") : '<div class="empty-state"><span><b>尚未加载 Skill</b>扫描目录以加载可用能力</span></div>';
  } catch (error) {
    renderLlmRuntime({ error: true });
    root.innerHTML = '<div class="empty-state"><span>Skills 加载失败</span></div>';
    toast(error.message);
  }
}

async function loadFailures() {
  try {
    const [failuresData, status, runsData] = await Promise.all([
      api("/api/failures"),
      api("/v1/evolution/status"),
      api("/v1/evolution/runs?limit=5"),
    ]);
    $("#evolution-status").textContent = formatJson(status);
    const cases = failuresData.cases || [];
    const runs = runsData.runs || [];
    const failureHtml = cases.length
      ? cases.slice(0, 8).map((item) => `
          <div class="task-row">
            <span class="task-main"><span class="task-glyph">FC</span><span class="task-copy">
              <span class="task-name">${escapeHtml(feedbackLabels[item.category] || item.category)}</span>
              <span class="task-meta"><span>${escapeHtml(item.task_id)}</span><span>${escapeHtml((item.payload || {}).note || "无说明")}</span></span>
            </span></span>
            <span class="status ${item.resolved ? "state-success" : "state-pending"}">${item.resolved ? "已解决" : "待处理"}</span>
          </div>`).join("")
      : '<div class="empty-state"><span><b>暂无失败反馈</b>系统当前没有未处理案例</span></div>';
    const historyHtml = runs.length
      ? `<p class="list-section-label">最近评测</p>${runs.map((run) => `
          <div class="task-row">
            <span class="task-main"><span class="task-glyph">V${escapeHtml(run.candidate_version)}</span><span class="task-copy">
              <span class="task-name">${escapeHtml(run.decision)}</span>
              <span class="task-meta">${Number(run.candidate_score).toFixed(3)} vs ${Number(run.baseline_score).toFixed(3)}</span>
            </span></span>
          </div>`).join("")}`
      : "";
    $("#failure-list").innerHTML = failureHtml + historyHtml;
  } catch (error) {
    $("#evolution-status").textContent = "暂时无法读取评测状态。";
    $("#failure-list").innerHTML = '<div class="empty-state"><span>反馈加载失败</span></div>';
    toast(error.message);
  }
}

$("#review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  const body = { repository: values.get("repository"), diff: values.get("diff") };
  if (values.get("pull_request")) body.pull_request = Number(values.get("pull_request"));
  const asyncQuery = values.get("async") ? "?async=true" : "";
  const output = $("#review-result");
  output.classList.remove("empty");
  output.textContent = "正在提交审查任务…";
  setButtonBusy(button, true, "正在提交…");
  try {
    const data = await api(`/v1/reviews${asyncQuery}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    output.textContent = formatJson(data);
    toast("审查任务已成功提交");
    loadDashboard();
  } catch (error) {
    output.textContent = error.message;
  } finally {
    setButtonBusy(button, false);
  }
});

$("#create-fix").addEventListener("click", async () => {
  if (!selectedTask) return;
  const button = $("#create-fix");
  setButtonBusy(button, true, "正在创建…");
  try {
    const data = await api(`/v1/tasks/${encodeURIComponent(selectedTask)}/fix`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    $("#task-report").textContent = formatJson(data);
    toast("修复分支已创建");
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#feedback-category").addEventListener("change", (event) => {
  const missed = event.target.value === "missed_issue";
  $("#feedback-missed-fields").classList.toggle("hidden", !missed);
  $("#feedback-hint").textContent = missed
    ? "补充规则和位置可让候选评测学习更精确的检查点。"
    : "提交后可在本任务和演进实验室查看状态。";
});

$("#feedback-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedTask || !selectedTaskData?.report) return;
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  const category = String(values.get("category"));
  const selectedIndex = values.get("finding_index");
  const findings = selectedTaskData.report.findings || [];
  const finding = selectedIndex === "" ? {} : { ...(findings[Number(selectedIndex)] || {}) };
  if (category === "missed_issue") {
    const ruleId = String(values.get("rule_id") || "").trim();
    const path = String(values.get("path") || "").trim();
    const line = Number(values.get("line"));
    if (ruleId) finding.rule_id = ruleId;
    if (path) finding.path = path;
    if (Number.isInteger(line) && line > 0) finding.line = line;
  }
  const output = $("#feedback-result");
  output.textContent = "正在保存反馈…";
  setButtonBusy(button, true, "正在提交…");
  try {
    const data = await api(`/v1/tasks/${encodeURIComponent(selectedTask)}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category,
        finding: Object.keys(finding).length ? finding : null,
        note: String(values.get("note") || "").trim(),
      }),
    });
    output.textContent = `${feedbackLabels[data.category] || data.category}已记录；可在演进实验室等待候选评测。`;
    form.reset();
    $("#feedback-missed-fields").classList.add("hidden");
    $("#feedback-hint").textContent = "提交后可在本任务和演进实验室查看状态。";
    await Promise.all([loadTaskFeedback(selectedTask), loadDashboard()]);
    toast("反馈已记录");
  } catch (error) {
    output.textContent = `提交失败：${error.message}`;
  } finally {
    setButtonBusy(button, false);
  }
});

$("#reload-skills").addEventListener("click", async () => {
  const button = $("#reload-skills");
  setButtonBusy(button, true, "正在扫描…");
  try {
    await api("/v1/skills/reload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    await loadSkills();
    toast("Skills 已重新加载");
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#evolution-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  setButtonBusy(button, true, "正在评测…");
  try {
    const data = await api("/v1/evolution/propose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_name: values.get("skill_name"), prompt: values.get("prompt") }),
    });
    $("#evolution-result").classList.remove("empty");
    $("#evolution-result").textContent = formatJson(data);
    toast("新旧版本回放评测已完成");
    loadFailures();
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#auto-evolve").addEventListener("click", async () => {
  const button = $("#auto-evolve");
  setButtonBusy(button, true, "正在生成…");
  try {
    const data = await api("/v1/evolution/auto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_name: "llm-review" }),
    });
    $("#evolution-result").classList.remove("empty");
    $("#evolution-result").textContent = formatJson(data);
    toast("反馈候选评测已完成");
    loadFailures();
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#refresh").addEventListener("click", async () => {
  const view = location.hash.slice(1) || "overview";
  if (view === "overview") await loadDashboard();
  else if (view === "tasks") await loadTasks();
  else if (view === "chat") await loadDashboard().then(loadChat);
  else if (view === "trace") await loadTraceTasks();
  else if (view === "skills") await loadSkills();
  else if (view === "evolution") await loadFailures();
  else await loadDashboard();
  toast("数据已刷新");
});

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  setButtonBusy(button, true, "正在登录…");
  try {
    const data = await api("/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: values.get("username"),
        password: values.get("password"),
        tenant_id: values.get("tenant_id"),
      }),
    });
    accessToken = data.access_token;
    localStorage.setItem("evoagent_token", accessToken);
    $("#login-overlay").classList.add("hidden");
    $("#logout").classList.remove("hidden");
    $("#login-error").textContent = "";
    await loadDashboard();
  } catch (error) {
    $("#login-error").textContent = error.message;
  } finally {
    setButtonBusy(button, false);
  }
});

$("#logout").addEventListener("click", () => {
  accessToken = "";
  localStorage.removeItem("evoagent_token");
  $("#login-overlay").classList.remove("hidden");
  $("#logout").classList.add("hidden");
});

const diffInput = $('textarea[name="diff"]', $("#review-form"));
const diffStats = $("#diff-stats");
function updateDiffStats() {
  const value = diffInput.value;
  const lines = value ? value.split(/\r?\n/).length : 0;
  diffStats.textContent = `${lines} 行，${value.length} 字符`;
}
diffInput.addEventListener("input", updateDiffStats);
updateDiffStats();

/* ---- WP4: 报告对话工作台 ---- */
const chat = {
  status: { enabled: false, insights_enabled: false, feedback_enabled: false, model_configured: false },
  statusLoaded: false,
  taskId: "",
  sessionId: "",
  sessions: [],
  messages: [],
  insights: [],
  task: null,
  generation: 0,
  sending: false,
  editingInsightId: null,
};

const chatCategoryLabels = {
  false_positive: "误报",
  missed_issue: "漏报",
  bad_fix: "坏修复",
  accepted: "已接受",
};
const chatInsightStatusLabels = {
  draft: "草稿",
  confirmed: "已确认",
  rejected: "已驳回",
  superseded: "已取代",
};
const chatCitationTypeLabels = {
  finding: "Finding",
  diff: "Diff",
  report: "报告",
  trace: "Trace",
  memory: "记忆",
};

function setChatStatus() {
  const { enabled, insights_enabled, feedback_enabled, model_configured } = chat.status;
  const statusEl = $("#chat-status");
  const noteEl = $("#chat-status-note");
  if (!enabled) {
    statusEl.className = "runtime-status is-disabled";
    statusEl.textContent = "已关闭";
    noteEl.textContent = "报告对话功能未启用（EVOAGENT_CHAT_ENABLED=false）。";
    return;
  }
  statusEl.className = "runtime-status is-active";
  const stages = ["问答"];
  if (insights_enabled) stages.push("候选草稿");
  if (feedback_enabled) stages.push("确认沉淀");
  statusEl.textContent = `已启用 · ${stages.join(" / ")}`;
  noteEl.textContent = model_configured
    ? "模型已就绪，可开始对话。"
    : "分析模型未配置：可新建会话并查看历史，但发送消息会返回 409。";
}

function chatComposerEnabled() {
  return chat.status.enabled && chat.status.model_configured && !chat.sending;
}

function updateChatComposer() {
  const input = $("#chat-input");
  const send = $("#chat-send");
  const disabled = !chatComposerEnabled() || !chat.sessionId;
  send.disabled = disabled;
  input.readOnly = !chat.sessionId;
  if (!chat.sessionId) {
    input.placeholder = "请先选择或新建一个会话";
  } else if (!chat.status.model_configured) {
    input.placeholder = "分析模型未配置，暂无法发送消息";
  } else {
    input.placeholder = "例如：SEC-EVAL 为什么是高风险？这里是否可能是误报？";
  }
}

async function loadChat() {
  if (!chat.statusLoaded) {
    try {
      const data = await api("/api/dashboard");
      if (data.chat) chat.status = { enabled: false, insights_enabled: false, feedback_enabled: false, model_configured: false, ...data.chat };
      chat.statusLoaded = true;
    } catch (error) {
      /* fall through with default status; the request below will surface issues */
    }
  }
  setChatStatus();
  const select = $("#chat-task-select");
  if (!chat.status.enabled) {
    select.innerHTML = '<option value="">报告对话未启用</option>';
    $("#chat-session-list").innerHTML = '<div class="empty-state"><span>功能未启用，请在服务端开启 EVOAGENT_CHAT_ENABLED</span></div>';
    resetChatThread();
    updateChatComposer();
    return;
  }
  try {
    const data = await api("/api/tasks?limit=100");
    const tasks = (data.tasks || []).filter((task) => task.state === "SUCCESS" && task.report);
    const previous = chat.taskId;
    select.innerHTML = tasks.length
      ? '<option value="">选择任务…</option>' + tasks.map((task) => `
          <option value="${escapeHtml(task.id)}">${escapeHtml(task.repository || "未命名仓库")}${task.pull_request ? ` · PR #${escapeHtml(task.pull_request)}` : ""}</option>`).join("")
      : '<option value="">暂无已完成任务</option>';
    if (previous && tasks.some((task) => task.id === previous)) select.value = previous;
    else if (tasks.length && !select.value) select.value = tasks[0] ? tasks[0].id : "";
    if (chat.pendingTaskId && tasks.some((task) => task.id === chat.pendingTaskId)) {
      select.value = chat.pendingTaskId;
      chat.pendingTaskId = "";
    }
    if (select.value) await selectChatTask(select.value);
    else resetChatThread();
  } catch (error) {
    $("#chat-session-list").innerHTML = `<div class="empty-state"><span>任务加载失败：${escapeHtml(error.message)}</span></div>`;
    toast(error.message);
  }
}

async function selectChatTask(taskId) {
  chat.generation += 1;
  const generation = chat.generation;
  chat.taskId = taskId;
  chat.sessionId = "";
  chat.messages = [];
  chat.insights = [];
  chat.task = null;
  $("#chat-new-session").classList.toggle("hidden", !taskId);
  $("#chat-thread").innerHTML = '<div class="list-loading"></div>';
  $("#chat-session-title").textContent = "未选择会话";
  $("#chat-thread-meta").textContent = "";
  $("#chat-session-state").textContent = "—";
  clearChatEvidence();
  updateChatComposer();
  if (!taskId) {
    $("#chat-session-list").innerHTML = '<div class="empty-state"><span>请先选择一个已完成的任务</span></div>';
    return;
  }
  try {
    const [sessionsData, task] = await Promise.all([
      api(`/v1/tasks/${encodeURIComponent(taskId)}/chat/sessions`),
      api(`/v1/tasks/${encodeURIComponent(taskId)}`),
    ]);
    if (generation !== chat.generation) return;
    chat.sessions = sessionsData.sessions || [];
    chat.task = task;
    renderChatSessions();
    renderChatEvidence();
    if (chat.sessions.length) {
      await openChatSession(chat.sessions[0].id);
    } else {
      $("#chat-thread").innerHTML = '<div class="empty-state"><span>还没有会话，点击右上角“新建会话”开始分析</span></div>';
    }
  } catch (error) {
    if (generation !== chat.generation) return;
    $("#chat-session-list").innerHTML = `<div class="empty-state"><span>${escapeHtml(error.message)}</span></div>`;
  }
}

function renderChatSessions() {
  const root = $("#chat-session-list");
  if (!chat.sessions.length) {
    root.innerHTML = '<div class="empty-state"><span>暂无会话，点击“新建会话”开始</span></div>';
    return;
  }
  root.innerHTML = chat.sessions.map((session) => {
    const active = session.id === chat.sessionId;
    const state = String(session.status || "active");
    return `
      <div class="chat-session-row ${active ? "active" : ""} ${state === "stale" ? "stale" : ""}" data-session="${escapeHtml(session.id)}">
        <button class="chat-session-main" data-session-open="${escapeHtml(session.id)}" type="button">
          <span class="chat-session-title">${escapeHtml(session.title || "报告分析")}</span>
          <span class="chat-session-meta">${escapeHtml(formatTime(session.updated_at || session.created_at))}${state === "stale" ? " · 报告已变化" : ""}</span>
        </button>
        <span class="status ${state === "active" ? "state-success" : state === "stale" ? "state-pending" : "status-neutral"}">${state === "active" ? "进行中" : state === "stale" ? "已过期" : "已归档"}</span>
        ${state !== "archived" ? `<button class="chat-session-archive" data-session-archive="${escapeHtml(session.id)}" title="归档会话（保留审计，不删除）" type="button">归档</button>` : ""}
      </div>`;
  }).join("");
  $$("[data-session-open]", root).forEach((btn) => btn.addEventListener("click", () => openChatSession(btn.dataset.sessionOpen)));
  $$("[data-session-archive]", root).forEach((btn) => btn.addEventListener("click", () => archiveChatSession(btn.dataset.sessionArchive)));
  $("#chat-new-session").classList.remove("hidden");
}

async function archiveChatSession(sessionId) {
  try {
    const updated = await api(`/v1/chat/sessions/${encodeURIComponent(sessionId)}/archive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    chat.sessions = chat.sessions.map((session) => session.id === sessionId ? updated : session);
    renderChatSessions();
    if (chat.sessionId === sessionId) {
      $("#chat-session-state").textContent = "已归档";
      $("#chat-session-state").className = "runtime-status";
    }
    toast("会话已归档（保留审计记录）");
  } catch (error) {
    toast(error.message);
  }
}

async function openChatSession(sessionId) {
  if (!sessionId) return;
  chat.generation += 1;
  const generation = chat.generation;
  chat.sessionId = sessionId;
  chat.messages = [];
  chat.insights = [];
  $("#chat-thread").innerHTML = '<div class="list-loading"></div>';
  updateChatComposer();
  try {
    const session = await api(`/v1/chat/sessions/${encodeURIComponent(sessionId)}`);
    if (generation !== chat.generation) return;
    chat.messages = session.messages || [];
    chat.insights = session.insights || [];
    $("#chat-session-title").textContent = session.title || "报告分析";
    $("#chat-thread-meta").textContent = `报告指纹 ${(session.report_fingerprint || "").slice(0, 12)}…`;
    const state = String(session.status || "active");
    $("#chat-session-state").textContent = state === "active" ? "进行中" : state === "stale" ? "已过期" : "已归档";
    $("#chat-session-state").className = `runtime-status ${state === "active" ? "is-active" : state === "stale" ? "is-disabled" : ""}`;
    renderChatMessages();
    renderChatInsights();
    renderChatSessionListActive();
  } catch (error) {
    if (generation !== chat.generation) return;
    $("#chat-thread").innerHTML = `<div class="empty-state"><span>${escapeHtml(error.message)}</span></div>`;
  }
}

function renderChatSessionListActive() {
  $$(".chat-session-row").forEach((row) => row.classList.toggle("active", row.dataset.session === chat.sessionId));
}

function renderChatMessages() {
  const thread = $("#chat-thread");
  if (!chat.messages.length) {
    thread.innerHTML = '<div class="empty-state"><span>还没有消息，向助理提出你的第一个问题</span></div>';
    renderChatSuggestions();
    return;
  }
  thread.innerHTML = chat.messages.map((message) => {
    if (message.role === "user") return renderUserMessage(message);
    return renderAssistantMessage(message);
  }).join("");
  bindChatMessageActions(thread);
  renderChatSuggestions();
  thread.scrollTop = thread.scrollHeight;
}

function renderUserMessage(message) {
  const failed = message.status === "failed";
  return `
    <div class="chat-msg chat-msg-user ${failed ? "failed" : ""}">
      <div class="chat-msg-bubble">${escapeHtml(message.content || "")}</div>
      ${failed ? `<div class="chat-msg-error">发送失败：${escapeHtml(message.error || "未知错误")}<button class="link-button" data-retry="${escapeHtml(message.client_request_id || "")}" data-content="${escapeHtml(message.content)}">重试</button></div>` : ""}
    </div>`;
}

function renderAssistantMessage(message) {
  const citations = message.citations || [];
  const meta = [message.model || "", message.prompt_version || ""].filter(Boolean).join(" · ");
  return `
    <div class="chat-msg chat-msg-assistant">
      <div class="chat-msg-bubble">${escapeHtml(message.content || "")}</div>
      ${citations.length ? `<div class="chat-citations">${citations.map(renderCitationChip).join("")}</div>` : ""}
      ${meta ? `<div class="chat-msg-meta">${escapeHtml(meta)}</div>` : ""}
    </div>`;
}

function renderCitationChip(citation) {
  const type = citation.type || "finding";
  const label = chatCitationTypeLabels[type] || type;
  if (type === "finding") {
    const ref = String(citation.ref || "");
    return `<button class="chat-citation" data-cite="finding" data-ref="${escapeHtml(ref)}" type="button">${label} ${escapeHtml(ref.replace("finding:", ""))}</button>`;
  }
  if (type === "diff") {
    return `<button class="chat-citation" data-cite="diff" data-path="${escapeHtml(citation.path || "")}" data-line="${escapeHtml(citation.line ?? "")}" type="button">${label} ${escapeHtml([citation.path, citation.line].filter((v) => v !== "" && v != null).join(":"))}</button>`;
  }
  return `<span class="chat-citation static">${label}</span>`;
}

function bindChatMessageActions(root) {
  $$("[data-cite]", root).forEach((chip) => chip.addEventListener("click", () => highlightEvidence(chip)));
  $$("[data-retry]", root).forEach((btn) => btn.addEventListener("click", () => retryChatMessage(btn)));
}

function highlightEvidence(chip) {
  const scopeEl = $("#chat-evidence-scope");
  const baseScope = chat.task ? `${chat.task.repository || ""} · ${(chat.task.report || {}).risk || "风险未知"}` : "—";
  if (chip.dataset.cite === "finding") {
    const ref = chip.dataset.ref || "";
    const index = Number(ref.replace("finding:", ""));
    const targets = $$(".chat-finding-row");
    const target = targets[index];
    if (target) {
      target.scrollIntoView({ behavior: reduceMotion.matches ? "auto" : "smooth", block: "center" });
      /* aria-live announcement */
      scopeEl.textContent = `已定位 ${ref}`;
      setTimeout(() => { scopeEl.textContent = baseScope; }, 1800);
    }
  } else if (chip.dataset.cite === "diff") {
    const path = chip.dataset.path;
    const line = chip.dataset.line;
    const targets = $$(".chat-finding-row").filter((row) => row.dataset.path === path && String(row.dataset.line) === line);
    if (targets[0]) {
      targets[0].scrollIntoView({ behavior: reduceMotion.matches ? "auto" : "smooth", block: "center" });
    }
  }
}

function renderChatEvidence() {
  clearChatEvidence();
  if (!chat.task) {
    $("#chat-evidence-scope").textContent = "—";
    return;
  }
  const report = chat.task.report || {};
  $("#chat-evidence-scope").textContent = `${chat.task.repository || ""} · ${report.risk || "风险未知"}`;
  const summary = report.summary;
  const summaryEl = $("#chat-report-summary .chat-report-summary");
  summaryEl.textContent = summary || "（无摘要）";

  const findings = report.findings || [];
  const findingsRoot = $("#chat-evidence-findings .chat-finding-list");
  findingsRoot.innerHTML = findings.length
    ? findings.map((finding, index) => `
        <div class="chat-finding-row" data-index="${index}" data-path="${escapeHtml(finding.path || "")}" data-line="${escapeHtml(finding.line ?? "")}">
          <div class="chat-finding-head">
            <span class="chat-finding-idx">#${index}</span>
            <span class="chat-finding-rule">${escapeHtml(finding.rule_id || "未命名规则")}</span>
            <span class="chat-finding-conf">${Number(finding.confidence ?? 0).toFixed(2)}</span>
          </div>
          <span class="chat-finding-loc">${escapeHtml(finding.path || "未知文件")}:${escapeHtml(finding.line ?? "?")}</span>
          <span class="chat-finding-title">${escapeHtml(finding.title || "")}</span>
        </div>`).join("")
    : '<p class="feedback-empty">无 Finding</p>';
}

function clearChatEvidence() {
  $("#chat-report-summary .chat-report-summary").textContent = "—";
  $("#chat-evidence-findings .chat-finding-list").innerHTML = "";
  $("#chat-insight-list").innerHTML = "";
}

function renderChatSuggestions() {
  const wrap = $("#chat-suggestions");
  if (!chat.status.model_configured || chat.sending || !chat.messages.length) {
    wrap.hidden = true;
    return;
  }
  const suggestions = [
    "这个任务的最高风险是什么？",
    "是否有可能是误报？为什么？",
    "哪些 Finding 缺少证据？",
    "请给出最需要关注的 3 个结论。",
  ];
  wrap.hidden = false;
  wrap.innerHTML = suggestions.map((text) => `<button class="chat-suggestion" type="button">${escapeHtml(text)}</button>`).join("");
  $$(".chat-suggestion", wrap).forEach((btn) => btn.addEventListener("click", () => {
    $("#chat-input").value = btn.textContent;
    sendChatMessage();
  }));
}

async function createChatSession() {
  if (!chat.taskId) return;
  if (!chat.status.model_configured) {
    toast("分析模型未配置，仍可创建会话查看历史，但无法发送消息。");
  }
  const title = `报告分析 ${formatTime(new Date().toISOString())}`;
  const button = $("#chat-new-session");
  setButtonBusy(button, true, "创建中…");
  try {
    const session = await api(`/v1/tasks/${encodeURIComponent(chat.taskId)}/chat/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    chat.sessions = [session, ...chat.sessions].sort((a, b) => new Date(b.updated_at || b.created_at) - new Date(a.updated_at || a.created_at));
    renderChatSessions();
    await openChatSession(session.id);
    $("#chat-input").focus();
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

async function sendChatMessage(contentOverride) {
  if (!chat.sessionId || chat.sending) return;
  const input = $("#chat-input");
  const content = (contentOverride ?? input.value).trim();
  if (!content) return;
  if (!chat.status.model_configured) {
    $("#chat-error").textContent = "分析模型未配置，无法发送消息。";
    return;
  }
  chat.sending = true;
  const sendButton = $("#chat-send");
  sendButton.disabled = true;
  sendButton.textContent = "分析中…";
  $("#chat-error").textContent = "";
  const clientRequestId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
  const generation = chat.generation;
  try {
    const data = await api(`/v1/chat/sessions/${encodeURIComponent(chat.sessionId)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, client_request_id: clientRequestId }),
    });
    if (generation !== chat.generation) return;
    chat.messages = data.messages || [];
    chat.insights = data.insights || [];
    input.value = "";
    renderChatMessages();
    renderChatInsights();
  } catch (error) {
    if (generation !== chat.generation) return;
    $("#chat-error").textContent = `发送失败：${error.message}`;
    // Preserve input for retry.
    if (!contentOverride) input.value = content;
    // Refresh the thread to surface the persisted failed message.
    await openChatSession(chat.sessionId);
  } finally {
    chat.sending = false;
    updateChatComposer();
    sendButton.textContent = "发送";
  }
}

function retryChatMessage(button) {
  const content = button.dataset.content || "";
  $("#chat-input").value = content;
  sendChatMessage(content);
}

function renderChatInsights() {
  const root = $("#chat-insight-list");
  if (!chat.insights.length) {
    root.innerHTML = "";
    return;
  }
  const feedbackEnabled = chat.status.feedback_enabled;
  root.innerHTML = `<p class="list-section-label">候选结论</p>` + chat.insights.map((insight) => {
    if (chat.editingInsightId === insight.id) return renderInsightEditor(insight);
    const finding = insight.finding || {};
    const reference = finding.rule_id
      ? `${finding.rule_id}${finding.path ? ` · ${finding.path}:${finding.line || "?"}` : ""}`
      : finding.path ? `${finding.path}:${finding.line || "?"}` : "未关联 Finding";
    const validation = insight.validation || {};
    const validated = validation.valid !== false;
    const status = String(insight.status || "draft");
    const confirmed = status === "confirmed";
    const rejected = status === "rejected";
    const warnings = validation.warnings || [];
    return `
      <div class="chat-insight-card ${status}">
        <div class="chat-insight-head">
          <span class="chat-insight-type">${escapeHtml(chatCategoryLabels[insight.category] || insight.category)}</span>
          <span class="chat-insight-conf">置信度 ${Number(insight.confidence ?? 0).toFixed(2)}</span>
          <span class="status ${confirmed ? "state-success" : rejected ? "state-failed" : "state-pending"}">${escapeHtml(chatInsightStatusLabels[status] || status)}</span>
        </div>
        <span class="chat-insight-ref">${escapeHtml(reference)}</span>
        ${insight.note ? `<p class="chat-insight-note">${escapeHtml(insight.note)}</p>` : ""}
        <div class="chat-insight-validation">
          <span class="${validated ? "ok" : "warn"}">${validated ? "证据校验通过" : "证据不完整"}</span>
          ${validation.issues?.length ? `<small>${escapeHtml(validation.issues.join("；"))}</small>` : ""}
        </div>
        ${warnings.length ? `<div class="chat-insight-warning">${escapeHtml(warnings.join("；"))}</div>` : ""}
        ${status === "draft" ? `<div class="chat-insight-actions">
          <button class="button ${feedbackEnabled ? "" : "avoid-disabled"}" data-insight-confirm="${escapeHtml(insight.id)}" ${feedbackEnabled ? "" : 'disabled title="沉淀功能尚未启用（EVOAGENT_CHAT_FEEDBACK_ENABLED=false）"'}>确认沉淀</button>
          <button class="button secondary" data-insight-edit="${escapeHtml(insight.id)}">编辑</button>
          <button class="button secondary" data-insight-reject="${escapeHtml(insight.id)}">驳回</button>
        </div>` : ""}
        ${confirmed && insight.feedback_case_id ? `<div class="chat-insight-sediment">已进入沉淀链路 · Failure Case #${escapeHtml(String(insight.feedback_case_id))}</div>` : ""}
      </div>`;
  }).join("");
  $$("[data-insight-reject]", root).forEach((btn) => btn.addEventListener("click", () => rejectChatInsight(btn.dataset.insightReject)));
  $$("[data-insight-confirm]", root).forEach((btn) => btn.addEventListener("click", () => confirmChatInsight(btn.dataset.insightConfirm)));
  $$("[data-insight-edit]", root).forEach((btn) => btn.addEventListener("click", () => { chat.editingInsightId = btn.dataset.insightEdit; renderChatInsights(); }));
  $$("[data-edit-save]", root).forEach((btn) => btn.addEventListener("click", () => saveInsightEdit(btn.dataset.editSave)));
  $$("[data-edit-cancel]", root).forEach((btn) => btn.addEventListener("click", () => { chat.editingInsightId = null; renderChatInsights(); }));
}

function renderInsightEditor(insight) {
  const finding = insight.finding || {};
  const selectOption = (value, label) =>
    `<option value="${value}" ${insight.category === value ? "selected" : ""}>${label}</option>`;
  return `
    <div class="chat-insight-card editing" data-editor="${escapeHtml(insight.id)}">
      <div class="chat-insight-head">
        <span class="chat-insight-type">编辑候选</span>
        <span class="chat-insight-conf">编辑后重新校验</span>
      </div>
      <label class="chat-edit-label">类型
        <select data-edit-category>
          ${selectOption("false_positive", "误报")}
          ${selectOption("missed_issue", "漏报")}
          ${selectOption("bad_fix", "坏修复")}
          ${selectOption("accepted", "已接受")}
        </select>
      </label>
      <div class="chat-edit-location">
        <label>规则 ID<input data-edit-rule value="${escapeHtml(finding.rule_id || "")}"></label>
        <label>文件路径<input data-edit-path value="${escapeHtml(finding.path || "")}"></label>
        <label>行号<input data-edit-line type="number" min="1" value="${escapeHtml(finding.line ?? "")}"></label>
      </div>
      <label class="chat-edit-label">说明<textarea data-edit-note rows="3">${escapeHtml(insight.note || "")}</textarea></label>
      <div class="chat-insight-actions">
        <button class="button" data-edit-save="${escapeHtml(insight.id)}">保存</button>
        <button class="button secondary" data-edit-cancel="${escapeHtml(insight.id)}">取消</button>
      </div>
    </div>`;
}

async function saveInsightEdit(insightId) {
  const card = $(`[data-editor="${insightId}"]`);
  if (!card) return;
  const line = Number($("[data-edit-line]", card).value);
  const finding = {
    rule_id: String($("[data-edit-rule]", card).value || "").trim(),
    path: String($("[data-edit-path]", card).value || "").trim(),
  };
  if (Number.isInteger(line) && line > 0) finding.line = line;
  try {
    const updated = await api(`/v1/chat/insights/${encodeURIComponent(insightId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category: String($("[data-edit-category]", card).value),
        finding,
        note: String($("[data-edit-note]", card).value || "").trim(),
      }),
    });
    chat.insights = chat.insights.map((insight) => insight.id === updated.id ? updated : insight);
    chat.editingInsightId = null;
    renderChatInsights();
    toast("候选已更新并重新校验");
  } catch (error) {
    toast(error.message);
  }
}

async function confirmChatInsight(insightId) {
  if (!chat.status.feedback_enabled) {
    toast("沉淀功能尚未启用（EVOAGENT_CHAT_FEEDBACK_ENABLED=false）");
    return;
  }
  try {
    const data = await api(`/v1/chat/insights/${encodeURIComponent(insightId)}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const updated = data.insight;
    chat.insights = chat.insights.map((insight) => insight.id === updated.id ? updated : insight);
    renderChatInsights();
    toast(`候选已确认，进入沉淀链路（Failure Case #${updated.feedback_case_id ?? "?"}）`);
  } catch (error) {
    toast(error.message);
  }
}

async function rejectChatInsight(insightId) {
  try {
    const data = await api(`/v1/chat/insights/${encodeURIComponent(insightId)}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const updated = data.insight;
    chat.insights = chat.insights.map((insight) => insight.id === updated.id ? updated : insight);
    renderChatInsights();
    toast("候选结论已驳回");
  } catch (error) {
    toast(error.message);
  }
}

function resetChatThread() {
  $("#chat-session-title").textContent = "未选择会话";
  $("#chat-thread-meta").textContent = "";
  $("#chat-session-state").textContent = "—";
  $("#chat-session-state").className = "runtime-status";
  $("#chat-thread").innerHTML = '<div class="empty-state"><span>请先选择一个已完成的任务</span></div>';
  $("#chat-suggestions").hidden = true;
  $("#chat-new-session").classList.add("hidden");
  updateChatComposer();
}

$("#chat-task-select").addEventListener("change", (event) => selectChatTask(event.target.value));
$("#chat-new-session").addEventListener("click", createChatSession);
$("#chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  sendChatMessage();
});
$("#chat-analyze").addEventListener("click", () => {
  if (!selectedTask) return;
  chat.pendingTaskId = selectedTask;
  show("chat");
});

if (accessToken) $("#logout").classList.remove("hidden");
show(location.hash.slice(1) || "overview", false);
loadDashboard();
