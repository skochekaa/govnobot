# trader.py — Paper Trading (симуляция сделок)

import time
from datetime import datetime
import config
from log_setup import setup_logger

log = setup_logger("trader")


class PaperTrader:
    def __init__(self, initial_balance: float = None):
        self.balance = initial_balance or config.INITIAL_BALANCE
        self.initial_balance = self.balance
        self.open_trades = {}
        self.trade_history = []
        self.max_trades_per_symbol = 1
        self.max_open_trades = 3

    def open_trade(self, signal: dict) -> dict | None:
        symbol = signal["symbol"]

        if symbol in self.open_trades:
            log.debug("%s: уже есть открытая сделка, пропуск", symbol)
            return None

        if len(self.open_trades) >= self.max_open_trades:
            log.debug("Лимит открытых сделок (%d)", self.max_open_trades)
            return None

        entry = signal["entry"]
        stop = signal["stop"]
        take = signal["take"]

        # Защита: entry, stop, take не должны совпадать
        if entry == stop or entry == take:
            log.warning("%s: entry=stop или entry=take (%.8f/%.8f/%.8f), пропуск",
                        symbol, entry, stop, take)
            return None

        risk_amount = self.balance * config.RISK_PER_TRADE
        stop_distance_pct = abs(entry - stop) / entry

        if stop_distance_pct == 0:
            return None

        position_size_usdt = risk_amount / stop_distance_pct
        position_size_usdt = min(position_size_usdt, self.balance * config.LEVERAGE)
        quantity = position_size_usdt / entry

        trade = {
            "id": len(self.trade_history) + 1,
            "symbol": symbol,
            "type": signal["type"],
            "direction": signal["direction"],
            "entry_price": entry,
            "stop_price": stop,
            "take_price": signal["take"],
            "quantity": round(quantity, 6),
            "position_usdt": round(position_size_usdt, 2),
            "risk_amount": round(risk_amount, 2),
            "risk_reward": signal["risk_reward"],
            "strength": signal["strength"],
            "reason": signal["reason"],
            "open_time": datetime.now().isoformat(),
            "open_timestamp": time.time(),
            "status": "open",
            "breakeven_moved": False,
            "close_price": None,
            "close_time": None,
            "pnl": None,
            "pnl_pct": None,
            "result": None,
            "close_reason": None,
        }

        self.open_trades[symbol] = trade

        direction_label = "LONG" if trade["direction"] == "long" else "SHORT"
        log.info("OPEN %s %s %s | Entry: %s | Stop: %s | Take: %s | RR: %s | Size: %s USDT | Risk: %s USDT | %s",
                 direction_label, symbol, trade["type"],
                 trade["entry_price"], trade["stop_price"], trade["take_price"],
                 trade["risk_reward"], trade["position_usdt"],
                 trade["risk_amount"], trade["reason"])

        return trade

    def update_trades(self, current_prices: dict[str, float]):
        to_close = []

        for symbol, trade in self.open_trades.items():
            if symbol not in current_prices:
                continue
            price = current_prices[symbol]

            if trade["direction"] == "long":
                self._update_long(trade, price, to_close)
            else:
                self._update_short(trade, price, to_close)

        for symbol, reason in to_close:
            self._close_trade(symbol, current_prices[symbol], reason)

    def _update_long(self, trade, price, to_close):
        symbol = trade["symbol"]
        if price <= trade["stop_price"]:
            to_close.append((symbol, "stop_loss"))
            return
        if price >= trade["take_price"]:
            to_close.append((symbol, "take_profit"))
            return
        if not trade["breakeven_moved"]:
            profit_pct = (price - trade["entry_price"]) / trade["entry_price"] * 100
            if profit_pct >= config.MOVE_TO_BREAKEVEN_PCT:
                trade["stop_price"] = trade["entry_price"]
                trade["breakeven_moved"] = True
                log.info("%s: стоп перенесён в безубыток", symbol)

    def _update_short(self, trade, price, to_close):
        symbol = trade["symbol"]
        if price >= trade["stop_price"]:
            to_close.append((symbol, "stop_loss"))
            return
        if price <= trade["take_price"]:
            to_close.append((symbol, "take_profit"))
            return
        if not trade["breakeven_moved"]:
            profit_pct = (trade["entry_price"] - price) / trade["entry_price"] * 100
            if profit_pct >= config.MOVE_TO_BREAKEVEN_PCT:
                trade["stop_price"] = trade["entry_price"]
                trade["breakeven_moved"] = True
                log.info("%s: стоп перенесён в безубыток", symbol)

    def _close_trade(self, symbol, close_price, reason):
        trade = self.open_trades.pop(symbol, None)
        if trade is None:
            return

        trade["close_price"] = close_price
        trade["close_time"] = datetime.now().isoformat()
        trade["close_reason"] = reason

        if trade["direction"] == "long":
            pnl_pct = (close_price - trade["entry_price"]) / trade["entry_price"] * 100
        else:
            pnl_pct = (trade["entry_price"] - close_price) / trade["entry_price"] * 100

        pnl_raw = trade["position_usdt"] * (pnl_pct / 100)
        # Комиссия: вход + выход (taker fee × 2), минус возврат от брокера
        fee_gross = trade["position_usdt"] * config.TAKER_FEE_PCT / 100 * 2
        fee = fee_gross * (1 - config.FEE_REBATE_PCT)
        pnl = pnl_raw - fee
        trade["pnl"] = round(pnl, 2)
        trade["pnl_pct"] = round(pnl / trade["position_usdt"] * 100, 4)
        trade["fee"] = round(fee, 2)

        if pnl > 0:
            trade["result"] = "win"
            result_label = "WIN"
        elif pnl < 0:
            trade["result"] = "loss"
            result_label = "LOSS"
        else:
            trade["result"] = "breakeven"
            result_label = "BE"

        trade["status"] = "closed"
        self.balance += pnl
        self.trade_history.append(trade)

        log.info("CLOSE %s %s | %s → %s | %s | PnL: %+.2f USDT (fee: %.2f) | Balance: %.2f",
                 result_label, symbol,
                 trade["entry_price"], close_price, reason,
                 pnl, fee, self.balance)

    def get_stats(self) -> dict:
        closed = [t for t in self.trade_history if t["status"] == "closed"]
        if not closed:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "winrate": 0, "total_pnl": 0, "total_pnl_pct": 0,
                "avg_rr": 0, "best_trade": 0, "worst_trade": 0,
                "current_balance": self.balance,
                "open_trades": len(self.open_trades),
            }

        wins = [t for t in closed if t["result"] == "win"]
        losses = [t for t in closed if t["result"] == "loss"]
        pnls = [t["pnl"] for t in closed]

        return {
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "winrate": round(len(wins) / len(closed) * 100, 2),
            "total_pnl": round(sum(pnls), 2),
            "total_pnl_pct": round(
                (self.balance - self.initial_balance) / self.initial_balance * 100, 2),
            "avg_rr": round(sum(t["risk_reward"] for t in closed) / len(closed), 2),
            "best_trade": round(max(pnls), 2),
            "worst_trade": round(min(pnls), 2),
            "current_balance": round(self.balance, 2),
            "open_trades": len(self.open_trades),
        }
