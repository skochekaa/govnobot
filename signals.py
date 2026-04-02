# signals.py — Генерация торговых сигналов
# ==========================================
#
# Этот модуль принимает уровни + объём и решает:
# 1. Есть ли сигнал на вход? (bounce / breakout)
# 2. Где входить, где стоп, где тейк?
# 3. Проходит ли сигнал фильтры? (RR, объём, BTC)
#
# Два типа сигналов:
#   BOUNCE (отскок) — цена дошла до уровня и отскочила
#   BREAKOUT (пробой) — цена пробила уровень и закрепилась

import numpy as np
import config
from levels import calculate_atr


# ── Детекция отскока ─────────────────────────

def detect_bounce(candles: np.ndarray, levels: dict,
                   volume: dict) -> list[dict]:
    """
    Ищет сигналы отскока от уровня.
    
    Условия для BOUNCE (все должны быть True):
    1. Цена рядом с уровнем (в пределах ATR * NEAR_LEVEL_ATR_MULT)
    2. Есть реакция — свеча показала отторжение:
       - Длинная тень (wick) в сторону уровня
       - Или текущая свеча развернулась от уровня
    3. Нет подтверждённого пробоя
    4. Объём НЕ подтверждает движение к уровню (= нет силы на пробой)
    
    Возвращает список сигналов:
    [
        {
            "type": "bounce",
            "direction": "long",      # long = покупка, short = продажа
            "level": 42000,
            "entry": 42050,
            "stop": 41850,
            "take": 42500,
            "risk_reward": 2.25,
            "strength": "strong",
            "reason": "support bounce with rejection wick",
        }
    ]
    """
    signals = []
    
    if len(candles) < 5:
        return signals
    
    atr = levels.get("atr", 0)
    if atr == 0:
        return signals
    
    current = candles[-1]
    close = float(current[4])
    high = float(current[2])
    low = float(current[3])
    open_price = float(current[1])
    
    near_threshold = atr * config.NEAR_LEVEL_ATR_MULT
    
    for lvl in levels.get("all_levels", []):
        level_price = lvl["price"]
        distance = abs(close - level_price)
        
        # 1. Цена рядом с уровнем?
        if distance > near_threshold:
            continue
        
        # 2. Определяем направление
        if lvl["type"] == "support" and close > level_price:
            direction = "long"  # отскок вверх от поддержки
        elif lvl["type"] == "resistance" and close < level_price:
            direction = "short"  # отскок вниз от сопротивления
        else:
            continue
        
        # 3. Проверяем реакцию (rejection)
        rejection = _check_rejection(candles, level_price, direction, atr)
        if not rejection["detected"]:
            continue
        
        # 4. Нет ложного пробоя с сильным объёмом
        # (если объём сильный в сторону пробоя — это не отскок)
        vol_spike = volume.get("spike", {}).get("is_spike", False)
        trend_dir = volume.get("trend_confirmation", {}).get("price_direction")
        
        # Если объём подтверждает движение ЧЕРЕЗ уровень — пропускаем
        if vol_spike and (
            (direction == "long" and trend_dir == "down") or
            (direction == "short" and trend_dir == "up")
        ):
            continue
        
        # 5. Рассчитываем вход, стоп, тейк
        trade = _calculate_bounce_trade(
            direction, level_price, levels, atr
        )
        
        if trade is None:
            continue
        
        # 6. Проверяем Risk/Reward
        if trade["risk_reward"] < config.MIN_RISK_REWARD:
            continue
        
        # Оценка силы сигнала
        strength = _evaluate_signal_strength(lvl, rejection, volume)
        
        signals.append({
            "type": "bounce",
            "direction": direction,
            "level": level_price,
            "entry": trade["entry"],
            "stop": trade["stop"],
            "take": trade["take"],
            "risk_reward": trade["risk_reward"],
            "strength": strength,
            "reason": rejection["reason"],
        })
    
    return signals


def _check_rejection(candles: np.ndarray, level_price: float,
                      direction: str, atr: float) -> dict:
    """
    Проверяет, есть ли "реакция" от уровня.
    
    Реакция — это видимый разворот цены у уровня:
    
    Для LONG (отскок вверх от поддержки):
    - Длинная нижняя тень (свеча "пощупала" уровень и ушла вверх)
    - Цена закрылась выше чем открылась (бычья свеча)
    - Замедление падения (свечи становятся меньше)
    
    Для SHORT (отскок вниз от сопротивления):
    - Длинная верхняя тень
    - Цена закрылась ниже чем открылась (медвежья свеча)
    """
    last = candles[-1]
    open_p = float(last[1])
    high = float(last[2])
    low = float(last[3])
    close = float(last[4])
    
    body_size = abs(close - open_p)
    candle_range = high - low
    
    if candle_range == 0:
        return {"detected": False, "reason": ""}
    
    if direction == "long":
        # Нижняя тень = low до min(open, close)
        lower_wick = min(open_p, close) - low
        wick_ratio = lower_wick / candle_range
        
        # Бычья свеча (close > open) у поддержки
        is_bullish = close > open_p
        
        # Тень должна быть > 50% свечи (пин-бар)
        # ИЛИ бычья свеча у уровня
        if wick_ratio > 0.5:
            return {
                "detected": True,
                "reason": f"rejection wick at support ({wick_ratio:.0%})"
            }
        elif is_bullish and lower_wick > atr * 0.3:
            return {
                "detected": True,
                "reason": "bullish candle at support with wick"
            }
        
        # Проверяем замедление: последние 3 свечи уменьшаются
        if len(candles) >= 3:
            ranges = [float(c[2] - c[3]) for c in candles[-3:]]
            if ranges[-1] < ranges[-2] < ranges[-3]:
                return {
                    "detected": True,
                    "reason": "momentum slowdown at support"
                }
    
    elif direction == "short":
        upper_wick = high - max(open_p, close)
        wick_ratio = upper_wick / candle_range
        
        is_bearish = close < open_p
        
        if wick_ratio > 0.5:
            return {
                "detected": True,
                "reason": f"rejection wick at resistance ({wick_ratio:.0%})"
            }
        elif is_bearish and upper_wick > atr * 0.3:
            return {
                "detected": True,
                "reason": "bearish candle at resistance with wick"
            }
        
        if len(candles) >= 3:
            ranges = [float(c[2] - c[3]) for c in candles[-3:]]
            if ranges[-1] < ranges[-2] < ranges[-3]:
                return {
                    "detected": True,
                    "reason": "momentum slowdown at resistance"
                }
    
    return {"detected": False, "reason": ""}


def _calculate_bounce_trade(direction: str, level_price: float,
                             levels: dict, atr: float) -> dict | None:
    """
    Рассчитывает параметры сделки для отскока.
    
    LONG от поддержки:
        entry = чуть выше уровня (уровень + offset)
        stop  = ниже уровня (уровень - 1 ATR)
        take  = ближайшее сопротивление (или +2 ATR если нет)
    
    SHORT от сопротивления:
        entry = чуть ниже уровня
        stop  = выше уровня
        take  = ближайшая поддержка
    """
    offset = atr * 0.2  # небольшой отступ от уровня
    stop_distance = atr * 1.0  # стоп за 1 ATR от уровня
    
    if direction == "long":
        entry = level_price + offset
        stop = level_price - stop_distance
        
        # Ищем ближайшее сопротивление для тейка
        resistances = [
            r["price"] for r in levels.get("resistances", [])
            if r["price"] > entry + atr
        ]
        take = resistances[0] if resistances else entry + atr * 3
        
    elif direction == "short":
        entry = level_price - offset
        stop = level_price + stop_distance
        
        supports = [
            s["price"] for s in levels.get("supports", [])
            if s["price"] < entry - atr
        ]
        take = supports[0] if supports else entry - atr * 3
    else:
        return None
    
    # Risk/Reward
    risk = abs(entry - stop)
    reward = abs(take - entry)
    
    if risk == 0:
        return None
    
    rr = round(reward / risk, 2)
    
    return {
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "take": round(take, 2),
        "risk_reward": rr,
    }


# ── Детекция пробоя ─────────────────────────

def detect_breakout(candles: np.ndarray, levels: dict,
                     volume: dict) -> list[dict]:
    """
    Ищет сигналы пробоя уровня.
    
    Условия для BREAKOUT (все должны быть True):
    1. Цена закрылась за уровнем (close выше сопротивления
       или ниже поддержки)
    2. Есть всплеск объёма (подтверждение силы пробоя)
    3. Нет мгновенного возврата (цена держится за уровнем)
    4. Нет признаков ложного пробоя
    """
    signals = []
    
    if len(candles) < config.BREAKOUT_CONFIRM_CANDLES + 2:
        return signals
    
    atr = levels.get("atr", 0)
    if atr == 0:
        return signals
    
    # Берём несколько последних свечей для проверки закрепления
    recent_closes = [float(c[4]) for c in candles[-config.BREAKOUT_CONFIRM_CANDLES:]]
    
    vol_spike = volume.get("spike", {}).get("is_spike", False)
    fake_breakouts = volume.get("fake_breakouts", [])
    fake_levels = {fb["level"] for fb in fake_breakouts}
    
    for lvl in levels.get("all_levels", []):
        level_price = lvl["price"]
        
        # Пропускаем уровни с ложным пробоем
        if level_price in fake_levels:
            continue
        
        # Пробой сопротивления вверх
        if lvl["type"] == "resistance":
            # Все последние свечи закрылись выше уровня?
            all_above = all(c > level_price for c in recent_closes)
            
            if all_above and vol_spike:
                trade = _calculate_breakout_trade(
                    "long", level_price, levels, atr
                )
                if trade and trade["risk_reward"] >= config.MIN_RISK_REWARD:
                    signals.append({
                        "type": "breakout",
                        "direction": "long",
                        "level": level_price,
                        **trade,
                        "strength": _evaluate_signal_strength(
                            lvl, {"detected": True, "reason": ""}, volume
                        ),
                        "reason": f"breakout above {level_price} confirmed",
                    })
        
        # Пробой поддержки вниз
        elif lvl["type"] == "support":
            all_below = all(c < level_price for c in recent_closes)
            
            if all_below and vol_spike:
                trade = _calculate_breakout_trade(
                    "short", level_price, levels, atr
                )
                if trade and trade["risk_reward"] >= config.MIN_RISK_REWARD:
                    signals.append({
                        "type": "breakout",
                        "direction": "short",
                        "level": level_price,
                        **trade,
                        "strength": _evaluate_signal_strength(
                            lvl, {"detected": True, "reason": ""}, volume
                        ),
                        "reason": f"breakout below {level_price} confirmed",
                    })
    
    return signals


def _calculate_breakout_trade(direction: str, level_price: float,
                               levels: dict, atr: float) -> dict | None:
    """
    Рассчитывает параметры сделки для пробоя.
    
    LONG (пробой вверх):
        entry = текущая цена (или ретест уровня)
        stop  = ниже пробитого уровня (уровень - 0.5 ATR)
        take  = следующее сопротивление (или +3 ATR)
    
    SHORT (пробой вниз):
        entry = текущая цена
        stop  = выше пробитого уровня
        take  = следующая поддержка
    """
    stop_offset = atr * 0.5  # стоп ближе чем у bounce (пробой сильнее)
    
    if direction == "long":
        entry = level_price + atr * 0.1  # чуть выше уровня
        stop = level_price - stop_offset
        
        resistances = [
            r["price"] for r in levels.get("resistances", [])
            if r["price"] > entry + atr
        ]
        take = resistances[0] if resistances else entry + atr * 3
        
    elif direction == "short":
        entry = level_price - atr * 0.1
        stop = level_price + stop_offset
        
        supports = [
            s["price"] for s in levels.get("supports", [])
            if s["price"] < entry - atr
        ]
        take = supports[0] if supports else entry - atr * 3
    else:
        return None
    
    risk = abs(entry - stop)
    reward = abs(take - entry)
    
    if risk == 0:
        return None
    
    return {
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "take": round(take, 2),
        "risk_reward": round(reward / risk, 2),
    }


# ── Оценка силы сигнала ─────────────────────

def _evaluate_signal_strength(level: dict, rejection: dict,
                                volume: dict) -> str:
    """
    Оценивает силу сигнала: strong / medium / weak.
    
    Факторы:
    - Сила уровня (сколько касаний)
    - Качество реакции (rejection)
    - Подтверждение объёмом
    """
    score = 0
    
    # Сила уровня
    if level.get("strength", 0) >= 3:
        score += 2
    elif level.get("strength", 0) >= 2:
        score += 1
    
    # Реакция от уровня
    if rejection.get("detected"):
        score += 1
    
    # Объём
    vol_strength = volume.get("overall_strength", "weak")
    if vol_strength == "strong":
        score += 2
    elif vol_strength == "normal":
        score += 1
    
    if score >= 4:
        return "strong"
    elif score >= 2:
        return "medium"
    return "weak"


# ── BTC-корреляция ───────────────────────────

def check_btc_filter(btc_candles: np.ndarray,
                      signal_direction: str) -> dict:
    """
    Проверяет, не противоречит ли сигнал движению BTC.
    
    Правило:
    - Если BTC резко падает → не открываем лонги на альтах
    - Если BTC резко растёт → не открываем шорты на альтах
    
    Для BTC/USDT этот фильтр не применяется (он сам себе корреляция).
    
    Возвращает:
    {
        "allowed": True/False,
        "btc_change_pct": -1.5,
        "reason": "BTC dumping -1.5%, long blocked",
    }
    """
    if not config.BTC_CORRELATION_CHECK:
        return {"allowed": True, "btc_change_pct": 0, "reason": "filter disabled"}
    
    if len(btc_candles) < 12:  # нужно минимум 12 свечей 5m = 1 час
        return {"allowed": True, "btc_change_pct": 0, "reason": "insufficient data"}
    
    # Изменение BTC за последний час (12 свечей по 5m)
    hour_ago_close = float(btc_candles[-12, 4])
    current_close = float(btc_candles[-1, 4])
    change_pct = ((current_close - hour_ago_close) / hour_ago_close) * 100
    
    if signal_direction == "long" and change_pct < config.BTC_DUMP_THRESHOLD:
        return {
            "allowed": False,
            "btc_change_pct": round(change_pct, 2),
            "reason": f"BTC dumping {change_pct:.1f}%, long blocked",
        }
    
    if signal_direction == "short" and change_pct > config.BTC_PUMP_THRESHOLD:
        return {
            "allowed": False,
            "btc_change_pct": round(change_pct, 2),
            "reason": f"BTC pumping +{change_pct:.1f}%, short blocked",
        }
    
    return {
        "allowed": True,
        "btc_change_pct": round(change_pct, 2),
        "reason": "BTC filter passed",
    }


# ── Главная функция ─────────────────────────

def generate_signals(candles: np.ndarray, levels: dict,
                      volume: dict,
                      btc_candles: np.ndarray = None,
                      symbol: str = "") -> list[dict]:
    """
    ГЛАВНАЯ ФУНКЦИЯ — генерирует все сигналы для монеты.
    
    Собирает bounce + breakout сигналы,
    фильтрует через BTC-корреляцию,
    сортирует по силе.
    """
    all_signals = []
    
    # Ищем отскоки
    bounces = detect_bounce(candles, levels, volume)
    all_signals.extend(bounces)
    
    # Ищем пробои
    breakouts = detect_breakout(candles, levels, volume)
    all_signals.extend(breakouts)
    
    # Фильтр BTC (только для альтов)
    is_btc = "BTC/USDT" in symbol
    
    if not is_btc and btc_candles is not None and len(btc_candles) > 0:
        filtered = []
        for sig in all_signals:
            btc_check = check_btc_filter(btc_candles, sig["direction"])
            sig["btc_filter"] = btc_check
            if btc_check["allowed"]:
                filtered.append(sig)
            else:
                print(f"  [Signal] {symbol} {sig['type']} {sig['direction']} "
                      f"заблокирован: {btc_check['reason']}")
        all_signals = filtered
    
    # Добавляем символ
    for sig in all_signals:
        sig["symbol"] = symbol
    
    # Сортируем: strong > medium > weak
    strength_order = {"strong": 0, "medium": 1, "weak": 2}
    all_signals.sort(key=lambda s: strength_order.get(s["strength"], 3))
    
    return all_signals
