"""
core.engine — 规则执行引擎
================================================================
run_rule: 对单台设备的配置执行一条规则
run_all:  对单台设备执行多条规则, 返回汇总结果
run_all_detailed: 额外返回执行统计(用于区分"全部合规"与"未执行任何规则")
================================================================
"""

import logging

from .config_parser import split_blocks, compile_tpl, select_targets, pass_preconds
from .models import Finding
from .builtin_checks import BUILTINS

logger = logging.getLogger("vrp_baseline")


def run_rule_detailed(rule, cfg, variables):
    """对单台设备的配置执行一条规则

    返回 (findings, evaluated):
      evaluated 为实际参与判定的目标数。规则一条都没命中目标时该值为 0,
      调用方据此区分"检查后全部合规"与"根本没有检查"。
    """
    blocks = split_blocks(cfg)
    findings = []
    evaluated = 0

    # 内置检查
    if rule.builtin_name:
        fn = BUILTINS.get(rule.builtin_name)
        if not fn:
            findings.append(Finding(
                target="system",
                evidence=f"内置检查 '{rule.builtin_name}' 未注册",
                fix_lines=rule.fix_text.split("\n"),
            ))
            return findings, 0

        ret = fn(cfg, blocks, variables)
        per_target = None
        if len(ret) == 4:
            st, detail, pos, per_target = ret
        else:
            st, detail, pos = ret

        evaluated = 1
        if st != "PASS":
            if per_target:
                for target in (pos or sorted(per_target)):
                    info = per_target.get(target) or {}
                    fix_lines = info.get("fix", rule.fix_text.split("\n"))
                    findings.append(Finding(
                        target=target,
                        evidence=f"[{st}] {info.get('evidence', detail)}",
                        fix_lines=fix_lines,
                    ))
            else:
                targets = "; ".join(pos) if pos else "system"
                findings.append(Finding(
                    target=targets,
                    evidence=f"[{st}] {detail}",
                    fix_lines=rule.fix_text.split("\n"),
                ))
        return findings, evaluated

    # 普通section检查
    for sec in rule.sections:
        targets = select_targets(sec, blocks)
        for target_name, block in targets.items():
            if not pass_preconds(block, sec.preconds):
                continue
            evaluated += 1
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
    return findings, evaluated


def run_rule(rule, cfg, variables):
    """对单台设备的配置执行一条规则, 返回 [Finding]"""
    return run_rule_detailed(rule, cfg, variables)[0]


def run_all_detailed(rules, cfg, variables, platform="", device_type=""):
    """对单台设备执行多条规则

    返回 (results, stats):
      results | [(rule, [Finding])]
      stats   | total/applied/evaluated/skipped_platform/skipped_device_type/no_target
    """
    results = []
    stats = {
        "total": len(rules),
        "applied": 0,
        "evaluated": 0,
        "skipped_platform": 0,
        "skipped_device_type": 0,
        "no_target": 0,
    }
    for rule in rules:
        if platform and platform not in rule.platforms:
            stats["skipped_platform"] += 1
            continue
        if device_type and rule.device_type != device_type:
            stats["skipped_device_type"] += 1
            continue

        findings, evaluated = run_rule_detailed(rule, cfg, variables)
        stats["applied"] += 1
        stats["evaluated"] += evaluated
        if evaluated == 0:
            stats["no_target"] += 1
        if findings:
            results.append((rule, findings))
    return results, stats


def not_checked_reason(stats):
    """把执行统计转成可读的"未检查"原因(CLI 与 Web 共用)。"""
    stats = stats or {}
    if stats.get("total", 0) == 0:
        return "没有启用任何规则, 未执行检查"
    if stats.get("applied") and not stats.get("evaluated"):
        return (
            "未执行任何检查: 规则未匹配到可检查的配置对象"
            f"(未命中目标的规则 {stats.get('no_target', 0)} 条), 结果不代表合规"
        )
    return (
        "未执行任何规则: 规则适用平台/设备类型与设备不匹配"
        f"(平台不匹配 {stats.get('skipped_platform', 0)} 条, "
        f"设备类型不匹配 {stats.get('skipped_device_type', 0)} 条), "
        "结果不代表合规"
    )


def run_all(rules, cfg, variables, platform="", device_type=""):
    """对单台设备配置执行多条规则
    返回 [(rule, [Finding])] 列表
    platform/device_type 用于过滤适用规则
    """
    return run_all_detailed(
        rules, cfg, variables, platform=platform, device_type=device_type
    )[0]
