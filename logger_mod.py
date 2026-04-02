# logger_mod.py — Логирование сделок в JSON + CSV

import json
import csv
import os
from datetime import datetime
import config
from log_setup import setup_logger

log = setup_logger("trade_log")


class TradeLogger:
    def __init__(self, log_dir: str = None):
        self.log_dir = log_dir or config.LOG_DIR
        os.makedirs(self.log_dir, exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        self.json_path = os.path.join(self.log_dir, f"trades_{today}.json")
        self.csv_path = os.path.join(self.log_dir, f"trades_{today}.csv")
        self.signals_path = os.path.join(self.log_dir, f"signals_{today}.json")

        self.trades = self._load_json(self.json_path)
        self.signals = self._load_json(self.signals_path)

        if not os.path.exists(self.csv_path):
            self._init_csv()

        log.info("TradeLogger инициализирован, логи: %s", self.log_dir)

    def log_trade(self, trade: dict):
        self.trades.append(trade)
        self._save_json(self.json_path, self.trades)
        self._append_csv(trade)
        log.info("Сделка записана: %s %s %s | PnL: %+.2f",
                 trade.get("symbol"), trade.get("direction"),
                 trade.get("result"), trade.get("pnl", 0))

    def log_signal(self, signal: dict):
        signal_record = {**signal, "logged_at": datetime.now().isoformat()}
        self.signals.append(signal_record)
        self._save_json(self.signals_path, self.signals)

    def get_today_trades(self) -> list:
        return self.trades

    def get_today_signals(self) -> list:
        return self.signals

    def _init_csv(self):
        headers = [
            "id", "symbol", "type", "direction",
            "entry_price", "stop_price", "take_price",
            "close_price", "quantity", "position_usdt",
            "risk_amount", "risk_reward",
            "pnl", "pnl_pct", "result", "close_reason",
            "strength", "reason", "open_time", "close_time",
        ]
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)

    def _append_csv(self, trade: dict):
        row = [
            trade.get("id"), trade.get("symbol"), trade.get("type"),
            trade.get("direction"), trade.get("entry_price"),
            trade.get("stop_price"), trade.get("take_price"),
            trade.get("close_price"), trade.get("quantity"),
            trade.get("position_usdt"), trade.get("risk_amount"),
            trade.get("risk_reward"), trade.get("pnl"),
            trade.get("pnl_pct"), trade.get("result"),
            trade.get("close_reason"), trade.get("strength"),
            trade.get("reason"), trade.get("open_time"),
            trade.get("close_time"),
        ]
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)

    def _save_json(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_json(self, path) -> list:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []
