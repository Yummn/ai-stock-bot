"""Immutable signal recipes shared by offline research and portfolio execution."""
from dataclasses import asdict, dataclass, replace

from pydantic import BaseModel, ConfigDict, Field


class ModelTuning(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    years: int | None = Field(None, ge=1, le=10)
    horizon: int | None = Field(None, ge=1, le=20)
    iterations: int | None = Field(None, ge=20, le=500)
    leaves: int | None = Field(None, ge=3, le=31)
    leaf_samples: int | None = Field(None, ge=5, le=500)
    regularization: float | None = Field(None, ge=.001, le=10000)
    window: int | None = Field(None, ge=10, le=60)
    epochs: int | None = Field(None, ge=4, le=30)


TUNABLE_FIELDS = {
    "hgb": ("years", "horizon", "iterations", "leaves", "leaf_samples", "regularization"),
    "ridge": ("years", "horizon", "regularization"),
    "extra": ("years", "horizon", "iterations", "leaf_samples"),
    "xgb": ("years", "horizon", "iterations", "regularization"),
    "cnn": ("years", "horizon", "window", "epochs"),
}


def resolved_spec(key, tuning=None):
    spec = model_specs()[key]
    values = ModelTuning.model_validate(tuning or {}).model_dump(exclude_none=True)
    invalid = set(values) - set(TUNABLE_FIELDS[spec.model])
    if invalid:
        raise ValueError("此模型不使用参数：" + ", ".join(sorted(invalid)))
    return replace(spec, **values)


@dataclass(frozen=True)
class ModelSpec:
    model: str = "hgb"
    horizon: int = 20
    years: int = 3
    extended: bool = True
    iterations: int = 120
    leaves: int = 7
    leaf_samples: int = 30
    regularization: float = 1.
    window: int = 1
    epochs: int = 8


@dataclass(frozen=True)
class RiskNormalizedSpec(ModelSpec):
    target_scaling: str = "trailing_volatility"


def model_specs():
    return {
        "hgb5_old": ModelSpec(horizon=5, extended=False),
        "hgb20_old": ModelSpec(extended=False),
        "hgb20": ModelSpec(),
        "hgb20_5y": ModelSpec(years=5),
        "hgb20_regularized": ModelSpec(leaves=5, leaf_samples=80, regularization=10.),
        "ridge20": ModelSpec(model="ridge", regularization=100.),
        "ridge20_light": ModelSpec(model="ridge", regularization=10.),
        "extra20": ModelSpec(model="extra", iterations=100, leaf_samples=40),
        "extra20_smooth": ModelSpec(model="extra", iterations=200, leaf_samples=80),
        "xgb20": ModelSpec(model="xgb", iterations=120, regularization=10.),
        "xgb20_smooth": ModelSpec(model="xgb", iterations=200, regularization=50.),
        "cnn20": ModelSpec(model="cnn", window=20),
        "cnn20_long": ModelSpec(model="cnn", window=40, epochs=12),
    }


RECIPE_LABELS = {
    "hgb5_old": "HGB · 5日基础", "hgb20_old": "HGB · 20日基础",
    "hgb20": "HGB · 标准", "hgb20_5y": "HGB · 5年训练",
    "hgb20_regularized": "HGB · 强正则",
    "ridge20": "Ridge · 正则100", "ridge20_light": "Ridge · 正则10",
    "extra20": "Extra Trees · 标准", "extra20_smooth": "Extra Trees · 平滑",
    "xgb20": "XGBoost · 标准", "xgb20_smooth": "XGBoost · 强正则",
    "cnn20": "1D-CNN · 20日序列", "cnn20_long": "1D-CNN · 40日序列",
}


def recipe_catalog():
    return {key: {"label": RECIPE_LABELS[key], **asdict(spec)} for key, spec in model_specs().items()}


def recipe_parameters(key, tuning=None):
    spec = resolved_spec(key, tuning)
    values = dict(training_years=spec.years, horizon=spec.horizon, min_samples=800,
                  model_iterations=spec.iterations, model_leaves=spec.leaves, model_learning_rate=.04)
    if spec.model == "cnn":
        values.update(cnn_window=spec.window, cnn_epochs=spec.epochs)
    return values
