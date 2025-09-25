import os
import requests
import datetime as dt
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv

# Загружаем токены из .env
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEATHER_KEY = os.getenv("OPENWEATHER_API_KEY")

# В какой час отправлять прогноз (07:00)
SEND_HOUR = 7
TZ = "Europe/Prague"  # часовой пояс

# Сохраняем города пользователей в памяти
user_cities = {}

# ---- функции ----
def get_weather(city):
    url = (f"http://api.openweathermap.org/data/2.5/weather?q={city}"
           f"&appid={WEATHER_KEY}&units=metric&lang=ru")
    r = requests.get(url)
    if r.status_code != 200:
        return None
    data = r.json()
    temp = data["main"]["temp"]
    feels = data["main"]["feels_like"]
    desc = data["weather"][0]["description"]
    return temp, feels, desc

def clothing_advice(temp, feels, desc):
    tips = []
    if feels < 0:
        tips.append("Очень холодно ❄️ — тёплая куртка, шарф, перчатки.")
    elif feels < 10:
        tips.append("Прохладно 🧥 — куртка или толстовка.")
    elif feels < 20:
        tips.append("Умеренно 🌤 — лёгкая куртка или кофта.")
    else:
        tips.append("Тепло 😎 — футболка или лёгкая одежда.")
    if "дожд" in desc.lower() or "морос" in desc.lower():
        tips.append("Возьми зонт ☔️.")
    if "снег" in desc.lower():
        tips.append("Обувь для снега и перчатки 🧤.")
    return "\n".join(tips)

def get_user_city(user_id):
    return user_cities.get(user_id)

# ---- команды ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["/now", "/today", "/setcity Praha"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Привет! Я бот прогноза погоды 👋\n"
        "Сначала задай город: /setcity <город>\n"
        "Можно нажать кнопку ниже 👇",
        reply_markup=reply_markup
    )

async def setcity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Напиши город, например: /setcity Praha")
        return
    city = " ".join(context.args)
    user_cities[update.effective_user.id] = city
    await update.message.reply_text(f"Город сохранён: {city}")

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = get_user_city(update.effective_user.id)
    if not city:
        await update.message.reply_text("Сначала задай город: /setcity <город>")
        return
    w = get_weather(city)
    if not w:
        await update.message.reply_text("Не удалось получить погоду 😢")
        return
    temp, feels, desc = w
    tips = clothing_advice(temp, feels, desc)
    msg = (f"🌤 Погода в {city}:\n"
           f"Температура: {temp}°C (ощущается {feels}°C)\n"
           f"Описание: {desc}\n\n"
           f"👕 Советы:\n{tips}")
    await update.message.reply_text(msg)

async def now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await today(update, context)

# ---- ежедневная рассылка ----
async def send_daily(context: ContextTypes.DEFAULT_TYPE):
    for user_id, city in user_cities.items():
        w = get_weather(city)
        if not w:
            continue
        temp, feels, desc = w
        tips = clothing_advice(temp, feels, desc)
        msg = (f"🌤 Утренний прогноз в {city}:\n"
               f"Температура: {temp}°C (ощущается {feels}°C)\n"
               f"Описание: {desc}\n\n"
               f"👕 Советы:\n{tips}")
        try:
            await context.bot.send_message(chat_id=user_id, text=msg)
        except Exception as e:
            print(f"Ошибка при отправке {user_id}: {e}")

# ---- запуск ----
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setcity", setcity))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("now", now))

    # ежедневная задача в 07:00
    job_queue = app.job_queue
    job_queue.run_daily(send_daily, dt.time(hour=SEND_HOUR, minute=0, tzinfo=ZoneInfo(TZ)))

    print("Бот запущен ✅")
    app.run_polling()

if __name__ == "__main__":
    main()