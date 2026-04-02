# trader.py — Paper Trading (симуляция сделок)
# =============================================
#
# Этот модуль НЕ отправляет реальные ордера на биржу.
# Он симулирует сделки на реальных ценах и считает P&L.
#
# Логика:
# 1. Получает сигнал (bounce / breakout)
# 2. "Открывает" сделку — записывает вход, стоп, тейк
# 3. Следит за ценой — сработал ли стоп или тейк?
# 4. "Закрывает" сделку — считает прибыль/убыток
# 5. Обновляет баланс

import time
from datetime import datetime
import config


class PaperTrader:
    """
    Симулятор торговли.
    
    Использование:
        trader = PaperTrader()
        trader.open_trade(signal)       # открыть
        trader.update_trades(prices)    # обновить (в цикле)
        trader.get_stats()              # статистика
    """
    
    def __init__(self, initial_balance: float = None):
        self.balance = initial_balance or config.INITIAL_BALANCE
        self.initial_balance = self.balance
        
        # Открытые сделки: {symbol: trade_dict}
        self.open_trades = {}
        
        # История всех сделок
        self.trade_history = []
        
        # Максимум одна сделка на символ одновременно
        self.max_trades_per_symbol = 1
        
        # Общий лимит открытых сделок
        self.max_open_trades = 3
    
    def open_trade(self, signal: dict) -> dict | None:
        """
        Открывает paper-сделку по сигналу.
        
        Аргументы:
            signal: словарь из signals.py с полями:
                symbol, type, direction, entry, stop, take,
                risk_reward, strength, reason
        
        Возвращает trade_dict или None если не открыли.
        """
        symbol = signal["symbol"]
        
        # Проверки
        if symbol in self.open_trades:
            print(f"  [Trader] {symbol}: уже есть открытая сделка, пропуск")
            return None
        
        if len(self.open_trades) >= self.max_open_trades:
            print(f"  [Trader] Лимит открытых сделок ({self.max_open_trades})")
            return None
        
        # Расчёт размера позиции
        entry = signal["entry"]
        stop = signal["stop"]
        risk_amount = self.balance * config.RISK_PER_TRADE  # $ которыми рискуем
        stop_distance_pct = abs(entry - stop) / entry       # стоп в %
        
        if stop_distance_pct == 0:
            return None
        
        # Размер позиции = риск / расстояние до стопа
        # С учётом плеча
        position_size_usdt = risk_amount / stop_distance_pct
        position_size_usdt = min(
            position_size_usdt,
            self.balance * config.LEVERAGE  # не больше чем баланс * плечо
        )
        
        # Количество монет
        quantity = position_size_usdt / entry
        
        trade = {
            "id": len(self.trade_history) + 1,
            "symbol": symbol,
            "type": signal["type"],          # bounce / breakout
            "direction": signal["direction"], # long / short
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
            # Заполнятся при закрытии:
            "close_price": None,
            "close_time": None,
            "pnl": None,
            "pnl_pct": None,
            "result": None,  # "win" / "loss" / "breakeven"
            "close_reason": None,
        }
        
        self.open_trades[symbol] = trade
        
        dir_emoji = "🟢" if trade["direction"] == "long" else "🔴"
        print(f"\n{dir_emoji} [TRADE OPENED] {symbol}")
        print(f"   Тип: {trade['type']} | Направление: {trade['direction']}")
        print(f"   Вход: {trade['entry_price']}")
        print(f"   Стоп: {trade['stop_price']}")
        print(f"   Тейк: {trade['take_price']}")
        print(f"   RR: {trade['risk_reward']}")
        print(f"   Размер: {trade['position_usdt']} USDT")
        print(f"   Риск: {trade['risk_amount']} USDT")
        print(f"   Причина: {trade['reason']}")
        
        return trade
    
    def update_trades(self, current_prices: dict[str, float]):
        """
        Обновляет все открытые сделки.
        Вызывается в каждом цикле бота.
        
        Проверяет:
        1. Сработал ли стоп-лосс?
        2. Сработал ли тейк-профит?
        3. Пора ли перенести стоп в безубыток?
        
        Аргументы:
            current_prices: {"BTC/USDT:USDT": 42000.0, ...}
        """
        to_close = []
        
        for symbol, trade in self.open_trades.items():
            if symbol not in current_prices:
                continue
            
            price = current_prices[symbol]
            
            if trade["direction"] == "long":
                self._update_long(trade, price, to_close)
            else:
                self._update_short(trade, price, to_close)
        
        # Закрываем сделки (отдельно, чтоб не менять dict во время итерации)
        for symbol, reason in to_close:
            self._close_trade(symbol, current_prices[symbol], reason)
    
    def _update_long(self, trade: dict, price: float, to_close: list):
        """Обновление лонг-позиции."""
        symbol = trade["symbol"]
        
        # Стоп-лосс сработал
        if price <= trade["stop_price"]:
            to_close.append((symbol, "stop_loss"))
            return
        
        # Тейк-профит сработал
        if price >= trade["take_price"]:
            to_close.append((symbol, "take_profit"))
            return
        
        # Перенос в безубыток
        if not trade["breakeven_moved"]:
            profit_pct = (price - trade["entry_price"]) / trade["entry_price"] * 100
            if profit_pct >= config.MOVE_TO_BREAKEVEN_PCT:
                trade["stop_price"] = trade["entry_price"]
                trade["breakeven_moved"] = True
                print(f"  [Trader] {symbol}: стоп перенесён в безубыток")
    
    def _update_short(self, trade: dict, price: float, to_close: list):
        """Обновление шорт-позиции."""
        symbol = trade["symbol"]
        
        # Стоп-лосс (для шорта стоп ВЫШЕ входа)
        if price >= trade["stop_price"]:
            to_close.append((symbol, "stop_loss"))
            return
        
        # Тейк-профит (для шорта тейк НИЖЕ входа)
        if price <= trade["take_price"]:
            to_close.append((symbol, "take_profit"))
            return
        
        # Безубыток
        if not trade["breakeven_moved"]:
            profit_pct = (trade["entry_price"] - price) / trade["entry_price"] * 100
            if profit_pct >= config.MOVE_TO_BREAKEVEN_PCT:
                trade["stop_price"] = trade["entry_price"]
                trade["breakeven_moved"] = True
                print(f"  [Trader] {symbol}: стоп перенесён в безубыток")
    
    def _close_trade(self, symbol: str, close_price: float, reason: str):
        """Закрывает сделку и считает P&L."""
        trade = self.open_trades.pop(symbol, None)
        if trade is None:
            return
        
        trade["close_price"] = close_price
        trade["close_time"] = datetime.now().isoformat()
        trade["close_reason"] = reason
        
        # Расчёт P&L
        if trade["direction"] == "long":
            pnl_pct = (close_price - trade["entry_price"]) / trade["entry_price"] * 100
        else:
            pnl_pct = (trade["entry_price"] - close_price) / trade["entry_price"] * 100
        
        # P&L в USDT (с учётом размера позиции)
        pnl = trade["position_usdt"] * (pnl_pct / 100)
        
        trade["pnl"] = round(pnl, 2)
        trade["pnl_pct"] = round(pnl_pct, 4)
        
        # Результат
        if pnl > 0:
            trade["result"] = "win"
            emoji = "✅"
        elif pnl < 0:
            trade["result"] = "loss"
            emoji = "❌"
        else:
            trade["result"] = "breakeven"
            emoji = "➖"
        
        trade["status"] = "closed"
        
        # Обновляем баланс
        self.balance += pnl
        
        self.trade_history.append(trade)
        
        print(f"\n{emoji} [TRADE CLOSED] {symbol}")
        print(f"   Вход: {trade['entry_price']} → Выход: {close_price}")
        print(f"   Причина: {reason}")
        print(f"   P&L: {pnl:+.2f} USDT ({pnl_pct:+.2f}%)")
        print(f"   Баланс: {self.balance:.2f} USDT")
    
    def get_stats(self) -> dict:
        """
        Возвращает статистику торговли.
        
        {
            "total_trades": 15,
            "wins": 10,
            "losses": 5,
            "winrate": 66.67,
            "total_pnl": 150.0,
            "total_pnl_pct": 15.0,
            "avg_rr": 2.1,
            "best_trade": 50.0,
            "worst_trade": -20.0,
            "current_balance": 1150.0,
            "open_trades": 2,
        }
        """
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
                (self.balance - self.initial_balance) / self.initial_balance * 100, 2
            ),
            "avg_rr": round(
                sum(t["risk_reward"] for t in closed) / len(closed), 2
            ),
            "best_trade": round(max(pnls), 2),
            "worst_trade": round(min(pnls), 2),
            "current_balance": round(self.balance, 2),
            "open_trades": len(self.open_trades),
        }
