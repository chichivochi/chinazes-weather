import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

import requests
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

# ---------- ЛОГИ ----------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("chinazes-weather")

# ---------- КЛЮЧИ ----------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OW_KEY = os.getenv("OPENWEATHER_API_KEY")

# часовой пояс для "завтра"
TZ = ZoneInfo("Europe/Prague")


# ---------- СОВЕТЫ ПО ОДЕЖДЕ ----------
def get_clothing_advice(temp_c: float, description: str, wind_speed: float = 0) -> str:
    d = (description or "").lower()
    tips = []

    # по температуре (по «факту», можно заменить на feels_like если хочешь)
    if temp_c <= 0:
        tips.append("Очень холодно 🥶. Зимняя куртка, шапка, шарф и перчатки.")
    elif 0 < temp_c <= 5:
        tips.append("Холодно ❄️. Тёплая куртка, шапка и перчатки.")
    elif 5 < temp_c <= 15:
        tips.append("Прохладно 🌬. Куртка или худи, закрытая обувь.")
    elif 15 < temp_c <= 25:
        tips.append("Комфортно 🙂. Футболка и лёгкие брюки/джинсы.")
    else:
        tips.append("Жарко ☀️. Лёгкая одежда, шорты, пейте больше воды.")

    # по осадкам/небу
    if "дожд" in d or "rain" in d or "морос" in d:
        tips.append("Возьми зонт ☔️ или дождевик.")
    if "снег" in d or "snow" in d:
        tips.append("Тёплая непромокаемая обувь и перчатки ❄️.")
    if "гроза" in d or "thunderstorm" in d:
        tips.append("⛈ Избегай открытых мест и высоких деревьев.")
    if "обла" in d or "cloud" in d:
        tips.append("Пасмурно — пригодится лёгкая куртка.")
    if "ясно" in d or "clear" in d:
        tips.append("Ясно 🌞 — солнечные очки будут кстати.")

    # по ветру
    if wind_speed >= 8:
        tips.append("Сильный ветер 💨 — надень ветровку/капюшон.")

    return " ".join(tips)


def kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["today", "tomorrow"],
            ["Praha", KeyboardButton("📍 Моя геолокация", request_location=True)],
        ],
        resize_keyboard=True,
    )


# ---------- ЗАПРОСЫ К OPENWEATHER ----------
def current_by_city(city: str) -> Optional[Tuple[str, float, float, float, str]]:
    """Возвращает: name, temp, feels_like, wind_speed, description"""
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
            float(r.get("wind", {}).get("speed", 0.0)),
            str(r["weather"][0]["description"]),
        )
    except Exception as e:
        log.exception("current_by_city error: %s", e)
        return None


def current_by_coords(lat: float, lon: float) -> Optional[Tuple[str, float, float, float, str]]:
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
            float(r.get("wind", {}).get("speed", 0.0)),
            str(r["weather"][0]["description"]),
        )
    except Exception as e:
        log.exception("current_by_coords error: %s", e)
        return None


def tomorrow_by_city(city: str) -> Optional[Tuple[str, float, float, float, str]]:
    """Возвращает прогноз на завтра: name, tmin, tmax, wind_noon, desc_noon"""
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
        # точка, ближайшая к полудню
        near12 = min(pts, key=lambda i: abs(datetime.fromtimestamp(i["dt"], TZ).hour - 12))
        desc = near12["weather"][0]["description"]
        wind = float(near12.get("wind", {}).get("speed", 0.0))

        return name, float(tmin), float(tmax), wind, str(desc)
    except Exception as e:
        log.exception("tomorrow_by_city error: %s", e)
        return None


def tomorrow_by_coords(lat: float, lon: float) -> Optional[Tuple[str, float, float, float, str]]:
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
        wind = float(near12.get("wind", {}).get("speed", 0.0))

        return name, float(tmin), float(tmax), wind, str(desc)
    except Exception as e:
        log.exception("tomorrow_by_coords error: %s", e)
        return None


# ---------- ФОРМАТЫ ОТВЕТА ----------
def fmt_now(name: str, temp: float, feels: float, wind: float, desc: str) -> str:
    advice = get_clothing_advice(temp, desc, wind)
    return (
        f"🌤 Сейчас в {name}:\n"
        f"Температура: {round(temp)}°C (ощущается {round(feels)}°C)\n"
        f"Ветер: {round(wind)} м/с\n"
        f"Описание: {desc.capitalize()}\n\n"
        f"👕 Совет: {advice}"
    )


def fmt_tomorrow(name: str, tmin: float, tmax: float, wind_noon: float, desc_noon: str) -> str:
    mid = (tmin + tmax) / 2
    advice = get_clothing_advice(mid, desc_noon, wind_noon)
    return (
        f"📅 Завтра в {name}:\n"
        f"Мин/макс: {round(tmin)}°C / {round(tmax)}°C\n"
        f"Ветер (около полудня): {round(wind_noon)} м/с\n"
        f"Описание: {desc_noon.capitalize()}\n\n"
        f"👕 Совет: {advice}"
    )


# ---------- ХЭНДЛЕРЫ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data.setdefault("mode", "city")   # city | geo
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
        res = current_by_city(city)
    else:
        coords = context.chat_data.get("coords")
        res = current_by_coords(*coords) if coords else None

    if not res:
        await update.message.reply_text("Не получилось получить погоду. Попробуй ещё раз.", reply_markup=kb())
        return

    name, temp, feels, wind, desc = res
    await update.message.reply_text(fmt_now(name, temp, feels, wind, desc), reply_markup=kb())


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.chat_data.get("mode", "city")
    if mode == "city":
        city = context.chat_data.get("city", "Praha")
        res = tomorrow_by_city(city)
    else:
        coords = context.chat_data.get("coords")
        res = tomorrow_by_coords(*coords) if coords else None

    if not res:
        await update.message.reply_text("Не получилось получить прогноз на завтра.", reply_markup=kb())
        return

    name, tmin, tmax, wind_noon, desc_noon = res
    await update.message.reply_text(fmt_tomorrow(name, tmin, tmax, wind_noon, desc_noon), reply_markup=kb())


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


# Зарегистрируем команды в меню Telegram
async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "показать клавиатуру"),
        BotCommand("today", "погода сейчас + советы"),
        BotCommand("tomorrow", "прогноз на завтра + советы"),
    ])


# ---------- ЗАПУСК ----------
def main():
    if not TOKEN or not OW_KEY:
        raise RuntimeError("Нет TELEGRAM_BOT_TOKEN или OPENWEATHER_API_KEY в переменных окружения")

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
