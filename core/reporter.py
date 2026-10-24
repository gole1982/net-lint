"""
core.reporter — 报告生成 (结构化数据 + 文件输出)
================================================================
build_report_data:  将检查结果转为结构化 dict (供 API JSON 或 CLI 写文件)
write_report_files: 从结构化数据生成 remediation_report.md + baseline_report.xlsx
render_fix:         将fix模板中的变量替换为实际值
save_snapshots:     将采集到的设备配置保存为快照文件
================================================================
"""

import os
import re
from datetime import datetime

from .models import Rule, VAR_RE, SEV_ORDER


def render_fix(fix_text, target, variables):
    """将fix模板中的变量替换为实际值

    target 可能是多个接口用 "; " 拼接的字符串 (内置检查的历史格式)。
    整改命令必须逐接口生成, 否则会拼出 "interface A; B; C" 这种非法命令。
    """
    targets = [t.strip() for t in str(target).split(";") if t.strip()] or ["system"]

    def _render_one(one_target):
        def _sub(m):
            name = m.group(1)
            if name == "if":
                return "" if one_target == "system" else one_target
            if name == "platform":
                return ""
            v = variables.get(name)
            if v is None:
                return "${" + name + "}"
            return str(v[0] if isinstance(v, list) else v)

        result = VAR_RE.sub(_sub, fix_text).strip("\n")
        return "\n".join(l for l in result.split("\n") if l.strip())

    blocks = []
    for one_target in targets:
        block = _render_one(one_target)
        if block and block not in blocks:
            blocks.append(block)
    return "\n".join(blocks)


def build_report_data(results, devices, variables, device_results=None):
    """将检查结果转为结构化 dict
    results: {ip: [(rule_or_"ERROR", (target, evidence, fix_lines))]}
    devices: [dict]
    variables: {var_name: value}
    device_results: {ip: {status, message}} 可选, 用于标记"未执行任何规则"
    返回: {
        "generated_at": str,
        "device_count": int,
        "devices": [{ip, name, errors, findings, suggestions, unchecked}],
        "summary": {total_fail, total_suggest, total_unchecked, total_devices}
    }
    """
    name_of = {d["ip"]: d.get("name", d["ip"]) for d in devices}
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    device_reports = []
    total_fail = 0
    total_suggest = 0
    total_unchecked = 0
    device_results = device_results or {}

    for ip in sorted(results.keys()):
        items = results[ip]
        meta = device_results.get(ip) or {}
        unchecked = meta.get("status") == "not_checked"

        # 区分错误项和规则检查结果
        # run_all 返回 [(rule, [Finding])], 错误为 ("ERROR", (ip, msg, []))
        err_items = []
        rule_items = []
        for item in items:
            if isinstance(item[0], str) and item[0] == "ERROR":
                err_items.append(item)
            elif isinstance(item[0], Rule):
                rule_items.append(item)

        # 按严重度排序规则项
        rule_items.sort(key=lambda x: SEV_ORDER.get(x[0].severity, 9))

        bad = [(rule, finding) for rule, findings in rule_items
               for finding in findings
               if rule.severity != "suggest"]
        sug = [(rule, finding) for rule, findings in rule_items
               for finding in findings
               if rule.severity == "suggest"]

        if not bad and not sug and not err_items and not unchecked:
            continue

        dev_report = {
            "ip": ip,
            "name": name_of.get(ip, ""),
            "errors": [],
            "findings": [],
            "suggestions": [],
            "unchecked": "",
            "checked_rules": meta.get("checked_rules", 0),
            "total_rules": meta.get("total_rules", 0),
        }
        if unchecked:
            dev_report["unchecked"] = meta.get("message") or "未执行任何规则"
            total_unchecked += 1

        # 错误项
        for _, (target, evidence, _) in err_items:
            dev_report["errors"].append({
                "target": target,
                "evidence": evidence,
            })

        # 不合规项
        for rule_obj, finding in bad:
            target = finding.target
            evidence = finding.evidence
            fix_lines = finding.fix_lines
            rendered_fix = ""
            if fix_lines and any(l.strip() for l in fix_lines):
                rendered_fix = render_fix("\n".join(fix_lines), target, variables)
            dev_report["findings"].append({
                "rule_id": rule_obj.rid,
                "title": rule_obj.title,
                "severity": rule_obj.severity,
                "target": target,
                "evidence": evidence,
                "fix": rendered_fix,
            })
            total_fail += 1

        # 建议优化项
        for rule_obj, finding in sug:
            dev_report["suggestions"].append({
                "rule_id": rule_obj.rid,
                "title": rule_obj.title,
                "severity": "suggest",
                "target": finding.target,
                "evidence": finding.evidence,
            })
            total_suggest += 1

        device_reports.append(dev_report)

    return {
        "generated_at": now_str,
        "device_count": len(devices),
        "devices": device_reports,
        "summary": {
            "total_fail": total_fail,
            "total_suggest": total_suggest,
            "total_unchecked": total_unchecked,
            "total_devices": len(devices),
        },
    }


def write_report_files(report_data, output_dir):
    """从结构化数据生成 remediation_report.md + baseline_report.xlsx
    report_data: build_report_data() 的返回值
    output_dir: 输出目录
    """
    # openpyxl: 优先已安装的包, 缺失时回退到 vendor/(离线绿色版)
    import sys
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        vendor_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor"
        )
        if os.path.isdir(vendor_dir) and vendor_dir not in sys.path:
            sys.path.append(vendor_dir)
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter

    # ---- 写 MD 报告 ----
    os.makedirs(output_dir, exist_ok=True)

    md_lines = [
        "# 基线整改文档",
        "",
        f"**生成时间**: {report_data['generated_at']}",
        f"**设备总数**: {report_data['device_count']}",
        f"**未执行检查的设备**: "
        f"{report_data['summary'].get('total_unchecked', 0)}",
        "",
        "> 未执行检查的设备不代表合规: 请核对 devices.csv 中的 "
        "platform/type 与规则库 platforms/device_type 是否一致。",
        "",
    ]

    xlsx_rows = []

    for dev in report_data["devices"]:
        ip = dev["ip"]
        name = dev["name"]
        md_lines.append(f"## {ip}（{name}）")
        md_lines.append("")

        # 错误项
        for err in dev["errors"]:
            md_lines.append(f"### 采集失败")
            md_lines.append(f"- **位置**: {err['target']}")
            md_lines.append(f"- **原因**: {err['evidence']}")
            md_lines.append("")

        # 未执行检查
        if dev.get("unchecked"):
            md_lines.append("### 未执行检查")
            md_lines.append(f"- **原因**: {dev['unchecked']}")
            md_lines.append(
                f"- **说明**: 已执行规则 "
                f"{dev.get('checked_rules', 0)}/{dev.get('total_rules', 0)} 条, "
                "该设备结果不代表合规"
            )
            md_lines.append("")
            xlsx_rows.append({
                "ip": ip,
                "rule": "-",
                "severity": "unchecked",
                "target": "-",
                "evidence": dev["unchecked"],
            })

        # 不合规项
        for f in dev["findings"]:
            md_lines.append(
                f"### [{f['rule_id']}] {f['title']}"
                f" ｜ 严重度: {f['severity']}"
            )
            md_lines.append(f"- **不合规位置**: {f['target']}")
            md_lines.append(f"- **证据**: {f['evidence']}")
            if f["fix"]:
                md_lines.append("- **整改命令**:")
                md_lines.append("```vrp")
                md_lines.append(f["fix"])
                md_lines.append("```")
            md_lines.append("")
            xlsx_rows.append({
                "ip": ip,
                "rule": f["rule_id"],
                "severity": f["severity"],
                "target": f["target"],
                "evidence": f["evidence"],
            })

        # 建议优化项
        if dev["suggestions"]:
            md_lines.append("#### 建议优化项")
            md_lines.append("")
            for s in dev["suggestions"]:
                md_lines.append(
                    f"- **[{s['rule_id']}] {s['title']}**: {s['evidence']}"
                )
                xlsx_rows.append({
                    "ip": ip,
                    "rule": s["rule_id"],
                    "severity": "suggest",
                    "target": s["target"],
                    "evidence": s["evidence"],
                })
            md_lines.append("")

    md_path = os.path.join(output_dir, "remediation_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    # ---- 写 Excel 汇总 ----
    xlsx_path = os.path.join(output_dir, "baseline_report.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "不合规明细"

    headers = ["IP", "规则ID", "严重度", "不合规位置", "证据"]
    # 表头是中文, 而 xlsx_rows 里的键是英文, 必须显式映射;
    # 直接用 headers 做键查找会导致除 IP 外所有列都是空值。
    col_keys = ["ip", "rule", "severity", "target", "evidence"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    fills = {
        "high": "FFC7CE",
        "medium": "FFEB9C",
        "low": "E2EFDA",
        "suggest": "DDEBF7",
        "unchecked": "D9D9D9",
    }
    for row_data in xlsx_rows:
        ws.append([
            str(row_data.get(k, ""))
            for k in col_keys
        ])
        sev = row_data.get("severity", "")
        if sev in fills:
            fill = PatternFill("solid", fgColor=fills[sev])
            for cell in ws[ws.max_row]:
                cell.fill = fill

    col_widths = [18, 16, 10, 45, 70]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"

    wb.save(xlsx_path)

    total_bad = sum(1 for r in xlsx_rows if r.get("severity") != "suggest")
    total_sug = sum(1 for r in xlsx_rows if r.get("severity") == "suggest")
    print(f"\n{'='*60}")
    print(f"完成! 设备 {report_data['device_count']} 台")
    print(f"  不合规项: {total_bad} 条")
    print(f"  建议优化: {total_sug} 条")
    print(f"  报告:")
    print(f"    {md_path}")
    print(f"    {xlsx_path}")


def save_snapshots(cfg_map, devices, base_dir):
    """将采集到的设备配置保存为快照文件
    cfg_map: {ip: config_text}
    """
    snap_dir = os.path.join(base_dir, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    saved = 0
    for dev in devices:
        ip = dev["ip"]
        cfg = cfg_map.get(ip)
        if cfg is None:
            continue
        snap_path = os.path.join(snap_dir, f"{ip}.cfg")
        try:
            with open(snap_path, "w", encoding="utf-8") as f:
                f.write(cfg)
            saved += 1
        except OSError as e:
            # 静默吞掉会让 "快照已更新" 的假设不成立, 至少给出可见告警。
            import logging
            logging.getLogger("vrp_baseline").warning(
                f"快照保存失败: {snap_path} ({e})"
            )
    print(f"  快照目录: {snap_dir}/ (已保存 {saved} 台)")
