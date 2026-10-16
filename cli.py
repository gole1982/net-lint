#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli.py — 华为VRP基线检查工具 CLI 入口
================================================================
用法:
  ./run.sh                    # 全量检查(在线Telnet采集)
  ./run.sh --offline          # 离线模式(读 snapshots/ 快照)
  ./run.sh --selfcheck        # 环境自检(验证依赖完整性)

本文件是 CLI 交互层, 调用 core/ 中的纯业务逻辑。
Web 前端 (api/) 复用同一个 core/ 层。
================================================================
"""

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---- vendor 路径注入 (离线绿色版用vendor, Docker容器用pip安装的包) ----
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_vendor_dir = os.path.join(_BASE_DIR, "vendor")
if os.path.isdir(_vendor_dir) and _vendor_dir not in sys.path:
    sys.path.insert(0, _vendor_dir)

# ---- 日志配置 ----
logger = logging.getLogger("vrp_baseline")
logger.setLevel(logging.DEBUG)
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_console)


def selfcheck():
    """环境自检: 验证依赖完整性"""
    import yaml
    import openpyxl
    try:
        import telnetlib
        print(f"  telnetlib OK (Python {sys.version.split()[0]})")
    except ImportError:
        print(f"  telnetlib 不可用 (Python {sys.version.split()[0]}, 仅离线模式可用)")
    print(f"  yaml {yaml.__version__}")
    print(f"  openpyxl {openpyxl.__version__}")
    # Web依赖检测(可选)
    try:
        import fastapi
        import uvicorn
        import pydantic
        print(f"  fastapi {fastapi.__version__} (Web可用)")
        print(f"  uvicorn {uvicorn.__version__}")
        print(f"  pydantic {pydantic.__version__}")
    except ImportError:
        print("  fastapi/uvicorn/pydantic 不可用 (Web功能不可用, CLI正常)")
    print("环境自检完成")


def run(offline=False):
    """执行基线检查主流程"""
    from core.rule_loader import load_rules, load_profile, validate_vars
    from core.credentials import load_devices
    from core.collector import fetch
    from core.engine import run_all
    from core.reporter import build_report_data, write_report_files, save_snapshots

    base_dir = _BASE_DIR
    profile_path = os.path.join(base_dir, "profile.yaml")
    rules_path = os.path.join(base_dir, "rules.yaml")
    devices_path = os.path.join(base_dir, "devices.csv")
    snapshots_dir = os.path.join(base_dir, "snapshots")

    # 加载 profile
    profile = load_profile(profile_path)
    variables = profile.get("variables", {})
    switches = profile.get("rules", {})

    # 加载规则库
    rules = load_rules(rules_path)

    # 校验变量引用
    validate_vars(rules, variables)

    # 过滤启用规则
    active_rules = [r for r in rules if switches.get(r.rid, False)]
    logger.info(f"启用规则: {len(active_rules)} 条")

    # 加载设备清单
    devices = load_devices(devices_path)

    # 检查凭证完整性
    missing_creds = [d["ip"] for d in devices if not d.get("password")]
    if missing_creds and not offline:
        logger.warning(
            f"以下设备缺少密码(CSV为空且环境变量未设置): {missing_creds}"
        )
        logger.warning(
            "可设置环境变量 VRP_PASS_<IP> (点号替换为下划线), "
            "例如: export VRP_PASS_10_10_1_11=你的密码"
        )

    logger.info(f"设备总数: {len(devices)} 台, 并发采集开始")

    results = {}
    cfg_map = {}

    # 并发采集 + 检查
    workers = min(len(devices), 10) if devices else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_dev = {
            pool.submit(fetch, d, offline=offline, snapshots_dir=snapshots_dir): d
            for d in devices
        }
        for fut in as_completed(future_to_dev):
            dev = future_to_dev[fut]
            ip = dev["ip"]
            try:
                cfg = fut.result()
                cfg_map[ip] = cfg
                results[ip] = run_all(
                    active_rules, cfg, variables,
                    platform=dev.get("platform", ""),
                    device_type=dev.get("type", ""),
                )
                logger.info(f"采集完成: {ip} ({dev.get('name', '')})")
            except Exception as e:
                results[ip] = [("ERROR", (ip, str(e), []))]
                logger.error(f"采集失败: {ip} - {e}")

    # 生成报告数据
    report_data = build_report_data(results, devices, variables)

    # 写报告文件
    write_report_files(report_data, base_dir)

    # 快照保存(仅在线模式)
    if not offline:
        save_snapshots(cfg_map, devices, base_dir)


def main():
    parser = argparse.ArgumentParser(
        description="华为VRP基线检查工具 v2.2"
    )
    parser.add_argument(
        "--offline", action="store_true",
        help="离线模式(读 snapshots/ 快照)"
    )
    parser.add_argument(
        "--selfcheck", action="store_true",
        help="环境自检(验证依赖完整性)"
    )
    args = parser.parse_args()

    if args.selfcheck:
        selfcheck()
        sys.exit(0)

    run(offline=args.offline)


if __name__ == "__main__":
    main()
