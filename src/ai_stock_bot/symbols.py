from __future__ import annotations


def normalize_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if cleaned.startswith(("SH", "SZ", "BJ")) and len(cleaned) == 8:
        return f"{cleaned[2:]}.{cleaned[:2]}"
    if cleaned.endswith(".SS"):
        return f"{cleaned[:-3]}.SH"
    return cleaned


def symbol_digits(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    return normalized.split(".", 1)[0]


def infer_cn_exchange(code: str) -> str:
    digits = symbol_digits(code)
    if digits.startswith(("5", "6", "9")):
        return "SH"
    if digits.startswith(("0", "1", "2", "3")):
        return "SZ"
    if digits.startswith(("4", "8")):
        return "BJ"
    raise ValueError(f"Cannot infer Chinese exchange for symbol: {code}")


def to_cn_symbol(symbol: str, suffix_style: str = "qmt") -> str:
    normalized = normalize_symbol(symbol)
    digits = symbol_digits(normalized)
    exchange = normalized.split(".", 1)[1] if "." in normalized else infer_cn_exchange(digits)
    exchange = "SH" if exchange == "SS" else exchange
    if suffix_style == "tushare":
        return f"{digits}.{exchange}"
    if suffix_style == "qmt":
        return f"{digits}.{exchange}"
    if suffix_style == "ptrade":
        ptrade_exchange = "SS" if exchange == "SH" else exchange
        return f"{digits}.{ptrade_exchange}"
    if suffix_style == "akshare":
        return digits
    raise ValueError(f"Unsupported suffix_style: {suffix_style}")


def to_yahoo_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if "." not in normalized:
        digits = symbol_digits(normalized)
        exchange = infer_cn_exchange(digits)
        normalized = f"{digits}.{exchange}"
    if normalized.endswith(".SH"):
        return f"{normalized[:-3]}.SS"
    return normalized
