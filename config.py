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
# Blacklist убыточных монет — не торгуем их даже если scanner выбрал.
# Основано на статистике сделок (убыток на монете за длительный период).
# Можно пополнять по мере выявления проблемных монет.
COIN_BLACKLIST = {"1000BONK", "1000PEPE"}
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
TAKER_FEE_PCT = 0.04   # Binance futures taker fee (0.04%)
FEE_REBATE_PCT = 0.35  # Возврат комиссии от брокера (35%)

# ── Интервалы обновления ─────────────────────
UPDATE_INTERVAL = 5

# ── Circuit Breaker ───────────────────────────
# После N убыточных сделок подряд — пауза на M минут.
# Защита от серийных потерь при резком изменении рынка.
# Счётчик сбрасывается после любой прибыльной сделки или после срабатывания.
CIRCUIT_BREAKER_ENABLED = True
CIRCUIT_BREAKER_LOSSES = 5
CIRCUIT_BREAKER_PAUSE_MINUTES = 60

# ── Daily Loss Limit ──────────────────────────
# Если дневной убыток превысил DAILY_LOSS_LIMIT_PCT от стартового баланса дня,
# новые сделки не открываются до следующего календарного дня (UTC).
# Существующие сделки продолжают управляться (стопы/тейки).
DAILY_LOSS_LIMIT_ENABLED = True
DAILY_LOSS_LIMIT_PCT = 3.0

# ── ATR Filter ────────────────────────────────
# Пропускает сигналы если волатильность экстремальная:
#   - ATR > ATR_MAX_PCT% от цены → рынок в хаосе (новости, паника), стопы рвутся
#   - ATR < ATR_MIN_PCT% от цены → рынок плоский, движения меньше спреда+комиссий
# ATR берётся с рабочего ТФ (5m) из levels["atr"].
ATR_FILTER_ENABLED = False
ATR_MAX_PCT = 3.0
ATR_MIN_PCT = 0.1

# ── Correlation Filter ────────────────────────
# Ограничивает сколько сделок можно открыть в одном направлении одновременно.
# Альткоины сильно коррелированы — если открыто 3 longs и BTC дампит,
# все 3 идут в минус одновременно. Лимит защищает от каскадных потерь.
#
# MAX_CORRELATED_TRADES = 2 означает: максимум 2 longs ИЛИ 2 shorts активны.
# Общий лимит max_open_trades из PaperTrader работает независимо.
CORRELATION_FILTER_ENABLED = False
MAX_CORRELATED_TRADES = 2

# ── Session Filter ────────────────────────────
# Торговля только в активные часы (UTC).
# Формат: список кортежей (start_hour, end_hour), end НЕ включается.
# По умолчанию (12, 20) UTC = 15:00-23:00 МСК — overlap US+EU сессий.
# Можно задать несколько окон или окно через полночь, например (22, 4).
# Уже открытые сделки продолжают управляться независимо от сессии
# (только блокируется ОТКРЫТИЕ новых).
SESSION_FILTER_ENABLED = False
TRADING_SESSIONS = [(12, 20)]

# ── Логирование ──────────────────────────────
LOG_DIR = "logs"
TRADE_LOG_FILE = "trades.json"
TRADE_CSV_FILE = "trades.csv"

# ── BTC-корреляция ───────────────────────────
BTC_CORRELATION_CHECK = True
BTC_DUMP_THRESHOLD = -1.0
BTC_PUMP_THRESHOLD = 1.0

# ── Pattern Filter ────────────────────────────
# По умолчанию ВСЕ паттерны разрешены (PATTERN_FILTER_ENABLED = False).
# Если включить, то будут торговаться ТОЛЬКО сигналы чей reason содержит
# одну из подстрок в ENABLED_PATTERNS.
#
# Доступные паттерны (reason в сигнале):
#   "5m bullish candle at support"         — бычья свеча у поддержки (лонг)
#   "5m momentum slowdown at support"      — замедление у поддержки (лонг)
#   "5m rejection wick at support"         — проколы у поддержки (лонг)
#   "5m bearish candle at resistance"      — медвежья свеча у сопротивления (шорт)
#   "5m momentum slowdown at resistance"   — замедление у сопротивления (шорт)
#   "5m rejection wick at resistance"      — проколы у сопротивления (шорт)
#   "breakout"                              — пробой уровня (breakout above/below)
PATTERN_FILTER_ENABLED = False
ENABLED_PATTERNS = {
    "5m bullish candle at support",
    "5m momentum slowdown at support",
    "5m rejection wick at support",
    "5m bearish candle at resistance",
    "5m momentum slowdown at resistance",
    "5m rejection wick at resistance",
    "breakout",
}

# ── Market Regime Filter ──────────────────────
# Определяет режим рынка по BTC 1h:
#   BULL  — рынок растёт  → блокируем шорты
#   BEAR  — рынок падает  → блокируем лонги
#   RANGE — боковик       → разрешаем оба направления
# Это экономит убытки от торговли против тренда.
REGIME_FILTER_ENABLED = True
REGIME_BLOCK_COUNTER_TREND = True

# Параметры детекции (можно тонко настроить):
REGIME_SMA_FAST = 20              # период быстрой SMA
REGIME_SMA_SLOW = 50              # период медленной SMA
REGIME_SLOPE_LOOKBACK = 10        # за сколько свечей считать наклон
REGIME_SLOPE_THRESHOLD_PCT = 0.5  # минимальный наклон в % для тренда
