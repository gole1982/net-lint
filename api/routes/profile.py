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

import errno
import os
import re
import sys
import tempfile
import threading

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
# profile.yaml 的读-改-写必须串行化, 否则并发请求会互相覆盖。
_PROFILE_LOCK = threading.Lock()


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


def _find_section(lines, section):
    """返回段头行号 (顶格 'name:'), 不存在时返回 None。"""
    for i, line in enumerate(lines):
        if line.rstrip() == f"{section}:" and not line.startswith((" ", "\t")):
            return i
    return None


def _section_end(lines, start):
    """段尾: 下一个顶格键或文件结尾 (顶格注释不算段尾)。"""
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        if line.startswith((" ", "\t")):
            continue
        if line.lstrip().startswith("#"):
            continue
        return i
    return len(lines)


def _update_section(text, section, updates, prune=False):
    """在保留注释的前提下更新 YAML 中某个映射段

    text:     原始文件文本
    section:  段名 (如 'variables')
    updates:  {键: 新值}
    prune:    True 时删除该段中不在 updates 里的键(整段替换语义)
    返回:     更新后的文本

    修正点 (旧实现的三处问题):
      1. 新增键插到“段尾”而不是最后一个键之后 —— 会落进下一个段;
         若被删除的段位于文件末尾, 还会因索引越界抛 IndexError。
      2. 先删行、后用旧索引插入, 导致插入位置错乱。
      3. prune 删除键时不考虑键行上的注释。
    """
    if not updates and not prune:
        return text

    lines = text.splitlines()
    start = _find_section(lines, section)

    if start is None:
        # 段不存在: 追加到文件末尾 (前面补一个空行, 避免与上一个段粘连)
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{section}:")
        for k, v in updates.items():
            lines.append(f"  {k}: {_render_scalar(v)}")
        return "\n".join(lines) + "\n"

    end = _section_end(lines, start)

    key_lines = []  # [(行号, 键, 缩进)]
    for i in range(start + 1, end):
        m = _KEY_RE.match(lines[i])
        if m:
            key_lines.append((i, m.group(2), m.group(1)))

    remaining = dict(updates)
    remove = set()
    replace = {}
    for i, key, indent in key_lines:
        if key in remaining:
            cm = _COMMENT_RE.search(lines[i])
            comment = cm.group(1).rstrip() if cm else ""
            replace[i] = (
                f"{indent}{key}: {_render_scalar(remaining.pop(key))}{comment}"
            )
        elif prune:
            remove.add(i)

    for i, new_line in replace.items():
        lines[i] = new_line

    # 插入锚点: 段内最后一个保留下来的键; 一个都没有时紧跟段头
    anchor = max((i for i, _k, _ind in key_lines if i not in remove), default=start)
    new_lines = [
        f"  {k}: {_render_scalar(v)}" for k, v in remaining.items()
    ]

    out = []
    for i, line in enumerate(lines):
        if i == anchor + 1:
            out.extend(new_lines)
        if i in remove:
            continue
        out.append(line)
    if anchor >= len(lines) - 1:
        out.extend(new_lines)

    return "\n".join(out) + "\n"


def _save_profile(sections):
    """保存 profile.yaml (保留注释)

    sections: [(段名, {键: 值}, 是否删除多余键), ...]
    写入采用"临时文件 + 原子替换", 避免进程中断导致配置文件被截断。
    写盘前先做回读校验, 渲染逻辑一旦出错不会污染用户配置文件。
    """
    with _PROFILE_LOCK:
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
                        f"profile.yaml 生成结果校验失败: "
                        f"{name}.{k} = {got.get(k)!r}, 期望 {v!r}"
                    )

        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(_profile_path), prefix=".profile-", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_path, _profile_path)
        except OSError as e:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            # 单文件 bind mount 上 rename 会返回 EBUSY/EXDEV,
            # 此时退化为原地截断写 (可以写挂载点里的文件, 但不能替换它)。
            if e.errno not in (errno.EBUSY, errno.EXDEV, errno.EACCES):
                raise
            with open(_profile_path, "w", encoding="utf-8", newline="") as f:
                f.write(text)


@router.get("/api/profile")
async def get_profile():
    return _load_profile()


@router.put("/api/profile")
async def update_profile(update: ProfileUpdate):
    profile = _load_profile()
    sections = []
    # 合并而非整段替换: 客户端只提交部分变量时, 整段 prune 会删掉其余变量,
    # 触发 validate_vars 的 KeyError, 让后续所有检查任务 500。
    if update.variables is not None:
        profile.setdefault("variables", {}).update(update.variables)
        sections.append(("variables", update.variables, False))
    if update.rules is not None:
        profile.setdefault("rules", {}).update(update.rules)
        sections.append(("rules", update.rules, False))
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
