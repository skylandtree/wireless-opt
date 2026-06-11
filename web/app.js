const $ = (id) => document.getElementById(id);

const prompts = [
  "查询高科路_001小区的工单信息，分析根因，并生成优化方案",
  "世纪大道_005小区最近投诉多，判断是弱覆盖还是干扰，并给出处理建议",
  "对张江_012小区生成优化方案，但不要下发，先做风险评估",
  "请下发陆家嘴_003小区的容量优化方案并进入效果观察",
  "评估昨天对陆家嘴_003小区下发方案后的效果",
];

const causeNames = {
  coverage: "覆盖",
  interference: "干扰",
  capacity: "容量",
  handover: "切换",
};

const loopSkillMap = [
  ["ticket_query", 0],
  ["cell_profile", 1],
  ["kpi_fetch", 1],
  ["coverage_analysis", 1],
  ["interference_analysis", 1],
  ["capacity_analysis", 1],
  ["handover_analysis", 1],
  ["root_cause_ranker", 2],
  ["solution_generator", 3],
  ["risk_guard", 3],
  ["report_writer", 4],
];

let latestPlan = null;
let dagState = [];
let selectedTicket = null;
let ticketsCache = [];
let kpiTimer = null;
let ticketScrollTimer = null;
let statsTimer = null;
let currentKpis = [];
let resultLines = [];
let causeScores = { coverage: 0.25, interference: 0.18, capacity: 0.2, handover: 0.15 };

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "请求失败");
  return data;
}

async function health() {
  try {
    await api("/api/health");
    $("health-dot").classList.add("ok");
    $("health-text").textContent = "在线";
  } catch {
    $("health-dot").classList.remove("ok");
    $("health-text").textContent = "离线";
  }
}

function setupPrompts() {
  const box = $("quick-prompts");
  box.innerHTML = "";
  prompts.forEach((text) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = text;
    chip.onclick = () => {
      selectedTicket = null;
      $("query").value = text;
      previewPlan();
    };
    box.appendChild(chip);
  });
}

async function loadTickets() {
  const data = await api("/api/tickets");
  ticketsCache = data.tickets || [];
  $("summary-open").textContent = ticketsCache.length;
  renderTickets(ticketsCache, true);
  if (ticketsCache.length) selectTicket(ticketsCache[0], false);
  startTicketAutoScroll();
  startStatsMotion();
}

function renderTickets(tickets = [], keepSelection = false) {
  const box = $("tickets");
  $("ticket-count").textContent = `${tickets.length} 条`;
  box.innerHTML = "";
  tickets.forEach((ticket) => {
    const item = document.createElement("div");
    item.className = `ticket ${selectedTicket?.ticket_id === ticket.ticket_id ? "selected" : ""}`;
    item.innerHTML = `
      <header><strong>${ticket.ticket_id}</strong><span class="badge">${ticket.severity}</span></header>
      <p>${ticket.title}</p>
      <p>${ticket.description}</p>
      <p class="meta">${ticket.created_at.slice(0, 16).replace("T", " ")} · ${ticket.cell_name || ticket.cell_id} · ${ticket.status}</p>
    `;
    item.onclick = () => selectTicket(ticket, true);
    box.appendChild(item);
  });
  if (!keepSelection && selectedTicket) {
    [...box.children].forEach((child) => child.classList.toggle("selected", child.textContent.includes(selectedTicket.ticket_id)));
  }
}

function startTicketAutoScroll() {
  if (ticketScrollTimer) clearInterval(ticketScrollTimer);
  const box = $("tickets");
  ticketScrollTimer = setInterval(() => {
    if (!box || box.matches(":hover")) return;
    const next = box.scrollTop + 1;
    box.scrollTop = next >= box.scrollHeight - box.clientHeight - 2 ? 0 : next;
  }, 70);
}

function startStatsMotion() {
  if (statsTimer) clearInterval(statsTimer);
  const open = $("summary-open");
  const statEls = [...document.querySelectorAll(".top-summary strong")];
  statsTimer = setInterval(() => {
    const base = ticketsCache.length || 100;
    open.textContent = String(base + Math.floor(Math.random() * 5));
    statEls.forEach((el, idx) => {
      el.classList.add("stat-tick");
      setTimeout(() => el.classList.remove("stat-tick"), 260);
      if (idx === 1) el.textContent = `${(1.7 + Math.random() * 0.5).toFixed(1)}h`;
      if (idx === 2) el.textContent = `${(20 + Math.random() * 2).toFixed(1)}%`;
      if (idx === 3) el.textContent = String(34 + Math.floor(Math.random() * 8));
    });
  }, 2400);
}

async function selectTicket(ticket, shouldPlan) {
  selectedTicket = ticket;
  $("selected-ticket-title").textContent = ticket.title;
  $("selected-ticket-meta").textContent = `${ticket.ticket_id} · ${ticket.cell_name || ticket.cell_id} · ${ticket.severity} · ${ticket.status}`;
  $("query").value = `分析${ticket.cell_name || ticket.cell_id}小区工单${ticket.ticket_id}：${ticket.description}，请分析根因并生成优化方案`;
  latestPlan = null;
  renderTickets(ticketsCache, true);
  await loadKpi(ticket.cell_id, ticket.cell_name || ticket.cell_id);
  renderCauseBars([]);
  resetLoop();
  if (shouldPlan) previewPlan();
}

async function loadKpi(cellId, label) {
  if (!cellId) return;
  const data = await api(`/api/kpi/${cellId}?hours=24`);
  const kpis = data.kpi || [];
  currentKpis = kpis;
  updateKpiMetrics(kpis, label);
  drawChart(kpis);
}

function updateKpiMetrics(kpis, label) {
  $("kpi-cell").textContent = label || "未选择小区";
  const latest = kpis[kpis.length - 1];
  if (!latest) return;
  $("m-rsrp").textContent = Number(latest.rsrp).toFixed(1);
  $("m-sinr").textContent = Number(latest.sinr).toFixed(1);
  $("m-prb").textContent = `${Number(latest.prb_util).toFixed(1)}%`;
  $("m-drop").textContent = `${Number(latest.drop_rate).toFixed(2)}%`;
}

function drawChart(kpis = []) {
  const canvas = $("kpi-chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#0a111a";
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = "rgba(255,255,255,.08)";
  for (let i = 1; i < 5; i++) {
    const y = (height / 5) * i;
    ctx.beginPath();
    ctx.moveTo(34, y);
    ctx.lineTo(width - 16, y);
    ctx.stroke();
  }
  const series = [
    { key: "prb_util", color: "#f2bd6b", min: 0, max: 100, name: "PRB" },
    { key: "sinr", color: "#5ad7f2", min: -5, max: 30, name: "SINR" },
    { key: "drop_rate", color: "#ee6b82", min: 0, max: 5, name: "掉线" },
  ];
  series.forEach((s) => {
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    kpis.forEach((row, i) => {
      const x = 38 + (i / Math.max(1, kpis.length - 1)) * (width - 58);
      const y = height - 20 - ((Number(row[s.key]) - s.min) / (s.max - s.min)) * (height - 42);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
}

function startKpiMotion(base = []) {
  stopKpiMotion();
  let tick = 0;
  kpiTimer = setInterval(() => {
    if (!base.length) return;
    tick += 1;
    const shifted = base.map((row, i) => ({
      ...row,
      prb_util: Math.max(5, Math.min(99, Number(row.prb_util) + Math.sin((tick + i) / 3) * 3)),
      sinr: Number(row.sinr) + Math.cos((tick + i) / 4) * 0.8,
      drop_rate: Math.max(0.05, Number(row.drop_rate) + Math.sin((tick + i) / 5) * 0.08),
    }));
    updateKpiMetrics(shifted, selectedTicket?.cell_name || selectedTicket?.cell_id);
    drawChart(shifted);
  }, 900);
}

function stopKpiMotion() {
  if (kpiTimer) clearInterval(kpiTimer);
  kpiTimer = null;
}

function renderCauseBars(items = []) {
  const scores = { ...causeScores };
  items.forEach((item) => { scores[item.cause] = item.score || 0; });
  causeScores = scores;
  const box = $("cause-bars");
  box.innerHTML = Object.entries(scores).map(([key, value]) => `
    <div class="cause-row pulse">
      <span>${causeNames[key]}</span>
      <span class="bar"><i style="--w:${Math.round(value * 100)}%"></i></span>
      <b>${Math.round(value * 100)}%</b>
    </div>
  `).join("");
}

function nudgeCause(skill) {
  const boosts = {
    coverage_analysis: "coverage",
    interference_analysis: "interference",
    capacity_analysis: "capacity",
    handover_analysis: "handover",
  };
  const key = boosts[skill];
  if (!key) return;
  causeScores = Object.fromEntries(Object.entries(causeScores).map(([name, value]) => [name, Math.max(0.08, value * 0.92)]));
  causeScores[key] = Math.min(0.88, causeScores[key] + 0.18);
  renderCauseBars([]);
}

function driftCause() {
  causeScores = Object.fromEntries(
    Object.entries(causeScores).map(([key, value], index) => [
      key,
      Math.max(0.08, Math.min(0.82, value + Math.sin(Date.now() / 700 + index) * 0.015)),
    ])
  );
  renderCauseBars([]);
}

function resetLoop() {
  $("loop-status").textContent = "待执行";
  [...$("loop-flow").children].forEach((node) => node.classList.remove("active"));
}

function activateLoopBySkill(skill) {
  const match = loopSkillMap.find(([name]) => name === skill);
  if (!match) return;
  const index = match[1];
  [...$("loop-flow").children].forEach((node, i) => node.classList.toggle("active", i <= index));
  $("loop-status").textContent = $("loop-flow").children[index]?.textContent || "执行中";
}

function renderDag(dag = [], label = "等待规划") {
  dagState = dag;
  $("dag-cell").textContent = label;
  const box = $("skill-dag");
  box.innerHTML = "";
  if (!dag.length) {
    box.innerHTML = `<div class="dag-node"><span class="num">00</span><strong>等待 Planner</strong><small>选择工单或点击规划</small></div>`;
    return;
  }
  dag.forEach((node) => {
    const item = document.createElement("div");
    item.className = `dag-node ${node.status || "queued"}`;
    item.innerHTML = `<span class="num">${String(node.id).padStart(2, "0")}</span><strong>${node.skill}</strong><small>${(node.depends_on || [])[0] || "入口 Skill"}</small>`;
    box.appendChild(item);
  });
}

function setDagState(skill, status) {
  dagState = dagState.map((node) => (node.skill === skill ? { ...node, status } : node));
  renderDag(dagState, $("dag-cell").textContent);
}

function appendLog(type, skill, message, className = "") {
  const box = $("runtime-console");
  const line = document.createElement("div");
  line.className = `log-line ${className}`;
  line.innerHTML = `<span class="log-type">${type}</span><span class="log-skill">${skill || "runtime"}</span><span>${message}</span>`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function resetRuntime() {
  $("runtime-console").innerHTML = "";
  $("runtime-status").textContent = "idle";
  resultLines = ["<span class=\"thinking-line\">智能体正在分析当前工单</span>"];
  renderStreamingAnswer();
  resetLoop();
  causeScores = { coverage: 0.25, interference: 0.18, capacity: 0.2, handover: 0.15 };
  renderCauseBars([]);
}

function renderStreamingAnswer() {
  const box = $("answer");
  box.innerHTML = resultLines.join("\n");
  box.scrollTop = box.scrollHeight;
}

function pushResultLine(text, type = "过程") {
  resultLines.push(`${new Date().toLocaleTimeString("zh-CN", { hour12: false })}  ${type}：${text}`);
  renderStreamingAnswer();
}

async function previewPlan() {
  const query = $("query").value.trim();
  if (!query) return;
  const btn = $("plan-btn");
  btn.disabled = true;
  btn.textContent = "规划中";
  try {
    const data = await api("/api/plan", { method: "POST", body: JSON.stringify({ query }) });
    latestPlan = data;
    const dag = (data.dag || []).map((node) => ({ ...node, status: "queued" }));
    renderDag(dag, data.intent?.cell_name ? `${data.intent.cell_name} · ${dag.length} Skills` : "未识别小区");
    $("runtime-console").innerHTML = "";
    appendLog("规划", "planner", `已生成 ${dag.length} 个 Skill 节点，目标小区 ${data.intent?.cell_name || "未识别"}。`, "done");
  } catch (err) {
    appendLog("错误", "planner", err.message, "failed");
  } finally {
    btn.disabled = false;
    btn.textContent = "规划";
  }
}

async function runQuery() {
  const query = $("query").value.trim();
  if (!query) return;
  const btn = $("run-btn");
  btn.disabled = true;
  btn.classList.remove("triggered");
  void btn.offsetWidth;
  btn.classList.add("triggered");
  btn.textContent = "分析中";
  resetRuntime();
  startKpiMotion(currentKpis);
  if (!latestPlan || latestPlan.intent?.query !== query) await previewPlan();
  try {
    await runStream(query);
  } catch (err) {
    appendLog("错误", "runtime", err.message, "failed");
    $("answer").textContent = `执行失败：${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "执行分析";
    stopKpiMotion();
  }
}

async function runStream(query) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  if (!response.ok || !response.body) throw new Error("流式调度启动失败");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() || "";
    for (const frame of frames) {
      const line = frame.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      const event = JSON.parse(line.slice(6));
      handleRuntimeEvent(event);
      if (event.type === "final") {
        await reader.cancel();
        return;
      }
    }
  }
}

function handleRuntimeEvent(event) {
  if (event.type === "planner_start") {
    $("runtime-status").textContent = "planning";
    appendLog("观察", "planner", event.message, "running");
    pushResultLine(event.message, "规划");
    return;
  }
  if (event.type === "planner_done") {
    $("runtime-status").textContent = "planned";
    $("trace-id").textContent = event.trace_id;
    const dag = (event.dag || []).map((node) => ({ ...node, status: "queued" }));
    renderDag(dag, event.intent?.cell_name ? `${event.intent.cell_name} · ${dag.length} Skills queued` : "未识别小区");
    appendLog("编排", "planner", event.message, "done");
    pushResultLine(event.message, "编排");
    return;
  }
  if (event.type === "reasoning_step") {
    const phase = { observe: "观察", hypothesis: "假设", conclusion: "结论" }[event.phase] || "推理";
    appendLog(phase, event.skill_name, event.message, event.phase === "hypothesis" ? "running" : "done");
    if (event.phase === "hypothesis") driftCause();
    if (event.phase !== "observe") pushResultLine(`${event.skill_name}：${event.message}`, phase);
    return;
  }
  if (event.type === "skill_start") {
    $("runtime-status").textContent = `running ${event.skill_name}`;
    setDagState(event.skill_name, "running");
    activateLoopBySkill(event.skill_name);
    nudgeCause(event.skill_name);
    appendLog("调用", event.skill_name, `${event.description} · memory=[${(event.memory_keys || []).join(", ") || "empty"}]`, "running");
    pushResultLine(`调用 ${event.skill_name}，读取上下文并执行原子能力。`, "调用");
    return;
  }
  if (event.type === "skill_done") {
    $("runtime-status").textContent = `done ${event.skill_name}`;
    setDagState(event.skill_name, event.status === "success" ? "success" : "failed");
    appendLog("返回", event.skill_name, `${event.summary} · ${event.duration_ms}ms · 置信度 ${Math.round((event.confidence || 0) * 100)}%`, "done");
    (event.evidence || []).slice(0, 2).forEach((item) => appendLog("证据", event.skill_name, item, "done"));
    pushResultLine(`${event.skill_name} 返回：${event.summary}`, "返回");
    (event.evidence || []).slice(0, 1).forEach((item) => pushResultLine(item, "证据"));
    return;
  }
  if (event.type === "final") {
    $("runtime-status").textContent = "completed";
    appendLog("完成", "report_writer", event.message, "final");
    renderFinal(event);
  }
}

function renderFinal(result) {
  $("trace-id").textContent = result.trace_id;
  $("cell-label").textContent = result.cell ? `${result.cell.name} · ${result.cell.rat} ${result.cell.band}` : "未识别小区";
  resultLines.push("");
  resultLines.push("===== 最终分析报告 =====");
  resultLines.push(result.answer || "执行完成。");
  renderStreamingAnswer();
  renderDag(result.dag || [], result.cell ? `${result.cell.name} · ${result.dag.length} Skills success` : "未识别小区");
  renderRootCauses(result.root_causes || []);
  renderCauseBars(result.root_causes || []);
  renderRisk(result.risks);
  if (result.charts_data?.kpi?.length) {
    updateKpiMetrics(result.charts_data.kpi, result.cell?.name);
    drawChart(result.charts_data.kpi);
  }
}

function renderRootCauses(items = []) {
  const box = $("root-causes");
  box.innerHTML = "";
  items.slice(0, 3).forEach((item) => {
    const card = document.createElement("div");
    card.className = "root-card";
    card.innerHTML = `<header><strong>${item.title}</strong><span class="badge">${Math.round(item.score * 100)}%</span></header><p>${item.summary}</p><p>${(item.evidence || []).slice(0, 1).join("；")}</p>`;
    box.appendChild(card);
  });
}

function renderRisk(risk) {
  $("risk").textContent = risk ? [
    `风险等级：${risk.risk_level}`,
    `审批要求：${risk.approval_required ? "需要人工审批" : "可模拟下发"}`,
    "",
    "风险点：",
    ...(risk.risk_points || []).map((item) => `- ${item}`),
  ].join("\n") : "暂无风险审阅。";
}

$("plan-btn").onclick = previewPlan;
$("run-btn").onclick = runQuery;
$("query").addEventListener("keydown", (event) => { if (event.key === "Enter") runQuery(); });
$("query").addEventListener("input", () => { latestPlan = null; });

setupPrompts();
renderDag([]);
renderCauseBars([]);
resetLoop();
health();
loadTickets().then(() => previewPlan());
