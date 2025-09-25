import os
import logging
import datetime as dt
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from zoneinfo import ZoneInfo

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEATHER_API = os.getenv("OPENWEATHER_API_KEY")
TZ = "Europe/Prague"   # твой часовой пояс
SEND_HOUR = 7          # время отправки прогноза

# Получение погоды
def get_weather(city: str):
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API}&units=metric&lang=ru"
    res = requests.get(url).json()
    if res.get("cod") != 200:
        return "Город не найден!"
    temp = res["main"]["temp"]
    feels = res["main"]["feels_like"]
    desc = res["weather"][0]["description"]

    advice = "🌞 Легкая одежда."
    if temp < 5:
        advice = "🧥 Теплая куртка и шапка."
    elif temp < 15:
        advice = "🧥 Легкая куртка."
    elif temp < 25:
        advice = "👕 Кофта или футболка."

    return f"Погода в {city}:\nТемпература: {temp}°C\nОщущается: {feels}°C\nОписание: {desc}\nСовет: {advice}"

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Напиши /weather <город>, или /now для прогноза сейчас.")

# Команда /weather
async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Напиши город: /weather Прага")
        return
    city = " ".join(context.args)
    report = get_weather(city)
    await update.message.reply_text(report)

# Команда /now (прогноз сейчас)
async def now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = "Praha"
    report = get_weather(city)
    await update.message.reply_text(report)

# Отправка прогноза каждое утро
async def send_daily(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    report = get_weather("Praha")
    await context.bot.send_message(chat_id=chat_id, text=report)

# Главная функция
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("weather", weather))
    app.add_handler(CommandHandler("now", now))

    # Планировщик на 7 утра
    app.job_queue.run_daily(
        send_daily,
        dt.time(hour=SEND_HOUR, minute=0, tzinfo=ZoneInfo(TZ)),
        chat_id=123456789   # <-- замени на свой chat_id
    )

    print("Бот запущен ✅")
    app.run_polling()

if __name__ == "__main__":
    main()
