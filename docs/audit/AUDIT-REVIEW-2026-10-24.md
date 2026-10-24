# 审计报告横向复核与评分（2026-10-24）

> 复核对象：`docs/audit/` 下 7 份针对 `net-lint`（华为 VRP 基线检查工具 v2.2，HEAD `514c4ef`）的审计报告。
> 复核方式：逐条对照代码 + 最小复现 + git 历史比对；不修改被审代码。
> 复核环境：Python 3.12.2（本机无 fastapi，`vendor/pydantic_core` 为 aarch64 二进制，无法在本机导入 Web 栈），因此 HTTP 层结论以源码语义 + 最小复现为准，引擎层结论全部实跑。
> 计分规则：**每条经复核为真的独立缺陷 +2；每条不成立/根因或解法错误的条目 -1；每漏掉一个基准严重缺陷 -1；报告自称"已验证/已修复"而事实不成立 -1。**

---

## 一、结论总览

| 排名 | 报告 | 条目数 | 有效 | 错误 | 漏基准 | 其他扣分 | **总分** | 基准覆盖 | 质量评级 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `AUDIT-minimax-2026-10-22.md` | 38 | 35 | 3 | 1 | 0 | **66** | 5/6 | A |
| 2 | `AUDIT_qwen3.8f_20261022.md` | 31 | 31 | 0（3 处细节过头，未扣分） | 2 | 0 | **60** | 4/6 | A- |
| 3 | `AUDIT-hy4-2026-10-22.md` | 27 | 27 | 0 | 4 | 0 | **50** | 2/6 | B+ |
| 4 | `AUDIT-dsf-2026-10-22.md` | 22 | 22 | 0 | 0 | 0 | **44** | 6/6 | A |
| 5 | `AUDIT-agnes-2026-10-21.md` | 18 | 18（A07 仅发现有效，净 +1） | 1 处修复无效 | 6 | -1 | **28** | 0/6 | B- |
| 6 | `AUDIT-GlyphCluster-20261022.md` | 8（另有 4 处结论失实） | 8 | 4 | 6 | 0 | **6** | 0/6 | D |
| 7 | `AUDIT-beam-2026-10-22.md` | 5（另有 1 处动作失实） | 5 | 1 | 5 | 0 | **4** | 1/6 | C |

> 总分严格按"每条 +2 / -1"计算，因此**条目多且真的报告天然占优**。若改看"单位条目的可信度"和"严重缺陷覆盖"，`dsf` 与 `minimax` 并列最优，`qwen` 在 VRP 业务语义上最强，`hy4` 在 API 层根因分析上最锋利。详见第六节。

---

## 二、评分基准：6 个经复核的严重缺陷（K1–K6）

这 6 项是本轮复核确认"必然导致主功能失效或结论错误"的缺陷，用于计算"漏报"扣分。

| 编号 | 缺陷 | 复核证据 |
|---|---|---|
| **K1** | `api/store.py:21` 类体内 `def list()` 遮蔽内建 `list`，`:58` 的注解 `devices: list[dict]` 在类体求值时解析到方法对象 → `TypeError: 'function' object is not subscriptable`，`api.app` 无法导入，**Web 从启动即失败** | 最小复现（同类类体）必定抛该异常；`git log -- api/store.py` 仅 `35cfddd` 一次提交，缺陷自首版存在 |
| **K2** | `api/task_manager.py:134-135` `for fut in asyncio.as_completed(futures): dev = futures[fut]` → `as_completed` 普通迭代产出的是 `_wait_for_one()` 协程而非原 Future，必 `KeyError`，**每个 Web 检查任务首台设备完成即崩** | CPython 3.13 源码 `_AsCompletedIterator.__next__` 返回 `self._wait_for_one(resolve=True)`，仍是协程；只有 `async for` 才产出 Future。故 3.12/3.13 的 `for` 迭代均崩 |
| **K3** | `api/routes/devices.py:47` `DeviceOut(**dev.model_dump(), password=...)` 重复关键字 → `POST /api/devices` 恒 500（设备却已写入内存） | `DeviceCreate.model_dump()` 必含 `password` 键；重复关键字在调用点即 `TypeError` |
| **K4** | `api/routes/tasks.py:52` `/results` 直接返回 `[(Rule,[Finding])]` 对象；`Check`/`Finding` 用 `__slots__` 无 `__dict__`，`jsonable_encoder` 的 `dict()`→`vars()` 兜底均失败 → 有不合规项即 500 | `vendor/fastapi/encoders.py` 兜底路径为 `dict(obj)` → `vars(obj)` → `ValueError`；`core/models.py` 中 `Check`/`Finding` 确为 `__slots__` |
| **K5** | `core/engine.py:88-91` 对 `platform`/`device_type` 做精确比较，不匹配即跳过；调用方不检查"零规则执行"，`core/reporter.py:77` 又把无 finding 设备整段剔除 → **整机"全部合规"且无任何告警** | 实跑：`device_type="Switch"`、`platform="S-Series"` 命中规则数均为 **0**；`build_report_data([], ...)` 的 `devices` 为空数组 |
| **K6** | `core/builtin_checks.py` `vlan1_check` 三类误报 + 两类漏报 | 实跑见下；access 口 `port default vlan 10`（无 link-type）、带 `undo port trunk allow-pass vlan 1` 的 trunk、三层子接口均被判"携带 VLAN1"；`Eth-Trunk` 放行 VLAN1 与实际 `port hybrid untagged vlan 1` 则检不出 |

关键实测输出（本机 Python 3.12.2）：

```text
vlan1_check:
('FAIL',
 'GigabitEthernet0/0/7: 默认携带VLAN1(pvid=1); GigabitEthernet0/0/6: trunk放行VLAN1; '
 'GigabitEthernet0/0/4.100: 默认携带VLAN1(pvid=1)',
 ['GigabitEthernet0/0/7', 'GigabitEthernet0/0/6', 'GigabitEthernet0/0/4.100'])

Eth-Trunk 上应用流量策略（成员口 Gi0/0/1、Gi0/0/2）：
('WARN', '... 缺口端口(2个): GigabitEthernet0/0/1, GigabitEthernet0/0/2', ...)   ← 误报

策略从未应用、且无在用物理口：
('PASS', 'IPv6阻断: 策略链完整且覆盖全部在用物理口', None)                        ← 漏报

同一 classifier 两条 if-match acl：
('FAIL', "IPv6阻断: ACL ['4000'] 未被任何流分类引用", None)                        ← 误报

run_all(platform="S-Series") / run_all(device_type="Switch") → 0 条规则
render_fix(..., target="Vlanif1; Gi0/0/1; Gi0/0/2"):
'interface Vlanif1; GigabitEthernet0/0/1; GigabitEthernet0/0/2\n port trunk allow-pass vlan 2 to 4094'

split_blocks: snapshots/10.10.1.11.cfg → 22 段 / 7 个 interface 段
              snapshots/sample.cfg     → 5 段 / 0 个 interface 段   ← 仍是空行分段
```

另有两条与被审计报告相关的事实：

- `api/routes/profile.py` 的 `_update_section(prune=True)`：新增键会被插进 `rules:` 段（实测 `brandNew` 落在 `rules` 内）；若被 prune 的段位于文件末尾且同时新增键，则 `lines[insert_at-1]` 直接 `IndexError`。
- `hmac.compare_digest("中文令牌", "abc")` → `TypeError: comparing strings with non-ASCII characters is not supported`。

---

## 三、逐篇分析

### 3.1 minimax（66 分，38 条，35 条成立，3 条不成立，漏 K5）

**做得最好的地方**：唯一一份把"Web 完全跑不起来"的两个串联阻断项（K1 + K2）都定位到语言语义层的报告，且给出了可复现的异常文本；安全面（XSS、路径穿越、令牌/@docs、CI 正则失效）与工程卫生（缓存无界、公式注入、事件循环阻塞）覆盖最全。

| 编号 | 判定 | 复核依据 |
|---|---|---|
| C-01（K1） | +2 | 类体遮蔽 + 注解立即求值，成立 |
| C-02（K2） | +2 | `as_completed` 普通迭代产出协程，成立（3.12/3.13 均如此） |
| H-01（K3） | +2 | 重复关键字，成立 |
| H-02（K4） | +2 | `Check`/`Finding.__slots__` + 编码器兜底，成立（"前端字段名对不上"一句过头：`Rule` 确有 `rid/title/severity`） |
| H-03 | +2 | `esc()` 不转义引号 + `onclick="editDevice('${d.ip}')"`，成立 |
| H-04 | +2 | `PUT /api/profile` prune 语义删除其余变量 → `validate_vars` 抛 KeyError → 后续任务 500，链路成立（前端未调用该端点，严重度偏高） |
| H-05 | +2 | `ip` 无校验 → 快照路径穿越（`.cfg` 约束），成立 |
| H-06 | +2 | CI 三 job 失败（test/hygiene/docker），当前工作区确有 `D docs/AUDIT-2026-10-21.md` |
| M-01 | +2 | access 口 `port default vlan 10` 误报，已实测 |
| M-02 | +2 | 查询参数令牌对所有 `/api` 生效，成立 |
| M-03 | +2 | `/docs`、`/openapi.json` 不在守卫前缀内，成立 |
| M-04 | +2 | 全合规设备被 `continue` 剔除，已实测 |
| M-05 | +2 | CSV 无校验 + 整表替换 + 非法 platform 使 `GET` 恒 500，成立 |
| M-06 | +2 | 设备 CRUD 仅内存、启动被 `devices.csv` 覆盖，成立 |
| M-07 | +2 | `rules.yaml` 新增规则在 profile 中缺失 → 404，成立 |
| M-08 | +2 | SSE 无 `Cache-Control`/心跳/超时，成立 |
| M-09 | +2 | profile 读改写无锁，成立 |
| M-10 | +2 | `Huawei@12[0-9]` 匹配不到 `Ro@Huawei123`，成立 |
| L-01 | **-1** | `TaskStatus(str, Enum)` 与 `"done"` 比较是正确的，不是缺陷 |
| L-02 / L-04 / L-05 / L-06 / L-07 / L-08 / L-09 / L-10 / L-12 / L-13 / L-14 / L-15 / L-16 / L-17 / L-18 / L-19 / L-20 | +2×17 | 逐条核对成立（缓存无界、口令 CRLF、缓冲区判断提示符、提示符行被丢弃、空 platform 过滤失效、公式注入、print、死代码带明文、on_event、basicConfig、安全响应头、无限流、validate_vars 用 yaml.dump、逐规则重复分段、缺 ip 裸 KeyError、快照写失败被吞、切页签清空结果） |
| L-03 | **-1** | `\s*` 含换行确实成立，但"shutdown 可匹配 `\n\n shutdown\n\n`"不是缺陷——该配置本来就含 shutdown；无任何误判后果 |
| L-11 | **-1** | 用 `password=""` 可以清空口令（`model_dump(exclude_none=True)` 会保留空串），"无法置空"不成立 |
| 漏 K5 | -1 | 未识别"platform/device_type 不匹配 → 零规则 → 全绿"这一失效模式（L-07 讲的是相反方向） |

**其他问题**：结论摘要写"28 项"，实际列出 38 条（低危 20 条），统计口径与实际不符（不扣分，但影响可读性）。整体可信度高。

---

### 3.2 qwen3.8f（60 分，31 条全部成立，漏 K1/K4）

**做得最好的地方**：VRP 业务语义最扎实——提示符正则排斥数字、Eth-Trunk 策略不可见、空端口集合判 PASS、VLAN1 的 `undo`/子接口、NTP 带 `source`、多 `if-match`/命名 ACL、两份样例快照的分段差异、prune 的 `IndexError`，这些都是别的报告没抓到或抓得不全的。对上一轮 A01–A18 的逐条复核也有实际价值（A05/A07/A09/A10/A15/A16/A17 的残留判断基本正确）。31 条主结论全部成立，另有 3 处细节过头（N01 的版本边界、N16 的影响、N17 的 CSRF），已在对应条目注明。

| 编号 | 判定 | 复核依据 |
|---|---|---|
| N01（K2） | +2 | 缺陷成立；但"3.13 改为返回 future 本身"是错的——3.13 普通 `for` 迭代仍产出 `_wait_for_one(resolve=True)` 协程，只有 `async for` 才产出 Future（细节错误，不改结论） |
| N02（K3） | +2 | 成立 |
| N03（K5） | +2 | `platform`/`device_type` 精确匹配 → 0 规则，已实测 |
| N04 | +2 | `<[A-Za-z_]+>` 拒绝 `<SW-01-SAMPLE>`（正是随包样例的 sysname），成立且重要 |
| N05 | +2 | Eth-Trunk 应用策略不可见，已实测 WARN 误报 |
| N06 | +2 | 在用物理口为空即 PASS，已实测（"仅 outbound 也被视为已阻断"只在空端口时成立，措辞略宽） |
| N07（K6） | +2 | `undo`/access/子接口三类误报，已实测 |
| N08 | +2 | 多目标拼接成非法命令，已实测 |
| N09 | +2 | `SW-IPV6-01`/`SW-DHCP-02` 的 fix 缺 `system-view`；VLAN1 fix 对 Vlanif/access 下发 trunk 命令——均可在 `rules.yaml` 直接读到 |
| N10 | +2 | `cipher ${snmpRo}` 配明文团体字、`profile.yaml` 明文入库 + 无鉴权回显，成立 |
| N11 | +2 | `ntp-service unicast-server X source Y` 被判不合规，已实测 |
| N12 | +2 | XSS 三处，成立 |
| N13 | +2 | cli 未挂 `profile.yaml`、`README:416` 的 `sample.cfg` 无法被 `<IP>.cfg` 逻辑读取、全新 clone `cp devices.csv` 必失败——全部成立 |
| N14 | +2 | `vendor/pydantic_core` 仅 aarch64 `.so`，成立 |
| N15 | +2 | 多 `if-match` 只取第一条（已实测）；命名 ACL 不识别（`\d{4}`），成立 |
| N16 | +2 | `validate_vars` 只校验键存在，成立；但"`(?:)` 匹配任意前缀 → 漏报"不成立（实测带 `\s*$` 的 NTP 模板不会匹配任意地址） |
| N17 | +2 | 默认无鉴权、`/docs` 不受保护、令牌可走查询参数成立；"multipart 无 CSRF"是弱指控（本 API 不用 Cookie，跨站表单无法带自定义头），不影响整体成立 |
| N18–N31 | +2×14 | 逐条成立（create_task 无强引用、同步 IO 阻塞事件循环、prune 索引错位含 IndexError、单文件挂载 `os.replace` EBUSY、`screen-length` expect 未判、cli 的 try 包住 run_all、GBK CSV 裸 500、快照写失败被吞、store 引用/TTL/死代码、规则开关默认关、镜像烘配置且 root、公式注入、CI 断言/正则、陈旧注释与缓存无界） |
| 漏 K1 | -1 | **未发现 `api/store.py` 的 `list` 遮蔽**。这不是小遗漏：它使"Web 不可用"的真实首因判断错位（报告把 Web 不可用归因于 vendor 架构），也意味着其 HTTP 层"实测"不可能真正经过 `api.app` |
| 漏 K4 | -1 | 全文检索无"序列化/jsonable/__slots__"，`/results` 500 未提及 |

**其他问题**：结论摘要的"完全修复 6 项"与随后列举的 7 条不一致；N16 的影响描述、N17 的 CSRF 属细节过度推断。总体是"业务逻辑最强、但漏掉最大阻断项"的一份报告。

---

### 3.3 hy4（50 分，27 条全部成立，漏 K1/K2/K5/K6）

**做得最好的地方**：API 层根因写得最精确——N01 的 `DICT_MERGE` 机制、N02 的 `jsonable_encoder` 兜底链、N07 的"删除行后 `end` 索引失效"都经复核完全正确；27 条无一是编造的。

| 编号 | 判定 | 复核依据 |
|---|---|---|
| N01（K3） | +2 | 成立，机制描述准确 |
| N02（K4） | +2 | 成立，兜底链描述准确 |
| N03 | +2 | `_run_task` 无 try/except、无 `failed` 终态，成立 |
| N04 | +2 | 下载链接无法带 `X-API-Token`，成立 |
| N05 | +2 | "有 sections 但零目标 → 静默 PASS"，与实测的 `select_targets` 空字典一致 |
| N06 | +2 | 默认无鉴权 + `0.0.0.0`，成立（属既定设计，仍是有效安全项） |
| N07 | +2 | prune + 新增键错位并触发回读校验 500，已实测 |
| N08 | +2 | 多目标整改命令非法，已实测 |
| N09 | +2 | `${platform}` 匹配为 `\S+`、渲染为空串，与 `compile_tpl`/`render_fix` 一致（当前规则库未使用该变量，属潜在项） |
| N10–N27 | +2×18 | 逐条成立（无锁、嵌套 dict 破坏 YAML、内存增长、返回内部引用、异常信息外泄、CSV 无校验、快照权限、`screen-length`、on_event、死代码、未用参数、print、XSS、规则库校验缺失、事件循环阻塞、审计文件缺失、缺文件被 Docker 建成目录、README 的 commit 说明） |
| 漏 K1 | -1 | 未发现模块导入即失败 |
| 漏 K2 | -1 | 全文无 `as_completed`，任务 100% 崩溃未识别 |
| 漏 K5 | -1 | 未讨论 platform/device_type 失配导致零规则 |
| 漏 K6 | -1 | 未讨论 `vlan1_check` 的误报/漏报 |

**其他问题**：因为本机无法导入 fastapi，它没有做任何 HTTP/启动验证，于是把"两个 500"当成主 P0，却没意识到服务根本起不来；结论"引擎层健康、两个 P0"偏乐观。若补上 K1/K2 的验证，这份报告的 API 部分会是最有价值的。

---

### 3.4 dsf（44 分，22 条全部成立，基准覆盖 6/6）

**做得最好的地方**：唯一一份**六个基准缺陷全部命中**、且零误报的报告；也是唯一在隔离 venv 里真正跑起 ASGI 应用、拿到 HTTP 500 与异常栈的报告（A21/A22 的异常文本与源码语义完全吻合）。A28（单文件 bind mount 上 `os.replace` → EBUSY）虽标注"未实测"，但 Linux 语义判断正确，且是别的报告没提的关键点——它直接解释了 A07 修复为何无效。

| 编号 | 判定 | 复核依据 |
|---|---|---|
| A19（K1） | +2 | 成立 |
| A20（K2） | +2 | 成立 |
| A21（K3） | +2 | 成立 |
| A22（K4） | +2 | 成立 |
| A23 | +2 | 成立 |
| A24 | +2 | SSE 无超时/断开检测，成立 |
| A25 | +2 | 存储型 XSS，成立 |
| A26 | +2 | CSV 导入三种坏输入，成立 |
| A27 | +2 | cli 未挂 profile.yaml，成立 |
| A28 | +2 | EBUSY 判断正确（作者已声明未实测） |
| A29（K5） | +2 | 零规则全绿，已实测 |
| A30（K6） | +2 | Eth-Trunk 与 hybrid untagged 漏检，成立（未覆盖 access/undo 两类误报，但已构成 K6 命中） |
| A31–A40 | +2×10 | 逐条成立（ACL 块越界、多目标命令、路径穿越、TTL、ip 校验、非 ASCII 令牌 500、团体字明文、缺 ip KeyError、测试有效性、上一轮 52/52 与事实不符） |

**其他问题**：条目数最少（22），所以按"每条 +2"算总分不占优；但信噪比最高。A39 的"用例多为源码 grep"和 A40 的"验证记录需可复现"是有分量的方法论批评。A28 与 A22 的未实测项都做了明确标注，符合审计伦理。

---

### 3.5 agnes（28 分，18 条，修复多为真，但验证声明失实）

这是第一轮"发现+修复"报告。**发现本身基本可信**：我按 `35cfddd` 逐条比对旧代码，A01（`blocks.items()` 对 list）、A03（无 MULTILINE）、A04（preconds 无 MULTILINE）、A05（丢弃 expect 索引）、A14（`[^;]*` 跨行贪婪）、A15（中文表头查英文键）、A16（旧样例空行分段）、A06（固定报告路径）、A08（`base_dir` 当输出目录）、A10（通配 CORS+凭据）、A12（Mount 先注册）、A17（`exec(open())` 未指定编码）、A18（`sys.path.insert(0, vendor)`）全部与旧代码吻合。

| 编号 | 判定 | 复核依据 |
|---|---|---|
| A01–A06 | +2×6 | 旧代码逐条核实成立 |
| A07 | **+1** | 问题成立；但修复无效——只把 `:ro` 改成读写，`_save_profile` 的 `os.replace` 打在单文件挂载点上仍会 EBUSY（dsf A28 / qwen N21），且 compose 的 `cli` 服务至今完全没挂 `profile.yaml`（beam A20/dsf A27），"已修复"不成立 |
| A08–A18 | +2×11 | 逐条成立（A11 的注释保留方案已验证有效；prune 边界遗留 bug 另计） |
| 自称"52/52 通过" | **-1** | 不可能：该套件的 API 段在 `from api.app import app` 处必因 K1 抛 TypeError，`main()` 捕获后返回 1。52 条也不可能在 API 段被跳过的环境下凑齐 |
| 漏 K1–K6 | -6 | 这 6 项都在它审计/修改过的代码里（api/、core/builtin_checks.py、core/engine.py），全部漏掉 |

**其他问题**：A05 只修了抓取配置的超时，`screen-length` 的 expect 仍未判（qwen N22）；A16 只修了 `10.10.1.11.cfg`，同为随包样例的 `snapshots/sample.cfg` 仍是空行分段（实测 5 段 / 0 个 interface 段），"随包样例自检"目标（G10）并未真正达成。作为"修复记录"它有实际价值，但"18 项已全部修复并回归验证"的结论不成立——这也是后续几份报告反复引用"上一轮已修复"而放松警惕的根源。

---

### 3.6 GlyphCluster（6 分，无具体缺陷编号，结论失实）

**可取之处**：架构评述准确（`core/` 零 Web 依赖、规则与配置分离、CORS 默认关闭、`hmac.compare_digest`），提到 Telnet 明文与默认无鉴权、任务/缓存无上限、`asyncio.create_task` 未追踪、`innerHTML` 注入风险。这些方向都对。

| 条目 | 判定 | 复核依据 |
|---|---|---|
| Telnet 明文（业务所限） | +2 | 成立 |
| 默认无鉴权 | +2 | 成立 |
| 任务无限累积 | +2 | 成立 |
| 异常捕获过宽 | +2 | 成立（`cli.py` 单 try 包住采集与判定） |
| `_tpl_cache` 无上限 | +2 | 成立 |
| SSE 用查询参数传 token | +2 | 成立 |
| `create_task` 未追踪 | +2 | 成立 |
| `innerHTML`/`onclick` 注入风险 | +2 | 成立（但称"IP 格式受限、风险低"是错误定性——`ip` 无任何校验） |
| "slim 镜像默认安全性较好、非 root" | **-1** | 事实错误：`Dockerfile` 无 `USER`，uvicorn 以 root 运行；qwen N28/minimax O5 均指出 |
| "历史审计问题已全部修复" | **-1** | 不成立（A05/A07/A11/A16 均有残留，且 K1–K4 仍在） |
| "回归测试完善、覆盖 18 个缺陷" | **-1** | 不成立（API 段必崩或恒 skip，从未真正验证过 A06/A10/A11/A12） |
| 总体"B+ / 通过" | **-1** | 结论性错误：当前 HEAD 的 Web 连启动都做不到 |
| 漏 K1–K6 | -6 | 六项全部未识别 |

**其他问题**：整篇是"架构+最佳实践"式审阅，缺少可复现证据与行级定位，把"可能的风险"和"确定的缺陷"混在一起；`esc()` 不转义引号、`ip` 无校验这类关键事实被轻描淡写为"风险较低"。不适合作为整改依据。

---

### 3.7 beam（4 分，5 条全部成立，但覆盖面过窄）

**做得最好的地方**：A19（K1）的根因解释完全正确，并明确指出"API 层用例因此从未执行"——这是对上一轮"52/52"最直接的反证；A20（cli 未挂 profile）、A21（审计文件被删导致 hygiene 红）、A22（TaskStore 返回内部引用）、A23（任务无 failed 终态）也都成立。

| 编号 | 判定 | 复核依据 |
|---|---|---|
| A19（K1） | +2 | 成立 |
| A20 | +2 | 成立 |
| A21 | +2 | 成立（`git status` 仍为 `D`，CI 第 99 行断言该文件） |
| A22 | +2 | 成立 |
| A23 | +2 | 成立 |
| 自称已 `git restore` 恢复审计文件 | **-1** | 当前工作区该文件仍是未提交删除状态，动作未生效或未落盘 |
| 漏 K2/K3/K4/K5/K6 | -5 | 只找到 K1；`as_completed`、两个必现 500、零规则全绿、VLAN1 误报全部未识别 |

**其他问题**：结论"引擎层 A01–A18 修复稳定"过于信任上一轮；"Web/API 完全不可用"的判断正确，但只归因于导入失败，没有继续追问"导入修好后任务仍会崩"。作为快速巡检合格，作为完整审计不足。

---

## 四、横向对比：谁发现了什么

| 关键缺陷 | minimax | qwen | hy4 | dsf | agnes | Glyph | beam |
|---|---|---|---|---|---|---|---|
| K1 `store.py` 遮蔽 / 无法导入 | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ | ✅ |
| K2 `as_completed` KeyError | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| K3 `POST /api/devices` 500 | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| K4 `/results` 序列化 500 | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ |
| K5 零规则全绿 | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| K6 VLAN1 误报/漏报 | ✅（部分） | ✅ | ❌ | ✅（部分） | ❌ | ❌ | ❌ |
| 覆盖率 | 5/6 | 4/6 | 2/6 | **6/6** | 0/6 | 0/6 | 1/6 |

### 各报告独有的高价值发现

- **qwen**：提示符正则 `<[A-Za-z_]+>` 拒绝数字/连字符（连随包样例的 `SW-01-SAMPLE` 都不匹配）；`snapshots/sample.cfg` 仍是空行分段；prune 的 `IndexError`；vendor 架构导致离线 Web 不可用；`NTP ... source` 误报。
- **dsf**：单文件 bind mount 上 `os.replace` → EBUSY（解释 A07 为何无效）；ACL 块切分越界；非 ASCII 令牌 500；上一轮"52/52"不可复现。
- **minimax**：CI 卫生正则与真实团体字形态不匹配；`PUT /api/profile` 的 prune 会连带删空变量并使后续任务 500；切页签清空结果。
- **hy4**：`${platform}` 文档与实现不符；快照文件权限；`rule_loader` 不校验 severity/platforms。
- **beam**：最早明确指出 API 用例从未执行。
- **agnes**：第一轮 A01/A02/A03/A14/A16 等引擎层深水区缺陷，确实修对了大部分。

---

## 五、分项得分明细

| 报告 | +2 条目 | -1 条目 | 漏基准 | 其他 | 合计 |
|---|---|---|---|---|---|
| minimax | 35 | 3（L-01/L-03/L-11） | K5 | — | 70-3-1 = **66** |
| qwen | 31 | 0 | K1/K4 | — | 62-0-2 = **60** |
| hy4 | 27 | 0 | K1/K2/K5/K6 | — | 54-4 = **50** |
| dsf | 22 | 0 | 0 | — | 44 = **44** |
| agnes | 17×2 + A07(+1) = 35 | 0（A07 已按 +1 计） | K1–K6 | -1（52/52） | 35-6-1 = **28** |
| GlyphCluster | 8 | 4（root/全部修复/回归完善/B+ 结论） | K1–K6 | — | 16-4-6 = **6** |
| beam | 5 | 1（git restore 未生效） | K2–K6 | — | 10-1-5 = **4** |

---

## 六、结论与使用建议

1. **作为整改依据**：优先采用 `dsf`（6/6 覆盖、零误报、有 HTTP 级证据）与 `minimax`（两个阻断项 + 安全面最全）。两者的交集（K1+K2+K3+K4）就是恢复 Web 可用的最小修复集。
2. **作为业务规则整改依据**：优先采用 `qwen` 的 N04/N05/N06/N07/N11/N15/N20/N24，它对 VRP 语义和"错误结论"的识别最贴近现场。
3. **作为 API 层设计参考**：`hy4` 的 N01/N02/N07 根因写得最准确，可直接照它的修法改。
4. **谨慎引用**：`agnes` 的"18 项全部修复、52/52 通过"、`GlyphCluster` 的"B+/通过/非 root/回归完善"都不能作为事实依据；`beam` 的 A19 可信，但其"引擎层稳定"的结论不代表 Web 可用。
5. **计分口径提醒**：本表严格按"每条 +2 / -1"计算，条目数量会显著影响总分。若改看"错误条目占比"，排序为 `dsf`(0/22) = `hy4`(0/27) = `qwen`(0/31) < `agnes`(1/18) < `minimax`(3/38) < `beam`(1/5) < `GlyphCluster`(4/12)。

---

## 七、复核局限

- 本机 Python 3.12.2 缺少 fastapi/pydantic，且 `vendor/pydantic_core` 为 aarch64 二进制，无法在本机启动 ASGI 应用；K3/K4/K5 以源码语义 + 最小复现确认，未做真实 HTTP 调用。
- 未在 Docker 中复现 EBUSY（A28/N21），该项按 mount namespace 语义判定为成立。
- 未做 Telnet 实机联调；`collector.py` 的登录状态机结论均为静态审阅。
- 未审计 `vendor/` 第三方代码。
