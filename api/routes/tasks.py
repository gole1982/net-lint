"""
api.routes.tasks — 检查任务管理
================================================================
POST   /api/tasks          创建并启动检查任务
GET    /api/tasks           列出所有任务
GET    /api/tasks/{id}      查看任务状态
GET    /api/tasks/{id}/results  查看任务结果(JSON)
GET    /api/tasks/{id}/download/{fmt}  下载报告文件(md/xlsx)
================================================================
"""

import logging
import os
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..schemas import TaskCreate, TaskOut
from ..store import task_store
from ..task_manager import create_and_run_task, report_dir

router = APIRouter(prefix="/api/tasks", tags=["任务管理"])
logger = logging.getLogger("vrp_baseline")
# task_id 会参与报告目录拼接, 只允许安全字符
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _check_task_id(task_id: str):
    if not _TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")


@router.post("", response_model=TaskOut)
async def create_task(task: TaskCreate):
    try:
        task_id = await create_and_run_task(
            offline=task.offline,
            device_ips=task.device_ips,
            rule_ids=task.rule_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        # 不把内部异常文本回给客户端 (可能含路径/凭证线索)
        logger.exception("创建任务失败")
        raise HTTPException(status_code=500, detail="创建任务失败, 详情见服务端日志")
    return task_store.get(task_id)


@router.get("", response_model=list[TaskOut])
async def list_tasks():
    return task_store.list_all()


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: str):
    _check_task_id(task_id)
    t = task_store.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return t


@router.get("/{task_id}/results")
async def get_task_results(task_id: str):
    _check_task_id(task_id)
    t = task_store.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    results = task_store.get_results(task_id)
    return {
        "task_id": task_id,
        "status": t["status"],
        "progress": t["progress"],
        "results": results or {},
    }


@router.get("/{task_id}/download/{fmt}")
async def download_report(task_id: str, fmt: str):
    """下载报告文件 md/xlsx"""
    if fmt not in ("md", "xlsx"):
        raise HTTPException(status_code=400, detail="格式仅支持 md 或 xlsx")

    _check_task_id(task_id)
    t = task_store.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    if t["status"] == "failed":
        raise HTTPException(
            status_code=400,
            detail=f"任务失败, 无报告可下载: {t.get('message', '')}",
        )
    if t["status"] != "done":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    # 报告按 task_id 存放, 保证下载到的确实是该任务的报告
    fname = "remediation_report.md" if fmt == "md" else "baseline_report.xlsx"
    base = os.path.realpath(report_dir(task_id))
    path = os.path.realpath(os.path.join(base, fname))
    if os.path.commonpath([base, path]) != base:
        raise HTTPException(status_code=404, detail="报告路径非法")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"报告文件不存在: {fname}")

    if fmt == "md":
        return FileResponse(path, media_type="text/markdown",
                            filename=fname)
    else:
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=fname,
        )
