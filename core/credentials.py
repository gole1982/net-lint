"""
core.credentials — 设备凭证安全解析
================================================================
resolve_credentials: 从环境变量解析设备凭证
  CSV中password字段为空时, 从环境变量 VRP_PASS_<IP> 读取
  (IP中点号替换为下划线, 如 10.10.1.11 → VRP_PASS_10_10_1_11)

load_devices: 从CSV文件加载设备清单
  自动跳过以 '#' 开头的注释行
================================================================
"""

import csv
import os


def resolve_credentials(dev):
    """从环境变量解析设备凭证, CSV中password字段为空时从环境变量读取
    环境变量命名: VRP_PASS_<IP> (点号替换为下划线)
    例如 10.10.1.11 -> VRP_PASS_10_10_1_11
    """
    dev = dict(dev)  # 浅拷贝, 不修改原始数据
    ip = dev.get("ip", "")
    if not dev.get("password"):
        env_key = "VRP_PASS_" + ip.replace(".", "_")
        env_pass = os.environ.get(env_key)
        if env_pass:
            dev["password"] = env_pass
        else:
            dev["password"] = ""
    return dev


def load_devices(path):
    """从CSV文件加载设备清单
    自动跳过以 '#' 开头的注释行
    自动解析凭证(从环境变量补充密码)
    返回 [dict] 列表
    """
    with open(path, encoding="utf-8-sig") as f:
        rows = [line for line in f if not line.lstrip().startswith("#")]
    devices = list(csv.DictReader(rows))
    return [resolve_credentials(d) for d in devices]
