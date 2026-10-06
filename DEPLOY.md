# 🚀 Інструкція із запуску Telegram-бота 24/7

Цей сервіс дозволяє налаштовувати фільтри для **Audi A6 C5** прямо в Telegram:
- Команда `/settings` або `/filter` відкриває панель із кнопками.
- Можна вмикати/вимикати мотори (`1.8T`, `2.4`, `1.9 TDI`), задавати ціни, КПП, пробіг та шукати авто кнопкою **«🔍 Знайти зараз»**.

---

## Варіант 1. Локальний запуск на комп'ютері (Mac / Linux)

### У терміналі (активне вікно):
```bash
source .venv/bin/activate
python -m src.bot
```

### У фоновому режимі (не закривається при виході з терміналу):
```bash
source .venv/bin/activate
nohup python -m src.bot > bot.log 2>&1 &
```
Щоб перевірити статус: `ps aux | grep src.bot`  
Щоб переглянути логи: `tail -f bot.log`

---

## Варіант 2. Безкоштовний хмарний запуск на Render.com

1. Зареєструйтеся на [render.com](https://render.com) (через GitHub).
2. Натисніть **«New +»** ➡️ **«Background Worker»**.
3. Підключіть ваш репозиторій `car_parser`.
4. Вкажіть параметри:
   * **Name:** `car-parser-bot`
   * **Runtime:** `Python 3` або `Docker`
   * **Build Command:** `pip install -r requirements.txt`
   * **Start Command:** `python -m src.bot`
   * **Instance Type:** `Free`
5. У вкладці **Environment** додайте змінні середовища:
   * `DATABASE_URL` = ваше посилання на Neon PostgreSQL
   * `TELEGRAM_BOT_TOKEN` = токен бота
   * `TELEGRAM_CHAT_ID` = ваш chat ID
   * `TELEGRAM_API_ID` = ваш API ID
   * `TELEGRAM_API_HASH` = ваш API Hash
6. Натисніть **«Create Background Worker»**. Бот працюватиме в хмарі 24/7!

---

## Варіант 3. Запуск через Docker на будь-якому сервері (VPS)

```bash
docker build -t audi_c5_bot .
docker run -d --name audi_bot --restart always --env-file .env audi_c5_bot
```
