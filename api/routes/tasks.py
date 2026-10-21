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

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..schemas import TaskCreate, TaskOut
from ..store import task_store
from ..task_manager import create_and_run_task, report_dir

router = APIRouter(prefix="/api/tasks", tags=["任务管理"])


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

    # 报告按 task_id 存放, 保证下载到的确实是该任务的报告
    fname = "remediation_report.md" if fmt == "md" else "baseline_report.xlsx"
    path = os.path.join(report_dir(task_id), fname)
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
