# market_regime.py — Детектор режима рынка по BTC
# ================================================
#
# Анализирует BTC на старшем ТФ (рекомендуется 1h) и возвращает:
#   - BULL:  бычий рынок (тренд вверх)   → блокируем шорты
#   - BEAR:  медвежий рынок (тренд вниз) → блокируем лонги
#   - RANGE: боковик/неопределённо       → разрешаем оба направления
#
# Логика основана на:
#   1. Положение цены относительно SMA50 (выше/ниже)
#   2. Положение SMA20 относительно SMA50 (быстрая выше/ниже медленной)
#   3. Наклон SMA20 за последние N свечей (растёт/падает/плоская)
#
# Все три условия должны совпадать для определённого тренда.
# Если хоть одно не совпадает — режим RANGE (безопаснее не угадывать).

import numpy as np


def _sma(values: np.ndarray, period: int) -> float:
    """Простая скользящая средняя последних N значений."""
    if len(values) == 0:
        return 0.0
    if len(values) < period:
        return float(np.mean(values))
    return float(np.mean(values[-period:]))


def detect_regime(btc_candles: np.ndarray,
                    sma_fast: int = 20,
                    sma_slow: int = 50,
                    slope_lookback: int = 10,
                    slope_threshold_pct: float = 0.5) -> dict:
    """
    Определяет режим рынка по свечам BTC.

    Args:
        btc_candles: np.ndarray OHLCV свечей BTC (желательно 1h)
        sma_fast: период быстрой SMA (по умолчанию 20)
        sma_slow: период медленной SMA (по умолчанию 50)
        slope_lookback: за сколько свечей считать наклон SMA_fast (10)
        slope_threshold_pct: минимальный наклон в % для тренда (0.5)

    Returns:
        {
          "regime": "bull" | "bear" | "range",
          "reason": str,
          "price": float,
          "sma_fast": float,
          "sma_slow": float,
          "slope_pct": float,
        }
    """
    # Пустые данные → безопасно возвращаем RANGE
    if btc_candles is None or len(btc_candles) == 0:
        return {
            "regime": "range",
            "reason": "no BTC data",
            "price": 0.0,
            "sma_fast": 0.0,
            "sma_slow": 0.0,
            "slope_pct": 0.0,
        }

    # Недостаточно данных для корректного расчёта → RANGE
    min_required = max(sma_slow, sma_fast + slope_lookback)
    if len(btc_candles) < min_required:
        return {
            "regime": "range",
            "reason": f"insufficient BTC data ({len(btc_candles)} < {min_required})",
            "price": float(btc_candles[-1, 4]),
            "sma_fast": 0.0,
            "sma_slow": 0.0,
            "slope_pct": 0.0,
        }

    closes = btc_candles[:, 4].astype(float)
    price = float(closes[-1])

    fast_now = _sma(closes, sma_fast)
    slow_now = _sma(closes, sma_slow)

    # Наклон SMA_fast: сравниваем текущее значение с тем что было slope_lookback свечей назад
    fast_past = _sma(closes[:-slope_lookback], sma_fast)
    if fast_past <= 0:
        slope_pct = 0.0
    else:
        slope_pct = (fast_now - fast_past) / fast_past * 100

    # Три условия для тренда
    price_above_slow = price > slow_now
    fast_above_slow = fast_now > slow_now
    trending_up = slope_pct > slope_threshold_pct
    trending_down = slope_pct < -slope_threshold_pct

    if price_above_slow and fast_above_slow and trending_up:
        regime = "bull"
        reason = (f"BULL: price > SMA{sma_slow}, SMA{sma_fast} > SMA{sma_slow}, "
                  f"slope +{slope_pct:.2f}%")
    elif (not price_above_slow) and (not fast_above_slow) and trending_down:
        regime = "bear"
        reason = (f"BEAR: price < SMA{sma_slow}, SMA{sma_fast} < SMA{sma_slow}, "
                  f"slope {slope_pct:.2f}%")
    else:
        regime = "range"
        reason = (f"RANGE: price_above={price_above_slow}, "
                  f"fast_above={fast_above_slow}, slope={slope_pct:.2f}%")

    return {
        "regime": regime,
        "reason": reason,
        "price": price,
        "sma_fast": round(fast_now, 2),
        "sma_slow": round(slow_now, 2),
        "slope_pct": round(slope_pct, 3),
    }


def is_direction_allowed(regime: str, direction: str,
                           block_counter_trend: bool = True) -> bool:
    """
    Проверяет, разрешена ли сделка в данном направлении при данном режиме.

    Args:
        regime: "bull" | "bear" | "range"
        direction: "long" | "short"
        block_counter_trend: если True — блокирует против-трендовые сделки

    Returns:
        True  — сделка разрешена
        False — заблокирована (режим против направления)
    """
    if not block_counter_trend:
        return True

    if regime == "bull" and direction == "short":
        return False
    if regime == "bear" and direction == "long":
        return False

    # RANGE — оба направления разрешены
    return True
