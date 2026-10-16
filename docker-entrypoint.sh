#!/bin/bash
# ============================================================
# docker-entrypoint.sh — 容器入口脚本
# ============================================================
#
# 根据第一个参数决定运行模式:
#   web       → 启动 FastAPI Web 服务 (默认端口 8080)
#   cli       → 执行 CLI 检查 (--offline / --selfcheck / 无参数=在线)
#   *         → 直接执行该命令 (灵活扩展)
#
# 默认(无参数): 启动 Web 服务
#
# ============================================================

set -e

# 切换到工作目录 (数据卷挂载点)
cd /app

# 确保输出目录存在
mkdir -p /app/snapshots /app/output

case "${1:-web}" in
  web)
    echo "启动 Web 服务: http://0.0.0.0:${WEB_PORT:-8080}"
    exec uvicorn api.app:app \
      --host 0.0.0.0 \
      --port "${WEB_PORT:-8080}" \
      ${UVICORN_EXTRA:-}
    ;;

  cli)
    shift
    echo "执行 CLI 检查: $*"
    exec python cli.py "$@"
    ;;

  *)
    # 直接执行传入的命令
    exec "$@"
    ;;
esac
