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

import os

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse

from ..schemas import TaskCreate, TaskOut
from ..store import task_store
from ..task_manager import create_and_run_task

router = APIRouter(prefix="/api/tasks", tags=["任务管理"])

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return task_store.get(task_id)


@router.get("", response_model=list[TaskOut])
async def list_tasks():
    return task_store.list_all()


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: str):
    t = task_store.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return t


@router.get("/{task_id}/results")
async def get_task_results(task_id: str):
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

    t = task_store.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    if t["status"] != "done":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    if fmt == "md":
        path = os.path.join(_PROJECT_ROOT, "remediation_report.md")
        return FileResponse(path, media_type="text/markdown",
                            filename="remediation_report.md")
    else:
        path = os.path.join(_PROJECT_ROOT, "baseline_report.xlsx")
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="baseline_report.xlsx",
        )
