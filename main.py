# main.py — Точка входа скальпинг-бота
# =======================================
#
# Запуск: python main.py
#
# Бот работает в бесконечном цикле:
# 1. Загружает свечи для каждой монеты из watchlist
# 2. Определяет уровни
# 3. Анализирует объём
# 4. Ищет сигналы (bounce / breakout)
# 5. Открывает paper-сделки
# 6. Следит за открытыми сделками (стоп/тейк)
# 7. Логирует всё
# 8. Ждёт N секунд → повтор
#
# Ctrl+C для остановки (покажет итоговый отчёт).

import asyncio
import signal
import sys
from datetime import datetime

import config
from exchange import Exchange
from levels import detect_levels, detect_consolidation
from volume_analyzer import analyze_volume
from signals import generate_signals
from trader import PaperTrader
from logger_mod import TradeLogger
from analytics import generate_daily_report, analyze_trade


# ── Глобальный флаг для корректной остановки ──

running = True


def handle_shutdown(signum, frame):
    """Обработка Ctrl+C — корректно завершаем бота."""
    global running
    print("\n\n⏹ Остановка бота...")
    running = False


# ── Основная логика ──────────────────────────

async def run_bot():
    """Главная функция бота."""
    global running
    
    # Инициализация модулей
    exchange = Exchange()
    trader = PaperTrader()
    logger = TradeLogger()
    
    print("=" * 60)
    print("  СКАЛЬПИНГ-БОТ (Paper Trading)")
    print(f"  Баланс: {config.INITIAL_BALANCE} USDT")
    print(f"  Плечо: {config.LEVERAGE}x")
    print(f"  Риск на сделку: {config.RISK_PER_TRADE * 100}%")
    print(f"  Мин. RR: {config.MIN_RISK_REWARD}")
    print(f"  Watchlist: {', '.join(config.WATCHLIST)}")
    print("=" * 60)
    
    try:
        # Подключаемся к бирже
        await exchange.connect()
        logger.log_event("Bot started")
        
        # Счётчик циклов (для периодического вывода статистики)
        cycle = 0
        
        while running:
            cycle += 1
            
            try:
                await process_cycle(
                    exchange, trader, logger, cycle
                )
            except Exception as e:
                print(f"\n⚠ Ошибка в цикле {cycle}: {e}")
                logger.log_event(f"Error in cycle {cycle}: {e}")
            
            # Ждём перед следующим циклом
            await asyncio.sleep(config.UPDATE_INTERVAL)
    
    except KeyboardInterrupt:
        pass
    
    finally:
        # Финальный отчёт
        print("\n")
        report = generate_daily_report(
            trader.trade_history,
            trader.initial_balance,
            trader.balance
        )
        print(report)
        logger.log_event("Bot stopped")
        logger.log_event(f"Final balance: {trader.balance:.2f} USDT")
        
        # Закрываем соединение
        await exchange.close()


async def process_cycle(exchange: Exchange, trader: PaperTrader,
                         logger: TradeLogger, cycle: int):
    """
    Один цикл работы бота.
    
    Вызывается каждые UPDATE_INTERVAL секунд.
    """
    current_prices = {}
    btc_candles = None
    
    # ── Шаг 1: Загружаем данные BTC (для фильтра корреляции) ──
    
    btc_symbol = "BTC/USDT:USDT"
    btc_candles = await exchange.fetch_candles(
        btc_symbol, config.PRIMARY_TF, limit=200
    )
    
    if len(btc_candles) > 0:
        ticker = await exchange.get_ticker(btc_symbol)
        if ticker:
            current_prices[btc_symbol] = ticker["last"]
    
    # ── Шаг 2: Обрабатываем каждую монету из watchlist ──
    
    for symbol in config.WATCHLIST:
        try:
            # 2.1 Загружаем свечи
            candles = await exchange.fetch_candles(
                symbol, config.PRIMARY_TF, limit=200
            )
            
            if len(candles) < 50:  # мало данных
                continue
            
            # 2.2 Получаем текущую цену
            ticker = await exchange.get_ticker(symbol)
            if not ticker:
                continue
            current_prices[symbol] = ticker["last"]
            
            # 2.3 Определяем уровни
            levels = detect_levels(candles)
            
            # 2.4 Анализируем объём
            delta = exchange.calculate_buy_sell_delta(symbol)
            volume = analyze_volume(candles, levels, delta)
            
            # 2.5 Генерируем сигналы
            signals = generate_signals(
                candles, levels, volume,
                btc_candles=btc_candles,
                symbol=symbol,
            )
            
            # 2.6 Логируем сигналы
            for sig in signals:
                logger.log_signal(sig)
            
            # 2.7 Пробуем открыть сделку (берём лучший сигнал)
            if signals and symbol not in trader.open_trades:
                best_signal = signals[0]  # уже отсортированы по силе
                
                # Только strong и medium сигналы
                if best_signal["strength"] in ("strong", "medium"):
                    trade = trader.open_trade(best_signal)
                    if trade:
                        logger.log_event(
                            f"OPEN: {symbol} {trade['direction']} "
                            f"{trade['type']} @ {trade['entry_price']}"
                        )
        
        except Exception as e:
            print(f"  ⚠ Ошибка обработки {symbol}: {e}")
    
    # ── Шаг 3: Обновляем открытые сделки ──
    
    trader.update_trades(current_prices)
    
    # Логируем закрытые сделки
    for trade in trader.trade_history:
        if (trade["status"] == "closed" and
                trade.get("_logged") is None):
            trade["_logged"] = True
            logger.log_trade(trade)
            
            # Анализ сделки
            analysis = analyze_trade(trade)
            print(f"  📝 Анализ: {analysis}")
            logger.log_event(f"ANALYSIS: {analysis}")
    
    # ── Шаг 4: Периодический вывод статистики ──
    
    # Каждые 60 циклов (~5 минут при UPDATE_INTERVAL=5)
    if cycle % 60 == 0:
        stats = trader.get_stats()
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n📊 [{timestamp}] Статистика:")
        print(f"   Сделок: {stats['total_trades']} | "
              f"Win: {stats['wins']} | Loss: {stats['losses']} | "
              f"WR: {stats['winrate']}%")
        print(f"   PnL: {stats['total_pnl']:+.2f} USDT | "
              f"Баланс: {stats['current_balance']:.2f} USDT")
        print(f"   Открытых: {stats['open_trades']}")


# ── Запуск ───────────────────────────────────

if __name__ == "__main__":
    # Перехват Ctrl+C
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    print("\n🚀 Запуск скальпинг-бота...")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("   Нажми Ctrl+C для остановки\n")
    
    asyncio.run(run_bot())
