// ============================================================
// 华为VRP基线检查工具 — 前端交互
// ============================================================

const API = "";

// ---- 通用工具 ----
async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
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
  const d = document.createElement("div");
  d.textContent = s || "";
  return d.innerHTML;
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
          <button class="btn btn-secondary btn-small" onclick="editDevice('${d.ip}')">编辑</button>
          <button class="btn btn-danger btn-small" onclick="deleteDevice('${d.ip}')">删除</button>
        </td>
      </tr>
    `).join("");
  } catch (e) {
    alert("加载设备失败: " + e.message);
  }
}

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
    const res = await fetch("/api/devices/import", { method: "POST", body: formData });
    if (!res.ok) throw new Error("导入失败");
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

    // SSE 监听进度
    if (_sseSource) _sseSource.close();
    _sseSource = new EventSource(`/api/sse/${task.task_id}`);
    _sseSource.onmessage = (e) => {
      const data = JSON.parse(e.data);
      const pct = Math.round((data.progress || 0) * 100);
      document.getElementById("progress-fill").style.width = pct + "%";
      document.getElementById("progress-text").textContent = pct + "%";
      document.getElementById("progress-detail").textContent =
        `${data.completed || 0} / ${data.total || 0}`;
      if (data.status === "done") {
        _sseSource.close();
        _sseSource = null;
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

async function loadTaskResults(taskId) {
  if (!taskId) {
    document.getElementById("task-results").innerHTML =
      '<div class="empty">点击上方按钮启动检查</div>';
    return;
  }
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
      const findings = r.findings || [];
      if (!findings.length) {
        return `
          <div class="result-card pass">
            <h4>${esc(r.ip)}（${esc(r.name)}）<span class="badge badge-low">全部合规</span></h4>
          </div>
        `;
      }
      const items = findings.map(([rule, findingList]) => {
        return (findingList || []).map(f => `
          <div class="result-item">
            <strong>[${esc(rule.rid)}]</strong> ${esc(rule.title)}
            <span class="badge ${sevClass(rule.severity)}">${rule.severity}</span><br>
            位置: ${esc(f.target)} | 证据: ${esc(f.evidence)}
          </div>
        `).join("");
      }).join("");
      return `
        <div class="result-card fail">
          <h4>${esc(r.ip)}（${esc(r.name)}）<span class="badge badge-high">${findings.length} 项不合规</span></h4>
          ${items}
        </div>
      `;
    }).join("") + `
      <div style="margin-top:16px;text-align:center">
        <a href="/api/tasks/${taskId}/download/md" class="btn btn-secondary">下载MD报告</a>
        <a href="/api/tasks/${taskId}/download/xlsx" class="btn btn-secondary">下载Excel报告</a>
      </div>
    `;
  } catch (e) {
    document.getElementById("task-results").innerHTML =
      `<div class="empty">${esc(e.message)}</div>`;
  }
}

// ---- 系统信息 ----
async function loadSystemInfo() {
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
    `;
  } catch (e) {
    alert("自检失败: " + e.message);
  }
}

// ---- 初始化 ----
loadDevices();
