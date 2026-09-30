from __future__ import annotations

import copy
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROFILES = {"aggressive": "激进", "balanced": "均衡", "conservative": "保守", "custom": "自定义"}
SYMBOLS = {
    "510300.SH": "沪深300ETF", "510500.SH": "中证500ETF", "512100.SH": "中证1000ETF",
    "588000.SH": "科创50ETF", "159915.SZ": "创业板ETF", "512880.SH": "证券ETF",
    "512890.SH": "红利低波ETF", "518880.SH": "黄金ETF", "511260.SH": "国债ETF",
    "513100.SH": "纳指ETF", "510050.SH": "上证50ETF", "159919.SZ": "沪深300ETF（深）",
    "159922.SZ": "中证500ETF（深）", "512010.SH": "医药ETF", "512000.SH": "券商ETF",
    "515000.SH": "科技ETF", "512690.SH": "酒ETF", "515180.SH": "红利ETF",
    "513500.SH": "标普500ETF", "159934.SZ": "黄金ETF（深）",
}


def parameter(key: str, label: str, default: Any, help_text: str, kind: str = "number", minimum: float | None = None, maximum: float | None = None, step: float = 1, options: list | None = None) -> dict:
    return dict(key=key, label=label, default=default, help=help_text, kind=kind, min=minimum, max=maximum, step=step, options=options)


MODEL_FIELDS = {
    "random_forest": [
        parameter("n_estimators", "树数量", 80, "更多树通常使预测更稳定，也增加训练时间。", minimum=1),
        parameter("max_depth", "树深度", 5, "更深能学习更复杂的关系，也更容易记住历史噪声。", minimum=1),
        parameter("min_samples_leaf", "叶子最小样本", 20, "越大越平滑，越小越敏感。", minimum=1),
        parameter("max_features_sqrt", "随机抽取特征 √", True, "每次分裂只检查部分特征，让各棵树更有差异。", "boolean"),
        parameter("class_weight", "类别权重", True, "平衡涨跌类别的训练影响，概率仍需验证。", "boolean"),
    ],
    "hist_gradient_boosting": [
        parameter("max_iter", "迭代轮数", 140, "更多轮拟合能力更强，但耗时和过拟合风险增加。", minimum=1),
        parameter("learning_rate", "学习率", .03, "每轮更新幅度；较小通常需要更多轮。", minimum=.000001, step=.005),
        parameter("max_leaf_nodes", "最大叶节点", 15, "控制每棵树的复杂程度。", minimum=2),
        parameter("min_samples_leaf", "叶子最小样本", 12, "较大值减少对少数样本的过度拟合。", minimum=1),
        parameter("l2_regularization", "L2 正则", .08, "惩罚过大的拟合权重，提高平滑程度。", minimum=0, step=.01),
    ],
    "xgboost": [
        parameter("n_estimators", "树数量", 120, "控制逐步纠错的轮数。", minimum=1),
        parameter("max_depth", "树深度", 3, "更深会学习更复杂的组合关系。", minimum=1),
        parameter("learning_rate", "学习率", .035, "控制每棵树的贡献。", minimum=.000001, step=.005),
        parameter("subsample", "样本采样比例", .85, "随机抽取训练样本，减少过拟合。", minimum=.01, maximum=1, step=.05),
        parameter("colsample_bytree", "特征采样比例", .85, "每棵树使用的特征比例。", minimum=.01, maximum=1, step=.05),
        parameter("min_child_weight", "节点最小权重", 5., "越大越谨慎地增加分支。", minimum=0, step=1),
        parameter("reg_lambda", "L2 正则", 1.5, "越大越抑制复杂拟合。", minimum=0, step=.1),
        parameter("device", "计算设备", "auto", "自动优先尝试 CUDA；不可用时记录 CPU 回退。", "select", options=["auto", "cpu", "cuda"]),
    ],
    "mlp": [
        parameter("layer_size", "隐藏层宽度", 64, "每层神经元数量，越大占用越多内存。", minimum=1),
        parameter("layer_count", "隐藏层数量", 2, "增加网络深度，可能提高复杂关系拟合能力。", minimum=1),
        parameter("epochs", "最大训练轮数", 120, "训练上限；启用验证早停时可提前结束。", minimum=1),
        parameter("learning_rate", "学习率", .001, "太大会不稳定，太小会收敛缓慢。", minimum=.000001, step=.0001),
        parameter("weight_decay", "权重衰减", .001, "抑制过大的网络权重。", minimum=0, step=.0001),
        parameter("dropout", "随机失活比例", .08, "训练时随机关闭部分神经元，减少过拟合。", minimum=0, maximum=.95, step=.05),
    ],
    "cnn": [
        parameter("window", "序列窗口", 30, "每次预测查看多少根历史行情。", minimum=2),
        parameter("channels", "卷积通道", 24, "学习不同形态的通道数量。", minimum=1),
        parameter("kernel_size", "卷积核长度", 3, "每次观察的局部时间跨度，必须为奇数。", minimum=1, step=2),
        parameter("layers", "卷积层数", 2, "增加形态组合能力，同时增加计算量。", minimum=1),
        parameter("epochs", "最大训练轮数", 100, "验证指标不再改善时可提前结束。", minimum=1),
        parameter("learning_rate", "学习率", .001, "网络每次更新的幅度。", minimum=.000001, step=.0001),
        parameter("weight_decay", "权重衰减", .001, "抑制过大的网络权重。", minimum=0, step=.0001),
        parameter("dropout", "随机失活比例", .08, "减少对单一历史形态的依赖。", minimum=0, maximum=.95, step=.05),
    ],
}
for name in ("mlp", "cnn"):
    MODEL_FIELDS[name] += [
        parameter("batch_size", "批次样本数", 128, "每批处理多少样本；较小更省显存。", minimum=1),
        parameter("early_stopping", "验证集早停", True, "使用按时间留出的验证集判断何时停止。", "boolean"),
        parameter("device", "计算设备", "auto", "自动优先使用可用的 CUDA GPU。", "select", options=["auto", "cpu", "cuda"]),
    ]
MODEL_NAMES = {"random_forest": "RandomForest", "hist_gradient_boosting": "HistGradientBoosting", "xgboost": "XGBoost", "mlp": "MLP", "cnn": "1D-CNN"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Preset(StrictModel):
    @model_validator(mode="before")
    @classmethod
    def discard_legacy_ai_reference_options(cls, value):
        if isinstance(value, dict) and ("use_ml_reference" in value or "reference_model" in value):
            value = {key: item for key, item in value.items() if key not in {"use_ml_reference", "reference_model"}}
        return value

    training_years: Literal[1, 3, 5, 10] = 3
    training_mode: Literal["self", "classified", "mixed"] = "self"
    target: Literal["binary", "trend5"] = "binary"
    label_basis: Literal["close_return", "next_open_net"] = "close_return"
    horizon: int = Field(1, ge=1, le=20)
    buy_threshold: float = Field(.65, ge=0, le=1)
    sell_threshold: float = Field(.35, ge=0, le=1)
    min_samples: int = Field(100, ge=20)
    max_buys: int = Field(2, ge=0)
    buy_score: int = Field(4, ge=0)
    allow_add: bool = False
    calibrate_regime: bool = True
    ai_prompt: str = Field("", max_length=5000)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def thresholds(self):
        if self.sell_threshold >= self.buy_threshold:
            raise ValueError("卖出阈值必须小于买入阈值")
        return self


class Risk(StrictModel):
    starting_cash: float = Field(100000, gt=0)
    max_trade_cash: float = Field(10000, gt=0)
    max_order_shares: int = Field(2500, ge=1)
    max_position_pct: float = Field(.5, gt=0, le=1)
    stop_loss_pct: float = Field(.2, gt=0, le=1)
    take_profit_pct: float = Field(.5, gt=0)
    max_daily_loss_pct: float = Field(.02, gt=0, le=1)
    commission_rate: float = Field(.0003, ge=0, le=.05)
    min_commission: float = Field(0., ge=0, le=1000)
    slippage_bps: float = Field(5, ge=0, le=1000)


def default_presets(model: str) -> dict[str, Preset]:
    values = {}
    for key in PROFILES:
        args = {"aggressive": (.55, .27, 3, 3), "balanced": (.65, .35, 2, 4), "conservative": (.72, .4, 1, 6), "custom": (.65, .35, 2, 4)}[key]
        params = {f["key"]: f["default"] for f in MODEL_FIELDS.get(model, [])}
        if model == "random_forest" and key == "aggressive":
            params["n_estimators"] = 300
        prompt = {"aggressive": "趋势与动量确认后积极参与，强趋势中允许耐心持有，避免无依据追涨。", "balanced": "兼顾趋势延续和区间波动，证据不足时观望，避免过度交易。", "conservative": "优先控制回撤，弱趋势中谨慎买入，趋势破坏时及时降低风险。", "custom": "根据趋势、动量和风险给出买卖或观望判断。"}[key] if model == "ai" else ""
        values[key] = Preset(buy_threshold=args[0], sell_threshold=args[1], max_buys=args[2], buy_score=args[3], allow_add=key == "aggressive", params=params, ai_prompt=prompt)
    return values


class AppConfig(StrictModel):
    schema_version: Literal[2] = 2
    name: str = Field("我的研究方案", min_length=1, max_length=80)
    engine: Literal["local", "ml", "ai"] = "local"
    model: Literal["random_forest", "hist_gradient_boosting", "xgboost", "mlp", "cnn"] = "random_forest"
    profile: Literal["aggressive", "balanced", "conservative", "custom"] = "balanced"
    auto_profile: bool = False
    profile_selection: Literal["fixed", "trend", "recent"] = "fixed"
    symbols: list[str] = Field(default_factory=lambda: list(SYMBOLS)[:10], min_length=1, max_length=30)
    training_symbols: list[str] = Field(default_factory=lambda: list(SYMBOLS)[10:], max_length=50)
    risk: Risk = Field(default_factory=Risk)
    models: dict[str, dict[str, Preset]] = Field(default_factory=lambda: {m: default_presets(m) for m in MODEL_NAMES})
    local: dict[str, Preset] = Field(default_factory=lambda: default_presets("local"))
    ai: dict[str, Preset] = Field(default_factory=lambda: default_presets("ai"))
    annual_labels: dict[str, Literal["bull", "bear", "sideways"]] = Field(default_factory=dict)
    seed: int = 42

    @model_validator(mode="before")
    @classmethod
    def migrate_profile_selection(cls, raw):
        if not isinstance(raw, dict):
            return raw
        values = dict(raw)
        if "profile_selection" not in values:
            values["profile_selection"] = "trend" if values.get("auto_profile") else "fixed"
        elif values["profile_selection"] == "fixed" and values.get("auto_profile"):
            values["profile_selection"] = "trend"
        values["auto_profile"] = values["profile_selection"] != "fixed"
        return values

    @model_validator(mode="after")
    def validate_models(self):
        if set(self.models) != set(MODEL_NAMES) or set(self.local) != set(PROFILES) or set(self.ai) != set(PROFILES):
            raise ValueError("模型或档位配置不完整")
        for model, presets in self.models.items():
            if set(presets) != set(PROFILES):
                raise ValueError(f"{model} 缺少档位")
            allowed = {f["key"]: f for f in MODEL_FIELDS[model]}
            for preset in presets.values():
                if set(preset.params) != set(allowed):
                    raise ValueError(f"{model} 参数不完整或含未知参数")
                for key, value in preset.params.items():
                    field = allowed[key]
                    if field["kind"] == "boolean":
                        if not isinstance(value, bool):
                            raise ValueError(f"{key} 应为开关")
                    elif field["kind"] == "select":
                        if value not in field["options"]:
                            raise ValueError(f"{key} 选项无效")
                    else:
                        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                            raise ValueError(f"{key} 应为有限数字")
                        if isinstance(field["default"], int) and value != int(value):
                            raise ValueError(f"{key} 应为整数")
                        if isinstance(field["default"], int):
                            preset.params[key] = int(value)
                        if field["min"] is not None and value < field["min"] or field["max"] is not None and value > field["max"]:
                            raise ValueError(f"{key} 超出算法合法范围")
                if model == "cnn" and int(preset.params["kernel_size"]) % 2 == 0:
                    raise ValueError("卷积核长度必须为奇数")
        self.symbols = list(dict.fromkeys(s.upper().strip() for s in self.symbols))
        self.training_symbols = list(dict.fromkeys(s.upper().strip() for s in self.training_symbols))
        if any(s not in SYMBOLS for s in self.symbols + self.training_symbols):
            raise ValueError("请选择支持的交易标的")
        return self

    def preset(self, profile: str | None = None) -> Preset:
        key = profile or self.profile
        return (self.models[self.model] if self.engine == "ml" else self.ai if self.engine == "ai" else self.local)[key]


from ..v4.schema import Plan as InvestmentPlan
from ..v4.lab_schema import Study


class JobRequest(StrictModel):
    kind: Literal["backtest", "evaluate", "learn", "refresh", "paper", "research", "portfolio_research", "portfolio_test", "portfolio_frequency", "portfolio_update", "portfolio_review", "portfolio_observe", "intraday_refresh", "intraday_test"] = "backtest"
    study: Study | None = None
    observation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    observation_automatic: bool = False
    investment: InvestmentPlan | None = None
    research_method: Literal["rotation", "ml", "ml_class", "cnn", "cnn_class"] | None = None
    compare_models: bool = True
    period: Literal["year", "random14"] = "year"
    year: int = Field(2025, ge=2000, le=2100)
    years: list[int] = Field(default_factory=lambda: [2021, 2022, 2023, 2024, 2025], min_length=1, max_length=15)
    trials: int = Field(3, ge=1, le=50)
    continuous: bool = False
    profiles: list[Literal["aggressive", "balanced", "conservative", "custom"]] = Field(default_factory=lambda: ["aggressive", "balanced", "conservative"], min_length=1, max_length=4)
    check_years: list[int] = Field(default_factory=list, max_length=5)
    weights: dict[str, float] = Field(default_factory=lambda: {"specialized": .5, "fallback": .2, "composite": .3})
    config: AppConfig | None = None

    @model_validator(mode="after")
    def weights_valid(self):
        if self.kind == "portfolio_research" and (self.study is None or self.investment is None or self.investment.research_interval != "daily"):
            raise ValueError("研究任务需要研究配置和日线计划快照")
        if self.kind == 'portfolio_observe' and not self.observation_id:
            raise ValueError('前向观察任务缺少记录编号')
        if (self.kind.startswith("portfolio_") or self.kind.startswith("intraday_")) and self.investment is None:
            raise ValueError("投资任务缺少计划快照")
        if set(self.weights) != {"specialized", "fallback", "composite"} or any(not math.isfinite(v) or v < 0 for v in self.weights.values()) or sum(self.weights.values()) <= 0:
            raise ValueError("评分权重应为非负数字，合计必须大于零")
        if any(y < 2000 or y > 2100 for y in self.years + self.check_years):
            raise ValueError("验证年份无效")
        self.profiles = list(dict.fromkeys(self.profiles))
        self.years = sorted(set(self.years))
        self.check_years = sorted(set(self.check_years))
        if self.kind == "learn" and set(self.years) & set(self.check_years):
            raise ValueError("调参年份与验收年份不能重叠")
        if self.kind == "evaluate" and self.config and self.config.engine == "ai":
            raise ValueError("AI 请使用短期验证")
        return self


def migrate_config(raw: dict) -> tuple[AppConfig, list[str]]:
    config = AppConfig()
    notes = []
    config.engine = raw.get("run_engine", "local") if raw.get("run_engine") in {"local", "ml", "ai"} else "local"
    model = raw.get("ml_model", "random_forest")
    config.model = model if model in MODEL_NAMES else "random_forest"
    config.profile = raw.get("strategy_profile", "balanced") if raw.get("strategy_profile") in PROFILES else "balanced"
    config.symbols = [s for s in str(raw.get("symbols", "")).split(",") if s in SYMBOLS] or config.symbols
    config.training_symbols = [s for s in str(raw.get("training_symbols", "")).split(",") if s in SYMBOLS] or config.training_symbols
    for key in Risk.model_fields:
        if key in raw:
            try:
                data = config.risk.model_dump()
                data[key] = raw[key]
                config.risk = Risk.model_validate(data)
            except Exception:
                notes.append(f"账户参数 {key} 无效，使用新版默认值")
    prefixes = {"random_forest": "rf", "hist_gradient_boosting": "hgb", "xgboost": "xgb", "mlp": "mlp", "cnn": "cnn"}
    renamed = {"epochs": "max_iter", "weight_decay": "alpha", "window": "window"}
    for model, prefix in prefixes.items():
        selected = raw.get(f"{prefix}_strategy_profile", config.profile)
        for profile, preset in config.models[model].items():
            overrides = copy.deepcopy(raw.get("ml_profile_overrides", {}).get(model, {}).get(profile, {}))
            if profile == selected:
                overrides.update({k: v for k, v in raw.items() if k.startswith(prefix + "_")})
            data = preset.model_dump()
            for dest, old in [("buy_threshold", "buy_threshold"), ("sell_threshold", "sell_threshold"), ("min_samples", "min_samples"), ("training_mode", "training_mode"), ("target", "target")]:
                value = overrides.get(f"ml_{old}", overrides.get(f"{prefix}_{old}"))
                if value not in (None, ""):
                    if dest == "training_mode":
                        value = {"general": "mixed", "single": "self", "auto": "self"}.get(value, value)
                    data[dest] = value
            days = overrides.get(f"{prefix}_training_days", raw.get(f"{prefix}_training_days"))
            if days:
                data["training_years"] = min((1, 3, 5, 10), key=lambda y: abs(y * 365 - float(days)))
            for field in MODEL_FIELDS[model]:
                old = f"{prefix}_{renamed.get(field['key'], field['key'])}"
                if old in overrides:
                    data["params"][field["key"]] = overrides[old]
            try:
                config.models[model][profile] = Preset.model_validate(data)
                AppConfig.model_validate(config.model_dump())
            except Exception:
                config.models[model][profile] = preset
                notes.append(f"{MODEL_NAMES[model]} {PROFILES[profile]} 的部分旧参数不兼容，保留新版默认值")
    return AppConfig.model_validate(config.model_dump()), notes
