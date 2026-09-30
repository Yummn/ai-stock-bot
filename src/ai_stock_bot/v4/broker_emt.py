"""Local-only access to the broker's EMT simulation server."""

from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from uuid import UUID

from .schema import ASSETS


ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / ".tools" / "python310" / "python.exe"
WORKER = ROOT / "scripts" / "emt_snapshot_worker.py"
ORDER_WORKER = ROOT / "scripts" / "emt_test_order_worker.py"
SDK_LIB = ROOT / ".tools" / "emt_api_python" / "emt_api_python" / "lib" / "windows" / "vnemttrader.pyd"
SIMULATION_IP = "61.152.230.41"
SIMULATION_PORT = "19088"
SIMULATION_ORDINARY_ACCOUNT = "REDACTED_LOCAL_ACCOUNT"
_snapshot_lock = threading.Lock()
_used_order_requests: set[str] = set()


def available() -> bool:
    return PYTHON.is_file() and WORKER.is_file() and SDK_LIB.is_file()


def order_available() -> bool:
    return available() and ORDER_WORKER.is_file()


def _test_buy_price(symbol: str, price: object) -> Decimal:
    if symbol not in ASSETS or not symbol.endswith(".SH"):
        raise ValueError("仅支持列表中的上海 ETF 仿真测试委托")
    try:
        value = Decimal(str(price))
    except InvalidOperation:
        raise ValueError("限价无效") from None
    if not value.is_finite() or value <= 0 or value > 10 or value.as_tuple().exponent < -3:
        raise ValueError("限价需在 0 至 10 元之间，最多三位小数")
    return value


def submit_test_buy(account: str, password: str, symbol: str, price: object,
                    request_id: str) -> dict:
    if account != SIMULATION_ORDINARY_ACCOUNT:
        raise ValueError("仅允许已核验的普通 EMT API 仿真测试账号")
    if not password:
        raise ValueError("请输入本次使用的仿真密码")
    limit_price = _test_buy_price(symbol, price)
    try:
        if UUID(request_id).version != 4:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("测试委托编号无效") from None
    if not order_available():
        raise ValueError("本机尚未安装 EMT Python 仿真 SDK")
    if not _snapshot_lock.acquire(blocking=False):
        raise ValueError("券商查询或测试委托正在进行，请稍后再试")
    try:
        if request_id in _used_order_requests:
            raise ValueError("这笔测试委托已经尝试提交，请先核对券商订单，不能重复发送")
        _used_order_requests.add(request_id)
        with tempfile.TemporaryDirectory(prefix="stocklab-emt-order-") as directory:
            env = os.environ.copy()
            env.update({
                "EMT_TEST_ENV": "simulation",
                "EMT_TEST_IP": SIMULATION_IP,
                "EMT_TEST_PORT": SIMULATION_PORT,
                "EMT_TEST_ACCOUNT": account,
                "EMT_TEST_PASSWORD": password,
                "EMT_TEST_SYMBOL": symbol,
                "EMT_TEST_PRICE": str(limit_price),
                "EMT_PROBE_LOG_PREFIX": str(Path(directory) / "emt"),
            })
            try:
                result = subprocess.run(
                    [str(PYTHON), str(ORDER_WORKER)], env=env, cwd=ROOT,
                    capture_output=True, text=True, timeout=45, check=False,
                )
            except subprocess.TimeoutExpired:
                raise ValueError("测试委托结果未知；请到券商仿真终端核对订单，不要重复提交") from None
            if result.returncode != 0:
                raise ValueError("测试委托未确认成功；请核对仿真订单后再操作")
            try:
                lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                data = json.loads(lines[-1]) if lines else None
            except json.JSONDecodeError:
                data = None
            if (not isinstance(data, dict) or data.get("environment") != "simulation"
                    or data.get("symbol") != symbol or data.get("side") != "buy"
                    or data.get("quantity") != 100 or data.get("price") != str(limit_price)
                    or not str(data.get("order_id", "")).isdigit()):
                raise ValueError("券商委托回报无法核验；请到仿真终端核对订单")
            return data
    finally:
        _snapshot_lock.release()


def snapshot(account: str, password: str) -> dict:
    if not available():
        raise ValueError("本机尚未安装 EMT Python 仿真 SDK")
    if not _snapshot_lock.acquire(blocking=False):
        raise ValueError("券商查询正在进行，请稍后再试")
    try:
        with tempfile.TemporaryDirectory(prefix="stocklab-emt-") as directory:
            env = os.environ.copy()
            env.update({
                "EMT_TEST_ENV": "simulation",
                "EMT_TEST_IP": SIMULATION_IP,
                "EMT_TEST_PORT": SIMULATION_PORT,
                "EMT_TEST_ACCOUNT": account,
                "EMT_TEST_PASSWORD": password,
                "EMT_PROBE_LOG_PREFIX": str(Path(directory) / "emt"),
            })
            try:
                result = subprocess.run(
                    [str(PYTHON), str(WORKER)],
                    env=env, cwd=ROOT, capture_output=True, text=True,
                    timeout=45, check=False,
                )
            except subprocess.TimeoutExpired:
                raise ValueError("券商仿真查询超时，请稍后重试") from None
            if result.returncode != 0:
                error = result.stderr or ""
                if "Read-only EMT snapshot failed:" in error:
                    code = error.split("Read-only EMT snapshot failed:", 1)[1].strip().splitlines()[0]
                    phase, _, detail = code.partition(":")
                    if phase in {"startup", "sdk_load", "sdk_create", "login", "asset", "positions"}:
                        if phase == "login" and detail == "Simulation login failed":
                            raise ValueError("仿真服务器拒绝登录，请核对 API 测试账号和密码")
                        raise ValueError(f"券商仿真查询在 {phase} 阶段失败（{detail[:80]}）")
                for marker, message in (
                    ("Simulation login failed", "仿真服务器拒绝登录，请核对 API 测试账号和密码"),
                    ("Asset query", "已登录，但资产查询失败"),
                    ("Position query", "已登录，但持仓查询失败"),
                    ("Position pagination", "已登录，但持仓分页结果异常"),
                    ("queryPositionByPage", "已登录，但当前 SDK 的持仓分页接口不可用"),
                    ("KeyError", "已登录，但券商返回的数据字段与预期不一致"),
                    ("TypeError", "已登录，但 SDK 持仓查询参数不匹配"),
                ):
                    if marker in error:
                        raise ValueError(message)
                raise ValueError(f"券商仿真查询进程异常退出（代码 {result.returncode}）")
            try:
                lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                data = json.loads(lines[-1]) if lines else None
            except json.JSONDecodeError:
                raise ValueError("券商返回的数据无法解析") from None
            if not isinstance(data, dict):
                raise ValueError("券商返回的数据无法解析")
            if data.get("environment") != "simulation" or data.get("orders_enabled") is not False:
                raise ValueError("券商响应未通过仿真只读校验")
            return data
    finally:
        _snapshot_lock.release()
