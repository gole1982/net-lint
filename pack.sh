#!/bin/bash
# =============================================================================
# pack.sh — 华为VRP基线检查工具 一键打包脚本
# =============================================================================
#
# 用法:
#   ./pack.sh                 打包为 tar.gz (含Python runtime, 适合离线部署)
#   ./pack.sh --no-runtime    不含Python runtime (目标机器自带Python 3.12)
#
# 打包内容:
#   cli.py / vrp_baseline.py  CLI入口 + 向后兼容wrapper
#   core/                     核心引擎(7个模块)
#   api/                      FastAPI接口层
#   web/                      前端静态资源
#   rules.yaml / profile.yaml 规则库 + 用户配置
#   devices.csv.example      设备清单模板(不含口令, 部署后复制为 devices.csv)
#   snapshots/                配置快照(含sample.cfg)
#   docs/                     文档(含审计报告)
#   tests/                    回归测试(python tests/test_regression.py)
#   vendor/                   纯Python依赖 + aarch64 C扩展
#   runtime/                  独立Python 3.12 aarch64 Linux运行时
#   run.sh / run_web.sh       启动脚本
#   README.txt                用户文档
#
# 不打包:
#   __pycache__/  Python缓存
#   .git/        Git仓库
#   *.md/*.xlsx  运行时生成的报告文件
#
# =============================================================================

set -e

cd "$(dirname "$0")"
BASE_DIR="$(pwd)"
NAME="vrp-baseline"
VERSION="2.2"
DATE=$(date +%Y%m%d)
ARCHIVE="${NAME}-v${VERSION}-${DATE}.tar.gz"

# 清理缓存
echo "清理缓存..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true
find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true

# 清理运行时生成文件
rm -f remediation_report.md baseline_report.xlsx 2>/dev/null || true
rm -rf output 2>/dev/null || true

# 构建排除列表
EXCLUDES=(
  --exclude=".git"
  --exclude=".gitignore"
  --exclude="__pycache__"
  --exclude="*.pyc"
  --exclude=".pytest_cache"
  --exclude="remediation_report.md"
  --exclude="baseline_report.xlsx"
  --exclude="output"
  --exclude="pack.sh"
  --exclude="*.tar.gz"
  --exclude="vendor_web"
  # devices.csv 可能含明文口令, 不进包; 只发模板
  --exclude="devices.csv"
)

if [ "$1" = "--no-runtime" ]; then
  EXCLUDES+=(--exclude="runtime")
  echo "打包模式: 不含runtime"
else
  echo "打包模式: 含runtime (aarch64 Linux Python 3.12)"
fi

# 打包
echo "打包中: $ARCHIVE"
tar czf "$ARCHIVE" \
  "${EXCLUDES[@]}" \
  -C "$(dirname "$BASE_DIR")" \
  "$(basename "$BASE_DIR")"

# 显示包大小
SIZE=$(du -sh "$ARCHIVE" | cut -f1)
echo ""
echo "====================================="
echo "打包完成!"
echo "  文件: $ARCHIVE"
echo "  大小: $SIZE"
echo "====================================="
echo ""
echo "部署步骤:"
echo "  1. 将 $ARCHIVE 拷贝到目标ARM64 Linux机器"
echo "  2. tar xzf $ARCHIVE"
echo "  3. cd $(basename "$BASE_DIR")"
echo "  4. chmod +x run.sh run_web.sh runtime/bin/python3.12"
if [ "$1" = "--no-runtime" ]; then
  echo "  5. python3 cli.py --selfcheck    # 环境自检(系统Python >= 3.7)"
  echo "  6. python3 cli.py --offline       # 离线检查"
  echo "  7. ./run.sh                       # 在线检查"
  echo "  8. ./run_web.sh                   # 启动Web服务"
else
  echo "  5. ./run.sh --selfcheck           # 环境自检"
  echo "  6. ./run.sh --offline              # 离线检查"
  echo "  7. ./run.sh                        # 在线检查(Telnet)"
  echo "  8. ./run_web.sh                    # 启动Web服务(端口8080)"
fi
echo ""
