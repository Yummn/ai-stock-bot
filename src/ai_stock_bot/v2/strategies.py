from __future__ import annotations

import numpy as np
import pandas as pd

from .features import online_regimes
from .schema import AppConfig, Preset


def current_profile(config: AppConfig, scene: str):
    return {"bull": "aggressive", "bear": "conservative", "sideways": "balanced"}[scene] if config.auto_profile else config.profile


def decide(row, probabilities, config: AppConfig, profile, scene, fallback=False):
    p = config.preset(profile)
    score = sum([row.trend > 0, row.price_sma20 > 0, row.momentum5 > 0, row.momentum20 > 0, .3 < row.rsi < .8, row.volume_ratio > 1])
    probability = None
    model_used = probabilities is not None and np.isfinite(probabilities).all()
    fallback = bool(fallback or config.engine == "ml" and not model_used)
    if model_used:
        probability = float(probabilities[1]) if len(probabilities) == 2 else float(probabilities[3] + probabilities[4] + .5 * probabilities[2])
        buy, sell = p.buy_threshold, p.sell_threshold
        if p.calibrate_regime:
            if scene == "bull":
                buy, sell = max(0, buy - .03), max(0, sell - .04)
            elif scene == "bear":
                buy, sell = min(1, buy + .05), min(1, sell + .04)
            else:
                buy = min(1, buy + .02)
                sell = min(1, sell + .06) if row.range_position > .85 else sell
        side = "buy" if probability >= buy else "sell" if probability <= sell else "hold"
        reason = f"上涨机会分 {probability:.1%}；买入门槛 {buy:.1%}，卖出门槛 {sell:.1%}"
    else:
        side = "buy" if score >= p.buy_score else "sell" if score <= 1 else "hold"
        probability = score / 6
        reason = f"本地买入分 {score}/6，门槛 {p.buy_score}" + ("；机器学习样本不足，已回退" if fallback else "")
    return {"side": side, "probability": probability, "rank_score": probability, "opportunity_score": probability if model_used else None, "confidence": None, "effective_buy_threshold": buy if model_used else None, "effective_sell_threshold": sell if model_used else None, "source": config.model if model_used else "local", "score_kind": "opportunity" if model_used else "rule_score", "score": int(score), "profile": profile, "scene": scene, "reason": reason, "fallback": fallback}


def ai_decisions(config, snapshots, account_context=None, as_of=None, ml_references=None):
    from ..ai import CodexSignalProvider, OpenAISignalProvider
    from ..config import load_settings
    from ..models import MarketSnapshot, Signal, Side

    import json
    import shutil
    import hashlib
    from typing import Literal
    from pydantic import BaseModel, ConfigDict, Field
    from pathlib import Path

    class AISignal(BaseModel):
        model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
        symbol: str
        side: Literal["buy", "sell", "hold"]
        confidence: float = Field(ge=0, le=1)
        reason: str = Field(min_length=1, max_length=2000)

    class PanelCodexProvider(CodexSignalProvider):
        def decide(self, items):
            from .ai_transport import codex_json
            try:
                payload, _ = codex_json(settings, prompt(items), web=False)
                return self._signals_from_payload(payload)
            except Exception as exc:
                return self._fallback(items, "parse" if isinstance(exc, ValueError) else "provider")

        def _build_prompt(self, items):
            return prompt(items)

        def _guard_signals(self, items, signals):
            return signals

        def _signals_from_payload(self, payload):
            if not isinstance(payload, dict) or not isinstance(payload.get("signals"), list):
                raise ValueError("Missing signals")
            parsed = [AISignal.model_validate(row) for row in payload["signals"]]
            if len({row.symbol for row in parsed}) != len(parsed) or any(row.symbol not in supplied for row in parsed):
                raise ValueError("Duplicate or unknown symbol")
            return [Signal(row.symbol, Side(row.side), row.confidence, row.reason) for row in parsed]

        def _fallback(self, items, reason):
            # Provider errors never turn into an unrequested local buy order.
            self.failed = True
            self.failure_code = "invalid_response" if "parse" in reason.lower() else "provider_unavailable"
            return [Signal(item.symbol, Side.HOLD, 0., "AI 调用或响应校验失败，本轮安全观望；风控仍独立执行") for item in items]

    settings, output, groups, scenes = load_settings(), {}, {}, {}
    def compact_reference(value):
        entries = []
        for entry in value.get("references", []):
            fields = ("model", "profile", "status", "side", "opportunity_score", "class_scores", "target", "label_basis", "horizon", "training_cutoff", "training_mode", "samples", "buy_threshold", "sell_threshold", "reason")
            entries.append({key: entry[key] for key in fields if key in entry})
        return {"status": value.get("status", "unavailable"), "summary": value.get("summary", {}), "references": entries}

    for symbol, price, row in snapshots:
        scene = str(online_regimes(pd.DataFrame([row]))[0])
        profile = current_profile(config, scene)
        scenes[symbol] = scene
        groups.setdefault(profile, []).append(MarketSnapshot(symbol=symbol, last_price=float(price), previous_close=float(price / (1 + row.change)), change_pct=float(row.change), sma_5=float(price / (1 + row.price_sma5)), sma_20=float(price / (1 + row.price_sma20)), volume=0, momentum_5=float(row.momentum5), momentum_20=float(row.momentum20), rsi_14=float(row.rsi * 100), volatility_20=float(row.volatility), volume_ratio_20=float(row.volume_ratio)))
    for profile, items in groups.items():
        preset = config.preset(profile)
        supplied = {item.symbol for item in items}
        failed = False
        failure_code = None
        context = {"as_of": as_of, "account": account_context, "risk": config.risk.model_dump(), "profile": profile, "strategy_prompt": preset.ai_prompt, "max_buys": preset.max_buys, "allow_add": preset.allow_add, "scenes": {s: scenes[s] for s in sorted(supplied)}, "execution": "Signals at close; next session open with fees/slippage. No future price is available."}
        context["ml_references"] = {s: compact_reference((ml_references or {}).get(s, {})) for s in sorted(supplied)}

        def prompt(batch):
            return "\n".join([
                "You generate signals for a local paper-trading simulator. Use only these historical numeric snapshots. Do not use tools or external knowledge.",
                f"Strategy: {profile}. Maximum buy signals: {preset.max_buys}.", preset.ai_prompt,
                "Confidence is confidence in the selected action, NOT the probability of a price rise or a calibrated win rate. Account/risk context below is authoritative; never invent missing data. Volume=0 means unavailable, not zero activity.",
                "Review all 15 ML references per symbol: five models, each with aggressive, balanced, and conservative profiles. Custom is excluded. Some may be unavailable. Compare target, horizon, label basis, training date, and contradictory actions; do not count correlated models as independent votes or average uncalibrated scores. Decide independently with account risk and explain the decisive support and disagreements in reason. If every ML reference for a symbol is unavailable, HOLD. No current news is supplied in this historical simulation; never infer it from memory.",
                json.dumps(context, ensure_ascii=False),
                'Return JSON only: {"signals":[{"symbol":"510300.SH","side":"buy|sell|hold","confidence":0.0,"reason":"Chinese reason"}]}. Include every supplied symbol. Confidence must be between 0 and 1.',
                json.dumps([item.__dict__ for item in batch], ensure_ascii=False),
            ])

        provider_name = "openai" if settings.ai_provider == "openai" else "codex"
        model = settings.openai_model if provider_name == "openai" else settings.codex_model
        try:
            if provider_name == "openai":
                if not settings.openai_api_key:
                    raise ValueError("Missing API key")
                provider = OpenAISignalProvider(settings.openai_api_key, settings.openai_model)
                response = provider.client.chat.completions.create(model=settings.openai_model, messages=[{"role":"system","content":"Return JSON only."},{"role":"user","content":prompt(items)}], temperature=.1)
                parser = PanelCodexProvider(codex_bin="unused", strategy=profile)
                signals = parser._signals_from_payload(json.loads(response.choices[0].message.content or "{}"))
            else:
                binary = settings.codex_bin if settings.codex_bin and Path(settings.codex_bin).exists() else shutil.which("codex")
                if not binary:
                    raise ValueError("Missing Codex CLI")
                provider = PanelCodexProvider(codex_bin=binary, model=settings.codex_model, timeout_seconds=settings.codex_timeout_seconds, strategy=profile, max_buys_per_day=preset.max_buys)
                signals = provider.decide(items)
            failed = bool(getattr(provider, "failed", False))
            failure_code = getattr(provider, "failure_code", None)
        except Exception as exc:
            failed = True
            failure_code = "invalid_response" if isinstance(exc, (ValueError, TypeError)) else "provider_unavailable"
            signals = PanelCodexProvider(codex_bin="unused", strategy=profile)._fallback(items, "provider error")
        audit = {"provider": provider_name, "model": model or "provider_default", "status": "failed" if failed else "ok", "failure_code": failure_code, "as_of": as_of, "prompt_hash": hashlib.sha256(prompt(items).encode()).hexdigest(), "input": context, "snapshots": [item.__dict__ for item in items]}
        for signal in signals:
            if signal.symbol not in supplied or not np.isfinite(signal.confidence):
                continue
            output[signal.symbol] = {"symbol": signal.symbol, "side": signal.side.value, "probability": signal.confidence, "rank_score": signal.confidence, "opportunity_score": None, "confidence": None if failed else signal.confidence, "score_kind": "action_confidence", "source": "safe_hold" if failed else provider_name, "profile": profile, "scene": scenes[signal.symbol], "reason": signal.reason, "fallback": failed, "score": 0, "audit": audit}
        for symbol in supplied - output.keys():
            output[symbol] = {"symbol":symbol,"side":"hold","probability":0.,"rank_score":0.,"opportunity_score":None,"confidence":None,"score_kind":"action_confidence","source":"safe_hold","profile":profile,"scene":scenes[symbol],"reason":"AI 未返回该标的，安全观望","fallback":True,"score":0,"audit":{**audit, "status":"failed", "failure_code":"missing_symbol"}}
        for symbol in supplied:
            value = output[symbol]
            ref = (ml_references or {}).get(symbol, {"status": "unavailable", "summary": {"available": 0}, "references": []})
            value["ml_reference"] = ref
            value["ai_proposed_side"] = value["side"] if not value["fallback"] else None
            available = [e for e in ref.get("references", []) if e.get("status") == "ok"]
            matching = sum(e.get("side") == value["side"] for e in available)
            value["reference_agreement"] = {"matching": matching, "available": len(available)}
            value["reference_alignment"] = "unavailable" if not available else "ai_unavailable" if value["fallback"] else "agree" if matching == len(available) else "mixed" if matching else "override"
            if not available:
                value.update(side="hold", source="safe_hold", fallback=True, confidence=None, rank_score=0., probability=0., reason="机器学习参考不可用，本轮安全观望；" + value["reason"])
    return output
