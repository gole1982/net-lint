"""
core.builtin_checks — 内置链式检查 (策略链/VLAN1等复杂逻辑)
================================================================
ipv6_deny_chain:  IPv6流量策略阻断(ACL 0x86dd 五段链追踪)
dhcp_deny_chain:  DHCP流量策略阻断(UDP 67/68 双向检查)
vlan1_check:       VLAN1禁用检查
BUILTINS:          内置检查函数注册表
================================================================
"""

import re

from .models import PHYS


def _acl_blocks(cfg):
    """从配置文本中提取 {acl号: acl段}"""
    out = {}
    for part in re.split(r"(?m)^(?=acl )", cfg):
        m = re.match(r"acl (?:number )?(\d{4})", part)
        if m:
            out[m.group(1)] = part
    return out


def _view_name(block):
    """从配置块首行解析视图名
    'interface GigabitEthernet0/0/1' -> 'GigabitEthernet0/0/1'
    'traffic classifier c1'            -> 'c1'
    """
    lines = block.splitlines()
    if not lines:
        return ""
    parts = lines[0].split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def _in_use_ports(blocks):
    """返回 (在用物理口集合, 成员->Eth-Trunk映射)
    blocks: split_blocks() 返回的列表, 每项为一个配置块文本
    """
    ports, m2t = set(), {}
    for body in blocks:
        name = _view_name(body)
        if PHYS.match(name) and not re.search(r"(?m)^\s*shutdown\s*$", body):
            ports.add(name)
            t = re.search(r"(?m)^\s+eth-trunk (\d+)", body)
            if t:
                m2t[name] = f"Eth-Trunk{t.group(1)}"
    return ports, m2t


def trace_chain(cfg, blocks, rule_hit_fn):
    """
    通用五段链追踪器:
      ACL -> 流分类 -> 流行为(deny) -> 流策略 -> 应用位置
    参数:
      rule_hit_fn(acl_block_text) -> bool
    返回: (status, detail_str, missing_ports_or_None)
    """
    acls = {n: b for n, b in _acl_blocks(cfg).items() if rule_hit_fn(b)}
    if not acls:
        return "FAIL", "未发现含目标deny规则的ACL", None

    # 流分类: 提取 classifier名 -> 引用的ACL号
    cls_map = {}
    for m in re.finditer(
        r"(?ms)^traffic classifier (\S+).*?(?=^traffic |^#|\Z)", cfg
    ):
        a = re.search(r"if-match acl (\d{4})", m.group(0))
        cls_map[m.group(1)] = a.group(1) if a else None
    cls_ok = {c for c, a in cls_map.items() if a and a in acls}
    if not cls_ok:
        return (
            "FAIL",
            f"ACL {sorted(acls)} 未被任何流分类引用",
            None,
        )

    # 流行为: 含 deny 的行为集合
    behs = set()
    for m in re.finditer(
        r"(?ms)^traffic behavior (\S+).*?(?=^traffic |^#|\Z)", cfg
    ):
        if re.search(r"(?m)^\s+deny\s*$", m.group(0)):
            behs.add(m.group(1))
    if not behs:
        return "FAIL", "流分类存在, 但无含 deny 的流行为", None

    # 流策略: 绑定了合规分类+行为的策略集合
    pols = set()
    for m in re.finditer(
        r"(?ms)^traffic policy (\S+).*?(?=^traffic |^#|\Z)", cfg
    ):
        bindings = re.findall(
            r"classifier (\S+) behavior (\S+)", m.group(0)
        )
        if any(c in cls_ok and b in behs for c, b in bindings):
            pols.add(m.group(1))
    if not pols:
        return "FAIL", "无流策略完成 流分类+deny行为 绑定", None

    # 应用位置
    global_applied = False
    for m in re.finditer(
        r"(?m)^traffic-policy (\S+) global inbound", cfg
    ):
        if m.group(1) in pols:
            global_applied = True
            break
    if global_applied:
        return "PASS", "策略链完整且已全局应用(inbound)", None

    # 逐接口检查覆盖度
    ports, m2t = _in_use_ports(blocks)
    applied_names = set()
    for body in blocks:
        name = _view_name(body)
        if not PHYS.match(name):
            continue
        m = re.search(
            r"(?m)^\s+traffic-policy (\S+) inbound", body
        )
        if m and m.group(1) in pols:
            applied_names.add(name)
            for mem, trk in m2t.items():
                if trk == name:
                    applied_names.add(mem)

    missing = sorted(set(ports) - applied_names)
    if missing:
        return (
            "WARN",
            f"策略链完整但未全局应用, 缺口端口({len(missing)}个): "
            + ", ".join(missing[:10])
            + (f"... 共{len(missing)}个" if len(missing) > 10 else ""),
            missing,
        )
    return "PASS", "策略链完整且覆盖全部在用物理口", None


# --- IPv6阻断 ---
def ipv6_deny_chain(cfg, blocks, variables):
    st, detail, pos = trace_chain(
        cfg, blocks, lambda b: bool(re.search(r"0x86[dD][dD]", b))
    )
    return st, ("IPv6阻断: " + detail), pos


# --- DHCP阻断(DORA双向: UDP 67+68) ---
def dhcp_deny_chain(cfg, blocks, variables):
    def hit(body):
        # [^;\r\n]* 不能跨行: 贪婪的 [^;]* 会把同一 ACL 内所有规则一次吞掉,
        # 只捕获到最后一个端口, 导致 67/68 双向 deny 永远判定为不满足。
        found = set(
            re.findall(
                r"deny udp[^;\r\n]*destination-port eq (67|68|bootps|bootpc)",
                body,
            )
        )
        norm = {"67" if p in ("67", "bootps") else "68" for p in found}
        return norm == {"67", "68"}

    st, detail, pos = trace_chain(cfg, blocks, hit)
    return st, ("DHCP阻断(需覆盖UDP67/68双向): " + detail), pos


# --- VLAN1禁用 ---
def vlan1_check(cfg, blocks, variables):
    evid = []      # 人类可读的证据描述
    targets = []   # 涉及的接口名(用于定位与整改命令渲染)
    for b in blocks:
        head = b.splitlines()[0] if b.splitlines() else ""
        if not head.startswith("interface"):
            continue
        name = _view_name(b)

        if name == "Vlanif1":
            evid.append("存在 interface Vlanif1")
            targets.append(name)
            continue

        if not PHYS.match(name) or re.search(r"(?m)^\s*shutdown\s*$", b):
            continue

        # trunk口
        if "port link-type trunk" in b:
            m = re.search(r"(?m)^\s+port trunk allow-pass vlan (.+)$", b)
            if not m:
                evid.append(f"{name}: trunk未显式裁剪, 默认放行VLAN1")
                targets.append(name)
            elif _vlan_contains_1(m.group(1)):
                evid.append(f"{name}: trunk放行VLAN1")
                targets.append(name)
            p = re.search(r"(?m)^\s+port trunk pvid vlan (\d+)", b)
            if p and p.group(1) == "1":
                evid.append(f"{name}: trunk pvid=1")
                targets.append(name)
        # access/hybrid/默认
        elif re.search(r"port (default vlan|hybrid pvid vlan) 1\s*$", b, re.MULTILINE):
            evid.append(f"{name}: 划入VLAN1或pvid=1")
            targets.append(name)
        elif "port link-type" not in b and "port hybrid pvid vlan" not in b:
            evid.append(f"{name}: 默认携带VLAN1(pvid=1)")
            targets.append(name)

    if evid:
        return "FAIL", "; ".join(evid), targets
    return "PASS", "无Vlanif1, 在用端口均未承载VLAN1", None


def _vlan_contains_1(text):
    """判断vlan范围是否包含1"""
    if re.search(r"\ball\b", text):
        return True
    for m in re.finditer(r"(\d+)\s*to\s*(\d+)|(\d+)", text):
        lo, hi = (int(m.group(1)), int(m.group(2))) if m.group(1) \
                 else (int(m.group(3)),) * 2
        if lo <= 1 <= hi:
            return True
    return False


# 注册表
BUILTINS = {
    "ipv6_deny_chain": ipv6_deny_chain,
    "dhcp_deny_chain": dhcp_deny_chain,
    "vlan1_check": vlan1_check,
}
