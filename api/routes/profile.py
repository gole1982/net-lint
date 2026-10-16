"""
api.routes.profile — 规则开关与变量管理
================================================================
GET  /api/profile           获取profile(变量+规则开关)
PUT  /api/profile           更新profile(变量+规则开关)
GET  /api/profile/variables  获取变量
PUT  /api/profile/variables  更新变量
GET  /api/profile/rules      获取规则开关
PUT  /api/profile/rules/{rule_id}  切换单条规则
GET  /api/rules              获取规则库定义(只读)
================================================================
"""

import os
import sys

from fastapi import APIRouter, HTTPException

from ..schemas import RuleToggle, VariableUpdate, ProfileUpdate

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VENDOR_DIR = os.path.join(_PROJECT_ROOT, "vendor")
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

import yaml

router = APIRouter(tags=["配置管理"])

_profile_path = os.path.join(_PROJECT_ROOT, "profile.yaml")
_rules_path = os.path.join(_PROJECT_ROOT, "rules.yaml")


def _load_profile():
    with open(_profile_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _save_profile(data):
    with open(_profile_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


@router.get("/api/profile")
async def get_profile():
    return _load_profile()


@router.put("/api/profile")
async def update_profile(update: ProfileUpdate):
    profile = _load_profile()
    if update.variables is not None:
        profile["variables"] = update.variables
    if update.rules is not None:
        profile["rules"] = update.rules
    _save_profile(profile)
    return profile


@router.get("/api/profile/variables")
async def get_variables():
    return _load_profile().get("variables", {})


@router.put("/api/profile/variables")
async def update_variables(update: VariableUpdate):
    profile = _load_profile()
    profile.setdefault("variables", {}).update(update.variables)
    _save_profile(profile)
    return profile["variables"]


@router.get("/api/profile/rules")
async def get_rule_switches():
    return _load_profile().get("rules", {})


@router.put("/api/profile/rules/{rule_id}")
async def toggle_rule(rule_id: str, toggle: RuleToggle):
    profile = _load_profile()
    if "rules" not in profile:
        profile["rules"] = {}
    if rule_id not in profile["rules"]:
        raise HTTPException(status_code=404, detail=f"规则ID不存在: {rule_id}")
    profile["rules"][rule_id] = toggle.enabled
    _save_profile(profile)
    return {"rule_id": rule_id, "enabled": toggle.enabled}


@router.get("/api/rules")
async def get_rules():
    """获取规则库定义(只读)"""
    with open(_rules_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    rules = []
    for r in doc.get("rules", []):
        rules.append({
            "id": r.get("id", ""),
            "title": r.get("title", ""),
            "device_type": r.get("device_type", ""),
            "platforms": r.get("platforms", []),
            "severity": r.get("severity", "medium"),
            "builtin": r.get("builtin"),
            "has_fix": bool(r.get("fix") or any(
                s.get("fix") for s in r.get("sections", [])
            )),
        })
    return rules
