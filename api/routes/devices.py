"""
api.routes.devices — 设备管理 CRUD
================================================================
GET    /api/devices          列出所有设备
POST   /api/devices          添加设备
GET    /api/devices/{ip}     查看设备详情
PUT    /api/devices/{ip}      更新设备
DELETE /api/devices/{ip}      删除设备
POST   /api/devices/import    从CSV导入设备
================================================================
"""

import csv
import io

from fastapi import APIRouter, HTTPException, UploadFile, File
from ..schemas import DeviceCreate, DeviceOut, DeviceUpdate
from ..store import device_store

router = APIRouter(prefix="/api/devices", tags=["设备管理"])


@router.get("", response_model=list[DeviceOut])
async def list_devices():
    devices = device_store.list_all()
    return [
        DeviceOut(
            type=d.get("type", "交换机"),
            name=d.get("name", ""),
            ip=d.get("ip", ""),
            platform=d.get("platform", ""),
            protocol=d.get("protocol", "telnet"),
            username=d.get("username", ""),
            password="***" if d.get("password") else "",
            has_password=bool(d.get("password")),
        )
        for d in devices
    ]


@router.post("", response_model=DeviceOut)
async def create_device(dev: DeviceCreate):
    try:
        device_store.add(dev.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return DeviceOut(
        **dev.model_dump(),
        password="***" if dev.password else "",
        has_password=bool(dev.password),
    )


@router.get("/{ip}", response_model=DeviceOut)
async def get_device(ip: str):
    d = device_store.get(ip)
    if not d:
        raise HTTPException(status_code=404, detail=f"设备不存在: {ip}")
    return DeviceOut(
        type=d.get("type", "交换机"),
        name=d.get("name", ""),
        ip=d.get("ip", ""),
        platform=d.get("platform", ""),
        protocol=d.get("protocol", "telnet"),
        username=d.get("username", ""),
        password="***" if d.get("password") else "",
        has_password=bool(d.get("password")),
    )


@router.put("/{ip}", response_model=DeviceOut)
async def update_device(ip: str, dev: DeviceUpdate):
    update_data = dev.model_dump(exclude_none=True)
    try:
        device_store.update(ip, update_data)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    d = device_store.get(update_data.get("ip", ip))
    return DeviceOut(
        type=d.get("type", "交换机"),
        name=d.get("name", ""),
        ip=d.get("ip", ""),
        platform=d.get("platform", ""),
        protocol=d.get("protocol", "telnet"),
        username=d.get("username", ""),
        password="***" if d.get("password") else "",
        has_password=bool(d.get("password")),
    )


@router.delete("/{ip}")
async def delete_device(ip: str):
    device_store.delete(ip)
    return {"ok": True}


@router.post("/import")
async def import_devices(file: UploadFile = File(...)):
    """从CSV文件导入设备(替换全部)"""
    content = await file.read()
    text = content.decode("utf-8-sig")
    # 过滤注释行
    lines = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    devices = list(reader)
    from core.credentials import resolve_credentials
    devices = [resolve_credentials(d) for d in devices]
    device_store.load_from_csv(devices)
    return {"ok": True, "count": len(devices)}
