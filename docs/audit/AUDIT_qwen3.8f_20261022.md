# VRP 基线检查工具 — 第二轮代码审计报告

| 项目 | 内容 |
|---|---|
| 审计对象 | 华为 VRP 基线检查工具（v2.2 规则库 + 三层架构 + FastAPI + Docker） |
| 审计基线 | commit `514c4ef`（HEAD, 2026-10-22）— 已包含上一轮 A01–A18 修复与三轮 CI 修补 |
| 上一轮参照 | `docs/AUDIT-2026-10-21.md`（commit `0b8ff4f`；**该文件在当前工作区处于未提交的删除状态**） |
| 审计范围 | `core/` `api/` `web/` `cli.py` `vrp_baseline.py` `rules.yaml` `profile.yaml` `tests/` `snapshots/` + 部署配置与 CI（排除 `vendor/` 第三方代码） |
| 一方代码规模 | 约 2 100 行 Python + 394 行前端 JS + 301 行规则库 |
| 审计日期 | 2026-10-22 |
| 审计方法 | 静态逐行审阅 + **仓库外临时副本动态复现**（构造真实 VRP 配置、字节码取证、异常路径实测） |
| 是否改动代码 | **否**。本次审计对 `D:\Programing\net-lint` 零写入；所有动态验证在 `%TEMP%\audit-verify`（仓库副本）上执行 |
| 结论 | 新发现 **31 项**缺陷（严重 4 / 高 10 / 中 12 / 低 5）；上一轮 18 项中 **4 项仅部分修复、3 项修复引入新问题、3 项无任何自动化防护** |

**证据标记**：`实测` = 已在副本上跑出结果；`静态` = 代码可直接读出；`取证` = 通过字节码/官方语义推定；`待确认` = 需真实 Docker/浏览器复现。

---

## 一、结论摘要

| 编号 | 严重度 | 模块 | 问题 | 证据 |
|---|---|---|---|---|
| N01 | 🔴 严重 | `api/task_manager.py:134` | Python≤3.12 下 `asyncio.as_completed` 返回协程对象而非 future，`futures[fut]` 抛 `KeyError` → Web 检查任务永久 `running` | 实测+取证 |
| N02 | 🔴 严重 | `api/routes/devices.py:47-51` | `DeviceOut(**model_dump(), password=...)` 重复关键字 → `POST /api/devices` 100% 500，Web"添加设备"完全不可用 | 实测 |
| N03 | 🔴 严重 | `core/engine.py:88-91` + `core/reporter.py:77-78` | `platform`/`device_type` 精确匹配失败 → 整套规则静默 no-op，报告呈现为"全部合规" | 实测 |
| N04 | 🔴 严重 | `core/collector.py:61,164` | 提示符正则 `<[A-Za-z_]+>` 不接受数字与连字符 → 绝大多数真实 `sysname` 登录判定失败 | 实测 |
| N05 | 🟠 高 | `core/builtin_checks.py:117-125` | Eth-Trunk 上应用的流量策略看不见（`PHYS` 过滤掉 `Eth-Trunk` 块）→ 成员口被判"缺口端口" | 实测 |
| N06 | 🟠 高 | `core/builtin_checks.py:130-139` | 在用物理口集合为空即判 PASS；`global outbound`（方向错误）亦被视为已阻断 | 实测 |
| N07 | 🟠 高 | `core/builtin_checks.py:187-205` | 忽略 `undo port trunk allow-pass vlan 1`；把 `port default vlan 20` 与三层子接口判为"默认携带 VLAN1" | 实测 |
| N08 | 🟠 高 | `core/engine.py:35` + `core/reporter.py:22-29` | builtin 多 target 被 `"; "` 拼接后整体注入 `${if}` → 生成的整改命令非法 | 实测 |
| N09 | 🟠 高 | `rules.yaml:206-215, 224-234, 243-245` | 两条链式规则 fix 缺 `system-view`；VLAN1 fix 对 Vlanif/access 口下发 trunk 命令 | 静态 |
| N10 | 🟠 高 | `rules.yaml:116,132` + `profile.yaml:35-36` | fix 把明文团体字塞进 `cipher` 关键字后；SNMP RO/RW 明文入库并被无鉴权 API 回显 | 静态 |
| N11 | 🟠 高 | `rules.yaml:95-96` | `\s*$` 锚点使 `ntp-service unicast-server <ip> source <ip>` 判不合规（合规设备恒定误报） | 实测 |
| N12 | 🟠 高 | `web/app.js:93-94,222` + `api/schemas.py:21` + `api/routes/devices.py:99-111` | CSV 导入零校验 + `esc()` 不转单引号 + `onclick="editDevice('${d.ip}')"` → 存储型 XSS | 静态 |
| N13 | 🟠 高 | `docker-compose.yml:61-65` vs `README.txt:412-416` | `cli` 服务不挂 `profile.yaml`（读镜像内默认值）；文档指引的 `sample.cfg`/`devices.csv` 路径均不可用 | 实测+静态 |
| N14 | 🟠 高 | `vendor/pydantic_core/` | 仅含 `cpython-312-aarch64-linux-gnu.so` → 离线绿色版在 x86_64 上 `import fastapi` 必失败，Web 整体不可用 | 实测 |
| N15 | 🟡 中 | `core/builtin_checks.py:67-71,145` | classifier 只取第一个 `if-match acl`；不支持命名 ACL 与 `if-match acl name` → 合法配置恒 FAIL | 实测 |
| N16 | 🟡 中 | `core/rule_loader.py:116-125` + `core/config_parser.py:59-61` | `validate_vars` 只校验键存在不校验值：空值编译成 `(?:)`（匹配任意前缀→漏报）或 `(?:None)`（恒误报） | 实测 |
| N17 | 🟡 中 | `api/app.py:85,91,95` + `docker-compose.yml:49` | 鉴权默认关闭、`/docs` 与 `/openapi.json` 不受保护、multipart 导入无 CSRF、令牌可走查询参数 | 静态 |
| N18 | 🟡 中 | `api/task_manager.py:107,114-176` | `create_task` 未持强引用；无外层 try；`TaskStatus.failed` 全仓无赋值点 → 任何异常都表现为"永久 running" | 静态 |
| N19 | 🟡 中 | `api/task_manager.py:167-174`、`api/routes/profile.py:47-49,243-244` | Excel 生成/快照落盘/YAML 解析在事件循环内同步执行 → 大任务期间整服务无响应 | 静态 |
| N20 | 🟡 中 | `api/routes/profile.py:136-146` | prune 后仍用旧 `end` 索引插入：新键被插进 `rules:` 段，或删除末段时 `IndexError`；`PUT /api/profile` 整段替换会静默删除其余变量 | 实测 |
| N21 | 🟡 中 | `api/routes/profile.py:177-183` + `docker-compose.yml:37` | `os.replace` 覆盖单文件 bind mount 目标 → 容器内 EBUSY/EXDEV，A07 的修复被 A11 的实现抵消 | 待确认 |
| N22 | 🟡 中 | `core/collector.py:134` | `expect()` 返回值被丢弃（A05 同类残留）：分屏未关闭时静默继续；且无 `finally`，`close()` 二次抛错会覆盖原异常 | 静态 |
| N23 | 🟡 中 | `cli.py:128-139` / `api/task_manager.py:137-156` | A01 的结构性根因仍在：采集与 `run_all` 同 try，单条坏规则 → 整台设备已检出的不合规项被 `("ERROR",)` 整体替换 | 实测 |
| N24 | 🟡 中 | `api/routes/devices.py:102-103` + `web/app.js:179` | CSV 编码硬编码 `utf-8-sig`，中文 Windows 的 GBK 导出 → 裸 500，前端丢弃 `detail`，无任何可诊断信息 | 静态 |
| N25 | 🟡 中 | `core/reporter.py:290-295` | 快照写失败 `except OSError: pass` → 离线复检静默复用上一轮陈旧快照（与 A05 同类） | 静态 |
| N26 | 🟢 低 | `api/store.py:21-23,63-76`、`output/reports/<id>` | `list()` 返回内部 dict 引用（与 `get()` 副本语义不一致）；任务/结果/报告目录永不清理；`to_csv_rows()` 是含明文口令的死代码 | 静态 |
| N27 | 🟢 低 | `cli.py:90` / `task_manager.py:85` | 规则开关默认关（`switches.get(rid, False)`），新增规则忘加开关即静默不执行 | 实测 |
| N28 | 🟢 低 | `Dockerfile:56` + `.dockerignore` | `COPY . /app/` 把 `profile.yaml`（明文团体字）与 `snapshots/*.cfg`（cipher 块）烘进镜像层；无 `USER`、无 `HEALTHCHECK` | 静态 |
| N29 | 🟢 低 | `core/reporter.py:238-252` | Excel/MD 单元格无 `= + - @` 前缀处置 → 设备可控文本导致的公式注入 | 静态 |
| N30 | 🟢 低 | `ci.yml:88,99` | 把"审计报告文件存在"当测试断言（当前该文件已被删除未提交，提交即红）；口令扫描只匹配单一口令形态 | 静态 |
| N31 | 🟢 低 | `core/config_parser.py:21,28`、`rules.yaml:24-25,57,78`、`api/app.py:113,144` | 注释/文档仍固化 A02 修复前的错误假设；`_tpl_cache` 无上限；`version` 字段无消费者；废弃的 `on_event`；未用导入（A13 同类残留） | 静态 |

---

## 二、严重缺陷详述

### 🔴 N01 — Web 检查任务在 Python 3.12 上必然崩溃，且表现为"永久运行中"

**现象**　浏览器点"开始检查"后进度条永远停在 0%，SSE 连接不关闭，报告下载恒返回 400"任务尚未完成"。CLI 路径不受影响。

**根因**　`api/task_manager.py:126-136` 用 `dict[Future] -> dev` 作映射，然后：

```python
for fut in asyncio.as_completed(futures):
    dev = futures[fut]              # :135
```

`asyncio.as_completed` 在 **Python ≤ 3.12** 中 `yield` 的是 `_wait_for_one()` **协程对象**，不是原 future（3.13 才改为返回 future 本身）。取证：本机 `python312.zip/asyncio/tasks.pyc` 第 638 行字节码为 `PUSH_NULL; LOAD_FAST _wait_for_one; CALL; YIELD_VALUE`，且 `_on_completion` 向队列 `put_nowait(f)`、`_wait_for_one` 返回 `f.result()` —— 即 `await` 拿到的是结果，而 `futures[...]` 拿到的是协程对象。

**实测**　用同构的 `@types.coroutine` 生成器复现：`KeyError: <coroutine object _wait_for_one at 0x...>`。

**影响链**　`_run_task` 无外层 try（N18）→ 异常直接杀死后台任务 → `task_store` 状态停留在 `running`、`completed=0`；异常抛出点位于 `with ThreadPoolExecutor(...)` 内，`__exit__` 的 `shutdown(wait=True)` 在协程里**同步 join**，最长可达 `15+10+120≈145s`（`core/collector.py` 超时上限），期间整个事件循环停摆（所有页面请求、其它任务的 SSE 一起卡住）。`Dockerfile:27` 基线正是 `python:3.12-slim`，CI matrix 亦含 3.12。

**为何没被发现**　`grep` 确认 `create_and_run_task` / `_run_task` / `TestClient` / `httpx` 在 `tests/`、`ci.yml`、`requirements.txt` 中**零命中** —— 异步任务执行体从未被任何测试跑过。

**修复方向**　改用 `concurrent.futures.as_completed(futures)`（同步版本，配 `run_in_executor` 时应保留 dict 键序语义）或 `asyncio.wait(..., return_when=FIRST_COMPLETED)`；`_run_task` 外层加 try/except 置 `failed` + `message`；`create_task` 返回值存入集合并挂 `add_done_callback` 释放。

---

### 🔴 N02 — 添加设备接口 100% 返回 500

**根因**　`api/routes/devices.py:47-51`：

```python
return DeviceOut(**dev.model_dump(), password="***" if dev.password else "", ...)
```

`DeviceCreate.model_dump()`（`api/schemas.py:28`）恒含 `password` 键，与显式关键字重名。

**实测**　`TypeError: dict() got multiple values for keyword argument 'password'`（等价调用复现）。

**影响**　Web"添加设备"提交后必 500（设备已写入 store，仅响应阶段崩溃 → 用户看到失败但列表里已有一台，重复点击则 409）。同文件其余四个端点手写字段所以不受影响，掩盖了问题。CI 走的是直接调用路由函数之外的路径且不经 ASGI 栈（`ci.yml:56` + `requirements.txt` 无 httpx），response_model 校验层完全没被测到。

**修复方向**　`d = dev.model_dump(); d["password"] = "***" if ... ; return DeviceOut(**d)`；并补 `httpx`+`TestClient` 让 CI 真正打通一次 HTTP。

---

### 🔴 N03 — 设备属性取值不匹配时，整套规则静默失效并伪装成"全部合规"

**根因**　两处叠加：

1. `core/engine.py:88-91`：`platform not in rule.platforms` 与 `rule.device_type != device_type` 均为**大小写与空格敏感的精确比较**，而 `core/credentials.py:34-43` 用 `csv.DictReader` 直出、无任何归一化与取值校验（`api/schemas.py` 的 `Platform` 枚举只覆盖 API 新增路径，覆盖不到 CSV 导入与 `devices.py:99-111`）。
2. `core/reporter.py:77-78`：设备若无任何 finding 也无 error，直接 `continue`，报告里不出现该设备段落。

**实测**

| CSV 取值 | 命中规则数 |
|---|---|
| `platform=s-series, type=交换机` | 4 |
| `platform=S-Series, type=交换机` | **0** |
| `platform=s-series, type=switch` | **0** |
| `platform='' , type=''` | 4（不填反而安全） |

一台规则全灭的设备，报告呈现为：`device_count=1  summary={total_fail:0,total_suggest:0,total_devices:1}  devices段落数=0` —— 与"这台设备完全合规"**不可区分**。

**影响**　这是本工具最危险的失效模式：基线检查的价值在于"漏报"，而这里漏报被渲染成合规，运维团队会据此签署"网络达标"结论。任何一次手填 CSV 的大小写失误、或新增 `device_type`（如"接入交换机"）都会整批触发。

**修复方向**　① 比较前 `strip().lower()` 并允许别名表；② `load_devices` 对 `type`/`platform` 取值做白名单校验，非法值直接让任务失败而非跳过；③ `run_all` 返回命中规则数，`build_report_data` 对"命中 0 条规则"的设备显式输出 `<该设备未匹配任何规则，检查未执行>` 段落；④ 报告首部输出"启用规则数 × 设备数 = 期望检查数"。

---

### 🔴 N04 — 登录提示符正则排斥数字与连字符，在线采集对多数真实命名不可用

**根因**　`core/collector.py:61` `prompt_re = rb"<[A-Za-z_]+>"`，`core/collector.py:164` 清理回显时同样用 `<[A-Za-z_]+>`。`devices.csv.example` 注意事项 3 与 `README.txt:256,327,379` 把它作为"用户需保证的约束"写进了文档。

**实测**

| 真实提示符 | 匹配 |
|---|---|
| `<Huawei>`（出厂默认） | ✅ |
| `<SW1>` | ❌ |
| `<SW-01>` | ❌ |
| `<SW-01-SAMPLE>` | ❌（**这正是随包样例 `snapshots/10.10.1.11.cfg:28` 的 sysname**） |
| `<Core_SW2>`、`<AR2220>` | ❌ |

**影响**　凡 sysname 含数字或连字符的设备（生产网络的常态命名，如 `ACC-SW-01`、`Core2`），`_telnet_login` 在最后一步 `expect([prompt_re])` 拿不到匹配 → 抛 `TelnetAuthError("登录失败, 未检测到VRP提示符")`，错误信息把**格式约束问题伪装成认证问题**，运维会去查 AAA 配置而浪费大量时间。项目自身样例文件即违反该约束，说明该路径从未在真实设备上被完整验证过。

**修复方向**　提示符改为 `rb"<[^>\r\n]+>"`（并在清理时用同一条动态构造的正则）；错误信息区分"未匹配提示符"与"认证失败"；同步修正样例 sysname。

---

## 三、高缺陷详述

### 🟠 N05 / N06 — 策略链覆盖度判定的两处失效

`core/builtin_checks.py:114-139` 的逐接口覆盖度检查有两个方向相反的漏洞，均已实测：

- **N05 误报**：`applied_names` 循环先 `if not PHYS.match(name): continue`，而 `PHYS`（`core/models.py:13-16`）不含 `Eth-Trunk` —— **在 Eth-Trunk 上应用的策略永远看不见**。成员口因 `eth-trunk 1` 被计入 `ports`，却无法从 trunk 侧获得豁免（`:126-128` 的 `m2t` 展开逻辑正是为此写的，但因 trunk 块先被 `continue` 掉而永不执行）。实测配置（`traffic-policy p6 inbound` 应用在 `Eth-Trunk1`，成员 `Gi0/0/1/2`，另有独立口 `Gi0/0/3` 已应用）结果：`[WARN] IPv6阻断: 策略链完整但未全局应用, 缺口端口(2个): GigabitEthernet0/0/1, GigabitEthernet0/0/2`。汇聚/核心设备上链路聚合是常态，误报面积大。
- **N06 漏报**：`missing = sorted(set(ports) - applied_names)`，当 `ports` 为空（设备只有 Vlanif、或所有物理口都 shutdown）时 `missing` 恒空 → `:139` 返回 `PASS "策略链完整且覆盖全部在用物理口"`。实测一份"策略从未在任何位置应用（连 `global inbound` 都没有）"的配置得到 **PASS**。方向错误的 `traffic-policy X global outbound` 同样不会命中 `:106` 的 `global inbound`，进而落入这条空集合 PASS 分支。

**修复方向**　覆盖度循环放开 `Eth-Trunk`/`PortGigabitEthernet` 等聚合口；`ports` 为空时返回 `WARN/UNKNOWN`（"无法判定，设备无在用物理口"）而非 PASS；显式区分 `inbound`/`outbound` 并对仅 outbound 的应用报 FAIL。

### 🟠 N07 — VLAN1 检查对最常见的正确配置恒定误报

`core/builtin_checks.py:187-194` 只读 `^\s+port trunk allow-pass vlan (.+)$` 的**第一条**命中，且不识别 `undo port trunk allow-pass vlan 1`（该行以 `undo` 开头，`^\s+port` 不匹配）。而 S 系列 trunk 口默认放行 VLAN1、必须靠 `undo` 裁剪，**这正是 `README` 与本工具推荐的标准做法**。实测：

```
interface GigabitEthernet0/0/6
 port link-type trunk
 port trunk pvid vlan 10
 port trunk allow-pass vlan 1 to 4094
 undo port trunk allow-pass vlan 1        ← 已正确裁剪
→ [FAIL] GigabitEthernet0/0/6: trunk放行VLAN1
```

同函数 `:203-205` 的 `elif "port link-type" not in b` 分支，把"省略 link-type（VRP 默认即 access）但已 `port default vlan 20`"的端口、以及三层子接口 `Gi0/0/4.100` 一并判为"默认携带 VLAN1(pvid=1)"。实测二者均误报。叠加 N08 后，工具还会对这些 access/Vlanif 口给出 trunk 整改命令。

**修复方向**　识别 `undo port trunk allow-pass` 并从集合中扣除；`port default vlan N`（N≠1）优先于"默认携带"判定；子接口按其父口判定或跳过；`Vlanif1` 与物理口分开给 fix。

### 🟠 N08 — 整改命令把多个违规对象拼成一条非法命令

`core/engine.py:35` 对 builtin 返回的 `pos` 列表执行 `"; ".join(pos)` 存进单个 `Finding.target`；`core/reporter.py:22-29` 的 `render_fix` 把整个 `target` 字符串塞进 `${if}`。实测：

```
target = 'Vlanif1; GigabitEthernet0/0/1; GigabitEthernet0/0/2'
生成的整改命令（将被直接粘进交换机）：
  interface Vlanif1; GigabitEthernet0/0/1; GigabitEthernet0/0/2
   port trunk allow-pass vlan 2 to 4094
```

**影响**　这是一份"整改文档生成器"，输出的就是给人复制到设备上执行的命令。多违规对象时命令必然报错；若运维用 `; ` 分隔手工拆分，也会因 N07/N09 的命令类型错配而改错配置。

**修复方向**　builtin 逐 target 产出独立 `Finding`（`reporter` 已按 finding 渲染 fix，天然支持）；或让 `render_fix` 接受列表并按 target 展开成多段。

### 🟠 N09 / N10 — 规则库 fix 文本自身的命令错误

| 规则 | 行号 | 问题 |
|---|---|---|
| SW-IPV6-01 | `rules.yaml:206-215` | 缺 `system-view`（其余 9 条 section 类规则都有），首行 `acl 4000` 在用户视图直接报错 |
| SW-DHCP-02 | `rules.yaml:224-234` | 同上 |
| SW-VLAN1-01 | `rules.yaml:243-245` | 对 `Vlanif1` 与 access 口下发 trunk 专用命令 |
| SW-SNMP-01/02 | `rules.yaml:116,132` | `snmp-agent community read cipher ${snmpRo}`，而 `profile.yaml:35-36` 的 `Ro@Huawei123` 是**明文** → 设备按密文解析，生成的团体字与网管侧不一致（监控中断）或命令被拒；应为 `simple` |
| SW-SNMP-04 | `rules.yaml:164` | `target-host trap-hostname ... udp-port 162 params securityname` 混合 S/CE 两套语法、缺 `udp-domain`，现网大概率报错 |
| SW-PS-02 | `rules.yaml:301` | 末尾 `commit` 仅在 CE 两阶段提交模式下有效 |

另：`kind: vrp` 失败时证据文本直接把原始正则吐给用户（`core/engine.py:57` → 报告里显示 `缺失配置: ['^snmp-agent target-host\\b']`），运维读到的是正则而不是"缺少 SNMP trap 主机配置"。建议 `lines` 增加可选 `desc` 字段用于人类可读证据。

### 🟠 N11 — NTP 规则的真实形态匹配不到

`rules.yaml:95-96` 以 `\s*$` 收尾，而合规配置常带 `source`：

| 配置 | 实测结果 |
|---|---|
| `ntp-service unicast-server 10.1.1.101 source 10.0.0.1` | **FIRE（误报）** |
| `ntp-service unicast-server 10.1.1.101` | PASS |
| `ntp-service peer 10.1.1.101` | FIRE（可接受，但 title 写的是"必须配置NTP"） |

### 🟠 N12 — 存储型 XSS：设备 IP / 变量键名未做上下文转义

- `web/app.js:55-59` 的 `esc()` 用 `textContent→innerHTML`，只转 `& < >`，**不转单引号与双引号**；
- `web/app.js:93-94` 把它拼进 JS 字面量：`onclick="editDevice('${d.ip}')"`；
- `api/schemas.py:21` 的 `ip: str` 无任何格式约束，`api/routes/devices.py:99-111` 的 CSV 导入更是绕开 pydantic 直接入库；
- `web/app.js:222` 用未转义的 `${k}` 拼 `id="var-${k}"`，而 `api/routes/profile.py:107,144-146` 对新增变量键名不做校验。

**攻击路径**　导入一份 `ip = 1.1.1.1');fetch(...)//` 的 CSV（例如从工单系统/他人转发的清单文件），操作员打开设备列表页即在其浏览器执行脚本；`web/app.js:6-11` 的 API 令牌存于 localStorage → 令牌外泄 → 攻击者反向驱动无鉴权服务端 telnet 打内网交换机。默认部署零鉴权（N17）使该链路无需任何凭据即可发起。

**修复方向**　事件改 `data-ip` + `addEventListener`（或 `JSON.stringify` 后再实体转义）；`ip` 加 `pattern` 与 `field_validator`；CSV 导入走同一 pydantic 模型；变量键名白名单 `^[A-Za-z_][A-Za-z0-9_-]*$`。

### 🟠 N13 — Docker 部署链路三处断裂

1. **CLI 读的是镜像里的 profile.yaml**：`docker-compose.yml:61-65` 的 `cli` 服务只挂 `devices.csv`/`rules.yaml`/`snapshots`/`output`，**未挂 `profile.yaml`**；而 `README.txt:414` 让用户 `cp profile.yaml data/` 并编辑其中的变量。结果：操作员把 NTP 改成实际地址后跑 `docker compose run --rm cli --offline`，用的仍是镜像内烘的默认值（`Dockerfile:56` `COPY . /app/`），结论静默错误。`Dockerfile:19` 自己给出的 `docker run` 示例倒是挂了 `profile.yaml` —— compose 与其文档矛盾。
2. **文档指引的离线样例不存在**：`README.txt:416` 让 `cp snapshots/sample.cfg data/snapshots/`，但 `core/collector.py:180` 只按 `<IP>.cfg` 取文件 → `FileNotFoundError: 离线模式下缺少快照: .../sample.cfg`。
3. **首次运行不自洽**：`README.txt:412` 的 `cp devices.csv data/` 在 `devices.csv` 已被 `.gitignore:27` 排除的全新 clone 上必然失败；若用户跳过该步，Docker 会把缺失的 bind 源创建成**目录**，`cli.py:94` 的 `os.path.exists` 对目录同样为真 → `open()` 抛 `IsADirectoryError`，用户拿到一条裸 traceback。

### 🟠 N14 — `vendor/` 内的二进制是错误架构，离线绿色版 Web 功能整体不可用

`vendor/pydantic_core/` 只有 `_pydantic_core.cpython-312-aarch64-linux-gnu.so`（ARM64 Linux）。x86_64 的 Windows 开发机与 ubuntu-latest 均无法 `import pydantic_core` → `import fastapi` 失败。影响面：

- 打包为"离线绿色版"分发时（`pack.sh`、`PYTHONPATH=vendor:.`），CLI 可用但 **Web 完全起不来**；
- `tests/test_regression.py` 的 25 条 API 用例（A06/A08/A10/A11/A12/A15）在本机恒 `skip`，而 `main()` 仍返回 0 → **"全绿但半数未跑"**；
- CI 靠 `pip install -r requirements.txt`（`ci.yml:43`）在 x86_64 上拉到正确 wheel 才掩盖了这一点。

**修复方向**　`pack.sh` 按目标架构分别打包（或只 vendored 纯 Python 依赖，Web 依赖强制要求 pip）；测试脚本在"被 skip 的用例数 > 0"时返回非零，或至少打印醒目统计。

---

## 四、中/低缺陷要点（未在上文展开者）

- **N15**　`core/builtin_checks.py:70-71` 用 `re.search` 只取 classifier 第一条 `if-match acl`，实测"同时 `if-match acl 3000` 与 `acl 4000`"的合法配置被判 `[FAIL] ACL ['4000'] 未被任何流分类引用`；`_acl_blocks`（`:20`）与 `:70` 均只认 4 位数字 ACL，实测 `acl name blockv6` + `if-match acl blockv6` 恒 FAIL。改 `findall` 求交 + 支持按名引用。
- **N16**　实测 `ntpServer1` 置空后编译结果：`None → '^ntp-service unicast-server (?:None)'`、`'' 或 [] → '^ntp-service unicast-server (?:)'`。空选择组匹配空前缀 → **任何** `ntp-service unicast-server ...` 都算合规（漏报）。`validate_vars` 只看键存在性，拦不住。应校验值类型与非空。
- **N18/N19**　异步层的结构性问题：未持 task 强引用（CPython 官方明确警告可被 GC）、无 per-device `wait_for` 与全局 deadline、报告生成在事件循环内同步跑、`GET /api/rules` 每请求重解析 11KB YAML 无缓存。
- **N20**　实测 `PUT /api/profile`（prune + 新增键）时新键被插进 `rules:` 段；若 prune 的最后一段则是 `IndexError`（`profile.py:142` 读 `lines[insert_at-1]`）。`_save_profile:164-175` 的回读校验能拦下错插但代价是 500。另 `tests/test_regression.py:318-319` 把"整段替换只保留指定键"这一**破坏性语义固化为期望值**，前端一旦漏传某个变量即静默删除它。
- **N21**　A07 的修复（挂载可写，`docker-compose.yml:37`）与 A11 的实现（`tempfile.mkstemp(dir=/app)` + `os.replace` 覆盖 `/app/profile.yaml`）冲突：目标是单文件 bind mount 挂载点，`rename(2)` 语义为 EBUSY。需 `docker compose up` 后实测一次 PUT 才能定论，但静态判断概率高。卷目录 `./data/output` 是目录挂载，不受影响。
- **N22/N23/N25**　同族问题：**关键返回值被丢弃 / 异常被过度吞没**。`core/collector.py:134` 不看 `expect` 索引；`cli.py:137-139` 的 try 同时包住采集与规则执行（实测加入一条含非法正则的规则即抛 `error`，该设备已检出的不合规项被整体替换为"采集失败"）；`core/reporter.py:294` 快照写失败 `pass`。A01/A05 只修了触发点，没修这个结构。
- **N24**　`api/routes/devices.py:103` 只认 `utf-8-sig`，中文 Windows Excel 默认"CSV(ANSI)"即 GBK → `UnicodeDecodeError` 未捕获 → 裸 500；`web/app.js:179` 又 `throw new Error("导入失败")` 丢弃 `detail`。国内主力场景"导入设备清单"失败且不可诊断。
- **N26–N29**　卫生类：任务与报告目录无 TTL（常驻服务内存与磁盘单调增长）；`to_csv_rows()`（`api/store.py:63-76`，含明文 password）为无调用者的待接上泄漏点；规则开关默认关（实测 `profile.yaml:50,56` 关掉 SW-SNMP-05 与唯一的 CE 规则 SW-PS-02 → CE 设备只跑 8 条、无任何端口安全类检查）；镜像烘入配置且 root 运行；Excel 单元格未防 `=+-@` 公式注入。
- **N30/N31**　CI 与文档：`ci.yml:99` 用"审计报告文件存在"当断言（**当前工作区该文件是未提交的删除，提交即 hygiene 红**）；`ci.yml:88` 的口令扫描只匹配 `Huawei@12[0-9]`，扫不到 `profile.yaml:35-36` 的团体字与 `snapshots/*.cfg:30-31` 的 cipher 块；`core/config_parser.py:21` 的 docstring 与 `rules.yaml:24-25,57` 仍在教 A02 修复前的错误语义（"第一个 # 之前即 system"）；`_tpl_cache`（`:28`）无上限，Web 每改一次变量就多一条缓存。

---

## 五、上一轮 18 项修复复核

| 编号 | 上轮声称 | 本轮复核结论 |
|---|---|---|
| A01 | 已修复 | ✅ 崩溃点已修（`_view_name`/列表语义统一），但**结构性根因保留** → N23 |
| A02 | 已修复 | ✅ 逻辑已修（合并非 interface 段），**文档/注释未同步** → N31；system 上下文仍会吞入 acl/traffic/vlan 段（当前 12 条规则无误报，属架构隐患） |
| A03 / A04 | 已修复 | ✅ `re.MULTILINE` 已在 `compile_tpl:71` 与 `pass_preconds:115,118` 落地 |
| A05 | 已修复 | ⚠️ **部分修复**：`:138` 抓取配置已判 `idx<0`，但 `:134` 关闭分屏的 `expect` 返回值仍被丢弃 → N22 |
| A06 | 已修复 | ✅ `report_dir()` 按 task_id 隔离（`task_manager.py:35-41`）。残留：下载文件名对所有任务恒定，浏览器里互相覆盖（观感） |
| A07 | 已修复 | ⚠️ **修复引入新问题**：挂载确已可写，但被 A11 的 `os.replace` 原子写抵消 → N21（待确认） |
| A08 | 已修复 | ✅ `OUTPUT_DIR=<根>/output` 与 compose 卷一致；`README.txt:416-418` 的路径描述仍是平铺旧结构（文档漂移） |
| A09 | 已修复 | ⚠️ **部分修复**：`devices.csv` 已不跟踪、工作区口令列已清空；但 ① `git show 35cfddd:devices.csv` 历史仍含 3 台设备明文口令；② `profile.yaml:35-36` 团体字仍明文入库并由无鉴权 `GET /api/profile` 回显；③ `snapshots/*.cfg:30-31` cipher 块仍在库；④ CI 扫描模式只认那一个口令（N30） |
| A10 | 已修复 | ⚠️ **部分修复**：CORS 通配+凭据已根治（`app.py:65-79`，默认不加中间件）；但默认部署**仍零鉴权**（token 为可选注释项）、`/docs`/`/openapi.json` 不在保护前缀内、multipart 导入无 CSRF 校验、令牌可走查询参数 → N17/N12 |
| A11 | 已修复 | ⚠️ **修复引入新问题**：注释保留目标达成（定点行替换 + 回读校验），但 prune 索引错位/键名不校验/嵌套值畸形/`os.replace` 与单文件挂载冲突 → N20/N21/N12 |
| A12 | 已修复 | ✅ 注册顺序正确（`app.py:162` 早于 `:170`），且 CI 有**动态** `curl /api` 断言（`ci.yml:154-162`）—— 本轮唯一被真实跑起来验证过的历史缺陷 |
| A13 | 已修复 | ⚠️ `api/routes/tasks.py` 导入已干净；同类残留于 `api/app.py:113`、`api/routes/devices.py:14`、`task_manager.py:44` 死参数、`store.py:63` 死方法 → N31（CI 无 lint 关卡，故不会被拦） |
| A14 | 已修复 | ✅ `[^;\r\n]*` 不跨行（`builtin_checks.py:157`），3 条实测用例通过 |
| A15 | 已修复 | ⚠️ 列映射已修（`reporter.py:237`）；但测试读的是**上一次运行留下的 xlsx**（`tests:245-258`），本地陈旧产物会掩盖 reporter 回归（仅 CI 靠 `ci.yml:48-53` 预生成才有意义） |
| A16 | 已修复 | ⚠️ **只修了一半**：`10.10.1.11.cfg` 已改为 `#` 分段（实测 22 段 / 6 个 interface 目标）；但同为随包样例的 `snapshots/sample.cfg` **仍是空行分段**（实测 5 段 / **0 个 interface 目标**），其头部 12-13 行声明的 SW-PS-01/SW-VLAN1-01 因此静默 PASS，且没有任何用例覆盖它 |
| A17 | 已修复 | ⚠️ `vrp_baseline.py` 显式 utf-8 已生效（GBK 环境实测可过），但用例是 `grep 'encoding="utf-8"'` 源码文本，捕获不了行为回归 |
| A18 | 已修复 | ✅ `vendor` 改为 `sys.path.append`（`cli.py:28`、`rule_loader.py:30`、`app.py:30`），已安装同名片段优先 |

**统计**：完全修复 6 项（A02/A03/A06/A08/A12/A14/A18 中的 6 条）· 部分修复 5 项（A05/A09/A10/A15/A17）· 修复引入新问题 2 项（A07→N21、A11→N20）· 只修一半 1 项（A16）· 结构未动 1 项（A01→N23）。
**A07 / A13 / A18 三项完全无自动化用例**；A05 / A17 为"grep 源码"型伪测试；A06/A08/A10/A11/A12/A15 共 25 条用例在本地恒 skip（N14）。

---

## 六、做得好的地方

- `api/app.py:65-79`：CORS 默认**完全关闭**、白名单模式且 `allow_credentials=False`，注释里准确写出了"通配 + 凭据"组合的危害与攻击后果 —— 这是上一轮 A10 的正确修法。
- `api/app.py:96`：用 `hmac.compare_digest` 做常量时间比较，未自研 token。
- `api/routes/profile.py:60-63,83-148,164-175`：注释保留方案是"真解决"而非"绕过"，且**落盘前回读校验**，把两处渲染错位 bug 挡在污染用户配置之前。
- `api/store.py`：设备/任务读写全程 `threading.Lock`，`get()` 返回副本。
- `core/reporter.py:35,61-65` 的 `("ERROR", (ip, msg, []))` 约定与 `api/task_manager.py:149` 的构造一致，采集失败不会炸掉整份报告。
- CI 的 `docker` 作业是**真实起容器 + curl 断言**（`ci.yml:127-163`），并在失败时自动附容器状态/日志/退出码 —— A12 因此是唯一被动态验证过的修复，这个模式值得推广到其它端点。
- `core/collector.py:140-146` 对 `telnetlib.expect` 超时"不抛异常只返回 -1"这一语言陷阱的注释与处置准确，是 A05 的正确修复。

---

## 七、建议的修复顺序

1. **先止住"伪装成合规"的失效**：N03（规则匹配归一化 + 报告显式提示"未执行检查"）、N06（空端口集合不得 PASS）、N01/N02（Web 任务与添加设备当前根本不可用）。
2. **再修"给人错的命令"**：N08/N09/N10/N07/N11 —— 本工具的产物是运维直接粘贴到现网交换机的命令，错误命令的后果高于漏报本身。
3. **补测试地基**（否则上述修复仍会回归）：`requirements.txt` 增 `httpx` + `pytest`，用 `TestClient` 走一遍真实 HTTP（同时暴露 N01/N02/response_model）；给 `_run_task` 加一条端到端用例；把 A05/A17 的源码 grep 改为行为断言；skip>0 时非零退出。
4. **安全面收口**：N12（转义与校验）、N17（默认鉴权 + `/docs` 保护 + CSRF）、N10/A09 残留（团体字移出配置、历史清理、CI 扫描泛化）、N28/N29。
5. **部署一致性**：N13（compose 挂 `profile.yaml`、README 与实现互相印证）、N14（vendor 架构）、N21（容器内 PUT 实测）。

---

## 附录 A：动态验证方式（可复现）

所有验证在仓库**外**的副本执行，`D:\Programing\net-lint` 全程零写入（未运行会产物的测试、未生成 `__pycache__`，`python` 一律带 `-B`）。副本内容 = `core/` + `api/schemas.py` + `rules.yaml` + `profile.yaml` + `snapshots/` + `vendor/`。

| 探针 | 结论 |
|---|---|
| `asyncio/tasks.pyc` 第 638 行字节码 + 同构 `@types.coroutine` 复现 | N01 `KeyError: <coroutine object ...>` |
| 等价 `dict(**d, password=...)` 调用 | N02 `TypeError: ... multiple values for keyword argument 'password'` |
| 遍历 `(platform, device_type)` 组合跑 `run_all` | N03 `S-Series`/`switch`/`SW` → 命中 0 条规则；`build_report_data` 段落数 0 |
| `<[A-Za-z_]+>.fullmatch()` 对 6 种提示符 | N04 仅 `<Huawei>` 命中 |
| 构造 Eth-Trunk 策略链配置跑 `SW-IPV6-01` | N05 成员口被判缺口 |
| 无在用物理口 + 策略从未应用 | N06 判 PASS |
| `undo port trunk allow-pass vlan 1` / `port default vlan 20` / 子接口 | N07 三者均误报 |
| 多违规口配置 + `render_fix` | N08 `interface Vlanif1; Gi0/0/1; Gi0/0/2` |
| 三种 NTP 写法 | N11 带 `source` 者 FIRE |
| 多 `if-match` / 命名 ACL | N15 两者恒 FAIL |
| `compile_tpl` 空变量 | N16 `(?:None)` 与 `(?:)` |
| `split_blocks` 对两份样例快照 | A16 `10.10.1.11.cfg` 22 段/6 目标，`sample.cfg` 5 段/**0 目标** |
| 非法正则规则注入 `run_all` | N23 抛出后整台设备结果被替换 |

## 附录 B：工作区状态提示（非代码缺陷）

- `git status` 显示 `D docs/AUDIT-2026-10-21.md`（已删除、未提交）。若提交，`.github/workflows/ci.yml:99` 的 hygiene 步骤将失败。本轮审计未改动该状态。
- `.freebuff/`、`.workbuddy/`、`output/`、`__pycache__/` 为未跟踪的本地产物；`output/` 内留有上一轮运行生成的 `baseline_report.xlsx`（A15 用例会读它，见第五节）。
- `README.txt:38` 称 `core/` 有 7 个模块，实为 8 个；其反复引用的 `runtime/` 目录在本工作区不存在，而 `run.sh:34` 无条件 `exec runtime/bin/python3.12`（README:361 承诺的"回退到系统 python3"在脚本中没有实现）→ 全新 clone 上 `./run.sh --offline` 会先失败在这里。
