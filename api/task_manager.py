"""
api.task_manager — 后台任务管理器
================================================================
异步执行基线检查任务, 实时更新任务状态
Telnet是同步阻塞操作, 使用 asyncio.to_thread / run_in_executor 包装
================================================================
"""

import asyncio
import logging
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .schemas import TaskStatus
from .store import task_store, device_store

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
# vendor 路径 (追加到末尾: 已安装的同名包优先, vendor 仅作离线版兜底)
_VENDOR_DIR = os.path.join(_PROJECT_ROOT, "vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.append(_VENDOR_DIR)

# 报告输出根目录(与 docker-compose 的 ./data/output:/app/output 挂载点一致)
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")

logger = logging.getLogger("vrp_baseline")

# 持有后台任务的强引用: asyncio 只保留弱引用, 不保存会被 GC 提前取消。
_background_tasks = set()


def _spawn(coro):
    """创建后台任务并持有强引用, 完成后自动释放。"""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def report_dir(task_id: str) -> str:
    """任务报告目录。

    按 task_id 隔离: 多个任务串行或并发运行时不会互相覆盖报告文件,
    否则下载历史任务的报告会拿到最新一次任务的内容。
    """
    return os.path.join(OUTPUT_DIR, "reports", task_id)


def _run_single_device(dev, rules, variables, offline, snapshots_dir):
    """同步: 采集单台设备配置 + 执行规则检查

    返回 (ip, dev, cfg, findings, stats)。stats 用于区分
    "执行了规则且全部合规" 与 "一条规则都没执行"。
    """
    from core.collector import fetch
    from core.engine import run_all_detailed
    from core.credentials import resolve_credentials

    ip = dev["ip"]
    dev = resolve_credentials(dev)

    cfg = fetch(dev, offline=offline, snapshots_dir=snapshots_dir)
    findings, stats = run_all_detailed(
        rules, cfg, variables,
        platform=dev.get("platform", ""),
        device_type=dev.get("type", ""),
    )
    return ip, dev, cfg, findings, stats


def _serialize_findings(findings, variables):
    """把 [(Rule, [Finding])] 转成可 JSON 序列化的 DTO。

    Check/Finding 使用 __slots__, 没有 __dict__; FastAPI 的 jsonable_encoder
    对它们的兜底路径 (dict() -> vars()) 都会失败并抛 500。因此必须在写入
    task_store 之前完成转换, /results 与 SSE 才能安全返回。
    """
    from core.reporter import render_fix

    rows = []
    for rule, items in findings:
        for f in items:
            fix = ""
            if f.fix_lines and any(l.strip() for l in f.fix_lines):
                fix = render_fix("\n".join(f.fix_lines), f.target, variables)
            rows.append({
                "rule_id": rule.rid,
                "title": rule.title,
                "severity": rule.severity,
                "target": f.target,
                "evidence": f.evidence,
                "fix": fix,
            })
    return rows


async def create_and_run_task(
    offline: bool = False,
    device_ips: Optional[list[str]] = None,
    rule_ids: Optional[list[str]] = None,
) -> str:
    """创建并异步执行基线检查任务
    返回 task_id
    """
    from core.rule_loader import load_rules, load_profile, validate_vars

    base_dir = _PROJECT_ROOT
    profile_path = os.path.join(base_dir, "profile.yaml")
    rules_path = os.path.join(base_dir, "rules.yaml")
    snapshots_dir = os.path.join(base_dir, "snapshots")

    # 加载 profile + 规则
    profile = load_profile(profile_path)
    variables = profile.get("variables", {})
    switches = profile.get("rules", {})
    all_rules = load_rules(rules_path)
    validate_vars(all_rules, variables)

    # 过滤启用规则
    active_rules = [r for r in all_rules if switches.get(r.rid, False)]

    # 如果指定了规则ID, 进一步过滤
    if rule_ids:
        rid_set = set(rule_ids)
        active_rules = [r for r in active_rules if r.rid in rid_set]

    # 获取设备列表
    devices = device_store.list_all()
    if device_ips:
        ip_set = set(device_ips)
        devices = [d for d in devices if d["ip"] in ip_set]

    if not devices:
        raise ValueError("没有可用设备")

    # 创建任务
    task_id = str(uuid.uuid4())[:8]
    task_store.create(task_id, total=len(devices))
    task_store.update(task_id, status=TaskStatus.running)

    # 异步执行 (保留强引用, 避免任务被 GC 取消)
    _spawn(
        _run_task(task_id, devices, active_rules, variables, offline, snapshots_dir, base_dir)
    )

    return task_id


async def _run_task(task_id, devices, rules, variables, offline, snapshots_dir, base_dir):
    """任务执行体

    任何未捕获异常都必须落到 failed 终态: 否则任务永远停在 running,
    前端轮询不到结果, 下载接口也只会返回"任务尚未完成"。
    """
    from core.engine import not_checked_reason as _not_checked_reason
    from core.reporter import build_report_data, write_report_files, save_snapshots

    completed = 0
    results = {}
    cfg_map = {}
    device_results = {}

    try:
        # 使用线程池并发采集
        workers = min(len(devices), 10) if devices else 1
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                loop.run_in_executor(
                    pool, _run_single_device,
                    dev, rules, variables, offline, snapshots_dir
                ): dev
                for dev in devices
            }

            # 必须用 async for: 普通 for 迭代 as_completed 得到的是
            # _wait_for_one() 协程, 既不是原 Future 也无法反查设备, 必然 KeyError。
            async for fut in asyncio.as_completed(futures):
                dev = futures[fut]
                ip = dev["ip"]
                try:
                    ret_ip, ret_dev, cfg, findings, stats = await fut
                    results[ret_ip] = findings
                    cfg_map[ret_ip] = cfg
                    status = "ok" if stats.get("evaluated") else "not_checked"
                    message = "" if status == "ok" else _not_checked_reason(stats)
                    device_results[ret_ip] = {
                        "status": status,
                        "message": message,
                        "checked_rules": stats.get("applied", 0),
                        "total_rules": stats.get("total", 0),
                    }
                    task_store.set_result(task_id, ret_ip, {
                        "ip": ret_ip,
                        "name": ret_dev.get("name", ""),
                        "status": status,
                        "error": "",
                        "message": message,
                        "checked_rules": stats.get("applied", 0),
                        "total_rules": stats.get("total", 0),
                        "findings": _serialize_findings(findings, variables),
                    })
                except Exception as e:
                    results[ip] = [("ERROR", (ip, str(e), []))]
                    device_results[ip] = {"status": "error", "message": str(e)}
                    task_store.set_result(task_id, ip, {
                        "ip": ip,
                        "name": dev.get("name", ""),
                        "status": "error",
                        "error": str(e),
                        "message": "",
                        "checked_rules": 0,
                        "total_rules": 0,
                        "findings": [],
                    })
                    logger.error(f"采集失败: {ip} - {e}")

                completed += 1
                task_store.update(
                    task_id,
                    completed=completed,
                    progress=completed / max(len(devices), 1),
                )

        # 生成报告数据
        report_data = build_report_data(
            results, devices, variables, device_results=device_results
        )

        # 写报告文件(按 task_id 隔离, 避免并发任务互相覆盖)
        write_report_files(report_data, report_dir(task_id))

        # 快照保存(仅在线模式)
        if not offline:
            save_snapshots(cfg_map, devices, base_dir)

        task_store.update(
            task_id, status=TaskStatus.done, progress=1.0, message="检查完成"
        )
    except Exception as e:
        logger.exception(f"任务执行失败: {task_id}")
        task_store.update(
            task_id,
            status=TaskStatus.failed,
            message=f"任务执行失败: {e}",
        )
        raise
