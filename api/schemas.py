"""
api.schemas — Pydantic 数据模型 (请求/响应)
================================================================
定义 API 层的数据交换模型, 与 core.models 的业务模型解耦
================================================================
"""

import ipaddress

from pydantic import BaseModel, Field, field_validator
from typing import Optional
from enum import Enum


class Platform(str, Enum):
    s_series = "s-series"
    ce_series = "ce-series"


class DeviceBase(BaseModel):
    type: str = Field("交换机", description="设备类型")
    name: str = Field(..., description="设备名称")
    ip: str = Field(..., description="设备管理IP")
    platform: Platform = Field(..., description="设备平台")
    protocol: str = Field("telnet", description="采集协议")
    username: str = Field("", description="Telnet登录用户名")

    @field_validator("ip")
    @classmethod
    def _ip_must_be_valid(cls, v: str) -> str:
        """ip 会参与快照文件名拼接 (snapshots/<ip>.cfg), 必须严格校验。"""
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError("ip 必须是合法的 IPv4/IPv6 地址")
        return v


class DeviceCreate(DeviceBase):
    password: Optional[str] = Field(None, description="Telnet登录密码(为空则从环境变量读取)")


class DeviceOut(DeviceBase):
    password: str = Field("", description="密码(输出时已脱敏或从环境变量填充)")
    has_password: bool = Field(False, description="是否有密码")

    class Config:
        from_attributes = True


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    platform: Optional[Platform] = None
    username: Optional[str] = None
    password: Optional[str] = None

    @field_validator("ip")
    @classmethod
    def _ip_must_be_valid(cls, v):
        if v is None:
            return v
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError("ip 必须是合法的 IPv4/IPv6 地址")
        return v


class RuleToggle(BaseModel):
    enabled: bool = Field(..., description="启用/禁用")


class VariableUpdate(BaseModel):
    variables: dict = Field(..., description="变量键值对")


class ProfileUpdate(BaseModel):
    variables: Optional[dict] = None
    rules: Optional[dict] = None


class TaskCreate(BaseModel):
    offline: bool = Field(False, description="是否离线模式")
    device_ips: Optional[list[str]] = Field(
        None, description="指定设备IP列表(为空则全部)"
    )
    rule_ids: Optional[list[str]] = Field(
        None, description="指定规则ID列表(为空则全部启用的)"
    )


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class TaskOut(BaseModel):
    task_id: str
    status: TaskStatus
    progress: float = 0.0
    total: int = 0
    completed: int = 0
    message: str = ""
    created_at: str = ""

    class Config:
        from_attributes = True


class TaskResult(BaseModel):
    """单台设备的检查结果"""
    ip: str
    name: str
    status: str = "ok"
    error: str = ""
    findings: list = []
    suggestions: list = []


class FindingItem(BaseModel):
    rule_id: str
    title: str
    severity: str
    target: str
    evidence: str
    fix: str = ""


class SelfCheckResult(BaseModel):
    ok: bool
    telnetlib: bool
    yaml_version: str = ""
    openpyxl_version: str = ""
    python_version: str = ""
    # 离线绿色包(vendor/)的架构提示; 空串表示无需提示
    vendor_note: str = ""
