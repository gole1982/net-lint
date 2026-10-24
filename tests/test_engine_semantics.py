"""引擎语义回归测试

覆盖审计中确认的“错误结论”类缺陷:
  - VLAN1 的 access/trunk/hybrid/子接口/Eth-Trunk/undo 判定
  - platform/device_type 不匹配 → 零规则(未检查) 而不是“全部合规”
  - 策略链: Eth-Trunk 成员继承、空端口集合、多 if-match/命名 ACL
  - 整改命令按接口逐条渲染
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from core.builtin_checks import vlan1_check
from core.config_parser import split_blocks
from core.engine import run_all, run_all_detailed, not_checked_reason
from core.reporter import build_report_data, render_fix
from core.rule_loader import load_profile, load_rules

RULES = {r.rid: r for r in load_rules(str(ROOT / "rules.yaml"))}
VARS = load_profile(str(ROOT / "profile.yaml"))["variables"]


def cfg_of(*blocks):
    return "\n#\n".join(blocks) + "\n"


def run(rid, cfg, platform="s-series", device_type="交换机"):
    return run_all([RULES[rid]], cfg, VARS,
                   platform=platform, device_type=device_type)


def vlan1(cfg):
    return vlan1_check(cfg, split_blocks(cfg), VARS)


# ---------------- VLAN1 ----------------

def test_access_default_vlan_is_reported():
    st, _d, _pos, per = vlan1(cfg_of("interface GigabitEthernet0/0/1"))
    assert st == "FAIL"
    assert "GigabitEthernet0/0/1" in per
    assert any("port default vlan" in line for line in per["GigabitEthernet0/0/1"]["fix"])


def test_access_port_in_vlan10_is_clean():
    cfg = cfg_of("interface GigabitEthernet0/0/1\n port link-type access\n port default vlan 10")
    assert vlan1(cfg)[0] == "PASS"


def test_trunk_allow_all_is_reported():
    cfg = cfg_of("interface GigabitEthernet0/0/1\n port link-type trunk\n port trunk allow-pass vlan all")
    st, _d, _p, per = vlan1(cfg)
    assert st == "FAIL"
    assert "trunk放行VLAN1" in per["GigabitEthernet0/0/1"]["evidence"]


def test_trunk_pruned_vlan1_is_clean():
    # 旧实现会误报“默认携带VLAN1”, 实际已裁剪且 pvid != 1
    cfg = cfg_of(
        "interface GigabitEthernet0/0/1\n port link-type trunk\n"
        " port trunk allow-pass vlan 2 to 4094\n port trunk pvid vlan 999"
    )
    assert vlan1(cfg)[0] == "PASS"


def test_trunk_undo_allow_pass_vlan1_is_clean():
    # 旧实现只匹配 allow-pass 行, 忽略紧随其后的 undo, 因此误报
    cfg = cfg_of(
        "interface GigabitEthernet0/0/1\n port link-type trunk\n"
        " port trunk allow-pass vlan 1\n port trunk pvid vlan 999\n"
        " undo port trunk allow-pass vlan 1"
    )
    assert vlan1(cfg)[0] == "PASS"


def test_hybrid_untagged_vlan1_is_reported():
    cfg = cfg_of(
        "interface GigabitEthernet0/0/1\n port link-type hybrid\n"
        " port hybrid pvid vlan 10\n port hybrid untagged vlan 1 10"
    )
    st, _d, _p, per = vlan1(cfg)
    assert st == "FAIL"
    assert "hybrid untagged" in per["GigabitEthernet0/0/1"]["evidence"]


def test_hybrid_vlan10_is_clean():
    cfg = cfg_of(
        "interface GigabitEthernet0/0/1\n port link-type hybrid\n"
        " port hybrid pvid vlan 10\n port hybrid untagged vlan 10"
    )
    assert vlan1(cfg)[0] == "PASS"


def test_subinterface_is_not_checked():
    cfg = cfg_of(
        "interface GigabitEthernet0/0/4.100\n vlan-type dot1q 100\n"
        " ip address 10.0.100.1 255.255.255.0"
    )
    assert vlan1(cfg)[0] == "PASS"


def test_vlanif1_is_reported():
    cfg = cfg_of("interface Vlanif1\n ip address 10.0.0.1 255.255.255.0")
    st, _d, _p, per = vlan1(cfg)
    assert st == "FAIL"
    assert "Vlanif1" in per


def test_eth_trunk_member_inherits_trunk_config():
    # 成员口没有自己的 VLAN 配置, 不能按 access 默认 pvid=1 误报
    cfg = cfg_of(
        "interface Eth-Trunk1\n port link-type trunk\n"
        " port trunk allow-pass vlan 2 to 4094\n port trunk pvid vlan 999",
        "interface GigabitEthernet0/0/1\n eth-trunk 1",
        "interface GigabitEthernet0/0/2\n eth-trunk 1",
    )
    assert vlan1(cfg)[0] == "PASS"


def test_no_interface_block_is_warn_not_pass():
    st, _d, _pos, per = vlan1("sysname SW\n")
    assert st == "WARN"
    assert per["system"]["fix"] == []


# ---------------- 零规则 / 未检查 ----------------

def test_platform_mismatch_returns_zero_rules_and_stats():
    cfg = cfg_of("interface GigabitEthernet0/0/1\n port link-type access\n port default vlan 10")
    res, stats = run_all_detailed(
        list(RULES.values()), cfg, VARS, platform="Switch", device_type="交换机"
    )
    assert res == []
    assert stats["applied"] == 0
    assert stats["skipped_platform"] > 0
    assert "未执行" in not_checked_reason(stats)


def test_device_type_mismatch_marks_not_checked():
    cfg = cfg_of("interface GigabitEthernet0/0/1\n port link-type access\n port default vlan 10")
    res, stats = run_all_detailed(
        list(RULES.values()), cfg, VARS, platform="s-series", device_type="防火墙"
    )
    assert res == []
    assert stats["applied"] == 0
    assert stats["skipped_device_type"] > 0


def test_no_target_device_is_not_checked_even_if_rules_applied():
    # 只有全局配置、没有任何接口: 接口类规则执行了但无目标
    cfg = "sysname SW\n"
    res, stats = run_all_detailed(
        list(RULES.values()), cfg, VARS, platform="s-series", device_type="交换机"
    )
    assert stats["applied"] > 0
    assert stats["evaluated"] > 0  # 内置检查与 system 段仍会执行
    assert stats["no_target"] > 0


def test_report_keeps_not_checked_device():
    report = build_report_data(
        {"10.0.0.1": []},
        [{"ip": "10.0.0.1", "name": "SW"}],
        VARS,
        device_results={"10.0.0.1": {
            "status": "not_checked",
            "message": "平台不匹配",
            "checked_rules": 0,
            "total_rules": 12,
        }},
    )
    assert report["summary"]["total_unchecked"] == 1
    dev = report["devices"][0]
    assert dev["unchecked"] == "平台不匹配"
    assert dev["findings"] == []


# ---------------- 策略链 ----------------

def chain(*blocks):
    return cfg_of(
        "acl 4000\n rule 5 deny l2-protocol 0x86dd",
        "traffic classifier C1 operator or\n if-match acl 3999\n if-match acl 4000",
        "acl 3999\n rule 5 permit",
        "traffic behavior B1\n deny",
        "traffic policy P1\n classifier C1 behavior B1",
        *blocks,
    )


def test_multi_if_match_acl_is_recognised():
    assert run("SW-IPV6-01", chain("traffic-policy P1 global inbound")) == []


def test_named_acl_is_recognised():
    cfg = cfg_of(
        "acl name V6 3999\n rule 5 deny l2-protocol 0x86dd",
        "traffic classifier C1\n if-match acl name V6",
        "traffic behavior B1\n deny",
        "traffic policy P1\n classifier C1 behavior B1",
        "traffic-policy P1 global inbound",
    )
    assert run("SW-IPV6-01", cfg) == []


def test_policy_on_eth_trunk_covers_members():
    cfg = chain(
        "interface Eth-Trunk1\n port link-type trunk\n traffic-policy P1 inbound",
        "interface GigabitEthernet0/0/1\n eth-trunk 1",
        "interface GigabitEthernet0/0/2\n eth-trunk 1",
    )
    assert run("SW-IPV6-01", cfg) == []


def test_no_active_port_is_warn_not_pass():
    cfg = chain("interface GigabitEthernet0/0/1\n shutdown")
    res = run("SW-IPV6-01", cfg)
    assert res and "未发现任何在用二层端口" in res[0][1][0].evidence


def test_ntp_with_source_is_compliant():
    cfg = "ntp-service unicast-server 10.1.1.100 source 10.10.1.11\n"
    assert run("SW-NTP-01", cfg) == []


# ---------------- 整改命令渲染 ----------------

def test_render_fix_emits_one_block_per_interface():
    fix = "interface ${if}\n port-security enable"
    out = render_fix(fix, "GE0/0/1; GE0/0/2", {})
    assert out == (
        "interface GE0/0/1\n port-security enable\n"
        "interface GE0/0/2\n port-security enable"
    )


def test_vlan1_fix_is_type_aware():
    assert vlan1(cfg_of(
        "interface GigabitEthernet0/0/1\n port link-type access\n port default vlan 10"
    ))[0] == "PASS"
    _st, _d, _p, per = vlan1(cfg_of("interface GigabitEthernet0/0/2"))
    assert per["GigabitEthernet0/0/2"]["fix"][1].strip() == "port default vlan 2"
