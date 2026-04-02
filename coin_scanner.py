# coin_scanner.py — Автоматический отбор монет для скальпинга
# ============================================================
# Сканирует ВСЕ фьючерсные пары на Binance (~600+)
# и выбирает лучшие альткоины по: объёму, спреду, волатильности.
# Фундаментальные монеты (BTC, ETH, SOL...) исключаются.

import time
import config
from log_setup import setup_logger

log = setup_logger("scanner")


class CoinScanner:
    def __init__(self, exchange):
        self.exchange = exchange
        self.current_watchlist = []
        self.last_scan_time = 0
        self.scan_results = []

    async def scan(self) -> list[str]:
        log.info("Сканирование рынка...")

        all_tickers = await self._fetch_all_tickers()

        if not all_tickers:
            log.warning("Не удалось получить тикеры, используем запасной список")
            return config.WATCHLIST_FALLBACK

        log.info("Найдено %d фьючерсных пар", len(all_tickers))

        filtered = self._filter_pairs(all_tickers)
        log.info("После фильтрации: %d пар", len(filtered))

        scored = self._score_pairs(filtered)

        top_n = config.SCANNER_TOP_N
        best = scored[:top_n]

        watchlist = [coin["symbol"] for coin in best]

        self.current_watchlist = watchlist
        self.scan_results = best
        self.last_scan_time = time.time()

        log.info("Отобрано %d монет:", len(watchlist))
        log.info("%-20s %12s %8s %8s %6s", "Монета", "Объём 24ч", "Спред", "Волат.", "Скор")
        for coin in best:
            vol_m = coin['volume_24h'] / 1_000_000
            log.info(
                "%-20s %9.1fM %7.3f%% %7.2f%% %6.1f",
                coin['symbol'], vol_m, coin['spread_pct'],
                coin['volatility'], coin['score']
            )

        return watchlist

    async def should_rescan(self) -> bool:
        elapsed = time.time() - self.last_scan_time
        return elapsed >= config.SCANNER_INTERVAL_MINUTES * 60

    async def _fetch_all_tickers(self) -> list[dict]:
        try:
            raw_tickers = await self.exchange.fetch_all_tickers()
            tickers = []
            for symbol, data in raw_tickers.items():
                if not symbol.endswith(":USDT"):
                    continue
                if (data.get('last') and data.get('bid') and
                        data.get('ask') and data.get('quoteVolume')):
                    tickers.append({
                        "symbol": symbol,
                        "last": data["last"],
                        "bid": data["bid"],
                        "ask": data["ask"],
                        "volume_24h": data["quoteVolume"],
                        "change_24h": data.get("percentage", 0) or 0,
                    })
            return tickers
        except Exception as e:
            log.error("Ошибка загрузки тикеров: %s", e)
            return []

    def _filter_pairs(self, tickers: list[dict]) -> list[dict]:
        # Объединяем все исключения
        excluded = config.EXCLUDED_FUNDAMENTAL | config.EXCLUDED_STABLECOINS
        filtered = []

        for t in tickers:
            base = t["symbol"].split("/")[0]

            # Исключаем фундаментальные + стейблкоины
            if base in excluded:
                continue

            if t["volume_24h"] < config.SCANNER_MIN_VOLUME_24H:
                continue

            if t["ask"] > 0 and t["bid"] > 0:
                spread_pct = (t["ask"] - t["bid"]) / t["last"] * 100
                t["spread_pct"] = spread_pct
                if spread_pct > config.SCANNER_MAX_SPREAD_PCT:
                    continue
            else:
                continue

            t["volatility"] = abs(t["change_24h"])
            filtered.append(t)

        return filtered

    def _score_pairs(self, pairs: list[dict]) -> list[dict]:
        if not pairs:
            return []

        volumes = [p["volume_24h"] for p in pairs]
        max_vol = max(volumes) if volumes else 1
        spreads = [p["spread_pct"] for p in pairs]
        max_spread = max(spreads) if spreads else 1

        for p in pairs:
            vol_score = (p["volume_24h"] / max_vol) * 100
            spread_score = (1 - p["spread_pct"] / max_spread) * 100 if max_spread > 0 else 50
            vol_pct = p["volatility"]
            ideal_vol = config.SCANNER_IDEAL_VOLATILITY
            vol_penalty = abs(vol_pct - ideal_vol) / ideal_vol
            volatility_score = max(0, 100 - vol_penalty * 50)

            p["score"] = round(
                vol_score * 0.40 + spread_score * 0.30 + volatility_score * 0.30, 1
            )

        pairs.sort(key=lambda x: x["score"], reverse=True)
        return pairs
