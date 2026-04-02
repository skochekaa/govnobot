# levels.py — Определение уровней поддержки и сопротивления
# =========================================================
#
# Что делает этот модуль:
# 1. Находит локальные максимумы (high) и минимумы (low)
# 2. Группирует близкие уровни в кластеры (через ATR)
# 3. Оценивает силу уровня (сколько раз тестировался)
# 4. Определяет зоны консолидации (боковик)
# 5. Находит уровни пробоя
#
# Почему ATR, а не фиксированный %:
#   BTC двигается на $500 за свечу, а DOGE на $0.001.
#   ATR автоматически подстраивается под волатильность монеты.

import numpy as np
import config


def calculate_atr(candles: np.ndarray, period: int = None) -> float:
    """
    ATR (Average True Range) — средний размах свечи.
    
    Показывает, на сколько в среднем двигается цена за одну свечу.
    Используем для адаптивного определения "расстояния" между уровнями.
    
    Формула True Range для каждой свечи:
      TR = max(high - low, |high - prev_close|, |low - prev_close|)
    
    ATR = среднее TR за N свечей.
    """
    if period is None:
        period = config.ATR_PERIOD
    
    if len(candles) < period + 1:
        # Недостаточно данных — возвращаем простой high-low
        return float(np.mean(candles[:, 2] - candles[:, 3]))
    
    highs = candles[:, 2]    # столбец 2 = high
    lows = candles[:, 3]     # столбец 3 = low
    closes = candles[:, 4]   # столбец 4 = close
    
    true_ranges = []
    for i in range(1, len(candles)):
        tr = max(
            highs[i] - lows[i],                 # размах текущей свечи
            abs(highs[i] - closes[i - 1]),       # гэп вверх
            abs(lows[i] - closes[i - 1]),        # гэп вниз
        )
        true_ranges.append(tr)
    
    # Берём среднее за последние `period` свечей
    return float(np.mean(true_ranges[-period:]))


def find_local_extremes(candles: np.ndarray,
                         window: int = None) -> dict:
    """
    Находит локальные максимумы и минимумы.
    
    Логика:
      Точка считается локальным максимумом, если её high
      выше всех high в окне ±window свечей.
    
    Пример (window=3):
      Свечи: 10, 12, 15, 13, 11  →  15 это локальный хай
      (15 > 10, 12, 13, 11 в окне 3 свечи вокруг)
    
    Аргументы:
        candles: OHLCV массив
        window: размер окна (из config если не указан)
    
    Возвращает:
        {"highs": [42500, 42800, ...], "lows": [41200, 41500, ...]}
    """
    if window is None:
        window = config.LEVEL_WINDOW
    
    highs = candles[:, 2]   # high
    lows = candles[:, 3]    # low
    
    local_highs = []
    local_lows = []
    
    for i in range(window, len(candles) - window):
        # Проверяем: текущий high выше всех в окне?
        window_highs = highs[i - window:i + window + 1]
        if highs[i] == np.max(window_highs):
            local_highs.append(float(highs[i]))
        
        # Проверяем: текущий low ниже всех в окне?
        window_lows = lows[i - window:i + window + 1]
        if lows[i] == np.min(window_lows):
            local_lows.append(float(lows[i]))
    
    return {"highs": local_highs, "lows": local_lows}


def cluster_levels(prices: list[float], atr: float,
                    mult: float = None) -> list[dict]:
    """
    Группирует близкие уровни в кластеры.
    
    Зачем: если цена отбивалась от 42000, 42010, 41990 —
    это один и тот же уровень, а не три разных.
    
    Алгоритм:
    1. Сортируем цены
    2. Если следующая цена ближе чем ATR * mult — в тот же кластер
    3. Уровень кластера = среднее цен внутри
    4. Сила = сколько цен попало в кластер (= сколько раз тестировался)
    
    Возвращает:
    [
        {"price": 42000.0, "strength": 3},
        {"price": 41500.0, "strength": 2},
    ]
    """
    if mult is None:
        mult = config.CLUSTER_ATR_MULT
    
    if not prices:
        return []
    
    threshold = atr * mult  # максимальное расстояние внутри кластера
    sorted_prices = sorted(prices)
    
    clusters = []
    current_cluster = [sorted_prices[0]]
    
    for i in range(1, len(sorted_prices)):
        if sorted_prices[i] - sorted_prices[i - 1] <= threshold:
            current_cluster.append(sorted_prices[i])
        else:
            clusters.append(current_cluster)
            current_cluster = [sorted_prices[i]]
    
    clusters.append(current_cluster)  # не забыть последний
    
    # Преобразуем кластеры в уровни
    levels = []
    for cluster in clusters:
        levels.append({
            "price": round(float(np.mean(cluster)), 2),
            "strength": len(cluster),  # чем больше точек — тем сильнее
        })
    
    return levels


def detect_levels(candles: np.ndarray) -> dict:
    """
    ГЛАВНАЯ ФУНКЦИЯ модуля.
    
    Находит все уровни для переданных свечей:
    - Поддержки (support): уровни, от которых цена отскакивала вверх
    - Сопротивления (resistance): уровни, от которых цена отскакивала вниз
    
    Возвращает:
    {
        "supports": [{"price": 41500, "strength": 3}, ...],
        "resistances": [{"price": 42500, "strength": 2}, ...],
        "all_levels": [...],  # все уровни вместе (для сигналов)
        "atr": 150.0,         # текущий ATR (нужен другим модулям)
    }
    """
    if len(candles) < config.LEVEL_WINDOW * 2 + 1:
        return {
            "supports": [], "resistances": [],
            "all_levels": [], "atr": 0
        }
    
    atr = calculate_atr(candles)
    extremes = find_local_extremes(candles)
    
    # Кластеризуем отдельно хаи и лои
    resistance_levels = cluster_levels(extremes["highs"], atr)
    support_levels = cluster_levels(extremes["lows"], atr)
    
    # Фильтруем слабые уровни (меньше MIN_TOUCHES касаний)
    resistance_levels = [
        lvl for lvl in resistance_levels
        if lvl["strength"] >= config.MIN_TOUCHES
    ]
    support_levels = [
        lvl for lvl in support_levels
        if lvl["strength"] >= config.MIN_TOUCHES
    ]
    
    # Добавляем тип
    for lvl in resistance_levels:
        lvl["type"] = "resistance"
    for lvl in support_levels:
        lvl["type"] = "support"
    
    current_price = float(candles[-1, 4])  # последний close
    
    # Разделяем: всё что выше текущей цены = сопротивление,
    # всё что ниже = поддержка
    all_levels = resistance_levels + support_levels
    
    # Корректируем тип по положению относительно цены
    for lvl in all_levels:
        if lvl["price"] > current_price:
            lvl["type"] = "resistance"
        else:
            lvl["type"] = "support"
    
    # Сортируем: ближайшие к цене — первые
    all_levels.sort(key=lambda x: abs(x["price"] - current_price))
    
    supports = [l for l in all_levels if l["type"] == "support"]
    resistances = [l for l in all_levels if l["type"] == "resistance"]
    
    return {
        "supports": supports,
        "resistances": resistances,
        "all_levels": all_levels,
        "atr": atr,
    }


def detect_consolidation(candles: np.ndarray,
                          range_pct: float = 1.5,
                          min_candles: int = 20) -> list[dict]:
    """
    Находит зоны консолидации (боковик / рейндж).
    
    Консолидация = цена ходит в узком диапазоне.
    
    Логика:
    1. Берём скользящее окно из min_candles свечей
    2. Считаем диапазон (max high - min low)
    3. Если диапазон < range_pct% от средней цены → это консолидация
    
    Аргументы:
        candles: OHLCV
        range_pct: максимальный размер диапазона в %
        min_candles: минимальная длина консолидации
    
    Возвращает:
    [
        {"low": 41000, "high": 41500, "start_idx": 50, "length": 30},
    ]
    """
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
            # Нашли начало консолидации — расширяем вправо
            end = i + min_candles
            while end < len(candles):
                ext_high = float(np.max(candles[i:end + 1, 2]))
                ext_low = float(np.min(candles[i:end + 1, 3]))
                ext_mid = (ext_high + ext_low) / 2
                ext_range = (ext_high - ext_low) / ext_mid * 100
                
                if ext_range < range_pct:
                    end += 1
                else:
                    break
            
            ranges.append({
                "low": round(range_low, 2),
                "high": round(range_high, 2),
                "start_idx": i,
                "length": end - i,
            })
            
            i = end  # прыгаем за конец найденной зоны
        else:
            i += 1
    
    return ranges
