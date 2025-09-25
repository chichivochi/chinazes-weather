import os
import logging
import requests
from datetime import time
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# Берём токены из переменных окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEATHER_KEY = os.getenv("OPENWEATHER_API_KEY")

# Часовой пояс и время для ежедневного сообщения
TZ = ZoneInfo("Europe/Prague")
SEND_HOUR = 9  # во сколько утра слать авто-сообщение

# Получение погоды
def get_weather(city: str) -> str:
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_KEY}&units=metric&lang=ru"
    resp = requests.get(url).json()
    if resp.get("cod") != 200:
        return "Не удалось получить погоду 😢"
    return (
        f"🌤 Погода в городе {resp['name']}:\n"
        f"Температура: {resp['main']['temp']}°C\n"
        f"Ощущается как: {resp['main']['feels_like']}°C\n"
        f"Описание: {resp['weather'][0]['description'].capitalize()}"
    )

# Команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Напиши /weather <город> или /now 😉")

async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Напиши город, например: /weather Praha")
        return
    city = " ".join(context.args)
    await update.message.reply_text(get_weather(city))

async def now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_weather("Praha"))

# Авто-отправка каждый день
async def send_daily(context: ContextTypes.DEFAULT_TYPE):
    chat_id = os.getenv("CHAT_ID")
    if chat_id:
        await context.bot.send_message(chat_id=chat_id, text=get_weather("Praha"))

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("weather", weather))
    app.add_handler(CommandHandler("now", now))

    # ежедневная задача
    app.job_queue.run_daily(send_daily, time(hour=SEND_HOUR, minute=0, tzinfo=TZ))

    print("Бот запущен ✅")
    app.run_polling()

if __name__ == "__main__":
    main()
