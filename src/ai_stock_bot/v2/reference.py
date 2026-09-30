"""Causal evidence from the three standard profiles of every ML model."""
from __future__ import annotations

import numpy as np

from .estimators import probabilities
from .features import features
from .schema import MODEL_NAMES
from .strategies import decide


REFERENCE_PROFILES = ("aggressive", "balanced", "conservative")


class MLReferences:
    def __init__(self, store, histories, config, cutoff, progress, symbols=None):
        self.store, self.histories, self.config = store, histories, config
        self.cutoff, self.progress = cutoff, progress
        self.symbols = [s for s in (symbols if symbols is not None else config.symbols) if s in histories]
        self.predictions = {}
        self.feature_frames = {}
        self.prepared = False

    def prepare(self):
        if self.prepared:
            return
        # Fitted estimators stay in memory only while shared across symbols.
        for model in MODEL_NAMES:
            for profile in REFERENCE_PROFILES:
                cfg = self.config.model_copy(deep=True)
                cfg.engine, cfg.model, cfg.profile = "ml", model, profile
                preset = cfg.preset()
                workspace = {"samples": {}, "estimators": {}}
                for symbol in self.symbols:
                    try:
                        self.predictions[(symbol, model, profile)] = probabilities(
                            self.store, self.histories, cfg, preset, self.cutoff,
                            symbol, profile, self.progress, workspace=workspace,
                        )
                    except Exception as exc:
                        self.predictions[(symbol, model, profile)] = (
                            None, {"mode": "unavailable", "error": type(exc).__name__},
                        )
                workspace.clear()
        self.prepared = True

    def at(self, symbol, date, scene):
        self.prepare()
        if symbol not in self.feature_frames:
            self.feature_frames[symbol] = features(self.histories[symbol])
        frame = self.histories[symbol]
        index = frame.index.get_loc(date)
        row = self.feature_frames[symbol].loc[date]
        entries = []
        for model in MODEL_NAMES:
            for profile in REFERENCE_PROFILES:
                preset = self.config.models[model][profile]
                prediction, report = self.predictions[(symbol, model, profile)]
                entry = {
                    "model": model, "profile": profile, "status": "unavailable",
                    "training_cutoff": str(self.cutoff.date()),
                    "training_mode": report.get("mode"),
                    "requested_training_mode": preset.training_mode,
                    "samples": report.get("samples", 0),
                    "target": preset.target, "label_basis": preset.label_basis,
                    "horizon": preset.horizon,
                }
                vector = prediction[index] if prediction is not None else None
                if vector is None or not np.isfinite(vector).all():
                    entry["reason"] = "模型参考不可用；未将本地规则视为模型预测"
                else:
                    cfg = self.config.model_copy(update={"engine": "ml", "model": model, "profile": profile})
                    decision = decide(row, vector, cfg, profile, scene)
                    entry.update(
                        status="ok", side=decision["side"],
                        opportunity_score=decision["opportunity_score"],
                        class_scores=vector.tolist(),
                        scope_fallback=report.get("mode") != preset.training_mode,
                        buy_threshold=decision["effective_buy_threshold"],
                        sell_threshold=decision["effective_sell_threshold"],
                    )
                entries.append(entry)
        counts = {side: sum(e.get("side") == side for e in entries) for side in ("buy", "sell", "hold")}
        available = sum(e["status"] == "ok" for e in entries)
        return {
            "status": "ok" if available == len(entries) else "partial" if available else "unavailable",
            "as_of": str(date.date()), "training_cutoff": str(self.cutoff.date()),
            "summary": {"available": available, "unavailable": len(entries) - available, **counts},
            "references": entries,
        }
