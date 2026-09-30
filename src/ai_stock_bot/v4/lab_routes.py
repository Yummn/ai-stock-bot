from typing import Literal

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from ..v2.schema import JobRequest
from ..v2.store import ConflictError
from .lab_schema import Study
from .model_recipes import ModelTuning, TUNABLE_FIELDS, recipe_catalog
from .profile_library import view
from .schema import ASSETS, fingerprint, Plan
from .service import settings


class StudyStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: Literal["active", "conservative", "balanced", "aggressive", "custom"] = "active"
    base_plan_key: str = Field(min_length=24, max_length=24)
    study: Study


def register(app, store, lock):
    @app.get("/api/v2/research-workspace")
    def workspace():
        current = settings(store)
        return {"settings": current, "plan_key": fingerprint(Plan.model_validate(current["plan"])),
                "profiles": view(store, current), "recipes": recipe_catalog(), "assets": ASSETS,
                "fields": TUNABLE_FIELDS, "parameter_schema": ModelTuning.model_json_schema()["properties"]}

    @app.post("/api/v2/research-workspace/run", status_code=202)
    def start(payload: StudyStart):
        with lock:
            current = settings(store)
            plan = Plan.model_validate(current["plan"] if payload.slot == "active" else view(store, current)["entries"][payload.slot]["plan"])
            if payload.base_plan_key != fingerprint(plan):
                raise ConflictError("所选投资计划已改变，请重新加载研究基线")
            with store.connect() as connection:
                count = connection.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
                active = connection.execute("SELECT 1 FROM jobs WHERE kind='portfolio_research' AND status IN ('queued','running')").fetchone()
            if count >= 5 or active:
                raise ValueError("已有研究任务运行或队列已满，请等待或停止任务")
            request = JobRequest(kind="portfolio_research", investment=plan, study=payload.study,
                years=list(range(payload.study.start_year, payload.study.end_year + 1)))
            return store.create_job(request.model_dump())

    @app.get("/api/v2/research-workspace/draft")
    def draft(job_id: str = Query(pattern=r"^[a-f0-9]{32}$"), candidate_id: str = Query(pattern=r"^[a-f0-9]{24}$")):
        from .lab import candidate_draft
        return candidate_draft(store, job_id, candidate_id)
