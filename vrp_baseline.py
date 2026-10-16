#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vrp_baseline.py — 向后兼容入口
================================================================
本文件是向后兼容的薄 wrapper, 透传给 cli.py。
老用户 ./run.sh 或 python3 vrp_baseline.py 的使用方式不受影响。

v2.2 起核心逻辑已拆分至 core/ 目录:
  core/models.py          数据结构
  core/rule_loader.py     规则库加载与校验
  core/config_parser.py   VRP配置文本解析
  core/builtin_checks.py  内置检查(策略链/VLAN1)
  core/engine.py          规则执行引擎
  core/collector.py       Telnet采集 + 离线快照
  core/credentials.py    凭证安全
  core/reporter.py        报告生成

CLI入口: cli.py (argparse)
Web入口: api/ (FastAPI)
================================================================
"""

import os
import sys

# 透传所有参数到 cli.py
sys.argv[0] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli.py")
exec(open(sys.argv[0]).read())
