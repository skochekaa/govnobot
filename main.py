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
from datetime import datetime

import config
from log_setup import setup_logger
from exchange import Exchange
from levels import detect_levels_mtf
from volume_analyzer import analyze_volume
from signals import generate_signals_mtf
from trader import PaperTrader
from logger_mod import TradeLogger
from analytics import generate_daily_report, analyze_trade
from coin_scanner import CoinScanner

log = setup_logger("main")
running = True

ALL_TFS = [config.TF_SENIOR, config.TF_MIDDLE, config.TF_WORK, config.TF_ENTRY]


def handle_shutdown(signum, frame):
    global running
    log.info("Получен сигнал остановки...")
    running = False


async def run_bot():
    global running

    exchange = Exchange()
    trader = PaperTrader()
    logger = TradeLogger()
    scanner = CoinScanner(exchange)

    log.info("=" * 55)
    log.info("СКАЛЬПИНГ-БОТ (Paper Trading) — WebSocket + MTF")
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
        watchlist = await scanner.scan()
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
                new_watchlist = await scanner.scan()
                if new_watchlist != watchlist:
                    watchlist = new_watchlist
                    log.info("Новый watchlist: %s", watchlist)
                    # Перезапуск стримов для новых монет
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

    # BTC-свечи для корреляции (из кэша)
    btc_candles = exchange.get_candles("BTC/USDT:USDT", config.TF_WORK)

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

            # MTF-сигналы
            signals = generate_signals_mtf(
                candles_by_tf, levels, volume,
                btc_candles=btc_candles, symbol=symbol,
            )

            for sig in signals:
                logger.log_signal(sig)

            if signals and symbol not in trader.open_trades:
                best = signals[0]
                if best["strength"] in ("strong", "medium"):
                    trader.open_trade(best)

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
