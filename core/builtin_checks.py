"""
core.builtin_checks — 内置链式检查 (策略链/VLAN1等复杂逻辑)
================================================================
ipv6_deny_chain:  IPv6流量策略阻断(ACL 0x86dd 五段链追踪)
dhcp_deny_chain:  DHCP流量策略阻断(UDP 67/68 双向检查)
vlan1_check:       VLAN1禁用检查
BUILTINS:          内置检查函数注册表

内置检查返回 (status, detail, targets) 或
(status, detail, targets, per_target):
  per_target[target] = {"evidence": str, "fix": [命令行]}
这样每个接口可以携带自己的证据与类型化整改命令。
================================================================
"""

import re

# 物理/逻辑端口名 (不含子接口)。PHYS 是宽松的前缀匹配, 这里需要精确定位。
_PORT_RE = re.compile(
    r"^(?:GigabitEthernet|XGigabitEthernet|MultiGE|Ethernet"
    r"|10GE|25GE|40GE|100GE|GE)\S*$"
)
_TRUNK_RE = re.compile(r"^Eth-Trunk\d+$")
_SUBIF_RE = re.compile(r"\.\d+$")
# 不作为二层端口检查的接口类型
_L3_RE = re.compile(r"^(?:Vlanif|LoopBack|Tunnel|NULL|Nve)\S*", re.IGNORECASE)


def _acl_blocks(cfg):
    """从配置文本中提取 {acl标识: acl段}

    支持数字 ACL (acl 3000) 与命名 ACL (acl name FOO / acl name FOO 3999)。
    命名 ACL 同时以名称与编号注册, 便于流分类用任一种方式引用。
    """
    out = {}
    for part in re.split(r"(?m)^(?=acl )", cfg):
        m = re.match(r"acl (?:number )?(\d{4})\b", part)
        if m:
            out[m.group(1)] = part
            continue
        m = re.match(r"acl name (\S+)(?:\s+(\d{4}))?", part)
        if m:
            out[m.group(1)] = part
            if m.group(2):
                out[m.group(2)] = part
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
    """返回 (在用端口集合, 成员->Eth-Trunk映射)

    Eth-Trunk 成员口不单独计入: 成员口的 VLAN/策略继承自 Eth-Trunk,
    若把它当作独立 access 口会误报; 由 Eth-Trunk 段代表。
    """
    ports, m2t = set(), {}
    for body in blocks:
        name = _view_name(body)
        if not (_PORT_RE.match(name) or _TRUNK_RE.match(name)):
            continue
        if _SUBIF_RE.search(name):
            continue
        if re.search(r"(?m)^\s*shutdown\s*$", body):
            continue
        t = re.search(r"(?m)^\s+eth-trunk (\d+)", body)
        if t and _PORT_RE.match(name):
            m2t[name] = f"Eth-Trunk{t.group(1)}"
            continue
        ports.add(name)
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

    # 流分类: 提取 classifier名 -> 引用的ACL集合
    # 同一 classifier 可以有多个 if-match acl, 只取第一条会误判"未引用"。
    cls_map = {}
    for m in re.finditer(
        r"(?ms)^traffic classifier (\S+).*?(?=^traffic |^#|\Z)", cfg
    ):
        refs = set(
            re.findall(r"(?m)^\s*if-match acl (?:name )?(\S+)", m.group(0))
        )
        cls_map[m.group(1)] = refs
    cls_ok = {c for c, refs in cls_map.items() if refs & set(acls)}
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
        if not (_PORT_RE.match(name) or _TRUNK_RE.match(name)):
            continue
        m = re.search(
            r"(?m)^\s+traffic-policy (\S+) inbound", body
        )
        if m and m.group(1) in pols:
            applied_names.add(name)
            # 策略应用在 Eth-Trunk 上时, 成员口继承该策略
            for mem, trk in m2t.items():
                if trk == name:
                    applied_names.add(mem)

    if not ports:
        # 没有任何在用端口时, "覆盖全部" 是无意义的: 不能判 PASS。
        return (
            "WARN",
            "未发现任何在用二层端口, 无法确认策略覆盖范围",
            None,
        )

    missing = sorted(ports - applied_names)
    if missing:
        return (
            "WARN",
            f"策略链完整但未全局应用, 缺口端口({len(missing)}个): "
            + ", ".join(missing[:10])
            + (f"... 共{len(missing)}个" if len(missing) > 10 else ""),
            missing,
        )
    return "PASS", "策略链完整且覆盖全部在用端口", None


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

def _vlans_from_text(text):
    """'all' / '2 to 4094' / '10 20' -> VLAN 集合"""
    if re.search(r"\ball\b", text, re.IGNORECASE):
        return set(range(1, 4095))
    out = set()
    for m in re.finditer(r"(\d+)\s*to\s*(\d+)|(\d+)", text):
        if m.group(1):
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            out.update(range(lo, hi + 1))
        else:
            out.add(int(m.group(3)))
    return out


def _apply_vlan_cmds(body, cmd_regex):
    """按行序应用 allow-pass/tagged/untagged 的增删

    返回 (VLAN 集合, 是否显式配置过)。配置里的 undo 行会抵消前面的放行,
    只匹配 "allow-pass vlan 1" 而忽略 "undo ..." 是此前的误报来源。
    """
    vlans = set()
    configured = False
    pat = re.compile(r"^\s*(undo\s+)?" + cmd_regex + r"\s+(.+?)\s*$")
    for line in body.splitlines():
        m = pat.match(line)
        if not m:
            continue
        configured = True
        vals = _vlans_from_text(m.group(2))
        if m.group(1):
            vlans -= vals
        else:
            vlans |= vals
    return vlans, configured


def _link_type(body):
    m = re.search(r"(?m)^\s*port\s+link-type\s+(\w+)", body)
    if m:
        return m.group(1).lower()
    if re.search(r"(?m)^\s+port\s+hybrid\s", body):
        return "hybrid"
    if re.search(r"(?m)^\s+port\s+trunk\s+", body):
        return "trunk"
    # VRP 默认链路类型为 access
    return "access"


def _pvid(body, ltype):
    if ltype == "trunk":
        m = re.search(r"(?m)^\s*port\s+trunk\s+pvid\s+vlan\s+(\d+)", body)
    elif ltype == "hybrid":
        m = re.search(r"(?m)^\s*port\s+hybrid\s+pvid\s+vlan\s+(\d+)", body)
    else:
        m = re.search(r"(?m)^\s*port\s+default\s+vlan\s+(\d+)", body)
    return int(m.group(1)) if m else 1


def _vlan1_port_state(body):
    """计算单个二层端口的 VLAN1 承载情况

    返回 dict(ltype, pvid, allowed/trunk_configured, tagged, untagged, reasons)
    """
    ltype = _link_type(body)
    pvid = _pvid(body, ltype)
    state = {
        "ltype": ltype,
        "pvid": pvid,
        "trunk_allowed": set(),
        "trunk_configured": False,
        "tagged": set(),
        "untagged": set(),
        "reasons": [],
    }
    if ltype == "trunk":
        allowed, configured = _apply_vlan_cmds(
            body, r"port\s+trunk\s+allow-pass\s+vlan"
        )
        state["trunk_allowed"] = allowed
        state["trunk_configured"] = configured
        if not configured:
            state["reasons"].append("trunk未显式裁剪, 默认放行VLAN1")
        elif 1 in allowed:
            state["reasons"].append("trunk放行VLAN1")
        if pvid == 1:
            state["reasons"].append("trunk pvid=1")
    elif ltype == "hybrid":
        tagged, _ = _apply_vlan_cmds(body, r"port\s+hybrid\s+tagged\s+vlan")
        untagged, configured = _apply_vlan_cmds(
            body, r"port\s+hybrid\s+untagged\s+vlan"
        )
        state["tagged"] = tagged
        state["untagged"] = untagged
        if pvid == 1:
            state["reasons"].append("hybrid pvid=1")
        if 1 in tagged:
            state["reasons"].append("hybrid tagged放行VLAN1")
        if 1 in untagged:
            state["reasons"].append("hybrid untagged放行VLAN1")
    else:
        if pvid == 1:
            state["reasons"].append("默认携带VLAN1(pvid=1)")
    return state


def _vlan1_fix(name, state):
    """按端口类型生成整改命令"""
    ltype = state["ltype"]
    pvid = state["pvid"]
    cmds = [f"interface {name}"]
    if ltype == "trunk":
        if not state["trunk_configured"]:
            cmds.append(" port trunk allow-pass vlan 2 to 4094")
        elif 1 in state["trunk_allowed"]:
            cmds.append(" undo port trunk allow-pass vlan 1")
        if pvid == 1:
            cmds.append(" port trunk pvid vlan 2")
    elif ltype == "hybrid":
        if pvid == 1:
            cmds.append(" port hybrid pvid vlan 2")
        if 1 in state["tagged"]:
            cmds.append(" undo port hybrid tagged vlan 1")
        if 1 in state["untagged"] or (pvid == 1 and not state["untagged"]):
            cmds.append(" undo port hybrid untagged vlan 1")
    else:
        if pvid == 1:
            cmds.append(" port default vlan 2")
    if len(cmds) == 1:
        cmds.append(" port default vlan 2")
    return cmds


def vlan1_check(cfg, blocks, variables):
    per_target = {}
    interface_blocks = 0

    for b in blocks:
        head = b.splitlines()[0] if b.splitlines() else ""
        if not head.startswith("interface"):
            continue
        interface_blocks += 1
        name = _view_name(b)

        if name == "Vlanif1":
            per_target[name] = {
                "evidence": "存在 interface Vlanif1 (三层管理接口, 仍使用VLAN1网段)",
                "fix": [],
            }
            continue

        if _L3_RE.match(name) or _SUBIF_RE.search(name):
            # 三层接口与子接口不承载二层 VLAN1, 不是本规则的检查对象
            continue

        if not (_PORT_RE.match(name) or _TRUNK_RE.match(name)):
            continue
        if re.search(r"(?m)^\s*shutdown\s*$", b):
            continue
        # Eth-Trunk 成员口的 VLAN 由 Eth-Trunk 决定, 不单独判定
        if _PORT_RE.match(name) and re.search(r"(?m)^\s+eth-trunk\s+\d+", b):
            continue

        state = _vlan1_port_state(b)
        if state["reasons"]:
            per_target[name] = {
                "evidence": "; ".join(state["reasons"]),
                "fix": _vlan1_fix(name, state),
            }

    if per_target:
        detail = "; ".join(
            f"{name}: {info['evidence']}" for name, info in per_target.items()
        )
        return "FAIL", detail, sorted(per_target), per_target

    if interface_blocks == 0:
        return (
            "WARN",
            "配置中未发现任何 interface 段, 无法判定 VLAN1 承载情况",
            ["system"],
            {"system": {"evidence": "配置中未发现任何 interface 段", "fix": []}},
        )

    return "PASS", "无Vlanif1, 在用端口均未承载VLAN1", None


# 注册表
BUILTINS = {
    "ipv6_deny_chain": ipv6_deny_chain,
    "dhcp_deny_chain": dhcp_deny_chain,
    "vlan1_check": vlan1_check,
}
