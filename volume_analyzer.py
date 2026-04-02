# volume_analyzer.py — Анализ объёма
# ====================================
# Объём = "топливо" для движения цены.
# Без объёма пробой = ловушка, отскок = слабый.

import numpy as np
import config
from log_setup import setup_logger

log = setup_logger("volume")


def detect_volume_spike(candles: np.ndarray) -> dict:
    """Определяет всплеск объёма на последней свече."""
    period = config.VOLUME_AVG_PERIOD

    if len(candles) < period + 1:
        return {"is_spike": False, "current_volume": 0, "avg_volume": 0, "ratio": 0}

    volumes = candles[:, 5]
    current_vol = float(volumes[-1])
    avg_vol = float(np.mean(volumes[-(period + 1):-1]))
    ratio = current_vol / avg_vol if avg_vol > 0 else 0

    return {
        "is_spike": ratio >= config.VOLUME_SPIKE_MULT,
        "current_volume": current_vol,
        "avg_volume": avg_vol,
        "ratio": round(ratio, 2),
    }


def check_trend_confirmation(candles: np.ndarray,
                              lookback: int = 5) -> dict:
    """
    Проверяет подтверждение тренда объёмом.

    Цена растёт + объём растёт = бычье подтверждение
    Цена растёт + объём падает = слабость
    Цена падает + объём растёт = медвежье подтверждение
    Цена падает + объём падает = истощение
    """
    if len(candles) < lookback + 1:
        return {"price_direction": "flat", "volume_direction": "flat",
                "confirmed": False, "description": "insufficient_data"}

    recent = candles[-lookback:]

    price_change = float(recent[-1, 4] - recent[0, 4])
    avg_price = float(np.mean(recent[:, 4]))
    price_change_pct = (price_change / avg_price) * 100

    volumes = recent[:, 5]
    x = np.arange(len(volumes))
    vol_slope = np.polyfit(x, volumes, 1)[0]
    avg_vol = float(np.mean(volumes))
    vol_slope_pct = (vol_slope / avg_vol) * 100 if avg_vol > 0 else 0

    threshold = 0.1
    if price_change_pct > threshold:
        price_dir = "up"
    elif price_change_pct < -threshold:
        price_dir = "down"
    else:
        price_dir = "flat"

    if vol_slope_pct > 1:
        vol_dir = "up"
    elif vol_slope_pct < -1:
        vol_dir = "down"
    else:
        vol_dir = "flat"

    if price_dir == "up" and vol_dir == "up":
        desc, confirmed = "bullish_confirmed", True
    elif price_dir == "up" and vol_dir == "down":
        desc, confirmed = "weakness", False
    elif price_dir == "down" and vol_dir == "up":
        desc, confirmed = "bearish_confirmed", True
    elif price_dir == "down" and vol_dir == "down":
        desc, confirmed = "exhaustion", False
    else:
        desc, confirmed = "neutral", False

    return {
        "price_direction": price_dir,
        "volume_direction": vol_dir,
        "confirmed": confirmed,
        "description": desc,
    }


def detect_fake_breakout(candles: np.ndarray, level_price: float,
                          atr: float) -> dict:
    """Определяет ложный пробой уровня."""
    if len(candles) < 3:
        return {"is_fake": False, "direction": None, "wick_size": 0}

    last = candles[-1]
    high = float(last[2])
    low = float(last[3])
    close = float(last[4])
    volume_spike = detect_volume_spike(candles)

    if high > level_price and close < level_price:
        wick = (high - level_price) / atr if atr > 0 else 0
        return {
            "is_fake": not volume_spike["is_spike"],
            "direction": "above",
            "wick_size": round(wick, 2),
        }

    if low < level_price and close > level_price:
        wick = (level_price - low) / atr if atr > 0 else 0
        return {
            "is_fake": not volume_spike["is_spike"],
            "direction": "below",
            "wick_size": round(wick, 2),
        }

    return {"is_fake": False, "direction": None, "wick_size": 0}


def analyze_volume(candles: np.ndarray, levels: dict,
                    trade_delta: dict = None) -> dict:
    """
    ГЛАВНАЯ ФУНКЦИЯ — полный анализ объёма.

    Собирает: всплеск, подтверждение тренда, ложные пробои, дельту.
    """
    spike = detect_volume_spike(candles)
    trend = check_trend_confirmation(candles)

    atr = levels.get("atr", 0)
    fake_breakouts = []
    for lvl in levels.get("all_levels", [])[:5]:
        fb = detect_fake_breakout(candles, lvl["price"], atr)
        if fb["is_fake"]:
            fake_breakouts.append({"level": lvl["price"], **fb})

    if spike["is_spike"] and trend["confirmed"]:
        strength = "strong"
    elif spike["is_spike"] or trend["confirmed"]:
        strength = "normal"
    else:
        strength = "weak"

    log.debug("Volume: spike=%s trend=%s strength=%s",
              spike["is_spike"], trend["description"], strength)

    return {
        "spike": spike,
        "trend_confirmation": trend,
        "fake_breakouts": fake_breakouts,
        "delta": trade_delta or {},
        "overall_strength": strength,
    }
