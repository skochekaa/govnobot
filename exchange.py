# exchange.py — Подключение к Binance Futures через ccxt.pro
# ============================================================
#
# Архитектура:
# 1. При старте — fetch_ohlcv (REST) загружает историю один раз
# 2. Дальше — watch_ohlcv (WebSocket) обновляет кэш в реальном времени
# 3. Торговый цикл читает ТОЛЬКО из кэша (мгновенно, 0 задержки)
#
# Это критично для скальпинга: REST-запрос = 100-300мс задержки,
# WebSocket = данные приходят сами, как только появляются на бирже.

import asyncio
import ccxt.pro as ccxtpro
import numpy as np
import config
from log_setup import setup_logger

log = setup_logger("exchange")


class Exchange:
    def __init__(self):
        self.exchange = ccxtpro.binanceusdm({
            'apiKey': config.API_KEY or None,
            'secret': config.API_SECRET or None,
            'sandbox': False,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'},
        })

        # Кэш свечей: {symbol: {tf: np.ndarray}}
        # Обновляется автоматически через WebSocket
        self._candle_cache = {}

        # Кэш сделок: {symbol: [trade_dicts]}
        self._recent_trades = {}

        # Кэш тикеров: {symbol: ticker_dict}
        self._ticker_cache = {}

        # Фоновые задачи (WebSocket стримы)
        self._stream_tasks = []
        self._running = False

    # ── Подключение ──────────────────────────

    async def connect(self):
        await self.exchange.load_markets()
        self._running = True
        log.info("Подключено к %s (%d пар)", config.EXCHANGE_ID, len(self.exchange.markets))

    def validate_symbol(self, symbol: str) -> bool:
        """Проверяет, существует ли символ на бирже."""
        return symbol in self.exchange.markets

    def filter_valid_symbols(self, symbols: list[str]) -> list[str]:
        """Возвращает только валидные символы, логирует невалидные."""
        valid = []
        for s in symbols:
            if self.validate_symbol(s):
                valid.append(s)
            else:
                log.warning("Символ %s не найден на бирже, пропускаем", s)
        return valid

    async def close(self):
        self._running = False
        await self.stop_streams()
        await self.exchange.close()
        log.info("Соединение закрыто, стримы остановлены")

    # ── Начальная загрузка истории (REST, один раз) ──

    async def preload_history(self, symbols: list[str],
                                timeframes: list[str]):
        """
        Загружает историческре свечи через REST API.
        Вызывается ОДИН РАЗ при старте, до включения WebSocket.
        """
        symbols = self.filter_valid_symbols(symbols)
        log.info("Загрузка истории для %d монет × %d ТФ...",
                 len(symbols), len(timeframes))

        for symbol in symbols:
            self._candle_cache.setdefault(symbol, {})
            for tf in timeframes:
                limit = config.TF_CANDLE_LIMITS.get(tf, 200)
                try:
                    ohlcv = await self.exchange.fetch_ohlcv(
                        symbol, tf, limit=limit
                    )
                    self._candle_cache[symbol][tf] = np.array(ohlcv, dtype=float)
                    log.debug("Загружено %s %s: %d свечей", symbol, tf, len(ohlcv))
                except Exception as e:
                    log.error("Ошибка загрузки %s %s: %s", symbol, tf, e)

        # Также загружаем BTC для корреляции
        btc = "BTC/USDT:USDT"
        self._candle_cache.setdefault(btc, {})
        try:
            ohlcv = await self.exchange.fetch_ohlcv(btc, config.TF_WORK, limit=200)
            self._candle_cache[btc][config.TF_WORK] = np.array(ohlcv, dtype=float)
        except Exception as e:
            log.error("Ошибка загрузки BTC: %s", e)

        log.info("История загружена")

    # ── Запуск WebSocket стримов (фоновые задачи) ──

    async def start_streams(self, symbols: list[str],
                              timeframes: list[str]):
        """
        Запускает фоновые WebSocket-стримы.

        Каждый стрим — отдельная asyncio задача, которая
        бесконечно слушает обновления с биржи и обновляет кэш.

        Для 7 монет × 4 ТФ = 28 стримов свечей + 7 стримов сделок
        + 1 стрим тикеров.
        """
        await self.stop_streams()

        symbols = self.filter_valid_symbols(symbols)
        all_symbols = list(set(symbols + ["BTC/USDT:USDT"]))

        for symbol in all_symbols:
            for tf in timeframes:
                task = asyncio.create_task(
                    self._stream_candles(symbol, tf)
                )
                self._stream_tasks.append(task)

            # Стрим сделок (для buy/sell дельты)
            task = asyncio.create_task(
                self._stream_trades(symbol)
            )
            self._stream_tasks.append(task)

        # Стрим тикеров (текущие цены) — по одному на символ
        for symbol in all_symbols:
            task = asyncio.create_task(self._stream_single_ticker(symbol))
            self._stream_tasks.append(task)

        total = len(self._stream_tasks)
        log.info("Запущено %d WebSocket стримов (%d монет × %d ТФ + trades + tickers)",
                 total, len(all_symbols), len(timeframes))

    async def stop_streams(self):
        """Останавливает все фоновые стримы и ждёт их завершения."""
        for task in self._stream_tasks:
            task.cancel()
        if self._stream_tasks:
            await asyncio.gather(*self._stream_tasks, return_exceptions=True)
        self._stream_tasks = []

    async def restart_streams(self, symbols: list[str],
                                timeframes: list[str]):
        """
        Перезапускает стримы с новым списком монет.
        Вызывается при пересканировании.
        """
        log.info("Перезапуск стримов для нового watchlist...")
        await self.stop_streams()
        await self.preload_history(symbols, timeframes)
        await self.start_streams(symbols, timeframes)

    # ── Фоновые стримы (бесконечные циклы) ───

    async def _stream_candles(self, symbol: str, tf: str):
        """
        Фоновый цикл: слушает обновления свечей через WebSocket.

        watch_ohlcv возвращает массив свечей при каждом обновлении.
        Мы берём последнюю свечу и обновляем/добавляем её в кэш.
        """
        while self._running:
            try:
                ohlcv_list = await self.exchange.watch_ohlcv(symbol, tf)

                self._candle_cache.setdefault(symbol, {})
                existing = self._candle_cache[symbol].get(tf)

                if existing is not None and len(existing) > 0:
                    # Обновляем кэш новыми данными
                    new_data = np.array(ohlcv_list, dtype=float)
                    self._candle_cache[symbol][tf] = self._merge_candles(
                        existing, new_data
                    )
                else:
                    self._candle_cache[symbol][tf] = np.array(
                        ohlcv_list, dtype=float
                    )

            except asyncio.CancelledError:
                return
            except Exception as e:
                log.debug("WS candles %s %s: %s", symbol, tf, e)
                await asyncio.sleep(1)

    async def _stream_trades(self, symbol: str):
        """
        Фоновый цикл: слушает поток сделок через WebSocket.
        Нужен для расчёта buy/sell дельты.
        """
        while self._running:
            try:
                trades = await self.exchange.watch_trades(symbol)
                self._recent_trades.setdefault(symbol, [])

                for trade in trades:
                    self._recent_trades[symbol].append({
                        'timestamp': trade['timestamp'],
                        'price': trade['price'],
                        'amount': trade['amount'],
                        'side': trade['side'],
                        'cost': trade['cost'],
                    })

                # Храним последние 1000 сделок
                self._recent_trades[symbol] = self._recent_trades[symbol][-1000:]

            except asyncio.CancelledError:
                return
            except Exception as e:
                log.debug("WS trades %s: %s", symbol, e)
                await asyncio.sleep(1)

    async def _stream_single_ticker(self, symbol: str):
        """Фоновый цикл: слушает обновления цены одного символа."""
        while self._running:
            try:
                ticker = await self.exchange.watch_ticker(symbol)
                self._ticker_cache[symbol] = {
                    'last': ticker['last'],
                    'bid': ticker['bid'],
                    'ask': ticker['ask'],
                    'volume': ticker.get('quoteVolume', 0),
                    'change': ticker.get('percentage', 0),
                    'timestamp': ticker.get('timestamp', 0),
                }
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.debug("WS ticker %s: %s", symbol, e)
                await asyncio.sleep(5)

    # ── Чтение из кэша (МГНОВЕННО, без запросов) ──

    def get_candles(self, symbol: str, tf: str) -> np.ndarray:
        """
        Возвращает свечи из кэша.
        Вызывается торговым циклом — никаких API-запросов.
        """
        try:
            return self._candle_cache[symbol][tf]
        except KeyError:
            return np.array([])

    def get_price(self, symbol: str) -> float | None:
        """Возвращает последнюю цену из кэша тикеров."""
        ticker = self._ticker_cache.get(symbol)
        if ticker:
            return ticker['last']
        # Фолбэк: цена из последней свечи рабочего ТФ
        candles = self.get_candles(symbol, config.TF_WORK)
        if len(candles) > 0:
            return float(candles[-1, 4])
        return None

    def get_all_prices(self, symbols: list[str]) -> dict[str, float]:
        """Возвращает текущие цены для всех символов из кэша."""
        prices = {}
        for symbol in symbols:
            price = self.get_price(symbol)
            if price is not None:
                prices[symbol] = price
        # BTC всегда нужен
        btc_price = self.get_price("BTC/USDT:USDT")
        if btc_price:
            prices["BTC/USDT:USDT"] = btc_price
        return prices

    def calculate_buy_sell_delta(self, symbol: str, last_n: int = 100) -> dict:
        """Дельта buy/sell за последние N сделок."""
        trades = self._recent_trades.get(symbol, [])[-last_n:]
        if not trades:
            return {'buy_volume': 0, 'sell_volume': 0, 'delta': 0, 'ratio': 1.0}

        buy_vol = sum(t['cost'] for t in trades if t['side'] == 'buy')
        sell_vol = sum(t['cost'] for t in trades if t['side'] == 'sell')
        return {
            'buy_volume': buy_vol,
            'sell_volume': sell_vol,
            'delta': buy_vol - sell_vol,
            'ratio': buy_vol / sell_vol if sell_vol > 0 else float('inf'),
        }

    # ── REST-запросы (для сканера, разовые) ──

    async def fetch_all_tickers(self) -> dict:
        """Загружает все тикеры через REST (для сканера)."""
        try:
            return await self.exchange.fetch_tickers()
        except Exception as e:
            log.error("Ошибка загрузки тикеров: %s", e)
            return {}

    # ── Вспомогательные ──────────────────────

    @staticmethod
    def _merge_candles(existing: np.ndarray, new_data: np.ndarray) -> np.ndarray:
        """
        Объединяет историю (REST) с обновлениями (WebSocket).

        Логика:
        - Если новая свеча имеет тот же timestamp что последняя
          в истории — обновляем (свеча ещё формируется)
        - Если timestamp новый — добавляем
        - Отрезаем старые, чтобы не раздувать память
        """
        if len(new_data) == 0:
            return existing
        if len(existing) == 0:
            return new_data

        result = existing.copy()

        for row in new_data:
            ts = row[0]
            if ts == result[-1, 0]:
                # Обновляем текущую (формирующуюся) свечу
                result[-1] = row
            elif ts > result[-1, 0]:
                # Новая свеча — добавляем
                result = np.vstack([result, row])

        # Ограничиваем размер (последние 500 свечей макс)
        if len(result) > 500:
            result = result[-500:]

        return result
