"""
api.routes.system — 系统管理
================================================================
GET  /api/system/selfcheck   环境自检
GET  /api/system/info        系统信息
================================================================
"""

import os
import platform
import sys

from fastapi import APIRouter

from ..schemas import SelfCheckResult

router = APIRouter(prefix="/api/system", tags=["系统管理"])

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VENDOR_DIR = os.path.join(_PROJECT_ROOT, "vendor")


def _vendor_note():
    """检查随包 vendor/ 的二进制是否与当前机器架构匹配。

    vendor/ 只为离线绿色版准备, 其中的 pydantic_core 是编译好的扩展模块,
    架构不匹配时导入会失败, 而报错信息 (cannot open shared object) 很难
    让人联想到“换台机器就不能用”, 因此在自检里显式说明。
    """
    core_dir = os.path.join(_VENDOR_DIR, "pydantic_core")
    if not os.path.isdir(core_dir):
        return ""
    so_files = [
        f for f in os.listdir(core_dir)
        if f.startswith("_pydantic_core") and f.endswith((".so", ".pyd"))
    ]
    if not so_files:
        return ""
    machine = platform.machine().lower()
    for name in so_files:
        for tag, want in (
            ("aarch64", ("aarch64", "arm64")),
            ("x86_64", ("x86_64", "amd64", "x64")),
        ):
            if tag in name and not any(w in machine for w in want):
                return (
                    f"vendor/ 中的 pydantic_core 仅适用于 {tag}, "
                    f"当前机器为 {machine}; 请改用 "
                    f"pip install -r requirements.txt 安装依赖"
                )
    return ""


@router.get("/selfcheck", response_model=SelfCheckResult)
async def selfcheck():
    """环境自检"""
    try:
        import yaml
        import openpyxl
        try:
            import telnetlib
            telnet_ok = True
        except ImportError:
            telnet_ok = False

        return SelfCheckResult(
            ok=True,
            telnetlib=telnet_ok,
            yaml_version=yaml.__version__,
            openpyxl_version=openpyxl.__version__,
            python_version=sys.version.split()[0],
            vendor_note=_vendor_note(),
        )
    except ImportError:
        return SelfCheckResult(
            ok=False,
            telnetlib=False,
            python_version=sys.version.split()[0],
            vendor_note=_vendor_note(),
        )


@router.get("/info")
async def system_info():
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "vendor_note": _vendor_note(),
    }
