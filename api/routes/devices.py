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
import ipaddress

from fastapi import APIRouter, HTTPException, UploadFile, File
from ..schemas import DeviceCreate, DeviceOut, DeviceUpdate
from ..store import device_store

router = APIRouter(prefix="/api/devices", tags=["设备管理"])

_ALLOWED_PLATFORMS = {"s-series", "ce-series"}
_ALLOWED_PROTOCOLS = {"telnet"}
_REQUIRED_COLUMNS = ("type", "name", "ip", "platform", "protocol", "username", "password")


def _to_out(device: dict) -> DeviceOut:
    """内存 dict -> 响应模型 (口令脱敏)

    直接 DeviceOut(**model_dump(), password=...) 会因关键字重复抛 TypeError,
    使 POST /api/devices 恒 500, 因此统一先构造 payload 再覆盖字段。
    """
    payload = {
        "type": device.get("type", "交换机"),
        "name": device.get("name", ""),
        "ip": device.get("ip", ""),
        "platform": device.get("platform", ""),
        "protocol": device.get("protocol", "telnet"),
        "username": device.get("username", ""),
    }
    payload["password"] = "***" if device.get("password") else ""
    payload["has_password"] = bool(device.get("password"))
    return DeviceOut(**payload)


@router.get("", response_model=list[DeviceOut])
async def list_devices():
    return [_to_out(d) for d in device_store.list_all()]


@router.post("", response_model=DeviceOut)
async def create_device(dev: DeviceCreate):
    try:
        device_store.add(dev.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_out(dev.model_dump())


@router.get("/{ip}", response_model=DeviceOut)
async def get_device(ip: str):
    d = device_store.get(ip)
    if not d:
        raise HTTPException(status_code=404, detail=f"设备不存在: {ip}")
    return _to_out(d)


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
    if not d:
        raise HTTPException(status_code=404, detail=f"设备不存在: {ip}")
    return _to_out(d)


@router.delete("/{ip}")
async def delete_device(ip: str):
    if not device_store.get(ip):
        raise HTTPException(status_code=404, detail=f"设备不存在: {ip}")
    device_store.delete(ip)
    return {"ok": True}


def _decode_csv(content: bytes) -> str:
    """按 UTF-8(BOM) -> GBK 顺序解码, 都失败则判定为不可识别编码。"""
    for enc in ("utf-8-sig", "gbk"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise HTTPException(
        status_code=400, detail="CSV 编码无法识别(支持 UTF-8/GBK)"
    )


def _validate_csv_devices(rows: list[dict]) -> list[dict]:
    """逐行校验, 返回清洗后的设备列表; 有任何问题时一次性报出行号。"""
    errors = []
    seen = set()
    cleaned = []

    for lineno, row in enumerate(rows, start=2):
        name = (row.get("name") or "").strip()
        ip = (row.get("ip") or "").strip()
        platform = (row.get("platform") or "").strip().lower()
        protocol = (row.get("protocol") or "telnet").strip().lower() or "telnet"
        username = (row.get("username") or "").strip()
        password = row.get("password") or ""
        device_type = (row.get("type") or "交换机").strip() or "交换机"

        if not name:
            errors.append(f"第 {lineno} 行: name 为空")
        if not ip:
            errors.append(f"第 {lineno} 行: ip 为空")
        else:
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                errors.append(f"第 {lineno} 行: ip 非法 ({ip})")
        if platform not in _ALLOWED_PLATFORMS:
            errors.append(
                f"第 {lineno} 行: platform 非法 ({platform or '空'}), "
                f"仅支持 {sorted(_ALLOWED_PLATFORMS)}"
            )
        if protocol not in _ALLOWED_PROTOCOLS:
            errors.append(
                f"第 {lineno} 行: protocol 非法 ({protocol}), "
                f"仅支持 {sorted(_ALLOWED_PROTOCOLS)}"
            )
        if ip and ip in seen:
            errors.append(f"第 {lineno} 行: ip 重复 ({ip})")
        seen.add(ip)

        cleaned.append({
            "type": device_type,
            "name": name,
            "ip": ip,
            "platform": platform,
            "protocol": protocol,
            "username": username,
            "password": password,
        })

    if errors:
        raise HTTPException(
            status_code=400,
            detail="CSV 校验失败: " + "; ".join(errors[:10])
            + (f" 等 {len(errors)} 处问题" if len(errors) > 10 else ""),
        )
    return cleaned


@router.post("/import")
async def import_devices(file: UploadFile = File(...)):
    """从CSV文件导入设备(替换全部)"""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="CSV 文件为空")

    text = _decode_csv(content)
    # 过滤注释行与空行
    lines = [
        l for l in text.splitlines()
        if l.strip() and not l.lstrip().startswith("#")
    ]
    if not lines:
        raise HTTPException(status_code=400, detail="CSV 中没有数据行")

    reader = csv.DictReader(lines)
    header = tuple((h or "").strip() for h in (reader.fieldnames or []))
    missing = [c for c in _REQUIRED_COLUMNS if c not in header]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"CSV 缺少列: {', '.join(missing)} "
                   f"(需要的列为 {', '.join(_REQUIRED_COLUMNS)})",
        )

    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV 中没有设备记录")

    devices = _validate_csv_devices(rows)
    from core.credentials import resolve_credentials
    devices = [resolve_credentials(d) for d in devices]
    device_store.load_from_csv(devices)
    device_store.save_to_csv()
    return {"ok": True, "count": len(devices)}
