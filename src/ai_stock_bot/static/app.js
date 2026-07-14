const state = {
  busy: false,
  autoRunning: false,
  activeView: "dashboardView",
  activeSettingsTab: "account",
  settingsDirty: false,
  backtestTaskId: null,
  backtestPollTimer: null,
  activeTrainingTab: "data",
  autoLearnTaskId: null,
  autoLearnPollTimer: null,
  autoLearnResult: null,
  mlProfileOverrides: {},
};

const fields = [
  "symbols",
  "training_symbols",
  "training_pool_mode",
  "training_regime_filter",
  "training_volatility_filter",
  "year_regime_overrides",
  "ml_profile_overrides",
  "strategy_auto_select",
  "starting_cash",
  "max_trade_cash",
  "max_order_shares",
  "max_position_pct",
  "stop_loss_pct",
  "take_profit_pct",
  "strategy_profile",
  "local_strategy_profile",
  "ml_strategy_profile",
  "ai_strategy_profile",
  "conservative_buy_score",
  "balanced_buy_score",
  "aggressive_buy_score",
  "custom_buy_score",
  "conservative_max_buy_signals",
  "balanced_max_buy_signals",
  "aggressive_max_buy_signals",
  "custom_max_buy_signals",
  "run_engine",
  "ml_model",
  "ml_training_days",
  "ml_training_mode",
  "ml_target",
  "backtest_interval_minutes",
  "ml_buy_threshold",
  "ml_sell_threshold",
  "ml_min_samples",
  "rf_strategy_profile",
  "rf_training_days",
  "rf_training_mode",
  "rf_target",
  "rf_buy_threshold",
  "rf_sell_threshold",
  "rf_min_samples",
  "rf_n_estimators",
  "rf_max_depth",
  "rf_min_samples_leaf",
  "rf_max_features_sqrt",
  "rf_class_weight",
  "rf_sample_weight",
  "hgb_strategy_profile",
  "hgb_training_days",
  "hgb_training_mode",
  "hgb_target",
  "hgb_buy_threshold",
  "hgb_sell_threshold",
  "hgb_min_samples",
  "hgb_max_iter",
  "hgb_learning_rate",
  "hgb_max_leaf_nodes",
  "hgb_min_samples_leaf",
  "hgb_l2_regularization",
  "hgb_early_stopping",
  "hgb_sample_weight",
  "xgb_strategy_profile",
  "xgb_training_days",
  "xgb_training_mode",
  "xgb_target",
  "xgb_buy_threshold",
  "xgb_sell_threshold",
  "xgb_min_samples",
  "xgb_n_estimators",
  "xgb_max_depth",
  "xgb_learning_rate",
  "xgb_subsample",
  "xgb_colsample_bytree",
  "xgb_min_child_weight",
  "xgb_reg_lambda",
  "xgb_sample_weight",
  "mlp_strategy_profile",
  "mlp_training_days",
  "mlp_training_mode",
  "mlp_target",
  "mlp_buy_threshold",
  "mlp_sell_threshold",
  "mlp_min_samples",
  "mlp_layer_size",
  "mlp_layer_count",
  "mlp_max_iter",
  "mlp_learning_rate",
  "mlp_alpha",
  "mlp_early_stopping",
  "cnn_strategy_profile",
  "cnn_training_days",
  "cnn_training_mode",
  "cnn_target",
  "cnn_buy_threshold",
  "cnn_sell_threshold",
  "cnn_min_samples",
  "cnn_window",
  "cnn_channels",
  "cnn_kernel_size",
  "cnn_layers",
  "cnn_max_iter",
  "cnn_learning_rate",
  "cnn_alpha",
  "cnn_dropout",
  "cnn_early_stopping",
  "cnn_sample_weight",
];

const fieldDefaults = {
  symbols: "510300.SH,510500.SH,512100.SH,588000.SH,159915.SZ,512880.SH,512890.SH,518880.SH,511260.SH,513100.SH",
  training_symbols: "510050.SH,159919.SZ,159922.SZ,512010.SH,512000.SH,515000.SH,512690.SH,515180.SH,513500.SH,159934.SZ",
  training_pool_mode: "training_plus_trade",
  training_regime_filter: "off",
  training_volatility_filter: "off",
  year_regime_overrides: "{}",
  ml_profile_overrides: "{}",
  strategy_auto_select: "off",
  starting_cash: "100000",
  max_trade_cash: "10000",
  max_order_shares: "2500",
  max_position_pct: "50",
  stop_loss_pct: "20",
  take_profit_pct: "50",
  strategy_profile: "conservative",
  local_strategy_profile: "conservative",
  ml_strategy_profile: "conservative",
  ai_strategy_profile: "conservative",
  conservative_buy_score: "",
  balanced_buy_score: "",
  aggressive_buy_score: "",
  custom_buy_score: "",
  conservative_max_buy_signals: "",
  balanced_max_buy_signals: "",
  aggressive_max_buy_signals: "",
  custom_max_buy_signals: "",
  run_engine: "local",
  ml_model: "random_forest",
  ml_training_days: "1095",
  ml_training_mode: "self",
  ml_target: "binary",
  backtest_interval_minutes: "30",
  ml_buy_threshold: "",
  ml_sell_threshold: "",
  ml_min_samples: "80",
  rf_strategy_profile: "conservative",
  rf_training_days: "1095",
  rf_training_mode: "self",
  rf_target: "binary",
  rf_buy_threshold: "",
  rf_sell_threshold: "",
  rf_min_samples: "80",
  rf_n_estimators: "80",
  rf_max_depth: "5",
  rf_min_samples_leaf: "5",
  rf_max_features_sqrt: "true",
  rf_class_weight: "true",
  rf_sample_weight: "true",
  hgb_strategy_profile: "conservative",
  hgb_training_days: "1095",
  hgb_training_mode: "self",
  hgb_target: "binary",
  hgb_buy_threshold: "",
  hgb_sell_threshold: "",
  hgb_min_samples: "80",
  hgb_max_iter: "140",
  hgb_learning_rate: "0.03",
  hgb_max_leaf_nodes: "15",
  hgb_min_samples_leaf: "12",
  hgb_l2_regularization: "0.08",
  hgb_early_stopping: "false",
  hgb_sample_weight: "false",
  xgb_strategy_profile: "conservative",
  xgb_training_days: "1095",
  xgb_training_mode: "self",
  xgb_target: "binary",
  xgb_buy_threshold: "",
  xgb_sell_threshold: "",
  xgb_min_samples: "100",
  xgb_n_estimators: "100",
  xgb_max_depth: "2",
  xgb_learning_rate: "0.03",
  xgb_subsample: "0.8",
  xgb_colsample_bytree: "0.8",
  xgb_min_child_weight: "7",
  xgb_reg_lambda: "2",
  xgb_sample_weight: "true",
  mlp_strategy_profile: "conservative",
  mlp_training_days: "365",
  mlp_training_mode: "self",
  mlp_target: "binary",
  mlp_buy_threshold: "",
  mlp_sell_threshold: "",
  mlp_min_samples: "160",
  mlp_layer_size: "48",
  mlp_layer_count: "1",
  mlp_max_iter: "160",
  mlp_learning_rate: "0.001",
  mlp_alpha: "0.003",
  mlp_early_stopping: "true",
  cnn_strategy_profile: "conservative",
  cnn_training_days: "1095",
  cnn_training_mode: "self",
  cnn_target: "binary",
  cnn_buy_threshold: "",
  cnn_sell_threshold: "",
  cnn_min_samples: "160",
  cnn_window: "30",
  cnn_channels: "24",
  cnn_kernel_size: "3",
  cnn_layers: "2",
  cnn_max_iter: "140",
  cnn_learning_rate: "0.001",
  cnn_alpha: "0.003",
  cnn_dropout: "0.12",
  cnn_early_stopping: "true",
  cnn_sample_weight: "true",
};

const profileMlDefaults = {
  conservative: {
    ml_buy_threshold: "0.61",
    ml_sell_threshold: "0.44",
    ml_min_samples: "80",
    rf_n_estimators: "80",
    rf_max_depth: "5",
    rf_min_samples_leaf: "5",
    rf_max_features_sqrt: "true",
  },
  balanced: {
    ml_buy_threshold: "0.57",
    ml_sell_threshold: "0.42",
    ml_min_samples: "80",
    rf_n_estimators: "80",
    rf_max_depth: "5",
    rf_min_samples_leaf: "5",
    rf_max_features_sqrt: "true",
  },
  aggressive: {
    ml_buy_threshold: "0.4",
    ml_sell_threshold: "0.2",
    ml_min_samples: "100",
    rf_n_estimators: "300",
    rf_max_depth: "5",
    rf_min_samples_leaf: "20",
    rf_max_features_sqrt: "true",
  },
};

const hgbProfileDefaults = {
  conservative: {
    ml_buy_threshold: "0.63",
    ml_sell_threshold: "0.20",
    ml_min_samples: "80",
    hgb_max_iter: "140",
    hgb_learning_rate: "0.03",
    hgb_max_leaf_nodes: "15",
    hgb_min_samples_leaf: "12",
    hgb_l2_regularization: "0.08",
    hgb_early_stopping: "false",
    hgb_sample_weight: "false",
  },
  balanced: {
    ml_buy_threshold: "0.60",
    ml_sell_threshold: "0.22",
    ml_min_samples: "80",
    hgb_max_iter: "140",
    hgb_learning_rate: "0.03",
    hgb_max_leaf_nodes: "15",
    hgb_min_samples_leaf: "12",
    hgb_l2_regularization: "0.08",
    hgb_early_stopping: "false",
    hgb_sample_weight: "false",
  },
  aggressive: {
    ml_buy_threshold: "0.52",
    ml_sell_threshold: "0.18",
    ml_min_samples: "80",
    hgb_max_iter: "120",
    hgb_learning_rate: "0.035",
    hgb_max_leaf_nodes: "15",
    hgb_min_samples_leaf: "12",
    hgb_l2_regularization: "0.08",
    hgb_early_stopping: "false",
    hgb_sample_weight: "true",
  },
};

const xgbProfileDefaults = {
  conservative: {
    ml_buy_threshold: "0.62",
    ml_sell_threshold: "0.22",
    ml_min_samples: "100",
    xgb_n_estimators: "100",
    xgb_max_depth: "2",
    xgb_learning_rate: "0.03",
    xgb_subsample: "0.8",
    xgb_colsample_bytree: "0.8",
    xgb_min_child_weight: "7",
    xgb_reg_lambda: "2",
    xgb_sample_weight: "true",
  },
  balanced: {
    ml_buy_threshold: "0.57",
    ml_sell_threshold: "0.22",
    ml_min_samples: "90",
    xgb_n_estimators: "120",
    xgb_max_depth: "3",
    xgb_learning_rate: "0.035",
    xgb_subsample: "0.85",
    xgb_colsample_bytree: "0.85",
    xgb_min_child_weight: "5",
    xgb_reg_lambda: "1.5",
    xgb_sample_weight: "true",
  },
  aggressive: {
    ml_buy_threshold: "0.50",
    ml_sell_threshold: "0.18",
    ml_min_samples: "80",
    xgb_n_estimators: "120",
    xgb_max_depth: "3",
    xgb_learning_rate: "0.045",
    xgb_subsample: "0.9",
    xgb_colsample_bytree: "0.9",
    xgb_min_child_weight: "3",
    xgb_reg_lambda: "1",
    xgb_sample_weight: "true",
  },
};

const mlpProfileDefaults = {
  conservative: {
    ml_buy_threshold: "0.64",
    ml_sell_threshold: "0.32",
    ml_min_samples: "160",
    mlp_layer_size: "48",
    mlp_layer_count: "1",
    mlp_max_iter: "160",
    mlp_learning_rate: "0.001",
    mlp_alpha: "0.003",
    mlp_early_stopping: "true",
  },
  balanced: {
    ml_buy_threshold: "0.60",
    ml_sell_threshold: "0.30",
    ml_min_samples: "140",
    mlp_layer_size: "64",
    mlp_layer_count: "1",
    mlp_max_iter: "180",
    mlp_learning_rate: "0.001",
    mlp_alpha: "0.002",
    mlp_early_stopping: "true",
  },
  aggressive: {
    ml_buy_threshold: "0.60",
    ml_sell_threshold: "0.28",
    ml_min_samples: "120",
    mlp_layer_size: "64",
    mlp_layer_count: "2",
    mlp_max_iter: "200",
    mlp_learning_rate: "0.001",
    mlp_alpha: "0.0015",
    mlp_early_stopping: "true",
  },
};

const cnnProfileDefaults = {
  conservative: {
    ml_buy_threshold: "0.64",
    ml_sell_threshold: "0.34",
    ml_min_samples: "160",
    cnn_window: "30",
    cnn_channels: "24",
    cnn_kernel_size: "3",
    cnn_layers: "2",
    cnn_max_iter: "140",
    cnn_learning_rate: "0.001",
    cnn_alpha: "0.003",
    cnn_dropout: "0.12",
    cnn_early_stopping: "true",
    cnn_sample_weight: "true",
  },
  balanced: {
    ml_buy_threshold: "0.60",
    ml_sell_threshold: "0.30",
    ml_min_samples: "140",
    cnn_window: "24",
    cnn_channels: "32",
    cnn_kernel_size: "3",
    cnn_layers: "2",
    cnn_max_iter: "160",
    cnn_learning_rate: "0.001",
    cnn_alpha: "0.002",
    cnn_dropout: "0.08",
    cnn_early_stopping: "true",
    cnn_sample_weight: "true",
  },
  aggressive: {
    ml_buy_threshold: "0.58",
    ml_sell_threshold: "0.26",
    ml_min_samples: "120",
    cnn_window: "20",
    cnn_channels: "48",
    cnn_kernel_size: "5",
    cnn_layers: "2",
    cnn_max_iter: "180",
    cnn_learning_rate: "0.0012",
    cnn_alpha: "0.0015",
    cnn_dropout: "0.08",
    cnn_early_stopping: "true",
    cnn_sample_weight: "true",
  },
};

const strategyKeys = ["conservative", "balanced", "aggressive", "custom"];
const buyScoreFields = strategyKeys.map((key) => `${key}_buy_score`);
const maxBuySignalFields = strategyKeys.map((key) => `${key}_max_buy_signals`);
const mlCommonFields = ["ml_training_days", "ml_training_mode", "ml_target", "ml_buy_threshold", "ml_sell_threshold", "ml_min_samples"];
const localFields = ["local_strategy_profile", ...buyScoreFields];
const aiFields = ["ai_strategy_profile"];
const rfCommonFields = ["rf_strategy_profile", "rf_training_days", "rf_training_mode", "rf_target", "rf_buy_threshold", "rf_sell_threshold", "rf_min_samples"];
const hgbCommonFields = ["hgb_strategy_profile", "hgb_training_days", "hgb_training_mode", "hgb_target", "hgb_buy_threshold", "hgb_sell_threshold", "hgb_min_samples"];
const xgbCommonFields = ["xgb_strategy_profile", "xgb_training_days", "xgb_training_mode", "xgb_target", "xgb_buy_threshold", "xgb_sell_threshold", "xgb_min_samples"];
const mlpCommonFields = ["mlp_strategy_profile", "mlp_training_days", "mlp_training_mode", "mlp_target", "mlp_buy_threshold", "mlp_sell_threshold", "mlp_min_samples"];
const cnnCommonFields = ["cnn_strategy_profile", "cnn_training_days", "cnn_training_mode", "cnn_target", "cnn_buy_threshold", "cnn_sell_threshold", "cnn_min_samples"];
const rfTuningFields = ["rf_buy_threshold", "rf_sell_threshold", "rf_min_samples", "rf_n_estimators", "rf_max_depth", "rf_min_samples_leaf", "rf_max_features_sqrt", "rf_class_weight", "rf_sample_weight"];
const hgbTuningFields = [
  "hgb_buy_threshold",
  "hgb_sell_threshold",
  "hgb_min_samples",
  "hgb_max_iter",
  "hgb_learning_rate",
  "hgb_max_leaf_nodes",
  "hgb_min_samples_leaf",
  "hgb_l2_regularization",
  "hgb_early_stopping",
  "hgb_sample_weight",
];
const xgbTuningFields = [
  "xgb_buy_threshold",
  "xgb_sell_threshold",
  "xgb_min_samples",
  "xgb_n_estimators",
  "xgb_max_depth",
  "xgb_learning_rate",
  "xgb_subsample",
  "xgb_colsample_bytree",
  "xgb_min_child_weight",
  "xgb_reg_lambda",
  "xgb_sample_weight",
];
const mlpTuningFields = [
  "mlp_buy_threshold",
  "mlp_sell_threshold",
  "mlp_min_samples",
  "mlp_layer_size",
  "mlp_layer_count",
  "mlp_max_iter",
  "mlp_learning_rate",
  "mlp_alpha",
  "mlp_early_stopping",
];
const cnnTuningFields = [
  "cnn_buy_threshold",
  "cnn_sell_threshold",
  "cnn_min_samples",
  "cnn_window",
  "cnn_channels",
  "cnn_kernel_size",
  "cnn_layers",
  "cnn_max_iter",
  "cnn_learning_rate",
  "cnn_alpha",
  "cnn_dropout",
  "cnn_early_stopping",
  "cnn_sample_weight",
];
const rfFields = [...rfCommonFields, "rf_n_estimators", "rf_max_depth", "rf_min_samples_leaf", "rf_max_features_sqrt", "rf_class_weight", "rf_sample_weight"];
const hgbFields = [
  ...hgbCommonFields,
  "hgb_max_iter",
  "hgb_learning_rate",
  "hgb_max_leaf_nodes",
  "hgb_min_samples_leaf",
  "hgb_l2_regularization",
  "hgb_early_stopping",
  "hgb_sample_weight",
];
const xgbFields = [
  ...xgbCommonFields,
  "xgb_n_estimators",
  "xgb_max_depth",
  "xgb_learning_rate",
  "xgb_subsample",
  "xgb_colsample_bytree",
  "xgb_min_child_weight",
  "xgb_reg_lambda",
  "xgb_sample_weight",
];
const mlpFields = [
  ...mlpCommonFields,
  "mlp_layer_size",
  "mlp_layer_count",
  "mlp_max_iter",
  "mlp_learning_rate",
  "mlp_alpha",
  "mlp_early_stopping",
];
const cnnFields = [
  ...cnnCommonFields,
  "cnn_window",
  "cnn_channels",
  "cnn_kernel_size",
  "cnn_layers",
  "cnn_max_iter",
  "cnn_learning_rate",
  "cnn_alpha",
  "cnn_dropout",
  "cnn_early_stopping",
  "cnn_sample_weight",
];

const fmtMoney = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  maximumFractionDigits: 2,
});

const fmtNumber = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 2,
});

const stockNames = {
  "510300.SH": "沪深300ETF",
  "510300.SS": "沪深300ETF",
  "510300": "沪深300ETF",
  "510500.SH": "中证500ETF",
  "510500.SS": "中证500ETF",
  "510500": "中证500ETF",
  "159915.SZ": "创业板ETF",
  "159915": "创业板ETF",
  "512100.SH": "中证1000ETF",
  "512100.SS": "中证1000ETF",
  "512100": "中证1000ETF",
  "588000.SH": "科创50ETF",
  "588000.SS": "科创50ETF",
  "588000": "科创50ETF",
  "512880.SH": "证券ETF",
  "512880.SS": "证券ETF",
  "512880": "证券ETF",
  "512890.SH": "红利低波ETF",
  "512890.SS": "红利低波ETF",
  "512890": "红利低波ETF",
  "518880.SH": "黄金ETF",
  "518880.SS": "黄金ETF",
  "518880": "黄金ETF",
  "511260.SH": "十年国债ETF",
  "511260.SS": "十年国债ETF",
  "511260": "十年国债ETF",
  "513100.SH": "纳指ETF",
  "513100.SS": "纳指ETF",
  "513100": "纳指ETF",
};

const strategyProfiles = [
  {
    key: "conservative",
    label: "保守",
    buyScore: 6,
    sellScore: 4,
    trendMin: 0.002,
    momentum5Min: 0.008,
    momentum5Strong: 0.018,
    reversalMomentum5: 0.045,
    reversalChange: 0.025,
    rsiMin: 45,
    rsiMax: 72,
    overboughtRsi: 78,
    maxVolatility: 0.045,
    maxBuysPerDay: 1,
    allowAddPosition: false,
  },
  {
    key: "balanced",
    label: "均衡",
    buyScore: 5,
    sellScore: 4,
    trendMin: 0.0,
    momentum5Min: 0.004,
    momentum5Strong: 0.012,
    reversalMomentum5: 0.035,
    reversalChange: 0.018,
    rsiMin: 40,
    rsiMax: 76,
    overboughtRsi: 82,
    maxVolatility: 0.055,
    maxBuysPerDay: 2,
    allowAddPosition: false,
  },
  {
    key: "aggressive",
    label: "激进",
    buyScore: 4,
    sellScore: 5,
    trendMin: -0.004,
    momentum5Min: 0.0,
    momentum5Strong: 0.008,
    reversalMomentum5: 0.025,
    reversalChange: 0.012,
    rsiMin: 35,
    rsiMax: 84,
    overboughtRsi: 88,
    maxVolatility: 0.075,
    maxBuysPerDay: 3,
    allowAddPosition: true,
  },
  {
    key: "custom",
    label: "自定义",
    buyScore: 5,
    sellScore: 4,
    trendMin: 0,
    momentum5Min: 0.004,
    momentum5Strong: 0.012,
    reversalMomentum5: 0.035,
    reversalChange: 0.018,
    rsiMin: 40,
    rsiMax: 76,
    overboughtRsi: 82,
    maxVolatility: 0.055,
    maxBuysPerDay: 2,
    allowAddPosition: false,
  },
];

function pct(value) {
  return `${(Number(value || 0) * 100).toFixed(2)}%`;
}

function money(value) {
  return fmtMoney.format(Number(value || 0));
}

function pctShort(value) {
  return `${(Number(value || 0) * 100).toFixed(2)}%`;
}

function sideText(side) {
  if (side === "buy") return "买入";
  if (side === "sell") return "卖出";
  return "观望";
}

function stockName(symbol) {
  const key = String(symbol || "").trim().toUpperCase();
  return stockNames[key] || key || "--";
}

function stockLabel(symbol) {
  const key = String(symbol || "").trim().toUpperCase();
  const name = stockName(key);
  if (!key || name === key) {
    return `<span class="stock-name">${escapeHtml(name)}</span>`;
  }
  return `<span class="stock-name">${escapeHtml(name)}</span><span class="stock-code">${escapeHtml(key)}</span>`;
}

function symbolNamesPreview(symbols) {
  const names = String(symbols || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((symbol) => stockName(symbol));
  return names.length ? `当前显示：${names.join("、")}` : "";
}

function parseYearRegimeOverrides(value) {
  if (!value) return {};
  if (typeof value === "object") return { ...value };
  try {
    const parsed = JSON.parse(String(value));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function regimeText(regime) {
  if (regime === "bull") return "大涨/牛市";
  if (regime === "bear") return "下跌/熊市";
  return "震荡";
}

function regimeStrategyText(regime) {
  if (regime === "bull") return "偏激进";
  if (regime === "bear") return "偏保守";
  return "偏均衡";
}

function regimeClass(regime) {
  if (regime === "bull") return "bull";
  if (regime === "bear") return "bear";
  return "sideways";
}

function miniTrendSvg(curve = [], regime = "sideways") {
  const points = curve
    .map((item) => Number(item.close))
    .filter((value) => Number.isFinite(value) && value > 0);
  if (points.length < 2) {
    return '<div class="mini-trend empty">暂无走势</div>';
  }
  const width = 160;
  const height = 46;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const polyline = points
    .map((value, index) => {
      const x = (index / (points.length - 1)) * width;
      const y = height - 4 - ((value - min) / span) * (height - 8);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const cls = regimeClass(regime);
  return `
    <svg class="mini-trend mini-trend-${cls}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
      <line x1="0" y1="${height - 4}" x2="${width}" y2="${height - 4}"></line>
      <polyline points="${polyline}"></polyline>
    </svg>
  `;
}

function updateYearRegimeHiddenFromControls() {
  const current = parseYearRegimeOverrides(fieldValue("year_regime_overrides"));
  document.querySelectorAll("[data-year-regime-key]").forEach((select) => {
    const key = select.dataset.yearRegimeKey;
    const value = select.value;
    if (!key) return;
    if (!value || value === "auto") {
      delete current[key];
    } else {
      current[key] = value;
    }
  });
  setFieldValue("year_regime_overrides", JSON.stringify(current));
}

function renderYearRegimeLabels(rows = [], overrides = {}) {
  const el = document.getElementById("yearRegimeLabels");
  if (!el) return;
  const overrideMap = parseYearRegimeOverrides(overrides);
  setFieldValue("year_regime_overrides", JSON.stringify(overrideMap));
  if (!rows.length) {
    el.className = "year-regime-list empty";
    el.textContent = "暂无标签。点击刷新标签，或先运行/回测一次拉取历史行情。";
    return;
  }
  el.className = "year-regime-list";
  const bySymbol = new Map();
  for (const row of rows) {
    const symbol = String(row.symbol || "").toUpperCase();
    if (!bySymbol.has(symbol)) bySymbol.set(symbol, []);
    bySymbol.get(symbol).push(row);
  }
  el.innerHTML = [...bySymbol.entries()]
    .map(([symbol, items]) => {
      const years = items
        .sort((a, b) => Number(b.year) - Number(a.year))
        .map((row) => {
          const key = row.key || `${symbol}:${row.year}`;
          const autoRegime = row.auto_regime || row.regime || "sideways";
          const selected = overrideMap[key] || "auto";
          const manualBadge = selected === "auto" ? "" : '<span class="regime-badge manual">手动</span>';
          const shownRegime = selected === "auto" ? autoRegime : selected;
          return `
            <div class="year-regime-row">
              <div>
                <strong>${escapeHtml(row.year)}</strong>
                <span>${escapeHtml(regimeText(autoRegime))} / ${escapeHtml(regimeStrategyText(autoRegime))}</span>
                ${manualBadge}
              </div>
              <div class="year-regime-chart">${miniTrendSvg(row.curve || [], shownRegime)}</div>
              <div class="year-regime-metrics">
                <span>收益 ${pctShort(row.annual_return)}</span>
                <span>回撤 ${pctShort(row.max_drawdown)}</span>
                <span>波动 ${pctShort(row.volatility)}</span>
              </div>
              <select data-year-regime-key="${escapeHtml(key)}" data-auto-regime="${escapeHtml(autoRegime)}">
                <option value="auto"${selected === "auto" ? " selected" : ""}>自动：${escapeHtml(regimeText(autoRegime))}</option>
                <option value="bull"${selected === "bull" ? " selected" : ""}>大涨/牛市：训练激进</option>
                <option value="sideways"${selected === "sideways" ? " selected" : ""}>震荡：训练均衡</option>
                <option value="bear"${selected === "bear" ? " selected" : ""}>下跌/熊市：训练保守</option>
              </select>
            </div>
          `;
        })
        .join("");
      return `
        <details class="year-regime-symbol">
          <summary>
            <span>${stockLabel(symbol)}</span>
            <small>${items.length} 年</small>
          </summary>
          <div class="year-regime-rows">${years}</div>
        </details>
      `;
    })
    .join("");
}

function renderAutoLearnResult(result) {
  const el = document.getElementById("autoLearnResult");
  if (!el) return;
  if (!result) {
    state.autoLearnResult = null;
    el.className = "auto-learn-result empty";
    el.textContent = "运行后显示推荐参数和各年验证结果";
    return;
  }
  state.autoLearnResult = result;
  const best = result.best || {};
  const profileSuggestions = result.best_by_profile || {};
  const rows = result.results || [];
  const ranking = rows
    .slice(0, 20)
    .map((item) => {
      return `
        <tr>
          <td>${escapeHtml(item.rank || "")}</td>
          <td>${escapeHtml(item.label || "--")}</td>
          <td>${escapeHtml(autoLearnPhaseText(item.phase))}</td>
          <td>${escapeHtml(item.target_regime_label || "--")}</td>
          <td class="${Number(item.avg_return_pct || 0) >= 0 ? "positive" : "negative"}">${pct(item.avg_return_pct)}</td>
          <td class="${Number(item.avg_excess_return_pct || 0) >= 0 ? "positive" : "negative"}">${pct(item.avg_excess_return_pct)}</td>
          <td>${escapeHtml(Number(item.specialized_score || 0).toFixed(4))}</td>
          <td>${escapeHtml(Number(item.fallback_score || 0).toFixed(4))}</td>
          <td>${escapeHtml(Number(item.composite_score || 0).toFixed(4))}</td>
          <td>${escapeHtml(`${item.beat_buy_hold_count || 0}/${item.valid_year_count || 0}`)}</td>
          <td>${escapeHtml(autoLearnValidYearText(item))}</td>
          <td>${escapeHtml(Number(item.score || 0).toFixed(4))}</td>
        </tr>
      `;
    })
    .join("");
  el.className = "auto-learn-result";
  const rounds = (result.rounds || [])
    .map((round) => `
      <tr>
        <td>${escapeHtml(round.round)}</td>
        <td>${escapeHtml(round.candidate_count || 0)}</td>
        <td>${escapeHtml(round.best_label || "--")}</td>
        <td>${escapeHtml(Number(round.best_score || 0).toFixed(4))}</td>
        <td>${round.improved ? "是" : "否"}</td>
        <td>${escapeHtml(round.stale_rounds || 0)}</td>
      </tr>
    `)
    .join("");
  const weights = result.score_weights || {};
  const suggestionCards = ["aggressive", "balanced", "conservative"]
    .map((profile) => autoLearnSuggestionCard(profile, profileSuggestions[profile], result.model))
    .join("");
  el.innerHTML = `
    <div class="auto-learn-summary">
      <div><span>模型</span><strong>${escapeHtml(result.model_label || mlModelText(result.model))}</strong></div>
      <div><span>验证年份</span><strong>${escapeHtml((result.years || []).join(", "))}</strong></div>
      <div><span>轮数</span><strong>${escapeHtml(result.round_count || 0)}</strong></div>
      <div><span>候选数量</span><strong>${escapeHtml(result.candidate_count || 0)}${result.returned_candidate_count ? ` / 显示${escapeHtml(result.returned_candidate_count)}` : ""}</strong></div>
      <div><span>耗时</span><strong>${escapeHtml(result.elapsed_seconds || 0)} 秒</strong></div>
    </div>
    <details class="auto-learn-section" open>
      <summary>
        <span>三档建议</span>
        <small>激进、均衡、保守分别保留自己的最优参数</small>
      </summary>
      <p>可以一键应用三档，也可以只应用其中一档。应用后会立即保存，并同步到机器学习设置。</p>
      <div class="form-actions">
        <button type="button" data-auto-learn-apply="all">一键应用三档建议</button>
        <button type="button" data-auto-learn-apply="global">只应用全局最佳</button>
      </div>
      <div class="auto-learn-suggestions">${suggestionCards}</div>
    </details>
    <details class="auto-learn-section">
      <summary>
        <span>全局最佳</span>
        <small>${escapeHtml(best.label || "--")}</small>
      </summary>
      <p>${escapeHtml(best.reason || result.apply_note || "")}</p>
      <div class="auto-learn-summary compact">
        <div><span>平均收益</span><strong class="${Number(best.avg_return_pct || 0) >= 0 ? "positive" : "negative"}">${pct(best.avg_return_pct)}</strong></div>
        <div><span>最差年份</span><strong class="${Number(best.worst_return_pct || 0) >= 0 ? "positive" : "negative"}">${pct(best.worst_return_pct)}</strong></div>
        <div><span>平均超额</span><strong class="${Number(best.avg_excess_return_pct || 0) >= 0 ? "positive" : "negative"}">${pct(best.avg_excess_return_pct)}</strong></div>
        <div><span>跑赢持有</span><strong>${escapeHtml(`${best.beat_buy_hold_count || 0}/${best.valid_year_count || 0}`)}</strong></div>
        <div><span>有效年份</span><strong>${escapeHtml(autoLearnValidYearText(best))}</strong></div>
        <div><span>目标行情</span><strong>${escapeHtml(best.target_regime_label || "--")}</strong></div>
        <div><span>专项/兜底/综合</span><strong>${escapeHtml(`${Number(best.specialized_score || 0).toFixed(3)} / ${Number(best.fallback_score || 0).toFixed(3)} / ${Number(best.composite_score || 0).toFixed(3)}`)}</strong></div>
      </div>
      ${autoLearnYearTable(best.per_year || [])}
      <pre>${escapeHtml(JSON.stringify(best.payload || {}, null, 2))}</pre>
    </details>
    <div class="reason">评分权重：专项 ${escapeHtml(weights.raw_specialized ?? "--")} / 兜底 ${escapeHtml(weights.raw_fallback ?? "--")} / 综合 ${escapeHtml(weights.raw_composite ?? "--")}。${escapeHtml(result.stop_reason || "")}</div>
    <details class="auto-learn-section">
      <summary><span>完整迭代记录</span><small>${escapeHtml((result.rounds || []).length)} 轮</small></summary>
      <div class="table-wrap auto-learn-table-scroll">
        <table>
          <thead><tr><th>轮数</th><th>候选</th><th>本轮最佳</th><th>本轮分</th><th>提升</th><th>无提升轮数</th></tr></thead>
          <tbody>${rounds || '<tr><td colspan="6">暂无迭代记录</td></tr>'}</tbody>
        </table>
      </div>
    </details>
    <details class="auto-learn-section">
      <summary><span>候选排名</span><small>显示前 ${escapeHtml(Math.min(20, rows.length))} / ${escapeHtml(rows.length)}</small></summary>
      <div class="table-wrap auto-learn-table-scroll">
        <table>
          <thead><tr><th>排名</th><th>候选</th><th>类别</th><th>目标</th><th>平均收益</th><th>平均超额</th><th>专项</th><th>兜底</th><th>综合</th><th>跑赢持有</th><th>有效年份</th><th>评分</th></tr></thead>
          <tbody>${ranking || '<tr><td colspan="12">暂无候选结果</td></tr>'}</tbody>
        </table>
      </div>
    </details>
    <div class="reason">${escapeHtml(result.accuracy_note || "")}<br>${escapeHtml(result.apply_note || "")}</div>
  `;
}

function autoLearnValidYearText(item) {
  const valid = Number(item && item.valid_year_count || 0);
  const expected = Number(item && item.expected_year_count || 0);
  if (expected > 0) return `${valid}/${expected}`;
  return `${valid}`;
}

function autoLearnSuggestionCard(profile, item, model) {
  const label = strategyText(profile);
  if (!item) {
    return `<div class="auto-learn-suggestion"><h4>${escapeHtml(label)}建议</h4><p>本轮没有跑出可用建议。</p></div>`;
  }
  const payload = item.payload || {};
  const changes = autoLearnParamAdvice(payload, model);
  return `
    <details class="auto-learn-suggestion" open>
      <summary>
        <span>${escapeHtml(label)}最佳</span>
        <small>${escapeHtml(item.label || "--")}</small>
      </summary>
      <div class="auto-learn-candidate-name">候选：${escapeHtml(item.label || "--")} · ${escapeHtml(autoLearnPhaseText(item.phase))}</div>
      <div class="auto-learn-summary compact">
        <div><span>目标行情</span><strong>${escapeHtml(item.target_regime_label || "--")}</strong></div>
        <div><span>平均收益</span><strong class="${Number(item.avg_return_pct || 0) >= 0 ? "positive" : "negative"}">${pct(item.avg_return_pct)}</strong></div>
        <div><span>最差年份</span><strong class="${Number(item.worst_return_pct || 0) >= 0 ? "positive" : "negative"}">${pct(item.worst_return_pct)}</strong></div>
        <div><span>平均超额</span><strong class="${Number(item.avg_excess_return_pct || 0) >= 0 ? "positive" : "negative"}">${pct(item.avg_excess_return_pct)}</strong></div>
        <div><span>跑赢持有</span><strong>${escapeHtml(`${item.beat_buy_hold_count || 0}/${item.valid_year_count || 0}`)}</strong></div>
        <div><span>有效年份</span><strong>${escapeHtml(autoLearnValidYearText(item))}</strong></div>
        <div><span>评分</span><strong>${escapeHtml(Number(item.score || 0).toFixed(4))}</strong></div>
      </div>
      ${autoLearnYearTable(item.per_year || [])}
      <p>${escapeHtml(item.reason || "")}</p>
      <ul>${changes.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
      <div class="form-actions">
        <button type="button" data-auto-learn-apply="${escapeHtml(profile)}">应用到${escapeHtml(label)}档</button>
      </div>
    </details>
  `;
}

function autoLearnYearTable(items) {
  const rows = (items || [])
    .map((item) => {
      if (item.error) {
        return `<tr><td>${escapeHtml(item.year)}</td><td colspan="6" class="negative">${escapeHtml(item.error)}</td></tr>`;
      }
      return `
        <tr>
          <td>${escapeHtml(item.year)}</td>
          <td>${escapeHtml(item.regime_label || "--")}</td>
          <td class="${Number(item.return_pct || 0) >= 0 ? "positive" : "negative"}">${pct(item.return_pct)}</td>
          <td>${pct(item.buy_hold_return_pct)}</td>
          <td class="${Number(item.excess_return_pct || 0) >= 0 ? "positive" : "negative"}">${pct(item.excess_return_pct)}</td>
          <td>${pct(item.max_drawdown_pct)}</td>
          <td>${escapeHtml(item.trade_count ?? "--")}</td>
        </tr>
      `;
    })
    .join("");
  return `
    <div class="table-wrap compact-table">
      <table>
        <thead><tr><th>年份</th><th>行情</th><th>策略收益</th><th>持有收益</th><th>超额</th><th>最大回撤</th><th>交易</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="7">暂无年份结果</td></tr>'}</tbody>
      </table>
    </div>
  `;
}

function autoLearnPhaseText(phase) {
  const map = {
    default: "默认",
    incumbent: "沿用最佳",
    coarse: "粗搜",
    fine: "细搜",
    random: "随机",
    model: "模型参数",
  };
  return map[phase] || phase || "--";
}

function autoLearnParamAdvice(payload, model) {
  const prefix = model === "xgboost" ? "xgb" : model === "mlp" ? "mlp" : model === "cnn" ? "cnn" : model === "hist_gradient_boosting" ? "hgb" : "rf";
  const lines = [];
  const buy = payload[`${prefix}_buy_threshold`];
  const sell = payload[`${prefix}_sell_threshold`];
  const minSamples = payload[`${prefix}_min_samples`];
  if (buy !== undefined && buy !== "") lines.push(`买入阈值建议为 ${buy}：越低越容易买入，越高越谨慎。`);
  if (sell !== undefined && sell !== "") lines.push(`卖出阈值建议为 ${sell}：越低越愿意持有，越高越容易卖出。`);
  if (minSamples !== undefined && minSamples !== "") lines.push(`最小训练样本建议为 ${minSamples}：越高越稳，但样本不足时更容易回退。`);
  const modelKeys = Object.keys(payload).filter((key) => key.startsWith(`${prefix}_`) && !key.includes("threshold") && !key.endsWith("_min_samples") && !key.endsWith("_strategy_profile"));
  for (const key of modelKeys.slice(0, 4)) {
    lines.push(`${key} 建议为 ${payload[key]}。`);
  }
  if (!lines.length) {
    lines.push("该建议主要沿用当前档位默认参数，可作为基线对照。");
  }
  return lines;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    const plain = text.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    throw new Error(plain.slice(0, 160) || "服务返回了非 JSON 内容，请确认本地网页服务仍在运行。");
  }
  if (!response.ok) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

function formPayload() {
  syncTrainingPlanFields();
  syncActiveMlFields();
  updateYearRegimeHiddenFromControls();
  const payload = {};
  for (const id of fields) {
    payload[id] = fieldValue(id);
  }
  setMlOverrides(fieldValue("ml_profile_overrides") || state.mlProfileOverrides);
  payload.ml_profile_overrides = state.mlProfileOverrides;
  const prefix = activeMlPrefix();
  const activeModelStrategy = fieldValue(`${prefix}_strategy_profile`) || fieldDefaults[`${prefix}_strategy_profile`];
  payload[`${prefix}_strategy_profile`] = activeModelStrategy;
  payload.ml_strategy_profile = activeModelStrategy;
  payload.ml_training_days = fieldValue(`${prefix}_training_days`) || fieldDefaults[`${prefix}_training_days`];
  payload.ml_training_mode = fieldValue(`${prefix}_training_mode`) || fieldDefaults[`${prefix}_training_mode`];
  payload.ml_target = fieldValue(`${prefix}_target`) || fieldDefaults[`${prefix}_target`];
  payload.ml_buy_threshold = fieldValue(`${prefix}_buy_threshold`);
  payload.ml_sell_threshold = fieldValue(`${prefix}_sell_threshold`);
  payload.ml_min_samples = fieldValue(`${prefix}_min_samples`) || fieldDefaults[`${prefix}_min_samples`];
  payload.strategy_profile = activeStrategyProfile();
  if (!payload.local_strategy_profile) payload.local_strategy_profile = payload.strategy_profile;
  if (!payload.ml_strategy_profile) payload.ml_strategy_profile = payload.strategy_profile;
  if (!payload.ai_strategy_profile) payload.ai_strategy_profile = payload.strategy_profile;
  return payload;
}

function normalizeTrainingPlan(value) {
  const raw = String(value || "").trim().toLowerCase().replace(/-/g, "_");
  if (["classified", "regime", "regime_classified", "by_regime", "year_regime"].includes(raw)) return "classified";
  if (["mixed", "all", "all_mixed", "general", "general_all"].includes(raw)) return "mixed";
  if (["single", "single_symbol", "per_symbol", "symbol", "auto", "adaptive"].includes(raw)) return "self";
  return "self";
}

function syncTrainingPlanFields() {
  const mode = normalizeTrainingPlan(fieldValue("rf_training_mode") || fieldDefaults.rf_training_mode);
  for (const id of ["rf_training_mode", "hgb_training_mode", "xgb_training_mode", "mlp_training_mode", "cnn_training_mode", "ml_training_mode"]) {
    setFieldValue(id, mode);
  }
  setFieldValue("training_pool_mode", "training_plus_trade");
  setFieldValue("training_regime_filter", mode === "classified" ? "on" : "off");
  setFieldValue("training_volatility_filter", "off");
}

function fieldValue(id) {
  const el = document.getElementById(id);
  if (el && el.type === "checkbox") {
    return el.checked ? "true" : "false";
  }
  return el ? el.value : fieldDefaults[id] || "";
}

function parseJsonObject(value, fallback = {}) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value;
  }
  try {
    const parsed = JSON.parse(String(value || "{}"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : fallback;
  } catch {
    return fallback;
  }
}

function activeStrategyProfile() {
  const engine = fieldValue("run_engine");
  if (engine === "ml") return fieldValue("ml_strategy_profile") || fieldDefaults.ml_strategy_profile;
  if (engine === "ai") return fieldValue("ai_strategy_profile") || fieldDefaults.ai_strategy_profile;
  return fieldValue("local_strategy_profile") || fieldDefaults.local_strategy_profile;
}

function syncActiveStrategyField() {
  setFieldValue("strategy_profile", activeStrategyProfile());
}

function activeMlPrefix() {
  const model = fieldValue("ml_model");
  if (model === "xgboost") return "xgb";
  if (model === "mlp") return "mlp";
  if (model === "cnn") return "cnn";
  return model === "hist_gradient_boosting" ? "hgb" : "rf";
}

function activeMlStrategyProfile() {
  return fieldValue(`${activeMlPrefix()}_strategy_profile`) || fieldDefaults[`${activeMlPrefix()}_strategy_profile`];
}

function syncActiveMlFields() {
  const prefix = activeMlPrefix();
  setFieldValue("ml_strategy_profile", fieldValue(`${prefix}_strategy_profile`) || fieldDefaults[`${prefix}_strategy_profile`]);
  setFieldValue("ml_training_days", fieldValue(`${prefix}_training_days`) || fieldDefaults[`${prefix}_training_days`]);
  setFieldValue("ml_training_mode", fieldValue(`${prefix}_training_mode`) || fieldDefaults[`${prefix}_training_mode`]);
  setFieldValue("ml_target", fieldValue(`${prefix}_target`) || fieldDefaults[`${prefix}_target`]);
  setFieldValue("ml_buy_threshold", fieldValue(`${prefix}_buy_threshold`));
  setFieldValue("ml_sell_threshold", fieldValue(`${prefix}_sell_threshold`));
  setFieldValue("ml_min_samples", fieldValue(`${prefix}_min_samples`) || fieldDefaults[`${prefix}_min_samples`]);
}

function normalizeMlProfileOverrides(value) {
  const raw = parseJsonObject(value, {});
  const clean = {};
  for (const [model, byProfile] of Object.entries(raw)) {
    if (!byProfile || typeof byProfile !== "object" || Array.isArray(byProfile)) continue;
    clean[model] = {};
    for (const [profile, params] of Object.entries(byProfile)) {
      if (!params || typeof params !== "object" || Array.isArray(params)) continue;
      clean[model][profile] = { ...params };
    }
    if (!Object.keys(clean[model]).length) delete clean[model];
  }
  return clean;
}

function modelNameForPrefix(prefix) {
  if (prefix === "xgb") return "xgboost";
  if (prefix === "hgb") return "hist_gradient_boosting";
  if (prefix === "mlp") return "mlp";
  if (prefix === "cnn") return "cnn";
  return "random_forest";
}

function setMlOverrides(overrides) {
  state.mlProfileOverrides = normalizeMlProfileOverrides(overrides);
  setFieldValue("ml_profile_overrides", JSON.stringify(state.mlProfileOverrides));
}

function activeProfileOverride(model, profile) {
  const normalized = normalizeMlProfileOverrides(state.mlProfileOverrides);
  return ((normalized[model] || {})[profile] || null);
}

function applyMlOverrideToFields(model, profile) {
  const override = activeProfileOverride(model, profile);
  if (!override) return false;
  const prefix = model === "xgboost" ? "xgb" : model === "hist_gradient_boosting" ? "hgb" : model === "mlp" ? "mlp" : model === "cnn" ? "cnn" : "rf";
  for (const [key, value] of Object.entries(override)) {
    if (key.startsWith("ml_")) {
      setFieldValue(key.replace("ml_", `${prefix}_`), value);
    } else if (key.startsWith(`${prefix}_`)) {
      setFieldValue(key, value);
    }
  }
  syncActiveMlFields();
  return true;
}

function setFieldValue(id, value) {
  const el = document.getElementById(id);
  if (el) {
    if (el.type === "checkbox") {
      el.checked = value === true || String(value).toLowerCase() === "true";
      return;
    }
    el.value = value;
  }
}

function setBusy(value) {
  state.busy = value;
  document.getElementById("runBtn").disabled = value;
  document.getElementById("resetBtn").disabled = value;
  document.getElementById("autoBtn").disabled = value;
  document.getElementById("backtestBtn").disabled = value;
  document.getElementById("manualBuyBtn").disabled = value;
  document.getElementById("saveSettingsBtn").disabled = value;
  document.getElementById("resetSettingsBtn").disabled = value;
  const refreshYearRegimesBtn = document.getElementById("refreshYearRegimesBtn");
  if (refreshYearRegimesBtn) refreshYearRegimesBtn.disabled = value;
  const autoLearnBtn = document.getElementById("autoLearnBtn");
  if (autoLearnBtn) autoLearnBtn.disabled = value;
  const stopAutoLearnBtn = document.getElementById("stopAutoLearnBtn");
  if (stopAutoLearnBtn) stopAutoLearnBtn.disabled = value && !state.autoLearnTaskId;
  document.getElementById("status").textContent = value ? "运行中" : "就绪";
}

function switchView(targetId) {
  state.activeView = targetId;
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === targetId);
  });
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.target === targetId);
  });
  syncTopbar();
  syncSettingsTabs();
}

function syncTopbar() {
  const title = document.getElementById("topTitle");
  const dashboardActions = document.getElementById("dashboardTopActions");
  const settingsActions = document.getElementById("settingsTopActions");
  const activeView = state.activeView || "dashboardView";
  if (title) {
    if (activeView === "settingsView") {
      title.textContent = "设置";
    } else if (activeView === "testView") {
      title.textContent = "测试";
    } else {
      title.textContent = "AI 模拟炒股";
    }
  }
  if (dashboardActions) {
    dashboardActions.classList.toggle("hidden", activeView !== "dashboardView");
  }
  if (settingsActions) {
    settingsActions.classList.toggle("hidden", activeView !== "settingsView");
  }
}

const settingsTabs = [
  ["account", "账户"],
  ["local", "本地"],
  ["ml", "机器学习"],
  ["training", "训练"],
  ["ai", "AI"],
  ["docs", "规则"],
];

function setupSettingsTabs() {
  const settingsView = document.getElementById("settingsView");
  const settingsGrid = document.querySelector("#settingsView .settings-grid");
  if (!settingsView || !settingsGrid) return;

  const panels = Array.from(settingsGrid.querySelectorAll(":scope > aside.controls"));
  if (panels[0]) panels[0].dataset.settingsTab = "account";
  if (panels[1]) panels[1].dataset.settingsTab = "account";

  const assignments = {
    localControls: "local",
    mlCommonControls: "ml",
    aiControls: "ai",
    rfControls: "ml",
    hgbControls: "ml",
    xgbControls: "ml",
    mlpControls: "ml",
    cnnControls: "ml",
    strategyDocs: "docs",
  };
  for (const [id, tab] of Object.entries(assignments)) {
    const panel = document.getElementById(id);
    if (panel) panel.dataset.settingsTab = tab;
  }
  for (const id of ["rfControls", "hgbControls", "xgbControls", "mlpControls", "cnnControls"]) {
    const panel = document.getElementById(id);
    if (panel) panel.dataset.modelPanel = id.replace("Controls", "");
  }

  let subnav = document.getElementById("settingsSubnav");
  if (!subnav) {
    subnav = document.createElement("div");
    subnav.id = "settingsSubnav";
    subnav.className = "settings-subnav";
    settingsGrid.insertAdjacentElement("beforebegin", subnav);
  }
  if (!subnav.querySelector(".settings-tab")) {
    subnav.innerHTML = settingsTabs
      .map(([key, label]) => `<button type="button" class="settings-tab" data-settings-target="${key}">${label}</button>`)
      .join("");
  }
  if (!subnav.dataset.bound) {
    subnav.addEventListener("click", (event) => {
      const button = event.target.closest("[data-settings-target]");
      if (!button) return;
      state.activeSettingsTab = button.dataset.settingsTarget;
      syncSettingsTabs();
    });
    subnav.dataset.bound = "true";
  }
}

function syncSettingsTabs() {
  setupSettingsTabs();
  const activeTab = state.activeSettingsTab || "account";
  document.querySelectorAll("#settingsSubnav .settings-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.settingsTarget === activeTab);
  });
  const model = fieldValue("ml_model");
  const activeModelPanel =
    model === "hist_gradient_boosting" ? "hgb" : model === "xgboost" ? "xgb" : model === "mlp" ? "mlp" : model === "cnn" ? "cnn" : "rf";
  document.querySelectorAll("#settingsView [data-settings-tab]").forEach((panel) => {
    let visible = panel.dataset.settingsTab === activeTab;
    if (activeTab === "ml" && panel.dataset.modelPanel) {
      visible = panel.dataset.modelPanel === activeModelPanel;
    }
    panel.classList.toggle("settings-tab-hidden", !visible);
  });
  const docs = document.querySelector("#settingsView .strategy-docs-panel");
  if (docs) {
    docs.classList.toggle("settings-tab-hidden", activeTab !== "docs");
  }
}

function fillConfig(config, options = {}) {
  if (state.settingsDirty && !options.force) {
    return;
  }
  const configValue = (id, fallback) => {
    if (config && Object.prototype.hasOwnProperty.call(config, id) && config[id] !== undefined && config[id] !== null && String(config[id]) !== "") {
      return config[id];
    }
    const current = fieldValue(id);
    if (current !== undefined && current !== null && String(current) !== "") {
      return current;
    }
    return fallback;
  };
  setFieldValue("symbols", config.symbols);
  document.getElementById("symbolsPreview").textContent = symbolNamesPreview(config.symbols);
  setFieldValue("training_symbols", config.training_symbols || fieldDefaults.training_symbols);
  const trainingPreview = document.getElementById("trainingSymbolsPreview");
  if (trainingPreview) {
    trainingPreview.textContent = symbolNamesPreview(config.training_symbols || fieldDefaults.training_symbols);
  }
  setFieldValue("training_pool_mode", "training_plus_trade");
  setFieldValue("training_regime_filter", config.training_regime_filter || fieldDefaults.training_regime_filter);
  setFieldValue("training_volatility_filter", "off");
  setFieldValue("year_regime_overrides", JSON.stringify(parseYearRegimeOverrides(config.year_regime_overrides)));
  setMlOverrides(config.ml_profile_overrides || {});
  setFieldValue("strategy_auto_select", config.strategy_auto_select || fieldDefaults.strategy_auto_select);
  setFieldValue("starting_cash", config.starting_cash);
  setFieldValue("max_trade_cash", config.max_trade_cash);
  setFieldValue("max_order_shares", config.max_order_shares);
  setFieldValue("max_position_pct", Math.round(config.max_position_pct * 100));
  setFieldValue("stop_loss_pct", Math.round(config.stop_loss_pct * 100));
  setFieldValue("take_profit_pct", Math.round(config.take_profit_pct * 100));
  const activeStrategy = config.strategy_profile || "conservative";
  setFieldValue("strategy_profile", activeStrategy);
  setFieldValue("local_strategy_profile", config.local_strategy_profile || activeStrategy);
  for (const key of strategyKeys) {
    setFieldValue(`${key}_buy_score`, config[`${key}_buy_score`] ?? fieldDefaults[`${key}_buy_score`]);
    setFieldValue(`${key}_max_buy_signals`, config[`${key}_max_buy_signals`] ?? fieldDefaults[`${key}_max_buy_signals`]);
  }
  const activeMlStrategy = config.ml_strategy_profile || activeStrategy;
  setFieldValue("ml_strategy_profile", activeMlStrategy);
  setFieldValue("rf_strategy_profile", configValue("rf_strategy_profile", config.ml_model === "random_forest" ? activeMlStrategy : fieldDefaults.rf_strategy_profile));
  setFieldValue("hgb_strategy_profile", configValue("hgb_strategy_profile", config.ml_model === "hist_gradient_boosting" ? activeMlStrategy : fieldDefaults.hgb_strategy_profile));
  setFieldValue("xgb_strategy_profile", configValue("xgb_strategy_profile", config.ml_model === "xgboost" ? activeMlStrategy : fieldDefaults.xgb_strategy_profile));
  setFieldValue("mlp_strategy_profile", configValue("mlp_strategy_profile", config.ml_model === "mlp" ? activeMlStrategy : fieldDefaults.mlp_strategy_profile));
  setFieldValue("cnn_strategy_profile", configValue("cnn_strategy_profile", config.ml_model === "cnn" ? activeMlStrategy : fieldDefaults.cnn_strategy_profile));
  setFieldValue("ai_strategy_profile", config.ai_strategy_profile || activeStrategy);
  setFieldValue("run_engine", config.run_engine || "local");
  setFieldValue("ml_model", config.ml_model || "random_forest");
  setFieldValue("ml_training_days", config.ml_training_days || fieldDefaults.ml_training_days);
  const trainingPlan = normalizeTrainingPlan(
    config.ml_training_mode || config.rf_training_mode || config.hgb_training_mode || config.xgb_training_mode || config.mlp_training_mode || config.cnn_training_mode || fieldDefaults.ml_training_mode
  );
  setFieldValue("ml_training_mode", trainingPlan);
  setFieldValue("ml_target", config.ml_target || fieldDefaults.ml_target);
  setFieldValue("backtest_interval_minutes", config.backtest_interval_minutes || fieldDefaults.backtest_interval_minutes);
  setFieldValue("rf_training_days", config.rf_training_days || (config.ml_model === "random_forest" ? config.ml_training_days : fieldDefaults.rf_training_days));
  setFieldValue("rf_training_mode", trainingPlan);
  setFieldValue("rf_target", config.rf_target || (config.ml_model === "random_forest" ? config.ml_target : fieldDefaults.rf_target));
  setFieldValue("rf_buy_threshold", "rf_buy_threshold" in config ? config.rf_buy_threshold : (config.ml_model === "random_forest" ? config.ml_buy_threshold : fieldDefaults.rf_buy_threshold));
  setFieldValue("rf_sell_threshold", "rf_sell_threshold" in config ? config.rf_sell_threshold : (config.ml_model === "random_forest" ? config.ml_sell_threshold : fieldDefaults.rf_sell_threshold));
  setFieldValue("rf_min_samples", config.rf_min_samples || (config.ml_model === "random_forest" ? config.ml_min_samples : fieldDefaults.rf_min_samples));
  setFieldValue("hgb_training_days", config.hgb_training_days || (config.ml_model === "hist_gradient_boosting" ? config.ml_training_days : fieldDefaults.hgb_training_days));
  setFieldValue("hgb_training_mode", trainingPlan);
  setFieldValue("hgb_target", config.hgb_target || (config.ml_model === "hist_gradient_boosting" ? config.ml_target : fieldDefaults.hgb_target));
  setFieldValue("hgb_buy_threshold", "hgb_buy_threshold" in config ? config.hgb_buy_threshold : (config.ml_model === "hist_gradient_boosting" ? config.ml_buy_threshold : fieldDefaults.hgb_buy_threshold));
  setFieldValue("hgb_sell_threshold", "hgb_sell_threshold" in config ? config.hgb_sell_threshold : (config.ml_model === "hist_gradient_boosting" ? config.ml_sell_threshold : fieldDefaults.hgb_sell_threshold));
  setFieldValue("hgb_min_samples", config.hgb_min_samples || (config.ml_model === "hist_gradient_boosting" ? config.ml_min_samples : fieldDefaults.hgb_min_samples));
  setFieldValue("xgb_training_days", config.xgb_training_days || (config.ml_model === "xgboost" ? config.ml_training_days : fieldDefaults.xgb_training_days));
  setFieldValue("xgb_training_mode", trainingPlan);
  setFieldValue("xgb_target", config.xgb_target || (config.ml_model === "xgboost" ? config.ml_target : fieldDefaults.xgb_target));
  setFieldValue("xgb_buy_threshold", "xgb_buy_threshold" in config ? config.xgb_buy_threshold : (config.ml_model === "xgboost" ? config.ml_buy_threshold : fieldDefaults.xgb_buy_threshold));
  setFieldValue("xgb_sell_threshold", "xgb_sell_threshold" in config ? config.xgb_sell_threshold : (config.ml_model === "xgboost" ? config.ml_sell_threshold : fieldDefaults.xgb_sell_threshold));
  setFieldValue("xgb_min_samples", config.xgb_min_samples || (config.ml_model === "xgboost" ? config.ml_min_samples : fieldDefaults.xgb_min_samples));
  setFieldValue("mlp_training_days", config.mlp_training_days || (config.ml_model === "mlp" ? config.ml_training_days : fieldDefaults.mlp_training_days));
  setFieldValue("mlp_training_mode", trainingPlan);
  setFieldValue("mlp_target", config.mlp_target || (config.ml_model === "mlp" ? config.ml_target : fieldDefaults.mlp_target));
  setFieldValue("mlp_buy_threshold", "mlp_buy_threshold" in config ? config.mlp_buy_threshold : (config.ml_model === "mlp" ? config.ml_buy_threshold : fieldDefaults.mlp_buy_threshold));
  setFieldValue("mlp_sell_threshold", "mlp_sell_threshold" in config ? config.mlp_sell_threshold : (config.ml_model === "mlp" ? config.ml_sell_threshold : fieldDefaults.mlp_sell_threshold));
  setFieldValue("mlp_min_samples", config.mlp_min_samples || (config.ml_model === "mlp" ? config.ml_min_samples : fieldDefaults.mlp_min_samples));
  setFieldValue("cnn_training_days", config.cnn_training_days || (config.ml_model === "cnn" ? config.ml_training_days : fieldDefaults.cnn_training_days));
  setFieldValue("cnn_training_mode", trainingPlan);
  setFieldValue("cnn_target", config.cnn_target || (config.ml_model === "cnn" ? config.ml_target : fieldDefaults.cnn_target));
  setFieldValue("cnn_buy_threshold", "cnn_buy_threshold" in config ? config.cnn_buy_threshold : (config.ml_model === "cnn" ? config.ml_buy_threshold : fieldDefaults.cnn_buy_threshold));
  setFieldValue("cnn_sell_threshold", "cnn_sell_threshold" in config ? config.cnn_sell_threshold : (config.ml_model === "cnn" ? config.ml_sell_threshold : fieldDefaults.cnn_sell_threshold));
  setFieldValue("cnn_min_samples", config.cnn_min_samples || (config.ml_model === "cnn" ? config.ml_min_samples : fieldDefaults.cnn_min_samples));
  for (const id of [...mlCommonFields, ...rfFields, ...hgbFields, ...xgbFields, ...mlpFields, ...cnnFields]) {
    if (id in config) {
      setFieldValue(id, config[id]);
    }
  }
  syncTrainingPlanFields();
  syncActiveMlFields();
  syncMlControls();
  syncManualTradeDefaults(config);
  state.settingsDirty = false;
}

function setGroupDisabled(groupId, fieldIds, disabled) {
  const group = document.getElementById(groupId);
  if (group) {
    group.classList.toggle("disabled-group", disabled);
  }
  for (const id of fieldIds) {
    const el = document.getElementById(id);
    if (el) {
      el.disabled = disabled;
    }
  }
}

function syncMlControls() {
  const engine = fieldValue("run_engine");
  const model = fieldValue("ml_model");
  syncTrainingPlanFields();
  syncActiveMlFields();
  syncActiveStrategyField();
  const strategy = activeMlStrategyProfile();
  const localActive = engine === "local";
  const mlActive = engine === "ml";
  const aiActive = engine === "ai";
  const customActive = strategy === "custom";
  const rfActive = mlActive && model === "random_forest";
  const hgbActive = mlActive && model === "hist_gradient_boosting";
  const xgbActive = mlActive && model === "xgboost";
  const mlpActive = mlActive && model === "mlp";
  const cnnActive = mlActive && model === "cnn";
  setGroupDisabled("localControls", localFields, !localActive);
  setGroupDisabled("aiControls", aiFields, !aiActive);
  setGroupDisabled("mlCommonControls", mlCommonFields, !mlActive);
  setGroupDisabled("rfControls", [], !rfActive);
  setGroupDisabled("hgbControls", [], !hgbActive);
  setGroupDisabled("xgbControls", [], !xgbActive);
  setGroupDisabled("mlpControls", [], !mlpActive);
  setGroupDisabled("cnnControls", [], !cnnActive);
  setGroupDisabled("rfControls", rfTuningFields, !rfActive || !customActive);
  setGroupDisabled("hgbControls", hgbTuningFields, !hgbActive || !customActive);
  setGroupDisabled("xgbControls", xgbTuningFields, !xgbActive || !customActive);
  setGroupDisabled("mlpControls", mlpTuningFields, !mlpActive || !customActive);
  setGroupDisabled("cnnControls", cnnTuningFields, !cnnActive || !customActive);
  const rfStrategySelect = document.getElementById("rf_strategy_profile");
  if (rfStrategySelect) rfStrategySelect.disabled = !rfActive;
  const rfTargetSelect = document.getElementById("rf_target");
  if (rfTargetSelect) rfTargetSelect.disabled = !rfActive;
  const hgbStrategySelect = document.getElementById("hgb_strategy_profile");
  if (hgbStrategySelect) hgbStrategySelect.disabled = !hgbActive;
  const hgbTargetSelect = document.getElementById("hgb_target");
  if (hgbTargetSelect) hgbTargetSelect.disabled = !hgbActive;
  const xgbStrategySelect = document.getElementById("xgb_strategy_profile");
  if (xgbStrategySelect) xgbStrategySelect.disabled = !xgbActive;
  const xgbTargetSelect = document.getElementById("xgb_target");
  if (xgbTargetSelect) xgbTargetSelect.disabled = !xgbActive;
  const mlpStrategySelect = document.getElementById("mlp_strategy_profile");
  if (mlpStrategySelect) mlpStrategySelect.disabled = !mlpActive;
  const mlpTargetSelect = document.getElementById("mlp_target");
  if (mlpTargetSelect) mlpTargetSelect.disabled = !mlpActive;
  const cnnStrategySelect = document.getElementById("cnn_strategy_profile");
  if (cnnStrategySelect) cnnStrategySelect.disabled = !cnnActive;
  const cnnTargetSelect = document.getElementById("cnn_target");
  if (cnnTargetSelect) cnnTargetSelect.disabled = !cnnActive;
  const modelSelect = document.getElementById("ml_model");
  if (modelSelect) {
    modelSelect.disabled = !mlActive;
  }
  const intervalSelect = document.getElementById("backtest_interval_minutes");
  const intervalHint = document.getElementById("backtestIntervalHint");
  const slowBacktest = fieldValue("time_granularity") === "slow";
  if (intervalSelect) {
    intervalSelect.disabled = !slowBacktest;
  }
  if (intervalHint) {
    intervalHint.textContent = slowBacktest
      ? "慢速回测会按这个粒度拆分时间点；越小越细，但越慢。"
      : "快速回测固定按日线测试，这个粒度不会生效。";
  }
  const hint = document.getElementById("mlParamsHint");
  if (hint) {
    if (!mlActive) {
      hint.textContent = "当前运行模式不是机器学习，这些参数不会生效。";
    } else if (!customActive) {
      hint.textContent = `${mlModelText(model)} 的保守、均衡、激进会固定模型超参数；预测目标仍可调，训练方式和训练窗口在“训练”页统一调整。`;
    } else if (model === "random_forest") {
      hint.textContent = "当前仅 RandomForest 调节框生效，其他模型调节框已灰掉。";
    } else if (model === "hist_gradient_boosting") {
      hint.textContent = "当前仅 HistGradientBoosting 调节框生效，RandomForest 调节框已灰掉。";
    } else if (model === "mlp") {
      hint.textContent = "当前仅 MLP 调节框生效，树模型调节框已灰掉。";
    } else if (model === "cnn") {
      hint.textContent = "当前仅 1D-CNN 调节框生效，其他模型调节框已灰掉。";
    } else {
      hint.textContent = "当前仅 XGBoost 调节框生效，其他模型调节框已灰掉。";
    }
  }
  syncSettingsTabs();
}

function applyProfileMlDefaults() {
  const model = fieldValue("ml_model");
  const prefix = activeMlPrefix();
  const strategy = fieldValue(`${prefix}_strategy_profile`) || fieldDefaults[`${prefix}_strategy_profile`];
  if (strategy !== "custom" && applyMlOverrideToFields(model, strategy)) {
    return;
  }
  const defaults = profileMlDefaults[strategy];
  if (defaults && model === "random_forest") {
    for (const [id, value] of Object.entries(defaults)) {
      const targetId = id.replace("ml_", "rf_");
      setFieldValue(targetId, value);
    }
    syncActiveMlFields();
    return;
  }
  const xgbDefaults = xgbProfileDefaults[strategy];
  if (xgbDefaults && model === "xgboost") {
    for (const [id, value] of Object.entries(xgbDefaults)) {
      const targetId = id.replace("ml_", "xgb_");
      setFieldValue(targetId, value);
    }
    syncActiveMlFields();
    return;
  }
  const mlpDefaults = mlpProfileDefaults[strategy];
  if (mlpDefaults && model === "mlp") {
    for (const [id, value] of Object.entries(mlpDefaults)) {
      const targetId = id.replace("ml_", "mlp_");
      setFieldValue(targetId, value);
    }
    syncActiveMlFields();
    return;
  }
  const cnnDefaults = cnnProfileDefaults[strategy];
  if (cnnDefaults && model === "cnn") {
    for (const [id, value] of Object.entries(cnnDefaults)) {
      const targetId = id.replace("ml_", "cnn_");
      setFieldValue(targetId, value);
    }
    syncActiveMlFields();
    return;
  }
  const hgbDefaults = hgbProfileDefaults[strategy];
  if (hgbDefaults && model === "hist_gradient_boosting") {
    for (const [id, value] of Object.entries(hgbDefaults)) {
      const targetId = id.replace("ml_", "hgb_");
      setFieldValue(targetId, value);
    }
    syncActiveMlFields();
    return;
  }
  if (strategy !== "custom" && model === "random_forest") {
    for (const id of ["rf_buy_threshold", "rf_sell_threshold", "rf_min_samples", "rf_n_estimators", "rf_max_depth", "rf_min_samples_leaf"]) {
      setFieldValue(id, fieldDefaults[id]);
    }
    setFieldValue("rf_max_features_sqrt", fieldDefaults.rf_max_features_sqrt);
  }
  syncActiveMlFields();
}
function render(data, options = {}) {
  document.getElementById("equity").textContent = money(data.account.equity);
  document.getElementById("cash").textContent = money(data.account.cash);
  document.getElementById("cycle").textContent = data.cycle;
  document.getElementById("source").textContent = data.config.market_data;
  state.autoRunning = Boolean(data.auto && data.auto.running);
  document.getElementById("autoState").textContent = state.autoRunning
    ? `${data.auto.interval_seconds}秒`
    : "关闭";
  document.getElementById("autoBtnText").textContent = state.autoRunning ? "停止自动" : "开始自动";
  if (data.auto && data.auto.interval_seconds) {
    document.getElementById("auto_interval").value = data.auto.interval_seconds;
  }
  fillConfig(data.config, { force: Boolean(options.forceConfig) });
  renderSignals(data.signals || []);
  renderCharts(data.histories || {}, data.snapshots || [], data.orders || []);
  renderBacktest(data.backtest);
  restoreAutoLearnState(data.auto_learn);
  renderPositions(data.positions || []);
  renderOrders(data.orders || []);
  renderSnapshots(data.snapshots || []);
  if (!state.settingsDirty || options.forceConfig) {
    renderYearRegimeLabels(data.year_regime_labels || [], data.config.year_regime_overrides || {});
  }
  if (data.errors && data.errors.length) {
    document.getElementById("status").textContent = data.errors[0].slice(0, 60);
  }
  syncMlControls();
}

function restoreAutoLearnState(autoLearn) {
  if (!autoLearn) return;
  const active = autoLearn.active || null;
  const latest = autoLearn.latest || null;
  const status = document.getElementById("autoLearnStatus");
  const resultEl = document.getElementById("autoLearnResult");
  const startButton = document.getElementById("autoLearnBtn");
  const stopButton = document.getElementById("stopAutoLearnBtn");

  if (active && ["queued", "running"].includes(active.status)) {
    const taskId = active.task_id;
    const progress = Number(active.progress || 0);
    const message = `${active.message || "自动学习运行中"} ${progress}%`;
    if (status) status.textContent = message;
    if (resultEl && !state.autoLearnResult) {
      resultEl.className = "auto-learn-result empty";
      resultEl.textContent = message;
    }
    if (startButton) startButton.disabled = true;
    if (stopButton) stopButton.disabled = false;
    if (taskId && state.autoLearnTaskId !== taskId) {
      state.autoLearnTaskId = taskId;
      pollAutoLearnTask(taskId);
    }
    return;
  }

  if (latest && latest.result && !state.autoLearnTaskId) {
    const restored = latest.result;
    restored.task_id = restored.task_id || latest.task_id;
    if (!state.autoLearnResult || state.autoLearnResult.task_id !== restored.task_id) {
      renderAutoLearnResult(restored);
    }
    if (status) {
      const label = latest.status === "stopped" ? "最近一次自动学习已停止，结果已恢复" : "最近一次自动学习结果已恢复";
      status.textContent = label;
    }
    if (startButton) startButton.disabled = false;
    if (stopButton) stopButton.disabled = false;
  } else if (latest && latest.status === "error" && !state.autoLearnTaskId) {
    if (status) status.textContent = latest.error || "最近一次自动学习失败";
    if (resultEl && !state.autoLearnResult) {
      resultEl.className = "auto-learn-result empty";
      resultEl.textContent = latest.error || "最近一次自动学习失败";
    }
  }
}

function syncManualTradeDefaults(config) {
  const symbols = String(config.symbols || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const manualSymbol = document.getElementById("manual_symbol");
  const current = manualSymbol.value;
  manualSymbol.innerHTML = symbols
    .map((symbol) => {
      const key = String(symbol).toUpperCase();
      const name = stockName(key);
      const text = name === key ? key : `${name} (${key})`;
      return `<option value="${escapeHtml(key)}">${escapeHtml(text)}</option>`;
    })
    .join("");
  if (!manualSymbol.value && symbols.length) {
    manualSymbol.value = String(symbols[0]).toUpperCase();
  } else if (current && symbols.map((item) => item.toUpperCase()).includes(current)) {
    manualSymbol.value = current;
  }
  const manualCash = document.getElementById("manual_cash");
  if (!manualCash.value) {
    manualCash.value = config.max_trade_cash;
  }
}

function renderBacktest(backtest) {
  const el = document.getElementById("backtest");
  if (!backtest) {
    el.className = "backtest empty";
    el.textContent = "点击开始回测";
    return;
  }
  el.className = "backtest";
  const daily = backtest.daily || [];
  const equities = daily.map((item) => Number(item.equity)).filter((value) => Number.isFinite(value));
  const cls = backtest.return_pct >= 0 ? "positive" : "negative";
  const verdict = backtestVerdict(backtest);
  const modeLabel = backtestModeLabel(backtest);
  const comparison = buyHoldComparison(backtest);
  el.innerHTML = `
    <div class="backtest-summary">
      <div><span>回测天数</span><strong>${backtest.actual_days}</strong></div>
      <div><span>回测点数</span><strong>${backtest.actual_steps || backtest.actual_days}</strong></div>
      <div><span>起始资产</span><strong>${money(backtest.start_equity)}</strong></div>
      <div><span>结束资产</span><strong>${money(backtest.end_equity)}</strong></div>
      <div><span>收益率</span><strong class="${cls}">${pct(backtest.return_pct)}</strong></div>
      <div><span>交易次数</span><strong>${backtest.trade_count}</strong></div>
      <div><span>结论</span><strong class="${verdict.className}">${verdict.text}</strong></div>
      <div><span>策略档位</span><strong>${backtest.strategy_label || strategyText(backtest.strategy)}</strong></div>
      <div><span>运行模式</span><strong>${backtestEngine(backtest.engine)}</strong></div>
      <div><span>机器模型</span><strong>${backtest.ml_model_label || mlModelText(backtest.ml_model)}</strong></div>
      <div><span>训练方式</span><strong>${trainingModeText(backtest.ml_training_mode || (backtest.ml_params || {}).ml_training_mode)}</strong></div>
      <div><span>训练窗口</span><strong>${trainingWindowText(backtest.ml_training_days)}</strong></div>
      <div><span>回测模式</span><strong>${modeLabel}</strong></div>
      <div><span>时间粒度</span><strong>${backtest.time_granularity_label || "快速 日线/天"}</strong></div>
      <div><span>兜底次数</span><strong>${backtest.fallback_count || 0}</strong></div>
      <div><span>测试区间</span><strong>${fullDateLabel(backtest.window_start)} 至 ${fullDateLabel(backtest.window_end)}</strong></div>
    </div>
    ${backtestEvaluation(backtest, comparison)}
    ${backtestWarnings(backtest.errors || [])}
    ${equityCurve(daily)}
    <div class="backtest-section">
      <h3>回测标的走势</h3>
      ${backtestSymbolCurves(backtest.symbol_curves || {}, backtest.marker_orders || backtest.orders || [])}
    </div>
    <div class="backtest-section">
      <h3>最终持仓</h3>
      ${backtestPositions(backtest.positions || [])}
    </div>
    <div class="backtest-section">
      <h3>最近交易</h3>
      ${backtestOrders(backtest.orders || [])}
    </div>
  `;
}

function buyHoldComparison(backtest) {
  const symbolCurves = backtest.symbol_curves || {};
  const rows = Object.entries(symbolCurves)
    .map(([symbol, bars]) => {
      const validBars = (bars || []).filter((bar) => Number(bar.close) > 0);
      if (validBars.length < 2) return null;
      const start = Number(validBars[0].close);
      const end = Number(validBars[validBars.length - 1].close);
      return {
        symbol,
        start,
        end,
        returnPct: start > 0 ? end / start - 1 : 0,
      };
    })
    .filter(Boolean);
  if (!rows.length) {
    return null;
  }
  const averageReturn = rows.reduce((sum, row) => sum + row.returnPct, 0) / rows.length;
  const sorted = [...rows].sort((a, b) => b.returnPct - a.returnPct);
  const strategyReturn = Number(backtest.return_pct || 0);
  return {
    rows,
    averageReturn,
    best: sorted[0],
    worst: sorted[sorted.length - 1],
    excessReturn: strategyReturn - averageReturn,
    beatCount: rows.filter((row) => strategyReturn > row.returnPct).length,
  };
}

function backtestEvaluation(backtest, comparison) {
  const strategyReturn = Number(backtest.return_pct || 0);
  const trades = Number(backtest.trade_count || 0);
  const fallback = Number(backtest.fallback_count || 0);
  if (!comparison) {
    return `<div class="evaluation-card"><h3>测试结果评价</h3><p>暂无足够标的走势，无法和买入持有对比。</p></div>`;
  }
  const beat = comparison.excessReturn >= 0;
  const beatClass = beat ? "positive" : "negative";
  const summary = backtestEvaluationText(backtest, comparison);
  return `
    <div class="evaluation-card">
      <div class="evaluation-head">
        <h3>测试结果评价</h3>
        <strong class="${beatClass}">${beat ? "跑赢持有" : "跑输持有"}</strong>
      </div>
      <p>${summary}</p>
      <div class="comparison-grid">
        <div><span>策略收益</span><strong class="${strategyReturn >= 0 ? "positive" : "negative"}">${pct(strategyReturn)}</strong></div>
        <div><span>平均持有收益</span><strong class="${comparison.averageReturn >= 0 ? "positive" : "negative"}">${pct(comparison.averageReturn)}</strong></div>
        <div><span>超额收益</span><strong class="${beatClass}">${pct(comparison.excessReturn)}</strong></div>
        <div><span>跑赢数量</span><strong>${comparison.beatCount}/${comparison.rows.length}</strong></div>
        <div><span>最好持有</span><strong>${stockName(comparison.best.symbol)} ${pct(comparison.best.returnPct)}</strong></div>
        <div><span>最差持有</span><strong>${stockName(comparison.worst.symbol)} ${pct(comparison.worst.returnPct)}</strong></div>
        <div><span>交易次数</span><strong>${trades}</strong></div>
        <div><span>兜底次数</span><strong>${fallback}</strong></div>
      </div>
      ${buyHoldTable(comparison.rows, strategyReturn)}
    </div>
  `;
}

function backtestEvaluationText(backtest, comparison) {
  const strategyReturn = Number(backtest.return_pct || 0);
  const excess = comparison.excessReturn;
  const trades = Number(backtest.trade_count || 0);
  const fallback = Number(backtest.fallback_count || 0);
  if (excess >= 0.02) {
    return `这次策略明显跑赢平均买入持有，说明择时或换仓对结果有帮助。继续看交易点是否集中在上涨前、下跌前是否能退出。`;
  }
  if (excess >= 0) {
    return `这次策略小幅跑赢平均买入持有，优势存在但不算大，可以继续对比其它年份，确认不是单一年份偶然。`;
  }
  if (strategyReturn > 0 && comparison.averageReturn > strategyReturn) {
    return `策略赚钱了，但不如直接平均买入持有，说明这段行情偏适合买完不动，频繁判断反而可能错过上涨。`;
  }
  if (strategyReturn < 0 && comparison.averageReturn < 0 && strategyReturn > comparison.averageReturn) {
    return `策略也是亏损，但亏得比平均持有少，说明防守有一定效果，适合继续观察弱市年份。`;
  }
  if (trades > 20) {
    return `策略跑输且交易偏多，可能存在过度交易，需要提高买入阈值、减少买入信号或加强卖出过滤。`;
  }
  if (fallback > 0) {
    return `策略跑输持有，且出现兜底，可能是训练样本不足或场景过滤太细，建议尝试关闭波动桶匹配或扩大训练池。`;
  }
  return `策略跑输平均买入持有，这段行情下买卖判断没有带来优势，建议换年份继续测试再决定是否调参。`;
}

function buyHoldTable(rows, strategyReturn) {
  const sorted = [...rows].sort((a, b) => b.returnPct - a.returnPct);
  return `
    <details class="comparison-detail">
      <summary>查看每只标的持有对比</summary>
      <div class="table-wrap">
        <table>
          <thead><tr><th>标的</th><th>持有收益</th><th>策略是否跑赢</th></tr></thead>
          <tbody>
            ${sorted
              .map((row) => {
                const beat = strategyReturn > row.returnPct;
                return `<tr><td>${stockLabel(row.symbol)}</td><td class="${row.returnPct >= 0 ? "positive" : "negative"}">${pct(row.returnPct)}</td><td>${beat ? "是" : "否"}</td></tr>`;
              })
              .join("")}
          </tbody>
        </table>
      </div>
    </details>
  `;
}

function backtestWarnings(errors) {
  if (!errors.length) return "";
  return `<div class="reason">${errors.map((item) => escapeHtml(String(item))).join("<br>")}</div>`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return map[char] || char;
  });
}

function backtestEngine(engine) {
  if (!engine) return "--";
  if (engine === "codex-login") return "Codex";
  if (engine.startsWith("codex-login-guarded")) return "AI";
  if (engine.startsWith("openai:")) return "OpenAI";
  if (engine.startsWith("random-forest-")) return "机器学习";
  if (engine.startsWith("hist-gradient-boosting-")) return "机器学习";
  if (engine.startsWith("xgboost-")) return "机器学习";
  if (engine.startsWith("mlp-")) return "机器学习";
  if (engine.startsWith("cnn1d-")) return "机器学习";
  if (engine.startsWith("heuristic-")) return "本地";
  return "本地规则";
}

function backtestModeLabel(backtest) {
  if (backtest.mode === "natural_year") return `${backtest.backtest_year || ""}年`.trim();
  if (backtest.mode === "fixed_year") return "固定过去一年";
  return "随机14天";
}

function strategyText(strategy) {
  if (strategy === "custom") return "自定义";
  if (strategy === "aggressive") return "激进";
  if (strategy === "balanced") return "均衡";
  return "保守";
}

function mlModelText(model) {
  if (model === "cnn") return "1D-CNN";
  if (model === "mlp") return "MLP";
  if (model === "xgboost") return "XGBoost";
  if (model === "hist_gradient_boosting") return "HistGradientBoosting";
  return "RandomForest";
}

function trainingWindowText(days) {
  const value = Number(days || fieldDefaults.ml_training_days);
  if (value >= 3600) return "最大 10 年";
  if (value >= 1800) return "5 年";
  if (value >= 1000) return "3 年";
  if (value >= 360) return "1 年";
  return `${value} 天`;
}

function trainingModeText(mode) {
  const normalized = normalizeTrainingPlan(mode);
  if (normalized === "self") return "自己训练自己 -> 分类 -> 混合";
  if (normalized === "classified") return "按行情分类训练 -> 混合";
  return "全部混合训练";
}

function renderStrategyDocs() {
  const el = document.getElementById("strategyDocs");
  if (!el) return;
  el.innerHTML = strategyProfiles.map(strategyDocCard).join("");
}

function strategyDocCard(profile) {
  return `
    <details class="strategy-card">
      <summary>
        <span>${profile.label}</span>
        <small>最多买入信号 ${profile.maxBuysPerDay} 个</small>
      </summary>
      <div class="strategy-rule-grid">
        <section>
          <h3>本地规则</h3>
          <ul>
            <li>买入分数至少 ${profile.buyScore}；卖出分数至少 ${profile.sellScore}。</li>
            <li>趋势 = 5日均线 / 20日均线 - 1，买入趋势门槛 ${pctShort(profile.trendMin)}。</li>
            <li>5日动量最小 ${pctShort(profile.momentum5Min)}，强动量 ${pctShort(profile.momentum5Strong)}。</li>
            <li>RSI 买入区间 ${profile.rsiMin}-${profile.rsiMax}；高于 ${profile.overboughtRsi} 会扣分。</li>
            <li>20日波动率必须低于 ${pctShort(profile.maxVolatility)}。</li>
            <li>强反转要求：5日动量大于 ${pctShort(profile.reversalMomentum5)}，当日涨幅大于 ${pctShort(profile.reversalChange)}，20日动量为正。</li>
            <li>每天最多保留 ${profile.maxBuysPerDay} 个买入信号；${profile.allowAddPosition ? "允许已有持仓继续加仓" : "已有持仓不重复加仓"}。</li>
            <li>可调方向：买入分、趋势门槛、动量门槛、RSI 上限、最大波动率越严格，交易越少；越宽松，交易越多但更容易追高。</li>
            <li>风控仍会二次限制：单笔金额、单笔股数、单标的仓位、止损止盈会决定最终是否真的下单。</li>
          </ul>
        </section>
        <section>
          <h3>RandomForest</h3>
          <ul>
            <li>用途：用很多棵决策树投票，判断下一根K线涨幅是否可能超过 0.10%。</li>
            <li>默认参数：保守/均衡 n_estimators=80、min_samples_leaf=5；激进采用你调试的 n_estimators=300、min_samples_leaf=20。</li>
            <li>当前参数：max_depth=5。深度越大越会学习复杂形态，但更容易把历史噪声当规律；越小越保守。</li>
            <li>当前参数：class_weight=balanced_subsample。涨跌样本不均衡时自动平衡，避免模型只学会一直看多或一直看空。</li>
            <li>当前参数：max_features=sqrt 默认开启。每次分裂只随机看一部分特征，通常更抗过拟合；关闭后每次可看全部特征。</li>
            <li>当前参数：random_state=42。固定随机种子，让同一批数据的结果更容易复现。</li>
            <li>买入阈值：${pctShort(mlThresholds(profile).buy)}；卖出阈值：${pctShort(mlThresholds(profile).sell)}。</li>
            <li>调参影响：提高买入阈值会减少买入；降低卖出阈值会更早卖出；加大 max_depth 往往提高回测拟合但也提高过拟合风险。</li>
          </ul>
        </section>
        <section>
          <h3>HistGradientBoosting</h3>
          <ul>
            <li>用途：一棵树接一棵树修正前面的错误，比 RandomForest 更强调“逐步改错”。</li>
            <li>当前参数：max_iter=140。迭代轮数越多模型越强，但更慢，也更容易过拟合。</li>
            <li>当前参数：learning_rate=0.03。学习率越小越稳但需要更多轮；越大收敛快但可能学过头。</li>
            <li>当前参数：max_leaf_nodes=15。叶子越多能表达更复杂关系，但也更容易记住历史。</li>
            <li>当前参数：min_samples_leaf=12。越大越保守、抗噪声更好；越小越敏感。</li>
            <li>当前参数：l2_regularization=0.08。正则越大越抑制复杂模型，通常更稳但可能错过机会。</li>
            <li>当前参数：early_stopping=False。小样本回测先不随机切验证集，避免过早停止导致模型太保守。</li>
            <li>激进默认：买入0.52、卖出0.18、max_iter=120、learning_rate=0.035、sample_weight=true，用来提升牛市快速回测表现。</li>
            <li>保守/均衡默认：不使用样本权重，避免在普涨行情中过度纠正样本比例，导致卖出信号偏多。</li>
            <li>调参影响：更高 max_iter、更大 max_leaf_nodes、更小 min_samples_leaf 会让模型更激进；更高 l2、更低学习率会更稳。</li>
          </ul>
        </section>
        <section>
          <h3>XGBoost</h3>
          <ul>
            <li>用途：梯度提升树模型，和 HGB 一样逐步修正错误，但可调项更多，适合做进阶对比。</li>
            <li>默认参数：保守 n_estimators=100、max_depth=2；均衡 n_estimators=120、max_depth=3；激进 n_estimators=120、max_depth=3。</li>
            <li>当前参数：learning_rate 控制每棵树学习幅度，越小越稳但需要更多树；越大越激进。</li>
            <li>当前参数：subsample 和 colsample_bytree 分别控制样本采样和特征采样，降低通常能减少过拟合。</li>
            <li>当前参数：min_child_weight 越高越保守，越低越容易抓短期细节。</li>
            <li>当前参数：reg_lambda 是 L2 正则，越高越抑制复杂模型。</li>
            <li>默认使用 tree_method=hist 和 n_jobs=1，尽量让本地一年回测更稳，不把电脑资源占满。</li>
            <li>阈值：保守 0.62/0.22，均衡 0.57/0.22，激进 0.50/0.18；前者是买入阈值，后者是卖出阈值。</li>
          </ul>
        </section>
        <section>
          <h3>MLP 神经网络</h3>
          <ul>
            <li>用途：用多层神经网络学习技术指标和未来上涨概率的非线性关系，适合观察扩大训练数据后的变化。</li>
            <li>自动标准化：MLP 对数值尺度敏感，后端会先用 StandardScaler 标准化特征，再训练神经网络。</li>
            <li>隐藏层大小：每层神经元数量，越大表达能力越强，但更慢、更容易过拟合。</li>
            <li>隐藏层数：层数越多越像深度学习，但当前特征较少，通常 1-2 层更稳。</li>
            <li>训练轮数：越高训练更充分，但回测会更慢；如果太高可能只是在记历史。</li>
            <li>学习率：越高调整越快但容易震荡；越低更稳但可能学不动。</li>
            <li>L2 正则：越高越保守，越低越激进；股票噪声大时不建议设太低。</li>
            <li>阈值：保守 0.62/0.30，均衡 0.58/0.28，激进 0.54/0.24；前者是买入阈值，后者是卖出阈值。</li>
          </ul>
        </section>
        <section>
          <h3>共同训练规则</h3>
          <ul>
            <li>训练方式：1 自己训练自己，不够回退分类；2 按牛市/熊市/震荡分类训练，不够回退混合；3 全部混合训练。</li>
            <li>预测目标：二分类涨跌会预测下一根K线是否上涨超过 0.10%；五分类走势概率会预测大涨、小涨、横盘、小跌、大跌。</li>
            <li>五分类决策：大涨权重 +2，小涨 +1，横盘 0，小跌 -1，大跌 -2，再归一化成加权分，用买入/卖出阈值判断动作。</li>
            <li>档位规则：保守、均衡、激进使用固定默认参数并锁定调参区；自定义档位才允许手动修改。</li>
            <li>输入特征：当日涨跌、5日/20日均线相对位置、5日/20日动量、RSI、20日波动率、成交量比。</li>
            <li>模型输出的是概率和加权分，不是保证会涨；概率只是让不同模型用同一套规则比较。</li>
            <li>回测规则：只使用回测开始前已经存在的数据训练，避免偷看未来。</li>
            <li>样本不足、涨跌类别单一或训练失败时，会自动 fallback 到本地规则。</li>
            <li>设置页切换模型后，会同时影响仪表盘、自动运行和回测。</li>
            <li>模型达到买卖阈值时，信号置信度至少给到 0.61，确保能通过风控的最低下单置信度。</li>
            <li>HGB 当前阈值：保守 0.63/0.20，均衡 0.60/0.22，激进优化为 0.52/0.18，前者是买入阈值，后者是卖出阈值。</li>
          </ul>
        </section>
        <section>
          <h3>AI 提示词</h3>
          <pre>${escapeHtml(aiPromptPreview(profile))}</pre>
        </section>
      </div>
    </details>
  `;
}

function mlThresholds(profile) {
  if (profile.key === "custom") return { buy: 0.57, sell: 0.42 };
  if (profile.key === "aggressive") return { buy: 0.4, sell: 0.2 };
  if (profile.key === "balanced") return { buy: 0.57, sell: 0.42 };
  return { buy: 0.61, sell: 0.44 };
}

function aiPromptPreview(profile) {
  return [
    `You are a ${profile.label} multi-factor signal generator for a paper-trading stock simulator.`,
    "This is not real-money trading. Use the selected style strictly.",
    "Use trend, momentum_5, momentum_20, rsi_14, volatility_20, and volume_ratio_20 together.",
    `Style=${profile.label}: max ${profile.maxBuysPerDay} buy signal(s) per day; buy only if RSI is roughly ${profile.rsiMin}-${profile.rsiMax}, volatility_20 below ${pctShort(profile.maxVolatility)}, and momentum is strong enough.`,
    "Prefer hold when evidence is weak. Avoid chasing when RSI is above the style limit or one-day moves look exhausted.",
    "Prefer sell when trend breaks down, momentum turns negative, or risk looks elevated.",
    "If several symbols look similar, choose the strongest one and hold the weaker ones.",
    "Do not add repeated buy signals just because yesterday was also positive.",
    'Return only valid JSON with this exact shape: {"signals":[{"symbol":"600519.SH","side":"buy|sell|hold","confidence":0.0,"reason":"short Chinese reason"}]}',
    "Use only the provided numeric market snapshots. Do not use tools. Do not include markdown.",
    "Market snapshots: ...",
  ].join("\n");
}

function backtestVerdict(backtest) {
  const returnPct = Number(backtest.return_pct || 0);
  const trades = Number(backtest.trade_count || 0);
  if (returnPct >= 0.01 && trades <= 12) return { text: "偏好", className: "positive" };
  if (returnPct <= -0.01) return { text: "偏弱", className: "negative" };
  if (trades > 20) return { text: "过频", className: "negative" };
  return { text: "持平", className: "" };
}

function equityCurve(daily) {
  if (!daily.length) return `<div class="reason">暂无资产曲线</div>`;
  const values = daily.map((item) => Number(item.equity));
  const high = Math.max(...values);
  const low = Math.min(...values);
  const width = 640;
  const height = 210;
  const left = 70;
  const right = 18;
  const top = 18;
  const bottom = 40;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const points = values
    .map((value, index) => {
      const x = left + (index / Math.max(1, values.length - 1)) * plotWidth;
      const y = top + (1 - (value - low) / Math.max(0.000001, high - low)) * plotHeight;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  const startDate = dateLabel(Number(daily[0].timestamp));
  const endDate = dateLabel(Number(daily[daily.length - 1].timestamp));
  return `
    <svg class="equity-curve" viewBox="0 0 ${width} ${height}" role="img" aria-label="回测资产曲线">
      <line x1="${left}" y1="${top}" x2="${width - right}" y2="${top}"></line>
      <line x1="${left}" y1="${top + plotHeight / 2}" x2="${width - right}" y2="${top + plotHeight / 2}"></line>
      <line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}"></line>
      <line class="axis" x1="${left}" y1="${top}" x2="${left}" y2="${height - bottom}"></line>
      <line class="axis" x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}"></line>
      <text class="axis-label y-label" x="${left - 8}" y="${top + 4}">${fmtNumber.format(high)}</text>
      <text class="axis-label y-label" x="${left - 8}" y="${height - bottom + 4}">${fmtNumber.format(low)}</text>
      <text class="axis-label x-label" x="${left}" y="${height - 14}" text-anchor="start">${startDate}</text>
      <text class="axis-label x-label" x="${width - right}" y="${height - 14}" text-anchor="end">${endDate}</text>
      <polyline points="${points}" class="${values[values.length - 1] >= values[0] ? "up-line" : "down-line"}"></polyline>
    </svg>
  `;
}

function backtestSymbolCurves(symbolCurves, orders = []) {
  const symbols = Object.keys(symbolCurves);
  if (!symbols.length) return `<div class="reason">暂无标的走势</div>`;
  return `
    <div class="charts backtest-charts">
      ${symbols
        .map((symbol) => chartCard(symbol, symbolCurves[symbol] || [], null, orders))
        .join("")}
    </div>
  `;
}

function backtestPositions(positions) {
  if (!positions.length) return `<div class="reason">暂无持仓</div>`;
  return `
    <div class="mini-list">
      ${positions
        .map(
          (item) => `
          <div>
            <strong>${stockLabel(item.symbol)}</strong>
            <span>${item.quantity} 股 / 成本 ${money(item.average_price)} / 市值 ${money(item.market_value)}</span>
          </div>
        `,
        )
        .join("")}
    </div>
  `;
}

function backtestOrders(orders) {
  if (!orders.length) return `<div class="reason">暂无交易</div>`;
  return `
    <div class="mini-list">
      ${orders
        .slice()
        .reverse()
        .slice(0, 8)
        .map(
          (item) => `
          <div>
            <strong>${fullDateLabel(item.date)} ${stockLabel(item.symbol)} ${sideText(item.side)} x ${item.quantity}</strong>
            <span>${money(item.price)} · ${item.reason}</span>
          </div>
        `,
        )
        .join("")}
    </div>
  `;
}

function renderCharts(histories, snapshots, orders = []) {
  const el = document.getElementById("charts");
  const symbols = Object.keys(histories);
  if (!symbols.length) {
    el.className = "charts empty";
    el.textContent = "点击运行一次";
    return;
  }
  const snapshotBySymbol = Object.fromEntries(snapshots.map((item) => [item.symbol, item]));
  el.className = "charts";
  el.innerHTML = symbols
    .map((symbol) => chartCard(symbol, histories[symbol], snapshotBySymbol[symbol], orders))
    .join("");
}

function chartCard(symbol, bars, snapshot, orders = []) {
  const validBars = bars
    .map((item) => ({
      close: Number(item.close),
      timestamp: Number(item.timestamp),
    }))
    .filter((item) => Number.isFinite(item.close));
  const closes = validBars.map((item) => item.close);
  if (closes.length < 2) {
    return `<div class="chart-card"><strong>${stockLabel(symbol)}</strong><div class="reason">历史数据不足</div></div>`;
  }
  const first = closes[0];
  const last = closes[closes.length - 1];
  const high = Math.max(...closes);
  const low = Math.min(...closes);
  const change = (last - first) / first;
  const width = 640;
  const height = 260;
  const left = 64;
  const right = 18;
  const top = 18;
  const bottom = 42;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const yValues = [high, (high + low) / 2, low];
  const xLabels = [
    { index: 0, label: dateLabel(validBars[0].timestamp), anchor: "start" },
    { index: Math.floor((validBars.length - 1) / 2), label: dateLabel(validBars[Math.floor((validBars.length - 1) / 2)].timestamp), anchor: "middle" },
    { index: validBars.length - 1, label: dateLabel(validBars[validBars.length - 1].timestamp), anchor: "end" },
  ];
  const points = closes
    .map((value, index) => {
      const x = left + (index / Math.max(1, closes.length - 1)) * plotWidth;
      const y = top + (1 - (value - low) / Math.max(0.000001, high - low)) * plotHeight;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  const tradeMarkers = tradeMarkersForSymbol(symbol, validBars, orders, left, top, plotWidth, plotHeight, low, high);
  const cls = change >= 0 ? "positive" : "negative";
  const maText = snapshot ? `5日 ${fmtNumber.format(snapshot.sma_5)} / 20日 ${fmtNumber.format(snapshot.sma_20)}` : "";
  return `
    <div class="chart-card">
      <div class="chart-title">
        <div>
          <strong>${stockLabel(symbol)}</strong>
          <span>${maText}</span>
        </div>
        <b class="${cls}">${pct(change)}</b>
      </div>
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${stockName(symbol)} 带坐标轴价格走势">
        ${yValues
          .map((value) => {
            const y = top + (1 - (value - low) / Math.max(0.000001, high - low)) * plotHeight;
            return `
              <line x1="${left}" y1="${y.toFixed(2)}" x2="${width - right}" y2="${y.toFixed(2)}"></line>
              <text class="axis-label y-label" x="${left - 8}" y="${(y + 4).toFixed(2)}">${fmtNumber.format(value)}</text>
            `;
          })
          .join("")}
        <line class="axis" x1="${left}" y1="${top}" x2="${left}" y2="${height - bottom}"></line>
        <line class="axis" x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}"></line>
        ${xLabels
          .map((item) => {
            const x = left + (item.index / Math.max(1, closes.length - 1)) * plotWidth;
            return `<text class="axis-label x-label" x="${x.toFixed(2)}" y="${height - 16}" text-anchor="${item.anchor}">${item.label}</text>`;
          })
          .join("")}
        <polyline points="${points}" class="${change >= 0 ? "up-line" : "down-line"}"></polyline>
        ${tradeMarkers}
      </svg>
      <div class="chart-meta">
        <span>低 ${fmtNumber.format(low)}</span>
        <span>现 ${fmtNumber.format(last)}</span>
        <span>高 ${fmtNumber.format(high)}</span>
        <span class="trade-legend"><i class="legend-dot buy-dot"></i>买入 <i class="legend-dot sell-dot"></i>卖出</span>
      </div>
    </div>
  `;
}

function tradeMarkersForSymbol(symbol, bars, orders, left, top, plotWidth, plotHeight, low, high) {
  if (!orders.length || !bars.length) return "";
  const key = String(symbol || "").trim().toUpperCase();
  const symbolOrders = orders.filter((order) => String(order.symbol || "").trim().toUpperCase() === key);
  if (!symbolOrders.length) return "";
  return symbolOrders
    .map((order, orderIndex) => {
      const timestamp = Number(order.date || order.timestamp || 0);
      const barIndex = timestamp ? nearestBarIndex(bars, timestamp) : bars.length - 1;
      const bar = bars[barIndex];
      if (!bar) return "";
      const x = left + (barIndex / Math.max(1, bars.length - 1)) * plotWidth;
      const y = top + (1 - (bar.close - low) / Math.max(0.000001, high - low)) * plotHeight;
      const side = String(order.side || "").toLowerCase();
      const cls = side === "sell" ? "trade-marker sell-marker" : "trade-marker buy-marker";
      const label = `${sideText(side)} ${stockName(symbol)} ${order.quantity || ""} 股 ${order.price || order.limit_price ? money(order.price || order.limit_price) : ""}`;
      const offset = ((orderIndex % 3) - 1) * 4;
      return `
        <circle class="${cls}" cx="${x.toFixed(2)}" cy="${(y + offset).toFixed(2)}" r="2.3">
          <title>${escapeHtml(label)}</title>
        </circle>
      `;
    })
    .join("");
}

function nearestBarIndex(bars, timestamp) {
  let bestIndex = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  bars.forEach((bar, index) => {
    const distance = Math.abs(Number(bar.timestamp || 0) - timestamp);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = index;
    }
  });
  return bestIndex;
}

function dateLabel(timestamp) {
  if (!timestamp) return "--";
  const date = new Date(timestamp * 1000);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${month}-${day}`;
}

function fullDateLabel(timestamp) {
  if (!timestamp) return "--";
  const date = new Date(timestamp * 1000);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function renderSignals(signals) {
  const el = document.getElementById("signals");
  if (!signals.length) {
    el.className = "signals empty";
    el.textContent = "点击运行一次";
    return;
  }
  el.className = "signals";
  el.innerHTML = signals
    .map(
      (item) => `
        <div class="signal">
          <span class="badge ${item.side}">${sideText(item.side)}</span>
          <div>
            <strong>${stockLabel(item.symbol)}</strong>
            <div class="reason">${item.reason}</div>
          </div>
          <strong>${Math.round(item.confidence * 100)}%</strong>
        </div>
      `,
    )
    .join("");
}

function renderPositions(positions) {
  const el = document.getElementById("positions");
  if (!positions.length) {
    el.innerHTML = `<tr><td colspan="5">暂无持仓</td></tr>`;
    return;
  }
  el.innerHTML = positions
    .map((item) => {
      const cls = item.pnl_pct >= 0 ? "positive" : "negative";
      return `
        <tr>
          <td>${stockLabel(item.symbol)}</td>
          <td>${item.quantity}</td>
          <td>${money(item.average_price)}</td>
          <td>${money(item.market_price)}</td>
          <td class="${cls}">${pct(item.pnl_pct)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderOrders(orders) {
  const el = document.getElementById("orders");
  if (!orders.length) {
    el.className = "orders empty";
    el.textContent = "暂无委托";
    return;
  }
  el.className = "orders";
  el.innerHTML = orders
    .map(
      (item) => `
        <div class="order">
          <span class="badge ${item.side}">${sideText(item.side)}</span>
          <div>
            <strong>${stockLabel(item.symbol)} x ${item.quantity}</strong>
            <div class="reason">${item.reason}</div>
          </div>
          <strong>${money(item.limit_price)}</strong>
        </div>
      `,
    )
    .join("");
}

function renderSnapshots(snapshots) {
  const el = document.getElementById("snapshots");
  if (!snapshots.length) {
    el.innerHTML = `<tr><td colspan="6">暂无行情</td></tr>`;
    return;
  }
  el.innerHTML = snapshots
    .map((item) => {
      const cls = item.change_pct >= 0 ? "positive" : "negative";
      return `
        <tr>
          <td>${stockLabel(item.symbol)}</td>
          <td>${money(item.last_price)}</td>
          <td class="${cls}">${pct(item.change_pct)}</td>
          <td>${fmtNumber.format(item.sma_5)}</td>
          <td>${fmtNumber.format(item.sma_20)}</td>
          <td>${fmtNumber.format(item.volume)}</td>
        </tr>
      `;
    })
    .join("");
}

document.getElementById("runBtn").addEventListener("click", async () => {
  try {
    setBusy(true);
    const data = await api("/api/run", {
      method: "POST",
      body: JSON.stringify(formPayload()),
    });
    state.settingsDirty = false;
    render(data, { forceConfig: true });
  } catch (error) {
    document.getElementById("status").textContent = error.message;
  } finally {
    setBusy(false);
  }
});

document.getElementById("resetBtn").addEventListener("click", async () => {
  try {
    setBusy(true);
    const data = await api("/api/reset", {
      method: "POST",
      body: JSON.stringify(formPayload()),
    });
    state.settingsDirty = false;
    render(data, { forceConfig: true });
  } catch (error) {
    document.getElementById("status").textContent = error.message;
  } finally {
    setBusy(false);
  }
});

document.getElementById("autoBtn").addEventListener("click", async () => {
  try {
    setBusy(true);
    if (state.autoRunning) {
      render(await api("/api/auto/stop", { method: "POST", body: "{}" }));
    } else {
      const interval = Number(fieldValue("auto_interval") || 1800);
      const payload = { ...formPayload(), interval_seconds: interval };
      const data = await api("/api/auto/start", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.settingsDirty = false;
      render(data, { forceConfig: true });
    }
  } catch (error) {
    document.getElementById("status").textContent = error.message;
  } finally {
    setBusy(false);
  }
});

document.getElementById("saveSettingsBtn").addEventListener("click", async () => {
  try {
    setBusy(true);
    const data = await api("/api/settings/save", {
      method: "POST",
      body: JSON.stringify(formPayload()),
    });
    state.settingsDirty = false;
    render(data, { forceConfig: true });
    document.getElementById("status").textContent = "设置已保存";
  } catch (error) {
    document.getElementById("status").textContent = error.message;
  } finally {
    setBusy(false);
  }
});

document.getElementById("resetSettingsBtn").addEventListener("click", async () => {
  try {
    setBusy(true);
    const data = await api("/api/settings/reset", {
      method: "POST",
      body: "{}",
    });
    state.settingsDirty = false;
    render(data, { forceConfig: true });
    document.getElementById("status").textContent = "设置已恢复默认";
  } catch (error) {
    document.getElementById("status").textContent = error.message;
  } finally {
    setBusy(false);
  }
});

const yearRegimeLabelsEl = document.getElementById("yearRegimeLabels");
if (yearRegimeLabelsEl) {
  yearRegimeLabelsEl.addEventListener("change", (event) => {
    if (!event.target.matches("[data-year-regime-key]")) return;
    updateYearRegimeHiddenFromControls();
    state.settingsDirty = true;
    document.getElementById("status").textContent = "自然年标签已修改，记得保存设置";
  });
}

const refreshYearRegimesBtn = document.getElementById("refreshYearRegimesBtn");
if (refreshYearRegimesBtn) {
  refreshYearRegimesBtn.addEventListener("click", async () => {
    try {
      setBusy(true);
      document.getElementById("status").textContent = "正在刷新自然年标签";
      refreshYearRegimesBtn.textContent = "正在刷新...";
      const labels = document.getElementById("yearRegimeLabels");
      if (labels) {
        labels.className = "year-regime-list empty";
        labels.textContent = "正在拉取历史行情并计算标签，请稍等...";
      }
      const data = await api("/api/training/regimes/refresh", {
        method: "POST",
        body: JSON.stringify(formPayload()),
      });
      state.settingsDirty = false;
      render(data, { forceConfig: true });
      const details = document.getElementById("yearRegimeDetails");
      if (details) details.open = true;
      document.getElementById("status").textContent = "自然年标签已刷新";
    } catch (error) {
      document.getElementById("status").textContent = error.message;
    } finally {
      refreshYearRegimesBtn.textContent = "刷新标签";
      setBusy(false);
    }
  });
}

const autoLearnBtn = document.getElementById("autoLearnBtn");
if (autoLearnBtn) {
  autoLearnBtn.addEventListener("click", async () => {
    try {
      setBusy(true);
      const status = document.getElementById("autoLearnStatus");
      const resultEl = document.getElementById("autoLearnResult");
      if (status) status.textContent = "自动学习任务启动中...";
      if (resultEl) {
        resultEl.className = "auto-learn-result empty";
        resultEl.textContent = "正在提交后台任务...";
      }
      const data = await api("/api/auto-learn/start", {
        method: "POST",
        body: JSON.stringify({
          ...formPayload(),
          auto_learn_model: fieldValue("auto_learn_model") || "current",
          auto_learn_budget: fieldValue("auto_learn_budget") || "balanced",
          auto_learn_continuous: fieldValue("auto_learn_continuous") || "false",
          auto_learn_patience: fieldValue("auto_learn_patience") || "6",
          auto_learn_min_improvement: fieldValue("auto_learn_min_improvement") || "0.002",
          auto_learn_specialized_weight: fieldValue("auto_learn_specialized_weight") || "50",
          auto_learn_fallback_weight: fieldValue("auto_learn_fallback_weight") || "25",
          auto_learn_composite_weight: fieldValue("auto_learn_composite_weight") || "25",
        }),
      });
      state.autoLearnTaskId = data.task_id;
      document.getElementById("status").textContent = data.message || "自动学习后台任务已启动";
      if (status) status.textContent = data.message || "后台运行中";
      autoLearnBtn.disabled = true;
      pollAutoLearnTask(data.task_id);
    } catch (error) {
      document.getElementById("status").textContent = error.message;
      const status = document.getElementById("autoLearnStatus");
      if (status) status.textContent = error.message;
    } finally {
      setBusy(false);
      if (state.autoLearnTaskId) {
        autoLearnBtn.disabled = true;
      }
    }
  });
}

const autoLearnResultEl = document.getElementById("autoLearnResult");
if (autoLearnResultEl) {
  autoLearnResultEl.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-auto-learn-apply]");
    if (!button) return;
    const result = state.autoLearnResult;
    if (!result || !result.task_id) {
      document.getElementById("status").textContent = "没有可应用的自动学习结果";
      return;
    }
    try {
      button.disabled = true;
      const profile = button.dataset.autoLearnApply;
      document.getElementById("status").textContent = "正在应用自动学习建议...";
      const data = await api("/api/auto-learn/apply", {
        method: "POST",
        body: JSON.stringify({
          task_id: result.task_id,
          model: result.model || fieldValue("ml_model"),
          profile,
        }),
      });
      state.settingsDirty = false;
      render(data, { forceConfig: true });
      setMlOverrides((data.config || {}).ml_profile_overrides || state.mlProfileOverrides);
      applyProfileMlDefaults();
      syncMlControls();
      const applied = data.applied_auto_learn || {};
      document.getElementById("status").textContent = `已应用并保存自动学习建议：${(applied.profiles || []).map(strategyText).join("、")}`;
    } catch (error) {
      document.getElementById("status").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}

const stopAutoLearnBtn = document.getElementById("stopAutoLearnBtn");
if (stopAutoLearnBtn) {
  stopAutoLearnBtn.addEventListener("click", async () => {
    try {
      const status = document.getElementById("autoLearnStatus");
      if (status) status.textContent = "正在停止自动学习...";
      document.getElementById("status").textContent = "正在停止自动学习";
      const data = await api("/api/auto-learn/stop", {
        method: "POST",
        body: JSON.stringify({ task_id: state.autoLearnTaskId || "" }),
      });
      if (status) status.textContent = data.message || "已发送停止信号";
    } catch (error) {
      document.getElementById("status").textContent = error.message;
      const status = document.getElementById("autoLearnStatus");
      if (status) status.textContent = error.message;
    }
  });
}

document.getElementById("backtestBtn").addEventListener("click", async () => {
  runBacktest();
});

document.getElementById("manualBuyBtn").addEventListener("click", async () => {
  try {
    setBusy(true);
    const symbol = document.getElementById("manual_symbol").value;
    const cash = Number(document.getElementById("manual_cash").value || 0);
    const shares = Number(document.getElementById("manual_shares").value || 0);
    document.getElementById("manualStatus").textContent = "正在按当前行情价买入...";
    const data = await api("/api/manual/buy", {
      method: "POST",
      body: JSON.stringify({ symbol, cash, shares }),
    });
    render(data);
    const latestOrder = (data.orders || [])[0];
    document.getElementById("manualStatus").textContent = latestOrder
      ? `已买入 ${stockName(latestOrder.symbol)} x ${latestOrder.quantity}，价格 ${money(latestOrder.limit_price)}`
      : "未产生买入委托";
  } catch (error) {
    document.getElementById("manualStatus").textContent = error.message;
    document.getElementById("status").textContent = error.message;
  } finally {
    setBusy(false);
  }
});

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.target));
});

for (const id of [...fields, "auto_interval"]) {
  const el = document.getElementById(id);
  if (!el) continue;
  const controlSyncIds = new Set([
    "run_engine",
    "local_strategy_profile",
    "ml_strategy_profile",
    "rf_strategy_profile",
    "hgb_strategy_profile",
    "xgb_strategy_profile",
    "mlp_strategy_profile",
    "cnn_strategy_profile",
    "ai_strategy_profile",
    "ml_model",
    "rf_training_mode",
    "time_granularity",
  ]);
  el.addEventListener("input", () => {
    state.settingsDirty = true;
    if (controlSyncIds.has(id)) {
      if (id === "rf_strategy_profile" || id === "hgb_strategy_profile" || id === "xgb_strategy_profile" || id === "mlp_strategy_profile" || id === "cnn_strategy_profile" || id === "ml_model") {
        applyProfileMlDefaults();
      }
      syncMlControls();
    }
  });
  el.addEventListener("change", () => {
    state.settingsDirty = true;
    if (controlSyncIds.has(id)) {
      if (id === "rf_strategy_profile" || id === "hgb_strategy_profile" || id === "xgb_strategy_profile" || id === "mlp_strategy_profile" || id === "cnn_strategy_profile" || id === "ml_model") {
        applyProfileMlDefaults();
      }
      syncMlControls();
    }
  });
}

const timeGranularityEl = document.getElementById("time_granularity");
if (timeGranularityEl) {
  timeGranularityEl.addEventListener("change", syncMlControls);
  timeGranularityEl.addEventListener("input", syncMlControls);
}

renderStrategyDocs();
syncMlControls();
syncTopbar();

async function runBacktest() {
  try {
    setBusy(true);
    const engine = fieldValue("run_engine");
    const strategy = activeStrategyProfile();
    const modeSelect = document.getElementById("backtest_mode");
    const selectedModeOption = modeSelect ? modeSelect.options[modeSelect.selectedIndex] : null;
    const mode = fieldValue("backtest_mode") || "random";
    const backtestYear = selectedModeOption ? selectedModeOption.dataset.year || "" : "";
    const days = mode === "fixed_year" || mode === "natural_year" ? 366 : 14;
    const timeGranularity = fieldValue("time_granularity") || "fast";
    const intervalMinutes = Number(fieldValue("backtest_interval_minutes") || 30);
    const intervalSeconds = intervalMinutes * 60;
    const engineText = engine === "local" ? "本地策略" : engine === "ml" ? "机器学习" : "AI";
    document.getElementById("status").textContent = `${engineText}回测任务启动中`;
    const backtestEl = document.getElementById("backtest");
    backtestEl.className = "backtest empty";
    const modeText = mode === "natural_year" ? `${backtestYear}年` : mode === "fixed_year" ? "固定过去一年" : "随机区间";
    const granularityText = timeGranularity === "slow" ? "慢速" : "快速";
    backtestEl.textContent = `${engineText}${modeText}${granularityText}回测已提交，正在启动后台任务...`;
    const data = await api("/api/backtest/start", {
      method: "POST",
      body: JSON.stringify({
        ...formPayload(),
        days,
        engine,
        strategy,
        mode,
        backtest_year: backtestYear,
        time_granularity: timeGranularity,
        backtest_interval_minutes: intervalMinutes,
        interval_seconds: intervalSeconds,
      }),
    });
    state.backtestTaskId = data.task_id;
    state.settingsDirty = false;
    document.getElementById("status").textContent = data.message || "回测后台任务已启动";
    document.getElementById("backtestBtn").disabled = true;
    pollBacktestTask(data.task_id);
  } catch (error) {
    document.getElementById("status").textContent = error.message;
    const backtestEl = document.getElementById("backtest");
    backtestEl.className = "backtest empty";
    backtestEl.textContent = error.message;
  } finally {
    setBusy(false);
    if (state.backtestTaskId) {
      document.getElementById("backtestBtn").disabled = true;
    }
  }
}

async function pollBacktestTask(taskId) {
  if (!taskId) return;
  if (state.backtestPollTimer) {
    clearTimeout(state.backtestPollTimer);
    state.backtestPollTimer = null;
  }
  try {
    const data = await api(`/api/backtest/status?id=${encodeURIComponent(taskId)}`);
    const backtestEl = document.getElementById("backtest");
    if (data.status === "done") {
      state.backtestTaskId = null;
      document.getElementById("backtestBtn").disabled = false;
      document.getElementById("status").textContent = "回测完成";
      render(data.state, { forceConfig: true });
      return;
    }
    if (data.status === "error") {
      state.backtestTaskId = null;
      document.getElementById("backtestBtn").disabled = false;
      document.getElementById("status").textContent = data.error || "回测失败";
      backtestEl.className = "backtest empty";
      backtestEl.textContent = data.error || "回测失败";
      return;
    }
    const progress = Number(data.progress || 0);
    backtestEl.className = "backtest empty";
    backtestEl.textContent = `${data.message || "回测运行中"} ${progress}%`;
    document.getElementById("status").textContent = `回测后台运行中 ${progress}%`;
    state.backtestPollTimer = setTimeout(() => pollBacktestTask(taskId), 2000);
  } catch (error) {
    document.getElementById("status").textContent = error.message;
    state.backtestPollTimer = setTimeout(() => pollBacktestTask(taskId), 3000);
  }
}

async function pollAutoLearnTask(taskId) {
  if (!taskId) return;
  if (state.autoLearnPollTimer) {
    clearTimeout(state.autoLearnPollTimer);
    state.autoLearnPollTimer = null;
  }
  const status = document.getElementById("autoLearnStatus");
  const resultEl = document.getElementById("autoLearnResult");
  const button = document.getElementById("autoLearnBtn");
  const stopButton = document.getElementById("stopAutoLearnBtn");
  try {
    const data = await api(`/api/auto-learn/status?id=${encodeURIComponent(taskId)}`);
    if (data.status === "done" || data.status === "stopped") {
      state.autoLearnTaskId = null;
      if (button) button.disabled = false;
      if (stopButton) stopButton.disabled = false;
      const doneText = data.status === "stopped" ? "自动学习已停止" : "自动学习完成";
      if (status) status.textContent = doneText;
      document.getElementById("status").textContent = doneText;
      if (data.result) data.result.task_id = data.task_id || taskId;
      renderAutoLearnResult(data.result);
      return;
    }
    if (data.status === "error") {
      state.autoLearnTaskId = null;
      if (button) button.disabled = false;
      if (stopButton) stopButton.disabled = false;
      if (status) status.textContent = data.error || "自动学习失败";
      document.getElementById("status").textContent = data.error || "自动学习失败";
      if (resultEl) {
        resultEl.className = "auto-learn-result empty";
        resultEl.textContent = data.error || "自动学习失败";
      }
      return;
    }
    const progress = Number(data.progress || 0);
    const message = `${data.message || "自动学习运行中"} ${progress}%`;
    if (status) status.textContent = message;
    if (resultEl) {
      resultEl.className = "auto-learn-result empty";
      resultEl.textContent = message;
    }
    document.getElementById("status").textContent = `自动学习后台运行中 ${progress}%`;
    state.autoLearnPollTimer = setTimeout(() => pollAutoLearnTask(taskId), 2500);
  } catch (error) {
    if (status) status.textContent = error.message;
    document.getElementById("status").textContent = error.message;
    state.autoLearnPollTimer = setTimeout(() => pollAutoLearnTask(taskId), 3500);
  }
}

setInterval(async () => {
  if (state.backtestTaskId || state.autoLearnTaskId) {
    return;
  }
  try {
    render(await api("/api/state"));
  } catch (error) {
    document.getElementById("status").textContent = error.message;
  }
}, 10000);

api("/api/state").then((data) => render(data, { forceConfig: true })).catch((error) => {
  document.getElementById("status").textContent = error.message;
});

