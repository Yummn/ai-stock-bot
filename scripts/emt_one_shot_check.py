"""One-shot loopback-only handoff for an EMT simulation read-only check."""

from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
from urllib.parse import parse_qs


ROOT = Path(__file__).resolve().parents[1]
PYTHON310 = ROOT / ".tools" / "python310" / "python.exe"
PROBE = ROOT / "scripts" / "emt_python_readonly_probe.py"
TOKEN = secrets.token_urlsafe(24)
HOST = "127.0.0.1"
SIMULATION_IP = "61.152.230.41"
SIMULATION_PORT = "19088"
SAFE_PREFIXES = (
    "EMT API version:",
    "Trader and quote extensions loaded",
    "Simulation API login succeeded",
    "Simulation API login failed, code",
    "Asset query request failed, code",
    "Asset query failed, code",
    "Asset query succeeded",
    "Asset query did not succeed",
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path != f"/{TOKEN}":
            return self.reply(404, "Not found")
        self.reply(
            200,
            "<meta charset='utf-8'><title>EMT simulation check</title>"
            "<h1>EMT simulation read-only check</h1>"
            "<form method='post'>"
            "<label>Account <input name='account' autocomplete='off'></label>"
            "<label>Password <input name='password' type='password' autocomplete='off'></label>"
            "<button type='submit'>Check connection</button></form>",
        )

    def do_POST(self):
        if self.path != f"/{TOKEN}":
            return self.reply(404, "Not found")
        origin = self.headers.get("Origin")
        expected_origin = f"http://{HOST}:{self.server.server_port}"
        if self.headers.get("Host") != f"{HOST}:{self.server.server_port}":
            return self.reply(403, "Invalid host")
        if origin not in (None, expected_origin, "null"):
            return self.reply(403, "Invalid origin")
        if self.headers.get("Content-Type", "").split(";")[0] != "application/x-www-form-urlencoded":
            return self.reply(415, "Invalid content type")
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > 2048:
            return self.reply(413, "Invalid form size")
        values = parse_qs(self.rfile.read(size).decode("utf-8"))
        account = values.get("account", [""])[0].strip()
        password = values.get("password", [""])[0]
        if not account.isdigit() or not 1 <= len(account) <= 20 or len(password) > 100:
            return self.reply(400, "Invalid account or password")

        with tempfile.TemporaryDirectory(prefix="emt-sim-check-") as folder:
            env = os.environ.copy()
            env.update(
                EMT_TEST_ENV="simulation",
                EMT_TEST_IP=SIMULATION_IP,
                EMT_TEST_PORT=SIMULATION_PORT,
                EMT_TEST_ACCOUNT=account,
                EMT_TEST_PASSWORD=password,
                EMT_PROBE_LOG_PREFIX=str(Path(folder) / "sdk"),
            )
            try:
                result = subprocess.run(
                    [str(PYTHON310), str(PROBE), "probe"],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=35,
                    check=False,
                )
                lines = [
                    line for line in result.stdout.splitlines()
                    if line.startswith(SAFE_PREFIXES)
                ]
                if not lines:
                    lines = [f"SDK check exited with code {result.returncode}"]
                message = "\n".join(lines)
                ok = result.returncode == 0
            except subprocess.TimeoutExpired:
                message = "SDK check timed out"
                ok = False

        self.reply(
            200 if ok else 502,
            "<meta charset='utf-8'><title>EMT simulation check</title>"
            f"<h1>{'Check passed' if ok else 'Check failed'}</h1>"
            f"<pre>{escape(message)}</pre>",
        )
        threading.Thread(target=self.server.shutdown, daemon=True).start()


if __name__ == "__main__":
    with HTTPServer((HOST, 0), Handler) as server:
        print(f"http://{HOST}:{server.server_port}/{TOKEN}", flush=True)
        server.serve_forever()
