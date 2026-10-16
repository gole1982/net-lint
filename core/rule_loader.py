"""
core.rule_loader — 规则库加载与校验
================================================================
从 YAML 文件解析规则定义, 校验字段完整性和ID唯一性
依赖: pyyaml, core.models, core.builtin_checks.BUILTINS
================================================================
"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor"))

import yaml

from .models import Rule, Section, Check, VAR_RE

logger = logging.getLogger("vrp_baseline")


def load_rules(path="rules.yaml"):
    """加载并校验规则库, 返回 [Rule]
    延迟导入 BUILTINS 以避免循环依赖
    """
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    rules = doc.get("rules", [])
    ids_seen = set()

    # 延迟导入避免循环依赖
    from .builtin_checks import BUILTINS

    for r in rules:
        # 必填字段校验
        for k in ("id", "title", "device_type", "severity"):
            if k not in r:
                raise ValueError(f"规则缺少字段 '{k}': {r.get('id', '?')}")
        rid = r["id"]
        if rid in ids_seen:
            raise ValueError(f"规则ID重复: {rid}")
        ids_seen.add(rid)
        # builtin注册校验
        if "builtin" in r and r["builtin"] not in BUILTINS:
            raise ValueError(
                f"规则 {rid}: 内置检查未注册 '{r['builtin']}'"
                f", 可用: {list(BUILTINS.keys())}"
            )
        # 检查项校验
        for sec in r.get("sections", []):
            for chk in sec.get("checks", []):
                kind = chk.get("kind", "vrp")
                if kind not in ("vrp", "any", "not"):
                    raise ValueError(f"{rid}: 非法kind={kind}")
                mm = chk.get("match", "exact")
                if mm not in ("exact", "regex"):
                    raise ValueError(f"{rid}: 非法match={mm}")
    logger.info(f"规则库加载完成: {len(rules)} 条规则")
    return [_rule_from_dict(r) for r in rules]


def _rule_from_dict(d):
    secs = []
    for s in d.get("sections", []):
        pcs = s.get("preconds") or {}
        chks = [
            Check(c.get("kind", "vrp"), c.get("match", "exact"),
                  c.get("lines", []))
            for c in s.get("checks", [])
        ]
        secs.append(Section(
            context=s.get("context", "system"),
            selector=s.get("selector", ["*"]),
            preconds=pcs,
            checks=chks,
            fix_text=s.get("fix", ""),
        ))
    return Rule(
        rid=d.get("id", ""),
        title=d.get("title", ""),
        device_type=d.get("device_type", ""),
        platforms=d.get("platforms", ["s-series", "ce-series"]),
        severity=d.get("severity", "medium"),
        sections=secs,
        builtin_name=d.get("builtin"),
        fix_text=d.get("fix", ""),
    )


def load_profile(path="profile.yaml"):
    """加载 profile.yaml, 返回 dict"""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_vars(rules, variables):
    """校验所有 ${var} 引用均有定义"""
    for r in rules:
        text = yaml.dump(r.to_dict(), allow_unicode=True)
        for name in VAR_RE.findall(text):
            if name not in variables and name not in ("if", "platform"):
                raise KeyError(
                    f"规则 [{r.rid}] 引用变量 ${{{name}}} "
                    f"未在profile.yaml中定义"
                )
