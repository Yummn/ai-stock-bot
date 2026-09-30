from __future__ import annotations

import hashlib
import json
import time
import uuid
import zipfile

import numpy as np

from .features import FEATURE_VERSION, prediction_samples, training_samples
from .schema import AppConfig, Preset
from .store import Store, dumps


class TorchNetwork:
    def __init__(self, model, params, seed, progress):
        self.kind, self.params, self.seed, self.progress = model, params, seed, progress

    def fit(self, x, y, dates, horizon):
        import torch
        from torch import nn

        torch.set_num_threads(2)
        torch.manual_seed(self.seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.seed)
        self.device = "cuda" if self.params["device"] != "cpu" and torch.cuda.is_available() else "cpu"
        self.classes_, encoded = np.unique(y, return_inverse=True)
        unique_dates = np.unique(dates)
        cut = unique_dates[max(1, int(len(unique_dates) * .8))] if len(unique_dates) > 10 else unique_dates[-1]
        train_dates = unique_dates[unique_dates < cut]
        embargo_date = train_dates[max(0, len(train_dates) - horizon)] if len(train_dates) else cut
        train_mask, val_mask = dates < embargo_date, dates >= cut
        if train_mask.sum() < 20 or val_mask.sum() < 5:
            raise ValueError("按时间留出的训练/验证样本不足")
        axes = (0, 1) if x.ndim == 3 else 0
        self.mean = x[train_mask].mean(axis=axes, keepdims=True)
        self.std = np.maximum(x[train_mask].std(axis=axes, keepdims=True), 1e-6)
        tensor = torch.from_numpy(((x - self.mean) / self.std).astype(np.float32))
        if self.kind == "cnn":
            tensor = tensor.permute(0, 2, 1).contiguous()
        target = torch.from_numpy(encoded.astype(np.int64))
        p, layers = self.params, []
        input_size = x.shape[-1]
        if self.kind == "mlp":
            for _ in range(int(p["layer_count"])):
                layers.extend([nn.Linear(input_size, int(p["layer_size"])), nn.ReLU(), nn.Dropout(p["dropout"])])
                input_size = int(p["layer_size"])
        else:
            for _ in range(int(p["layers"])):
                layers.extend([nn.Conv1d(input_size, int(p["channels"]), int(p["kernel_size"]), padding=int(p["kernel_size"]) // 2), nn.ReLU(), nn.Dropout(p["dropout"])])
                input_size = int(p["channels"])
            layers.extend([nn.AdaptiveAvgPool1d(1), nn.Flatten()])
        layers.append(nn.Linear(input_size, len(self.classes_)))
        self.network = nn.Sequential(*layers).to(self.device)
        optimizer = torch.optim.AdamW(self.network.parameters(), lr=p["learning_rate"], weight_decay=p["weight_decay"])
        criterion = nn.CrossEntropyLoss()
        loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(tensor[train_mask], target[train_mask]), batch_size=int(p["batch_size"]), shuffle=True, num_workers=0, generator=torch.Generator().manual_seed(self.seed))
        best_loss, best_state, stale, history = float("inf"), None, 0, []
        for epoch in range(int(p["epochs"])):
            self.network.train()
            train_loss = 0.
            for bx, by in loader:
                optimizer.zero_grad(set_to_none=True)
                loss = criterion(self.network(bx.to(self.device)), by.to(self.device))
                if not torch.isfinite(loss):
                    raise ValueError("训练损失非有限值，请降低学习率")
                loss.backward()
                optimizer.step()
                train_loss += float(loss.detach().cpu()) * len(bx)
            self.network.eval()
            val_loss, val_count = 0., 0
            indices = np.flatnonzero(val_mask)
            with torch.inference_mode():
                for offset in range(0, len(indices), int(p["batch_size"])):
                    subset = indices[offset:offset + int(p["batch_size"])]
                    val_loss += float(criterion(self.network(tensor[subset].to(self.device)), target[subset].to(self.device)).cpu()) * len(subset)
                    val_count += len(subset)
            val_loss /= max(1, val_count)
            history.append({"epoch": epoch + 1, "training_loss": train_loss / int(train_mask.sum()), "validation_loss": val_loss})
            if val_loss < best_loss - 1e-5:
                best_loss = val_loss
                best_state = {k: v.detach().cpu().clone() for k, v in self.network.state_dict().items()}
                stale = 0
            else:
                stale += 1
            if epoch % 10 == 0:
                self.progress(f"{self.kind.upper()} {self.device} · 第 {epoch + 1} 轮，验证损失 {val_loss:.4f}")
            if p["early_stopping"] and stale >= 12:
                break
        self.network.load_state_dict(best_state)
        self.report = {"device": self.device, "epochs": len(history), "validation_loss": best_loss, "training_samples": int(train_mask.sum()), "validation_samples": int(val_mask.sum()), "loss_history": history}
        return self

    def predict_proba(self, x):
        import torch

        tensor = torch.from_numpy(((x - self.mean) / self.std).astype(np.float32))
        if self.kind == "cnn":
            tensor = tensor.permute(0, 2, 1)
        self.network.eval()
        output = []
        with torch.inference_mode():
            for offset in range(0, len(x), int(self.params["batch_size"])):
                output.append(torch.softmax(self.network(tensor[offset:offset + int(self.params["batch_size"])].to(self.device)), dim=1).cpu().numpy())
        return np.concatenate(output)


def build_tree(model, params, seed):
    p = dict(params)
    if model == "random_forest":
        from sklearn.ensemble import RandomForestClassifier
        p["max_features"] = "sqrt" if p.pop("max_features_sqrt") else None
        p["class_weight"] = "balanced_subsample" if p["class_weight"] else None
        return RandomForestClassifier(**p, random_state=seed, n_jobs=2)
    if model == "hist_gradient_boosting":
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(**p, early_stopping=False, random_state=seed)
    from xgboost import XGBClassifier
    device = p.pop("device")
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
    return XGBClassifier(**p, device=device, tree_method="hist", random_state=seed, n_jobs=2)


def probabilities(store: Store, histories, config: AppConfig, preset: Preset, cutoff, symbol, profile, progress, workspace=None):
    workspace = workspace if workspace is not None else {"samples": {}, "estimators": {}}
    modes = ["self", "classified", "mixed"] if preset.training_mode == "self" else ["classified", "mixed"] if preset.training_mode == "classified" else ["mixed"]
    attempts, chosen = [], None
    for mode in modes:
        sample_key = (config.model, preset.training_years, preset.target, preset.horizon, preset.label_basis, config.risk.commission_rate, config.risk.slippage_bps, preset.params.get("window", 1), str(cutoff), mode, symbol if mode == "self" else None, profile if mode == "classified" else None)
        if sample_key not in workspace["samples"]:
            workspace["samples"][sample_key] = training_samples(histories, config, preset, cutoff, symbol, mode, profile)
        x, y, dates, used = workspace["samples"][sample_key]
        attempts.append({"mode": mode, "samples": len(y), "classes": len(np.unique(y))})
        if len(y) >= preset.min_samples and len(np.unique(y)) >= 2:
            chosen = mode
            break
    if not chosen:
        return None, {"symbol": symbol, "profile": profile, "mode": "local_fallback", "attempts": attempts, "samples": len(y), "reason": "样本或类别不足，回退本地规则"}
    report = {"symbol": symbol, "profile": profile, "mode": chosen, "samples": len(y), "classes": {str(int(k)): int((y == k).sum()) for k in np.unique(y)}, "attempts": attempts, "sources": used, "cutoff": str(cutoff.date()), "label_basis": preset.label_basis, "label_costs": {"commission_rate": config.risk.commission_rate, "slippage_bps": config.risk.slippage_bps} if preset.label_basis == "next_open_net" else None}
    test_frame = histories[symbol]
    test_x = prediction_samples(test_frame, config.model, preset)
    valid = np.isfinite(test_x).all(axis=tuple(range(1, test_x.ndim)))
    training_key = hashlib.sha256(dumps({"feature": FEATURE_VERSION, "model": config.model, "params": preset.params, "seed": config.seed, "target": preset.target, "horizon": preset.horizon, "label_basis": preset.label_basis, "costs": report["label_costs"]}).encode() + x.tobytes() + y.tobytes() + dates.tobytes()).hexdigest()
    key = hashlib.sha256(training_key.encode() + test_x.tobytes()).hexdigest()
    cache_dir = store.root / "predictions"
    cache_dir.mkdir(exist_ok=True)
    cache = cache_dir / f"{key}.npz"
    if cache.exists():
        try:
            with np.load(cache, allow_pickle=False) as saved:
                result = saved["probabilities"]
                saved_report = json.loads(str(saved["report"]))
            return result, {**report, **saved_report, "cached": True}
        except (ValueError, OSError, KeyError, EOFError, zipfile.BadZipFile):
            progress("预测缓存不完整，重新训练")
    progress(f"训练 {symbol} · {profile} · {len(y)} 条样本 · {chosen}")
    started = time.monotonic()
    if training_key in workspace["estimators"]:
        estimator, classes, previous_extra = workspace["estimators"][training_key]
        extra = {**previous_extra, "fit_reused": True}
    elif config.model in {"mlp", "cnn"}:
        estimator = TorchNetwork(config.model, preset.params, config.seed, progress)
        try:
            estimator.fit(x, y, dates, preset.horizon + int(preset.label_basis == "next_open_net"))
        except RuntimeError as exc:
            if "cuda" not in str(exc).lower() and "out of memory" not in str(exc).lower():
                raise
            progress("GPU 训练异常，使用 CPU 重试")
            estimator = TorchNetwork(config.model, {**preset.params, "device": "cpu"}, config.seed, progress).fit(x, y, dates, preset.horizon + int(preset.label_basis == "next_open_net"))
        extra = estimator.report
        classes = estimator.classes_
    else:
        classes, encoded = np.unique(y, return_inverse=True)
        estimator = build_tree(config.model, preset.params, config.seed)
        try:
            from threadpoolctl import threadpool_limits
            with threadpool_limits(limits=2):
                estimator.fit(x, encoded)
        except Exception as exc:
            if config.model != "xgboost" or preset.params.get("device") == "cpu":
                raise
            progress(f"XGBoost GPU 不可用，CPU 重试：{type(exc).__name__}")
            estimator = build_tree(config.model, {**preset.params, "device": "cpu"}, config.seed)
            with threadpool_limits(limits=2):
                estimator.fit(x, encoded)
        device = estimator.get_params().get("device", "cpu")
        extra = {"device": device}
    if chosen != "self":
        workspace["estimators"][training_key] = estimator, classes, dict(extra)
    count = 5 if preset.target == "trend5" else 2
    result = np.full((len(test_x), count), np.nan)
    if valid.any():
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=2):
            predicted = estimator.predict_proba(test_x[valid])
        aligned = np.zeros((int(valid.sum()), count))
        for i, cls in enumerate(classes):
            aligned[:, int(cls)] = predicted[:, i]
        result[valid] = aligned
    extra["training_seconds"] = round(time.monotonic() - started, 3)
    temporary = cache.with_name(f"{key}.{uuid.uuid4().hex}.tmp")
    with temporary.open("wb") as output:
        np.savez_compressed(output, probabilities=result, report=np.array(dumps(extra)))
    temporary.replace(cache)
    return result, {**report, **extra, "cached": False}
