"""
core — 华为VRP基线检查工具核心引擎
================================================================
本包包含纯业务逻辑, 不依赖任何 Web 框架或 CLI 框架。
可被 cli.py (CLI入口) 或 api/ (FastAPI入口) 直接调用。

模块说明:
  models          数据结构(Rule/Section/Check/Finding) + 常量
  rule_loader     规则库YAML加载 + 字段校验 + 变量校验
  config_parser   VRP配置文本解析(分段/模板编译/选择器/前提条件)
  builtin_checks  内置检查函数(策略链追踪/VLAN1禁用) + BUILTINS注册表
  engine          规则执行引擎(run_rule/run_all)
  collector       设备配置采集(Telnet在线 + 离线快照)
  credentials     凭证安全(环境变量解析 + CSV加载)
  reporter        报告生成(结构化数据 + MD/XLSX文件输出)

典型调用流程:
  from core.rule_loader import load_rules, load_profile, validate_vars
  from core.credentials import load_devices
  from core.collector import fetch
  from core.engine import run_all
  from core.reporter import build_report_data, write_report_files

  rules = load_rules("rules.yaml")
  profile = load_profile("profile.yaml")
  validate_vars(rules, profile["variables"])
  devices = load_devices("devices.csv")
  for dev in devices:
      cfg = fetch(dev, offline=False)
      results[dev["ip"]] = run_all(rules, cfg, profile["variables"],
                                   platform=dev["platform"],
                                   device_type=dev["type"])
  report_data = build_report_data(results, devices, profile["variables"])
  write_report_files(report_data, output_dir)
================================================================
"""

from .models import Rule, Section, Check, Finding, VAR_RE, PHYS, SEV_ORDER
from .rule_loader import load_rules, load_profile, validate_vars
from .config_parser import split_blocks, compile_tpl, select_targets, pass_preconds
from .engine import run_rule, run_all
from .collector import fetch, fetch_telnet, fetch_offline
from .credentials import resolve_credentials, load_devices
from .reporter import build_report_data, write_report_files, render_fix, save_snapshots

__all__ = [
    # models
    "Rule", "Section", "Check", "Finding",
    "VAR_RE", "PHYS", "SEV_ORDER",
    # rule_loader
    "load_rules", "load_profile", "validate_vars",
    # config_parser
    "split_blocks", "compile_tpl", "select_targets", "pass_preconds",
    # engine
    "run_rule", "run_all",
    # collector
    "fetch", "fetch_telnet", "fetch_offline",
    # credentials
    "resolve_credentials", "load_devices",
    # reporter
    "build_report_data", "write_report_files", "render_fix", "save_snapshots",
]
