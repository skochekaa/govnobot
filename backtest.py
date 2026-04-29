# backtest.py — Бэктест-движок для прогонки стратегии на истории
# ================================================================
#
# Запуск:
#   python backtest.py --symbol WIF/USDT:USDT --candles 2000
#
# Что делает:
#   1. Загружает исторические свечи через REST (ccxt)
#   2. Проигрывает их цикл за циклом как живой бот
#   3. Использует те же функции signals/levels/volume что live
#   4. Открывает/закрывает сделки через PaperTrader
#   5. В конце — отчёт с PnL, winrate, profit factor
#
# Ограничения MVP:
#   - Single-symbol (один символ за прогон)
#   - Stop/Take срабатывает по high/low свечи (без intra-candle)
#   - При амбигуитности (stop и take в одной свече) первым срабатывает STOP
#     (консервативная симуляция — даёт более реалистичную, скорее занижающую картинку)
#   - Circuit Breaker и Daily Loss Limit временно отключаются — они привязаны
#     к реальному wall-clock времени и в быстром бэктесте ломаются

import argparse
import numpy as np

try:
    import ccxt
except ImportError:
    print("Ошибка: ccxt не установлен. Установи: pip install ccxt")
    raise

import config
from log_setup import setup_logger
from levels import detect_levels_mtf
from volume_analyzer import analyze_volume
# Dispatcher: bounce или trend, в зависимости от config.STRATEGY_MODE
if config.STRATEGY_MODE == "trend":
    from signals_trend import generate_signals_mtf
else:
    from signals import generate_signals_mtf
from trader import PaperTrader
from analytics import generate_daily_report

log = setup_logger("backtest")


def fetch_history(exchange, symbol: str, timeframe: str,
                    limit_total: int) -> np.ndarray:
    """
    Загружает OHLCV свечи с пагинацией (ccxt ограничен 1500 за запрос).
    Возвращает numpy массив [[ts, o, h, l, c, v], ...].
    """
    all_candles = []
    since = None
    per_page = 1500
    while len(all_candles) < limit_total:
        remaining = limit_total - len(all_candles)
        batch = exchange.fetch_ohlcv(symbol, timeframe,
                                       since=since,
                                       limit=min(per_page, remaining))
        if not batch:
            break
        all_candles.extend(batch)
        since = batch[-1][0] + 1
        if len(batch) < min(per_page, remaining):
            break
    return np.array(all_candles[:limit_total], dtype=float)


def align_up_to(candles: np.ndarray, timestamp_ms: int) -> np.ndarray:
    """
    Возвращает свечи с timestamp строго <= указанного (то что "уже закрылось").
    Использует бинарный поиск для скорости.
    """
    if len(candles) == 0:
        return candles
    idx = np.searchsorted(candles[:, 0], timestamp_ms, side="right")
    return candles[:idx]


def check_closure(trade: dict, high: float, low: float):
    """
    Проверяет, закрылась ли позиция (stop/take) в данной свече
    и обрабатывает перенос в безубыток.

    Консервативная симуляция: если и stop, и take могли сработать
    в одной свече, считаем что сработал STOP первым.

    Возвращает (reason, close_price) или (None, None).
    """
    if trade["direction"] == "long":
        if low <= trade["stop_price"]:
            return "stop_loss", trade["stop_price"]
        if high >= trade["take_price"]:
            return "take_profit", trade["take_price"]
        # Перенос в безубыток
        if not trade.get("breakeven_moved"):
            entry = trade["entry_price"]
            max_profit_pct = (high - entry) / entry * 100
            if max_profit_pct >= config.MOVE_TO_BREAKEVEN_PCT:
                trade["stop_price"] = entry
                trade["breakeven_moved"] = True
    else:  # short
        if high >= trade["stop_price"]:
            return "stop_loss", trade["stop_price"]
        if low <= trade["take_price"]:
            return "take_profit", trade["take_price"]
        if not trade.get("breakeven_moved"):
            entry = trade["entry_price"]
            max_profit_pct = (entry - low) / entry * 100
            if max_profit_pct >= config.MOVE_TO_BREAKEVEN_PCT:
                trade["stop_price"] = entry
                trade["breakeven_moved"] = True
    return None, None


def run_backtest(symbol: str, timeframes: list, total_candles: int):
    exchange = ccxt.binanceusdm({
        'options': {'defaultType': 'future'},
        'enableRateLimit': True,
    })
    exchange.load_markets()

    if symbol not in exchange.markets:
        log.error("Символ %s не найден на бирже", symbol)
        return

    # ── Загружаем историю для символа ──
    history = {}
    for tf in timeframes:
        log.info("Загрузка %s %s...", symbol, tf)
        history[tf] = fetch_history(exchange, symbol, tf, total_candles)
        log.info("  %d свечей", len(history[tf]))

    # ── Загружаем BTC для regime и correlation фильтров ──
    btc = "BTC/USDT:USDT"
    btc_history = {}
    for tf in {config.TF_WORK, config.TF_SENIOR}:
        log.info("Загрузка BTC %s...", tf)
        btc_history[tf] = fetch_history(exchange, btc, tf, total_candles)
        log.info("  %d свечей", len(btc_history[tf]))

    # ── Отключаем time-based фильтры на время бэктеста ──
    backup = {
        'cb': config.CIRCUIT_BREAKER_ENABLED,
        'dll': config.DAILY_LOSS_LIMIT_ENABLED,
        'session': config.SESSION_FILTER_ENABLED,
    }
    config.CIRCUIT_BREAKER_ENABLED = False
    config.DAILY_LOSS_LIMIT_ENABLED = False
    config.SESSION_FILTER_ENABLED = False
    log.info("Time-based фильтры (CB, DLL, Session) временно отключены для бэктеста")

    try:
        _run_simulation(symbol, timeframes, history, btc_history)
    finally:
        # Восстанавливаем настройки
        config.CIRCUIT_BREAKER_ENABLED = backup['cb']
        config.DAILY_LOSS_LIMIT_ENABLED = backup['dll']
        config.SESSION_FILTER_ENABLED = backup['session']


def _run_simulation(symbol: str, timeframes: list,
                     history: dict, btc_history: dict):
    main_tf = config.TF_WORK
    work_candles = history[main_tf]

    # Warmup: нужно минимум 50 свечей 1h BTC для SMA50, 20 свечей 5m для сигналов
    warmup = 100
    if len(work_candles) < warmup + 10:
        log.error("Недостаточно свечей (%d), нужно минимум %d",
                   len(work_candles), warmup + 10)
        return

    trader = PaperTrader()
    log.info("Начальный баланс: %.2f USDT", trader.balance)
    log.info("Прогон: %d свечей (с warmup %d)", len(work_candles) - warmup, warmup)
    log.info("=" * 60)

    for i in range(warmup, len(work_candles)):
        current_ts = int(work_candles[i, 0])
        current_high = float(work_candles[i, 2])
        current_low = float(work_candles[i, 3])

        # 1. Проверяем стопы/тейки по high/low текущей свечи
        for sym in list(trader.open_trades.keys()):
            trade = trader.open_trades[sym]
            reason, close_price = check_closure(trade, current_high, current_low)
            if reason:
                trader._close_trade(sym, close_price, reason)

        # 2. Собираем данные по текущему времени
        candles_by_tf = {tf: align_up_to(history[tf], current_ts) for tf in timeframes}

        # Пропускаем если мало данных
        if main_tf not in candles_by_tf or len(candles_by_tf[main_tf]) < 50:
            continue

        btc_5m = align_up_to(btc_history[config.TF_WORK], current_ts)
        btc_1h = align_up_to(btc_history[config.TF_SENIOR], current_ts)

        # 3. MTF-уровни
        levels_tf_data = {tf: c for tf, c in candles_by_tf.items()
                          if tf in config.TF_LEVEL_WEIGHTS and len(c) >= 20}
        if not levels_tf_data:
            continue
        levels = detect_levels_mtf(levels_tf_data)

        # 4. Объём (в бэктесте нет реального trade flow — delta=None)
        volume = analyze_volume(candles_by_tf[main_tf], levels)

        # 5. Сигналы
        signals_list = generate_signals_mtf(
            candles_by_tf, levels, volume,
            btc_candles=btc_5m,
            btc_senior_candles=btc_1h,
            symbol=symbol,
        )

        # 6. Открытие сделки
        if signals_list and symbol not in trader.open_trades:
            best = signals_list[0]
            if best["strength"] in ("strong", "medium"):
                trader.open_trade(best)

    # ── Закрываем оставшиеся позиции ──
    last_close = float(work_candles[-1, 4])
    for sym in list(trader.open_trades.keys()):
        trader._close_trade(sym, last_close, "end_of_backtest")

    # ── Отчёт ──
    log.info("=" * 60)
    print()
    print(generate_daily_report(trader.trade_history,
                                  trader.initial_balance,
                                  trader.balance))

    # Profit factor
    wins_sum = sum(t["pnl"] for t in trader.trade_history if t["pnl"] > 0)
    losses_sum = abs(sum(t["pnl"] for t in trader.trade_history if t["pnl"] < 0))
    pf = wins_sum / losses_sum if losses_sum > 0 else float('inf')

    print()
    print(f"Profit factor: {pf:.2f}  (>1 прибыль, <1 убыток)")
    print(f"Final balance: {trader.balance:.2f} USDT "
          f"({(trader.balance - trader.initial_balance) / trader.initial_balance * 100:+.2f}%)")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backtest trading strategy on historical data")
    parser.add_argument("--symbol", default="WIF/USDT:USDT",
                        help="Символ (например WIF/USDT:USDT)")
    parser.add_argument("--candles", type=int, default=2000,
                        help="Сколько 5m свечей загружать (2000 ≈ 7 дней, "
                             "8640 ≈ 30 дней)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    tfs = [config.TF_SENIOR, config.TF_MIDDLE, config.TF_WORK, config.TF_ENTRY]
    run_backtest(args.symbol, tfs, args.candles)
