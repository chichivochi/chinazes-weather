import os
import datetime as dt
import requests
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEATHER_KEY = os.getenv("OPENWEATHER_API_KEY")
TZ = "Europe/Prague"
SEND_HOUR = 7

# --- Получение погоды ---
def get_weather(city: str) -> str:
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_KEY}&units=metric&lang=ru"
    res = requests.get(url).json()

    if res.get("cod") != 200:
        return "❌ Город не найден."

    temp = res["main"]["temp"]
    feels = res["main"]["feels_like"]
    desc = res["weather"][0]["description"].capitalize()

    # совет по одежде
    if temp < 0:
        advice = "Очень холодно 🧥 Надень тёплую куртку и шапку."
    elif temp < 10:
        advice = "Прохладно 🧣 Рекомендую куртку или свитер."
    elif temp < 20:
        advice = "Комфортно 👕 Подойдёт лёгкая куртка или худи."
    else:
        advice = "Тепло ☀️ Отлично подойдёт футболка."

    return f"🌤 Погода в {city}:\nТемпература: {temp}°C (ощущается {feels}°C)\n{desc}\n👕 Совет: {advice}"

# --- Команды ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Напиши /weather <город> или /now <город>.")

async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Напиши город, например: /weather Praha")
        return
    city = " ".join(context.args)
    await update.message.reply_text(get_weather(city))

async def now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Напиши город, например: /now Praha")
        return
    city = " ".join(context.args)
    await update.message.reply_text(get_weather(city))

# --- Отправка каждый день в 7 утра ---
async def send_daily(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    city = context.job.data
    await context.bot.send_message(chat_id=chat_id, text=get_weather(city))

async def setcity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Напиши город, например: /setcity Praha")
        return
    city = " ".join(context.args)
    chat_id = update.effective_chat.id
    # планируем задачу
    context.job_queue.run_daily(
        send_daily,
        time=dt.time(hour=SEND_HOUR, minute=0, tzinfo=ZoneInfo(TZ)),
        chat_id=chat_id,
        data=city,
        name=str(chat_id),
        replace_existing=True,
    )
    await update.message.reply_text(f"✅ Каждый день в {SEND_HOUR}:00 я буду присылать погоду для {city}.")

# --- Запуск ---
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("weather", weather))
    app.add_handler(CommandHandler("now", now))
    app.add_handler(CommandHandler("setcity", setcity))

    app.run_polling()

if __name__ == "__main__":
    main()
