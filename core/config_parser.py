"""
core.config_parser — VRP 配置文本解析工具
================================================================
split_blocks:       按 '#' 分割配置文本
compile_tpl:        将含 ${var} 的模板行编译为正则对象(带缓存)
select_targets:     根据上下文和选择器选出候选配置块
pass_preconds:      前提条件过滤
================================================================
"""

import re

from .models import VAR_RE


def split_blocks(cfg):
    """VRP display current-configuration 按 '#' 分段
    返回 [全局段, 接口段1, 接口段2, ...]
    """
    parts = re.split(r"(?m)^#\s*$", cfg)
    return [p.strip() for p in parts if p.strip()]


# ---- 正则编译缓存 ----
_tpl_cache = {}


def compile_tpl(line, match_mode, variables):
    """将含 ${var} 的模板行编译为正则对象
    exact -> 整行精确匹配(忽略缩进)
    regex -> 正则search模式
    带缓存: 相同(line, match_mode, variables指纹)不重复编译
    """
    var_names = sorted(VAR_RE.findall(line))
    var_sig = tuple(
        (vn, str(variables.get(vn, "")))
        for vn in var_names if vn not in ("if", "platform")
    )
    cache_key = (line, match_mode, var_sig)

    cached = _tpl_cache.get(cache_key)
    if cached is not None:
        return cached

    pat = ""
    pos = 0
    for m in VAR_RE.finditer(line):
        lit = line[pos:m.start()]
        pat += lit if match_mode == "regex" else re.escape(lit)
        varname = m.group(1)
        if varname == "if":
            pat += r"\S+"
        elif varname == "platform":
            pat += r"\S+"
        else:
            val = variables[varname]
            vals = val if isinstance(val, list) else [val]
            pat += "(?:" + "|".join(re.escape(str(v)) for v in vals) + ")"
        pos = m.end()
    tail = line[pos:]
    pat += tail if match_mode == "regex" else re.escape(tail)
    if match_mode == "exact":
        compiled = re.compile(r"(?m)^\s*" + pat + r"\s*$")
    else:
        compiled = re.compile(pat)

    _tpl_cache[cache_key] = compiled
    return compiled


def select_targets(sec, blocks):
    """根据上下文和选择器选出候选配置块
    system -> 返回 {system: 全局块}
    interface/其他 -> 返回 {视图名: 块文本}
    """
    if sec.context == "system":
        return {"system": blocks[0]} if blocks else {}
    out = {}
    for b in blocks[1:]:
        lines_b = b.splitlines()
        if not lines_b:
            continue
        head = lines_b[0]
        for sel in sec.selector:
            pat = re.escape(sel).replace(r"\*", ".*")
            full_pat = rf"{re.escape(sec.context)}\s+{pat}\s*$"
            if re.match(full_pat, head):
                parts = head.split(None, 1)
                name_part = parts[1] if len(parts) > 1 else head
                out[name_part] = b
                break
    return out


def pass_preconds(block, pre):
    """前提条件过滤: include全部命中 AND exclude全部不命中"""
    pre = pre or {}
    for p in pre.get("include", []):
        if not re.search(p, block):
            return False
    for p in pre.get("exclude", []):
        if re.search(p, block):
            return False
    return True
