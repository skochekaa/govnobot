# signals_trend.py — Trend-following стратегия
# ==============================================
#
# КОНЦЕПЦИЯ (отличается от bounce):
#   - НЕ ловим отскоки от уровней (bounce)
#   - Присоединяемся к УЖЕ установленному тренду
#   - Вход после отката к EMA20 + разворотная свеча в направлении тренда
#   - Альтернативный вход: пробой консолидации в направлении тренда
#
# ЦЕПОЧКА РЕШЕНИЯ:
#   1h уровни (для тейков/стопов) → 15m тренд → 5m откат к EMA20 → 1m реакция
#
# Интерфейс полностью совместим с signals.py (generate_signals_mtf)
# чтобы main.py мог переключаться через config.STRATEGY_MODE.

import numpy as np
import config
from levels import calculate_atr, _price_precision
from market_regime import detect_regime, is_direction_allowed
from log_setup import setup_logger

log = setup_logger("signals_trend")


def _round_price(price: float) -> float:
    """Округляет цену с учётом её порядка."""
    return round(price, _price_precision(price))


def calculate_ema(values: np.ndarray, period: int) -> float:
    """
    Exponential Moving Average — экспоненциальная скользящая средняя.
    Реагирует быстрее чем SMA на новые цены.
    """
    if len(values) == 0:
        return 0.0
    if len(values) < period:
        return float(np.mean(values))
    multiplier = 2 / (period + 1)
    ema = float(values[0])
    for v in values[1:]:
        ema = (float(v) - ema) * multiplier + ema
    return ema


def calculate_ema_series(values: np.ndarray, period: int) -> np.ndarray:
    """Возвращает массив EMA значений (последняя точка = текущая EMA)."""
    if len(values) == 0:
        return np.array([])
    multiplier = 2 / (period + 1)
    result = np.zeros(len(values))
    result[0] = float(values[0])
    for i in range(1, len(values)):
        result[i] = (float(values[i]) - result[i - 1]) * multiplier + result[i - 1]
    return result


def detect_trend(candles: np.ndarray,
                   period: int = None,
                   slope_pct_threshold: float = None,
                   slope_lookback: int = 5) -> str:
    """
    Определяет тренд по наклону EMA.

    Args:
        candles: OHLCV массив (используется close)
        period: период EMA (по умолчанию из config.TREND_EMA_FAST)
        slope_pct_threshold: минимальный наклон в % (по умолчанию config.TREND_SLOPE_PCT)
        slope_lookback: за сколько свечей считать наклон

    Returns:
        "up" / "down" / "flat"
    """
    if period is None:
        period = config.TREND_EMA_FAST
    if slope_pct_threshold is None:
        slope_pct_threshold = config.TREND_SLOPE_PCT

    if candles is None or len(candles) < period + slope_lookback:
        return "flat"

    closes = candles[:, 4].astype(float)
    ema_now = calculate_ema(closes, period)
    ema_past = calculate_ema(closes[:-slope_lookback], period)

    if ema_past <= 0:
        return "flat"

    slope_pct = (ema_now - ema_past) / ema_past * 100

    if slope_pct > slope_pct_threshold:
        return "up"
    if slope_pct < -slope_pct_threshold:
        return "down"
    return "flat"


# ── Pullback Entry: вход на откате к EMA20 ──

def detect_pullback_entry(candles_5m: np.ndarray,
                            trend_direction: str,
                            atr: float,
                            levels: dict) -> dict | None:
    """
    Ищет вход на откате к EMA20 в направлении тренда.

    Условия для LONG (тренд up):
      1. Цена находится около EMA20 (расстояние <= 0.5 × ATR)
      2. Последняя свеча — бычья (close > open)
      3. Есть нижняя тень (rejection from EMA, отскок)

    Зеркально для SHORT (тренд down).

    Args:
        candles_5m: OHLCV массив 5m
        trend_direction: "up" / "down" — определено detect_trend на старшем ТФ
        atr: ATR с рабочего ТФ
        levels: для определения тейк-профита (ближайший уровень в направлении тренда)

    Returns:
        Словарь сигнала или None
    """
    if trend_direction not in ("up", "down"):
        return None
    if candles_5m is None or len(candles_5m) < config.TREND_EMA_FAST + 5:
        return None
    if atr <= 0:
        return None

    closes = candles_5m[:, 4].astype(float)
    ema = calculate_ema(closes, config.TREND_EMA_FAST)

    last = candles_5m[-1]
    open_p = float(last[1])
    high = float(last[2])
    low = float(last[3])
    close = float(last[4])
    candle_range = high - low

    if candle_range == 0:
        return None

    # Расстояние от close до EMA20
    distance = abs(close - ema)
    if distance > atr * config.TREND_PULLBACK_ATR_MULT:
        return None  # слишком далеко от EMA, не откат

    if trend_direction == "up":
        # Бычья свеча с нижней тенью у EMA20
        is_bullish = close > open_p
        lower_wick = min(open_p, close) - low
        wick_ratio = lower_wick / candle_range

        if not is_bullish:
            return None
        if wick_ratio < 0.2:  # должна быть тень — признак отскока
            return None

        entry = close
        stop = low - atr * 0.2  # чуть ниже минимума свечи
        if entry <= stop:
            return None

        # Take: ближайшее сопротивление выше или 3×ATR
        resistances = [r["price"] for r in levels.get("resistances", [])
                       if r["price"] > entry + atr]
        take = resistances[0] if resistances else entry + atr * 3

        risk = entry - stop
        reward = take - entry
        if risk <= 0:
            return None
        rr = reward / risk

        return {
            "type": "trend_pullback",
            "direction": "long",
            "level": _round_price(ema),
            "entry": _round_price(entry),
            "stop": _round_price(stop),
            "take": _round_price(take),
            "risk_reward": round(rr, 2),
            "reason": f"pullback to EMA{config.TREND_EMA_FAST} in uptrend (wick {wick_ratio:.0%})",
        }

    elif trend_direction == "down":
        is_bearish = close < open_p
        upper_wick = high - max(open_p, close)
        wick_ratio = upper_wick / candle_range

        if not is_bearish:
            return None
        if wick_ratio < 0.2:
            return None

        entry = close
        stop = high + atr * 0.2
        if entry >= stop:
            return None

        # Take: ближайшая поддержка ниже или 3×ATR
        supports = [s["price"] for s in levels.get("supports", [])
                    if s["price"] < entry - atr]
        take = supports[0] if supports else entry - atr * 3

        risk = stop - entry
        reward = entry - take
        if risk <= 0:
            return None
        rr = reward / risk

        return {
            "type": "trend_pullback",
            "direction": "short",
            "level": _round_price(ema),
            "entry": _round_price(entry),
            "stop": _round_price(stop),
            "take": _round_price(take),
            "risk_reward": round(rr, 2),
            "reason": f"pullback to EMA{config.TREND_EMA_FAST} in downtrend (wick {wick_ratio:.0%})",
        }

    return None


# ── Breakout Entry: пробой локального экстремума по тренду ──

def detect_breakout_entry(candles_5m: np.ndarray,
                            trend_direction: str,
                            atr: float,
                            volume: dict,
                            levels: dict,
                            lookback: int = 10) -> dict | None:
    """
    Ищет пробой локального максимума/минимума В НАПРАВЛЕНИИ ТРЕНДА.

    Условия для LONG (тренд up):
      1. Текущая close > максимум последних N свечей (исключая текущую)
      2. Volume spike (всплеск объёма) — подтверждает пробой
      3. ATR > 0 (есть волатильность)

    Зеркально для SHORT.

    Это "продолжение тренда": после паузы / консолидации цена берёт новый
    максимум — обычно тренд продолжается.

    Args:
        candles_5m: OHLCV массив
        trend_direction: "up" / "down"
        atr: ATR с рабочего ТФ
        volume: результат analyze_volume() — нужен volume.spike.is_spike
        levels: для определения тейк-профита
        lookback: сколько свечей назад брать локальный экстремум

    Returns:
        Словарь сигнала или None
    """
    if trend_direction not in ("up", "down"):
        return None
    if candles_5m is None or len(candles_5m) < lookback + 2:
        return None
    if atr <= 0:
        return None

    # Volume spike обязателен — без него пробой часто ложный
    vol_spike = volume.get("spike", {}).get("is_spike", False)
    if not vol_spike:
        return None

    last = candles_5m[-1]
    open_p = float(last[1])
    high = float(last[2])
    low = float(last[3])
    close = float(last[4])

    # Локальный экстремум за последние N свечей (БЕЗ текущей)
    prior = candles_5m[-(lookback + 1):-1]
    prior_highs = prior[:, 2].astype(float)
    prior_lows = prior[:, 3].astype(float)
    local_max = float(np.max(prior_highs))
    local_min = float(np.min(prior_lows))

    if trend_direction == "up":
        # Пробой максимума на close
        if close <= local_max:
            return None
        # Свеча должна быть в направлении пробоя (бычья)
        if close <= open_p:
            return None

        entry = close
        stop = max(local_max - atr * 0.3, low - atr * 0.2)  # под уровень или под минимум свечи
        if entry <= stop:
            return None

        # Take: ближайшее сопротивление выше или 3×ATR
        resistances = [r["price"] for r in levels.get("resistances", [])
                       if r["price"] > entry + atr]
        take = resistances[0] if resistances else entry + atr * 3

        risk = entry - stop
        reward = take - entry
        if risk <= 0:
            return None
        rr = reward / risk

        return {
            "type": "trend_breakout",
            "direction": "long",
            "level": _round_price(local_max),
            "entry": _round_price(entry),
            "stop": _round_price(stop),
            "take": _round_price(take),
            "risk_reward": round(rr, 2),
            "reason": f"breakout above {_round_price(local_max)} in uptrend (vol spike)",
        }

    elif trend_direction == "down":
        if close >= local_min:
            return None
        if close >= open_p:  # медвежья свеча
            return None

        entry = close
        stop = min(local_min + atr * 0.3, high + atr * 0.2)
        if entry >= stop:
            return None

        supports = [s["price"] for s in levels.get("supports", [])
                    if s["price"] < entry - atr]
        take = supports[0] if supports else entry - atr * 3

        risk = stop - entry
        reward = entry - take
        if risk <= 0:
            return None
        rr = reward / risk

        return {
            "type": "trend_breakout",
            "direction": "short",
            "level": _round_price(local_min),
            "entry": _round_price(entry),
            "stop": _round_price(stop),
            "take": _round_price(take),
            "risk_reward": round(rr, 2),
            "reason": f"breakout below {_round_price(local_min)} in downtrend (vol spike)",
        }

    return None


# ── Оценка силы сигнала (для совместимости с trader.py) ──

def _evaluate_strength(signal_type: str, vol_strength: str,
                        rr: float, multi_tf: bool) -> str:
    """Оценивает силу trend-сигнала. Аналог bounce-версии из signals.py."""
    score = 0

    # RR — выше = лучше
    if rr >= 3.0:
        score += 2
    elif rr >= 2.0:
        score += 1

    # Объёмное подтверждение
    if vol_strength == "strong":
        score += 2
    elif vol_strength == "normal":
        score += 1

    # Multi-TF тренд (если на 1h тренд тоже совпадает)
    if multi_tf:
        score += 1

    # Breakout vs pullback: pullback обычно надёжнее
    if signal_type == "trend_pullback":
        score += 1

    if score >= 4:
        return "strong"
    elif score >= 2:
        return "medium"
    return "weak"


# ── Главная функция (полный совместимый интерфейс с signals.py) ──

def generate_signals_mtf(candles_by_tf: dict[str, np.ndarray],
                           levels: dict, volume: dict,
                           btc_candles: np.ndarray = None,
                           btc_senior_candles: np.ndarray = None,
                           symbol: str = "") -> list[dict]:
    """
    Главная функция trend-following стратегии.
    Интерфейс полностью совпадает с signals.generate_signals_mtf
    для прозрачной замены через config.STRATEGY_MODE.

    Цепочка:
      1. Определить тренд на 15m (старший работающий ТФ)
      2. Если тренд up/down → искать pullback и breakout entry на 5m
      3. Применить regime фильтр (как в bounce)
      4. Вернуть сигналы отсортированные по силе
    """
    candles_5m = candles_by_tf.get(config.TF_WORK)
    candles_15m = candles_by_tf.get(config.TF_MIDDLE)
    candles_1h = candles_by_tf.get(config.TF_SENIOR)

    if candles_5m is None or len(candles_5m) < config.TREND_EMA_FAST + 5:
        return []

    # ATR с рабочего ТФ
    atr = levels.get("atr", 0)
    if atr == 0 and len(candles_5m) > 20:
        atr = calculate_atr(candles_5m)
    if atr == 0:
        return []

    # ── Шаг 0: ATR filter ──
    if config.ATR_FILTER_ENABLED:
        current_price = float(candles_5m[-1, 4])
        if current_price > 0:
            atr_pct = atr / current_price * 100
            if atr_pct > config.ATR_MAX_PCT or atr_pct < config.ATR_MIN_PCT:
                log.debug("%s ATR %.2f%% вне диапазона, пропуск", symbol, atr_pct)
                return []

    # ── Шаг 1: определение тренда ──
    # Берём 15m как базовый ТФ для определения тренда (компромисс между шумом и реакцией)
    trend_source = candles_15m if (candles_15m is not None and len(candles_15m) >= 30) else candles_5m
    trend = detect_trend(trend_source)

    # Проверяем согласованность с 1h (multi-tf bonus)
    multi_tf_aligned = False
    if candles_1h is not None and len(candles_1h) >= 30:
        trend_1h = detect_trend(candles_1h)
        if trend_1h == trend and trend != "flat":
            multi_tf_aligned = True

    if trend == "flat":
        return []

    # ── Шаг 2: ищем точки входа ──
    all_signals = []

    pullback = detect_pullback_entry(candles_5m, trend, atr, levels)
    if pullback is not None and pullback["risk_reward"] >= config.TREND_MIN_RR:
        all_signals.append(pullback)

    breakout = detect_breakout_entry(candles_5m, trend, atr, volume, levels)
    if breakout is not None and breakout["risk_reward"] >= config.TREND_MIN_RR:
        all_signals.append(breakout)

    if not all_signals:
        return []

    # ── Шаг 3: market regime filter (тот же что в bounce) ──
    if config.REGIME_FILTER_ENABLED and btc_senior_candles is not None and len(btc_senior_candles) > 0:
        regime_info = detect_regime(
            btc_senior_candles,
            sma_fast=config.REGIME_SMA_FAST,
            sma_slow=config.REGIME_SMA_SLOW,
            slope_lookback=config.REGIME_SLOPE_LOOKBACK,
            slope_threshold_pct=config.REGIME_SLOPE_THRESHOLD_PCT,
        )
        regime = regime_info["regime"]
        after_regime = []
        for sig in all_signals:
            if is_direction_allowed(regime, sig["direction"],
                                      block_counter_trend=config.REGIME_BLOCK_COUNTER_TREND):
                sig["regime_check"] = regime_info
                after_regime.append(sig)
            else:
                log.info("%s %s %s заблокирован regime: %s",
                         symbol, sig["type"], sig["direction"], regime_info["reason"])
        all_signals = after_regime
        if not all_signals:
            return []

    # ── Шаг 4: оценка силы и финал ──
    vol_strength = volume.get("overall_strength", "weak")
    for sig in all_signals:
        sig["strength"] = _evaluate_strength(
            sig["type"], vol_strength, sig["risk_reward"], multi_tf_aligned
        )
        sig["multi_tf_level"] = multi_tf_aligned
        sig["level_source_tfs"] = [config.TF_MIDDLE] if not multi_tf_aligned else [
            config.TF_MIDDLE, config.TF_SENIOR
        ]
        sig["symbol"] = symbol

    # Сортировка по силе
    strength_order = {"strong": 0, "medium": 1, "weak": 2}
    all_signals.sort(key=lambda s: strength_order.get(s["strength"], 3))

    return all_signals
