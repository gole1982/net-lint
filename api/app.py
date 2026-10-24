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

import hmac
import logging
import os
import sys

# 路径注入 (离线绿色版用vendor, Docker容器用pip安装的包)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
_VENDOR_DIR = os.path.join(_PROJECT_ROOT, "vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    # 追加到 sys.path 末尾: 已安装的同名包优先, vendor/ 仅作为离线绿色版兜底。
    # 离线绿色版通过 PYTHONPATH=vendor:. 已经让 vendor 优先, 行为不变。
    sys.path.append(_VENDOR_DIR)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

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

# ---- CORS ----
# 前端页面由本应用在 "/" 提供, 同源访问本来就不需要 CORS, 因此默认关闭跨域。
# 只有把前端单独部署到其它域名时, 才通过 VRP_ALLOWED_ORIGINS 指定白名单:
#   VRP_ALLOWED_ORIGINS=https://vrp.example.com
# 注意: 不要使用 allow_origins=["*"] + allow_credentials=True。该组合下
# Starlette 会回显任意请求 Origin 并带 Access-Control-Allow-Credentials,
# 任意站点都能读取本 API 的设备清单并驱动服务端 telnet 内网交换机。
_allowed_origins = [
    o.strip() for o in os.environ.get("VRP_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info(f"CORS 白名单: {_allowed_origins}")
else:
    logger.info("CORS: 未配置 VRP_ALLOWED_ORIGINS, 仅允许同源访问")


# ---- 可选 API 令牌鉴权 ----
# 设置 VRP_API_TOKEN 后, 所有 /api 请求必须携带 X-API-Token 头。
# 未设置时不鉴权(便于内网/离线直接使用), 但启动日志会给出提示。
_API_TOKEN = os.environ.get("VRP_API_TOKEN", "").strip()


@app.middleware("http")
async def api_token_guard(request, call_next):
    """API 令牌校验中间件

    保护范围包括 /api 与 API 文档页: 否则未鉴权部署下 /docs 会直接暴露
    全部接口定义。令牌比较先编码为 bytes —— hmac.compare_digest 对含
    非 ASCII 字符的 str 会抛 TypeError (100% 拒登, 不是 401)。
    """
    path = request.url.path
    guarded = (
        path == "/api" or path.startswith("/api/")
        or path in ("/docs", "/redoc", "/openapi.json")
    )
    if _API_TOKEN and guarded:
        token = request.headers.get("X-API-Token", "")
        if not token and path.startswith("/api/sse/"):
            # EventSource 无法自定义请求头, 仅 SSE 允许查询参数传递
            token = request.query_params.get("token", "")
        try:
            valid = bool(token) and hmac.compare_digest(
                token.encode("utf-8"), _API_TOKEN.encode("utf-8")
            )
        except (UnicodeError, TypeError):
            valid = False
        if not valid:
            return JSONResponse({"detail": "无效的 API 令牌"}, status_code=401)
    return await call_next(request)

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
async def task_progress_sse(task_id: str, request: Request):
    """SSE 端点: 实时推送任务进度"""
    import asyncio
    import json
    import time

    async def event_stream():
        # 充满上限: 任务卡住时连接不能永久占用事件循环/连接槽
        deadline = time.monotonic() + 600
        while True:
            if await request.is_disconnected():
                break
            t = task_store.get(task_id)
            if not t:
                yield f"event: error\ndata: {json.dumps({'error': 'task not found'})}\n\n"
                break
            yield f"data: {json.dumps(t)}\n\n"
            if t["status"] in ("done", "failed"):
                break
            if time.monotonic() > deadline:
                yield "event: error\ndata: {\"error\": \"stream timeout\"}\n\n"
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


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


# ---- API 根路径 ----
# 必须注册在 StaticFiles 挂载("/")之前: 挂在 "/" 的 Mount 会匹配所有路径,
# 若先注册, /api 会被静态文件接管并返回 404。
@app.get("/api")
async def api_root():
    return {"name": "华为VRP基线检查工具", "version": "2.2", "docs": "/docs"}


# ---- 前端静态文件 ----
_web_dir = os.path.join(_PROJECT_ROOT, "web")
if os.path.isdir(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")
