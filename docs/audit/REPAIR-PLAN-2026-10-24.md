# 修复计划（2026-10-24，待确认）

> 依据：`docs/audit/AUDIT-REVIEW-2026-10-24.md` 中经复核实锤的条目。
> 原则：先恢复可用，再修"结论错误"，最后补健壮性与部署；每一阶段独立可验证、可回滚。
> 本文件只描述改动；P0–P4 已于 2026-10-25 按确认范围实施，执行与复核结果见文末「执行状态」。

## 阶段依赖

```
P0 恢复可用 ──► P4 端到端回归（没有 P0，e2e 跑不起来）
     │
     └──► P1 结论正确性 ──► P4 语义用例
                  │
                  └──► P2 健壮性/安全 ──► P3 部署与 CI
```

---

## P0 — 恢复 Web 可用（阻断项，必做）

| # | 位置 | 改动 | 验收 |
|---|---|---|---|
| P0-1 | `api/store.py:21` | 方法 `list()` 改名 `list_all()`，同步改 `api/task_manager.py`、`api/routes/devices.py` 调用点；与 `TaskStore.list_all` 命名一致 | `python -c "import api.app"` 不再抛 `TypeError: 'function' object is not subscriptable` |
| P0-2 | `api/task_manager.py:134` | `for pending in asyncio.as_completed(futures): fut = await pending; dev = futures[fut]`（3.12+ 的 `as_completed` 普通迭代产出的是 awaitable，不是原 Future） | 单设备任务能跑到 `done`，不再首台即 `KeyError` |
| P0-3 | `api/routes/devices.py:47` | 抽出 `_to_out(device: dict) -> DeviceOut`，先 `payload = dev.model_dump()` 再 `payload.update(password=..., has_password=...)`；四个端点统一走它 | `POST /api/devices` 返回 201/200，不再是 500 |
| P0-4 | `api/routes/tasks.py:40` | `/results` 返回前把 `(Rule, [Finding])` 转成纯 dict（`rule_id/title/severity/target/evidence/fix`）；在 `task_manager` 写 `task_store` 时就存 DTO，SSE 与下载共用 | 有不合规项时 `/results` 返回 200 且可 `json.dumps` |
| P0-5 | `api/task_manager.py:_run_task` | 整个执行体包 `try/except`：异常时 `status=TaskStatus.failed`、`message` 记原因、`progress` 保持；报告写盘失败也要落 `failed` | 人为让 `write_report_files` 抛错，任务终态为 `failed` 而不是永久 `running` |
| P0-6 | `api/task_manager.py:create_and_run_task` | 用模块级 `set` 持有 `asyncio.create_task` 的引用，done 回调里 `discard` | 任务不被 GC 提前取消（可用 `gc.collect()` 后仍完成验证） |

改动量：约 120 行，集中在 3 个文件。风险低，全部为局部修正。

---

## P1 — 修"结论错误"（误报/漏报，决定报告可信度）

| # | 位置 | 改动 |
|---|---|---|
| P1-1 | `core/engine.py:run_all` | 返回执行统计：`applied` / `skipped_by_platform` / `skipped_by_device_type`。零规则执行不再与"全部合规"同形。调用方 `cli.py`、`api/task_manager.py` 同步改 |
| P1-2 | `api/task_manager.py` + `core/reporter.py:77` | 设备结果增加 `status="not_checked"` 与原因；`build_report_data` 不再 `continue` 丢弃，摘要增加 `total_unchecked`，Markdown/Excel 明确写"未执行任何规则，不代表合规" |
| P1-3 | `core/builtin_checks.py:vlan1_check` | 重写判定：按接口类型取有效 VLAN 集合 —— access 看 `port default vlan`；trunk 解析 `allow-pass` 含 `undo`/`all`/`X to Y`；hybrid 解析 tagged/untagged；Vlanif 与子接口跳过；Eth-Trunk 聚合成员口 |
| P1-4 | `core/reporter.py:render_fix` | 按 `target` 拆分后逐接口渲染（现在是 `A; B; C` 拼成一条 `interface` 行）；VLAN1 按接口类型给不同命令（trunk/access/hybrid），不再统一下发 `port trunk allow-pass vlan 2 to 4094` |
| P1-5 | `core/config_parser.py` / 策略链检查 | Eth-Trunk 应用策略时展开成员口；目标集合为空时返回 WARN/未检查而非 PASS；同一 classifier 的所有 `if-match acl` 都要收集；ACL 匹配支持命名 ACL |
| P1-6 | `rules.yaml:SW-NTP-01` | 允许 `ntp-service unicast-server X source Y`（可选 `source` 段） |
| P1-7 | `core/collector.py` 提示符正则 | `<[A-Za-z_]+>` → 允许数字与连字符（`<[\w.-]+>`），随包样例 `SW-01-SAMPLE` 才匹配得上 |
| P1-8 | `snapshots/sample.cfg` | 按 `#` 分段重写（当前空行分段 → 5 段 / 0 个 interface 段），并让 README 的离线步骤能真正加载它 |
| P1-9 | `rules.yaml` | `SW-IPV6-01`/`SW-DHCP-02` 的 fix 补 `system-view`；VLAN1 fix 拆分到 P1-4 的类型化生成 |

改动量：约 350 行。风险中等：会改变报告输出与规则库内容，需你确认是否允许改 `rules.yaml` 与样例快照。

---

## P2 — API 健壮性与安全

1. CSV 导入：表头中英文兼容、`platform` 白名单、`ipaddress` 校验、去空白、编码回退（UTF-8→GBK），坏输入返回 400 + 行号，不再裸 500。
2. `profile.py` 的 `_update_section` 重写：按段定位插入（新增键不再落进 `rules:`）、末尾段不再 `IndexError`、写后回读校验、失败不留半截文件。
3. `ip` 入参校验 + 快照路径约束（`<IP>.cfg`、禁止 `..`/分隔符），下载路径限制在 `output/reports/<task_id>/` 内。
4. 令牌：`hmac.compare_digest` 改比较 bytes（非 ASCII 不再 500）；`/docs`、`/openapi.json` 纳入守卫；查询参数令牌只对 SSE 生效。
5. SSE：加 `Cache-Control: no-cache`、`X-Accel-Buffering: no`、总超时与断开检测。
6. XSS：`esc()` 转义引号；`onclick="editDevice('...')"` 改事件委托；结果表用 `textContent`。三处。
7. 统一异常出口：500 只回通用文案，细节进日志；404/409 语义对齐。
8. `_tpl_cache` 加上限；任务/结果加 TTL 或条数上限。
9. 设备持久化：默认改为 CRUD 后原子写回 `devices.csv`（保留"启动从 CSV 加载"语义）；若你更希望纯内存，我改为 UI 明示"重启丢失"。

---

## P3 — 部署、供应链与 CI

1. `profile.yaml` 写盘：`os.replace` 在单文件 bind mount 上必 EBUSY → 改为同目录原地截断写（`open('w')`），并加锁；compose 挂载方式不变。
2. `Dockerfile`：新增非 root `USER`，`/app/output` 与 `/app/data` chown；确认 `docker compose up` 后报告仍可写。
3. `vendor/`：README 与启动自检标注"仅 aarch64"，x86_64 走 `pip install -r requirements.txt`；自检在架构不匹配时给出明确提示。
4. CI：
   - `hygiene` 的文件断言从固定的 `docs/AUDIT-2026-10-21.md` 改为 `docs/audit/*.md`（该文件当前是未提交删除，我不擅自恢复；若要保留历史记录，你确认后我从 HEAD 取回）。
   - 明文口令正则覆盖 `Ro@`/`Rw@` 形态（现模式只匹配 `Huawei@123`）。
   - `test` job 增加 `pytest`（保留现有 grep 断言作辅助）。
5. `README`：更新离线/在线步骤、`profile.yaml` 热更新说明、`devices.csv` 与 API 权限边界。

---

## P4 — 测试（与 P0/P1 同步落地）

1. `tests/test_engine_semantics.py`：VLAN1 矩阵（access 无命令 / `port default vlan 10` / trunk 放行 1 / trunk `undo` 1 / hybrid tagged / hybrid untagged / 子接口 / Eth-Trunk 成员）、零规则统计、NTP `source`、多 `if-match`、`render_fix` 逐接口输出。
2. `tests/test_api_e2e.py`：`TestClient` 跑 导入 CSV → 建设备 → 建 offline 任务 → 轮询到 `done` → `/results` 可序列化 → 下载 md/xlsx；另测失败路径落 `failed`、无令牌 401、坏 CSV 400、坏 IP 400。
3. 现有 `test_regression.py` 的 grep 断言保留，但不再作为"功能可用"的证据；CI 以 pytest 为准。

运行环境：本机缺 fastapi、`vendor/pydantic_core` 是 aarch64，需先建 venv 并 `pip install -r requirements.txt`（vendor 在 `sys.path` 末尾，不会干扰 x86_64 wheel）。

---

## 明确不做（本轮范围外）

- 不重构架构、不改内存存储为 SQLite（可作为后续独立任务）。
- 不默认开启鉴权（属部署策略，需单独决策）。
- 不引入凭据加密/外部 Vault。
- 不做 Telnet→SSH 迁移。

## 验证口径

每个阶段结束跑：`python -m compileall`、`python cli.py --offline`、`python -m pytest -q`；P0/P2 另跑 `TestClient` 端到端。任一阶段失败即回退该阶段改动，不进入下一阶段。

---

## 执行状态（2026-10-25 复核）

P0–P4 全部落地，未改动架构与存储形态。复核依据为恢复后的 7 份审计报告与 `AUDIT-REVIEW-2026-10-24.md` 的 K1–K6 基准。

| 阶段 | 状态 | 对应位置 |
|---|---|---|
| P0-1 `list` 遮蔽 | ✅ 改名 `list_all()`，调用点同步 | `api/store.py`、`api/task_manager.py`、`api/routes/devices.py` |
| P0-2 `as_completed` | ✅ 改 `async for` + `futures[fut]` 反查 | `api/task_manager.py` |
| P0-3 `DeviceOut` 重复关键字 | ✅ 统一经 `_to_out()`（payload 后覆盖口令字段） | `api/routes/devices.py` |
| P0-4 `/results` 序列化 | ✅ 入库前转纯 dict DTO，SSE/下载复用 | `api/task_manager.py` |
| P0-5 失败终态 | ✅ 执行体 try/except，异常落 `failed` | `api/task_manager.py` |
| P0-6 后台任务引用 | ✅ `_spawn()` 持有强引用 | `api/task_manager.py` |
| P1-1 执行统计 | ✅ `run_all_detailed()` 返回 total/applied/evaluated/skipped_*/no_target | `core/engine.py`、`cli.py` |
| P1-2 未检查状态 | ✅ `status=not_checked` + 原因；报告摘要 `total_unchecked` | `api/task_manager.py`、`core/reporter.py` |
| P1-3 VLAN1 语义 | ✅ 按 access/trunk/hybrid/子接口/Eth-Trunk/undo 分支 | `core/builtin_checks.py` |
| P1-4 整改命令 | ✅ 逐接口渲染 + 按链路类型生成命令 | `core/reporter.py`、`core/builtin_checks.py` |
| P1-5 策略链 | ✅ 多 `if-match`、命名 ACL、Eth-Trunk 成员、空端口 WARN | `core/builtin_checks.py` |
| P1-6 NTP `source` | ✅ 正则允许可选 `source` 段 | `rules.yaml` |
| P1-7 提示符正则 | ✅ `<[\w.-]+>` | `core/collector.py` |
| P1-8 样例快照 | ✅ 按 `#` 分段重写（22 段 / 7 个 interface） | `snapshots/sample.cfg` |
| P1-9 `system-view` | ✅ 补到 IPv6/DHCP 规则的 fix | `rules.yaml` |
| P2-1 CSV 校验 | ✅ IP/平台/协议白名单、缺列 400、UTF-8→GBK | `api/routes/devices.py` |
| P2-2 profile 段更新 | ✅ 按段定位插入、末尾段安全、写后回读校验 | `api/routes/profile.py` |
| P2-3 路径约束 | ✅ `ipaddress` 校验；task_id 白名单 + `commonpath` | `api/schemas.py`、`api/routes/tasks.py` |
| P2-4 令牌 | ✅ bytes 比较、`/docs` 纳入守卫、查询令牌仅 SSE | `api/app.py` |
| P2-5 SSE | ✅ `no-cache`/`X-Accel-Buffering`、断开检测、600s 上限 | `api/app.py` |
| P2-6 XSS | ✅ `esc()` 转义引号 + 事件委派 + 下载走 Blob | `web/app.js` |
| P2-7 异常出口 | ✅ 500 只回通用文案，细节进日志 | `api/app.py`、`api/routes/tasks.py` |
| P2-8 缓存上限 | ✅ `_TPL_CACHE_MAX=2048`；任务历史上限 200 | `core/config_parser.py`、`api/store.py` |
| P2-9 设备持久化 | ✅ 原子写回 `devices.csv`（只读卷降级为内存） | `api/store.py`、`README.txt` |
| P3-1 profile 写盘 | ✅ 临时文件 + `os.replace`，EBUSY/EXDEV 退化为原地截断写 | `api/routes/profile.py` |
| P3-2 非 root | ✅ uid 1000 + chown，compose 目录放宽 | `Dockerfile`、`.github/workflows/ci.yml` |
| P3-3 vendor 提示 | ✅ `vendor_note`（架构不匹配时给出说明） | `api/routes/system.py`、`api/schemas.py`、`README.txt` |
| P3-4 CI | ✅ pytest 阶段、口令正则、报告断言 | `.github/workflows/ci.yml` |
| P4 用例 | ✅ 22 + 12 例新增，回归扩至 53 项 | `tests/` |

### 复核实测

- K1：`import api.app` 正常；K2：任务能跑到 `done`；K3：`POST /api/devices` 返回 200；K4：有 findings 的 `/results` 200 且 `json.dumps` 成功。
- K5：`platform="S-Series"`、`device_type="Switch"` → `applied=0`，设备状态 `not_checked`，报告摘要 `total_unchecked=1`，不再输出“合规”。
- K6：access 默认 VLAN10 → PASS；trunk 裁剪 VLAN1（含 `undo`）→ PASS；三层子接口不参与检查；Eth-Trunk `hybrid untagged vlan 1` → FAIL 且定位到 Eth-Trunk1；策略应用在 Eth-Trunk 上 → 成员口不再误报；无在用端口 → WARN 而非 PASS；同一 classifier 两条 `if-match acl` → PASS；`render_fix` 多目标逐接口输出；`split_blocks(sample.cfg)` = 22 段 / 7 个 interface。
- 全量：`tests/test_regression.py` 53/53；`pytest -q tests` 34 passed；`cli.py --offline` 正常出报告；HTTP 冒烟（任务/结果/下载/静态资源）全部 200。

### 与计划的差异

- P1-1 统计键名最终为 `total/applied/evaluated/skipped_platform/skipped_device_type/no_target`（计划写作 `skipped_by_*`），语义一致。
- P3-1 采用“临时文件 + `os.replace`，失败再原地写”的组合，而非一律原地写：正常文件系统保留原子性，bind mount 上仍可用。
- 平台/设备类型仍为精确匹配；大小写不一致会落到 `not_checked` 而不是猜测归一化——宁可显式“未检查”，不做隐式放行。

