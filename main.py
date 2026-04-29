# main.py — Точка входа скальпинг-бота (WebSocket + MTF)
# ======================================================
#
# Архитектура:
# 1. Подключение к бирже
# 2. Сканер отбирает монеты
# 3. REST: загружаем историю свечей (один раз)
# 4. WebSocket: стримы обновляют кэш в реальном времени
# 5. Торговый цикл: читает ТОЛЬКО из кэша (0 API-запросов)
#
# Запуск: python main.py | Остановка: Ctrl+C

import asyncio
import signal
from datetime import datetime, timezone

import config
from log_setup import setup_logger
from exchange import Exchange
from levels import detect_levels_mtf
from volume_analyzer import analyze_volume
from trader import PaperTrader
from logger_mod import TradeLogger
from analytics import generate_daily_report, analyze_trade
from coin_scanner import CoinScanner

# Выбор стратегии: bounce, trend или auto (обе параллельно)
# Все функции имеют одинаковый интерфейс generate_signals_mtf(...)
if config.STRATEGY_MODE == "auto":
    from signals import generate_signals_mtf as bounce_signals_mtf
    from signals_trend import generate_signals_mtf as trend_signals_mtf
elif config.STRATEGY_MODE == "trend":
    from signals_trend import generate_signals_mtf
else:  # bounce
    from signals import generate_signals_mtf

log = setup_logger("main")
running = True

ALL_TFS = [config.TF_SENIOR, config.TF_MIDDLE, config.TF_WORK, config.TF_ENTRY]


def handle_shutdown(signum, frame):
    global running
    log.info("Получен сигнал остановки...")
    running = False


def _strength_score(strength: str) -> int:
    """Числовой score для ранжирования сигналов."""
    return {"strong": 3, "medium": 2, "weak": 1}.get(strength, 0)


def resolve_strategy_conflicts(trend_signals: list, bounce_signals: list,
                                 symbol: str) -> list:
    """
    Разрешает конфликты между сигналами двух стратегий на одном символе.

    Правила:
      - Если все сигналы в ОДНУ сторону → берём с высшей силой
      - Если обе стратегии согласны (один long от trend + один long от bounce)
        → отмечаем как "consensus" (сильный консенсус-сигнал)
      - Если разные направления → пропускаем оба (неопределённость рынка)

    Args:
        trend_signals: список сигналов от trend-стратегии для этого символа
        bounce_signals: список сигналов от bounce-стратегии для этого символа
        symbol: символ для логирования

    Returns:
        Список итоговых сигналов (0 или 1 элемент)
    """
    # Тегируем каждый сигнал
    for s in trend_signals:
        s["strategy"] = "trend"
    for s in bounce_signals:
        s["strategy"] = "bounce"

    all_sigs = trend_signals + bounce_signals
    if not all_sigs:
        return []

    directions = set(s["direction"] for s in all_sigs)
    if len(directions) > 1:
        # Конфликт направлений
        log.info("%s: конфликт стратегий (trend+bounce разные направления), пропуск", symbol)
        return []

    # Все в одну сторону — выбираем лучший
    best = max(all_sigs, key=lambda s: (
        _strength_score(s.get("strength", "weak")),
        s.get("risk_reward", 0)
    ))

    # Если обе стратегии дали сигнал в одну сторону → consensus
    if trend_signals and bounce_signals:
        best["strategy"] = "consensus"
        # Бонус к силе: если был "medium" → "strong" (две стратегии согласны)
        if best.get("strength") == "medium":
            best["strength"] = "strong"
        log.info("%s: consensus (trend + bounce согласны: %s)", symbol, best["direction"])

    return [best]


def is_in_active_session() -> bool:
    """
    Проверяет, находится ли текущий час (UTC) в одном из окон
    config.TRADING_SESSIONS. Возвращает True если фильтр отключён
    или список пуст.

    Поддерживает окна через полночь (start > end), например (22, 4).
    """
    if not config.SESSION_FILTER_ENABLED:
        return True
    sessions = config.TRADING_SESSIONS
    if not sessions:
        return True
    now_hour = datetime.now(timezone.utc).hour
    for start, end in sessions:
        if start <= end:
            if start <= now_hour < end:
                return True
        else:
            # Окно через полночь, например (22, 4)
            if now_hour >= start or now_hour < end:
                return True
    return False


async def run_bot():
    global running

    exchange = Exchange()
    trader = PaperTrader()
    logger = TradeLogger()
    scanner = CoinScanner(exchange)

    log.info("=" * 55)
    log.info("СКАЛЬПИНГ-БОТ (Paper Trading) — WebSocket + MTF")
    log.info("Стратегия: %s", config.STRATEGY_MODE.upper())
    log.info("Баланс: %s USDT | Плечо: %sx | Риск: %s%%",
             config.INITIAL_BALANCE, config.LEVERAGE,
             config.RISK_PER_TRADE * 100)
    log.info("MTF: %s→уровни | %s→тренд | %s→сигнал | %s→вход",
             config.TF_SENIOR, config.TF_MIDDLE,
             config.TF_WORK, config.TF_ENTRY)
    log.info("=" * 55)

    try:
        # 1. Подключаемся
        await exchange.connect()

        # 2. Сканер отбирает монеты
        raw_watchlist = await scanner.scan()
        watchlist = exchange.filter_valid_symbols(raw_watchlist)
        if not watchlist:
            log.error("Нет валидных символов в watchlist! Проверьте WATCHLIST_FALLBACK")
            return
        log.info("Watchlist: %s", watchlist)

        # 3. Загружаем историю (REST, один раз)
        await exchange.preload_history(watchlist, ALL_TFS)

        # 4. Запускаем WebSocket стримы (фон)
        await exchange.start_streams(watchlist, ALL_TFS)
        log.info("WebSocket стримы запущены — данные обновляются в реальном времени")

        # 5. Даём стримам пару секунд заполнить кэш
        await asyncio.sleep(3)

        # 6. Торговый цикл (читает ТОЛЬКО из кэша)
        cycle = 0
        while running:
            cycle += 1

            # Пересканировать?
            if await scanner.should_rescan():
                log.info("Пересканирование рынка...")
                raw_new = await scanner.scan()
                new_watchlist = exchange.filter_valid_symbols(raw_new)
                if new_watchlist and new_watchlist != watchlist:
                    watchlist = new_watchlist
                    log.info("Новый watchlist: %s", watchlist)
                    await exchange.restart_streams(watchlist, ALL_TFS)
                    await asyncio.sleep(3)

            try:
                await process_cycle(exchange, trader, logger, cycle, watchlist)
            except Exception as e:
                log.error("Ошибка в цикле %d: %s", cycle, e)

            await asyncio.sleep(config.UPDATE_INTERVAL)

    except KeyboardInterrupt:
        pass
    finally:
        report = generate_daily_report(
            trader.trade_history, trader.initial_balance, trader.balance
        )
        log.info("\n%s", report)
        log.info("Финальный баланс: %.2f USDT", trader.balance)
        await exchange.close()


async def process_cycle(exchange: Exchange, trader: PaperTrader,
                         logger: TradeLogger, cycle: int,
                         watchlist: list[str]):
    """
    Один цикл торговли.

    ВАЖНО: Здесь НЕТ ни одного API-запроса.
    Все данные читаются из кэша, который обновляется
    фоновыми WebSocket-стримами.
    """
    # Цены из кэша (мгновенно)
    current_prices = exchange.get_all_prices(watchlist)

    # BTC-свечи для корреляции (5m) и market regime (1h)
    btc_candles = exchange.get_candles("BTC/USDT:USDT", config.TF_WORK)
    btc_senior_candles = exchange.get_candles("BTC/USDT:USDT", config.TF_SENIOR)

    # Проверка торговой сессии (один раз на цикл)
    session_active = is_in_active_session()

    for symbol in watchlist:
        try:
            # Свечи всех ТФ из кэша (мгновенно)
            candles_by_tf = {}
            for tf in ALL_TFS:
                candles = exchange.get_candles(symbol, tf)
                if len(candles) > 0:
                    candles_by_tf[tf] = candles

            if config.TF_WORK not in candles_by_tf:
                continue
            if len(candles_by_tf[config.TF_WORK]) < 50:
                continue

            # MTF-уровни
            levels_tf_data = {
                tf: c for tf, c in candles_by_tf.items()
                if tf in config.TF_LEVEL_WEIGHTS
            }
            levels = detect_levels_mtf(levels_tf_data)

            # Объём
            delta = exchange.calculate_buy_sell_delta(symbol)
            volume = analyze_volume(candles_by_tf[config.TF_WORK], levels, delta)

            # MTF-сигналы — в auto режиме вызываем обе стратегии
            if config.STRATEGY_MODE == "auto":
                trend_sigs = trend_signals_mtf(
                    candles_by_tf, levels, volume,
                    btc_candles=btc_candles,
                    btc_senior_candles=btc_senior_candles,
                    symbol=symbol,
                )
                bounce_sigs = bounce_signals_mtf(
                    candles_by_tf, levels, volume,
                    btc_candles=btc_candles,
                    btc_senior_candles=btc_senior_candles,
                    symbol=symbol,
                )
                signals = resolve_strategy_conflicts(trend_sigs, bounce_sigs, symbol)
            else:
                signals = generate_signals_mtf(
                    candles_by_tf, levels, volume,
                    btc_candles=btc_candles,
                    btc_senior_candles=btc_senior_candles,
                    symbol=symbol,
                )
                # Тегируем сигналы для статистики
                for sig in signals:
                    sig.setdefault("strategy", config.STRATEGY_MODE)

            for sig in signals:
                logger.log_signal(sig)

            if signals and symbol not in trader.open_trades:
                best = signals[0]
                if best["strength"] in ("strong", "medium"):
                    if session_active:
                        trader.open_trade(best)
                    else:
                        log.debug("%s: вне активной сессии, сделка не открыта",
                                  symbol)

        except Exception as e:
            log.warning("Ошибка %s: %s", symbol, e)

    # Обновляем сделки
    trader.update_trades(current_prices)

    # Логируем закрытые
    for trade in trader.trade_history:
        if trade["status"] == "closed" and trade.get("_logged") is None:
            trade["_logged"] = True
            logger.log_trade(trade)
            log.info("Анализ: %s", analyze_trade(trade))

    # Статистика каждые ~5 минут
    if cycle % 60 == 0:
        stats = trader.get_stats()
        log.info("STATS | Trades: %d | W: %d L: %d | WR: %s%% | "
                 "PnL: %+.2f | Bal: %.2f | Open: %d",
                 stats["total_trades"], stats["wins"], stats["losses"],
                 stats["winrate"], stats["total_pnl"],
                 stats["current_balance"], stats["open_trades"])


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    log.info("Запуск бота... %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    asyncio.run(run_bot())
