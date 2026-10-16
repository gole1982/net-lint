#!/bin/bash
# =============================================================================
# 华为VRP基线检查工具 启动脚本
# =============================================================================
#
# 用法:
#   ./run.sh               全量在线检查(Telnet登录所有设备)
#   ./run.sh --offline     离线模式(读取 snapshots/ 目录下已有快照)
#   ./run.sh --selfcheck   环境自检(验证Python运行时和依赖包完整性)
#
# 启动Web前端:
#   ./run_web.sh           启动FastAPI服务(端口8080)
#
# 工作目录:
#   脚本自动切换到自身所在目录, 确保相对路径(vendor/snapshots等)正确解析
#
# 运行时:
#   使用 runtime/bin/python3 (独立Python 3.12 aarch64 Linux, 已内置telnetlib)
#   vendor/ 目录包含 fastapi/uvicorn/pydantic 等依赖(已适配aarch64)
#   若 runtime/ 目录不存在, 可改为系统 python3 运行(需自行安装依赖)
#
# 输出:
#   remediation_report.md  整改文档(Markdown格式)
#   baseline_report.xlsx   Excel汇总表(含色标)
#   snapshots/<IP>.cfg    在线模式自动保存的设备配置快照
# =============================================================================

cd "$(dirname "$0")"

# 设置 PYTHONHOME 让 runtime 找到标准库
export PYTHONHOME="$(pwd)/runtime"
export PYTHONPATH="$(pwd)/vendor:$(pwd)"

exec "$(pwd)/runtime/bin/python3.12" cli.py "$@"
