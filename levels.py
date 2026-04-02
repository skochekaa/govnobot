# levels.py — Определение уровней поддержки и сопротивления
# =========================================================
# Мультитаймфрейм: уровни с 1h (вес x3), 15m (x2), 5m (x1).
# Близкие уровни с разных ТФ объединяются, их сила складывается.

import numpy as np
import config
from log_setup import setup_logger

log = setup_logger("levels")


def calculate_atr(candles: np.ndarray, period: int = None) -> float:
    if period is None:
        period = config.ATR_PERIOD
    if len(candles) < period + 1:
        return float(np.mean(candles[:, 2] - candles[:, 3]))

    highs = candles[:, 2]
    lows = candles[:, 3]
    closes = candles[:, 4]

    true_ranges = []
    for i in range(1, len(candles)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    return float(np.mean(true_ranges[-period:]))


def find_local_extremes(candles: np.ndarray, window: int = None) -> dict:
    if window is None:
        window = config.LEVEL_WINDOW

    highs = candles[:, 2]
    lows = candles[:, 3]

    local_highs = []
    local_lows = []

    for i in range(window, len(candles) - window):
        window_highs = highs[i - window:i + window + 1]
        if highs[i] == np.max(window_highs):
            local_highs.append(float(highs[i]))

        window_lows = lows[i - window:i + window + 1]
        if lows[i] == np.min(window_lows):
            local_lows.append(float(lows[i]))

    return {"highs": local_highs, "lows": local_lows}


def cluster_levels(prices: list[float], atr: float,
                    mult: float = None) -> list[dict]:
    if mult is None:
        mult = config.CLUSTER_ATR_MULT
    if not prices:
        return []

    threshold = atr * mult
    sorted_prices = sorted(prices)

    clusters = []
    current_cluster = [sorted_prices[0]]

    for i in range(1, len(sorted_prices)):
        if sorted_prices[i] - sorted_prices[i - 1] <= threshold:
            current_cluster.append(sorted_prices[i])
        else:
            clusters.append(current_cluster)
            current_cluster = [sorted_prices[i]]
    clusters.append(current_cluster)

    levels = []
    for cluster in clusters:
        levels.append({
            "price": round(float(np.mean(cluster)), 2),
            "strength": len(cluster),
        })
    return levels


def detect_levels(candles: np.ndarray) -> dict:
    """Находит уровни на одном таймфрейме."""
    if len(candles) < config.LEVEL_WINDOW * 2 + 1:
        return {"supports": [], "resistances": [], "all_levels": [], "atr": 0}

    atr = calculate_atr(candles)
    extremes = find_local_extremes(candles)

    resistance_levels = cluster_levels(extremes["highs"], atr)
    support_levels = cluster_levels(extremes["lows"], atr)

    resistance_levels = [l for l in resistance_levels if l["strength"] >= config.MIN_TOUCHES]
    support_levels = [l for l in support_levels if l["strength"] >= config.MIN_TOUCHES]

    for lvl in resistance_levels:
        lvl["type"] = "resistance"
    for lvl in support_levels:
        lvl["type"] = "support"

    current_price = float(candles[-1, 4])
    all_levels = resistance_levels + support_levels

    for lvl in all_levels:
        if lvl["price"] > current_price:
            lvl["type"] = "resistance"
        else:
            lvl["type"] = "support"

    all_levels.sort(key=lambda x: abs(x["price"] - current_price))

    return {
        "supports": [l for l in all_levels if l["type"] == "support"],
        "resistances": [l for l in all_levels if l["type"] == "resistance"],
        "all_levels": all_levels,
        "atr": atr,
    }


# ── Мультитаймфрейм: объединение уровней ────

def detect_levels_mtf(candles_by_tf: dict[str, np.ndarray]) -> dict:
    """
    ГЛАВНАЯ ФУНКЦИЯ — мультитаймфреймовые уровни.

    Логика:
    1. Находим уровни на каждом ТФ отдельно (1h, 15m, 5m)
    2. Умножаем силу на вес ТФ (1h x3, 15m x2, 5m x1)
    3. Объединяем близкие уровни с разных ТФ
    4. Итог: один список уровней, где часовые самые сильные

    Пример:
      1h: уровень 0.00850 (strength=2) → после веса = 6
      15m: уровень 0.00848 (strength=3) → после веса = 6
      Они близко → объединяются: 0.00849, strength=12
      Это ОЧЕНЬ сильный уровень (видно на двух ТФ).

    Аргументы:
        candles_by_tf: {"1h": np.array, "15m": np.array, "5m": np.array}

    Возвращает тот же формат что detect_levels(), но с MTF-силой.
    """
    all_weighted_levels = []
    atr_values = {}

    for tf, candles in candles_by_tf.items():
        if len(candles) < config.LEVEL_WINDOW * 2 + 1:
            continue

        weight = config.TF_LEVEL_WEIGHTS.get(tf, 1)
        levels = detect_levels(candles)
        atr_values[tf] = levels["atr"]

        for lvl in levels["all_levels"]:
            all_weighted_levels.append({
                "price": lvl["price"],
                "strength": lvl["strength"] * weight,  # умножаем на вес ТФ
                "type": lvl["type"],
                "source_tf": tf,
            })

        log.debug("%s: найдено %d уровней (вес x%d)", tf, len(levels["all_levels"]), weight)

    if not all_weighted_levels:
        return {"supports": [], "resistances": [], "all_levels": [], "atr": 0}

    # ATR берём с рабочего ТФ (5m) — он нужен для расстояний входа/стопа
    work_atr = atr_values.get(config.TF_WORK, 0)
    # Для кластеризации берём ATR с 15m (более стабильный)
    cluster_atr = atr_values.get(config.TF_MIDDLE, work_atr)

    if cluster_atr == 0:
        cluster_atr = work_atr
    if work_atr == 0 and cluster_atr > 0:
        work_atr = cluster_atr

    # Объединяем близкие уровни с разных ТФ
    merged = _merge_weighted_levels(all_weighted_levels, cluster_atr)

    # Определяем текущую цену с рабочего ТФ
    work_candles = candles_by_tf.get(config.TF_WORK)
    if work_candles is not None and len(work_candles) > 0:
        current_price = float(work_candles[-1, 4])
    else:
        current_price = merged[0]["price"] if merged else 0

    # Назначаем тип по положению относительно цены
    for lvl in merged:
        lvl["type"] = "resistance" if lvl["price"] > current_price else "support"

    # Сортируем: ближайшие к цене первые
    merged.sort(key=lambda x: abs(x["price"] - current_price))

    supports = [l for l in merged if l["type"] == "support"]
    resistances = [l for l in merged if l["type"] == "resistance"]

    log.info("MTF уровни: %d support, %d resistance (ATR work=%.4f)",
             len(supports), len(resistances), work_atr)

    return {
        "supports": supports,
        "resistances": resistances,
        "all_levels": merged,
        "atr": work_atr,
    }


def _merge_weighted_levels(levels: list[dict], atr: float) -> list[dict]:
    """
    Объединяет близкие уровни с разных таймфреймов.

    Если уровень с 1h (42000) и уровень с 15m (42010) ближе
    чем ATR*0.5 — они сливаются в один, силы складываются.
    Это значит: уровень виден на нескольких ТФ = он реально сильный.
    """
    if not levels:
        return []

    threshold = atr * config.CLUSTER_ATR_MULT
    sorted_levels = sorted(levels, key=lambda x: x["price"])

    merged = []
    current_group = [sorted_levels[0]]

    for i in range(1, len(sorted_levels)):
        avg_price = np.mean([l["price"] for l in current_group])
        if sorted_levels[i]["price"] - avg_price <= threshold:
            current_group.append(sorted_levels[i])
        else:
            merged.append(_combine_group(current_group))
            current_group = [sorted_levels[i]]

    merged.append(_combine_group(current_group))
    return merged


def _combine_group(group: list[dict]) -> dict:
    """Объединяет группу близких уровней в один."""
    avg_price = round(float(np.mean([l["price"] for l in group])), 2)
    total_strength = sum(l["strength"] for l in group)
    source_tfs = list(set(l.get("source_tf", "?") for l in group))

    return {
        "price": avg_price,
        "strength": total_strength,
        "type": group[0]["type"],
        "source_tfs": source_tfs,  # на каких ТФ виден
        "multi_tf": len(source_tfs) > 1,  # виден на нескольких ТФ?
    }


def detect_consolidation(candles: np.ndarray, range_pct: float = 1.5,
                          min_candles: int = 20) -> list[dict]:
    if len(candles) < min_candles:
        return []

    ranges = []
    i = 0
    while i < len(candles) - min_candles:
        window = candles[i:i + min_candles]
        range_high = float(np.max(window[:, 2]))
        range_low = float(np.min(window[:, 3]))
        mid_price = (range_high + range_low) / 2
        range_size = (range_high - range_low) / mid_price * 100

        if range_size < range_pct:
            end = i + min_candles
            while end < len(candles):
                ext_high = float(np.max(candles[i:end + 1, 2]))
                ext_low = float(np.min(candles[i:end + 1, 3]))
                ext_mid = (ext_high + ext_low) / 2
                if (ext_high - ext_low) / ext_mid * 100 < range_pct:
                    end += 1
                else:
                    break
            ranges.append({
                "low": round(range_low, 2), "high": round(range_high, 2),
                "start_idx": i, "length": end - i,
            })
            i = end
        else:
            i += 1

    return ranges
