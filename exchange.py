# exchange.py — Подключение к Binance Futures через ccxt.pro
# ============================================================
#
# Что делает этот модуль:
# 1. Подключается к Binance Futures (USDT-M)
# 2. Загружает исторические свечи (OHLCV)
# 3. Подписывается на реалтайм-данные через WebSocket
# 4. Отдаёт данные другим модулям бота
#
# ccxt.pro — это асинхронная версия ccxt с поддержкой WebSocket.
# Обычный ccxt делает HTTP-запросы (медленно для скальпинга).
# ccxt.pro держит постоянное соединение и получает данные мгновенно.

import asyncio
import ccxt.pro as ccxtpro
import numpy as np
from datetime import datetime
import config


class Exchange:
    """
    Обёртка над ccxt.pro для работы с Binance Futures.
    
    Использование:
        exchange = Exchange()
        await exchange.connect()
        candles = await exchange.fetch_candles("BTC/USDT:USDT", "5m")
        await exchange.close()
    """
    
    def __init__(self):
        # Создаём объект биржи
        # 'sandbox': True включает тестовую сеть (если нужно)
        self.exchange = ccxtpro.binanceusdm({
            'apiKey': config.API_KEY or None,
            'secret': config.API_SECRET or None,
            'sandbox': False,             # True = testnet
            'enableRateLimit': True,      # защита от бана за спам запросов
            'options': {
                'defaultType': 'future',  # работаем с фьючерсами
            }
        })
        
        # Кэш свечей: {"BTC/USDT:USDT": {"5m": [...], "1h": [...]}}
        self._candle_cache = {}
        
        # Кэш последних сделок (для анализа дельты buy/sell)
        self._recent_trades = {}
        
        # Флаг работы
        self._running = False
    
    # ── Подключение ──────────────────────────
    
    async def connect(self):
        """
        Загружает информацию о рынках.
        Вызывай один раз при старте бота.
        """
        await self.exchange.load_markets()
        self._running = True
        print(f"[Exchange] Подключено к {config.EXCHANGE_ID}")
        print(f"[Exchange] Доступно {len(self.exchange.markets)} торговых пар")
    
    async def close(self):
        """Закрывает соединение."""
        self._running = False
        await self.exchange.close()
        print("[Exchange] Соединение закрыто")
    
    # ── Получение свечей ─────────────────────
    
    async def fetch_candles(self, symbol: str, timeframe: str,
                            limit: int = 200) -> np.ndarray:
        """
        Загружает последние N свечей.
        
        Аргументы:
            symbol: торговая пара, например "BTC/USDT:USDT"
            timeframe: таймфрейм, например "5m", "1h"
            limit: сколько свечей загрузить (макс ~1000)
        
        Возвращает:
            numpy array формата:
            [[timestamp, open, high, low, close, volume], ...]
            
            Индексы:
            0 = timestamp (время в миллисекундах)
            1 = open  (цена открытия)
            2 = high  (максимум)
            3 = low   (минимум)
            4 = close (цена закрытия)
            5 = volume (объём)
        """
        try:
            ohlcv = await self.exchange.fetch_ohlcv(
                symbol, timeframe, limit=limit
            )
            data = np.array(ohlcv, dtype=float)
            
            # Сохраняем в кэш
            if symbol not in self._candle_cache:
                self._candle_cache[symbol] = {}
            self._candle_cache[symbol][timeframe] = data
            
            return data
            
        except Exception as e:
            print(f"[Exchange] Ошибка загрузки свечей {symbol} {timeframe}: {e}")
            return np.array([])
    
    async def watch_candles(self, symbol: str, timeframe: str) -> np.ndarray:
        """
        Подписка на обновление свечей в реальном времени (WebSocket).
        
        Отличие от fetch_candles:
        - fetch_candles = один запрос, получил данные, всё
        - watch_candles = постоянный поток, каждая новая свеча
                          приходит автоматически
        
        Возвращает последнюю порцию свечей при каждом обновлении.
        """
        try:
            ohlcv = await self.exchange.watch_ohlcv(symbol, timeframe)
            data = np.array(ohlcv, dtype=float)
            
            if symbol not in self._candle_cache:
                self._candle_cache[symbol] = {}
            self._candle_cache[symbol][timeframe] = data
            
            return data
            
        except Exception as e:
            print(f"[Exchange] Ошибка WebSocket свечей {symbol}: {e}")
            return np.array([])
    
    # ── Получение сделок (для дельты объёма) ──
    
    async def watch_trades(self, symbol: str) -> list:
        """
        Подписка на поток сделок (WebSocket).
        
        Каждая сделка содержит:
        - price: цена
        - amount: размер
        - side: 'buy' или 'sell' (кто был тейкером)
        - timestamp: время
        
        Это нужно для расчёта buy/sell дельты:
        если покупатели агрессивнее продавцов → цена скорее пойдёт вверх.
        """
        try:
            trades = await self.exchange.watch_trades(symbol)
            
            if symbol not in self._recent_trades:
                self._recent_trades[symbol] = []
            
            for trade in trades:
                self._recent_trades[symbol].append({
                    'timestamp': trade['timestamp'],
                    'price': trade['price'],
                    'amount': trade['amount'],
                    'side': trade['side'],  # 'buy' or 'sell'
                    'cost': trade['cost'],  # price * amount
                })
            
            # Храним только последние 1000 сделок (экономим память)
            self._recent_trades[symbol] = self._recent_trades[symbol][-1000:]
            
            return trades
            
        except Exception as e:
            print(f"[Exchange] Ошибка WebSocket trades {symbol}: {e}")
            return []
    
    # ── Текущая цена ─────────────────────────
    
    async def get_ticker(self, symbol: str) -> dict:
        """
        Получает текущую цену и базовую информацию.
        
        Возвращает:
        {
            'last': 42000.0,    # последняя цена
            'bid': 41999.5,     # лучшая цена покупки
            'ask': 42000.5,     # лучшая цена продажи
            'volume': 50000.0,  # объём за 24ч
            'change': 1.5,      # изменение за 24ч в %
        }
        """
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return {
                'last': ticker['last'],
                'bid': ticker['bid'],
                'ask': ticker['ask'],
                'volume': ticker['quoteVolume'],  # объём в USDT
                'change': ticker['percentage'],    # % за 24ч
            }
        except Exception as e:
            print(f"[Exchange] Ошибка получения тикера {symbol}: {e}")
            return {}
    
    # ── Вспомогательные методы ────────────────
    
    def get_cached_candles(self, symbol: str, timeframe: str) -> np.ndarray:
        """Возвращает свечи из кэша (без запроса к бирже)."""
        try:
            return self._candle_cache[symbol][timeframe]
        except KeyError:
            return np.array([])
    
    def get_recent_trades(self, symbol: str) -> list:
        """Возвращает последние сделки из кэша."""
        return self._recent_trades.get(symbol, [])
    
    def calculate_buy_sell_delta(self, symbol: str,
                                  last_n: int = 100) -> dict:
        """
        Считает дельту buy/sell за последние N сделок.
        
        Дельта = сумма покупок - сумма продаж (в USDT).
        
        Положительная дельта → покупатели сильнее → бычий сигнал
        Отрицательная дельта → продавцы сильнее → медвежий сигнал
        
        Возвращает:
        {
            'buy_volume': 150000,  # объём покупок в USDT
            'sell_volume': 120000, # объём продаж в USDT
            'delta': 30000,        # разница
            'ratio': 1.25,         # отношение buy/sell
        }
        """
        trades = self._recent_trades.get(symbol, [])[-last_n:]
        
        if not trades:
            return {
                'buy_volume': 0, 'sell_volume': 0,
                'delta': 0, 'ratio': 1.0
            }
        
        buy_vol = sum(t['cost'] for t in trades if t['side'] == 'buy')
        sell_vol = sum(t['cost'] for t in trades if t['side'] == 'sell')
        
        return {
            'buy_volume': buy_vol,
            'sell_volume': sell_vol,
            'delta': buy_vol - sell_vol,
            'ratio': buy_vol / sell_vol if sell_vol > 0 else float('inf'),
        }
