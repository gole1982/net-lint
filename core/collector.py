"""
core.collector — 设备配置采集 (Telnet + 离线快照)
================================================================
fetch_telnet:  通过Telnet登录设备抓取 display current-configuration
fetch_offline: 从 snapshots/ 目录读取已有快照
fetch:         采集入口, 根据 offline 参数选择模式

异常类:
  TelnetConnectionError / TelnetAuthError / TelnetTimeoutError

VRP适配特性:
  - Telnet认证自适应: 区分AAA模式(用户名+密码)与密码认证模式(仅密码)
  - AAA回退检测: 输入用户名后若未收到密码提示, 回车重试最多3次
  - 登录提示符: 以 ':\\s*$' 匹配(假设banner不含冒号)
  - 设备名提示符: <[A-Za-z_]+> (仅letter和下划线)
================================================================
"""

import os
import re

# 抓取 display current-configuration 的等待上限(秒)
CONFIG_TIMEOUT = 120

# Python 3.12 (runtime) 自带 telnetlib; 3.13+ 移除后离线模式不受影响
try:
    import telnetlib
except ImportError:
    telnetlib = None


class TelnetConnectionError(RuntimeError):
    """Telnet连接失败"""
    pass


class TelnetAuthError(RuntimeError):
    """Telnet认证失败"""
    pass


class TelnetTimeoutError(RuntimeError):
    """Telnet操作超时"""
    pass


def _telnet_login(tn, dev):
    """VRP Telnet登录, 自动适配AAA模式和密码模式

    策略:
      1. 等待以 ':' 结尾的提示符(用户名或密码), banner不含':'
      2. 先尝试输入用户名; 若接下来收到 ':' 则是密码提示, 输入密码
      3. 若输入用户名后未收到 ':' (密码认证模式), 回车直到出现登录提示符
      4. 成功后返回VRP用户视图提示符 <设备名>
    """
    host = dev["ip"]
    username = dev.get("username", "")
    password = dev.get("password", "")

    # 设备名只含 letter 和下划线
    prompt_re = rb"<[A-Za-z_]+>"

    # ---- 第一步: 等待登录提示符 (Username: 或 Password:) ----
    idx, match_obj, data = tn.expect([rb":\s*$"], timeout=15)
    if idx < 0:
        raise TelnetAuthError(f"{host}: 未检测到登录提示符(缺少':')")

    # 判断当前提示是用户名还是密码
    recv_text = data.decode("utf-8", errors="ignore").lower()

    if "password" in recv_text or "密码" in recv_text:
        # 密码认证模式: 直接输入密码
        tn.write(password.encode("utf-8") + b"\r\n")
    else:
        # AAA模式: 先输入用户名
        tn.write(username.encode("utf-8") + b"\r\n")

        # 等待下一个提示符: 可能是 Password: 或直接是VRP提示符
        idx2, match2, data2 = tn.expect(
            [rb":\s*$", prompt_re], timeout=15
        )
        if idx2 == 0:
            # 收到 Password: 提示, 输入密码
            tn.write(password.encode("utf-8") + b"\r\n")
        elif idx2 == 1:
            # 直接进入VRP提示符, 无需密码
            return match2.group(0)
        else:
            # 超时: 可能是密码认证模式但输入了用户名
            for _ in range(3):
                tn.write(b"\r\n")
                idx3, match3, _ = tn.expect(
                    [rb":\s*$", prompt_re], timeout=5
                )
                if idx3 == 0:
                    tn.write(password.encode("utf-8") + b"\r\n")
                    break
                elif idx3 == 1:
                    return match3.group(0)
            else:
                raise TelnetAuthError(
                    f"{host}: AAA回退检测失败, 无法确定认证流程"
                )

    # 等待VRP提示符出现
    idx, match_obj, _ = tn.expect([prompt_re], timeout=20)
    if idx < 0:
        raise TelnetAuthError(f"{host}: 登录失败, 未检测到VRP提示符")

    return match_obj.group(0)


def fetch_telnet(dev):
    """通过Telnet登录设备抓取display current-configuration
    返回配置文本字符串
    """
    if telnetlib is None:
        raise TelnetConnectionError(
            "当前Python不支持telnetlib (>=3.13), 请使用runtime或离线模式"
        )
    host = dev["ip"]
    try:
        tn = telnetlib.Telnet(host, 23, timeout=15)
    except OSError as e:
        raise TelnetConnectionError(f"{host}: Telnet连接失败 ({e})")

    try:
        # 登录(自动适配AAA/密码模式)
        prompt_bytes = _telnet_login(tn, dev)
        prompt_re = re.escape(prompt_bytes.decode("utf-8", errors="ignore"))

        # 关闭分屏
        tn.write(b"screen-length 0 temporary\r\n")
        tn.expect([prompt_re.encode()], timeout=10)

        # 抓取配置
        tn.write(b"display current-configuration\r\n")
        idx, _, data = tn.expect([prompt_re.encode()], timeout=CONFIG_TIMEOUT)
        if idx < 0:
            # telnetlib.expect 超时不会抛异常, 只返回 (-1, None, 已收到的部分数据)。
            # 若不显式判定, 会拿截断配置去做基线判定并写入快照, 导致缺失部分被
            # 误判为"合规", 且离线重跑会复现同样的错误结果。
            raise TelnetTimeoutError(
                f"{host}: 抓取配置超时({CONFIG_TIMEOUT}s), "
                f"配置可能不完整(已收 {len(data)} 字节)"
            )

        tn.write(b"quit\r\n")
        tn.close()
    except (TelnetAuthError, TelnetConnectionError):
        tn.close()
        raise
    except TimeoutError as e:
        tn.close()
        raise TelnetTimeoutError(f"{host}: Telnet操作超时 ({e})")
    except Exception:
        tn.close()
        raise

    # 解析: 去命令回显、去末尾提示符、去空行
    raw = data.decode("utf-8", errors="ignore").splitlines()
    lines = []
    skip_cmd = "display current-configuration"
    prompt_clean_re = re.compile(r"<[A-Za-z_]+>")
    for line in raw:
        stripped = line.rstrip()
        if stripped == skip_cmd or stripped.startswith(skip_cmd + " "):
            continue
        if stripped and prompt_clean_re.match(stripped):
            continue
        lines.append(stripped)

    return "\n".join(lines)


def fetch_offline(dev, snapshots_dir):
    """从 snapshots/ 目录读取已有快照
    返回配置文本字符串
    """
    snap_path = os.path.join(snapshots_dir, f'{dev["ip"]}.cfg')
    if not os.path.exists(snap_path):
        raise FileNotFoundError(
            f"离线模式下缺少快照: {snap_path}"
        )
    with open(snap_path, encoding="utf-8") as f:
        return f.read()


def fetch(dev, *, offline=False, snapshots_dir=None):
    """采集入口: 根据 offline 参数选择在线Telnet或离线快照
    返回配置文本字符串
    """
    if offline:
        return fetch_offline(dev, snapshots_dir or "snapshots")
    return fetch_telnet(dev)
