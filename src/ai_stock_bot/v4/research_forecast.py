"""Production adapter for exact, versioned signal recipes validated offline."""
import hashlib
import json
from dataclasses import asdict

import pandas as pd

from .research import build_panel, model_specs
from .model_recipes import RECIPE_LABELS, resolved_spec


class ResearchForecasts:
    classification = False
    horizon = 20

    def __init__(self, store, histories, progress=lambda *args: None, recipe="hgb20_regularized", tuning=None):
        self.histories, self.progress = histories, progress
        self.recipe, self.spec = recipe, resolved_spec(recipe, tuning)
        self.horizon = self.spec.horizon
        identity = hashlib.sha256()
        for symbol, frame in sorted(histories.items()):
            identity.update(symbol.encode())
            identity.update(frame.to_json(orient="split", date_format="iso", double_precision=15).encode())
        self.identity = identity.hexdigest()
        namespace = "hgb20-regularized-v1" if recipe == "hgb20_regularized" else f"recipe-{recipe}-v1"
        if tuning:
            namespace += "-" + hashlib.sha256(json.dumps(asdict(self.spec), sort_keys=True).encode()).hexdigest()[:16]
        self.directory = store.root/"portfolio_models"/namespace/self.identity
        self.month = None
        self.predictions, self.report, self.reports = {}, None, []

    def at(self, date):
        month = str(date)[:7]
        if month != self.month:
            cutoff = pd.Timestamp(date).to_period("M").start_time
            self.progress(15, f"{RECIPE_LABELS[self.recipe]} 滚动训练 {month}")
            panel = build_panel(self.histories, self.spec,
                self.directory, self.identity, start=cutoff, end=cutoff)
            self.predictions = {(r["date"], r["symbol"]): r["value"] for r in panel["rows"]}
            self.report = {**panel["reports"][0], "model": self.spec.model, "recipe": self.recipe,
                "parameters": asdict(self.spec),
                "reason": "时间验证通过" if panel["reports"][0]["status"] == "model" else "时间验证未通过，使用趋势组合",
                "target": f"未来{self.horizon}个交易日净持有收益",
                "feature_count": 24 if self.spec.extended else 11, "forecast_is_estimate": True}
            self.reports.append(self.report)
            self.month = month
        return {s: self.predictions[(str(pd.Timestamp(date).date()), s)] for s in self.histories
                if (str(pd.Timestamp(date).date()), s) in self.predictions}, self.report
