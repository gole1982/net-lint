// ============================================================
// 华为VRP基线检查工具 — 前端交互
// ============================================================

const API = "";
const TOKEN_KEY = "vrp_api_token";

// ---- API 令牌 ----
function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; }
}

function setToken(t) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch (e) { /* 隐私模式下 localStorage 不可用, 忽略 */ }
}

function saveApiToken() {
  const el = document.getElementById("api-token");
  setToken(el ? el.value.trim() : "");
  alert("API 令牌已保存");
}

function clearApiToken() {
  setToken("");
  const el = document.getElementById("api-token");
  if (el) el.value = "";
  alert("API 令牌已清除");
}

// ---- 通用工具 ----
async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers["X-API-Token"] = token;
  const res = await fetch(API + path, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "API错误");
  }
  return res;
}

async function apiJSON(path, options = {}) {
  const res = await api(path, options);
  return res.json();
}

function sevClass(sev) {
  return { high: "badge-high", medium: "badge-medium", low: "badge-low", suggest: "badge-suggest" }[sev] || "";
}

function esc(s) {
  // 同时转义引号: esc 的输出也会用于 value="..."/onclick="..." 属性,
  // 只替换 &<> 时属性里的引号会提前闭合标签。
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---- Tab 导航 ----
document.querySelectorAll(".tab").forEach(t => {
  t.addEventListener("click", e => {
    e.preventDefault();
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    document.querySelectorAll(".view").forEach(x => x.classList.remove("active"));
    t.classList.add("active");
    document.getElementById("view-" + t.dataset.view).classList.add("active");
    if (t.dataset.view === "devices") loadDevices();
    if (t.dataset.view === "rules") loadRules();
    if (t.dataset.view === "tasks") loadTaskResults();
    if (t.dataset.view === "system") loadSystemInfo();
  });
});

// ---- 设备管理 ----
async function loadDevices() {
  try {
    const devices = await apiJSON("/api/devices");
    const tbody = document.getElementById("device-list");
    if (!devices.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">暂无设备, 请添加或导入CSV</td></tr>';
      return;
    }
    tbody.innerHTML = devices.map(d => `
      <tr>
        <td>${esc(d.name)}</td>
        <td>${esc(d.ip)}</td>
        <td><span class="badge ${d.platform === 's-series' ? 'badge-low' : 'badge-suggest'}">${esc(d.platform)}</span></td>
        <td>${esc(d.username)}</td>
        <td>${d.has_password ? "已设置" : '<span style="color:#dc2626">未设置</span>'}</td>
        <td>
          <button class="btn btn-secondary btn-small" data-action="edit" data-ip="${esc(d.ip)}">编辑</button>
          <button class="btn btn-danger btn-small" data-action="delete" data-ip="${esc(d.ip)}">删除</button>
        </td>
      </tr>
    `).join("");
  } catch (e) {
    alert("加载设备失败: " + e.message);
  }
}

// 事件委派: 不把设备字段拼进 onclick 字符串, 避免属性逃逸
document.getElementById("device-list").addEventListener("click", e => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const ip = btn.dataset.ip;
  if (btn.dataset.action === "edit") editDevice(ip);
  if (btn.dataset.action === "delete") deleteDevice(ip);
});

function showAddDevice() {
  document.getElementById("modal-title").textContent = "添加设备";
  document.getElementById("device-form").reset();
  document.getElementById("orig-ip").value = "";
  document.getElementById("dev-platform").value = "s-series";
  document.getElementById("device-modal").style.display = "flex";
}

function editDevice(ip) {
  apiJSON(`/api/devices/${ip}`).then(d => {
    document.getElementById("modal-title").textContent = "编辑设备";
    document.getElementById("orig-ip").value = ip;
    document.getElementById("dev-name").value = d.name;
    document.getElementById("dev-ip").value = d.ip;
    document.getElementById("dev-platform").value = d.platform;
    document.getElementById("dev-username").value = d.username;
    document.getElementById("dev-password").value = "";
    document.getElementById("device-modal").style.display = "flex";
  });
}

function closeModal() {
  document.getElementById("device-modal").style.display = "none";
}

async function saveDevice(e) {
  e.preventDefault();
  const origIp = document.getElementById("orig-ip").value;
  const data = {
    name: document.getElementById("dev-name").value,
    ip: document.getElementById("dev-ip").value,
    platform: document.getElementById("dev-platform").value,
    type: "交换机",
    protocol: "telnet",
    username: document.getElementById("dev-username").value,
  };
  const pw = document.getElementById("dev-password").value;
  if (pw) data.password = pw;
  try {
    if (origIp) {
      await apiJSON(`/api/devices/${origIp}`, {
        method: "PUT",
        body: JSON.stringify(data),
      });
    } else {
      await apiJSON("/api/devices", {
        method: "POST",
        body: JSON.stringify(data),
      });
    }
    closeModal();
    loadDevices();
  } catch (e) {
    alert("保存失败: " + e.message);
  }
}

async function deleteDevice(ip) {
  if (!confirm(`确认删除设备 ${ip}?`)) return;
  try {
    await api(`/api/devices/${ip}`, { method: "DELETE" });
    loadDevices();
  } catch (e) {
    alert("删除失败: " + e.message);
  }
}

async function importCSV(e) {
  const file = e.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  try {
    const token = getToken();
    const headers = token ? { "X-API-Token": token } : {};
    const res = await fetch("/api/devices/import", { method: "POST", body: formData, headers });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "导入失败" }));
      throw new Error(err.detail || "导入失败");
    }
    const data = await res.json();
    alert(`导入成功: ${data.count} 台设备`);
    loadDevices();
  } catch (e) {
    alert(e.message);
  }
  e.target.value = "";
}

// ---- 规则配置 ----
let _variables = {};

async function loadRules() {
  try {
    const [rules, profile] = await Promise.all([
      apiJSON("/api/rules"),
      apiJSON("/api/profile"),
    ]);
    _variables = profile.variables || {};

    // 渲染规则开关
    const tbody = document.getElementById("rule-list");
    tbody.innerHTML = rules.map(r => {
      const enabled = (profile.rules || {})[r.id] || false;
      return `
        <tr>
          <td><strong>${esc(r.id)}</strong></td>
          <td>${esc(r.title)}</td>
          <td><span class="badge ${sevClass(r.severity)}">${r.severity}</span></td>
          <td>${(r.platforms || []).join(", ")}</td>
          <td>
            <div class="toggle ${enabled ? 'on' : ''}" onclick="toggleRule('${r.id}', ${!enabled})"></div>
          </td>
        </tr>
      `;
    }).join("");

    // 渲染变量编辑器
    const editor = document.getElementById("variables-editor");
    editor.innerHTML = Object.entries(_variables).map(([k, v]) => `
      <div class="form-row">
        <label>${esc(k)}</label>
        <input type="text" id="var-${k}" value="${esc(String(v))}">
      </div>
    `).join("");
  } catch (e) {
    alert("加载规则失败: " + e.message);
  }
}

async function toggleRule(ruleId, enabled) {
  try {
    await apiJSON(`/api/profile/rules/${ruleId}`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    });
    loadRules();
  } catch (e) {
    alert("切换失败: " + e.message);
  }
}

async function saveVariables() {
  const newVars = {};
  for (const [k, v] of Object.entries(_variables)) {
    const el = document.getElementById(`var-${k}`);
    if (el) newVars[k] = el.value;
  }
  try {
    await apiJSON("/api/profile/variables", {
      method: "PUT",
      body: JSON.stringify({ variables: newVars }),
    });
    alert("变量已保存");
  } catch (e) {
    alert("保存失败: " + e.message);
  }
}

// ---- 检查任务 ----
let _sseSource = null;

async function startTask(offline) {
  try {
    const task = await apiJSON("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ offline }),
    });
    document.getElementById("task-progress").style.display = "flex";
    document.getElementById("progress-fill").style.width = "0%";
    document.getElementById("progress-text").textContent = "0%";
    document.getElementById("progress-detail").textContent = "等待开始...";

    // SSE 监听进度 (EventSource 无法自定义请求头, 令牌通过查询参数传递)
    if (_sseSource) _sseSource.close();
    const _tok = getToken();
    const _q = _tok ? `?token=${encodeURIComponent(_tok)}` : "";
    _sseSource = new EventSource(`/api/sse/${task.task_id}${_q}`);
    _sseSource.onmessage = (e) => {
      const data = JSON.parse(e.data);
      const pct = Math.round((data.progress || 0) * 100);
      document.getElementById("progress-fill").style.width = pct + "%";
      document.getElementById("progress-text").textContent = pct + "%";
      document.getElementById("progress-detail").textContent =
        `${data.completed || 0} / ${data.total || 0}`;
      if (data.status === "done" || data.status === "failed") {
        _sseSource.close();
        _sseSource = null;
        if (data.status === "failed") {
          document.getElementById("progress-detail").textContent =
            "任务失败: " + (data.message || "详情见服务端日志");
        }
        loadTaskResults(task.task_id);
      }
    };
    _sseSource.onerror = () => {
      _sseSource.close();
      _sseSource = null;
    };
  } catch (e) {
    alert("启动任务失败: " + e.message);
  }
}

async function downloadReport(taskId, fmt) {
  // 用 fetch + Blob 下载: <a href> 无法携带 X-API-Token 头,
  // 不要把令牌拼进 URL (会进入访问日志/浏览器历史)。
  try {
    const res = await api(`/api/tasks/${taskId}/download/${fmt}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fmt === "md" ? "remediation_report.md" : "baseline_report.xlsx";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert("下载失败: " + e.message);
  }
}

let _currentTaskId = null;

document.getElementById("task-results").addEventListener("click", e => {
  const btn = e.target.closest("button[data-download]");
  if (btn && _currentTaskId) downloadReport(_currentTaskId, btn.dataset.download);
});

async function loadTaskResults(taskId) {
  if (!taskId) {
    document.getElementById("task-results").innerHTML =
      '<div class="empty">点击上方按钮启动检查</div>';
    return;
  }
  _currentTaskId = taskId;
  try {
    const data = await apiJSON(`/api/tasks/${taskId}/results`);
    const results = data.results || {};
    const container = document.getElementById("task-results");

    if (!Object.keys(results).length) {
      container.innerHTML = '<div class="empty">暂无结果</div>';
      return;
    }

    container.innerHTML = Object.values(results).map(r => {
      if (r.status === "error") {
        return `
          <div class="result-card error">
            <h4>${esc(r.ip)}（${esc(r.name)}）<span class="badge badge-high">采集失败</span></h4>
            <div class="result-item" style="color:#dc2626">${esc(r.error)}</div>
          </div>
        `;
      }
      if (r.status === "not_checked") {
        return `
          <div class="result-card">
            <h4>${esc(r.ip)}（${esc(r.name)}）<span class="badge badge-suggest">未执行检查</span></h4>
            <div class="result-item">${esc(r.message || "未执行任何规则, 结果不代表合规")}</div>
          </div>
        `;
      }
      const findings = r.findings || [];
      if (!findings.length) {
        return `
          <div class="result-card pass">
            <h4>${esc(r.ip)}（${esc(r.name)}）<span class="badge badge-low">全部合规</span></h4>
          </div>
        `;
      }
      const items = findings.map(f => `
          <div class="result-item">
            <strong>[${esc(f.rule_id)}]</strong> ${esc(f.title)}
            <span class="badge ${sevClass(f.severity)}">${esc(f.severity)}</span><br>
            位置: ${esc(f.target)} | 证据: ${esc(f.evidence)}
          </div>
        `).join("");
      return `
        <div class="result-card fail">
          <h4>${esc(r.ip)}（${esc(r.name)}）<span class="badge badge-high">${findings.length} 项不合规</span></h4>
          ${items}
        </div>
      `;
    }).join("") + `
      <div style="margin-top:16px;text-align:center">
        <button class="btn btn-secondary" data-download="md">下载MD报告</button>
        <button class="btn btn-secondary" data-download="xlsx">下载Excel报告</button>
      </div>
    `;
  } catch (e) {
    document.getElementById("task-results").innerHTML =
      `<div class="empty">${esc(e.message)}</div>`;
  }
}

// ---- 系统信息 ----
async function loadSystemInfo() {
  const tokEl = document.getElementById("api-token");
  if (tokEl && !tokEl.value) tokEl.value = getToken();
  try {
    const info = await apiJSON("/api/system/info");
    document.getElementById("system-info").innerHTML = `
      <p><strong>Python版本:</strong> ${esc(info.python)}</p>
      <p><strong>操作系统:</strong> ${esc(info.platform)}</p>
      <p><strong>架构:</strong> ${esc(info.machine)}</p>
      <p style="margin-top:12px;color:var(--text-muted)">点击"环境自检"验证依赖完整性</p>
    `;
  } catch (e) {
    document.getElementById("system-info").innerHTML = `<p>加载失败: ${esc(e.message)}</p>`;
  }
}

async function runSelfCheck() {
  try {
    const result = await apiJSON("/api/system/selfcheck");
    const panel = document.getElementById("system-info");
    panel.innerHTML = `
      <p><strong>自检结果:</strong> ${result.ok ? '✓ 通过' : '✗ 失败'}</p>
      <p><strong>Python:</strong> ${esc(result.python_version)}</p>
      <p><strong>telnetlib:</strong> ${result.telnetlib ? '✓ 可用' : '✗ 不可用(仅离线模式可用)'}</p>
      <p><strong>PyYAML:</strong> ${esc(result.yaml_version)}</p>
      <p><strong>openpyxl:</strong> ${esc(result.openpyxl_version)}</p>
      ${result.vendor_note ? `<p style="color:#dc2626"><strong>离线包提示:</strong> ${esc(result.vendor_note)}</p>` : ""}
    `;
  } catch (e) {
    alert("自检失败: " + e.message);
  }
}

// ---- 初始化 ----
loadDevices();
