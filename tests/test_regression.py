# -*- coding: utf-8 -*-
"""
tests/test_regression.py — 基线检查引擎回归测试
================================================================
对应 docs/AUDIT-2026-10-21.md 中的审计结论(A01~A18)。
每条用例都标注了对应的缺陷编号, 便于审计对照与后续回归。

运行:
    python tests/test_regression.py          # 全部用例
    python tests/test_regression.py --api    # 仅 API 层用例
    python tests/test_regression.py --core   # 仅引擎层用例

退出码:
    0 = 全部通过
    1 = 存在失败用例

依赖:
    引擎层用例仅需 PyYAML / openpyxl;
    API 层用例需要 fastapi + pydantic (缺失时自动跳过并明确提示)。
================================================================
"""

import os
import sys
import asyncio
import inspect
import yaml

# 项目根目录加入 sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PASSED, FAILED, SKIPPED = [], [], []


def check(fid, name, cond, detail=""):
    """记录一条用例结果"""
    tag = f"[{fid}]"
    if cond:
        PASSED.append(f"{tag} {name}")
        print(f"  [PASS] {tag} {name}")
    else:
        FAILED.append(f"{tag} {name} -> {detail}")
        print(f"  [FAIL] {tag} {name}" + (f"  -> {detail}" if detail else ""))
        # GitHub Actions 只从 stdout 解析 ::error:: 工作流命令, 必须打到 stdout
        # 才能在 PR 上形成注解; 同时写一份到 stderr 便于普通 CI 排查。
        line = f"::error::{tag} {name}" + (f" -> {detail}" if detail else "")
        print(line)
        print(line, file=sys.stderr)


def skip(fid, name, reason):
    SKIPPED.append(f"[{fid}] {name} ({reason})")
    print(f"  [SKIP] [{fid}] {name}  ({reason})")


def _class_annotation_shadowing(cls):
    """复现 Python<3.14 的"类体注解急切求值"语义, 返回被同名成员遮蔽的注解。

    Python 3.14 起(PEP 649)注解默认延迟求值, 因此"类成员名遮蔽内建类型"
    这类问题在本机完全不可见; 但在 Python 3.12/3.13(随包运行时与 Docker
    基础镜像的版本)上会在导入期直接抛
    TypeError: 'function' object is not subscriptable, 导致 Web 服务起不来。

    这里按类体语句顺序重建命名空间并逐个求值注解, 精确复现旧版语义,
    使该类问题在任何解释器上都能被检出。
    """
    import ast
    import textwrap

    try:
        src = textwrap.dedent(inspect.getsource(cls))
        tree = ast.parse(src)
    except (OSError, TypeError, SyntaxError):
        return []

    cnode = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)), None)
    if cnode is None:
        return []

    # 类体名字解析顺序: 类命名空间 -> 模块全局 -> 内建
    ns = dict(vars(sys.modules.get(cls.__module__, object)))
    problems = []
    for stmt in cnode.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            anns = []
            if stmt.returns is not None:
                anns.append(("return", stmt.returns))
            args = (list(stmt.args.posonlyargs) + list(stmt.args.args)
                    + list(stmt.args.kwonlyargs))
            for a in args:
                if a.annotation is not None:
                    anns.append((a.arg, a.annotation))
            for key, ann in anns:
                try:
                    eval(compile(ast.Expression(ann), "<annotation>", "eval"), ns)
                except TypeError as e:
                    problems.append(f"{cls.__name__}.{stmt.name} -> {key}: {e}")
            # 方法在类体中先求值注解, 之后才绑定到类命名空间
            ns[stmt.name] = getattr(cls, stmt.name, None)
        elif isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name):
                    try:
                        ns[t.id] = eval(  # noqa: S307 - 复现类体求值语义
                            compile(ast.Expression(stmt.value), "<value>", "eval"), ns)
                    except Exception:
                        ns[t.id] = None
    return problems


# =============================================================================
# 引擎层用例
# =============================================================================

def run_core_tests():
    from core.config_parser import split_blocks, select_targets, pass_preconds, compile_tpl
    from core.builtin_checks import _in_use_ports, trace_chain, dhcp_deny_chain
    from core.engine import run_all
    from core.rule_loader import load_rules
    import re

    rules = load_rules(os.path.join(_ROOT, "rules.yaml"))
    by_id = lambda rid: [r for r in rules if r.rid == rid]
    VARS = {"ntpServer1": "10.1.1.100", "ntpServer2": "10.1.1.101", "snmpRo": "x",
            "snmpRw": "y", "trapHost": "10.1.1.200", "snmpAcl": "2000"}

    # 真实 VRP 'display current-configuration' 结构: banner + sysname 独占首段,
    # 全局命令与接口视图各自成段
    COMPLIANT = """!Software Version VRP (R) software, Version 8.221
!Copyright (C) 2012-2016 Huawei Technologies Co., Ltd.
!
sysname SW_01
#
info-center loghost 192.168.1.100
#
ntp-service unicast-server 10.1.1.100
#
snmp-agent community read cipher %^%#A%#%#
#
snmp-agent community write cipher %^%#B%#%#
#
snmp-agent sys-info version v2c
#
snmp-agent target-host trap-hostname nms address 10.1.1.200 udp-port 162
#
interface GigabitEthernet0/0/1
 port link-type access
 port default vlan 10
 port-security enable
#
interface GigabitEthernet0/0/2
 port link-type access
 port default vlan 10
 port-security enable
#
"""

    print("\n--- A02 system 上下文必须取到全局命令 ---")
    res = run_all(by_id("SW-NTP-01") + by_id("SW-SNMP-01") + by_id("SW-SNMP-02")
                  + by_id("SW-SNMP-03") + by_id("SW-SNMP-04"),
                  COMPLIANT, VARS, platform="s-series", device_type="交换机")
    check("A02", "合规设备的 NTP/SNMP 规则不产生 findings", not res,
          [(r.rid, f.evidence) for r, fs in res for f in fs])

    res = run_all(by_id("SW-DHCP-01"), COMPLIANT, VARS, platform="s-series", device_type="交换机")
    check("A02", "未启用 dhcp enable 时不产生 findings", not res,
          [(r.rid, f.evidence) for r, fs in res for f in fs])

    DHCP_ON = COMPLIANT.replace("ntp-service unicast-server 10.1.1.100\n#",
                                "ntp-service unicast-server 10.1.1.100\n#\ndhcp enable\n#")
    res = run_all(by_id("SW-DHCP-01"), DHCP_ON, VARS, platform="s-series", device_type="交换机")
    check("A02", "启用 dhcp enable 必须被检出(反向漏报)",
          bool(res) and "dhcp enable" in res[0][1][0].evidence,
          res and res[0][1][0].evidence)

    print("\n--- A03/A04 正则必须按行匹配 ---")
    check("A03", "regex 模式启用 MULTILINE",
          bool(compile_tpl(r"^\s*mac-limit\b", "regex", {}).flags & re.MULTILINE))
    check("A03", "regex 可命中配置块内的非首行",
          compile_tpl(r"^\s*mac-limit\b", "regex", {}).search(
              "interface 10GE1/0/1\n mac-limit maximum 10 action alarm\n") is not None)
    check("A04", "preconds 的 shutdown 排除生效",
          not pass_preconds("interface GE0/0/3\n shutdown\n",
                            {"exclude": [r"^\s*shutdown\s*$"]}))

    CE = """!
sysname CE_01
#
interface 10GE1/0/1
 port link-type access
 port default vlan 10
 mac-limit maximum 10 action alarm
#
"""
    res = run_all(by_id("SW-PS-02"), CE, VARS, platform="ce-series", device_type="交换机")
    check("A03", "合规 CE 接口不产生 findings", not res,
          [(r.rid, f.evidence) for r, fs in res for f in fs])

    PS_CFG = """!
sysname SW_01
#
interface GigabitEthernet0/0/3
 shutdown
#
interface GigabitEthernet0/0/4
 port link-type access
 port default vlan 10
 port-security enable
#
"""
    res = run_all(by_id("SW-PS-01"), PS_CFG, VARS, platform="s-series", device_type="交换机")
    tgts = [f.target for _, fs in res for f in fs]
    check("A04", "shutdown 端口未被 SW-PS-01 标记",
          "GigabitEthernet0/0/3" not in tgts, tgts)

    print("\n--- A01 策略链: 非全局应用不得崩溃 ---")
    CHAIN = """acl number 4000
 rule 5 deny l2-protocol 0x86dd
#
traffic classifier IPV6_DENY
 if-match acl 4000
#
traffic behavior IPV6_DENY
 deny
#
traffic policy IPV6_DENY
 classifier IPV6_DENY behavior IPV6_DENY
#
interface GigabitEthernet0/0/1
 port link-type access
 port default vlan 10
#
interface GigabitEthernet0/0/2
 port link-type access
 port default vlan 10
#
"""
    try:
        ports, _m2t = _in_use_ports(split_blocks(CHAIN))
        check("A01", "_in_use_ports 接受 list 参数", True)
        check("A01", "正确识别在用物理口",
              ports == {"GigabitEthernet0/0/1", "GigabitEthernet0/0/2"}, ports)
    except Exception as e:
        check("A01", "_in_use_ports 接受 list 参数", False, f"{type(e).__name__}: {e}")

    try:
        res = run_all(by_id("SW-IPV6-01"), CHAIN, VARS, platform="s-series", device_type="交换机")
        check("A01", "非全局应用时 run_all 不抛异常", True)
        check("A01", "报出未覆盖端口缺口",
              bool(res) and "缺口端口" in res[0][1][0].evidence,
              res and res[0][1][0].evidence)
    except Exception as e:
        check("A01", "非全局应用时 run_all 不抛异常", False, f"{type(e).__name__}: {e}")

    GLOBAL = CHAIN.replace("#\ninterface GigabitEthernet0/0/1",
                            "traffic-policy IPV6_DENY global inbound\n#\ninterface GigabitEthernet0/0/1")
    res = run_all(by_id("SW-IPV6-01"), GLOBAL, VARS, platform="s-series", device_type="交换机")
    check("A01", "全局应用时判定通过", not res, res and res[0][1][0].evidence)

    print("\n--- A14 DHCP 双向 deny 识别 ---")
    only67 = "acl 3000\n rule 5 deny udp destination-port eq 67\n"
    only68 = "acl 3000\n rule 10 deny udp destination-port eq 68\n"
    both = "acl 3000\n rule 5 deny udp destination-port eq 67\n rule 10 deny udp destination-port eq 68\n"
    check("A14", "仅覆盖 67 时不满足",
          dhcp_deny_chain(only67, split_blocks(only67), {})[0] == "FAIL")
    check("A14", "仅覆盖 68 时不满足",
          dhcp_deny_chain(only68, split_blocks(only68), {})[0] == "FAIL")
    _st, det, _p = dhcp_deny_chain(both, split_blocks(both), {})
    check("A14", "67+68 同时覆盖时 ACL 被正确命中",
          "未发现含目标deny规则的ACL" not in det, det)

    print("\n--- A16 随包样例: 结果必须与样例头部声明一致 ---")
    sample_path = os.path.join(_ROOT, "snapshots", "10.10.1.11.cfg")
    if not os.path.exists(sample_path):
        skip("A16", "随包样例自检", "样例文件不存在")
    else:
        SAMPLE = open(sample_path, encoding="utf-8").read()
        res = run_all(rules, SAMPLE, VARS, platform="s-series", device_type="交换机")
        hits = {r.rid for r, fs in res for f in fs}
        check("A16", "SW-PS-01 被检出", "SW-PS-01" in hits, sorted(hits))
        check("A16", "SW-VLAN1-01 被检出", "SW-VLAN1-01" in hits, sorted(hits))
        check("A16", "SW-NTP-01 / SW-SNMP-01~03 通过",
              not (hits & {"SW-NTP-01", "SW-SNMP-01", "SW-SNMP-02", "SW-SNMP-03"}),
              sorted(hits))
        check("A16", "SW-DHCP-01 通过", "SW-DHCP-01" not in hits, sorted(hits))
        check("A16", "SW-IPV6-01 通过", "SW-IPV6-01" not in hits, sorted(hits))
        check("A16", "SW-DHCP-02 通过", "SW-DHCP-02" not in hits, sorted(hits))
        res = run_all(by_id("SW-VLAN1-01"), SAMPLE, VARS, platform="s-series", device_type="交换机")
        tgts = [f.target for _, fs in res for f in fs]
        check("A16", "VLAN1 检出的 target 为纯接口名",
              tgts == ["GigabitEthernet0/0/24"], tgts)

    print("\n--- A05 抓取超时必须显式失败 ---")
    src = open(os.path.join(_ROOT, "core", "collector.py"), encoding="utf-8").read()
    check("A05", "collector 校验 expect 返回索引",
          "if idx < 0:" in src and "TelnetTimeoutError" in src)

    print("\n--- A15 Excel 汇总列 ---")
    try:
        from openpyxl import load_workbook
        xlsx = os.path.join(_ROOT, "output", "baseline_report.xlsx")
        if not os.path.exists(xlsx):
            skip("A15", "Excel 列映射", "尚无 output/baseline_report.xlsx, 先运行 cli.py --offline")
        else:
            ws = load_workbook(xlsx).active
            rows = list(ws.iter_rows(min_row=2, values_only=True))
            if not rows:
                skip("A15", "Excel 列映射", "报告无数据行")
            else:
                check("A15", "规则ID列非空", all(r[1] for r in rows), rows)
                check("A15", "严重度列非空", all(r[2] for r in rows), rows)
                check("A15", "不合规位置列非空", all(r[3] for r in rows), rows)
                check("A15", "证据列非空", all(r[4] for r in rows), rows)
    except ImportError:
        skip("A15", "Excel 列映射", "openpyxl 不可用")

    print("\n--- A09 凭据卫生 ---")
    dev_path = os.path.join(_ROOT, "devices.csv")
    if os.path.exists(dev_path):
        dev = open(dev_path, encoding="utf-8").read()
        check("A09", "devices.csv 无明文口令", "Huawei@123" not in dev)
    else:
        check("A09", "devices.csv 未入库(gitignore 生效)", True)
    check("A09", "提供 devices.csv.example 模板",
          os.path.exists(os.path.join(_ROOT, "devices.csv.example")))

    print("\n--- A17 向后兼容入口 ---")
    wrapper = open(os.path.join(_ROOT, "vrp_baseline.py"), encoding="utf-8").read()
    check("A17", "vrp_baseline.py 显式指定 utf-8 编码",
          'encoding="utf-8"' in wrapper)


# =============================================================================
# API 层用例
# =============================================================================

def run_api_tests():
    try:
        import fastapi  # noqa: F401
    except ImportError:
        skip("API", "API 层用例", "fastapi 不可用")
        return

    from api.app import app
    from api.routes.profile import update_variables, toggle_rule, update_profile, get_profile
    from api.schemas import VariableUpdate, ProfileUpdate, RuleToggle
    from api.routes.tasks import download_report
    from api.store import task_store
    from api.task_manager import report_dir
    import api.app as appmod

    PROFILE = os.path.join(_ROOT, "profile.yaml")
    # 用二进制备份/还原: 文本模式会做换行符转换, 还原后文件不再是原始字节,
    # 会在工作区留下无意义的换行符差异。
    backup = open(PROFILE, "rb").read()
    before_vars = yaml.safe_load(backup)["variables"]
    try:
        print("\n--- A11 profile 回写必须保留注释 ---")
        r = asyncio.run(update_variables(VariableUpdate(variables={"ntpServer1": "10.88.88.88"})))
        after = open(PROFILE, encoding="utf-8").read()
        check("A11", "变量值已更新", r.get("ntpServer1") == "10.88.88.88", r)
        check("A11", "注释数量未减少", after.count("#") == backup.count(b"#"),
              (backup.count(b"#"), after.count("#")))
        check("A11", "中文注释原样保留", "第一NTP服务器IP" in after)
        check("A11", "其它变量未受影响",
              r.get("snmpRo") == before_vars["snmpRo"], r.get("snmpRo"))

        r = asyncio.run(toggle_rule("SW-SNMP-04", RuleToggle(enabled=False)))
        after2 = open(PROFILE, encoding="utf-8").read()
        check("A11", "规则开关已更新", r["enabled"] is False, r)
        check("A11", "开关段注释保留", "suggest级" in after2)
        check("A11", "其它开关未变",
              asyncio.run(get_profile())["rules"]["SW-NTP-01"] is True)

        r = asyncio.run(update_profile(ProfileUpdate(variables={"ntpServer1": "10.77.77.77"})))
        # 部分更新必须合并: 整段替换会删掉未提交的变量, 导致后续任务 KeyError
        check("A11", "部分更新不删除其它变量",
              set(before_vars) <= set(r["variables"]) and
              r["variables"]["ntpServer1"] == "10.77.77.77", r["variables"])
        check("A11", "rules 段不受影响", "SW-IPV6-01: true" in after2)
    finally:
        with open(PROFILE, "wb") as f:
            f.write(backup)

    print("\n--- A10 令牌鉴权 ---")

    class FakeURL:
        path = "/api/devices"

    class FakeReq:
        url = FakeURL()
        headers = {}
        query_params = {}

    async def call_next(req):
        return ("OK", 200)

    appmod._API_TOKEN = "secret123"
    try:
        resp = asyncio.run(appmod.api_token_guard(FakeReq(), call_next))
        check("A10", "无令牌返回 401", getattr(resp, "status_code", None) == 401, resp)
        FakeReq.headers = {"X-API-Token": "wrong"}
        resp = asyncio.run(appmod.api_token_guard(FakeReq(), call_next))
        check("A10", "错误令牌返回 401", getattr(resp, "status_code", None) == 401, resp)
        FakeReq.headers = {"X-API-Token": "secret123"}
        resp = asyncio.run(appmod.api_token_guard(FakeReq(), call_next))
        check("A10", "正确令牌放行", resp == ("OK", 200), resp)
        FakeReq.headers = {}
        FakeReq.query_params = {"token": "secret123"}
        FakeReq.url.path = "/api/sse/abc123"
        resp = asyncio.run(appmod.api_token_guard(FakeReq(), call_next))
        check("A10", "SSE 查询参数方式放行", resp == ("OK", 200), resp)
        FakeReq.url.path = "/api/devices"
        resp = asyncio.run(appmod.api_token_guard(FakeReq(), call_next))
        check("A10", "普通接口不接受查询参数令牌",
              getattr(resp, "status_code", None) == 401, resp)
        FakeReq.url.path = "/"
        FakeReq.query_params = {}
        resp = asyncio.run(appmod.api_token_guard(FakeReq(), call_next))
        check("A10", "非 /api 路径不受限制", resp == ("OK", 200), resp)
    finally:
        appmod._API_TOKEN = ""

    print("\n--- A06 报告按 task_id 隔离 ---")
    for tid in ("tA", "tB"):
        task_store.create(tid, total=1)
        task_store.update(tid, status="done")
        d = report_dir(tid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "remediation_report.md"), "w", encoding="utf-8") as f:
            f.write(f"# report {tid}\n")
    fa = asyncio.run(download_report("tA", "md"))
    fb = asyncio.run(download_report("tB", "md"))
    check("A06", "任务A 下载指向自己的报告", "tA" in fa.path, fa.path)
    check("A06", "任务B 下载指向自己的报告", "tB" in fb.path, fb.path)
    check("A06", "两个任务路径不同", fa.path != fb.path)
    check("A08", "报告落在 output/ 目录下",
          "output" in report_dir("tA").replace("\\", "/"), report_dir("tA"))

    task_store.create("tC", total=0)
    task_store.update("tC", status="done")
    try:
        asyncio.run(download_report("tC", "md"))
        check("A06", "报告缺失返回 404", False, "未抛出异常")
    except Exception as e:
        check("A06", "报告缺失返回 404", getattr(e, "status_code", None) == 404,
              f"{type(e).__name__}: {e}")

    print("\n--- A19 类体注解不得被同名成员遮蔽内建类型 ---")
    # Python 3.14 起注解默认延迟求值(PEP 649), "类成员遮蔽内建类型" 这类
    # 问题在本机完全不可见, 但在 Python 3.12/3.13(随包运行时与 Docker
    # 基础镜像的版本)上会在导入期直接抛
    # TypeError: 'function' object is not subscriptable, 导致 Web 服务起不来。
    # 这里用类命名空间强制求值注解, 复现旧版语义, 使问题在任何解释器上都能检出。
    import importlib
    import inspect
    import pkgutil

    shadowed = []
    import_fail = []
    for pkg_name in ("api", "core"):
        try:
            pkg = importlib.import_module(pkg_name)
        except Exception as e:
            # 包无法导入本身就是严重问题(例如 A19 这类导入期错误), 不能静默跳过
            import_fail.append(f"import {pkg_name} 失败: {type(e).__name__}: {e}")
            continue
        mods = [pkg]
        for m in pkgutil.walk_packages(pkg.__path__, pkg_name + "."):
            try:
                mods.append(importlib.import_module(m.name))
            except Exception as e:
                import_fail.append(f"import {m.name} 失败: {type(e).__name__}: {e}")
                continue
        for mod in mods:
            for cname, cls in vars(mod).items():
                if not inspect.isclass(cls) or getattr(cls, "__module__", "") != mod.__name__:
                    continue
                shadowed.extend(_class_annotation_shadowing(cls))
    check("A19", "类体注解未被同名成员遮蔽内建类型", not shadowed, shadowed)
    check("A19", "所有一方模块均可正常导入", not import_fail, import_fail)

    print("\n--- A12 /api 路由可达性 ---")
    sc = {"type": "http", "method": "GET", "path": "/api", "root_path": "", "headers": []}
    first = next((type(r).__name__ for r in app.router.routes
                  if r.matches(sc)[0].name == "FULL"), None)
    check("A12", "/api 命中 APIRoute 而非静态挂载", first == "APIRoute", first)
    check("A10", "默认未启用跨域中间件", "CORS" not in str(app.user_middleware))


# =============================================================================

def main():
    args = set(sys.argv[1:])
    only_core = "--core" in args
    only_api = "--api" in args

    print("=" * 64)
    print("VRP 基线检查工具 回归测试")
    print(f"项目根目录: {_ROOT}")
    print("=" * 64)

    try:
        if not only_api:
            run_core_tests()
        if not only_core:
            run_api_tests()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        traceback.print_exc()
        # 逐行输出为注解: GitHub 只从 stdout 解析 ::error::, 逐行输出才能在
        # PR 上直接看到出错的文件与行号。
        print("::error::回归测试异常终止, 完整调用栈:")
        for l in tb.rstrip().splitlines():
            print(f"::error::{l}")
        _write_summary()
        return 1

    print("\n" + "=" * 64)
    total = len(PASSED) + len(FAILED)
    print(f"通过 {len(PASSED)} / {total}")
    if SKIPPED:
        print(f"跳过 {len(SKIPPED)}:")
        for s in SKIPPED:
            print("  -", s)
    if FAILED:
        print(f"失败 {len(FAILED)}:")
        for f in FAILED:
            print("  -", f)
        _write_summary()
        return 1
    print("全部通过")
    _write_summary()
    return 0


def _write_summary():
    """把结果写入 GitHub Actions job summary(存在该环境变量时)"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "## 回归测试结果",
        "",
        f"- 通过: **{len(PASSED)}**",
        f"- 失败: **{len(FAILED)}**",
        f"- 跳过: **{len(SKIPPED)}**",
        "",
    ]
    if FAILED:
        lines += ["### 失败用例", ""]
        lines += [f"- {f}" for f in FAILED]
        lines.append("")
    if SKIPPED:
        lines += ["### 跳过用例", ""]
        lines += [f"- {s}" for s in SKIPPED]
        lines.append("")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
