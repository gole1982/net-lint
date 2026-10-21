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
import re
import sys
import tempfile

from fastapi import APIRouter, HTTPException

from ..schemas import RuleToggle, VariableUpdate, ProfileUpdate

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VENDOR_DIR = os.path.join(_PROJECT_ROOT, "vendor")


def _import_yaml():
    """导入 PyYAML: 优先已安装的包, 缺失时回退到 vendor/(离线绿色版)"""
    try:
        import yaml
        return yaml
    except ImportError:
        if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
            sys.path.append(_VENDOR_DIR)
        import yaml
        return yaml


yaml = _import_yaml()

router = APIRouter(tags=["配置管理"])

_profile_path = os.path.join(_PROJECT_ROOT, "profile.yaml")
_rules_path = os.path.join(_PROJECT_ROOT, "rules.yaml")


def _load_profile():
    with open(_profile_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# 保留注释的 profile.yaml 写回
#
# profile.yaml 是"用户唯一需要编辑的配置文件", 里面每条变量/规则都带中文注释
# 说明。直接 yaml.dump 回写会把全部注释清空(实测 56 行/32 注释 -> 21 行/0 注释),
# 因此改为按行定点更新: 只替换被修改的键的值, 行尾注释与文件其余内容原样保留。
# ---------------------------------------------------------------------------

# 形如 "  key: value   # 注释" 中的键
_KEY_RE = re.compile(r"^(\s+)([A-Za-z_][A-Za-z0-9_-]*)\s*:")
# 行尾注释: 两个及以上空白后开始的 # (值里的 # 不会被误判)
_COMMENT_RE = re.compile(r"(\s{2,}#.*)$")


def _render_scalar(value):
    """把一个值渲染成单行 YAML (必要时自动加引号)"""
    if isinstance(value, (list, tuple)):
        # 列表用流式写法, 保证结果是单行
        dumped = yaml.safe_dump(
            {"v": list(value)}, allow_unicode=True, default_flow_style=True
        ).strip()
    else:
        dumped = yaml.safe_dump(
            {"v": value}, allow_unicode=True, default_flow_style=False
        ).strip()
    # dumped 形如 "v: 10.1.1.100" / "v: '2000'" / "{v: [a, b]}"
    if dumped.startswith("{"):
        dumped = dumped[1:-1]
    return dumped.split(":", 1)[1].strip()


def _update_section(text, section, updates, prune=False):
    """在保留注释的前提下更新 YAML 中某个映射段

    text:     原始文件文本
    section:  段名 (如 'variables')
    updates:  {键: 新值}
    prune:    True 时删除该段中不在 updates 里的键(整段替换语义)
    返回:     更新后的文本
    """
    if not updates and not prune:
        return text

    lines = text.splitlines()

    # ---- 定位段头 ----
    start = None
    for i, l in enumerate(lines):
        if l.rstrip() == f"{section}:":
            start = i
            break

    if start is None:
        # 段不存在, 追加到文件末尾
        lines.append(f"{section}:")
        for k, v in updates.items():
            lines.append(f"  {k}: {_render_scalar(v)}")
        return "\n".join(lines) + "\n"

    # ---- 定位段尾(下一个顶格键 或 文件结束) ----
    end = len(lines)
    for i in range(start + 1, len(lines)):
        l = lines[i]
        if l.strip() and not l.startswith((" ", "\t")) and not l.lstrip().startswith("#"):
            end = i
            break

    # ---- 段内定点更新 ----
    remaining = dict(updates)
    for i in range(start + 1, end):
        l = lines[i]
        m = _KEY_RE.match(l)
        if not m:
            continue
        indent, key = m.group(1), m.group(2)
        if key not in remaining:
            # prune 模式下删除已不存在的键
            if prune and key not in updates:
                lines[i] = None
            continue
        cm = _COMMENT_RE.search(l)
        comment = cm.group(1).rstrip() if cm else ""
        lines[i] = f"{indent}{key}: {_render_scalar(remaining.pop(key))}{comment}"

    lines = [l for l in lines if l is not None]

    # ---- 段内新增不存在的键 ----
    if remaining:
        insert_at = end
        # 段尾若有空行, 把新键插到最后一条已知键之后, 空行留到段末
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines[insert_at:insert_at] = [
            f"  {k}: {_render_scalar(v)}" for k, v in remaining.items()
        ]

    return "\n".join(lines) + "\n"


def _save_profile(sections):
    """保存 profile.yaml (保留注释)

    sections: [(段名, {键: 值}, 是否删除多余键), ...]
    写入采用"临时文件 + 原子替换", 避免进程中断导致配置文件被截断。
    写盘前先做回读校验, 渲染逻辑一旦出错不会污染用户配置文件。
    """
    with open(_profile_path, encoding="utf-8") as f:
        text = f.read()

    for name, updates, prune in sections:
        text = _update_section(text, name, updates or {}, prune)

    # ---- 回写前校验 ----
    try:
        parsed = yaml.safe_load(text) or {}
    except Exception as e:
        raise ValueError(f"profile.yaml 生成结果无法解析, 已放弃写入: {e}")
    for name, updates, _prune in sections:
        got = parsed.get(name) or {}
        for k, v in (updates or {}).items():
            if got.get(k) != v:
                raise ValueError(
                    f"profile.yaml 生成结果校验失败: {name}.{k} = {got.get(k)!r}, 期望 {v!r}"
                )

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(_profile_path), prefix=".profile-", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, _profile_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


@router.get("/api/profile")
async def get_profile():
    return _load_profile()


@router.put("/api/profile")
async def update_profile(update: ProfileUpdate):
    profile = _load_profile()
    sections = []
    if update.variables is not None:
        profile["variables"] = update.variables
        sections.append(("variables", update.variables, True))
    if update.rules is not None:
        profile["rules"] = update.rules
        sections.append(("rules", update.rules, True))
    if sections:
        _save_profile(sections)
    return profile


@router.get("/api/profile/variables")
async def get_variables():
    return _load_profile().get("variables", {})


@router.put("/api/profile/variables")
async def update_variables(update: VariableUpdate):
    profile = _load_profile()
    profile.setdefault("variables", {}).update(update.variables)
    _save_profile([("variables", update.variables, False)])
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
    _save_profile([("rules", {rule_id: toggle.enabled}, False)])
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
