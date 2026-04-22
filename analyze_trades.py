"""Анализ трейдов: winrate по стратегиям, монетам, силе сигнала"""
import csv
import sys
from collections import defaultdict


def analyze(csv_path):
    trades = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["pnl"] = float(row["pnl"])
                row["pnl_pct"] = float(row["pnl_pct"])
                row["risk_reward"] = float(row["risk_reward"])
                trades.append(row)
            except (ValueError, KeyError):
                continue

    if not trades:
        print("Нет данных")
        return

    print(f"=" * 70)
    print(f"АНАЛИЗ {len(trades)} СДЕЛОК")
    print(f"=" * 70)

    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["result"] == "win"]
    losses = [t for t in trades if t["result"] == "loss"]
    winrate = len(wins) / len(trades) * 100
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses else 0

    print(f"\nОБЩЕЕ:")
    print(f"  Всего сделок: {len(trades)}")
    print(f"  Wins: {len(wins)} | Losses: {len(losses)}")
    print(f"  Winrate: {winrate:.1f}%")
    print(f"  Total PnL: {total_pnl:+.2f} USDT")
    print(f"  Avg win: {avg_win:+.2f} | Avg loss: {avg_loss:+.2f}")
    print(f"  Profit factor: {profit_factor:.2f}")
    print(f"  (PF > 1 = прибыль, < 1 = убыток)")

    # По типу сигнала (reason)
    print(f"\n{'='*70}")
    print("ПО ПРИЧИНЕ ВХОДА (reason):")
    print(f"{'='*70}")
    by_reason = defaultdict(list)
    for t in trades:
        # Убираем проценты из reason для группировки
        reason_key = t["reason"].split("(")[0].strip()
        by_reason[reason_key].append(t)

    reason_stats = []
    for reason, items in by_reason.items():
        w = sum(1 for t in items if t["result"] == "win")
        pnl = sum(t["pnl"] for t in items)
        wr = w / len(items) * 100
        reason_stats.append((reason, len(items), w, wr, pnl))
    reason_stats.sort(key=lambda x: x[4], reverse=True)
    for reason, n, w, wr, pnl in reason_stats:
        print(f"  {reason:45s} {n:3d} сд. | WR: {wr:5.1f}% | PnL: {pnl:+8.2f}")

    # По монетам
    print(f"\n{'='*70}")
    print("ПО МОНЕТАМ:")
    print(f"{'='*70}")
    by_symbol = defaultdict(list)
    for t in trades:
        by_symbol[t["symbol"]].append(t)

    sym_stats = []
    for sym, items in by_symbol.items():
        w = sum(1 for t in items if t["result"] == "win")
        pnl = sum(t["pnl"] for t in items)
        wr = w / len(items) * 100
        sym_stats.append((sym, len(items), w, wr, pnl))
    sym_stats.sort(key=lambda x: x[4], reverse=True)
    for sym, n, w, wr, pnl in sym_stats:
        print(f"  {sym:25s} {n:3d} сд. | WR: {wr:5.1f}% | PnL: {pnl:+8.2f}")

    # По направлению
    print(f"\n{'='*70}")
    print("ПО НАПРАВЛЕНИЮ:")
    print(f"{'='*70}")
    for direction in ("long", "short"):
        items = [t for t in trades if t["direction"] == direction]
        if not items:
            continue
        w = sum(1 for t in items if t["result"] == "win")
        pnl = sum(t["pnl"] for t in items)
        wr = w / len(items) * 100
        print(f"  {direction.upper():10s} {len(items):3d} сд. | WR: {wr:5.1f}% | PnL: {pnl:+8.2f}")

    # По силе сигнала
    print(f"\n{'='*70}")
    print("ПО СИЛЕ СИГНАЛА:")
    print(f"{'='*70}")
    for strength in ("strong", "medium", "weak"):
        items = [t for t in trades if t["strength"] == strength]
        if not items:
            continue
        w = sum(1 for t in items if t["result"] == "win")
        pnl = sum(t["pnl"] for t in items)
        wr = w / len(items) * 100
        print(f"  {strength:10s} {len(items):3d} сд. | WR: {wr:5.1f}% | PnL: {pnl:+8.2f}")

    # Комбинации: направление + reason
    print(f"\n{'='*70}")
    print("КОМБИНАЦИИ (направление + причина) — где бот зарабатывает/сливает:")
    print(f"{'='*70}")
    by_combo = defaultdict(list)
    for t in trades:
        reason_key = t["reason"].split("(")[0].strip()
        combo = f"{t['direction']:5s} | {reason_key}"
        by_combo[combo].append(t)

    combo_stats = []
    for combo, items in by_combo.items():
        w = sum(1 for t in items if t["result"] == "win")
        pnl = sum(t["pnl"] for t in items)
        wr = w / len(items) * 100
        combo_stats.append((combo, len(items), w, wr, pnl))
    combo_stats.sort(key=lambda x: x[4], reverse=True)
    for combo, n, w, wr, pnl in combo_stats:
        marker = "OK" if pnl > 0 else "BAD"
        print(f"  [{marker}] {combo:55s} {n:3d} сд. | WR: {wr:5.1f}% | PnL: {pnl:+8.2f}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "logs/trades.csv"
    analyze(path)
