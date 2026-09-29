# Audi A6 C5 Multi-Source Monitoring Service 🚗

Полноценный production-ready сервис для автоматического мониторинга объявлений о продаже **Audi A6 C5** на автомобильных площадках (**AUTO.RIA**, **OLX**, **RST**), в **Telegram-каналах** и **Instagram-аккаунтах** с фильтрацией по двигателям и мгновенными уведомлениями в Telegram.

---

## 🎯 Критерии поиска

Сервис ищет **строго Audi A6 C5 / 4B (1997–2005)** только со следующими двигателями:
* **1.8T** (1.8 бензин с турбонаддувом)
* **2.4 бензин / газ** (атмосферный 2.4 л)
* **1.9 TDI** (1.9 турбодизель)

Любые другие автомобили и моторы (2.5 TDI, 2.7 Biturbo, 2.8, 3.0, 4.2, Audi A4, A6 C6/C7 и др.) **строго отсекаются**.

---

## 🏗 Архитектура системы

Проект спроектирован по модульному принципу: добавление нового источника данных не требует изменения основной бизнес-логики.

```text
car/
├── .github/workflows/parser.yml   # Автозапуск по расписанию в GitHub Actions
├── config/
│   └── config.yaml                # Параметры поиска, лимиты и списки каналов/аккаунтов
├── src/
│   ├── config.py                  # Pydantic v2 Settings (ENV + config.yaml)
│   ├── main.py                    # Главный CLI-entrypoint
│   ├── runner.py                  # Конвейер запуска: сбор -> фильтр -> дедуп -> Telegram -> БД
│   ├── database/
│   │   ├── connection.py          # Асинхронный движок (PostgreSQL asyncpg / SQLite aiosqlite)
│   │   ├── ddl.py                 # Автоматическая инициализация таблиц и индексов
│   │   └── models.py              # SQLAlchemy 2.0 ORM модели
│   ├── filtering/
│   │   ├── normalizer.py          # Очистка текста, унификация омоглифов (кириллица/латиница)
│   │   ├── negative_rules.py      # Исключение запчастей, разборок, обменов ("обмен на A6")
│   │   ├── model_rules.py         # Проверка кузова C5 / 4B и годов 1997–2005
│   │   ├── engine_rules.py        # Белый/черный список двигателей
│   │   └── engine.py              # Оркестратор фильтрации со статусом NEEDS_REVIEW
│   ├── models/
│   │   ├── listing.py             # Единый контракт Listing (Pydantic v2)
│   │   └── filter_result.py       # Результат фильтрации и причины отклонения
│   ├── notifier/
│   │   ├── templates.py           # Форматирование карточек с эмодзи, ценами и скидками
│   │   └── telegram.py            # Telegram Bot API (фотоальбомы, rate limit, FloodWait)
│   ├── parsers/
│   │   ├── base.py                # Abstract BaseParser с изоляцией ошибок и метриками
│   │   ├── auto_ria.py            # Парсер AUTO.RIA
│   │   ├── olx.py                 # Парсер OLX Ukraine
│   │   ├── rst.py                 # Парсер RST.ua
│   │   ├── telegram.py            # MTProto Telethon клиент для публичных каналов
│   │   └── instagram.py           # Парсер постов и профилей Instagram
│   └── services/
│       └── deduplicator.py        # 3-уровневый алгоритм дедупликации с хэшированием
├── tests/
│   ├── unit/                      # 277 модульных тестов всех компонентов
│   └── e2e/                       # 170 E2E и интеграционных тестов (Tiers 1–4)
├── requirements.txt
├── .env.example
└── README.md
```

---

## ⚡️ 3-уровневая дедупликация

1. **Уровень 1 (Source + ID):** Поиск по паре `(source, source_listing_id)`.
2. **Уровень 2 (Канонический URL):** Нормализация URL без UTM-меток, трекинг-параметров (`igsh`, `fbclid`) и номеров сессий.
3. **Уровень 3 (Контентный Fingerprint):** Неизменяемый хэш SHA-256 по ключевым характеристикам `(AUDI_A6_C5 | год | кузов | мотор | КПП | город | продавец)`. Защищает от перевыставления одного и того же авто перекупщиками под новым ID.
4. **Отслеживание падения цен:** При изменении цены в меньшую сторону генерируется отдельное уведомление `📉 Снижение цены!`.

---

## 📦 Быстрый старт

### 1. Клонирование и установка зависимостей
```bash
git clone git@github.com:max93139/car_parser.git
cd car_parser

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Настройка переменных окружения
Скопируйте пример окружения:
```bash
cp .env.example .env
```
Заполните ключи:
- `DATABASE_URL`: Строка подключения к PostgreSQL (`postgresql+asyncpg://...`) или локальному SQLite (`sqlite+aiosqlite:///car_parser.db`).
- `TELEGRAM_BOT_TOKEN`: Токен бота от `@BotFather`.
- `TELEGRAM_CHAT_ID`: ID вашего чата или канала.
- `TELEGRAM_API_ID` и `TELEGRAM_API_HASH`: (Опционально) Для парсинга постов в Telegram-каналах с my.telegram.org.

### 3. Запуск
```bash
# Тестовый прогон без отправки в Telegram
python -m src.main --dry-run

# Однократный рабочий запуск
python -m src.main --once

# Запуск только одного источника
python -m src.main --source auto_ria
```

---

## 🤖 Запуск в GitHub Actions

В репозитории настроен рабочий процесс `.github/workflows/parser.yml`.

### Расписание:
- Автоматический запуск 3 раза в день: **08:00, 14:00, 20:00 UTC**.
- Ручной запуск в любой момент через **Actions -> Run workflow** (`workflow_dispatch`) с параметрами `dry_run` и `source`.

### Необходимые GitHub Secrets:
Добавьте в настройках репозитория (*Settings -> Secrets and variables -> Actions*):
- `DATABASE_URL` (например, бесплатный PostgreSQL на Supabase, Neon.tech или ElephantSQL)
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_SESSION_STRING` (при использовании чтения каналов)
- `INSTAGRAM_SESSION_COOKIE` (при необходимости)

---

## 🧪 Запуск тестов

В проекте реализована 4-уровневая пирамида тестирования с **100% прохождением** (447 тестов):

```bash
# Все юнит-тесты (277 тестов)
.venv/bin/pytest tests/unit/ -v

# Все E2E тесты (170 тестов)
.venv/bin/pytest tests/e2e/ -v

# Полный прогон всех тестов
.venv/bin/pytest
```
