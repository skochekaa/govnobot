# signals.py — Генерация торговых сигналов с мультитаймфрейм-подтверждением
# ==========================================================================
#
# Цепочка принятия решения:
#   1h  → уровни (уже в levels_mtf)
#   15m → подтверждение: тренд совпадает с направлением?
#   5m  → сигнал: bounce или breakout у уровня?
#   1m  → точный вход: есть ли реакция прямо сейчас?

import numpy as np
import config
from levels import calculate_atr
from volume_analyzer import check_trend_confirmation
from log_setup import setup_logger

log = setup_logger("signals")


# ── Подтверждение на 15m (тренд) ────────────

def check_middle_tf_confirmation(candles_15m: np.ndarray,
                                   direction: str) -> dict:
    """
    Проверяет, совпадает ли тренд на 15m с направлением сделки.

    Правила:
      LONG:  тренд на 15m должен быть up или flat (не down)
      SHORT: тренд на 15m должен быть down или flat (не up)

    Если тренд ПРОТИВ сделки — сигнал отклоняется.
    """
    if not config.REQUIRE_MIDDLE_TF_CONFIRMATION:
        return {"confirmed": True, "reason": "MTF check disabled"}

    if len(candles_15m) < 10:
        return {"confirmed": True, "reason": "insufficient 15m data"}

    trend = check_trend_confirmation(candles_15m, lookback=5)
    price_dir = trend["price_direction"]

    if direction == "long" and price_dir == "down":
        return {
            "confirmed": False,
            "reason": f"15m trend is DOWN, long rejected",
            "trend": trend,
        }

    if direction == "short" and price_dir == "up":
        return {
            "confirmed": False,
            "reason": f"15m trend is UP, short rejected",
            "trend": trend,
        }

    return {
        "confirmed": True,
        "reason": f"15m trend {price_dir} aligns with {direction}",
        "trend": trend,
    }


# ── Подтверждение на 1m (точный вход) ────────

def check_entry_tf_confirmation(candles_1m: np.ndarray,
                                  level_price: float,
                                  direction: str,
                                  atr_5m: float) -> dict:
    """
    Проверяет реакцию на 1m для точного входа.

    Зачем: сигнал на 5m может быть правильным, но если на 1m
    нет реакции — вход будет неточным, стоп шире, RR хуже.

    Проверяем на последних 3-5 минутных свечах:
    1. Rejection wick (длинная тень от уровня)
    2. Замедление (свечи уменьшаются у уровня)
    3. Разворотная свеча (бычья у поддержки / медвежья у сопротивления)
    """
    if not config.REQUIRE_ENTRY_TF_CONFIRMATION:
        return {"confirmed": True, "reason": "1m check disabled", "refined_entry": None}

    if len(candles_1m) < 5:
        return {"confirmed": True, "reason": "insufficient 1m data", "refined_entry": None}

    # ATR на 1m (для порогов)
    atr_1m = calculate_atr(candles_1m, period=14)
    if atr_1m == 0:
        atr_1m = atr_5m / 5  # грубая оценка

    last = candles_1m[-1]
    open_p = float(last[1])
    high = float(last[2])
    low = float(last[3])
    close = float(last[4])
    candle_range = high - low

    if candle_range == 0:
        return {"confirmed": False, "reason": "zero range 1m candle", "refined_entry": None}

    confirmed = False
    reason = ""
    refined_entry = None

    if direction == "long":
        lower_wick = min(open_p, close) - low
        wick_ratio = lower_wick / candle_range
        is_bullish = close > open_p

        # Rejection wick на 1m
        if wick_ratio > 0.5:
            confirmed = True
            reason = f"1m rejection wick ({wick_ratio:.0%})"
            refined_entry = round(close, 6)  # входим по close 1m-свечи

        # Бычья свеча у уровня
        elif is_bullish and abs(low - level_price) < atr_1m * 2:
            confirmed = True
            reason = "1m bullish candle at level"
            refined_entry = round(close, 6)

        # Замедление: последние 3 свечи уменьшаются
        elif len(candles_1m) >= 3:
            ranges = [float(c[2] - c[3]) for c in candles_1m[-3:]]
            if ranges[-1] < ranges[-2] < ranges[-3]:
                confirmed = True
                reason = "1m momentum slowdown"
                refined_entry = round(close, 6)

    elif direction == "short":
        upper_wick = high - max(open_p, close)
        wick_ratio = upper_wick / candle_range
        is_bearish = close < open_p

        if wick_ratio > 0.5:
            confirmed = True
            reason = f"1m rejection wick ({wick_ratio:.0%})"
            refined_entry = round(close, 6)

        elif is_bearish and abs(high - level_price) < atr_1m * 2:
            confirmed = True
            reason = "1m bearish candle at level"
            refined_entry = round(close, 6)

        elif len(candles_1m) >= 3:
            ranges = [float(c[2] - c[3]) for c in candles_1m[-3:]]
            if ranges[-1] < ranges[-2] < ranges[-3]:
                confirmed = True
                reason = "1m momentum slowdown"
                refined_entry = round(close, 6)

    if not confirmed:
        reason = "no 1m confirmation yet"

    return {"confirmed": confirmed, "reason": reason, "refined_entry": refined_entry}


# ── Детекция отскока ─────────────────────────

def detect_bounce(candles_5m: np.ndarray, levels: dict, volume: dict) -> list[dict]:
    signals = []
    if len(candles_5m) < 5:
        return signals

    atr = levels.get("atr", 0)
    if atr == 0:
        return signals

    current = candles_5m[-1]
    close = float(current[4])
    near_threshold = atr * config.NEAR_LEVEL_ATR_MULT

    for lvl in levels.get("all_levels", []):
        level_price = lvl["price"]
        distance = abs(close - level_price)

        if distance > near_threshold:
            continue

        if lvl["type"] == "support" and close > level_price:
            direction = "long"
        elif lvl["type"] == "resistance" and close < level_price:
            direction = "short"
        else:
            continue

        rejection = _check_rejection(candles_5m, level_price, direction, atr)
        if not rejection["detected"]:
            continue

        vol_spike = volume.get("spike", {}).get("is_spike", False)
        trend_dir = volume.get("trend_confirmation", {}).get("price_direction")

        if vol_spike and (
            (direction == "long" and trend_dir == "down") or
            (direction == "short" and trend_dir == "up")
        ):
            continue

        trade = _calculate_bounce_trade(direction, level_price, levels, atr)
        if trade is None or trade["risk_reward"] < config.MIN_RISK_REWARD:
            continue

        strength = _evaluate_signal_strength(lvl, rejection, volume)

        signals.append({
            "type": "bounce", "direction": direction, "level": level_price,
            **trade, "strength": strength, "reason": rejection["reason"],
            "multi_tf_level": lvl.get("multi_tf", False),
            "level_source_tfs": lvl.get("source_tfs", []),
        })

    return signals


# ── Детекция пробоя ─────────────────────────

def detect_breakout(candles_5m: np.ndarray, levels: dict, volume: dict) -> list[dict]:
    signals = []
    if len(candles_5m) < config.BREAKOUT_CONFIRM_CANDLES + 2:
        return signals

    atr = levels.get("atr", 0)
    if atr == 0:
        return signals

    recent_closes = [float(c[4]) for c in candles_5m[-config.BREAKOUT_CONFIRM_CANDLES:]]
    vol_spike = volume.get("spike", {}).get("is_spike", False)
    fake_levels = {fb["level"] for fb in volume.get("fake_breakouts", [])}

    for lvl in levels.get("all_levels", []):
        level_price = lvl["price"]
        if level_price in fake_levels:
            continue

        if lvl["type"] == "resistance":
            if all(c > level_price for c in recent_closes) and vol_spike:
                trade = _calculate_breakout_trade("long", level_price, levels, atr)
                if trade and trade["risk_reward"] >= config.MIN_RISK_REWARD:
                    signals.append({
                        "type": "breakout", "direction": "long", "level": level_price,
                        **trade,
                        "strength": _evaluate_signal_strength(lvl, {"detected": True}, volume),
                        "reason": f"breakout above {level_price}",
                        "multi_tf_level": lvl.get("multi_tf", False),
                        "level_source_tfs": lvl.get("source_tfs", []),
                    })

        elif lvl["type"] == "support":
            if all(c < level_price for c in recent_closes) and vol_spike:
                trade = _calculate_breakout_trade("short", level_price, levels, atr)
                if trade and trade["risk_reward"] >= config.MIN_RISK_REWARD:
                    signals.append({
                        "type": "breakout", "direction": "short", "level": level_price,
                        **trade,
                        "strength": _evaluate_signal_strength(lvl, {"detected": True}, volume),
                        "reason": f"breakout below {level_price}",
                        "multi_tf_level": lvl.get("multi_tf", False),
                        "level_source_tfs": lvl.get("source_tfs", []),
                    })

    return signals


# ── Вспомогательные функции ──────────────────

def _check_rejection(candles, level_price, direction, atr):
    last = candles[-1]
    open_p, high, low, close = float(last[1]), float(last[2]), float(last[3]), float(last[4])
    candle_range = high - low
    if candle_range == 0:
        return {"detected": False, "reason": ""}

    if direction == "long":
        lower_wick = min(open_p, close) - low
        wick_ratio = lower_wick / candle_range
        if wick_ratio > 0.5:
            return {"detected": True, "reason": f"5m rejection wick at support ({wick_ratio:.0%})"}
        if close > open_p and lower_wick > atr * 0.3:
            return {"detected": True, "reason": "5m bullish candle at support"}
        if len(candles) >= 3:
            ranges = [float(c[2] - c[3]) for c in candles[-3:]]
            if ranges[-1] < ranges[-2] < ranges[-3]:
                return {"detected": True, "reason": "5m momentum slowdown at support"}

    elif direction == "short":
        upper_wick = high - max(open_p, close)
        wick_ratio = upper_wick / candle_range
        if wick_ratio > 0.5:
            return {"detected": True, "reason": f"5m rejection wick at resistance ({wick_ratio:.0%})"}
        if close < open_p and upper_wick > atr * 0.3:
            return {"detected": True, "reason": "5m bearish candle at resistance"}
        if len(candles) >= 3:
            ranges = [float(c[2] - c[3]) for c in candles[-3:]]
            if ranges[-1] < ranges[-2] < ranges[-3]:
                return {"detected": True, "reason": "5m momentum slowdown at resistance"}

    return {"detected": False, "reason": ""}


def _calculate_bounce_trade(direction, level_price, levels, atr):
    offset = atr * 0.2
    stop_distance = atr * 1.0

    if direction == "long":
        entry = level_price + offset
        stop = level_price - stop_distance
        resistances = [r["price"] for r in levels.get("resistances", []) if r["price"] > entry + atr]
        take = resistances[0] if resistances else entry + atr * 3
    elif direction == "short":
        entry = level_price - offset
        stop = level_price + stop_distance
        supports = [s["price"] for s in levels.get("supports", []) if s["price"] < entry - atr]
        take = supports[0] if supports else entry - atr * 3
    else:
        return None

    risk = abs(entry - stop)
    reward = abs(take - entry)
    if risk == 0:
        return None

    return {"entry": round(entry, 2), "stop": round(stop, 2),
            "take": round(take, 2), "risk_reward": round(reward / risk, 2)}


def _calculate_breakout_trade(direction, level_price, levels, atr):
    stop_offset = atr * 0.5
    if direction == "long":
        entry = level_price + atr * 0.1
        stop = level_price - stop_offset
        resistances = [r["price"] for r in levels.get("resistances", []) if r["price"] > entry + atr]
        take = resistances[0] if resistances else entry + atr * 3
    elif direction == "short":
        entry = level_price - atr * 0.1
        stop = level_price + stop_offset
        supports = [s["price"] for s in levels.get("supports", []) if s["price"] < entry - atr]
        take = supports[0] if supports else entry - atr * 3
    else:
        return None

    risk = abs(entry - stop)
    reward = abs(take - entry)
    if risk == 0:
        return None
    return {"entry": round(entry, 2), "stop": round(stop, 2),
            "take": round(take, 2), "risk_reward": round(reward / risk, 2)}


def _evaluate_signal_strength(level, rejection, volume):
    score = 0
    # Сила уровня (с учётом MTF-весов)
    strength = level.get("strength", 0)
    if strength >= 6:      # сильный MTF-уровень (например 1h x3 + 15m x2)
        score += 3
    elif strength >= 3:
        score += 2
    elif strength >= 2:
        score += 1

    # Бонус за мультитаймфрейм
    if level.get("multi_tf"):
        score += 1

    if rejection.get("detected"):
        score += 1

    vol_strength = volume.get("overall_strength", "weak")
    if vol_strength == "strong":
        score += 2
    elif vol_strength == "normal":
        score += 1

    if score >= 5:
        return "strong"
    elif score >= 3:
        return "medium"
    return "weak"


# ── BTC-корреляция ───────────────────────────

def check_btc_filter(btc_candles, signal_direction):
    if not config.BTC_CORRELATION_CHECK:
        return {"allowed": True, "btc_change_pct": 0, "reason": "filter disabled"}
    if len(btc_candles) < 12:
        return {"allowed": True, "btc_change_pct": 0, "reason": "insufficient data"}

    hour_ago_close = float(btc_candles[-12, 4])
    current_close = float(btc_candles[-1, 4])
    change_pct = ((current_close - hour_ago_close) / hour_ago_close) * 100

    if signal_direction == "long" and change_pct < config.BTC_DUMP_THRESHOLD:
        return {"allowed": False, "btc_change_pct": round(change_pct, 2),
                "reason": f"BTC dumping {change_pct:.1f}%, long blocked"}
    if signal_direction == "short" and change_pct > config.BTC_PUMP_THRESHOLD:
        return {"allowed": False, "btc_change_pct": round(change_pct, 2),
                "reason": f"BTC pumping +{change_pct:.1f}%, short blocked"}

    return {"allowed": True, "btc_change_pct": round(change_pct, 2), "reason": "BTC OK"}


# ── Главная функция (MTF) ───────────────────

def generate_signals_mtf(candles_by_tf: dict[str, np.ndarray],
                          levels: dict, volume: dict,
                          btc_candles: np.ndarray = None,
                          symbol: str = "") -> list[dict]:
    """
    Генерирует сигналы с полной MTF-цепочкой:
      1h уровни → 15m подтверждение → 5m сигнал → 1m вход
    """
    candles_5m = candles_by_tf.get(config.TF_WORK)
    candles_15m = candles_by_tf.get(config.TF_MIDDLE)
    candles_1m = candles_by_tf.get(config.TF_ENTRY)

    if candles_5m is None or len(candles_5m) < 20:
        return []

    # Шаг 1: ищем сигналы на 5m (уровни уже MTF)
    all_signals = []
    all_signals.extend(detect_bounce(candles_5m, levels, volume))
    all_signals.extend(detect_breakout(candles_5m, levels, volume))

    if not all_signals:
        return []

    # Шаг 2: фильтруем через 15m подтверждение
    after_middle = []
    for sig in all_signals:
        if candles_15m is not None and len(candles_15m) > 10:
            mtf_check = check_middle_tf_confirmation(candles_15m, sig["direction"])
            sig["middle_tf_check"] = mtf_check
            if not mtf_check["confirmed"]:
                log.debug("%s %s %s отклонён: %s", symbol, sig["type"],
                          sig["direction"], mtf_check["reason"])
                continue
        after_middle.append(sig)

    # Шаг 3: подтверждение на 1m (точный вход)
    after_entry = []
    for sig in after_middle:
        if candles_1m is not None and len(candles_1m) > 5:
            entry_check = check_entry_tf_confirmation(
                candles_1m, sig["level"], sig["direction"],
                levels.get("atr", 0)
            )
            sig["entry_tf_check"] = entry_check
            if not entry_check["confirmed"]:
                log.debug("%s %s ждём 1m подтверждение: %s", symbol,
                          sig["type"], entry_check["reason"])
                continue
            # Уточняем вход по 1m
            if entry_check["refined_entry"]:
                sig["entry"] = entry_check["refined_entry"]
                # Пересчитываем RR с уточнённым входом
                risk = abs(sig["entry"] - sig["stop"])
                reward = abs(sig["take"] - sig["entry"])
                if risk > 0:
                    sig["risk_reward"] = round(reward / risk, 2)
        after_entry.append(sig)

    # Шаг 4: BTC-фильтр
    if btc_candles is not None and len(btc_candles) > 0:
        after_btc = []
        for sig in after_entry:
            btc_check = check_btc_filter(btc_candles, sig["direction"])
            sig["btc_filter"] = btc_check
            if btc_check["allowed"]:
                after_btc.append(sig)
            else:
                log.info("%s %s %s заблокирован: %s", symbol,
                         sig["type"], sig["direction"], btc_check["reason"])
        after_entry = after_btc

    # Финал
    for sig in after_entry:
        sig["symbol"] = symbol

    strength_order = {"strong": 0, "medium": 1, "weak": 2}
    after_entry.sort(key=lambda s: strength_order.get(s["strength"], 3))

    return after_entry
