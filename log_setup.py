# log_setup.py — Настройка логирования для всего бота
# ====================================================
#
# Вместо print() используем модуль logging.
# Преимущества:
# - Всё пишется и в терминал, И в файл одновременно
# - Каждая строка с таймстампом и уровнем (INFO/WARNING/ERROR)
# - Можно фильтровать по уровню (убрать DEBUG, оставить только ошибки)
# - Профессиональный стандарт

import logging
import os
from datetime import datetime
import config


def setup_logger(name: str = "bot") -> logging.Logger:
    """
    Создаёт и настраивает логгер.
    
    Логи пишутся:
    1. В терминал (stdout) — видишь в реальном времени
    2. В файл logs/bot_YYYY-MM-DD.log — сохраняется на диск
    
    Уровни:
    - DEBUG: детали для отладки (много текста)
    - INFO: основные события (сигналы, сделки)
    - WARNING: предупреждения (мало данных, пропуск)
    - ERROR: ошибки (не критичные, бот продолжает)
    - CRITICAL: фатальные ошибки (бот остановился)
    """
    logger = logging.getLogger(name)
    
    # Если логгер уже настроен — не дублируем хэндлеры
    if logger.handlers:
        return logger
    
    logger.setLevel(logging.DEBUG)
    
    # Формат: [время] УРОВЕНЬ | модуль | сообщение
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s | %(name)-12s | %(message)s",
        datefmt="%H:%M:%S"
    )
    
    # ── Хэндлер 1: Терминал ──
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)  # в терминал — INFO и выше
    console.setFormatter(fmt)
    logger.addHandler(console)
    
    # ── Хэндлер 2: Файл ──
    os.makedirs(config.LOG_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    file_handler = logging.FileHandler(
        os.path.join(config.LOG_DIR, f"bot_{today}.log"),
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)  # в файл — всё, включая DEBUG
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    
    return logger
