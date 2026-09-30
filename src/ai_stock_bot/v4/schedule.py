from __future__ import annotations

import datetime as dt

from ..v2.market import TZ
from ..v2.schema import AppConfig, JobRequest
from .schema import Plan
from .service import settings


def enqueue_daily(store, now=None):
    now = now or dt.datetime.now(TZ)
    plan = Plan.model_validate(settings(store)["plan"])
    if not plan.auto_update or now.weekday() >= 5:
        return
    if plan.research_interval != "daily":
        slots = (((10, 10), (10, 40), (11, 10), (11, 40), (13, 40), (14, 10), (14, 40), (15, 10))
                 if plan.research_interval == "30m" else ((10, 40), (11, 40), (14, 10), (15, 10)))
        eligible = [f"{hour:02d}:{minute:02d}" for hour, minute in slots
                    if (now.hour, now.minute) >= (hour, minute)]
        if not eligible:
            return
        key = f"{now.date()} {plan.research_interval} {eligible[-1]}"
    else:
        if now.hour < 16:
            return
        key = str(now.date())
    with store.connect() as c:
        if c.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            return
    if store.meta("portfolio_scheduled_date") == key:
        return
    config = AppConfig.model_validate(store.settings()["config"])
    request = JobRequest(kind="portfolio_update", investment=plan, config=config)
    store.create_job(request.model_dump())
    store.set_meta("portfolio_scheduled_date", key)
    if plan.research_interval == "daily":
        intraday = JobRequest(kind="intraday_refresh", investment=plan, config=config)
        store.create_job(intraday.model_dump())
