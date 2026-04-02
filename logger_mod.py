# logger_mod.py — Логирование сделок
# =====================================
#
# Записывает каждую сделку в два формата:
# 1. JSON — полные данные (для анализа ботом)
# 2. CSV — таблица (для Excel / Google Sheets)
#
# Можно открыть CSV в таблице и сразу увидеть
# все входы, выходы, прибыли и убытки.

import json
import csv
import os
from datetime import datetime
import config


class TradeLogger:
    """
    Логирование сделок в файлы.
    
    Использование:
        logger = TradeLogger()
        logger.log_trade(trade_dict)     # записать сделку
        logger.log_signal(signal_dict)   # записать сигнал (даже без сделки)
        logger.log_event("message")      # записать событие
    """
    
    def __init__(self, log_dir: str = None):
        self.log_dir = log_dir or config.LOG_DIR
        
        # Создаём папку для логов
        os.makedirs(self.log_dir, exist_ok=True)
        
        # Дата в имени файла (новый файл каждый день)
        today = datetime.now().strftime("%Y-%m-%d")
        
        self.json_path = os.path.join(
            self.log_dir, f"trades_{today}.json"
        )
        self.csv_path = os.path.join(
            self.log_dir, f"trades_{today}.csv"
        )
        self.signals_path = os.path.join(
            self.log_dir, f"signals_{today}.json"
        )
        self.event_log_path = os.path.join(
            self.log_dir, f"events_{today}.log"
        )
        
        # Загружаем существующие данные (если бот перезапустился)
        self.trades = self._load_json(self.json_path)
        self.signals = self._load_json(self.signals_path)
        
        # Создаём CSV с заголовками, если файла нет
        if not os.path.exists(self.csv_path):
            self._init_csv()
        
        self.log_event("Logger initialized")
    
    def log_trade(self, trade: dict):
        """
        Записывает закрытую сделку.
        
        Сохраняет в JSON (полные данные) и CSV (таблица).
        """
        self.trades.append(trade)
        
        # JSON
        self._save_json(self.json_path, self.trades)
        
        # CSV — добавляем строку
        self._append_csv(trade)
        
        result_emoji = {"win": "✅", "loss": "❌", "breakeven": "➖"}
        emoji = result_emoji.get(trade.get("result", ""), "?")
        
        self.log_event(
            f"{emoji} TRADE: {trade['symbol']} {trade['direction']} "
            f"{trade['type']} | Entry: {trade['entry_price']} → "
            f"Exit: {trade.get('close_price', '?')} | "
            f"PnL: {trade.get('pnl', 0):+.2f} USDT"
        )
    
    def log_signal(self, signal: dict):
        """
        Записывает обнаруженный сигнал (даже если не торговали).
        Полезно для анализа: сколько сигналов было, какие пропустили.
        """
        signal_record = {
            **signal,
            "logged_at": datetime.now().isoformat(),
        }
        self.signals.append(signal_record)
        self._save_json(self.signals_path, self.signals)
    
    def log_event(self, message: str):
        """
        Записывает текстовое событие в лог-файл.
        
        Пример: "[2024-01-15 14:30:22] Bot started"
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        
        with open(self.event_log_path, "a", encoding="utf-8") as f:
            f.write(line)
    
    def get_today_trades(self) -> list:
        """Возвращает все сделки за сегодня."""
        return self.trades
    
    def get_today_signals(self) -> list:
        """Возвращает все сигналы за сегодня."""
        return self.signals
    
    # ── Внутренние методы ────────────────────
    
    def _init_csv(self):
        """Создаёт CSV с заголовками."""
        headers = [
            "id", "symbol", "type", "direction",
            "entry_price", "stop_price", "take_price",
            "close_price", "quantity", "position_usdt",
            "risk_amount", "risk_reward",
            "pnl", "pnl_pct", "result", "close_reason",
            "strength", "reason",
            "open_time", "close_time",
        ]
        
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
    
    def _append_csv(self, trade: dict):
        """Добавляет строку в CSV."""
        row = [
            trade.get("id", ""),
            trade.get("symbol", ""),
            trade.get("type", ""),
            trade.get("direction", ""),
            trade.get("entry_price", ""),
            trade.get("stop_price", ""),
            trade.get("take_price", ""),
            trade.get("close_price", ""),
            trade.get("quantity", ""),
            trade.get("position_usdt", ""),
            trade.get("risk_amount", ""),
            trade.get("risk_reward", ""),
            trade.get("pnl", ""),
            trade.get("pnl_pct", ""),
            trade.get("result", ""),
            trade.get("close_reason", ""),
            trade.get("strength", ""),
            trade.get("reason", ""),
            trade.get("open_time", ""),
            trade.get("close_time", ""),
        ]
        
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)
    
    def _save_json(self, path: str, data: list):
        """Сохраняет данные в JSON."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _load_json(self, path: str) -> list:
        """Загружает данные из JSON (если файл есть)."""
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []
