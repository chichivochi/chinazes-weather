import os
import logging
import requests
from datetime import time as dtime
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---- ЛОГИ (видно в Render) ----
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ---- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ----
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEATHER_KEY = os.getenv("OPENWEATHER_API_KEY")

# Часовой пояс и время автосообщения
TZ = ZoneInfo("Europe/Prague")
SEND_HOUR = 7  # 07:00

# ---- ПОГОДА ----
def fetch_weather(city: str):
    """Возвращает словарь с погодой или None, если город не найден."""
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": city, "appid": WEATHER_KEY, "units": "metric", "lang": "ru"}
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("cod") != 200:
            return None
        return {
            "name": data["name"],
            "temp": float(data["main"]["temp"]),
            "feels": float(data["main"]["feels_like"]),
            "desc": str(data["weather"][0]["description"]).capitalize(),
        }
    except Exception:
        return None

def clothing_advice(temp: float, feels: float, desc: str) -> str:
    """Простая логика рекомендаций по одежде."""
    d = desc.lower()
    lines = []

    # Базово по ощущаемой температуре
    if feels < -5:
        lines.append("Очень холодно 🥶: тёплая куртка/пуховик, шапка, перчатки, шарф.")
    elif feels < 5:
        lines.append("Холодно ❄️: тёплая куртка, шапка, перчатки.")
    elif feels < 12:
        lines.append("Прохладно 🧥: лёгкая куртка/кофта.")
    elif feels < 20:
        lines.append("Умеренно 🌤: футболка + лёгкая накидка по желанию.")
    else:
        lines.append("Тепло ☀️: лёгкая одежда, пейте воду.")

    # Доп. условия
    if "дожд" in d or "морос" in d:
        lines.append("Возьми зонт ☔️ или дождевик.")
    if "снег" in d:
        lines.append("Незаменима непромокаемая обувь и перчатки 🧤.")
    if "гроза" in d:
        lines.append("Избегай открытых пространств и высоких деревьев 🌩.")
    if "ветер" in d:
        lines.append("Ветрозащитная куртка пригодится 🌬.")

    return "\n".join(lines)

def format_report(city: str) -> str:
    w = fetch_weather(city)
    if not w:
        return "❌ Не удалось получить погоду. Проверь название города."
    tips = clothing_advice(w["temp"], w["feels"], w["desc"])
    return (
        f"🌤 Погода в {w['name']}:\n"
        f"Температура: {round(w['temp'])}°C (ощущается {round(w['feels'])}°C)\n"
        f"Описание: {w['desc']}\n\n"
        f"👕 Советы по одежде:\n{tips}"
    )

# ---- ВСПОМОГАТЕЛЬНОЕ: хранение города в job_queue ----
def get_saved_city(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> str | None:
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    if not jobs:
        return None
    # city хранится в job.data
    return jobs[0].data

# ---- КОМАНДЫ ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [["/now", "/today", "/setcity Praha"]]
    await update.message.reply_text(
        "Привет! Я про погоду и одежду. "
        "Задай город: /setcity <город>\n"
        "Кнопки ниже помогут 👇",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

async def setcity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Напиши так: /setcity Praha")
        return
    city = " ".join(context.args)
    chat_id = update.effective_chat.id

    # удалим старую задачу (если была) и создадим новую
    for j in context.job_queue.get_jobs_by_name(str(chat_id)):
        j.schedule_removal()

    context.job_queue.run_daily(
        callback=send_daily,
        time=dtime(hour=SEND_HOUR, minute=0, tzinfo=TZ),
        chat_id=chat_id,
        data=city,              # тут хранится выбранный город
        name=str(chat_id),
        replace_existing=True,
    )
    await update.message.reply_text(
        f"Город сохранён: {city}\n"
        f"Теперь каждый день в {SEND_HOUR:02d}:00 пришлю прогноз и советы."
    )

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    city = get_saved_city(context, chat_id)
    if not city:
        await update.message.reply_text("Сначала задай город: /setcity <город>")
        return
    await update.message.reply_text(format_report(city))

async def now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # короткая команда — то же, что /today
    await today(update, context)

# ---- ДНЕВНАЯ РАССЫЛКА ----
async def send_daily(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    city = context.job.data
    await context.bot.send_message(chat_id=chat_id, text=format_report(city))

# ---- ЗАПУСК ----
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setcity", setcity))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("now", now))

    print("Бот запущен ✅")
    app.run_polling()

if __name__ == "__main__":
    main()
