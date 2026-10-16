"""
core.models — 数据结构定义
================================================================
Rule / Section / Check / Finding 等核心数据模型
不含任何业务逻辑, 仅定义数据结构
================================================================
"""

import re

# ---- 常量与正则 ----
VAR_RE = re.compile(r"\$\{([A-Za-z_]\w*)\}")
PHYS = re.compile(
    r"(GigabitEthernet|XGigabitEthernet|MultiGE|Ethernet"
    r"|10GE|25GE|40GE|100GE|GE)"
)
SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "suggest": 3}


class Check:
    """检查项: kind/match_mode/lines"""
    __slots__ = ("kind", "match_mode", "lines")

    def __init__(self, kind="vrp", match="exact", lines=None):
        self.kind = kind
        self.match_mode = match
        self.lines = lines or []

    def to_dict(self):
        return {
            "kind": self.kind,
            "match": self.match_mode,
            "lines": self.lines,
        }


class Section:
    """配置段: context/selector/preconds/checks/fix_text"""
    def __init__(self, context="system", selector=None,
                 preconds=None, checks=None, fix_text=""):
        self.context = context
        self.selector = selector or ["*"]
        self.preconds = preconds or {}
        self.checks = checks or []
        self.fix_text = fix_text

    def to_dict(self):
        return {
            "context": self.context,
            "selector": self.selector,
            "fix": self.fix_text,
            "preconds": self.preconds,
            "checks": [c.to_dict() for c in self.checks],
        }


class Rule:
    """规则: id/title/device_type/platforms/severity/sections/builtin_name/fix_text"""
    def __init__(self, rid="", title="", device_type="",
                 platforms=None, severity="medium",
                 sections=None, builtin_name=None, fix_text=""):
        self.rid = rid
        self.title = title
        self.device_type = device_type
        self.platforms = platforms or ["s-series", "ce-series"]
        self.severity = severity
        self.sections = sections or []
        self.builtin_name = builtin_name
        self.fix_text = fix_text

    def to_dict(self):
        d = {
            "id": self.rid,
            "title": self.title,
            "device_type": self.device_type,
            "platforms": self.platforms,
            "severity": self.severity,
            "builtin": self.builtin_name,
            "fix": self.fix_text,
            "sections": [s.to_dict() for s in self.sections],
        }
        return d


class Finding:
    """检查结果: target/evidence/fix_lines"""
    __slots__ = ("target", "evidence", "fix_lines")

    def __init__(self, target="", evidence="", fix_lines=None):
        self.target = target
        self.evidence = evidence
        self.fix_lines = fix_lines or []

    def to_dict(self):
        return {
            "target": self.target,
            "evidence": self.evidence,
            "fix_lines": self.fix_lines,
        }
