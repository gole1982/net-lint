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
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .schemas import TaskStatus
from .store import task_store, device_store

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
# vendor 路径
_VENDOR_DIR = os.path.join(_PROJECT_ROOT, "vendor")
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

logger = logging.getLogger("vrp_baseline")


def _run_single_device(dev, rules, variables, offline, snapshots_dir, ip_list):
    """同步: 采集单台设备配置 + 执行规则检查"""
    from core.collector import fetch
    from core.engine import run_all
    from core.credentials import resolve_credentials

    ip = dev["ip"]
    dev = resolve_credentials(dev)

    cfg = fetch(dev, offline=offline, snapshots_dir=snapshots_dir)
    findings = run_all(
        rules, cfg, variables,
        platform=dev.get("platform", ""),
        device_type=dev.get("type", ""),
    )
    return ip, dev, cfg, findings


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
    devices = device_store.list()
    if device_ips:
        ip_set = set(device_ips)
        devices = [d for d in devices if d["ip"] in ip_set]

    if not devices:
        raise ValueError("没有可用设备")

    # 创建任务
    task_id = str(uuid.uuid4())[:8]
    task_store.create(task_id, total=len(devices))
    task_store.update(task_id, status=TaskStatus.running)

    # 异步执行
    asyncio.create_task(
        _run_task(task_id, devices, active_rules, variables, offline, snapshots_dir, base_dir)
    )

    return task_id


async def _run_task(task_id, devices, rules, variables, offline, snapshots_dir, base_dir):
    """任务执行体"""
    from core.reporter import build_report_data, write_report_files, save_snapshots

    completed = 0
    results = {}
    cfg_map = {}

    loop = asyncio.get_event_loop()

    # 使用线程池并发采集
    workers = min(len(devices), 10) if devices else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        loop = asyncio.get_event_loop()
        futures = {
            loop.run_in_executor(
                pool, _run_single_device,
                dev, rules, variables, offline, snapshots_dir, None
            ): dev
            for dev in devices
        }

        for fut in asyncio.as_completed(futures):
            dev = futures[fut]
            ip = dev["ip"]
            try:
                ret_ip, ret_dev, cfg, findings = await fut
                results[ret_ip] = findings
                cfg_map[ret_ip] = cfg
                task_store.set_result(task_id, ret_ip, {
                    "ip": ret_ip,
                    "name": ret_dev.get("name", ""),
                    "status": "ok",
                    "error": "",
                    "findings": findings,
                })
            except Exception as e:
                results[ip] = [("ERROR", (ip, str(e), []))]
                task_store.set_result(task_id, ip, {
                    "ip": ip,
                    "name": dev.get("name", ""),
                    "status": "error",
                    "error": str(e),
                    "findings": [],
                })
                logger.error(f"采集失败: {ip} - {e}")

            completed += 1
            task_store.update(
                task_id,
                completed=completed,
                progress=completed / len(devices),
            )

    # 生成报告数据
    report_data = build_report_data(results, devices, variables)

    # 写报告文件
    write_report_files(report_data, base_dir)

    # 快照保存(仅在线模式)
    if not offline:
        save_snapshots(cfg_map, devices, base_dir)

    task_store.update(task_id, status=TaskStatus.done, progress=1.0, message="检查完成")
