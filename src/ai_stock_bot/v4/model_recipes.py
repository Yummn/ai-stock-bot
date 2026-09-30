"""Immutable signal recipes shared by offline research and portfolio execution."""
from dataclasses import asdict, dataclass


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


def recipe_parameters(key):
    spec = model_specs()[key]
    values = dict(training_years=spec.years, horizon=spec.horizon, min_samples=800,
                  model_iterations=spec.iterations, model_leaves=spec.leaves, model_learning_rate=.04)
    if spec.model == "cnn":
        values.update(cnn_window=spec.window, cnn_epochs=spec.epochs)
    return values
