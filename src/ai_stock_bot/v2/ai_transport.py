"""A tool-restricted Codex transport shared by signal review and public research."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def codex_json(settings, prompt, web=False, trace_path=None):
    from ..ai import CodexSignalProvider

    binary = settings.codex_bin if settings.codex_bin and Path(settings.codex_bin).is_file() else shutil.which("codex")
    if not binary:
        raise RuntimeError("未找到 Codex CLI，请先安装并登录")
    command = [binary, "exec", "--json", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "-c", f'web_search="{"live" if web else "disabled"}"']
    for feature in ("plugins", "shell_tool", "unified_exec", "browser_use", "computer_use", "apps", "hooks") + (() if web else ("code_mode_host",)):
        command += ["--disable", feature]
    if settings.codex_model:
        command += ["--model", settings.codex_model]
    command.append("-")
    # Do not expose the project working directory to a web-research agent.
    with tempfile.TemporaryDirectory(prefix="stocklab-ai-") as directory:
        completed = subprocess.run(command, input=prompt, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=max(240, settings.codex_timeout_seconds) if web else settings.codex_timeout_seconds, check=False, cwd=directory)
    if completed.returncode:
        raise RuntimeError(f"Codex 调用失败（退出码 {completed.returncode}）；请检查登录状态与额度")
    if trace_path is not None:
        Path(trace_path).parent.mkdir(parents=True, exist_ok=True)
        Path(trace_path).write_text(completed.stdout, encoding="utf-8")
    events = []
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") in {"web_search", "web_search_call"}:
            events.append(item)
    parser = CodexSignalProvider(codex_bin=binary)
    return parser._extract_payload(completed.stdout), events
