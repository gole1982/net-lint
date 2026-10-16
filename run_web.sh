#!/bin/bash
# =============================================================================
# 华为VRP基线检查工具 Web服务启动脚本
# =============================================================================
#
# 用法:
#   ./run_web.sh                启动Web服务(默认端口8080)
#   ./run_web.sh --port 9000    指定端口
#
# 启动后访问:
#   http://localhost:8080        前端页面
#   http://localhost:8080/docs   API文档(Swagger UI)
#
# 依赖(FastAPI/uvicorn/pydantic)已内置在vendor/目录, 已适配aarch64 Linux
# 运行时使用 runtime/bin/python3.12 (独立Python 3.12, 无需系统Python)
#
# =============================================================================

cd "$(dirname "$0")"

# 设置 PYTHONHOME 让 runtime 找到标准库
export PYTHONHOME="$(pwd)/runtime"
export PYTHONPATH="$(pwd)/vendor:$(pwd)"

exec "$(pwd)/runtime/bin/python3.12" -m uvicorn api.app:app --host 0.0.0.0 --port 8080 "$@"
