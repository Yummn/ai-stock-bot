from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .model_recipes import ModelTuning, model_specs, recipe_parameters, resolved_spec


ASSETS = {
    "510300.SH": {"name": "沪深300ETF", "group": "中国股票", "t0": False, "limit": .10},
    "510500.SH": {"name": "中证500ETF", "group": "中国股票", "t0": False, "limit": .10},
    "512890.SH": {"name": "红利低波ETF", "group": "中国股票", "t0": False, "limit": .10},
    "518880.SH": {"name": "黄金ETF", "group": "黄金", "t0": True, "limit": .10},
    "511260.SH": {"name": "十年国债ETF", "group": "国债", "t0": True, "limit": .10},
    "513100.SH": {"name": "纳指ETF", "group": "海外股票", "t0": True, "limit": .10},
}
METHODS = {
    "rotation": "趋势分散组合",
    "ml": "机器学习 · 净收益回归",
    "ml_class": "梯度树 · 涨跌分类",
    "cnn": "1D-CNN · 净收益回归",
    "cnn_class": "1D-CNN · 涨跌分类",
    "hold": "同板块偏好持有",
}
GROUPS = tuple(dict.fromkeys(asset["group"] for asset in ASSETS.values()))
PROFILE_PRESETS = {
    "conservative": {
        "label": "保守",
        "max_exposure": .50,
        "max_weight": .25,
        "holdings": 3,
        "drawdown_pause": .08,
        "cooldown_sessions": 20,
        "drift_band": .05,
        "allocation": {"中国股票": .20, "海外股票": .15, "黄金": .25, "国债": .40},
    },
    "balanced": {
        "label": "均衡",
        "max_exposure": .70,
        "max_weight": .35,
        "holdings": 3,
        "drawdown_pause": .12,
        "cooldown_sessions": 20,
        "drift_band": .04,
        "allocation": {"中国股票": .25, "海外股票": .25, "黄金": .25, "国债": .25},
    },
    "aggressive": {
        "label": "激进",
        "max_exposure": .90,
        "max_weight": .45,
        "holdings": 3,
        "drawdown_pause": .15,
        "cooldown_sessions": 15,
        "drift_band": .04,
        "allocation": {"中国股票": .45, "海外股票": .30, "黄金": .15, "国债": .10},
    },
}
PROFILE_FIELDS = ("max_exposure", "max_weight", "holdings", "drawdown_pause", "cooldown_sessions", "drift_band")
# Keep legacy presets intact so frozen plans retain their original identity.
FIXED_PROFILE_COMMON = {
    "symbols": list(ASSETS), "research_interval": "daily",
    "rebalance": "monthly", "trend_signal": "multi_horizon",
    "execution_policy": "cost_aware", "exposure_policy": "volatility_target",
    "signal_policy": "legacy", "risk_review": "rebalance",
    "allocation_policy": "legacy", "core_anchor": .5, "risk_aversion": 3.,
    "commission_rate": .0003, "min_commission": 5., "slippage_bps": 5.,
    "participation": .01, "research_annual_rate": .08,
    "research_maintenance_ratio": .30,
}
# Independent winners may coincide; changing one recipe must not change the others.
PROFILE_SIGNAL_RECIPES = {
    "conservative": {"method": "ml", "model_recipe": "hgb20_regularized"},
    "balanced": {"method": "ml", "model_recipe": "hgb20_regularized"},
    "aggressive": {"method": "ml", "model_recipe": "hgb20_regularized"},
}
FIXED_PROFILE_PRESETS = {
    key: {**FIXED_PROFILE_COMMON, **preset, **PROFILE_SIGNAL_RECIPES[key],
          **recipe_parameters(PROFILE_SIGNAL_RECIPES[key]["model_recipe"]),
          "research_multiple": 2 if key == "aggressive" else 1,
          "target_volatility": {"conservative": .08, "balanced": .15, "aggressive": .30}[key]}
    for key, preset in PROFILE_PRESETS.items()
}
FIXED_PROFILE_PRESETS["aggressive"].update(
    holdings=4, drift_band=.08,
    allocation={group: .25 for group in GROUPS},
)
FIXED_PROFILE_PRESETS["balanced"].update(
    allocation_policy="balanced_v1", core_anchor=.6, risk_aversion=6.,
    max_exposure=.98, max_weight=.40, holdings=4, target_volatility=.10,
    drawdown_pause=.12, drift_band=.04,
    allocation={"中国股票": .30, "海外股票": .30, "黄金": .20, "国债": .20},
)
FIXED_PROFILE_PRESETS["conservative"].update(
    allocation_policy="defensive_v1", core_anchor=.2, risk_aversion=3.,
    max_exposure=.70, max_weight=.35, holdings=4, target_volatility=.04,
    drawdown_pause=.06, drift_band=.04,
    allocation={"中国股票": .15, "海外股票": .15, "黄金": .15, "国债": .55},
)
ENGINE_VERSION = "portfolio-4.8.0"
RESEARCH_FIELDS = {"validation_start_year", "validation_end_year"}


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)
    name: str = Field("我的投资计划", min_length=1, max_length=60)
    method: Literal["rotation", "hold", "ml", "ml_class", "cnn", "cnn_class"] = "rotation"
    profile: Literal["conservative", "balanced", "aggressive", "custom"] = "balanced"
    preset_version: Literal[0, 1] = 0
    symbols: list[str] = Field(default_factory=lambda: list(ASSETS), min_length=2, max_length=6)
    allocation: dict[str, float] = Field(default_factory=lambda: dict(PROFILE_PRESETS["balanced"]["allocation"]))
    starting_cash: float = Field(100000, ge=1000, le=1e9)
    max_exposure: float = Field(.70, gt=0, le=1)
    max_weight: float = Field(.35, gt=0, le=1)
    holdings: int = Field(3, ge=1, le=4)
    rebalance: Literal["daily", "weekly", "monthly"] = "weekly"
    trend_signal: Literal["legacy", "multi_horizon"] = "legacy"
    exposure_policy: Literal["fixed", "volatility_target"] = "fixed"
    execution_policy: Literal["legacy", "corrected", "cost_aware"] = "legacy"
    target_volatility: float = Field(.12, ge=.03, le=.40)
    signal_policy: Literal["legacy", "trend_weighted", "confirmed", "consensus", "fast_confirmed"] = "legacy"
    risk_review: Literal["rebalance", "weekly", "daily"] = "rebalance"
    allocation_policy: Literal["legacy", "defensive_v1", "balanced_v1"] = "legacy"
    core_anchor: float = Field(.5, ge=0, le=5)
    risk_aversion: float = Field(3., ge=.1, le=30)
    research_interval: Literal["daily", "30m", "60m"] = "daily"
    intraday_strategy: Literal["legacy", "cost_aware", "breakout", "ml_edge"] = "cost_aware"
    intraday_cost_buffer: float = Field(1.5, ge=1, le=5)
    intraday_risk_budget: float = Field(.015, ge=.002, le=.10)
    intraday_training_sessions: int = Field(60, ge=20, le=750)
    intraday_min_samples: int = Field(80, ge=60, le=10000)
    intraday_horizon_hours: int = Field(4, ge=1, le=16)
    research_multiple: Literal[1, 2, 3] = 1
    research_symbol: str = "518880.SH"
    research_annual_rate: float = Field(.08, ge=0, le=.30)
    research_maintenance_ratio: float = Field(.30, ge=0, le=.80)
    validation_start_year: int = Field(default_factory=lambda: date.today().year - 5, ge=2015, le=2099)
    validation_end_year: int = Field(default_factory=lambda: date.today().year - 1, ge=2015, le=2099)
    drawdown_pause: float = Field(.12, ge=.02, le=.5)
    cooldown_sessions: int = Field(20, ge=1, le=120)
    drift_band: float = Field(.04, ge=0, le=.2)
    commission_rate: float = Field(.0003, ge=0, le=.01)
    min_commission: float = Field(5., ge=0, le=100)
    slippage_bps: float = Field(5., ge=0, le=100)
    participation: float = Field(.01, gt=0, le=.1)
    training_years: int = Field(3, ge=1, le=10)
    model_recipe: str = "legacy"
    model_tuning: ModelTuning | None = None
    horizon: int = Field(5, ge=1, le=20)
    model_iterations: int = Field(120, ge=20, le=500)
    model_leaves: int = Field(7, ge=3, le=31)
    model_learning_rate: float = Field(.04, ge=.005, le=.2)
    min_samples: int = Field(800, ge=100, le=100000)
    classification_threshold: float = Field(.5, ge=.05, le=.95)
    cnn_window: int = Field(20, ge=10, le=60)
    cnn_epochs: int = Field(12, ge=4, le=30)
    auto_update: bool = False

    @model_validator(mode="after")
    def validate_plan(self):
        if self.model_recipe != "legacy" and self.model_recipe not in model_specs():
            raise ValueError("未知模型配方")
        if self.allocation_policy != "legacy" and (self.research_multiple != 1 or self.research_interval != "daily"
                or self.execution_policy != "cost_aware" or self.exposure_policy != "volatility_target"
                or self.trend_signal != "multi_horizon" or self.method not in {"ml", "rotation", "hold"}):
            raise ValueError("分散核心组合仅支持回归模型或趋势、日线、1倍、成本约束与多周期波动控仓")
        if self.method == "ml" and self.model_recipe != "legacy":
            resolved_spec(self.model_recipe, self.model_tuning)
            if self.model_tuning:
                # Store only effective overrides, keeping old recipe identities stable.
                base = model_specs()[self.model_recipe]
                values = {k: v for k, v in self.model_tuning.model_dump(exclude_none=True).items()
                          if getattr(base, k) != v}
                self.model_tuning = ModelTuning(**values) if values else None
            for key, value in recipe_parameters(self.model_recipe, self.model_tuning).items():
                setattr(self, key, value)
        elif self.model_tuning is not None:
            raise ValueError("独立研究参数仅用于命名回归模型配方")
        if self.model_tuning:
            self.profile = "custom"
        self.symbols = list(dict.fromkeys(self.symbols))
        if len(self.symbols) < 2 or any(s not in ASSETS for s in self.symbols):
            raise ValueError("请至少选择两只支持的 ETF")
        if self.validation_start_year > self.validation_end_year or self.validation_end_year >= date.today().year:
            raise ValueError("历史验证只能选择已结束的年份，且起始年不能晚于结束年")
        if self.research_symbol not in ASSETS or not ASSETS[self.research_symbol]["t0"]:
            raise ValueError("小时实验只能选择支持当日回转的 ETF")
        if self.research_interval != "daily" and self.research_symbol not in self.symbols:
            raise ValueError("请在投资范围中选中小时实验所用 ETF")
        if self.max_weight > self.max_exposure:
            raise ValueError("单只上限不能超过总仓位上限")
        if set(self.allocation) != set(GROUPS) or any(not 0 <= part <= 1 for part in self.allocation.values()):
            raise ValueError("请为每个资产板块设置 0% 至 100% 的分配比例")
        if abs(sum(self.allocation.values()) - 1) > 1e-6:
            raise ValueError("资产板块分配比例合计必须为 100%")
        if not any(self.allocation[ASSETS[symbol]["group"]] > 0 for symbol in self.symbols):
            raise ValueError("已选 ETF 所在的板块至少需要一个非零预算")
        if self.profile != "custom":
            preset = (FIXED_PROFILE_PRESETS if self.preset_version else PROFILE_PRESETS)[self.profile]
            if self.preset_version:
                if any(getattr(self, key) != value for key, value in preset.items() if key != "label"):
                    self.profile = "custom"
            elif (any(abs(getattr(self, key) - preset[key]) > 1e-8 for key in PROFILE_FIELDS)
                    or any(abs(self.allocation[key] - preset["allocation"][key]) > 1e-8 for key in GROUPS)):
                self.profile = "custom"
        return self


def with_profile(plan: Plan, profile: Literal["conservative", "balanced", "aggressive"]) -> Plan:
    preset = PROFILE_PRESETS[profile]
    return Plan.model_validate({**plan.model_dump(), "profile": profile,
                                **{key: preset[key] for key in PROFILE_FIELDS},
                                "allocation": dict(preset["allocation"])})


def fingerprint(plan: Plan):
    values = plan.model_dump(exclude={"name", "auto_update", *RESEARCH_FIELDS})
    if values["model_tuning"] is None:
        values.pop("model_tuning")
    if values["allocation_policy"] == "legacy":
        for key in ("allocation_policy", "core_anchor", "risk_aversion"):
            values.pop(key)
    if values.pop("preset_version"):
        # Preset labels are presentation metadata, not execution inputs.
        values["profile"] = "custom"
    for key, default in (("signal_policy", "legacy"), ("risk_review", "rebalance")):
        if values[key] == default:
            values.pop(key)
    if values["execution_policy"] == "legacy":
        values.pop("execution_policy")
    if values["model_recipe"] == "legacy":
        values.pop("model_recipe")
    return hashlib.sha256(json.dumps({"engine": ENGINE_VERSION, **values}, sort_keys=True).encode()).hexdigest()[:24]


def comparison_fingerprint(plan: Plan):
    values = plan.model_dump(exclude={"name", "auto_update", "method", *RESEARCH_FIELDS})
    if values["model_tuning"] is None:
        values.pop("model_tuning")
    if values["allocation_policy"] == "legacy":
        for key in ("allocation_policy", "core_anchor", "risk_aversion"):
            values.pop(key)
    if values.pop("preset_version"):
        values["profile"] = "custom"
    for key, default in (("signal_policy", "legacy"), ("risk_review", "rebalance")):
        if values[key] == default:
            values.pop(key)
    if values["execution_policy"] == "legacy":
        values.pop("execution_policy")
    if values["model_recipe"] == "legacy":
        values.pop("model_recipe")
    return hashlib.sha256(json.dumps({"engine": ENGINE_VERSION, **values}, sort_keys=True).encode()).hexdigest()[:24]
