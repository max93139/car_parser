# Original User Request

## 2026-09-28T20:33:22Z

# Teamwork Project Prompt — Audi A6 C5 Multi-Source Monitoring Service

> Status: Launched
> Goal: Full multi-agent implementation of the car monitoring service
> Requested team: [none — teamwork routes from the description]

Разработка полноценного production-ready сервиса автоматического мониторинга объявлений о продаже Audi A6 C5 (1.8T, 2.4 газ/бензин, 1.9 TDI) по автомобильным сайтам (AUTO.RIA, OLX, RST), Telegram-каналам и Instagram-аккаунтам с отправкой новых предложений в Telegram.

Working directory: /Users/mac/Desktop/car
Repository: git@github.com:max93139/car_parser.git

## Requirements

### R1. Unified Listing Model & Extensible Architecture
- Единая модель `Listing` (Pydantic v2):
  - `source`: str (auto_ria, olx, rst, telegram, instagram)
  - `source_id`: str
  - `url`: str
  - `title`: str
  - `description`: Optional[str]
  - `brand`: str = "Audi"
  - `model`: str = "A6"
  - `generation`: str = "C5"
  - `year`: Optional[int]
  - `price`: Optional[float]
  - `currency`: Optional[str]
  - `mileage`: Optional[int]
  - `engine`: Optional[str]
  - `fuel_type`: Optional[str]
  - `transmission`: Optional[str]
  - `location`: Optional[str]
  - `seller`: Optional[str]
  - `images`: list[str]
  - `published_at`: Optional[datetime]
  - `first_seen_at`: datetime
- Базовый класс `BaseParser` (ABC) с методом `async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]`.
- Изолированные реализации парсеров:
  - `auto_ria`: парсинг поиска и карточек AUTO.RIA.
  - `olx`: парсинг категории легковых авто Audi A6 C5 на OLX.
  - `rst`: парсинг поиска RST.ua.
  - `telegram`: MTProto клиент (Telethon) для мониторинга указанных каналов из конфигурации.
  - `instagram`: парсер постов указанных аккаунтов (текст, хэштеги, фото).
- Ошибка любого парсера логируется и изолируется, не останавливая работу остальных.

### R2. Strict & Robust Filtering Logic (Audi A6 C5 Only)
- Нормализация текста: удаление мусора, приведение регистра, унификация сходных кириллических и латинских символов (А/A, С/C, Т/T, etc.).
- Защита от контекстных ложных срабатываний: отсекать "обмен на Audi", "куплю", "разборка", "на запчасти", "донор".
- Определение кузова / поколения:
  - Строго Audi A6 C5 / 4B (включая синонимы "Audi A6 C5", "A6 C5", "A6 4B", "Ауди А6 С5", "Ауди А6 4B").
  - Диапазон годов выпуска: 1997–2005.
  - Исключать: A4, A8, A5, C4, C6, C7, Allroad других поколений.
- Определение двигателя:
  - Строго один из трех:
    1. 1.8T (1.8 бензин с турбиной: "1.8T", "1.8 Turbo", "1.8 турбо", "1.8 т").
    2. 2.4 (бензин или газ/бензин: "2.4", "2.4 бензин", "2.4 газ", "2.4 газ/бензин", "2.4 ГБО").
    3. 1.9 TDI (дизель: "1.9 TDI", "1.9 тди", "1.9 дизель", "1.9 diesel").
  - Строго отсекать любые другие двигатели (2.5 TDI, 2.7T, 2.8, 3.0, 4.2 и т.д.).
- Если параметры не определены со 100% уверенностью — маркировать как `NEEDS_REVIEW` (не отправлять как гарантированное).

### R3. PostgreSQL Database & 3-Level Deduplication
- PostgreSQL таблицы:
  - `sources`
  - `listings`
  - `listing_versions`
  - `parser_runs`
- Приоритет дедупликации:
  1. `(source_id, source_listing_id)`
  2. Нормализованный канонический URL
  3. Контентный fingerprint: SHA-256 (год + приблизительный пробег + объем/мотор + город + продавец).
- Отслеживание даты обнаружения (`first_seen_at`), последней проверки (`last_checked_at`), отправки в Telegram (`is_sent_to_telegram`).

### R4. Telegram Notification Engine
- Telegram бот для отправки уведомлений с поддержкой медиагрупп (фотогалерея авто).
- Форматированное сообщение (цена, год, двигатель, КПП, пробег, город, источник, ссылка на оригинал).
- Rate limiter и повтор при ошибках 429/FloodWait.

### R5. GitHub Actions & Configuration
- `config/config.yaml` для управления поисковыми параметрами и списками источников/каналов/аккаунтов.
- `.github/workflows/parser.yml` с запуском по cron (08:00, 14:00, 20:00 UTC) и `workflow_dispatch`.
- Поддержка GitHub Secrets: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, etc.
- Итоговый сводный лог выполнения по каждому источнику в консоль и в `parser_runs`.
- Подключение git remote `git@github.com:max93139/car_parser.git`.

## Acceptance Criteria
- [ ] Все модули согласно архитектуре созданы и протестированы модульными тестами (`pytest`).
- [ ] Фильтр безошибочно валидирует все варианты написания C5 и целевых двигателей, отсекая все остальные.
- [ ] Дедупликатор гарантирует отсутствие повторной отправки одинаковых объявлений.
- [ ] Проект готов к пушу в git-репозиторий и запуску в GitHub Actions.
