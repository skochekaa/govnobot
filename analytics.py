# analytics.py — Аналитика торговли
# ====================================
#
# После каждого дня (или по запросу) показывает:
# - Общий результат (прибыль/убыток)
# - Winrate (% выигрышных сделок)
# - Лучшие и худшие сетапы
# - Какой тип сигнала работает лучше (bounce vs breakout)
# - Какие монеты прибыльные, какие нет

from datetime import datetime


def generate_daily_report(trade_history: list,
                           initial_balance: float,
                           current_balance: float) -> str:
    """
    Генерирует текстовый отчёт по итогам дня.
    
    Аргументы:
        trade_history: список всех закрытых сделок
        initial_balance: начальный баланс
        current_balance: текущий баланс
    
    Возвращает строку с отчётом.
    """
    closed = [t for t in trade_history if t.get("status") == "closed"]
    
    if not closed:
        return _empty_report(initial_balance, current_balance)
    
    # ── Основные метрики ─────────────────────
    
    wins = [t for t in closed if t["result"] == "win"]
    losses = [t for t in closed if t["result"] == "loss"]
    breakevens = [t for t in closed if t["result"] == "breakeven"]
    
    total_pnl = sum(t["pnl"] for t in closed)
    winrate = len(wins) / len(closed) * 100 if closed else 0
    
    avg_win = (
        sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    )
    avg_loss = (
        sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    )
    
    best = max(closed, key=lambda t: t["pnl"])
    worst = min(closed, key=lambda t: t["pnl"])
    
    # ── По типу сигнала ──────────────────────
    
    bounce_trades = [t for t in closed if t["type"] == "bounce"]
    breakout_trades = [t for t in closed if t["type"] == "breakout"]
    
    bounce_pnl = sum(t["pnl"] for t in bounce_trades)
    breakout_pnl = sum(t["pnl"] for t in breakout_trades)
    
    bounce_wr = _winrate(bounce_trades)
    breakout_wr = _winrate(breakout_trades)
    
    # ── По монетам ───────────────────────────
    
    symbols = set(t["symbol"] for t in closed)
    symbol_stats = {}
    for sym in symbols:
        sym_trades = [t for t in closed if t["symbol"] == sym]
        symbol_stats[sym] = {
            "trades": len(sym_trades),
            "pnl": round(sum(t["pnl"] for t in sym_trades), 2),
            "winrate": _winrate(sym_trades),
        }
    
    # ── По силе сигнала ──────────────────────
    
    strong = [t for t in closed if t.get("strength") == "strong"]
    medium = [t for t in closed if t.get("strength") == "medium"]
    weak = [t for t in closed if t.get("strength") == "weak"]
    
    # ── Формируем отчёт ──────────────────────
    
    report = []
    report.append("=" * 60)
    report.append(f"  ОТЧЁТ ЗА {datetime.now().strftime('%Y-%m-%d')}")
    report.append("=" * 60)
    report.append("")
    report.append(f"  Начальный баланс:  {initial_balance:.2f} USDT")
    report.append(f"  Текущий баланс:    {current_balance:.2f} USDT")
    report.append(f"  Общий P&L:         {total_pnl:+.2f} USDT "
                  f"({(total_pnl/initial_balance*100):+.2f}%)")
    report.append("")
    report.append(f"  Всего сделок:      {len(closed)}")
    report.append(f"  Выигрышных:        {len(wins)}")
    report.append(f"  Убыточных:         {len(losses)}")
    report.append(f"  Безубыточных:      {len(breakevens)}")
    report.append(f"  Winrate:           {winrate:.1f}%")
    report.append(f"  Средний выигрыш:   {avg_win:+.2f} USDT")
    report.append(f"  Средний проигрыш:  {avg_loss:+.2f} USDT")
    report.append("")
    report.append(f"  Лучшая сделка:     {best['symbol']} "
                  f"{best['pnl']:+.2f} USDT ({best['type']})")
    report.append(f"  Худшая сделка:     {worst['symbol']} "
                  f"{worst['pnl']:+.2f} USDT ({worst['type']})")
    
    report.append("")
    report.append("-" * 60)
    report.append("  ПО ТИПУ СИГНАЛА:")
    report.append(f"    Bounce:   {len(bounce_trades)} сделок, "
                  f"PnL: {bounce_pnl:+.2f}, WR: {bounce_wr:.0f}%")
    report.append(f"    Breakout: {len(breakout_trades)} сделок, "
                  f"PnL: {breakout_pnl:+.2f}, WR: {breakout_wr:.0f}%")
    
    report.append("")
    report.append("-" * 60)
    report.append("  ПО МОНЕТАМ:")
    for sym, stats in sorted(
        symbol_stats.items(), key=lambda x: x[1]["pnl"], reverse=True
    ):
        report.append(
            f"    {sym:20s}  {stats['trades']} сделок  "
            f"PnL: {stats['pnl']:+8.2f}  WR: {stats['winrate']:.0f}%"
        )
    
    report.append("")
    report.append("-" * 60)
    report.append("  ПО СИЛЕ СИГНАЛА:")
    for label, trades in [("Strong", strong), ("Medium", medium), ("Weak", weak)]:
        if trades:
            pnl = sum(t["pnl"] for t in trades)
            wr = _winrate(trades)
            report.append(
                f"    {label:10s}  {len(trades)} сделок  "
                f"PnL: {pnl:+8.2f}  WR: {wr:.0f}%"
            )
    
    report.append("")
    report.append("=" * 60)
    
    return "\n".join(report)


def analyze_trade(trade: dict) -> str:
    """
    Анализ одной сделки — что сработало, что нет.
    
    Возвращает текстовый комментарий.
    """
    comments = []
    
    if trade["result"] == "win":
        comments.append(f"Прибыль: +{trade['pnl']:.2f} USDT")
        if trade["risk_reward"] >= 3:
            comments.append("Отличный RR — сигнал отработал по полной")
        elif trade["risk_reward"] >= 2:
            comments.append("Хороший RR — стандартная отработка")
    
    elif trade["result"] == "loss":
        comments.append(f"Убыток: {trade['pnl']:.2f} USDT")
        
        if trade["close_reason"] == "stop_loss":
            comments.append("Сработал стоп-лосс")
            
            if trade.get("strength") == "weak":
                comments.append(
                    "ВЫВОД: слабый сигнал → рассмотреть фильтрацию weak сигналов"
                )
            
            if not trade.get("volume_confirmed", True):
                comments.append(
                    "ВЫВОД: объём не подтвердил → усилить фильтр объёма"
                )
    
    elif trade["result"] == "breakeven":
        comments.append("Безубыток — стоп перенесён, затем сработал")
        comments.append("Нормальный результат — депозит защищён")
    
    return " | ".join(comments)


def _winrate(trades: list) -> float:
    """Считает winrate для списка сделок."""
    if not trades:
        return 0
    wins = sum(1 for t in trades if t.get("result") == "win")
    return wins / len(trades) * 100


def _empty_report(initial: float, current: float) -> str:
    """Отчёт когда сделок не было."""
    return (
        f"{'='*60}\n"
        f"  ОТЧЁТ ЗА {datetime.now().strftime('%Y-%m-%d')}\n"
        f"{'='*60}\n\n"
        f"  Сделок не было.\n"
        f"  Баланс: {current:.2f} USDT\n"
        f"{'='*60}"
    )
