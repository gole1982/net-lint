# VRP 基线检查工具 — 仓库审计报告（2026-10-22）

> 审计对象：commit `514c4ef`（main），相对 `docs/AUDIT-2026-10-21.md` 的 A01–A18 修复之后的状态。
> 审计范围：`core/`、`api/`、`cli.py`、`vrp_baseline.py`、`web/`、部署配置（排除 `vendor/`、`runtime/`）。
> 审计方法：静态审阅 + 本地复现（Python 3.13.15 + requirements 锁定版本）+ 回归测试执行。
> 约束：本次审计**未修改任何代码**，仅输出报告与恢复缺失的审计链路文件。

## 一、执行摘要

| 项目 | 结果 |
|---|---|
| 一方代码规模 | 约 2010+ 行 Python（21 个模块）+ 前端 |
| 回归测试（core 层） | **31 / 31 通过** |
| 回归测试（API 层） | **无法执行**（导入阶段崩溃，见 A19） |
| 离线自检 `cli.py --offline` | **通过**（2 条不合规 + 1 条建议，与样例声明一致） |
| 语法检查 `compileall` | 全部通过 |
| 新发现缺陷 | **3 项**（阻断 1 / 高 1 / 中 1），另含 2 项低风险/运维项 |
| 结论 | **Web/API 入口当前完全不可用**（A19），需优先修复；其余为部署与工程卫生问题 |

## 二、验证记录

```
core 层:  python tests/test_regression.py --core   → 31/31 通过
API 层:   python tests/test_regression.py --api     → 导入 api.app 失败 (A19)
离线自检: python cli.py --offline                → 设备 1 台, 不合规 2, 建议 1  (与样例声明一致)
语法检查: python -m compileall -q core api ...   → 全部通过
```

## 三、缺陷详述

### 🔴 A19 — `DeviceStore.list` 方法遮蔽内置 `list`，导致 API/Web 入口完全无法启动

- **严重度**：🔴 严重（阻断）
- **模块**：`api/store.py`（`DeviceStore` 类）
- **现象**：`uvicorn api.app:app`、`python tests/test_regression.py --api` 在导入 `api.store` 时抛 `TypeError: 'function' object is not subscriptable`，Web 服务无法启动，全部 API 测试无法运行。
- **根因**：`DeviceStore` 中先定义了方法 `def list(self) -> list[dict]`，该方法名 `list` 进入类命名空间后遮蔽了内置 `list`。后续方法 `load_from_csv` / `to_csv_rows` 的注解 `list[dict]` 在定义时在类命名空间查找 `list`，得到的是前面的方法对象，而方法对象不可下标，从而抛异常。该行为在 Python 3.9+ 均成立（注解在定义期求值）。
- **影响**：API 层全部不可用；CI 的 `test` job（3.12/3.13）在 API 测试阶段失败；Web 容器无法通过健康检查。
- **建议修复**：将 `DeviceStore.list` 重命名为非遮蔽名称（如 `all()` 或 `list_all()`），并同步更新调用方 `api/routes/devices.py`（`list_devices`）与 `api/task_manager.py`（`_run_task` 中的 `device_store.list()`）；或为注解改用 `typing.List` 并保留方法名。推荐前者，避免未来继续遮蔽内置。
- **验证**：修复后 `python tests/test_regression.py --api` 应全部通过；`uvicorn api.app:app` 可正常启动。

### 🟠 A20 — Docker Compose 的 CLI 服务未挂载 `profile.yaml`

- **严重度**：🟠 高（部署一致性）
- **模块**：`docker-compose.yml`（`cli` 服务）
- **现象**：`cli` 服务挂载了 `devices.csv`、`rules.yaml`、`snapshots/`、`output/`，但**未挂载 `profile.yaml`**。容器内 CLI 使用镜像内置的默认 `profile.yaml`，用户在 `./data/profile.yaml` 中的规则开关与变量编辑全部不生效。
- **影响**：Docker CLI 模式下检查结果与 Web 端/用户配置不一致；规则开关、NTP/SNMP 等变量无法按用户配置生效。
- **建议修复**：在 `cli` 服务 volumes 中增加 `- ./data/profile.yaml:/app/profile.yaml:ro`（CLI 仅读取，只读即可）。
- **验证**：`docker compose run --rm cli --offline` 输出应与 Web 端相同配置下的结果一致。

### 🟡 A21 — `docs/AUDIT-2026-10-21.md` 被删除，CI 卫生检查与审计链路断裂

- **严重度**：🟡 中（工程卫生 / CI）
- **模块**：`docs/`、`.github/workflows/ci.yml`（hygiene job）
- **现象**：工作区中 `docs/AUDIT-2026-10-21.md` 显示为 `deleted`（未暂存）。CI 卫生检查步骤明确要求 `test -f docs/AUDIT-2026-10-21.md`，当前状态将导致该 job 失败；同时 A01–A18 的审计证据链不完整。
- **影响**：CI `hygiene` job 失败；审计报告缺失。
- **建议修复**：从 git 历史恢复该文件（`git restore docs/AUDIT-2026-10-21.md`），或以本报告为基准重新生成并提交；若有意升级审计轮次，应同步更新 CI 中的文件名断言。
- **验证**：`git status` 不再显示该文件为 deleted；CI hygiene job 通过。
- **处置**：本次审计已执行 `git restore docs/AUDIT-2026-10-21.md` 恢复文件。

### 🟢 A22 — `TaskStore` 返回内部可变对象引用，存在并发读写风险

- **严重度**：🟢 低
- **模块**：`api/store.py`（`TaskStore.get` / `TaskStore.list_all`）
- **现象**：`DeviceStore.get` 返回 `dict(d)` 副本，但 `TaskStore.get` 直接返回 `self._tasks.get(task_id)` 内部引用，`list_all` 返回内部 dict 的引用列表。SSE 端点与任务线程并发读取/更新时可能读到半更新状态。
- **建议修复**：`get` 返回 `dict(t)` 副本（None 时 None），`list_all` 返回 `[dict(t) for t in ...]`。
- **验证**：并发压测下 SSE 不出现 `KeyError`/半字段。

### 🟢 A23 — 任务异常分支未落状态，任务可能永远停留在 `running`

- **严重度**：🟢 低
- **模块**：`api/task_manager.py`（`_run_task`）
- **现象**：`_run_task` 的 `try/except` 仅在单设备采集失败时记录错误，但若报告生成、快照保存等后续步骤抛异常，外层无捕获，任务状态不会被置为 `failed`，前端/SSE 会一直看到 `running`。
- **建议修复**：用 `try/except/finally` 包裹任务体，异常时 `task_store.update(task_id, status=TaskStatus.failed, message=str(e))`。
- **验证**：构造失败场景（如 profile.yaml 缺失），任务状态最终为 `failed`。

## 四、其他观察（不作为缺陷编号）

1. **部署一致性**：`api/app.py` 使用已弃用的 `@app.on_event("startup")`，在 FastAPI 0.143 / Starlette 1.8 下仍可用，但建议迁移到 `lifespan` 上下文管理器以避免未来版本移除。
2. **编码假设**：`core/collector.py` 以 UTF-8 解码设备输出；若设备终端编码为 GBK，配置文本可能乱码。属于环境/设备差异，建议在采集层按设备或自动探测编码。
3. **持久化**：Web 端设备 CRUD 与导入仅存内存，重启后丢失（`devices.csv` 不回写）。与设计一致但需在文档中明确。
4. **打包卫生**：未跟踪的 `.workbuddy/` / `.freebuff/` 目录存在，`pack.sh` 的排除列表未包含，打包时可能把本地辅助文件带入分发包；建议加入排除。

## 五、总体结论

- **引擎层（core）**：A01–A18 修复稳定，31 条回归用例全部通过，离线自检与样例声明一致。
- **API/Web 层**：因 A19，当前完全无法启动，是本次审计的首要阻断项。
- **部署层**：Docker CLI 模式存在 profile.yaml 未挂载（A20），需修正以保证配置一致性。
- **工程卫生**：审计报告文件缺失（A21）导致 CI 卫生检查失败，已恢复；本报告新增为 `docs/AUDIT-2026-10-22.md`。

建议发布节奏：先修复 A19（阻断），再处理 A20/A21，随后发布 v2.3；A22/A23 可并入后续小版本。
