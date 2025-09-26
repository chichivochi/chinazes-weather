import os
import logging
from datetime import datetime, timedelta, time as dtime
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
    JobQueue,
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

TZ = ZoneInfo("Europe/Prague")
SEND_HOUR = 7  # 07:00

# список чатов, которые нажали /start (память процесса)
subscribers: set[int] = set()


# ---------- СОВЕТЫ ПО ОДЕЖДЕ ----------
def get_clothing_advice(temp_c: float, description: str, wind_speed: float = 0) -> str:
    d = (description or "").lower()
    tips = []

    if temp_c <= 0:
        tips.append("Очень холодно 🥶. Зимняя куртка, шапка, шарф и перчатки.")
    elif 0 < temp_c <= 5:
        tips.append("Холодно ❄️. Тёплая куртка, шапка и перчатки.")
    elif 5 < temp_c <= 15:
        tips.append("Прохладно 🌬. Куртка или худи, закрытая обувь.")
    elif 15 < temp_c <= 25:
        tips.append("Комфортно 🙂. Футболка и лёгкие брюки/джинсы.")
    else:
        tips.append("Жарко ☀️. Лёгкая одежда, шорты, пей воду.")

    if "дожд" in d or "rain" in d or "морос" in d:
        tips.append("Возьми зонт ☔️ или дождевик.")
    if "снег" in d or "snow" in d:
        tips.append("Тёплая непромокаемая обувь и перчатки ❄️.")
    if "гроза" in d or "thunderstorm" in d:
        tips.append("⛈ Избегай открытых мест и высоких деревьев.")
    if wind_speed >= 8:
        tips.append("💨 Сильный ветер — надень ветровку/капюшон.")
    if "ясно" in d or "clear" in d:
        tips.append("🌞 Ясная погода — очки будут кстати.")

    return " ".join(tips)


def kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["today", "tomorrow"],
            ["Praha", KeyboardButton("📍 Моя геолокация", request_location=True)],
        ],
        resize_keyboard=True,
    )


# ---------- OPENWEATHER ----------
def current_by_city(city: str) -> Optional[Tuple[str, float, float, float, str]]:
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
    """Мин/макс температуры на завтра + описание/ветер около полудня."""
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


# ---------- ТЕКСТЫ ----------
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
    subscribers.add(update.effective_chat.id)
    context.chat_data.setdefault("mode", "city")
    context.chat_data.setdefault("city", "Praha")
    await update.message.reply_text(
        "Выбери: today / tomorrow.\n"
        "Ниже — источник: Praha или 📍 Моя геолокация.\n"
        "Каждый день в 07:00 пришлю прогноз ☀️",
        reply_markup=kb(),
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await get_today_msg(context, update.effective_chat.id)
    await update.message.reply_text(msg, reply_markup=kb())


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await get_tomorrow_msg(context, update.effective_chat.id)
    await update.message.reply_text(msg, reply_markup=kb())


async def on_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").lower()
    if text == "today":
        await cmd_today(update, context)
    elif text == "tomorrow":
        await cmd_tomorrow(update, context)
    elif text == "praha":
        context.chat_data["mode"] = "city"
        context.chat_data["city"] = "Praha"
        await update.message.reply_text("Источник: Praha ✅", reply_markup=kb())
    else:
        await update.message.reply_text("Нажми today / tomorrow, или выбери источник ниже.", reply_markup=kb())


async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    if not loc:
        return
    context.chat_data["mode"] = "geo"
    context.chat_data["coords"] = (loc.latitude, loc.longitude)
    await update.message.reply_text("Источник: текущая геолокация ✅", reply_markup=kb())


# ---------- СБОРКА СООБЩЕНИЙ ----------
async def get_today_msg(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> str:
    chat_data = context.application.chat_data.get(chat_id, {})
    if chat_data.get("mode") == "geo":
        coords = chat_data.get("coords")
        res = current_by_coords(*coords) if coords else None
    else:
        city = chat_data.get("city", "Praha")
        res = current_by_city(city)
    if not res:
        return "Не удалось получить прогноз."
    name, temp, feels, wind, desc = res
    return fmt_now(name, temp, feels, wind, desc)


async def get_tomorrow_msg(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> str:
    chat_data = context.application.chat_data.get(chat_id, {})
    if chat_data.get("mode") == "geo":
        coords = chat_data.get("coords")
        res = tomorrow_by_coords(*coords) if coords else None
    else:
        city = chat_data.get("city", "Praha")
        res = tomorrow_by_city(city)
    if not res:
        return "Не удалось получить прогноз на завтра."
    name, tmin, tmax, wind_noon, desc_noon = res
    return fmt_tomorrow(name, tmin, tmax, wind_noon, desc_noon)


# ---------- РАССЫЛКА В 07:00 ----------
async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    for chat_id in list(subscribers):
        try:
            msg = await get_today_msg(context, chat_id)
            await context.bot.send_message(chat_id, "⏰ Ежедневный прогноз:\n\n" + msg)
        except Exception as e:
            log.error("Ошибка отправки %s: %s", chat_id, e)


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "начать и получать прогноз"),
        BotCommand("today", "погода сейчас + советы"),
        BotCommand("tomorrow", "прогноз на завтра + советы"),
    ])


def main():
    if not TOKEN or not OW_KEY:
        raise RuntimeError("Нет TELEGRAM_BOT_TOKEN или OPENWEATHER_API_KEY")

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_buttons))

    job_queue: JobQueue = app.job_queue
    job_queue.run_daily(daily_job, time=dtime(hour=SEND_HOUR, minute=0, tzinfo=TZ))

    print("Бот запущен ✅")
    app.run_polling()


if __name__ == "__main__":
    main()
