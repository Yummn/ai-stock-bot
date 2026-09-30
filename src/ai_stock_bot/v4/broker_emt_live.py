"""Explicitly configured, local-only, read-only access to a live EMT account."""

import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading

from . import broker_emt


CONFIG_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AiStock" / "emt-live-readonly.json"
WORKER = broker_emt.ROOT / "scripts" / "emt_live_snapshot_worker.py"
_read_lock = threading.Lock()
_TEST_ACCOUNTS = {
    "REDACTED_LOCAL_ACCOUNT", "REDACTED_LOCAL_ACCOUNT", "REDACTED_LOCAL_ACCOUNT",
    "REDACTED_LOCAL_ACCOUNT", "REDACTED_LOCAL_ACCOUNT", "REDACTED_LOCAL_ACCOUNT", "REDACTED_LOCAL_ACCOUNT",
}


def _profile() -> dict:
    try:
        if CONFIG_FILE.stat().st_size > 4096:
            raise ValueError("实盘只读配置文件过大")
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("尚未取得券商确认的 EMT 实盘接入信息") from None
    except (OSError, json.JSONDecodeError):
        raise ValueError("实盘只读配置无法读取") from None
    if not isinstance(data, dict) or data.get("environment") != "live_readonly":
        raise ValueError("实盘只读配置的环境标记无效")
    if data.get("broker_approved") is not True:
        raise ValueError("尚未记录券商已开通 EMT 实盘 API 权限")
    if any(key.lower() in {"password", "passwd", "pwd", "secret", "token"} for key in data):
        raise ValueError("实盘配置不得保存密码或令牌")
    account = data.get("account")
    if not isinstance(account, str) or not re.fullmatch(r"[0-9]{6,24}", account):
        raise ValueError("实盘资金账号格式无效")
    if account in _TEST_ACCOUNTS:
        raise ValueError("测试账号不能配置为实盘账号")
    host = data.get("host")
    if not isinstance(host, str):
        raise ValueError("实盘服务器 IP 无效")
    try:
        address = ipaddress.IPv4Address(host)
    except (ipaddress.AddressValueError, TypeError):
        raise ValueError("实盘服务器 IP 无效") from None
    if (str(address) == broker_emt.SIMULATION_IP or address.is_loopback
            or address.is_link_local or address.is_multicast or address.is_unspecified):
        raise ValueError("实盘只读配置不能指向测试或本机地址")
    port = data.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("实盘服务器端口无效")
    return {"account": account, "host": str(address), "port": port}


def readiness() -> dict:
    try:
        profile = _profile()
    except ValueError as exc:
        return {"configured": False, "reason": str(exc), "account_masked": None,
                "sdk_available": broker_emt.available() and WORKER.is_file(),
                "orders_enabled": False}
    account = profile["account"]
    return {"configured": True, "reason": None,
            "account_masked": "*" * (len(account) - 4) + account[-4:],
            "sdk_available": broker_emt.available() and WORKER.is_file(),
            "orders_enabled": False}


def snapshot() -> dict:
    profile = _profile()
    if not broker_emt.available() or not WORKER.is_file():
        raise ValueError("本机尚未安装 EMT Python SDK")
    if not _read_lock.acquire(blocking=False):
        raise ValueError("实盘只读查询正在进行，请稍后再试")
    try:
        with tempfile.TemporaryDirectory(prefix="stocklab-emt-live-readonly-") as directory:
            env = os.environ.copy()
            for key in list(env):
                if key.startswith("EMT_TEST_") or key in {"EMT_LIVE_PASSWORD", "EMT_LIVE_SECRET"}:
                    env.pop(key)
            env.update({
                "EMT_LIVE_READONLY": "1",
                "EMT_LIVE_IP": profile["host"],
                "EMT_LIVE_PORT": str(profile["port"]),
                "EMT_LIVE_ACCOUNT": profile["account"],
                "EMT_PROBE_LOG_PREFIX": str(Path(directory) / "emt"),
            })
            try:
                result = subprocess.run(
                    [str(broker_emt.PYTHON), str(WORKER)], env=env,
                    cwd=broker_emt.ROOT, capture_output=True, text=True,
                    timeout=60, check=False,
                )
            except subprocess.TimeoutExpired:
                raise ValueError("实盘只读查询超时，未发送任何订单") from None
            if result.returncode != 0:
                raise ValueError("实盘只读连接失败；请核对券商开通状态、白名单 IP 和服务器信息")
            try:
                lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                data = json.loads(lines[-1]) if lines else None
            except json.JSONDecodeError:
                data = None
            expected_mask = "*" * (len(profile["account"]) - 4) + profile["account"][-4:]
            if (not isinstance(data, dict) or data.get("environment") != "live_readonly"
                    or data.get("account_masked") != expected_mask
                    or data.get("orders_enabled") is not False
                    or not isinstance(data.get("positions"), list)
                    or not isinstance(data.get("open_orders"), list)):
                raise ValueError("实盘只读回报未通过账户与环境校验")
            return data
    finally:
        _read_lock.release()
