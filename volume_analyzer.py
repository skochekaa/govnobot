# volume_analyzer.py — Анализ объёма
# ====================================
#
# Объём — это "топливо" для движения цены.
# Без объёма пробой = ловушка, а отскок = слабый.
#
# Этот модуль отвечает на 3 вопроса:
# 1. Есть ли всплеск объёма? (кто-то крупный зашёл)
# 2. Объём подтверждает движение цены? (тренд реальный)
# 3. Это ложный пробой? (цена пробила уровень, но без силы)

import numpy as np
import config


def detect_volume_spike(candles: np.ndarray) -> dict:
    """
    Определяет, есть ли всплеск объёма на последней свече.
    
    Всплеск = объём текущей свечи значительно выше среднего.
    
    Зачем:
    - Всплеск на уровне → подтверждение (кто-то защищает уровень)
    - Всплеск при пробое → пробой настоящий
    - Нет всплеска при пробое → возможно ловушка
    
    Возвращает:
    {
        "is_spike": True/False,
        "current_volume": 5000000,
        "avg_volume": 2000000,
        "ratio": 2.5,           # во сколько раз больше среднего
    }
    """
    period = config.VOLUME_AVG_PERIOD
    
    if len(candles) < period + 1:
        return {"is_spike": False, "current_volume": 0,
                "avg_volume": 0, "ratio": 0}
    
    volumes = candles[:, 5]  # столбец 5 = volume
    
    current_vol = float(volumes[-1])
    # Среднее за предыдущие N свечей (не включая текущую)
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
    Проверяет, подтверждает ли объём текущее движение цены.
    
    Правила:
    - Цена растёт + объём растёт = БЫЧЬЕ подтверждение ✓
    - Цена растёт + объём падает = СЛАБОСТЬ (возможен разворот) ✗
    - Цена падает + объём растёт = МЕДВЕЖЬЕ подтверждение ✓
    - Цена падает + объём падает = ИСТОЩЕНИЕ (возможен отскок) ✗
    
    lookback: сколько последних свечей анализировать.
    
    Возвращает:
    {
        "price_direction": "up" / "down" / "flat",
        "volume_direction": "up" / "down" / "flat",
        "confirmed": True/False,
        "description": "bullish_confirmed" / "weakness" / ...
    }
    """
    if len(candles) < lookback + 1:
        return {"price_direction": "flat", "volume_direction": "flat",
                "confirmed": False, "description": "insufficient_data"}
    
    recent = candles[-lookback:]
    
    # Направление цены: сравниваем первый и последний close
    price_change = float(recent[-1, 4] - recent[0, 4])
    avg_price = float(np.mean(recent[:, 4]))
    price_change_pct = (price_change / avg_price) * 100
    
    # Направление объёма: линейная регрессия
    volumes = recent[:, 5]
    x = np.arange(len(volumes))
    # Наклон линии тренда объёма
    vol_slope = np.polyfit(x, volumes, 1)[0]
    avg_vol = float(np.mean(volumes))
    vol_slope_pct = (vol_slope / avg_vol) * 100 if avg_vol > 0 else 0
    
    # Определяем направления
    threshold = 0.1  # % — порог для "flat"
    
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
    
    # Определяем состояние
    if price_dir == "up" and vol_dir == "up":
        desc = "bullish_confirmed"
        confirmed = True
    elif price_dir == "up" and vol_dir == "down":
        desc = "weakness"
        confirmed = False
    elif price_dir == "down" and vol_dir == "up":
        desc = "bearish_confirmed"
        confirmed = True
    elif price_dir == "down" and vol_dir == "down":
        desc = "exhaustion"
        confirmed = False
    else:
        desc = "neutral"
        confirmed = False
    
    return {
        "price_direction": price_dir,
        "volume_direction": vol_dir,
        "confirmed": confirmed,
        "description": desc,
    }


def detect_fake_breakout(candles: np.ndarray, level_price: float,
                          atr: float) -> dict:
    """
    Определяет, был ли ложный пробой уровня.
    
    Ложный пробой = цена прошла за уровень, но вернулась обратно.
    Это ловушка — трейдеры заходят на пробое, а цена разворачивается.
    
    Признаки ложного пробоя:
    1. Свеча пробила уровень (high выше или low ниже)
    2. Закрылась обратно (close вернулся за уровень)
    3. Объём не подтвердил (нет всплеска)
    
    Аргументы:
        candles: последние свечи
        level_price: цена уровня
        atr: текущий ATR (для определения значимости пробоя)
    
    Возвращает:
    {
        "is_fake": True/False,
        "direction": "above" / "below" / None,
        "wick_size": 0.5,  # размер тени за уровнем (в ATR)
    }
    """
    if len(candles) < 3:
        return {"is_fake": False, "direction": None, "wick_size": 0}
    
    last = candles[-1]  # последняя свеча
    prev = candles[-2]  # предпоследняя
    
    high = float(last[2])
    low = float(last[3])
    close = float(last[4])
    open_price = float(last[1])
    volume_spike = detect_volume_spike(candles)
    
    # Пробой вверх: high выше уровня, но close ниже
    if high > level_price and close < level_price:
        wick = (high - level_price) / atr if atr > 0 else 0
        return {
            "is_fake": not volume_spike["is_spike"],
            "direction": "above",
            "wick_size": round(wick, 2),
        }
    
    # Пробой вниз: low ниже уровня, но close выше
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
    ГЛАВНАЯ ФУНКЦИЯ модуля.
    
    Собирает полную картину по объёму:
    - Всплеск
    - Подтверждение тренда
    - Ложные пробои ближайших уровней
    - Дельта buy/sell (если есть данные trades)
    
    Возвращает:
    {
        "spike": {...},
        "trend_confirmation": {...},
        "fake_breakouts": [...],
        "delta": {...},
        "overall_strength": "strong" / "normal" / "weak",
    }
    """
    spike = detect_volume_spike(candles)
    trend = check_trend_confirmation(candles)
    
    # Проверяем ложные пробои ближайших уровней
    atr = levels.get("atr", 0)
    fake_breakouts = []
    
    for lvl in levels.get("all_levels", [])[:5]:  # топ-5 ближайших
        fb = detect_fake_breakout(candles, lvl["price"], atr)
        if fb["is_fake"]:
            fake_breakouts.append({
                "level": lvl["price"],
                **fb,
            })
    
    # Оценка общей силы объёма
    if spike["is_spike"] and trend["confirmed"]:
        strength = "strong"
    elif spike["is_spike"] or trend["confirmed"]:
        strength = "normal"
    else:
        strength = "weak"
    
    return {
        "spike": spike,
        "trend_confirmation": trend,
        "fake_breakouts": fake_breakouts,
        "delta": trade_delta or {},
        "overall_strength": strength,
    }
