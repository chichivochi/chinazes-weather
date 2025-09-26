import os
import logging
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ------------ ЛОГИ ------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("weather-bot")

# ------------ КЛЮЧИ/НАСТРОЙКИ ------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OW_KEY = os.getenv("OPENWEATHER_API_KEY")
TZ = ZoneInfo("Europe/Prague")  # для «завтра» по местному времени

# ============ ВСПОМОГАТЕЛЬНОЕ ============
def clothing_advice(temp: float, feels: float, desc: str) -> str:
    """Советы по одежде с предупреждениями о дожде/снеге/ветре/грозе."""
    d = desc.lower()
    tips = []

    # по «ощущается»
    if feels < -5:
        tips.append("Очень холодно 🥶: тёплый пуховик, шапка, перчатки, шарф.")
    elif feels < 5:
        tips.append("Холодно ❄️: тёплая куртка, шапка, перчатки.")
    elif feels < 12:
        tips.append("Прохладно 🧥: лёгкая куртка/кофта.")
    elif feels < 20:
        tips.append("Умеренно 🌤: футболка + лёгкая накидка.")
    else:
        tips.append("Тепло ☀️: лёгкая одежда, пей воду.")

    # дополнительные предупреждения
    if ("rain" in d) or ("дожд" in d) or ("морос" in d):
        tips.append("🌧 Ожидается дождь — возьми зонт или дождевик.")
    if "snow" in d or "снег" in d:
        tips.append("❄ Возможен снег — тёплая непромокаемая обувь и перчатки.")
    if "thunderstorm" in d or "гроза" in d:
        tips.append("⛈ Гроза — избегай открытых мест и высотных деревьев.")
    if "wind" in d or "ветер" in d:
        tips.append("💨 Ветрено — пригодится ветрозащитная куртка/капюшон.")

    return "\n".join(tips)


def kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["today", "tomorrow"],
            ["Praha", KeyboardButton("📍 Моя геолокация", request_location=True)],
        ],
        resize_keyboard=True,
    )


# ============ OPENWEATHER ============

def current_by_city(city: str) -> Optional[Tuple[str, float, float, str]]:
    url = "https://api.openweathermap.org/data/2.5/weather"
    p = {"q": city, "appid": OW_KEY, "units": "metric", "lang": "ru"}
    try:
        r = requests.get(url, params=p, timeout=15).json()
        if r.get("cod") != 200:
            return None
        return (
            r["name"],
            float(r["main"]["temp"]),
            float(r["main"]["feels_like"]),
            str(r["weather"][0]["description"]),
        )
    except Exception as e:
        log.exception("current_by_city error: %s", e)
        return None


def current_by_coords(lat: float, lon: float) -> Optional[Tuple[str, float, float, str]]:
    url = "https://api.openweathermap.org/data/2.5/weather"
    p = {"lat": lat, "lon": lon, "appid": OW_KEY, "units": "metric", "lang": "ru"}
    try:
        r = requests.get(url, params=p, timeout=15).json()
        if r.get("cod") != 200:
            return None
        return (
            r["name"],
            float(r["main"]["temp"]),
            float(r["main"]["feels_like"]),
            str(r["weather"][0]["description"]),
        )
    except Exception as e:
        log.exception("current_by_coords error: %s", e)
        return None


def tomorrow_by_city(city: str) -> Optional[Tuple[str, float, float, str]]:
    """Прогноз на завтра: минимум/максимум и описание около полудня."""
    url = "https://api.openweathermap.org/data/2.5/forecast"
    p = {"q": city, "appid": OW_KEY, "units": "metric", "lang": "ru"}
    try:
        r = requests.get(url, params=p, timeout=20).json()
        if r.get("cod") != "200":
            return None
        name = r["city"]["name"]
        target_date = (datetime.now(TZ) + timedelta(days=1)).date()
        pts = [i for i in r["list"] if datetime.fromtimestamp(i["dt"], TZ).date() == target_date]
        if not pts:
            return None
        tmin = min(i["main"]["temp_min"] for i in pts)
        tmax = max(i["main"]["temp_max"] for i in pts)
        near12 = min(pts, key=lambda i: abs(datetime.fromtimestamp(i["dt"], TZ).hour - 12))
        desc = near12["weather"][0]["description"]
        return name, float(tmin), float(tmax), str(desc)
    except Exception as e:
        log.exception("tomorrow_by_city error: %s", e)
        return None


def tomorrow_by_coords(lat: float, lon: float) -> Optional[Tuple[str, float, float, str]]:
    url = "https://api.openweathermap.org/data/2.5/forecast"
    p = {"lat": lat, "lon": lon, "appid": OW_KEY, "units": "metric", "lang": "ru"}
    try:
        r = requests.get(url, params=p, timeout=20).json()
        if r.get("cod") != "200":
            return None
        name = r["city"]["name"]
        target_date = (datetime.now(TZ) + timedelta(days=1)).date()
        pts = [i for i in r["list"] if datetime.fromtimestamp(i["dt"], TZ).date() == target_date]
        if not pts:
            return None
        tmin = min(i["main"]["temp_min"] for i in pts)
        tmax = max(i["main"]["temp_max"] for i in pts)
        near12 = min(pts, key=lambda i: abs(datetime.fromtimestamp(i["dt"], TZ).hour - 12))
        desc = near12["weather"][0]["description"]
        return name, float(tmin), float(tmax), str(desc)
    except Exception as e:
        log.exception("tomorrow_by_coords error: %s", e)
        return None


# ============ ФОРМАТИРОВАНИЕ ============
def format_now(name: str, temp: float, feels: float, desc: str) -> str:
    return (
        f"🌤 Сейчас в {name}:\n"
        f"Температура: {round(temp)}°C (ощущается {round(feels)}°C)\n"
        f"Описание: {desc.capitalize()}\n\n"
        f"👕 Советы:\n{clothing_advice(temp, feels, desc)}"
    )


def format_tomorrow(name: str, tmin: float, tmax: float, desc: str) -> str:
    mid = (tmin + tmax) / 2
    return (
        f"📅 Завтра в {name}:\n"
        f"Мин/Макс: {round(tmin)}°C / {round(tmax)}°C\n"
        f"Описание: {desc.capitalize()}\n\n"
        f"👕 Советы:\n{clothing_advice(mid, mid, desc)}"
    )


# ============ ХЭНДЛЕРЫ ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data.setdefault("mode", "city")   # city|geo
    context.chat_data.setdefault("city", "Praha")
    await update.message.reply_text(
        "Выбери: today / tomorrow.\n"
        "Ниже — источник: Praha или 📍 Моя геолокация.",
        reply_markup=kb(),
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.chat_data.get("mode", "city")
    if mode == "city":
        city = context.chat_data.get("city", "Praha")
        cur = current_by_city(city)
    else:
        coords = context.chat_data.get("coords")
        cur = current_by_coords(*coords) if coords else None

    if not cur:
        await update.message.reply_text("Не получилось получить погоду. Попробуй снова.", reply_markup=kb())
        return

    name, temp, feels, desc = cur
    await update.message.reply_text(format_now(name, temp, feels, desc), reply_markup=kb())


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.chat_data.get("mode", "city")
    if mode == "city":
        city = context.chat_data.get("city", "Praha")
        tw = tomorrow_by_city(city)
    else:
        coords = context.chat_data.get("coords")
        tw = tomorrow_by_coords(*coords) if coords else None

    if not tw:
        await update.message.reply_text("Не получилось получить прогноз на завтра. Попробуй снова.", reply_markup=kb())
        return

    name, tmin, tmax, desc = tw
    await update.message.reply_text(format_tomorrow(name, tmin, tmax, desc), reply_markup=kb())


async def on_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()

    if text == "today":
        await cmd_today(update, context)
        return
    if text == "tomorrow":
        await cmd_tomorrow(update, context)
        return
    if text == "praha":
        context.chat_data["mode"] = "city"
        context.chat_data["city"] = "Praha"
        await update.message.reply_text("Источник: Praha ✅", reply_markup=kb())
        return

    await update.message.reply_text("Нажми кнопку: today / tomorrow, либо выбери источник ниже.", reply_markup=kb())


async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    if not loc:
        return
    context.chat_data["mode"] = "geo"
    context.chat_data["coords"] = (loc.latitude, loc.longitude)
    await update.message.reply_text("Источник: текущая геолокация ✅", reply_markup=kb())


# зарегистрируем команды в меню Telegram
async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "показать клавиатуру"),
        BotCommand("today", "погода сейчас + советы"),
        BotCommand("tomorrow", "прогноз на завтра + советы"),
    ])


# ============ ЗАПУСК ============
def main():
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))

    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_buttons))

    print("Бот запущен ✅")
    app.run_polling()


if __name__ == "__main__":
    main()
