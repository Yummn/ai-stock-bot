from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .schema import AppConfig, default_presets, migrate_config


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class ConflictError(Exception):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "stocklab.sqlite3"
        with self.connect() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,name TEXT NOT NULL,config TEXT NOT NULL,updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,progress REAL NOT NULL DEFAULT 0,message TEXT NOT NULL DEFAULT '',request TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,error TEXT,result TEXT,checkpoint TEXT);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,message TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS trials(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,fingerprint TEXT NOT NULL,profile TEXT NOT NULL,round INTEGER NOT NULL,label TEXT NOT NULL,result TEXT NOT NULL,UNIQUE(job_id,fingerprint));
                CREATE TABLE IF NOT EXISTS bars(symbol TEXT NOT NULL,date TEXT NOT NULL,open REAL,high REAL,low REAL,close REAL NOT NULL,volume REAL NOT NULL,source TEXT NOT NULL,adjusted REAL NOT NULL,PRIMARY KEY(symbol,date));
                CREATE TABLE IF NOT EXISTS datasets(symbol TEXT PRIMARY KEY,source TEXT NOT NULL,updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created DESC);
                CREATE INDEX IF NOT EXISTS trials_job ON trials(job_id,id);
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id,id);
            """)
            if c.execute("SELECT 1 FROM meta WHERE key='config'").fetchone() is None:
                c.execute("INSERT INTO meta VALUES('config',?)", (dumps(AppConfig().model_dump()),))
                c.execute("INSERT INTO meta VALUES('revision','1')")
                c.execute("INSERT INTO meta VALUES('initialized','false')")

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=15000")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def meta(self, key, default=None):
        with self.connect() as c:
            row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.connect() as c:
            c.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, dumps(value)))

    def settings(self):
        with self.connect() as c:
            rows = dict(c.execute("SELECT key,value FROM meta WHERE key IN ('config','revision')").fetchall())
        raw = json.loads(rows["config"])
        for profile, preset in default_presets("ai").items():
            raw.get("ai", {}).get(profile, {}).setdefault("ai_prompt", preset.ai_prompt)
        return {"config": AppConfig.model_validate(raw).model_dump(), "revision": int(rows["revision"])}

    def defaults(self):
        saved = self.meta("saved_defaults")
        return {"config": AppConfig.model_validate(saved["config"]).model_dump(), "saved_at": saved["saved_at"], "source_revision": saved["source_revision"]} if saved else {"config": AppConfig().model_dump(), "saved_at": None, "source_revision": None}

    def capture_defaults(self, revision: int):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            current = int(c.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            if revision != current:
                raise ConflictError("设置已变化，请重新加载后再保存默认参数。")
            config = AppConfig.model_validate(json.loads(c.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])).model_dump()
            saved = {"config": config, "saved_at": time.time(), "source_revision": current}
            c.execute("INSERT INTO meta VALUES('saved_defaults',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (dumps(saved),))
        return saved

    def save_settings(self, config: AppConfig, revision: int):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            current = int(c.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            if revision != current:
                raise ConflictError("另一台设备已更新设置，请重新加载后再保存。你的草稿仍保留。")
            c.execute("UPDATE meta SET value=? WHERE key='config'", (dumps(config.model_dump()),))
            c.execute("UPDATE meta SET value=? WHERE key='revision'", (str(current + 1),))
        return self.settings()

    def import_legacy(self, path: Path):
        if self.meta("initialized"):
            return
        notes = []
        if path.exists():
            try:
                with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as old:
                    row = old.execute("SELECT value_json FROM app_state WHERE key='latest'").fetchone()
                if row:
                    state = json.loads(row[0])
                    config, notes = migrate_config(state.get("config", {}))
                    self.set_meta("config", config.model_dump())
                    account = state.get("account", {})
                    positions = {v["symbol"]: {"quantity": v["quantity"], "average_price": v["average_price"], "available": v["quantity"], "price": v.get("market_price", v["average_price"])} for v in state.get("positions", [])}
                    self.set_meta("account", {"cash": account.get("cash", config.risk.starting_cash), "positions": positions, "trades": [], "last_signal_date": None})
                    with self.connect() as c:
                        for symbol, bars in state.get("histories", {}).items():
                            import datetime as dt
                            rows = [(symbol, dt.datetime.fromtimestamp(b["timestamp"], dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat(), None, None, None, b["close"], b.get("volume", 0), "旧版缓存（仅收盘）", b["close"]) for b in bars]
                            c.executemany("INSERT OR IGNORE INTO bars VALUES(?,?,?,?,?,?,?,?,?)", rows)
                    notes.append("已导入旧版设置、模拟账户和收盘缓存；旧回测结果保留在原数据库。")
            except Exception as exc:
                notes.append(f"旧数据导入未完成：{type(exc).__name__}。原数据库未改动。")
        self.set_meta("migration_notes", notes)
        self.set_meta("initialized", True)

    def create_job(self, request):
        job_id, now = uuid.uuid4().hex, time.time()
        with self.connect() as c:
            c.execute("INSERT INTO jobs(id,kind,status,message,request,created,updated) VALUES(?,?,'queued','等待计算',?,?,?)", (job_id, request["kind"], dumps(request), now, now))
        return self.job(job_id)

    def job(self, job_id, detail=False):
        with self.connect() as c:
            columns = "*" if detail else "id,kind,status,progress,message,request,created,updated,error,checkpoint,result IS NOT NULL AS has_result"
            row = c.execute(f"SELECT {columns} FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("任务不存在")
        data = dict(row)
        checkpoint = json.loads(data.pop("checkpoint") or "{}")
        data["result_version"] = checkpoint.get("result_version", 0)
        result = data.pop("result", None)
        request = json.loads(data.pop("request"))
        if request.get("investment") is not None:
            plan = request["investment"]
            data["summary"] = {"name": plan["name"], "engine": "portfolio", "method": plan["method"], "years": request.get("years")}
        else:
            config = request["config"]
            data["summary"] = {"name": config["name"], "engine": config["engine"], "model": config["model"], "profile": config["profile"], "auto_profile": config.get("auto_profile", False), "profile_selection": config.get("profile_selection", "trend" if config.get("auto_profile") else "fixed"), "profiles": request.get("profiles"), "year": request.get("year"), "years": request.get("years"), "period": request.get("period")}
        data["has_result"] = result is not None if detail else bool(data["has_result"])
        if detail:
            data["request"] = request
            data["result"] = json.loads(result) if result else None
        return data

    def jobs(self, limit=30, offset=0, kind=None):
        with self.connect() as c:
            condition = " WHERE kind=?" if kind else ""
            args = [kind] if kind else []
            ids = c.execute("SELECT id FROM jobs" + condition + " ORDER BY created DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
            count = c.execute("SELECT count(*) FROM jobs" + condition, args).fetchone()[0]
        return {"items": [self.job(row[0]) for row in ids], "total": count}

    def update_job(self, job_id, **fields):
        if "result" in fields:
            fields["checkpoint"] = {"result_version": time.time()}
        allowed = {"status", "progress", "message", "error", "result", "checkpoint"}
        fields = {k: dumps(v) if k in {"result", "checkpoint"} and v is not None else v for k, v in fields.items() if k in allowed}
        fields["updated"] = time.time()
        with self.connect() as c:
            c.execute(f"UPDATE jobs SET {','.join(k+'=?' for k in fields)} WHERE id=?", (*fields.values(), job_id))

    def event(self, job_id, message):
        with self.connect() as c:
            c.execute("INSERT INTO events(job_id,message,created) VALUES(?,?,?)", (job_id, str(message), time.time()))

    def events(self, job_id, after=0):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT 100", (job_id, after))]

    def trials(self, job_id):
        with self.connect() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT result FROM trials WHERE job_id=? ORDER BY id", (job_id,))]

    def has_trial(self, job_id, fingerprint):
        with self.connect() as c:
            return c.execute("SELECT 1 FROM trials WHERE job_id=? AND fingerprint=?", (job_id, fingerprint)).fetchone() is not None

    def trial_page(self, job_id, limit=15, offset=0):
        with self.connect() as c:
            count = c.execute("SELECT count(*) FROM trials WHERE job_id=?", (job_id,)).fetchone()[0]
            rows = c.execute("SELECT json_remove(result,'$.config','$.warnings') FROM trials WHERE job_id=? ORDER BY id DESC LIMIT ? OFFSET ?", (job_id, limit, offset)).fetchall()
        return {"total": count, "items": [json.loads(r[0]) for r in rows]}

    def learning_summary(self, job_id, before_round=None):
        best = {}
        with self.connect() as c:
            count, rounds = c.execute("SELECT count(*),coalesce(max(round),0) FROM trials WHERE job_id=?", (job_id,)).fetchone()
            for profile in ("aggressive", "balanced", "conservative", "custom"):
                args = [job_id, profile]
                condition = ""
                if before_round is not None:
                    condition = " AND round<?"
                    args.append(before_round)
                row = c.execute("SELECT result FROM trials WHERE job_id=? AND profile=? AND json_extract(result,'$.complete')=1" + condition + " ORDER BY json_extract(result,'$.score') DESC,id LIMIT 1", args).fetchone()
                if row:
                    best[profile] = json.loads(row[0])
        return {"best": best, "total": count, "rounds": rounds}

    def save_trial(self, job_id, fingerprint, result):
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO trials(job_id,fingerprint,profile,round,label,result) VALUES(?,?,?,?,?,?)", (job_id, fingerprint, result["profile"], result["round"], result["label"], dumps(result)))
