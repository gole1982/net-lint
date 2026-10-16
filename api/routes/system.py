"""
api.routes.system — 系统管理
================================================================
GET  /api/system/selfcheck   环境自检
GET  /api/system/info        系统信息
================================================================
"""

import platform
import sys

from fastapi import APIRouter

from ..schemas import SelfCheckResult

router = APIRouter(prefix="/api/system", tags=["系统管理"])


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
        )
    except ImportError as e:
        return SelfCheckResult(
            ok=False,
            telnetlib=False,
            python_version=sys.version.split()[0],
        )


@router.get("/info")
async def system_info():
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
