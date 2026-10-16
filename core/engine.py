"""
core.engine — 规则执行引擎
================================================================
run_rule: 对单台设备的配置执行一条规则
run_all:  对单台设备执行多条规则, 返回汇总结果
================================================================
"""

import logging

from .config_parser import split_blocks, compile_tpl, select_targets, pass_preconds
from .models import Finding
from .builtin_checks import BUILTINS

logger = logging.getLogger("vrp_baseline")


def run_rule(rule, cfg, variables):
    """对单台设备的配置执行一条规则, 返回 [Finding]"""
    blocks = split_blocks(cfg)
    findings = []

    # 内置检查
    if rule.builtin_name:
        fn = BUILTINS.get(rule.builtin_name)
        if not fn:
            findings.append(Finding(
                target="system",
                evidence=f"内置检查 '{rule.builtin_name}' 未注册",
                fix_lines=rule.fix_text.split("\n"),
            ))
            return findings
        st, detail, pos = fn(cfg, blocks, variables)
        if st != "PASS":
            targets = "; ".join(pos) if pos else "system"
            findings.append(Finding(
                target=targets,
                evidence=f"[{st}] {detail}",
                fix_lines=rule.fix_text.split("\n"),
            ))
        return findings

    # 普通section检查
    for sec in rule.sections:
        targets = select_targets(sec, blocks)
        for target_name, block in targets.items():
            if not pass_preconds(block, sec.preconds):
                continue
            for chk in sec.checks:
                patterns = [(l, compile_tpl(l, chk.match_mode, variables))
                            for l in chk.lines]
                if chk.kind == "vrp":
                    missing = [l for l, p in patterns if not p.search(block)]
                    if missing:
                        findings.append(Finding(
                            target=target_name,
                            evidence=f"缺失配置: {missing}",
                            fix_lines=sec.fix_text.split("\n"),
                        ))
                        break
                elif chk.kind == "any":
                    if not any(p.search(block) for _, p in patterns):
                        findings.append(Finding(
                            target=target_name,
                            evidence=f"至少需满足其一: {chk.lines}",
                            fix_lines=sec.fix_text.split("\n"),
                        ))
                        break
                elif chk.kind == "not":
                    bad = [l for l, p in patterns if p.search(block)]
                    if bad:
                        findings.append(Finding(
                            target=target_name,
                            evidence=f"存在禁止配置: {bad}",
                            fix_lines=sec.fix_text.split("\n"),
                        ))
                        break
    return findings


def run_all(rules, cfg, variables, platform="", device_type=""):
    """对单台设备配置执行多条规则
    返回 [(rule, [Finding])] 列表
    platform/device_type 用于过滤适用规则
    """
    results = []
    for rule in rules:
        if platform and platform not in rule.platforms:
            continue
        if device_type and rule.device_type != device_type:
            continue
        findings = run_rule(rule, cfg, variables)
        if findings:
            results.append((rule, findings))
    return results
