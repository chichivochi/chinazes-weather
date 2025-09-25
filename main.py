import os
import logging
import requests
from datetime import time as dtime
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---------- ЛОГИ ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("weather-bot")

# ---------- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ----------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEATHER_KEY = os.getenv("OPENWEATHER_API_KEY")

TZ = ZoneInfo("Europe/Prague")
SEND_HOUR = 7  # 07:00 местного времени

# ---------- ПОГОДА ----------
def fetch_weather(city: str):
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
    except Exception as e:
        log.exception("fetch_weather error: %s", e)
        return None

def clothing_advice(temp: float, feels: float, desc: str) -> str:
    d = desc.lower()
    lines = []
    if feels < -5:
        lines.append("Очень холодно 🥶: тёплый пуховик, шапка, перчатки, шарф.")
    elif feels < 5:
        lines.append("Холодно ❄️: тёплая куртка, шапка, перчатки.")
    elif feels < 12:
        lines.append("Прохладно 🧥: лёгкая куртка/кофта.")
    elif feels < 20:
        lines.append("Умеренно 🌤: футболка + лёгкая накидка по желанию.")
    else:
        lines.append("Тепло ☀️: лёгкая одежда, пейте воду.")
    if "дожд" in d or "морос" in d:
        lines.append("Возьми зонт ☔️.")
    if "снег" in d:
        lines.append("Непромокаемая обувь и перчатки 🧤.")
    if "ветер" in d:
        lines.append("Ветрозащитная куртка пригодится 🌬.")
    if "гроза" in d:
        lines.append("Избегай открытых мест и высоких деревьев 🌩.")
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

# ---------- КОМАНДЫ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [["/now", "/today", "/setcity Praha"]]
    await update.message.reply_text(
        "Привет! Я про погоду и одежду.\n"
        "Сначала задай город: /setcity <город>\n"
        "Кнопки ниже помогут 👇",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
    )

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = context.chat_data.get("city")
    if not city:
        await update.message.reply_text("Сначала задай город: /setcity <город>")
        return
    await update.message.reply_text(format_report(city))

async def now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await today(update, context)

async def setcity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            await update.message.reply_text("Напиши так: /setcity Praha")
            return
        city = " ".join(context.args).strip()
        chat_id = update.effective_chat.id

        # 1) сохраняем город «на всякий» (его видят /now и /today)
        context.chat_data["city"] = city

        # 2) снимаем старую ежедневную задачу (если была)
        name = f"daily-{chat_id}"
        for j in context.job_queue.get_jobs_by_name(name):
            j.schedule_removal()

        # 3) создаём новую ежедневную задачу на 07:00
        context.job_queue.run_daily(
            callback=send_daily,
            time=dtime(hour=SEND_HOUR, minute=0, tzinfo=TZ),
            chat_id=chat_id,
            data=city,
            name=name,
        )

        await update.message.reply_text(
            f"✅ Город сохранён: {city}\n"
            f"Теперь каждый день в {SEND_HOUR:02d}:00 пришлю прогноз и советы."
        )
        log.info("City set for chat %s: %s", chat_id, city)
    except Exception as e:
        log.exception("setcity error: %s", e)
        await update.message.reply_text("⚠️ Ошибка при сохранении города. Попробуй ещё раз.")

# ---------- ДНЕВНАЯ РАССЫЛКА ----------
async def send_daily(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    city = context.job.data or "Praha"
    try:
        await context.bot.send_message(chat_id=chat_id, text=format_report(city))
    except Exception as e:
        log.exception("send_daily error: %s", e)

# ---------- ЗАПУСК ----------
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
