# VRP 基线检查工具 v2.2 — 代码审计报告

| 项目 | 内容 |
|---|---|
| 审计对象 | 华为 VRP 基线检查工具 v2.2 |
| 审计基线 | commit `514c4ef`（`ci: 修正卫生检查自引用误报, 增强异常与容器诊断`） |
| 审计范围 | `core/` `api/` `web/` `cli.py` `vrp_baseline.py` `tests/` `.github/` + 部署配置（排除 `vendor/` `runtime/` 第三方代码） |
| 一方代码规模 | 20 个 Python 模块（~2 900 行）+ 3 个前端文件 + 1 个 CI 工作流 |
| 审计日期 | 2026-10-22 |
| 审计方法 | 静态审读 + 在隔离副本中构造真实 VRP 配置实跑复现 + HTTP 层行为验证 + 与 CPython 标准库语义对照 |
| 审计环境 | Python 3.13.15 / PyYAML 6.0.3 / FastAPI 0.143.2 / Pydantic 2.14.1 / openpyxl 3.1.5（与 `requirements.txt` 锁定版本一致） |
| 仓库改动 | **未修改任何代码**；本次仅新增本报告文件 `docs/AUDIT-2026-10-22.md` |
| 结论 | 发现 **28 项问题**（严重 2 / 高危 6 / 中危 10 / 低危 10）。**v2.2 的 Web 端从首次提交至今从未成功运行过**，CLI 链路可用 |

---

## 一、结论摘要

### 1.1 一句话结论

CLI（`./run.sh`）链路经实测可正常跑完离线检查并产出报告；**Web 端（v2.2 的主卖点）存在两个串联的阻断性缺陷——模块导入即崩溃、任务调度即 KeyError——导致 Web 服务根本无法启动、即使强行绕过也无法完成任何一次检查任务**。上一轮审计（`AUDIT-2026-10-21`）声称"18 项缺陷已全部修复并回归验证"，但其中 **A06 / A10 / A11 / A12 四条 API 层用例从未真正执行过**（它们依赖的 `api.store` 模块在导入阶段即抛 `TypeError`），因此这四条"已修复"结论缺乏实际验证支撑。

### 1.2 问题清单

| 编号 | 严重度 | 模块 | 问题 | 状态 |
|---|---|---|---|---|
| C-01 | 🔴 严重 | `api/store.py` | 类体内 `list` 方法遮蔽内建 `list`，`list[dict]` 注解在类体求值时解析到方法对象 → **模块导入即 `TypeError`，整个 FastAPI 应用无法启动** | 未修 |
| C-02 | 🔴 严重 | `api/task_manager.py` | `asyncio.as_completed()` 产出的是协程而非原始 Future，`futures[fut]` 必然 `KeyError` → **每个 Web 检查任务立即崩溃，任务永远停在 `running`，无报告产出，SSE 永不结束** | 未修 |
| H-01 | 🟠 高危 | `api/routes/devices.py` | `DeviceOut(**dev.model_dump(), password=...)` 重复关键字参数 → **`POST /api/devices` 恒返回 500**（设备却已写入内存库） | 未修 |
| H-02 | 🟠 高危 | `api/routes/tasks.py` | 任务结果里存的是 `Rule`/`Finding` 实例，`Check` 用 `__slots__` 无法被 `jsonable_encoder` 序列化 → **`GET /api/tasks/{id}/results` 恒返回 500**，前端结果页永远空白 | 未修 |
| H-03 | 🟠 高危 | `web/app.js` | `esc()` 不转义引号，且设备 IP 未转义即插入 `onclick` JS 字符串 → **存储型 XSS**（可窃取存于 `localStorage` 的 API 令牌） | 未修 |
| H-04 | 🟠 高危 | `api/routes/profile.py` | `PUT /api/profile` 采用 prune（整段替换）语义，提交一个变量会**删除其余全部变量** → 之后所有检查任务 500 | 未修 |
| H-05 | 🟠 高危 | `core/collector.py` `core/reporter.py` | 设备 `ip` 字段无任何校验，直接参与路径拼接 → **任意文件读 + 任意文件写**（文件名须以 `.cfg` 结尾） | 未修 |
| H-06 | 🟠 高危 | `.github/workflows/ci.yml` | CI 三个 job 同时失败：回归测试导入崩溃、`docs/AUDIT-2026-10-21.md` 已被删除、容器内 Web 进程起不来 | 未修 |
| M-01 | 🟡 中危 | `core/builtin_checks.py` | `vlan1_check` 对"已显式 `port default vlan 10` 但无 `port link-type` 行"的 access 口误报"默认携带 VLAN1" | 未修 |
| M-02 | 🟡 中危 | `api/app.py` | API 令牌可用 `?token=` 查询参数传递且对**全部** `/api` 路径生效 → 落入访问日志/浏览器历史 | 未修 |
| M-03 | 🟡 中危 | `api/app.py` | 令牌守卫只覆盖 `/api` 前缀，`/docs` `/redoc` `/openapi.json` 始终免鉴权 | 未修 |
| M-04 | 🟡 中危 | `core/reporter.py` | 全合规设备被整条从报告中剔除 → 审计方无法区分"已检且通过"与"从未检查" | 未修 |
| M-05 | 🟡 中危 | `api/routes/devices.py` | CSV 导入无 schema 校验：非法 `platform` 会让 `GET /api/devices` 永久 500；坏 CSV 还会覆盖全部设备清单 | 未修 |
| M-06 | 🟡 中危 | `api/` | 设备增删改/导入仅存内存，进程重启即丢失（而 `devices.csv` 每次启动都被重新读入） | 未修 |
| M-07 | 🟡 中危 | `api/routes/profile.py` | `rules.yaml` 中新增但未登记进 `profile.yaml` 的规则无法在 Web 端启用（切换返回 404） | 未修 |
| M-08 | 🟡 中危 | `api/app.py` | SSE 无 `Cache-Control`/`X-Accel-Buffering`/心跳，且任务卡死时连接永不释放 | 未修 |
| M-09 | 🟡 中危 | `api/routes/profile.py` | `profile.yaml` 读-改-写无锁 → 并发请求丢失更新 | 未修 |
| M-10 | 🟡 中危 | `.github/workflows/ci.yml` `tests/` `profile.yaml` | 凭据卫生正则 `Huawei@12[0-9]` 与本项目真实口令形态 `Ro@Huawei123` 不匹配 → **检查恒通过，仓库内明文 SNMP 团体字不被发现** | 未修 |
| L-01 | 🔵 低危 | 9 处 | 健壮性/可维护性/加固项（详见第五节） | 未修 |

> 严重度口径：**严重** = 主功能完全不可用；**高危** = 主功能受损或存在可被利用的安全缺陷；**中危** = 存在错误结论、可用性或数据完整性风险；**低危** = 健壮性、可维护性、加固建议。

### 1.3 上一轮审计的复核结论

| 编号 | 上轮结论 | 本次复核 |
|---|---|---|
| A02/A03/A04/A05/A14/A16/A17 | 已修复 | ✅ 复核通过，逻辑与回归用例均正确 |
| A01 | 已修复 | ✅ 复核通过 |
| **A06 / A10 / A11 / A12** | **已修复并回归验证** | ❌ **用例从未执行**：`tests/test_regression.py:289` 的 `from api.app import app` 因 C-01 直接 `TypeError`，`run_api_tests()` 在第一条用例前即中断 |
| A07/A08/A09/A13/A15/A18 | 已修复 | ✅ 基本通过（A09 的检测正则本身失效，见 M-10；A15 依赖 `output/baseline_report.xlsx` 存在时才跑） |

---

## 二、严重问题详述

### 🔴 C-01　`api/store.py` 类体内 `list` 遮蔽内建，模块导入即崩溃

**位置**　`api/store.py:21`（遮蔽源）、`api/store.py:58`、`api/store.py:63`（受害点）

```python
class DeviceStore:
    def list(self) -> list[dict]:          # ← 第 21 行：类命名空间内绑定名 "list"
        ...
    def load_from_csv(self, devices: list[dict]):   # ← 第 58 行：类体求值时 list 已是方法
        ...
```

**现象**　Python 类体是一个独立命名空间，且在类体执行期间按顺序求值。第 21 行的 `def list` 执行完毕后，类命名空间里就有了 `list` 这个名字，它**遮蔽内建 `list`**。第 21 行自身的注解 `-> list[dict]` 之所以不报错，是因为注解在 `def` 语句绑定名字**之前**求值（此时 `list` 仍是内建）。而第 58 行执行时，`list` 已解析到类内的方法函数，于是 `list[dict]` 变成"对函数对象取下标"：

```
TypeError: 'function' object is not subscriptable
```

**影响**

* `import api.store` 必然失败 → `api/app.py` → `api/routes/devices.py` → `api.store` 导入链断裂 → **uvicorn 启动即退出，Web 服务完全不可用**。
* `api/routes/tasks.py` / `api/routes/profile.py` 虽未被 `api.app` 直接触发，但它们在运行期导入 `..store`，同样不可用。
* 上一轮审计的 4 条 API 层回归用例（A06/A10/A11/A12）全部依赖该模块，**因此从未真正跑过**。
* CI 从引入工作流（`3a6bb02`）起即为红灯：`test` job 的 `python tests/test_regression.py` 退出码 1；`docker` job 的 Web 容器健康探测 60 秒超时。

**复现**（在仓库根目录，只读）

```console
$ python -c "import api.store"
  File "api/store.py", line 58, in DeviceStore
    def load_from_csv(self, devices: list[dict]):
TypeError: 'function' object is not subscriptable
```

**修复建议**（三选一，推荐第 1 项）

```python
# 1) 改名，彻底消除遮蔽
def list_devices(self) -> list[dict]: ...
# 调用方同步改为 device_store.list_devices()

# 2) 使用 typing.List
from typing import List
def load_from_csv(self, devices: List[dict]): ...

# 3) 字符串注解（配合 from __future__ import annotations）
from __future__ import annotations
```

> 附带风险提示：`TaskStore.list_all`（第 117 行）目前在 `TaskStore` 类内不受影响，但若日后有人在同一类里再定义一个 `list`/`dict`/`tuple`/`set` 成员，相同故障会重演。建议在项目规范中禁止以内建类型名作为类成员名。

---

### 🔴 C-02　`api/task_manager.py` 任务调度键错配，Web 端检查任务 100% 崩溃

**位置**　`api/task_manager.py:134-135`

```python
futures = {
    loop.run_in_executor(pool, _run_single_device, dev, ...) : dev
    for dev in devices
}

for fut in asyncio.as_completed(futures):
    dev = futures[fut]          # ← KeyError
```

**根因**　`asyncio.as_completed()` **产出的不是原始 Future，而是包装后的协程**。CPython 3.12 `Lib/asyncio/tasks.py` 的实现末尾是：

```python
    for _ in range(len(todo)):
        yield _wait_for_one()
```

其 docstring 亦明确写道："Note: The futures 'f' are not necessarily members of fs."。而 `futures` 这个字典是以 `loop.run_in_executor()` 返回的 Future 为键的，协程对象不在字典中，于是 `futures[fut]` 必然抛 `KeyError`。

> 该语义在 Python 3.12 与 3.13 上**一致**（3.13 的 `_AsCompletedIterator._wait_for_one` 同样是协程）。因此本缺陷影响随包 3.12 运行时、Docker 镜像（python:3.12-slim）与系统 Python 3.13 的**全部**部署形态。

**影响**（实测）

```
ERROR:asyncio:Task exception was never retrieved
future: <Task finished coro=<_run_task() ...> exception=KeyError(<coroutine object _AsCompletedIterator._wait_for_one ...>)>
  File "api/task_manager.py", line 135, in _run_task
    dev = futures[fut]
KeyError: <coroutine object _AsCompletedIterator._wait_for_one at 0x...>
RuntimeWarning: coroutine '_AsCompletedIterator._wait_for_one' was never awaited
```

连带后果：

| 现象 | 实测结果 |
|---|---|
| 任务状态 | 创建后 6 秒内始终为 `TaskStatus.running`，`progress=0.0`，`completed=0/N` |
| 报告文件 | `output/reports/<task_id>/` **从不生成** |
| 报告下载 | `GET /api/tasks/{id}/download/md` 恒返回 400「任务尚未完成」 |
| SSE | `event_stream()` 的 `while True` 永不退出（见 M-08），浏览器 EventSource 常驻 |
| 服务端日志 | 只有 `Task exception was never retrieved`，前端用户看不到任何错误提示 |

**修复建议**

```python
async def _run_one(dev):
    return dev, await loop.run_in_executor(
        pool, _run_single_device, dev, rules, variables, offline, snapshots_dir)

for fut in asyncio.as_completed([_run_one(d) for d in devices]):
    dev, (ret_ip, ret_dev, cfg, findings) = await fut
    ...
```

同时应给 `_run_task` 整体加 `try/except`，保证任何异常都能把任务置为 `failed` 而不是永远 `running`。

---

## 三、高危问题详述

### 🟠 H-01　`POST /api/devices` 恒返回 500

**位置**　`api/routes/devices.py:47-51`

```python
return DeviceOut(
    **dev.model_dump(),          # ← 已包含 password 键
    password="***" if dev.password else "",   # ← 再次传 password
)
```

`DeviceCreate.model_dump()` 的输出为
`{'type':..., 'name':..., 'ip':..., 'platform':..., 'protocol':..., 'username':..., 'password': None}`，
与显式的 `password=` 构成重复关键字参数：

```
TypeError: api.schemas.DeviceOut() got multiple values for keyword argument 'password'
```

**实测**

```
POST   /api/devices              -> 500  Internal Server Error
```

**加重情节**　`device_store.add(dev.model_dump())` 在第 44 行**已经执行完毕**，设备（连同明文口令）已写入内存库，但前端收到 500 后弹出「保存失败」。用户会以为没保存而反复提交，第二次会收到 409「设备IP已存在」，行为完全不可预期。

`PUT /api/devices/{ip}`（第 81 行）因为逐字段构造 `DeviceOut` 而未受影响——这正是两处写法不一致导致的漏网。

**修复建议**

```python
data = dev.model_dump()
data["password"] = "***" if data.get("password") else ""
data["has_password"] = bool(dev.password)
return DeviceOut(**data)
```

---

### 🟠 H-02　`GET /api/tasks/{id}/results` 恒返回 500

**位置**　`api/routes/tasks.py:52-63`，写入点 `api/task_manager.py:141-147`、`150-156`

`task_store.set_result()` 存入的是引擎内部的原始对象 `[(Rule, [Finding])]`。FastAPI 用 `jsonable_encoder` 序列化时：

* `Rule` / `Section` 有 `__dict__` → `vars()` 可用；
* `Check` 与 `Finding` 声明了 `__slots__` → `vars()` 抛 `TypeError: vars() argument must have __dict__ attribute`。

最终 `jsonable_encoder` 抛出：
```
ValueError: [TypeError("'Check' object is not iterable"), TypeError('vars() argument must have __dict__ attribute')]
```

**实测**

```
GET    /api/tasks/p2/results     -> 500  Internal Server Error
```

**前后端契约同时失配**　即使把序列化问题修好，接口形状仍然对不上：

* 后端产出：`findings: [[<Rule 对象>, [<Finding 对象>]], ...]`
* 前端 `web/app.js:333-341` 期望：`findings: [[{rid, title, severity}, [{target, evidence}]], ...]`

前端直接访问 `rule.rid` / `f.target`（第 336、338 行），而 `Rule`/`Finding` 上并没有这些**小写**属性名——即便序列化成功也会渲染出 `undefined`。

**修复建议**　在 API 边界统一转成普通 dict，复用已有的 `to_dict()`：

```python
# core/models.py 已有 Rule.to_dict() / Finding.to_dict()
task_store.set_result(task_id, ret_ip, {
    "ip": ret_ip, "name": ret_dev.get("name", ""),
    "status": "ok", "error": "",
    "findings": [
        {"rule_id": r.rid, "title": r.title, "severity": r.severity,
         "findings": [f.to_dict() for f in fs]}
        for r, fs in findings
    ],
})
```

并在 `api/schemas.py` 定义对应的响应模型（如 `TaskResultOut`），让前端按同一份 schema 渲染。

---

### 🟠 H-03　`web/app.js` 存储型 XSS

**位置**　`web/app.js:55-59`（`esc`）、`93-94`（设备 IP）、`211`（规则 ID）、`221-222`（变量名/变量值）

```javascript
function esc(s) {                       // 第 55 行
  const d = document.createElement("div");
  d.textContent = s || "";
  return d.innerHTML;                    // ← 只转义 & < >，不转义 " 和 '
}
```

按 HTML 规范，`innerHTML` 序列化文本节点时只转义 `&`、`<`、`>`（及 nbsp），**双引号与单引号原样透出**。而 `esc()` 的返回值被直接放进双引号属性：

```javascript
// 第 222 行
<input type="text" id="var-${k}" value="${esc(String(v))}">
```

实测（与浏览器序列化行为一致）：

```
原始值   : x" onfocus="alert(1)" autofocus="
esc()    : x" onfocus="alert(1)" autofocus="     ← 引号未被转义
最终 HTML: <input type="text" id="var-v" value="x" onfocus="alert(1)" autofocus="">
```

设备 IP 更是**完全没有转义**就插进了 JS 字符串字面量：

```javascript
// 第 93-94 行
<button onclick="editDevice('${d.ip}')">编辑</button>
<button onclick="deleteDevice('${d.ip}')">删除</button>
```

IP 取值 `10.0.0.1'-alert(document.cookie)-'` 即可闭合 JS 字符串并执行任意代码：

```
最终 HTML: <button onclick="editDevice('10.0.0.1'-alert(document.cookie)-'')">编辑</button>
```

**可达性**

| 注入点 | 路径 | 前置条件 |
|---|---|---|
| 变量值 | `PUT /api/profile/variables` | 默认**未启用**令牌鉴权（`VRP_API_TOKEN` 可选），同网段任意人可写入；随后任意管理员打开「规则配置」页即触发 |
| 变量名 | 同上 | 同上 |
| 设备 IP | `POST /api/devices` 或 `POST /api/devices/import` | 同上；也可通过投毒的 `devices.csv` 植入 |
| 规则 ID | `rules.yaml` | 需要能改规则库（信任边界内），风险较低 |

**放大因素**　API 令牌保存在 `localStorage`（`web/app.js:6-30`），XSS 可直接读取令牌；即使未启用令牌，XSS 也能以当前用户身份调用 `/api/tasks` 驱动服务端去 Telnet 登录内网交换机（SSRF 内网跳板）。

**修复建议**

1. 引入真正的属性/JS 转义函数，例如：

```javascript
function escAttr(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escJs(s) {
  return JSON.stringify(String(s ?? "")).slice(1, -1);
}
```

2. 设备行改为**事件委托 + data 属性**，彻底去掉字符串插值：

```html
<button class="btn btn-secondary btn-small" data-action="edit" data-ip="${escAttr(d.ip)}">编辑</button>
```

```javascript
tbody.addEventListener("click", e => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const ip = btn.dataset.ip;
  if (btn.dataset.action === "edit") editDevice(ip); else deleteDevice(ip);
});
```

3. 全局补上响应头（见 L-14）：`Content-Security-Policy: default-src 'self'; script-src 'self'` 可直接阻断内联事件处理器，作为第二道防线。

---

### 🟠 H-04　`PUT /api/profile` 的 prune 语义会删除用户其余变量

**位置**　`api/routes/profile.py:195-207`（`prune=True`）、`api/routes/profile.py:83-148`（`_update_section`）

```python
@router.put("/api/profile")
async def update_profile(update: ProfileUpdate):
    ...
    if update.variables is not None:
        profile["variables"] = update.variables
        sections.append(("variables", update.variables, True))   # ← prune=True
```

**实测**（提交 `{"variables": {"ntpServer1": "9.9.9.9"}}`）：

```
variables after PUT /api/profile: {'ntpServer1': '9.9.9.9'}     ← snmpRo/snmpRw/trapHost/snmpAcl/ntpServer2 全部消失
rules      after PUT /api/profile: 未受影响
validate_vars -> KeyError: 规则 [SW-NTP-01] 引用变量 ${ntpServer2} 未在profile.yaml中定义
POST /api/tasks after prune -> 500 {"detail":"'规则 [SW-NTP-01] 引用变量 ${ntpServer2} 未在profile.yaml中定义'"}
```

也就是说，一次看似无害的调用会**静默删掉用户的其余变量**，并让工具从此无法完成任何一次检查。

**加重情节**

* 该端点在 `api/app.py` 的路由清单与 OpenAPI 中均公开。
* 在 Docker 部署下 `profile.yaml` 是**挂载卷**（`docker-compose.yml` 的 `./data/profile.yaml:/app/profile.yaml`），损坏状态会跨重启持久存在。
* `PUT /api/profile/variables`（第 215-220 行）用的是 `prune=False`（合并语义），两个同名语义的端点行为不一致，调用方无从预期。

**修复建议**

* 把 `PUT /api/profile` 改为**合并语义**（`prune=False`），保留"整段替换"能力请另设显式端点（如 `PUT /api/profile/variables?replace=true`）并在文档中标注。
* 在 `_save_profile` 的回写校验中追加一条不变量：`prune=True` 时若删除的键被任一已启用规则引用，则拒绝写入并返回 409，避免把工具改到不可用状态。

---

### 🟠 H-05　设备 `ip` 字段路径穿越：任意文件读 + 任意文件写

**位置**　读：`core/collector.py:180`；写：`core/reporter.py:289`；入口：`api/schemas.py:23`（`ip: str`，无格式校验）、`api/routes/devices.py:99-111`（CSV 导入无校验）、`core/credentials.py:34-43`（CSV 加载无校验）

```python
# core/collector.py:180
snap_path = os.path.join(snapshots_dir, f'{dev["ip"]}.cfg')

# core/reporter.py:289
snap_path = os.path.join(snap_dir, f"{ip}.cfg")
```

`ip` 字段从 CSV / API 全程无任何格式校验，直接参与路径拼接。

**实测（任意文件读）**

```
fetch_offline({"ip": "../SECRET"}, snapshots_dir=".../snapshots")
  -> 'CONFIDENTIAL-SWITCH-CONFIG\n'
```

读取路径为 `snapshots/../SECRET.cfg`，即 `snapshots/` **上级目录**中的同名 `.cfg` 文件。在 Web 端离线模式中，该文件内容会被写进生成的整改报告并可经 `/api/tasks/{id}/download/md` 下载——构成一条从"写设备 IP"到"读服务器文件"的完整链路。

**实测（任意文件写）**

```
save_snapshots({"../../ESCAPED": "pwned-by-device-ip"}, [{"ip": "../../ESCAPED"}], ROOT)
  -> wrote <ROOT>/../../ESCAPED.cfg   exists = True
```

写入路径为 `snapshots/../../ESCAPED.cfg`，可越过 `snapshots/` 两级目录写入任意 `.cfg` 文件。

**修复建议**

* 在 `api/schemas.py` 与 `core/credentials.py` 统一校验 `ip`：

```python
import ipaddress
def validate_ip(value: str) -> str:
    try:
        ipaddress.ip_address(value.strip())
    except ValueError:
        raise ValueError(f"非法 IP 地址: {value!r}")
    return value.strip()
```

* 在 `core/collector.py` / `core/reporter.py` 再加一道防线（纵深防御）：

```python
snap_path = os.path.abspath(os.path.join(snapshots_dir, f"{ip}.cfg"))
if not snap_path.startswith(os.path.abspath(snapshots_dir) + os.sep):
    raise ValueError("非法快照路径")
```

---

### 🟠 H-06　CI 三个 job 同时失败（流水线已长期红灯）

| Job | 失败点 | 原因 |
|---|---|---|
| `test` | `python tests/test_regression.py` 退出码 1 | C-01 导致 `import api.app` 抛 `TypeError`；`main()` 捕获后返回 1 |
| `hygiene` | 第 99 行 `test -f docs/AUDIT-2026-10-21.md` | 该文件在当前工作区**已被删除**（`git status` 显示 ` D docs/AUDIT-2026-10-21.md`），`docs/` 目录现为空 |
| `docker` | Web 容器 60 秒就绪探测失败 | C-01 使 uvicorn 启动即退出；`docker compose logs` 会打印 `TypeError: 'function' object is not subscriptable` |

**这意味着 CI 从引入工作流的第一个提交（`3a6bb02`）起就是失败的**，没有任何一次提交通过流水线——因此 H-01/C-02 这类"运行时才会暴露"的缺陷从未被拦截。

**修复建议**

* 先修 C-01 / C-02，再把本报告落到 `docs/AUDIT-2026-10-22.md`，同时把 `ci.yml:99` 的断言改为指向新文件（或改为 `test -d docs && ls docs/AUDIT-*.md`），避免下次删除报告再次打断流水线。
* 建议在 `test` job 中增加一条**仅导入**的冒烟检查，把"导入失败"这类问题提前到毫秒级暴露：

```yaml
- name: 导入冒烟
  run: python -c "import api.app; print('api import ok')"
```

---

## 四、中危问题详述

### 🟡 M-01　`vlan1_check` 对 access 口误报"默认携带 VLAN1"

**位置**　`core/builtin_checks.py:199-205`

```python
elif re.search(r"port (default vlan|hybrid pvid vlan) 1\s*$", b, re.MULTILINE):
    evid.append(f"{name}: 划入VLAN1或pvid=1")
elif "port link-type" not in b and "port hybrid pvid vlan" not in b:
    evid.append(f"{name}: 默认携带VLAN1(pvid=1)")
```

判定顺序把"已显式划入 VLAN N（N≠1）但未写 `port link-type`"的接口，落进了"未配置 link-type 即默认 PVID=1"的兜底分支。

**实测**

```
输入:
 interface GigabitEthernet0/0/7
  description PC-ROOM-1
  port default vlan 10          ← PVID 明确为 10, 并未承载 VLAN1
#
输出:
 status  = FAIL
 detail  = GigabitEthernet0/0/7: 默认携带VLAN1(pvid=1); GigabitEthernet0/0/9: 划入VLAN1或pvid=1
```

`SW-VLAN1-01` 的严重度是 `high`，因此这条误报会生成一条错误的 high 级整改命令：

```
interface GigabitEthernet0/0/7
 port trunk allow-pass vlan 2 to 4094      ← 把一个 access 口改成 trunk 放行
```

这既会污染审计结论，也可能误导运维在生产设备上执行错误整改。

**修复建议**　在兜底分支之前先处理"已显式设置 PVID 但不等于 1"的情况：

```python
elif re.search(r"port (default vlan|hybrid pvid vlan)\s+(?!1\b)\d+", b, re.MULTILINE):
    pass   # 已显式划入非 VLAN1, 合规
```

并补一条回归用例覆盖"access 口 + `port default vlan 10` + 无 `port link-type`"。

---

### 🟡 M-02　API 令牌可用查询参数传递，且对全部 `/api` 路径生效

**位置**　`api/app.py:91-97`

```python
if _API_TOKEN and request.url.path.startswith("/api"):
    token = request.headers.get("X-API-Token", "")
    if not token:
        # EventSource 无法自定义请求头, SSE 允许用查询参数传递
        token = request.query_params.get("token", "")
```

注释说明查询参数是为 SSE（`EventSource` 无法自定义请求头）准备的，但实现没有做路径限制。实测：

```
GET /api/devices?token=secret123  -> 200      ← 非 SSE 端点同样接受
GET /api/devices?token=wrong       -> 401
```

后果：令牌会进入 uvicorn 访问日志、反向代理日志、浏览器历史、`Referer` 头、监控系统的 URL 采样。

**修复建议**　把查询参数通道限定到 SSE 端点：

```python
if _API_TOKEN:
    path = request.url.path
    is_sse = path.startswith("/api/sse/")
    token = request.headers.get("X-API-Token", "")
    if not token and is_sse:
        token = request.query_params.get("token", "")
```

---

### 🟡 M-03　`/docs`、`/redoc`、`/openapi.json` 不受令牌保护

**位置**　`api/app.py:91`（守卫只判断 `startswith("/api")`）

实测（已设置 `VRP_API_TOKEN`）：

```
GET /api/devices     -> 401
GET /docs            -> 200      ← 免鉴权
GET /openapi.json    -> 200      ← 免鉴权
GET /redoc           -> 200      ← 免鉴权
```

`/docs` 提供可交互的 "Try it out"，配合令牌即可完整操作 API；即使没有令牌，OpenAPI schema 也完整暴露了全部端点、参数与数据结构，降低了攻击者的摸索成本。

**修复建议**　把守卫扩展到文档端点（若不需要对外公开 Swagger，建议生产部署时直接关闭）：

```python
_PROTECTED = ("/api", "/docs", "/redoc", "/openapi.json")
if _API_TOKEN and request.url.path.startswith(_PROTECTED):
```

或通过 `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` 在生产环境关闭。

---

### 🟡 M-04　全合规设备被整条从报告中剔除

**位置**　`core/reporter.py:77-78`

```python
if not bad and not sug and not err_items:
    continue
```

**实测**

```
summary       : {'total_fail': 0, 'total_suggest': 0, 'total_devices': 2}
devices listed: ['10.0.0.2']        ← 10.0.0.1(全合规) 不在列表中
MD mentions 10.0.0.1 : False
MD mentions 10.0.0.2 : True
```

`summary.total_devices` 统计的是全部设备，而 `devices[]` 只含有问题的设备——两者口径不一致。整改文档中完全没有合规设备的段落，审计方无法证明"该设备已检查且通过"。

**修复建议**　保留全合规设备条目，标注为"全部通过"：

```python
dev_report["status"] = "fail" if (bad or err_items) else "pass"
```

并在 Markdown 中输出 `## <ip>（<name>）—— 全部通过（N 项规则）`。

---

### 🟡 M-05　CSV 导入无校验，可致设备接口永久 500 并清空设备清单

**位置**　`api/routes/devices.py:99-111`

**实测 1（枚举值击穿）**　导入含非法 `platform` 的 CSV：

```
POST /api/devices/import -> 200 {"ok":true,"count":2}
GET  /api/devices        -> 500 Internal Server Error
```

`api/schemas.py:13-15` 把 `platform` 定义为 `Platform` 枚举，而 `list_devices()`（第 27 行）直接 `DeviceOut(platform=d.get("platform", ""))`；一旦内存库中混入非法值，**整个设备列表接口持续 500**，直到进程重启。

**实测 2（无表头/畸形 CSV 被接受并覆盖全量）**

```
POST /api/devices/import (无表头 "no,header\nhere\n") -> 200 {"ok":true,"count":1}
```

`import_devices` 用 `device_store.load_from_csv(devices)`（第 110 行）**整体替换**设备清单。一次误传的文件就会清空此前通过 Web 端维护的全部设备。

**修复建议**

* 导入时用 `DeviceCreate` 模型逐行校验，非法行返回 422 并指出行号，**不要**污染全局存储；
* `platform` 用 `Platform` 枚举校验，未知值拒绝或降级为 `s-series` 并告警；
* 导入前先做全量校验，通过后再原子替换；空文件/无表头文件直接拒绝；
* 对上传体积设上限（`await file.read()` 目前无限制，属内存耗尽向量）。

---

### 🟡 M-06　设备增删改仅存内存，重启即丢失

**位置**　`api/store.py:15-76`（内存存储）、`api/app.py:144-156`（启动时重读 `devices.csv`）

Web 端新增/编辑/删除/CSV 导入的设备只写入内存；进程重启时 `startup_event` 会用 `devices.csv` **整体覆盖**内存库，用户在 Web 界面里做过的全部设备调整被静默丢弃。`docker-compose.yml` 中 `devices.csv` 还是 `:ro` 挂载，Web 端也无法落盘。

**修复建议**　二选一并在 README 明确：

* 设备 CRUD 落盘（写 `devices.csv` 或独立的 `devices.json`，注意 `devices.csv` 当前是 `:ro`）；
* 或在 UI 上明确提示"设备清单为临时状态，重启后以 devices.csv 为准"，并提供导出按钮。

---

### 🟡 M-07　`rules.yaml` 中新增的规则无法在 Web 端启用

**位置**　`api/routes/profile.py:228-237`

```python
if rule_id not in profile["rules"]:
    raise HTTPException(status_code=404, detail=f"规则ID不存在: {rule_id}")
```

前端 `web/app.js:202-215` 对 `/api/rules` 返回的**每一条**规则都渲染开关，开关状态取自 `profile.rules`；而 `profile.rules` 中不存在的规则一律显示为"关"。此时点击开关即收到 404：

```
PUT /api/profile/rules/SW-BRAND-NEW-99 -> 404 {"detail":"规则ID不存在: SW-BRAND-NEW-99"}
```

规则作者往 `rules.yaml` 增加新规则后，用户必须手工编辑 `profile.yaml` 才能启用——与"Web 端可开关全部规则"的产品预期不符。

**修复建议**　允许写入 `profile.rules` 中尚不存在的键（校验其存在于 `rules.yaml` 即可）：

```python
if rule_id not in {r["id"] for r in yaml.safe_load(open(_rules_path))["rules"]}:
    raise HTTPException(status_code=404, detail=f"规则库中不存在: {rule_id}")
profile["rules"][rule_id] = toggle.enabled
```

---

### 🟡 M-08　SSE 缺响应头与心跳，任务卡死时连接永不释放

**位置**　`api/app.py:120-137`

```python
async def event_stream():
    while True:
        ...
        await asyncio.sleep(1)

return StreamingResponse(event_stream(), media_type="text/event-stream")
```

* 未设置 `Cache-Control: no-cache` 与 `X-Accel-Buffering: no` → 经 Nginx 等反代时事件流会被缓冲，实时进度表现为"一次性跳到 100%"；
* 无心跳注释（`: keep-alive\n\n`）→ 中间代理可能在空闲 60 秒后主动断开；
* `while True` 仅在任务状态为 `done`/`failed` 时退出，而 C-02 保证了任务**永远**停在 `running` → 每打开一次「检查任务」页签就泄漏一条长连接。

**修复建议**

```python
return StreamingResponse(
    event_stream(),
    media_type="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
             "Connection": "keep-alive"},
)
```

并在循环中加入心跳与最大存活时长；同时让 `_run_task` 在异常时把任务置为 `failed`（见 C-02 修复建议）。

---

### 🟡 M-09　`profile.yaml` 读-改-写无锁，并发请求丢失更新

**位置**　`api/routes/profile.py:151-187`（`_save_profile`）

`_save_profile` 执行"读全文 → 逐段改写 → 临时文件 + `os.replace`"的读-改-写流程。原子替换保证了文件不会写坏，但**不保证不丢更新**：两个并发 `PUT /api/profile/variables` 会互相覆盖。CLI 同时在线编辑 `profile.yaml` 也有同样问题。

**修复建议**　在模块级加一把 `threading.Lock`，并用 `fcntl.flock`（Linux）做跨进程文件锁；同时保留"临时文件 + 原子替换"的写法。

---

### 🟡 M-10　凭据卫生检查的正则无法匹配本项目真实口令

**位置**　`.github/workflows/ci.yml:88`、`tests/test_regression.py:266`、`profile.yaml:35-36`

CI 的卫生检查：

```bash
hits=$(git grep -I -n -E 'Huawei@12[0-9]' -- . | grep -v -E '^(docs|tests)/' || true)
```

该正则想匹配的口令形态是 `Huawei@123`，而项目里**实际使用的** SNMP 团体字是：

```
profile.yaml:35:  snmpRo: Ro@Huawei123            # SW-SNMP-01: SNMP只读团体字
profile.yaml:36:  snmpRw: Rw@Huawei123            # SW-SNMP-02: SNMP读写团体字
```

注意 `@` 位于 `Huawei` **之前**（`Ro@Huawei123`），因此 `Huawei@12[0-9]` 匹配不上。逐字节核验：

```
b'Huawei'      in blob -> True
b'Huawei@'     in blob -> False     ← '@' 在 Huawei 之前
b'Ro@Huawei123' in blob -> True
```

于是：

* CI 卫生检查**恒通过**（唯一的命中在 `tests/`，被 `grep -v` 排除），仓库里带明文 SNMP 团体字却无人发现；
* 回归用例 A09 的断言 `"Huawei@123" not in dev` 同样**检测不到**以 `Ro@Huawei123` 形式出现的真实口令；
* 更实际的风险：`profile.yaml` 是随包分发且**默认带值**的，用户若未按 README 第六节替换 `snmpRo`/`snmpRw`，就会把一个公开可查的弱团体字 `Ro@Huawei123` / `Rw@Huawei123` 部署到生产交换机上。

**修复建议**

* 修正正则以覆盖真实形态，例如 `git grep -I -n -E '(Huawei|Ro@Huawei|Rw@Huawei)' -- .` 或直接用更宽的 `@Huawei[0-9]{3}`；
* `profile.yaml` 中的团体字改为空占位（如 `snmpRo: ""`）+ 醒目注释，强制用户填写；
* 规则校验时拒绝默认占位值：`validate_vars` 中对 `snmpRo`/`snmpRw` 做"仍为示例值"的检查并给出警告；
* A09 断言改为读取 `devices.csv` 中 `password` 列的实际值集合，与 `profile.yaml` 中的团体字做交叉比对。

---

## 五、低危与加固项

| 编号 | 位置 | 问题 | 建议 |
|---|---|---|---|
| L-01 | `api/routes/tasks.py:75` | `t["status"] != "done"` 依赖 `TaskStatus(str, Enum)` 的字符串相等语义 | 改为 `t["status"] != TaskStatus.done` |
| L-02 | `core/config_parser.py:28` | `_tpl_cache` 为无上限模块级字典，键含变量值；实测 2000 组不同变量 → 2002 条缓存；Web 进程长期运行会持续增长，且非线程安全 | 换用 `functools.lru_cache` 或加容量上限/TTL |
| L-03 | `core/config_parser.py:66` | exact 模式 `(?m)^\s*...\s*$` 中 `\s` 含 `\n`，实测 `"shutdown"` 可匹配 `"\n\n shutdown\n\n"` | 改用 `[^\S\n]*`（仅水平空白）表达"忽略缩进" |
| L-04 | `core/collector.py:73,84,96` | 口令原样写入 Telnet 流，含 CR/LF 的口令会向设备会话注入命令 | 写入前剥离 `\r\n`：`password.replace("\r","").replace("\n","")` |
| L-05 | `core/collector.py:69-73` | 用整个已收缓冲区判断"当前是否口令提示"，banner 中含 `password`/`密码` 会导致把**口令当用户名**发送 | 只判断最后一个提示符行 |
| L-06 | `core/collector.py:169` | 解析时丢弃所有以 `<Word>` 开头的行，banner 中出现该形态会被静默从快照删除 | 改为只丢弃与设备提示符完全一致的行 |
| L-07 | `core/engine.py:88-91` | `if platform and platform not in rule.platforms`：`platform`/`device_type` 为空时**过滤整体失效**，CE 专属规则会跑到 S 系列设备上 | 空值时应判定为"不适用"并告警 |
| L-08 | `core/reporter.py:248-257` | Excel 单元格写原始值，若内容以 `=`/`+`/`-`/`@` 开头会被 Excel 当作公式（公式注入） | 写入前对上述前缀加 `'` 转义 |
| L-09 | `core/reporter.py:268-274` | `write_report_files` 直接 `print` 汇总信息，Web 路径下污染服务器日志而非返回给调用方 | 改为返回摘要，由调用方决定输出位置 |
| L-10 | `api/store.py:63-76` | `to_csv_rows()` 返回**明文口令**且全项目无人调用（死代码，携带凭据泄漏隐患） | 删除，或改为返回掩码值 |
| L-11 | `api/schemas.py:39-44` + `api/routes/devices.py:73` | `exclude_none=True` 导致无法把口令置空 | 增加 `password=""` 的显式清空语义 |
| L-12 | `api/app.py:144` | `@app.on_event("startup")` 在 FastAPI ≥0.93 已废弃 | 改用 `lifespan` 上下文管理器 |
| L-13 | `api/app.py:38` | `logging.basicConfig(level=logging.INFO)` 在导入期覆盖 uvicorn 的日志配置 | 删除，交给 uvicorn 或仅配置本项目 logger |
| L-14 | `api/app.py` | 未设置 `Content-Security-Policy` / `X-Frame-Options` / `Referrer-Policy` | 补齐安全响应头；CSP 可作为 H-03 的第二道防线 |
| L-15 | `api/app.py:96` | 令牌比对使用了 `hmac.compare_digest`（正确），但**无限流/无锁定**，可在线爆破 | 增加失败计数与退避 |
| L-16 | `core/rule_loader.py:116-124` | `validate_vars` 为扫描 `${...}` 而把每条规则 `yaml.dump` 成文本再正则，应直接遍历原始字符串 | 直接遍历 `rule.sections[*].checks[*].lines` 与 `fix_text` |
| L-17 | `core/engine.py:20` | `run_rule` 对每条规则重新 `split_blocks(cfg)`，单设备重复解析 12 次 | 在 `run_all` 中解析一次并下传 |
| L-18 | `cli.py:103`、`core/reporter.py:47` | 畸形 CSV（缺 `ip` 列）触发 `KeyError` 裸栈 | 加载设备时做 schema 校验并给出友好报错 |
| L-19 | `core/reporter.py:294` | `save_snapshots` 吞掉 `OSError`，快照写失败却报告"已保存 N 台" | 记录失败清单并输出告警 |
| L-20 | `web/app.js:300-305` | 切到「检查任务」页签时以空参调用 `loadTaskResults()`，会清空已展示的结果 | 记住上一次 `taskId` |

---

## 六、验证方法与复现步骤

### 6.1 隔离原则

本次审计**未在仓库内执行任何会写入文件的命令**。所有动态验证均在 `%TEMP%\opencode\vrp-audit` 的副本中完成（仅复制 `core/ api/ web/ tests/ snapshots/` 与入口/配置文件），仓库工作区在审计全程保持 `git status` 仅有审计前既有的 ` D docs/AUDIT-2026-10-21.md` 一项。

### 6.2 复现环境

```
Python 3.13.15
PyYAML 6.0.3 / fastapi 0.143.2 / pydantic 2.14.1 / openpyxl 3.1.5 / starlette 1.8.0
（与 requirements.txt 锁定版本一致）
```

> 说明：随包 `runtime/` 与 `vendor/pydantic_core/` 为 aarch64 Linux 二进制，无法在本机执行；涉及 C-02 的判定改用 CPython 3.12 官方源码语义佐证（`Lib/asyncio/tasks.py` 的 `yield _wait_for_one()` 与 "The futures 'f' are not necessarily members of fs" 注释），结论不依赖具体 Python 小版本。

### 6.3 复现命令

```bash
# C-01 —— 模块导入即崩溃（只读操作）
python -c "import api.store"
# TypeError: 'function' object is not subscriptable  (api/store.py:58)

# C-02 —— 任务调度崩溃（需先修好 C-01 使应用能启动）
#   POST /api/tasks 之后观察任务状态与 output/reports/<task_id>/

# H-01 / H-02 —— HTTP 状态码
curl -i -X POST http://127.0.0.1:8080/api/devices \
     -H 'Content-Type: application/json' \
     -d '{"name":"SW-01","ip":"10.0.0.1","platform":"s-series","username":"admin","password":"p"}'
# HTTP/1.1 500 Internal Server Error

curl -i http://127.0.0.1:8080/api/tasks/<task_id>/results
# HTTP/1.1 500 Internal Server Error

# M-10 —— 凭据卫生检查失效
git grep -I -n -E 'Huawei@12[0-9]' -- .        # 只命中 tests/，profile.yaml 不被发现
git grep -I -n -F 'Ro@Huawei123' -- profile.yaml   # 真实存在的明文团体字
```

### 6.4 CLI 链路实测结论

在副本中执行 `python cli.py --offline`（等价于 CI 的离线自检步骤），结果正常：

```
启用规则: 10 条 / 设备总数: 3 台, 并发采集开始
采集完成: 10.10.1.11 (接入SW-01)
完成! 设备 3 台 / 不合规项: 2 条 / 建议优化: 1 条
  output/remediation_report.md
  output/baseline_report.xlsx
```

结论：**`core/` 引擎层与 CLI 编排层是健康的**，本轮问题高度集中在 `api/` 与 `web/` 两层。

---

## 七、修复优先级建议

### P0 —— 恢复可用性（建议合入一个版本）

1. **C-01** 修 `api/store.py` 的类内 `list` 遮蔽（一行改名即可）；
2. **C-02** 修 `api/task_manager.py` 的 `as_completed` 键错配，并补异常兜底使任务能落到 `failed`；
3. **H-01** 修 `POST /api/devices` 的重复关键字参数；
4. **H-02** 在 API 边界把 `Rule`/`Finding` 转成 dict，并让前端按同一 schema 渲染；
5. **H-06** 恢复 CI 绿灯（同步 `docs/` 报告路径 + 增加导入冒烟检查）。

### P1 —— 消除安全风险

6. **H-03** 前端转义（`escAttr`/`escJs` + 事件委托）与安全响应头；
7. **H-05** `ip` 字段白名单校验 + 快照路径归一化；
8. **H-04** `PUT /api/profile` 改合并语义 + 引用完整性保护；
9. **M-10** 修正凭据卫生正则，`profile.yaml` 团体字改为空占位。

### P2 —— 纠正结论正确性

10. **M-01** `vlan1_check` 误报（影响 high 级规则结论，需同步补回归用例）；
11. **M-04** 报告保留全合规设备；
12. **M-05 / M-07** 导入校验与规则开关放开；
13. **M-02 / M-03 / M-06 / M-08 / M-09** 鉴权与可用性加固。

### P3 —— 健壮性与可维护性

14. 第五节 L-01 ~ L-20 逐项清理。

---

## 八、建议补充的回归用例

现有 `tests/test_regression.py` 采用"引擎层 + API 层"两段式手写断言（无 pytest 依赖，便于离线绿色版运行）。建议补充以下用例，全部可在现有框架内表达：

| 建议编号 | 关联 | 用例要点 |
|---|---|---|
| A19 | C-01 | `import api.store` 必须成功（最廉价的冒烟检查，建议同时进 CI） |
| A20 | C-02 | 创建任务后 30 秒内状态必须为 `done` 或 `failed`，且 `output/reports/<task_id>/` 存在报告文件 |
| A21 | H-01 | `POST /api/devices` 返回 200，且随后 `GET /api/devices` 能看到该设备 |
| A22 | H-02 | `GET /api/tasks/{id}/results` 返回 200，且 `findings[0].rule_id` 非空 |
| A23 | H-03 | 设备 IP 含 `'`、`"`、`<` 时，渲染出的 HTML 中不得出现未转义的可执行上下文 |
| A24 | H-04 | `PUT /api/profile` 只更新指定变量，其余变量与规则开关必须保持不变 |
| A25 | H-05 | `ip` 含 `../` 或绝对路径时必须被拒绝 |
| A26 | M-01 | access 口 + `port default vlan 10` + 无 `port link-type` → `SW-VLAN1-01` 不得报出 |
| A27 | M-04 | 报告的 `devices[]` 长度必须等于被检设备数 |
| A28 | M-07 | 对 `rules.yaml` 中存在但 `profile.rules` 中缺失的规则，切换开关应成功 |
| A29 | M-10 | 卫生检查必须能命中 `Ro@Huawei123` 这类真实口令形态 |
| A30 | H-06 | CI 中增加 `python -c "import api.app"` 冒烟步骤 |

---

## 九、附录：审计覆盖清单

| 目录 | 文件 | 状态 |
|---|---|---|
| `core/` | `models.py` `rule_loader.py` `config_parser.py` `builtin_checks.py` `engine.py` `collector.py` `credentials.py` `reporter.py` `__init__.py` | ✅ 全部审读 + 动态验证 |
| `api/` | `app.py` `schemas.py` `store.py` `task_manager.py` `__init__.py` | ✅ 全部审读 + 动态验证 |
| `api/routes/` | `devices.py` `profile.py` `tasks.py` `system.py` `__init__.py` | ✅ 全部审读 + 动态验证 |
| `web/` | `index.html` `app.js` `style.css` | ✅ `index.html`/`app.js` 审读 + XSS 构造验证；`style.css` 仅审读 |
| 根目录 | `cli.py` `vrp_baseline.py` | ✅ 审读 + 离线端到端实跑 |
| `tests/` | `test_regression.py` | ✅ 审读 + 实跑（发现 API 层用例从未执行） |
| `.github/` | `workflows/ci.yml` | ✅ 审读 + 逐 job 失败推演 |
| 部署 | `Dockerfile` `docker-compose.yml` `docker-entrypoint.sh` `.dockerignore` `run.sh` `run_web.sh` `pack.sh` `requirements.txt` `.gitignore` | ✅ 审读 |
| 配置 | `rules.yaml` `profile.yaml` `devices.csv.example` `snapshots/10.10.1.11.cfg` | ✅ 审读 + 规则语义验证 |
| 文档 | `README.txt` | ✅ 审读（与实现的偏差见 M-06/M-07/H-04） |
| `vendor/` `runtime/` | 第三方代码 | ⛔ 按约定排除（仅抽查 `anyio` 对 `sniffio` 的缺失依赖，已确认可降级运行，不构成问题） |

---

*报告生成时间：2026-10-22 · 本次审计未修改仓库代码，仅新增本文件*
