"""
api.store — 内存存储 (设备列表 / 任务状态 / 报告结果)
================================================================
当前使用内存存储, 后续可替换为 SQLite 或其他持久化方案
================================================================
"""

import csv
import logging
import os
import tempfile
import threading
import time
from typing import Optional

from .schemas import TaskStatus

logger = logging.getLogger("vrp_baseline")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICES_CSV = os.path.join(_PROJECT_ROOT, "devices.csv")
_CSV_FIELDS = ["type", "name", "ip", "platform", "protocol", "username", "password"]
# 任务历史上限: 内存存储不可能无限增长
MAX_TASKS = 200


class DeviceStore:
    """设备清单存储 (线程安全, 可写回 CSV)"""
    def __init__(self, persist_path: Optional[str] = None):
        self._devices = []
        self._lock = threading.RLock()
        self._persist_path = persist_path

    def list_all(self) -> list[dict]:
        """列出全部设备。

        方法名不能叫 list: 类体内的名字 list 会遮蔽内建 list,
        使本类中后续的 list[dict] 注解在 Python < 3.14(注解急切求值)
        解析到该方法而非内建类型, 抛
        TypeError: 'function' object is not subscriptable,
        导致本模块无法导入。命名与 TaskStore.list_all 保持一致。
        """
        with self._lock:
            return [dict(d) for d in self._devices]

    def get(self, ip: str) -> Optional[dict]:
        with self._lock:
            for d in self._devices:
                if d["ip"] == ip:
                    return dict(d)
        return None

    def add(self, device: dict):
        with self._lock:
            # 检查重复IP
            for d in self._devices:
                if d["ip"] == device["ip"]:
                    raise ValueError(f"设备IP已存在: {device['ip']}")
            self._devices.append(dict(device))
        self.save_to_csv()

    def update(self, ip: str, device: dict):
        with self._lock:
            for i, d in enumerate(self._devices):
                if d["ip"] == ip:
                    new_ip = device.get("ip", ip)
                    # 如果IP变了, 检查不冲突
                    if new_ip != ip:
                        for j, dd in enumerate(self._devices):
                            if j != i and dd["ip"] == new_ip:
                                raise ValueError(f"设备IP已存在: {new_ip}")
                    self._devices[i] = {**d, **device}
                    break
            else:
                raise KeyError(f"设备不存在: {ip}")
        self.save_to_csv()

    def delete(self, ip: str):
        with self._lock:
            self._devices = [d for d in self._devices if d["ip"] != ip]
        self.save_to_csv()

    def load_from_csv(self, devices: list[dict]):
        """从CSV解析后的列表加载(替换全部)"""
        with self._lock:
            self._devices = [dict(d) for d in devices]

    def save_to_csv(self, path: Optional[str] = None):
        """将设备清单写回 CSV, 使 Web 端 CRUD 在重启后不丢失。

        口令不落盘: 保持“密码来自 VRP_PASS_<IP> 环境变量”的模型,
        避免把明文口令写到另一个文件里。只读挂载时降级为内存存储并告警。
        """
        path = path or self._persist_path
        if not path:
            return False
        rows = self.to_csv_rows()
        try:
            fd, tmp = tempfile.mkstemp(
                dir=os.path.dirname(os.path.abspath(path)),
                prefix=".devices-", suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
                    writer.writeheader()
                    for row in rows:
                        writer.writerow({**row, "password": ""})
                os.replace(tmp, path)
            except OSError:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                # 单文件 bind mount 上 rename 会失败, 退化为原地截断写
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
                    writer.writeheader()
                    for row in rows:
                        writer.writerow({**row, "password": ""})
            return True
        except OSError as e:
            logger.warning(
                f"设备清单写回失败({path}): {e}; 本次变更仅保留在内存中"
            )
            return False

    def to_csv_rows(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "type": d.get("type", "交换机"),
                    "name": d.get("name", ""),
                    "ip": d.get("ip", ""),
                    "platform": d.get("platform", ""),
                    "protocol": d.get("protocol", "telnet"),
                    "username": d.get("username", ""),
                    "password": d.get("password", ""),
                }
                for d in self._devices
            ]


class TaskStore:
    """任务状态存储 (线程安全)"""
    def __init__(self):
        self._tasks = {}
        self._results = {}
        self._lock = threading.Lock()

    def create(self, task_id: str, total: int = 0):
        with self._lock:
            # 淘汰最旧的已完成任务, 避免内存无上限增长
            if len(self._tasks) >= MAX_TASKS:
                for tid, t in list(self._tasks.items()):
                    if len(self._tasks) < MAX_TASKS:
                        break
                    if t["status"] in (TaskStatus.done, TaskStatus.failed):
                        self._tasks.pop(tid, None)
                        self._results.pop(tid, None)
            self._tasks[task_id] = {
                "task_id": task_id,
                "status": TaskStatus.pending,
                "progress": 0.0,
                "total": total,
                "completed": 0,
                "message": "",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._results[task_id] = {}

    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task is not None else None

    def update(self, task_id: str, **kwargs):
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update(kwargs)

    def set_result(self, task_id: str, ip: str, result: dict):
        with self._lock:
            if task_id in self._results:
                self._results[task_id][ip] = result

    def get_results(self, task_id: str) -> Optional[dict]:
        with self._lock:
            return self._results.get(task_id)

    def list_all(self) -> list[dict]:
        with self._lock:
            return [dict(t) for t in self._tasks.values()]


# 全局单例
device_store = DeviceStore(DEVICES_CSV)
task_store = TaskStore()
