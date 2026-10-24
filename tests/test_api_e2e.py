"""API 端到端回归测试

覆盖审计确认的阻断项:
  - api.app 可导入 (store 的类体注解不再被 list() 遮蔽)
  - POST /api/devices 不再因重复关键字 500
  - 检查任务能真正跑完 (as_completed 迭代修复)
  - /results 可 JSON 序列化 (Check/Finding 先转 DTO)
  - 零规则设备标记为 not_checked
  - 失败任务落 failed 终态, 不永久 running
  - 非 ASCII 令牌返回 401 而不是 500
"""

import csv
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
from fastapi.testclient import TestClient

import api.app as app_module
import api.store as store_module
from api.app import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """设备落盘重定向到临时文件, 避免测试改写仓库里的 devices.csv。"""
    monkeypatch.setattr(store_module.device_store, "_persist_path",
                        str(tmp_path / "devices.csv"))
    store_module.device_store.load_from_csv([])
    yield


def _csv_text(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=["type", "name", "ip", "platform", "protocol", "username", "password"]
    )
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _import(client, rows):
    data = _csv_text(rows).encode("utf-8")
    return client.post(
        "/api/devices/import",
        files={"file": ("devices.csv", data, "text/csv")},
    )


def _wait(client, tid, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = client.get(f"/api/tasks/{tid}").json()
        if t["status"] in ("done", "failed"):
            return t
        time.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout}s 内结束: {t}")


def _device(ip="10.10.1.11", dtype="交换机", platform="s-series"):
    return {
        "type": dtype, "name": "接入SW-01", "ip": ip, "platform": platform,
        "protocol": "telnet", "username": "admin", "password": "",
    }


# ---------------- 设备 CRUD ----------------

def test_create_device_does_not_500(client):
    r = client.post("/api/devices", json={
        "name": "新设备", "ip": "10.9.9.9", "platform": "s-series",
        "type": "交换机", "username": "admin",
    })
    assert r.status_code == 200, r.text
    assert r.json()["has_password"] is False
    assert r.json()["password"] == ""

    dup = client.post("/api/devices", json={
        "name": "重复", "ip": "10.9.9.9", "platform": "s-series",
    })
    assert dup.status_code == 409


def test_invalid_ip_is_rejected(client):
    r = client.post("/api/devices", json={
        "name": "坏IP", "ip": "../etc/passwd", "platform": "s-series",
    })
    assert r.status_code == 422


def test_delete_missing_device_returns_404(client):
    assert client.delete("/api/devices/10.8.8.8").status_code == 404


# ---------------- CSV 导入 ----------------

def test_import_csv_ok_and_persisted(client):
    r = _import(client, [_device()])
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    devices = client.get("/api/devices").json()
    assert [d["ip"] for d in devices] == ["10.10.1.11"]


def test_import_csv_rejects_bad_platform(client):
    rows = [_device()]
    rows[0]["platform"] = "huawei-s"
    r = _import(client, rows)
    assert r.status_code == 400
    assert "platform" in r.json()["detail"]


def test_import_csv_rejects_missing_column(client):
    bad = b"name,ip\nSW,10.10.1.11\n"
    r = client.post("/api/devices/import",
                    files={"file": ("devices.csv", bad, "text/csv")})
    assert r.status_code == 400
    assert "缺少列" in r.json()["detail"]


def test_import_csv_supports_gbk(client):
    data = _csv_text([_device()]).encode("gbk")
    r = client.post("/api/devices/import",
                    files={"file": ("devices.csv", data, "text/csv")})
    assert r.status_code == 200, r.text


# ---------------- 任务全链路 ----------------

def test_task_results_are_json_serializable(client):
    assert _import(client, [_device()]).status_code == 200
    r = client.post("/api/tasks", json={"offline": True, "device_ips": ["10.10.1.11"]})
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]

    final = _wait(client, tid)
    assert final["status"] == "done", final

    res = client.get(f"/api/tasks/{tid}/results")
    assert res.status_code == 200, res.text
    payload = res.json()
    dev = payload["results"]["10.10.1.11"]
    assert dev["status"] == "ok"
    assert dev["checked_rules"] > 0
    assert isinstance(dev["findings"], list)
    for f in dev["findings"]:
        assert set(f) >= {"rule_id", "title", "severity", "target", "evidence", "fix"}

    assert client.get(f"/api/tasks/{tid}/download/md").status_code == 200
    assert client.get(f"/api/tasks/{tid}/download/xlsx").status_code == 200


def test_task_with_zero_applicable_rules_is_not_checked(client):
    assert _import(client, [_device(dtype="防火墙")]).status_code == 200
    r = client.post("/api/tasks", json={"offline": True, "device_ips": ["10.10.1.11"]})
    tid = r.json()["task_id"]
    assert _wait(client, tid)["status"] == "done"

    dev = client.get(f"/api/tasks/{tid}/results").json()["results"]["10.10.1.11"]
    assert dev["status"] == "not_checked"
    assert "未执行" in dev["message"]

    md = client.get(f"/api/tasks/{tid}/download/md").text
    assert "未执行检查" in md


def test_failed_task_reaches_failed_state(client, monkeypatch):
    import core.reporter as reporter

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(reporter, "write_report_files", boom)
    assert _import(client, [_device()]).status_code == 200
    r = client.post("/api/tasks", json={"offline": True, "device_ips": ["10.10.1.11"]})
    tid = r.json()["task_id"]

    final = _wait(client, tid)
    assert final["status"] == "failed", final
    assert "disk full" in final["message"]

    dl = client.get(f"/api/tasks/{tid}/download/md")
    assert dl.status_code == 400
    assert "任务失败" in dl.json()["detail"]


# ---------------- 鉴权 ----------------

def test_non_ascii_token_returns_401_not_500(client, monkeypatch):
    # HTTP 头只能是 ASCII, 非 ASCII 令牌实际上只能出现在查询参数里;
    # 旧实现直接 compare_digest(str, str) 会抛 TypeError -> 500。
    monkeypatch.setattr(app_module, "_API_TOKEN", "中文令牌")
    assert client.get("/api/devices").status_code == 401
    assert client.get("/docs").status_code == 401
    assert client.get("/api/sse/deadbeef?token=错误令牌").status_code == 401


def test_query_token_only_allowed_for_sse(client, monkeypatch):
    monkeypatch.setattr(app_module, "_API_TOKEN", "tok")
    assert client.get("/api/devices?token=tok").status_code == 401
    assert client.get("/api/devices", headers={"X-API-Token": "tok"}).status_code == 200
