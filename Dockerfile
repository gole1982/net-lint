# ============================================================
# Dockerfile — 华为VRP基线检查工具
# ============================================================
#
# 基于 python:3.12-slim (自带 telnetlib, multi-arch 自动适配 aarch64)
#
# 构建:
#   docker build -t vrp-baseline:2.2 .
#
# 运行(Web模式):
#   docker run -d --name vrp-web \
#     -p 8080:8080 \
#     -v $(pwd)/data:/app/data \
#     vrp-baseline:2.2
#
# 运行(CLI模式):
#   docker run --rm \
#     -v $(pwd)/data/devices.csv:/app/devices.csv \
#     -v $(pwd)/data/profile.yaml:/app/profile.yaml \
#     -v $(pwd)/data/snapshots:/app/snapshots \
#     -v $(pwd)/data/output:/app/output \
#     vrp-baseline:2.2 cli --offline
#
# 架构: 自动匹配宿主机 (x86_64 / aarch64)
# ============================================================

FROM python:3.12-slim AS base

# 设置时区和语言
ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# 安装最小化系统依赖
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 先复制 requirements.txt 利用 Docker 层缓存
COPY requirements.txt /app/requirements.txt

# 安装 Python 依赖
# --no-cache-dir 减小镜像体积
# pip 会自动根据当前架构拉取对应 wheel (含 pydantic-core C 扩展)
RUN pip install --no-cache-dir -r /app/requirements.txt

# 复制项目代码 (.dockerignore 会排除 runtime/ / __pycache__ 等)
COPY . /app/

# 确保入口脚本可执行
RUN chmod +x /app/docker-entrypoint.sh

# 以非 root 运行: 容器逃逸/写文件的攻击面从 root 降为普通用户。
# uid 1000 与常见宿主首个用户一致, 便于 ./data 卷可写。
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/snapshots /app/output \
    && chown -R appuser:appuser /app

USER appuser

# 暴露 Web 端口
EXPOSE 8080

# 入口脚本: 默认启动 Web 服务
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["web"]
