import os
import logging
import requests
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    MessageHandler, filters
)

# ----- Логи -----
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger("weather-bot")

# ----- Ключи и настройки -----
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OW_KEY = os.getenv("OPENWEATHER_API_KEY")
TZ = ZoneInfo("Europe/Prague")
SEND_HOUR = 7  # если используешь ежедневную рассылку

# ====== вспомогательные ======
def clothing_advice(temp: float, feels: float, desc: str) -> str:
    d = desc.lower()
    tips = []
    if feels < -5: tips.append("Очень холодно 🥶: пуховик, шапка, перчатки.")
    elif feels < 5: tips.append("Холодно ❄️: тёплая куртка, шапка, перчатки.")
    elif feels < 12: tips.append("Прохладно 🧥: лёгкая куртка/кофта.")
    elif feels < 20: tips.append("Умеренно 🌤: футболка + лёгкая накидка.")
    else: tips.append("Тепло ☀️: лёгкая одежда, пей воду.")
    if "дожд" in d or "морос" in d: tips.append("Возьми зонт ☔️.")
    if "снег" in d: tips.append("Непромокаемая обувь и перчатки 🧤.")
    if "ветер" in d: tips.append("Ветрозащита пригодится 🌬.")
    if "гроза" in d: tips.append("Избегай открытых мест 🌩.")
    return "\n".join(tips)

def format_now(name: str, temp: float, feels: float, desc: str) -> str:
    return (f"🌤 Сейчас в {name}:\n"
            f"Температура: {round(temp)}°C (ощущается {round(feels)}°C)\n"
            f"Описание: {desc.capitalize()}\n\n"
            f"👕 Советы:\n{clothing_advice(temp, feels, desc)}")

def format_tomorrow(name: str, tmin: float, tmax: float, desc: str) -> str:
    return (f"📅 Завтра в {name}:\n"
            f"Мин/Макс: {round(tmin)}°C / {round(tmax)}°C\n"
            f"Описание: {desc.capitalize()}\n\n"
            f"👕 Советы:\n{clothing_advice((tmin+tmax)/2, (tmin+tmax)/2, desc)}")

# ====== OpenWeather запросы ======
def current_by_city(city: str):
    url = "https://api.openweathermap.org/data/2.5/weather"
    p = {"q": city, "appid": OW_KEY, "units": "metric", "lang": "ru"}
    r = requests.get(url, params=p, timeout=15).json()
    if r.get("cod") != 200: return None
    return r["name"], r["main"]["temp"], r["main"]["feels_like"], r["weather"][0]["description"]

def current_by_coords(lat: float, lon: float):
    url = "https://api.openweathermap.org/data/2.5/weather"
    p = {"lat": lat, "lon": lon, "appid": OW_KEY, "units": "metric", "lang": "ru"}
    r = requests.get(url, params=p, timeout=15).json()
    if r.get("cod") != 200: return None
    return r["name"], r["main"]["temp"], r["main"]["feels_like"], r["weather"][0]["description"]

def tomorrow_by_city(city: str):
    # 5-дневный прогноз с шагом 3 часа
    url = "https://api.openweathermap.org/data/2.5/forecast"
    p = {"q": city, "appid": OW_KEY, "units": "metric", "lang": "ru"}
    r = requests.get(url, params=p, timeout=20).json()
    if r.get("cod") != "200": return None
    name = r["city"]["name"]
    # фильтруем точки завтрашнего дня по UTC времени города
    tomorrow = (datetime.now(ZoneInfo("UTC")) + timedelta(days=1)).date()
    day_points = [i for i in r["list"] if datetime.fromtimestamp(i["dt"], ZoneInfo("UTC")).date() == tomorrow]
    if not day_points: return None
    tmin = min(i["main"]["temp_min"] for i in day_points)
    tmax = max(i["main"]["temp_max"] for i in day_points)
    # возьмём описание из точки около полудня, если есть
    near12 = min(day_points, key=lambda i: abs(datetime.fromtimestamp(i["dt"], ZoneInfo("UTC")).hour - 12))
    desc = near12["weather"][0]["description"]
    return name, tmin, tmax, desc

def tomorrow_by_coords(lat: float, lon: float):
    url = "https://api.openweathermap.org/data/2.5/forecast"
    p = {"lat": lat, "lon": lon, "appid": OW_KEY, "units": "metric", "lang": "ru"}
    r = requests.get(url, params=p, timeout=20).json()
    if r.get("cod") != "200": return None
    name = r["city"]["name"]
    tomorrow = (datetime.now(ZoneInfo("UTC")) + timedelta(days=1)).date()
    day_points = [i for i in r["list"] if datetime.fromtimestamp(i["dt"], ZoneInfo("UTC")).date() == tomorrow]
    if not day_points: return None
    tmin = min(i["main"]["temp_min"] for i in day_points)
    tmax = max(i["main"]["temp_max"] for i in day_points)
    near12 = min(day_points, key=lambda i: abs(datetime.fromtimestamp(i["dt"], ZoneInfo("UTC")).hour - 12))
    desc = near12["weather"][0]["description"]
    return name, tmin, tmax, desc

# ====== UI клавиатура ======
def main_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        ["today", "tomorrow"],
        ["Praha", KeyboardButton("📍 Моя геолокация", request_location=True)]
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

# ====== Хэндлеры ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # по умолчанию используем Прагу
    context.chat_data.setdefault("mode", "city")
    context.chat_data.setdefault("city", "Praha")
    await update.message.reply_text(
        "Выбери действие: today / tomorrow.\n"
        "Третий ряд — выбор источника: Praha или 📍 Моя геолокация.",
        reply_markup=main_keyboard()
    )

async def on_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()

    # переключение источника
    if text == "praha":
        context.chat_data["mode"] = "city"
        context.chat_data["city"] = "Praha"
        await update.message.reply_text("Источник: Praha ✅", reply_markup=main_keyboard())
        return

    # today / tomorrow
    mode = context.chat_data.get("mode", "city")
    if text == "today":
        if mode == "city":
            city = context.chat_data.get("city", "Praha")
            cur = current_by_city(city)
        else:
            coords = context.chat_data.get("coords")
            cur = current_by_coords(*coords) if coords else None
        if not cur:
            await update.message.reply_text("Не получилось получить погоду. Попробуй снова.")
            return
        name, temp, feels, desc = cur
        await update.message.reply_text(format_now(name, temp, feels, desc), reply_markup=main_keyboard())
        return

    if text == "tomorrow":
        if mode == "city":
            city = context.chat_data.get("city", "Praha")
            tw = tomorrow_by_city(city)
        else:
            coords = context.chat_data.get("coords")
            tw = tomorrow_by_coords(*coords) if coords else None
        if not tw:
            await update.message.reply_text("Не получилось получить прогноз на завтра. Попробуй снова.")
            return
        name, tmin, tmax, desc = tw
        await update.message.reply_text(format_tomorrow(name, tmin, tmax, desc), reply_markup=main_keyboard())
        return

    # если что-то другое — просто покажем помощь
    await update.message.reply_text("Нажми кнопку: today / tomorrow, или выбери источник.", reply_markup=main_keyboard())

async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    if not loc:
        return
    context.chat_data["mode"] = "geo"
    context.chat_data["coords"] = (loc.latitude, loc.longitude)
    await update.message.reply_text("Источник: текущая геолокация ✅", reply_markup=main_keyboard())

# (опционально) команда на каждый день в 07:00, если захочешь оставить
async def send_daily(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    mode = context.job.data.get("mode", "city")
    if mode == "city":
        city = context.job.data.get("city", "Praha")
        cur = current_by_city(city)
        if not cur: return
        name, temp, feels, desc = cur
        await context.bot.send_message(chat_id, format_now(name, temp, feels, desc))
    else:
        coords = context.job.data.get("coords")
        if not coords: return
        cur = current_by_coords(*coords)
        if not cur: return
        name, temp, feels, desc = cur
        await context.bot.send_message(chat_id, format_now(name, temp, feels, desc))

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_buttons))

    # пример ежедневной задачи для конкретного чата (если нужно — добавляй вручную из кода)
    # app.job_queue.run_daily(send_daily, dtime(hour=SEND_HOUR, minute=0, tzinfo=TZ),
    #                         chat_id=<CHAT_ID>, data={"mode": "city", "city": "Praha"})

    print("Бот запущен ✅")
    app.run_polling()

if __name__ == "__main__":
    main()
