import os
import logging
import json
from pathlib import Path
from datetime import datetime, timedelta, time as dtime
from typing import Optional, Tuple, Dict, Any, List
from zoneinfo import ZoneInfo
from html import unescape

import requests
import feedparser
from email.utils import parsedate_to_datetime

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    BotCommand,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    Job,
)

# ---------- ПЕРЕВОД НА РУССКИЙ (DeepL) ----------
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")

def translate_to_ru(text: str) -> str:
    """Перевод текста на русский через DeepL (если ключ задан). Никогда не падает."""
    if not text:
        return text
    if not DEEPL_API_KEY:
        return text  # без ключа отдаём оригинал
    try:
        resp = requests.post(
            "https://api-free.deepl.com/v2/translate",
            data={"text": text, "target_lang": "RU"},
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}"},
            timeout=12,
        )
        if resp.ok:
            data = resp.json()
            return data.get("translations", [{}])[0].get("text", text)
    except Exception as e:
        print("Ошибка перевода:", e)
    return text

# ---------- ЛОГИ ----------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("assistant-bot")

# ---------- КЛЮЧИ / НАСТРОЙКИ ----------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OW_KEY = os.getenv("OPENWEATHER_API_KEY")
TM_KEY = os.getenv("TICKETMASTER_API_KEY")
EVENTBRITE_TOKEN = os.getenv("EVENTBRITE_TOKEN")   # фолбэк событий
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")           # фоллбэк гороскопа

TZ = ZoneInfo("Europe/Prague")
DEFAULT_SEND_HOUR = 7

# ---------- ПРОСТЕЙШАЯ БД (переживает рестарты) ----------
DB_PATH = Path("users.db")  # JSON с настройками на чат

def load_db() -> Dict[str, Any]:
    if DB_PATH.exists():
        try:
            return json.loads(DB_PATH.read_text("utf-8"))
        except Exception:
            log.warning("users.db повреждён, создаю новый.")
    return {"users": {}}  # {chat_id: {...}}

def save_db(db: Dict[str, Any]) -> None:
    DB_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=2), "utf-8")

def get_user(chat_id: int) -> Dict[str, Any]:
    db = load_db()
    return db["users"].get(str(chat_id), {})

def set_user(chat_id: int, data: Dict[str, Any]) -> None:
    db = load_db()
    db["users"][str(chat_id)] = data
    save_db(db)

def ensure_defaults(chat_id: int) -> Dict[str, Any]:
    u = get_user(chat_id)
    u.setdefault("mode", "city")            # city | geo
    u.setdefault("city", "Praha")
    u.setdefault("coords", None)            # [lat, lon] | None
    u.setdefault("daily_hour", DEFAULT_SEND_HOUR)
    u.setdefault("horo_enabled", None)      # None -> ещё не спрашивали; True/False
    u.setdefault("horo_sign", None)         # 'leo' и т.п.
    set_user(chat_id, u)
    return u

# ---------- КЛАВИАТУРЫ ----------
def weather_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["today", "tomorrow"],
            ["Praha", KeyboardButton("📍 Моя геолокация", request_location=True)],
            ["🔙 Назад"],
        ],
        resize_keyboard=True,
    )

def settings_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["⏰ Время рассылки", "🌆 Изменить город"],
            ["♈ Знак зодиака"],
            ["🔙 Назад"],
        ],
        resize_keyboard=True,
    )

def hours_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["6", "7", "8", "9"],
            ["10", "11", "12", "13"],
            ["18", "19", "20", "21"],
            ["Ввести вручную", "Отмена"],
        ],
        resize_keyboard=True,
    )

def yesno_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["Да", "Нет"]], resize_keyboard=True)

def zodiac_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["♈ Овен", "♉ Телец", "♊ Близнецы"],
            ["♋ Рак", "♌ Лев", "♍ Дева"],
            ["♎ Весы", "♏ Скорпион", "♐ Стрелец"],
            ["♑ Козерог", "♒ Водолей", "♓ Рыбы"],
            ["Отмена"],
        ],
        resize_keyboard=True,
    )

# ---------- ЗОДИАК ----------
ZODIAC_MAP_RU_EN: Dict[str, str] = {
    "овен": "aries", "телец": "taurus", "близнецы": "gemini", "рак": "cancer",
    "лев": "leo", "дева": "virgo", "весы": "libra", "скорпион": "scorpio",
    "стрелец": "sagittarius", "козерог": "capricorn", "водолей": "aquarius", "рыбы": "pisces",
    "aries":"aries","taurus":"taurus","gemini":"gemini","cancer":"cancer","leo":"leo",
    "virgo":"virgo","libra":"libra","scorpio":"scorpio","sagittarius":"sagittarius",
    "capricorn":"capricorn","aquarius":"aquarius","pisces":"pisces",
}

def normalize_sign(text: str) -> Optional[str]:
    t = (text or "").strip().lower()
    t = (t.replace("♈","").replace("♉","").replace("♊","").replace("♋","").replace("♌","").replace("♍","")
           .replace("♎","").replace("♏","").replace("♐","").replace("♑","").replace("♒","").replace("♓","")
           .strip())
    return ZODIAC_MAP_RU_EN.get(t)

# ---------- OPENWEATHER ----------
def rain_warning_line(desc: str, pop: Optional[float] = None) -> str:
    d = (desc or "").lower()
    has_rain = any(k in d for k in ["дожд", "rain", "морос", "гроза", "thunder"])
    if pop is not None:
        if pop >= 0.30 or has_rain:
            pct = round(pop * 100)
            return f"☔️ Вероятность осадков ~{pct}% — возьми зонт."
        return ""
    return "☔️ Возможен дождь — возьми зонт." if has_rain else ""

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

def tomorrow_by_city(city: str) -> Optional[Tuple[str, float, float, float, str, float]]:
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
        pop_max = max(float(i.get("pop", 0.0)) for i in pts)
        return name, float(tmin), float(tmax), wind, str(desc), pop_max
    except Exception as e:
        log.exception("tomorrow_by_city error: %s", e)
        return None

def tomorrow_by_coords(lat: float, lon: float) -> Optional[Tuple[str, float, float, float, str, float]]:
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
        pop_max = max(float(i.get("pop", 0.0)) for i in pts)
        return name, float(tmin), float(tmax), wind, str(desc), pop_max
    except Exception as e:
        log.exception("tomorrow_by_coords error: %s", e)
        return None

# ---------- СОВЕТЫ ПО ОДЕЖДЕ (РАЗВЁРНУТЫЕ) ----------
def get_clothing_advice(temp_c: float, description: str, wind_speed: float = 0) -> str:
    d = (description or "").lower()
    is_rain = any(k in d for k in ["дожд", "rain", "морос"])
    is_snow = any(k in d for k in ["снег", "snow"])
    is_storm = any(k in d for k in ["гроза", "thunderstorm"])
    is_clear = any(k in d for k in ["ясно", "clear"])
    is_light = any(k in d for k in ["небольш", "легк", "light", "морос"])
    is_heavy = any(k in d for k in ["сильн", "heavy", "ливень", "pour"])
    windy = wind_speed >= 8
    very_windy = wind_speed >= 14

    lines: List[str] = []
    if temp_c <= 0:
        lines.append("Очень холодно 🥶. Надень тёплое термобельё, сверху — шерстяной/флисовый слой и плотную зимнюю куртку. Обязательно шапка, шарф и тёплые перчатки. Обувь — зимняя с толстой подошвой.")
    elif 0 < temp_c <= 5:
        lines.append("Холодно ❄️. Тёплая куртка + свитер/худи. Шапка и перчатки желательны. Обувь — утеплённые ботинки или кроссовки на толстой подошве.")
    elif 5 < temp_c <= 12:
        lines.append("Прохладно 🌬. Лёгкая куртка/ветровка или худи, можно тонкий свитер слоем. Обувь — закрытая (кроссовки/ботинки).")
    elif 12 < temp_c <= 20:
        lines.append("Умеренно 🌤. Футболка/лонгслив, при желании лёгкая накидка или тонкая куртка. Обувь — кроссовки, мокасины.")
    else:
        lines.append("Тепло/жарко ☀️. Лёгкая одежда из хлопка/льна: футболка, шорты/платье. Пей больше воды; по возможности избегай прямого солнца в полдень.")

    if is_rain:
        if is_heavy:
            lines.append("🌧 Сильный дождь — возьми зонт или непромокаемый плащ, куртку с капюшоном и водостойкую обувь.")
        elif is_light:
            lines.append("🌦 Возможна морось/небольшой дождь — зонт или лёгкий дождевик не помешают.")
        else:
            lines.append("☔️ Ожидается дождь — зонт/дождевик и водостойкая обувь.")
    if is_snow:
        lines.append("❄️ Снег — пригодятся непромокаемая тёплая обувь и утеплённые перчатки.")
    if is_storm:
        lines.append("⛈ Гроза — избегай открытых мест и высоких металлических конструкций.")
    if very_windy:
        lines.append("💨 Очень ветрено — надень ветрозащитную куртку/капюшон и прикрой уши/шею.")
    elif windy:
        lines.append("💨 Ветрено — возьми ветровку или вещь с высоким воротником.")
    if is_clear and not is_snow:
        lines.append("😎 Ясная погода — возьми солнечные очки; бутылка воды пригодится.")
    if is_clear and is_snow:
        lines.append("😎☃️ Ясно и снежно — очки особенно пригодятся: снег сильно отражает свет.")
    if temp_c > 25 and is_clear:
        lines.append("🧴 Если можешь, используй SPF и держи при себе воду.")
    return "\n".join(lines)

# ---------- ФОРМАТИРОВАНИЕ ПОГОДЫ ----------
def fmt_now(name: str, temp: float, feels: float, wind: float, desc: str) -> str:
    advice = get_clothing_advice(feels, desc, wind)
    rain_line = rain_warning_line(desc)
    return (
        f"🌤 Сейчас в {name}:\n"
        f"Температура: {round(temp)}°C (ощущается {round(feels)}°C)\n"
        f"Ветер: {round(wind)} м/с\n"
        f"Описание: {desc.capitalize()}\n"
        f"{rain_line}\n\n"
        f"👕 Совет:\n{advice}"
    )

def fmt_tomorrow(name: str, tmin: float, tmax: float, wind_noon: float, desc_noon: str, pop_max: float) -> str:
    mid = (tmin + tmax) / 2
    advice = get_clothing_advice(mid, desc_noon, wind_noon)
    rain_line = rain_warning_line(desc_noon, pop_max)
    return (
        f"📅 Завтра в {name}:\n"
        f"Мин/макс: {round(tmin)}°C / {round(tmax)}°C\n"
        f"Ветер (около полудня): {round(wind_noon)} м/с\n"
        f"Описание: {desc_noon.capitalize()}\n"
        f"{rain_line}\n\n"
        f"👕 Совет:\n{advice}"
    )

# ---------- НОВОСТИ (RSS: мир + крипто, бесплатно) ----------
WORLD_RSS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.reuters.com/reuters/worldNews",
    "https://apnews.com/rss",
]
CRYPTO_RSS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://www.cointelegraph.com/rss",
]

def parse_rss(url: str) -> List[dict]:
    feed = feedparser.parse(url)
    items = []
    for e in feed.entries:
        # Title
        title = unescape(getattr(e, "title", "") or "")
        # Description/summary
        summ = unescape(getattr(e, "summary", "") or "")
        # Link
        link = getattr(e, "link", "") or ""
        # Published
        pub = getattr(e, "published", None) or getattr(e, "updated", None)
        try:
            published_dt = parsedate_to_datetime(pub) if pub else None
        except Exception:
            published_dt = None
        items.append({
            "title": title.strip(),
            "summary": summ.strip(),
            "link": link.strip(),
            "published": published_dt,
        })
    return items

def filter_last_hours(items: List[dict], hours: int) -> List[dict]:
    if not items:
        return []
    now_utc = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
    cutoff = now_utc - timedelta(hours=hours)
    fresh = []
    for it in items:
        dt = it.get("published")
        if dt is None:
            # если нет времени — оставим, но в конец списка
            it["_score"] = 0
            fresh.append(it)
        else:
            # приводим к UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            if dt >= cutoff:
                it["_score"] = 1
                fresh.append(it)
    # Сортируем: сначала с датой (свежее сверху), затем без даты
    fresh.sort(key=lambda x: (x.get("_score", 0), x.get("published") or datetime.min), reverse=True)
    return fresh

def fetch_news_rss(hours: int = 5, max_world: int = 3, max_crypto: int = 3) -> Dict[str, List[dict]]:
    world_items: List[dict] = []
    crypto_items: List[dict] = []
    try:
        for u in WORLD_RSS:
            world_items.extend(parse_rss(u))
        for u in CRYPTO_RSS:
            crypto_items.extend(parse_rss(u))
    except Exception as e:
        log.exception("RSS parse error: %s", e)
    world_fresh = filter_last_hours(world_items, hours)[:max_world]
    crypto_fresh = filter_last_hours(crypto_items, hours)[:max_crypto]
    return {"world": world_fresh, "crypto": crypto_fresh}

def fmt_news_rss(world: List[dict], crypto: List[dict]) -> str:
    lines = []
    if world:
        lines.append("🗞 Главные мировые новости (последние 5 часов):")
        lines.append("")
        for it in world:
            ttl = translate_to_ru(it.get("title", ""))
            summ = translate_to_ru(it.get("summary", ""))
            link = it.get("link", "")
            piece = f"• {ttl}"
            if summ:
                piece += f"\n  {summ}"
            if link:
                piece += f"\n  {link}"
            lines.append(piece)
        lines.append("")
    else:
        lines.append("🗞 За последние 5 часов мировых новостей не нашлось.")
        lines.append("")

    if crypto:
        lines.append("₿ Крипто-новости (последние 5 часов):")
        lines.append("")
        for it in crypto:
            ttl = translate_to_ru(it.get("title", ""))
            summ = translate_to_ru(it.get("summary", ""))
            link = it.get("link", "")
            piece = f"• {ttl}"
            if summ:
                piece += f"\n  {summ}"
            if link:
                piece += f"\n  {link}"
            lines.append(piece)
    else:
        lines.append("₿ За последние 5 часов крипто-новостей не нашлось.")
    return "\n".join(lines)

# ---------- ГОРOСКОП (Aztro vercel -> RapidAPI fallback) ----------
def fetch_horoscope(sign_en: str) -> str:
    # 1) Vercel Aztro mirror (без ключа)
    try:
        url = f"https://aztro-api.vercel.app/api?sign={sign_en}&day=today"
        r = requests.post(url, timeout=10)
        if r.ok:
            data = r.json()
            desc = (data.get("description") or "").strip()
            if desc:
                return translate_to_ru(desc) or "Гороскоп недоступен."
    except Exception as e:
        log.warning("Aztro(vercel) error: %s", e)

    # 2) Fallback: RapidAPI (бесплатный ключ)
    if RAPIDAPI_KEY:
        try:
            url = "https://horoscope-astrology.p.rapidapi.com/horoscope"
            params = {"sign": sign_en, "day": "today"}
            headers = {
                "X-RapidAPI-Key": RAPIDAPI_KEY,
                "X-RapidAPI-Host": "horoscope-astrology.p.rapidapi.com",
            }
            r = requests.get(url, params=params, headers=headers, timeout=10)
            if r.ok:
                j = r.json()
                desc = (j.get("horoscope") or j.get("prediction") or j.get("message") or "").strip()
                if desc:
                    return translate_to_ru(desc) or "Гороскоп недоступен."
        except Exception as e:
            log.warning("RapidAPI horoscope error: %s", e)

    return "Не удалось получить гороскоп на сегодня."

# ---------- ИВЕНТЫ (Ticketmaster + Eventbrite fallback, сегодня) ----------
def today_utc_range(tz: ZoneInfo):
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z")
    end_utc = end_local.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z")
    return start_utc, end_utc

def events_today_by_city(city: str, size: int = 10):
    if not TM_KEY:
        return {"ok": False, "err": "NO_KEY", "items": []}
    start, end = today_utc_range(TZ)
    url = "https://app.ticketmaster.com/discovery/v2/events.json"
    params = {
        "apikey": TM_KEY,
        "city": city,
        "startDateTime": start,
        "endDateTime": end,
        "size": size,
        "sort": "date,asc",
        "locale": "*",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        evs = (data.get("_embedded") or {}).get("events", [])
        return {"ok": True, "items": evs}
    except Exception as e:
        log.exception("events_today_by_city error: %s", e)
        return {"ok": False, "err": "EXC", "items": []}

def events_today_by_coords(lat: float, lon: float, radius_km: int = 50, size: int = 10):
    if not TM_KEY:
        return {"ok": False, "err": "NO_KEY", "items": []}
    start, end = today_utc_range(TZ)
    url = "https://app.ticketmaster.com/discovery/v2/events.json"
    params = {
        "apikey": TM_KEY,
        "latlong": f"{lat},{lon}",
        "radius": radius_km,
        "unit": "km",
        "startDateTime": start,
        "endDateTime": end,
        "size": size,
        "sort": "date,asc",
        "locale": "*",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        evs = (data.get("_embedded") or {}).get("events", [])
        return {"ok": True, "items": evs}
    except Exception as e:
        log.exception("events_today_by_coords error: %s", e)
        return {"ok": False, "err": "EXC", "items": []}

# ---------- EVENTBRITE (фолбэк, если Ticketmaster пусто) ----------
def events_today_eventbrite_by_city(city: str, size: int = 10):
    if not EVENTBRITE_TOKEN:
        return {"ok": False, "err": "NO_KEY", "items": []}
    start, end = today_utc_range(TZ)
    url = "https://www.eventbriteapi.com/v3/events/search/"
    params = {
        "location.address": city,
        "start_date.range_start": start,
        "start_date.range_end": end,
        "expand": "venue",
        "sort_by": "date",
        "page_size": size,
    }
    try:
        r = requests.get(url, params=params, headers={"Authorization": f"Bearer {EVENTBRITE_TOKEN}"}, timeout=15)
        data = r.json()
        return {"ok": True, "items": data.get("events", [])}
    except Exception as e:
        log.exception("events_today_eventbrite_by_city error: %s", e)
        return {"ok": False, "err": "EXC", "items": []}

def events_today_eventbrite_by_coords(lat: float, lon: float, radius_km: int = 50, size: int = 10):
    if not EVENTBRITE_TOKEN:
        return {"ok": False, "err": "NO_KEY", "items": []}
    start, end = today_utc_range(TZ)
    url = "https://www.eventbriteapi.com/v3/events/search/"
    params = {
        "location.latitude": lat,
        "location.longitude": lon,
        "location.within": f"{radius_km}km",
        "start_date.range_start": start,
        "start_date.range_end": end,
        "expand": "venue",
        "sort_by": "date",
        "page_size": size,
    }
    try:
        r = requests.get(url, params=params, headers={"Authorization": f"Bearer {EVENTBRITE_TOKEN}"}, timeout=15)
        data = r.json()
        return {"ok": True, "items": data.get("events", [])}
    except Exception as e:
        log.exception("events_today_eventbrite_by_coords error: %s", e)
        return {"ok": False, "err": "EXC", "items": []}

def pick_event_text_tm(ev: dict) -> str:
    txt = (ev.get("info") or "").strip()
    if not txt:
        txt = (ev.get("pleaseNote") or "").strip()
    if not txt:
        try:
            promos = ev.get("promoter") or {}
            if isinstance(promos, dict):
                cand = promos.get("description") or ""
                txt = (cand or "").strip()
        except Exception:
            pass
    return txt

def pick_event_text_eb(ev: dict) -> str:
    # Eventbrite: description.text может быть очень длинным — урежем и переведём
    desc = ((ev.get("description") or {}).get("text") or "").strip()
    return desc

def truncate(s: str, n: int = 280) -> str:
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"

def fmt_events_today(city_name: str, events_tm: list, events_eb: list) -> str:
    events: List[str] = []
    # Сначала Ticketmaster
    for ev in events_tm[:10]:
        name = (ev.get("name") or "Без названия").strip()
        dates = (ev.get("dates") or {}).get("start", {})
        local_date = dates.get("localDate", "")
        local_time = dates.get("localTime", "")
        venue = ""
        try:
            venue = ev["_embedded"]["venues"][0]["name"]
        except Exception:
            pass
        when = local_date
        if local_time:
            when += f" {local_time[:5]}"
        desc_raw = pick_event_text_tm(ev)
        desc_ru = translate_to_ru(desc_raw) if desc_raw else ""
        piece = f"• {name}"
        if venue:
            piece += f" — {venue}"
        if when.strip():
            piece += f" ({when})"
        if desc_ru:
            piece += f"\n  {truncate(desc_ru)}"
        url = (ev.get("url") or "").strip()
        if url:
            piece += f"\n  {url}"
        events.append(piece)

    # Потом Eventbrite (если TM пусто — будут только EB)
    if not events_tm:
        for ev in events_eb[:10]:
            name = (ev.get("name") or {}).get("text") or "Без названия"
            name = name.strip()
            when = ""
            try:
                # local приводим как есть (Eventbrite отдаёт ISO)
                start = (ev.get("start") or {}).get("local") or ""
                if start:
                    when = start.replace("T", " ")[:16]
            except Exception:
                pass
            venue = ""
            try:
                venue = ((ev.get("venue") or {}).get("name") or "").strip()
            except Exception:
                pass
            desc_raw = pick_event_text_eb(ev)
            desc_ru = translate_to_ru(desc_raw) if desc_raw else ""
            url = (ev.get("url") or "").strip()

            piece = f"• {name}"
            if venue:
                piece += f" — {venue}"
            if when:
                piece += f" ({when})"
            if desc_ru:
                piece += f"\n  {truncate(desc_ru)}"
            if url:
                piece += f"\n  {url}"
            events.append(piece)

    if not events:
        return f"🎭 Сегодня в {city_name} событий не нашлось."
    return "🎭 События сегодня в {}:\n\n{}".format(city_name, "\n".join(events))

# ---------- ПЛАНИРОВЩИК (персональные рассылки) ----------
user_daily_jobs: Dict[int, Job] = {}

async def send_daily_one(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data["chat_id"]
    u = ensure_defaults(chat_id)
    try:
        # Погода today
        msg = await get_today_msg(context, chat_id)
        txt = "⏰ Ежедневный прогноз:\n\n" + msg

        # Гороскоп, если включён
        if u.get("horo_enabled") and u.get("horo_sign"):
            sign = u["horo_sign"]
            htxt = fetch_horoscope(sign)
            sign_ru = [k for k, v in ZODIAC_MAP_RU_EN.items() if v == sign and k.isalpha() and len(k) > 2]
            sign_ru = sign_ru[0].capitalize() if sign_ru else sign.capitalize()
            txt += f"\n\n🔮 Гороскоп ({sign_ru}):\n{htxt}"

        await context.bot.send_message(chat_id, txt)
    except Exception as e:
        log.error("Ошибка отправки ежедневного сообщения %s: %s", chat_id, e)

def schedule_daily_for(app, chat_id: int, hour: int):
    old = user_daily_jobs.get(chat_id)
    if old:
        old.schedule_removal()
    t = dtime(hour=hour, minute=0, tzinfo=TZ)
    job = app.job_queue.run_daily(send_daily_one, time=t, data={"chat_id": chat_id}, name=f"daily_{chat_id}")
    user_daily_jobs[chat_id] = job

# ---------- ПОГОДНЫЕ ХЭНДЛЕРЫ (сборка текста) ----------
async def get_today_msg(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> str:
    u = ensure_defaults(chat_id)
    if u.get("mode") == "geo" and u.get("coords"):
        lat, lon = u["coords"]
        res = current_by_coords(lat, lon)
    else:
        res = current_by_city(u.get("city", "Praha"))
    if not res:
        return "Не удалось получить прогноз."
    name, temp, feels, wind, desc = res
    return fmt_now(name, temp, feels, wind, desc)

async def get_tomorrow_msg(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> str:
    u = ensure_defaults(chat_id)
    if u.get("mode") == "geo" and u.get("coords"):
        lat, lon = u["coords"]
        res = tomorrow_by_coords(lat, lon)
    else:
        res = tomorrow_by_city(u.get("city", "Praha"))
    if not res:
        return "Не удалось получить прогноз на завтра."
    name, tmin, tmax, wind_noon, desc_noon, pop_max = res
    return fmt_tomorrow(name, tmin, tmax, wind_noon, desc_noon, pop_max)

# ---------- КОМАНДЫ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    u = ensure_defaults(chat_id)

    # Планируем персональную рассылку (если не спланирована ранее)
    schedule_daily_for(context.application, chat_id, u["daily_hour"])

    # Если гороскоп ещё не настроен — спросим
    if u.get("horo_enabled") is None:
        context.chat_data["awaiting_horo_yesno"] = True
        await update.message.reply_text(
            "Добавлять гороскоп в ежедневную рассылку?", reply_markup=yesno_kb()
        )
        return

    await update.message.reply_text(
        "Готово! Пользуйся меню команд:\n"
        "/weather — погода\n"
        "/news — мировые + крипто новости (последние 5 часов)\n"
        "/horoscope — гороскоп на сегодня\n"
        "/events — события сегодня в городе\n"
        "/settings — настройки",
        reply_markup=ReplyKeyboardRemove(),
    )

async def cmd_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ensure_defaults(chat_id)
    context.chat_data["weather_mode"] = True
    await update.message.reply_text(
        "Выбери: today / tomorrow.\nИ источник ниже: Praha или 📍 геолокация.",
        reply_markup=weather_kb()
    )

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await get_today_msg(context, update.effective_chat.id)
    await update.message.reply_text(msg, reply_markup=weather_kb())

async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await get_tomorrow_msg(context, update.effective_chat.id)
    await update.message.reply_text(msg, reply_markup=weather_kb())

async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = fetch_news_rss(hours=5, max_world=3, max_crypto=3)
    txt = fmt_news_rss(data.get("world", []), data.get("crypto", []))
    await update.message.reply_text(txt)

async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    u = ensure_defaults(chat_id)

    # Ticketmaster
    if u.get("mode") == "geo" and u.get("coords"):
        lat, lon = u["coords"]
        r_tm = events_today_by_coords(lat, lon)
        city_name = "твоём районе"
        # Eventbrite fallback
        r_eb = events_today_eventbrite_by_coords(lat, lon) if (r_tm.get("items") == []) else {"ok": True, "items": []}
    else:
        city = u.get("city", "Praha")
        r_tm = events_today_by_city(city)
        city_name = city
        r_eb = events_today_eventbrite_by_city(city) if (r_tm.get("items") == []) else {"ok": True, "items": []}

    if not r_tm["ok"] and not r_eb["ok"]:
        await update.message.reply_text("🛈 Функция событий не активирована (нет ключей) или временно недоступна.")
        return

    tm_items = r_tm.get("items") or []
    eb_items = r_eb.get("items") or []
    msg = fmt_events_today(city_name, tm_items, eb_items)
    await update.message.reply_text(msg)

async def cmd_horoscope(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    u = ensure_defaults(chat_id)
    sign = u.get("horo_sign")
    if not sign:
        context.chat_data["awaiting_zodiac_pick"] = True
        await update.message.reply_text("Выбери свой знак зодиака:", reply_markup=zodiac_kb())
        return
    text = fetch_horoscope(sign)
    sign_ru = [k for k, v in ZODIAC_MAP_RU_EN.items() if v == sign and k.isalpha() and len(k) > 2]
    sign_ru = sign_ru[0].capitalize() if sign_ru else sign.capitalize()
    await update.message.reply_text(f"🔮 Гороскоп ({sign_ru}):\n{text}")

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    u = ensure_defaults(chat_id)
    context.chat_data["settings_mode"] = True
    await update.message.reply_text(
        f"⚙️ Настройки:\n• Время рассылки: {u['daily_hour']:02d}:00\n• Город: {u.get('city','Praha')}\n"
        f"• Гороскоп: {'включён' if u.get('horo_enabled') else 'выключен' if u.get('horo_enabled') is not None else 'не настроен'}",
        reply_markup=settings_kb(),
    )

# ---------- ОБРАБОТКА ТЕКСТА / КНОПОК ----------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    u = ensure_defaults(chat_id)

    # --- настройка гороскопа (вопрос Да/Нет при старте) ---
    if context.chat_data.get("awaiting_horo_yesno"):
        if text.lower() == "да":
            u["horo_enabled"] = True
            set_user(chat_id, u)
            context.chat_data.pop("awaiting_horo_yesno", None)
            context.chat_data["awaiting_zodiac_pick"] = True
            await update.message.reply_text("Окей! Выбери знак зодиака:", reply_markup=zodiac_kb())
            return
        elif text.lower() == "нет":
            u["horo_enabled"] = False
            set_user(chat_id, u)
            context.chat_data.pop("awaiting_horo_yesno", None)
            await update.message.reply_text("Гороскоп отключён. Можно включить в /settings.", reply_markup=ReplyKeyboardRemove())
            return
        else:
            await update.message.reply_text("Ответь «Да» или «Нет».", reply_markup=yesno_kb())
            return

    # --- выбор знака зодиака ---
    if context.chat_data.get("awaiting_zodiac_pick"):
        if text.lower() == "отмена":
            context.chat_data.pop("awaiting_zodiac_pick", None)
            await update.message.reply_text("Ок, отменил.", reply_markup=ReplyKeyboardRemove())
            return
        sign = normalize_sign(text)
        if not sign:
            await update.message.reply_text("Выбери знак из клавиатуры ниже.", reply_markup=zodiac_kb())
            return
        u["horo_sign"] = sign
        u.setdefault("horo_enabled", True)
        set_user(chat_id, u)
        context.chat_data.pop("awaiting_zodiac_pick", None)
        sign_ru = [k for k, v in ZODIAC_MAP_RU_EN.items() if v == sign and k.isalpha() and len(k) > 2]
        sign_ru = sign_ru[0].capitalize() if sign_ru else sign.capitalize()
        await update.message.reply_text(f"Готово! Сохранил знак: {sign_ru}.", reply_markup=ReplyKeyboardRemove())
        return

    # --- меню настроек ---
    if context.chat_data.get("settings_mode"):
        if text == "⏰ Время рассылки":
            context.chat_data["awaiting_hour"] = True
            await update.message.reply_text("Выбери час (0–23) или «Ввести вручную».", reply_markup=hours_kb())
            return
        if context.chat_data.get("awaiting_hour"):
            if text.lower() == "отмена":
                context.chat_data.pop("awaiting_hour", None)
                await update.message.reply_text("Ок, отменил.", reply_markup=settings_kb())
                return
            if text.lower() == "ввести вручную":
                await update.message.reply_text("Напиши час числом (0–23).", reply_markup=ReplyKeyboardRemove())
                return
            try:
                hour = int(text)
                if not (0 <= hour <= 23):
                    raise ValueError
            except ValueError:
                await update.message.reply_text("Час должен быть числом от 0 до 23. Попробуй ещё раз.")
                return
            u["daily_hour"] = hour
            set_user(chat_id, u)
            schedule_daily_for(context.application, chat_id, hour)
            context.chat_data.pop("awaiting_hour", None)
            await update.message.reply_text(f"✅ Ежедневная рассылка в {hour:02d}:00 (Europe/Prague).", reply_markup=settings_kb())
            return

        if text == "🌆 Изменить город":
            context.chat_data["awaiting_city"] = True
            await update.message.reply_text("Введи название города (например: Praha).", reply_markup=ReplyKeyboardRemove())
            return

        if context.chat_data.get("awaiting_city"):
            city = text.strip()
            if not city:
                await update.message.reply_text("Введите корректное название города.")
                return
            u["city"] = city
            u["mode"] = "city"
            set_user(chat_id, u)
            context.chat_data.pop("awaiting_city", None)
            await update.message.reply_text(f"Город установлен: {city}", reply_markup=settings_kb())
            return

        if text == "♈ Знак зодиака":
            context.chat_data["awaiting_zodiac_pick"] = True
            await update.message.reply_text("Выбери знак зодиака:", reply_markup=zodiac_kb())
            return

        if text == "🔙 Назад":
            context.chat_data.pop("settings_mode", None)
            await update.message.reply_text("Ок. Используй команды меню: /weather /news /horoscope /events /settings", reply_markup=ReplyKeyboardRemove())
            return

        await update.message.reply_text("Выбери пункт меню настроек.", reply_markup=settings_kb())
        return

    # --- режим /weather (клавиатура today/tomorrow/Praha/гео) ---
    if context.chat_data.get("weather_mode"):
        tl = text.lower()
        if tl == "today":
            await cmd_today(update, context)
            return
        if tl == "tomorrow":
            await cmd_tomorrow(update, context)
            return
        if tl == "praha":
            u["mode"] = "city"
            u["city"] = "Praha"
            set_user(chat_id, u)
            await update.message.reply_text("Источник: Praha ✅", reply_markup=weather_kb())
            return
        if tl == "🔙 назад".lower():
            context.chat_data.pop("weather_mode", None)
            await update.message.reply_text("Ок. Используй команды меню: /weather /news /horoscope /events /settings", reply_markup=ReplyKeyboardRemove())
            return
        await update.message.reply_text("Нажми today / tomorrow или выбери источник ниже.", reply_markup=weather_kb())
        return

    # если ничего не подошло:
    await update.message.reply_text("Команды: /weather /news /horoscope /events /settings")

# ---------- ЛОКАЦИЯ ----------
async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    loc = update.message.location
    if not loc:
        return
    u = ensure_defaults(chat_id)
    u["mode"] = "geo"
    u["coords"] = [loc.latitude, loc.longitude]
    set_user(chat_id, u)
    await update.message.reply_text("Источник: текущая геолокация ✅", reply_markup=weather_kb())

# ---------- РЕГИСТРАЦИЯ КОМАНД, ЗАПУСК ----------
async def post_init(app):
    # на всякий случай убираем возможный webhook, чтобы polling не конфликтовал
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass

    await app.bot.set_my_commands([
        BotCommand("start", "запуск бота"),
        BotCommand("weather", "погода: сегодня/завтра"),
        BotCommand("news", "мировые + крипто новости (5 часов)"),
        BotCommand("horoscope", "гороскоп на сегодня"),
        BotCommand("events", "события сегодня в городе"),
        BotCommand("settings", "настройки"),
    ])

def main():
    if not TOKEN or not OW_KEY:
        raise RuntimeError("Нет TELEGRAM_BOT_TOKEN или OPENWEATHER_API_KEY")

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("weather", cmd_weather))
    app.add_handler(CommandHandler("today", cmd_today))       # на всякий случай
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow)) # на всякий случай
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("events", cmd_events))
    app.add_handler(CommandHandler("horoscope", cmd_horoscope))
    app.add_handler(CommandHandler("settings", cmd_settings))

    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Перезапустим персональные джобы для всех, кто уже в базе
    for cid in [int(cid) for cid in load_db()["users"].keys()]:
        u = ensure_defaults(cid)
        schedule_daily_for(app, cid, u["daily_hour"])

    log.info("Бот запущен ✅")
    app.run_polling()

if __name__ == "__main__":
    main()
