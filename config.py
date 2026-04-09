# config.py — Все настройки бота в одном месте
# ============================================

# ── Binance подключение ──────────────────────
EXCHANGE_ID = "binanceusdm"
API_KEY = ""
API_SECRET = ""

# ── Автоматический отбор монет (Scanner) ─────
SCANNER_TOP_N = 7
SCANNER_MIN_VOLUME_24H = 10_000_000
SCANNER_MAX_SPREAD_PCT = 0.05
SCANNER_IDEAL_VOLATILITY = 3.0
SCANNER_INTERVAL_MINUTES = 60

EXCLUDED_FUNDAMENTAL = {
    "BTC", "ETH", "BNB", "SOL", "XRP",
    "ADA", "AVAX", "DOT", "MATIC", "LINK",
    "TON", "TRX", "NEAR",
}
EXCLUDED_STABLECOINS = {
    "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP",
    "WBTC", "WETH", "STETH", "BTCDOM", "DEFI",
}
WATCHLIST_FALLBACK = [
    "1000PEPE/USDT:USDT", "WIF/USDT:USDT", "DOGE/USDT:USDT",
    "1000BONK/USDT:USDT", "1000FLOKI/USDT:USDT",
]

# ── Мультитаймфрейм (MTF) ───────────────────
# Каждый таймфрейм выполняет свою роль:
#
# SENIOR (1h) — поиск КЛЮЧЕВЫХ уровней.
#   Уровни на часовике — самые сильные. Цена реагирует на них
#   гораздо чаще чем на уровни с 5m. Вес уровня = x3.
#
# MIDDLE (15m) — ПОДТВЕРЖДЕНИЕ направления.
#   Смотрим тренд и объём на 15m. Если на 1h уровень поддержки,
#   а на 15m тренд вниз с сильным объёмом — пропускаем лонг.
#
# WORK (5m) — поиск СИГНАЛА.
#   Здесь ищем конкретный паттерн: bounce или breakout.
#   Уровни с 5m тоже учитываются, но с меньшим весом (x1).
#
# ENTRY (1m) — ТОЧНЫЙ ВХОД.
#   Когда сигнал найден на 5m, ждём подтверждение на 1m:
#   реакция от уровня (wick, поглощение, замедление).
#   Это даёт более точный вход и более короткий стоп.

TF_SENIOR = "1h"    # ключевые уровни
TF_MIDDLE = "15m"   # подтверждение тренда
TF_WORK = "5m"      # поиск сигнала
TF_ENTRY = "1m"     # точный вход

# Вес уровней с каждого таймфрейма при объединении.
# Чем старше ТФ — тем сильнее уровень.
TF_LEVEL_WEIGHTS = {
    "1h": 3,    # уровень с часовика = сила x3
    "15m": 2,   # уровень с 15m = сила x2
    "5m": 1,    # уровень с 5m = сила x1
}

# Сколько свечей загружать для каждого ТФ
TF_CANDLE_LIMITS = {
    "1h": 100,   # 100 часов = ~4 дня
    "15m": 200,  # 200 * 15m = ~2 дня
    "5m": 200,   # 200 * 5m = ~17 часов
    "1m": 100,   # 100 минут = ~1.5 часа
}

# ── Определение уровней ──────────────────────
LEVEL_WINDOW = 10
CLUSTER_ATR_MULT = 0.5
MIN_TOUCHES = 2
ATR_PERIOD = 14

# ── Анализ объёма ────────────────────────────
VOLUME_AVG_PERIOD = 20
VOLUME_SPIKE_MULT = 1.5

# ── Сигналы ──────────────────────────────────
NEAR_LEVEL_ATR_MULT = 0.3
BREAKOUT_CONFIRM_CANDLES = 2
MIN_RISK_REWARD = 2.0

# Для MTF-подтверждения: тренд на 15m должен совпадать
# с направлением сделки. Если не совпадает — пропускаем.
REQUIRE_MIDDLE_TF_CONFIRMATION = True

# Для точного входа: ждём реакцию на 1m перед входом.
# Если False — входим по сигналу с 5m сразу.
REQUIRE_ENTRY_TF_CONFIRMATION = True

# ── Управление позицией ──────────────────────
RISK_PER_TRADE = 0.01
INITIAL_BALANCE = 1000.0
LEVERAGE = 10
MOVE_TO_BREAKEVEN_PCT = 0.5

# ── Интервалы обновления ─────────────────────
UPDATE_INTERVAL = 5

# ── Логирование ──────────────────────────────
LOG_DIR = "logs"
TRADE_LOG_FILE = "trades.json"
TRADE_CSV_FILE = "trades.csv"

# ── BTC-корреляция ───────────────────────────
BTC_CORRELATION_CHECK = True
BTC_DUMP_THRESHOLD = -1.0
BTC_PUMP_THRESHOLD = 1.0
