"""
api.store — 内存存储 (设备列表 / 任务状态 / 报告结果)
================================================================
当前使用内存存储, 后续可替换为 SQLite 或其他持久化方案
================================================================
"""

import threading
import time
from typing import Optional

from .schemas import TaskStatus


class DeviceStore:
    """设备清单存储 (线程安全)"""
    def __init__(self):
        self._devices = []
        self._lock = threading.Lock()

    def list(self) -> list[dict]:
        with self._lock:
            return list(self._devices)

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
                    return
            raise KeyError(f"设备不存在: {ip}")

    def delete(self, ip: str):
        with self._lock:
            self._devices = [d for d in self._devices if d["ip"] != ip]

    def load_from_csv(self, devices: list[dict]):
        """从CSV解析后的列表加载(替换全部)"""
        with self._lock:
            self._devices = [dict(d) for d in devices]

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
            return self._tasks.get(task_id)

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
            return list(self._tasks.values())


# 全局单例
device_store = DeviceStore()
task_store = TaskStore()
