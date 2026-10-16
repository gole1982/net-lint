"""
api.app — FastAPI 应用入口
================================================================
启动: uvicorn api.app:app --host 0.0.0.0 --port 8080 --reload

路由:
  /api/devices       设备管理 CRUD
  /api/profile       规则开关与变量管理
  /api/rules         规则库定义(只读)
  /api/tasks         检查任务管理
  /api/system        系统管理
  /api/sse/{task_id} 任务进度实时推送(SSE)
  /                  前端静态页面
================================================================
"""

import logging
import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 路径注入 (离线绿色版用vendor, Docker容器用pip安装的包)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
_VENDOR_DIR = os.path.join(_PROJECT_ROOT, "vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

# 日志配置
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vrp_baseline")

# 创建 FastAPI 应用
app = FastAPI(
    title="华为VRP基线检查工具",
    description="""
## API 文档

本工具用于对华为S系列/CE系列园区交换机进行基线配置合规检查。

### 核心功能:
- **设备管理**: CRUD 设备清单
- **规则管理**: 查看规则库 / 开关规则 / 编辑变量
- **检查任务**: 启动检查 / 实时进度 / 查看结果 / 下载报告
- **系统管理**: 环境自检
    """,
    version="2.2",
)

# CORS 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
from .routes.devices import router as devices_router
from .routes.profile import router as profile_router
from .routes.tasks import router as tasks_router
from .routes.system import router as system_router

app.include_router(devices_router)
app.include_router(profile_router)
app.include_router(tasks_router)
app.include_router(system_router)


# ---- SSE 进度推送 ----
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from .store import task_store

sse_router = APIRouter(tags=["实时进度"])


@sse_router.get("/api/sse/{task_id}")
async def task_progress_sse(task_id: str):
    """SSE 端点: 实时推送任务进度"""
    import asyncio
    import json

    async def event_stream():
        while True:
            t = task_store.get(task_id)
            if not t:
                yield f"event: error\ndata: {json.dumps({'error': 'task not found'})}\n\n"
                break
            yield f"data: {json.dumps(t)}\n\n"
            if t["status"] in ("done", "failed"):
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


app.include_router(sse_router)


# ---- 启动时初始化 ----
@app.on_event("startup")
async def startup_event():
    """启动时从 devices.csv 加载设备清单"""
    from core.credentials import load_devices
    devices_csv = os.path.join(_PROJECT_ROOT, "devices.csv")
    if os.path.exists(devices_csv):
        try:
            devices = load_devices(devices_csv)
            from .store import device_store
            device_store.load_from_csv(devices)
            logger.info(f"启动时从 devices.csv 加载了 {len(devices)} 台设备")
        except Exception as e:
            logger.warning(f"加载 devices.csv 失败: {e}")


# ---- 前端静态文件 ----
_web_dir = os.path.join(_PROJECT_ROOT, "web")
if os.path.isdir(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")


@app.get("/api")
async def api_root():
    return {"name": "华为VRP基线检查工具", "version": "2.2", "docs": "/docs"}
