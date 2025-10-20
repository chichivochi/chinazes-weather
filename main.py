import os
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta, time as dtime
from typing import Optional, Tuple, Dict, Any, List
from zoneinfo import ZoneInfo
from html import unescape
import re

import requests
import feedparser
from bs4 import BeautifulSoup
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

# ---------------- ПЕРЕВОД НА РУССКИЙ (DeepL: опционально) ----------------
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")

def translate_to_ru(text: str) -> str:
    if not text:
        return text
    if not DEEPL_API_KEY:
        return text
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

# ---------------- ЛОГИ ----------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("assistant-bot")

# ---------------- КЛЮЧИ / НАСТРОЙКИ ----------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OW_KEY = os.getenv("OPENWEATHER_API_KEY")
NEWS_TG_CHANNEL = os.getenv("NEWS_TG_CHANNEL_USERNAME", "").strip().lstrip("@")  # например: tictacnews1

TZ = ZoneInfo("Europe/Prague")
DEFAULT_SEND_HOUR = 7

# ---------------- БД (JSON) ----------------
DB_PATH = Path("users.db")

def load_db() -> Dict[str, Any]:
    if DB_PATH.exists():
        try:
            return json.loads(DB_PATH.read_text("utf-8"))
        except Exception:
            log.warning("users.db повреждён, создаю новый.")
    return {"users": {}}

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
    u.setdefault("coords", None)            # [lat, lon]
    u.setdefault("daily_hour", DEFAULT_SEND_HOUR)
    u.setdefault("horo_enabled", None)      # None -> спросить; True/False
    u.setdefault("horo_sign", None)         # 'leo', 'scorpio' ...
    set_user(chat_id, u)
    return u

# ---------------- КЛАВИАТУРЫ ----------------
def weather_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["today", "tomorrow"],
            ["Praha", KeyboardButton("📍 Моя геолокация", request_location=True)],
            ["🔙 Назад"],
        ],
        resize_keyboard=True,
    )

def settings_kb(u: dict) -> ReplyKeyboardMarkup:
    if u.get("horo_enabled") is None:
        horo_label = "Не настроен"
    else:
        horo_label = "Включён" if u.get("horo_enabled") else "Выключен"
    return ReplyKeyboardMarkup(
        [
            ["⏰ Время рассылки", "🌆 Изменить город"],
            [f"🔮 Гороскоп: {horo_label}"],
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

# ---------------- ЗОДИАК ----------------
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

# ---------------- ПОГОДА (OpenWeather) ----------------
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

# ---------------- СОВЕТЫ ПО ОДЕЖДЕ ----------------
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
        lines.append("Очень холодно 🥶. Термобельё + флис/шерсть + зимняя куртка. Шапка, шарф, тёплые перчатки. Тёплая обувь.")
    elif 0 < temp_c <= 5:
        lines.append("Холодно ❄️. Тёплая куртка + свитер/худи. Желательны шапка и перчатки. Обувь — утеплённая.")
    elif 5 < temp_c <= 12:
        lines.append("Прохладно 🌬. Лёгкая куртка/ветровка или худи, можно тонкий свитер. Обувь — закрытая.")
    elif 12 < temp_c <= 20:
        lines.append("Умеренно 🌤. Футболка/лонгслив, при желании лёгкая куртка. Обувь — кроссовки, мокасины.")
    else:
        lines.append("Тепло/жарко ☀️. Лёгкая одежда (хлопок/лён), шорты/платье. Пей воду, избегай палящего солнца.")

    if is_rain:
        if is_heavy:
            lines.append("🌧 Сильный дождь — зонт/плащ, куртка с капюшоном и водостойкая обувь.")
        elif is_light:
            lines.append("🌦 Возможна морось — зонт или лёгкий дождевик.")
        else:
            lines.append("☔️ Ожидается дождь — возьми зонт и обувь, не боящуюся воды.")
    if is_snow:
        lines.append("❄️ Снег — непромокаемая тёплая обувь и утеплённые перчатки.")
    if is_storm:
        lines.append("⛈ Гроза — избегай открытых мест и высоких конструкций.")
    if very_windy:
        lines.append("💨 Очень ветрено — ветрозащитная куртка/капюшон, прикрой уши/шею.")
    elif windy:
        lines.append("💨 Ветрено — возьми ветровку или вещь с высоким воротником.")
    if is_clear and not is_snow:
        lines.append("😎 Ясно — солнечные очки пригодятся; вода — тоже.")
    if is_clear and is_snow:
        lines.append("😎☃️ Ясно и снежно — очки особенно кстати: снег сильно отражает свет.")
    if temp_c > 25 and is_clear:
        lines.append("🧴 Используй SPF по возможности.")
    return "\n".join(lines)

# ---------------- ФОРМАТЫ ПОГОДЫ ----------------
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

# ---------------- ГОРOСКОП (ru.astrologyk.com — ежедневный) ----------------
def fetch_horoscope(sign_en: str) -> str:
    slug = (sign_en or "").strip().lower()
    if slug not in {
        "aries","taurus","gemini","cancer","leo","virgo",
        "libra","scorpio","sagittarius","capricorn","aquarius","pisces"
    }:
        return "Не распознал знак зодиака."

    now = datetime.now(TZ)
    today = now.date()

    # ---------- helpers ----------
    import random, difflib

    def cache_buster() -> str:
        # агрессивно бьём кэш: поминутно + случайный хвост
        return now.strftime("%Y%m%d%H%M") + f"{random.randint(1000,9999)}"

    def robust_get(url: str, lang: str = "ru", timeout: int = 12):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7" if lang=="ru" else "en-US,en;q=0.9,ru;q=0.6",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://www.google.com/",
        }
        for _ in range(3):
            try:
                r = requests.get(f"{url}?_cb={cache_buster()}", headers=headers, timeout=timeout)
                if r.status_code == 200 and "<html" in r.text.lower():
                    return r.text
            except Exception:
                pass
        return None

    RU_MONTHS = {
        "января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,
        "июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12
    }
    EN_MONTHS = {m:i for i,m in enumerate(
        ["january","february","march","april","may","june","july","august",
         "september","october","november","december"], 1)}

    def extract_date(text: str, lang: str) -> Optional[datetime]:
        tl = text.lower()
        if lang == "ru":
            m = re.search(r'(\d{1,2})\s+(' + "|".join(RU_MONTHS.keys()) + r')\s+(\d{4})', tl)
            if not m: return None
            d, mon, y = int(m.group(1)), RU_MONTHS[m.group(2)], int(m.group(3))
        else:
            m = re.search(r'(' + "|".join(EN_MONTHS.keys()) + r')\s+(\d{1,2}),\s*(\d{4})', tl)
            if not m: return None
            mon, d, y = EN_MONTHS[m.group(1)], int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mon, d, tzinfo=TZ)
        except Exception:
            return None

    # === твой «жёсткий» парсер (без изменений) ===
    junk_keywords = [
        "все знаки", "сегодня", "завтра", "неделя", "месяц", "любовь",
        "работа", "здоровье", "китайский", "персональный", "гороскоп 202",
    ]
    zodiac_words = ["овен","телец","близнецы","рак","лев","дева","весы",
                    "скорпион","стрелец","козерог","водолей","рыбы"]

    def pick_text_preserving_your_rules(soup: BeautifulSoup) -> str:
        container = (
            soup.find("article")
            or soup.find("div", class_=re.compile("entry-content|post|content"))
            or soup
        )
        pieces: List[str] = []
        chars = 0
        for node in container.find_all(["p", "div"], recursive=True):
            classes = " ".join(node.get("class", [])).lower()
            if any(k in classes for k in ("tags", "share", "social", "breadcrumbs", "related", "nav")):
                continue
            links_count = len(node.find_all("a"))
            text = node.get_text(" ", strip=True)
            if not text:
                continue
            if len(text) < 50:
                continue
            if links_count >= 3:
                continue
            tl = text.lower()
            if sum(1 for w in junk_keywords if w in tl) >= 2:
                continue
            if sum(1 for w in zodiac_words if w in tl) >= 4:
                continue
            pieces.append(text)
            chars += len(text)
            if chars > 900 or len(pieces) >= 3:
                break
        clean = " ".join(pieces).strip()
        if not clean:
            clean = "Не удалось извлечь текст гороскопа. Открой ссылку ниже."
        if len(clean) > 1400:
            clean = clean[:1398].rstrip() + "…"
        return clean

    def fetch_page(base_url: str, lang: str) -> Tuple[Optional[str], Optional[datetime], Optional[str]]:
        html = robust_get(base_url, lang=lang)
        if not html:
            return None, None, None
        soup = BeautifulSoup(html, "lxml")
        text = pick_text_preserving_your_rules(soup)
        if not text:
            return None, None, None
        src_date = extract_date(soup.get_text(" ", strip=True), lang)
        return text, src_date, base_url

    # --- URL-ы ---
    RU_DAILY = f"https://ru.astrologyk.com/horoscope/daily/{slug}"
    EN_DAILY = f"https://astrologyk.com/horoscope/daily/{slug}"
    RU_TOMOR = f"https://ru.astrologyk.com/horoscope/tomorrow/{slug}"
    EN_TOMOR = f"https://astrologyk.com/horoscope/tomorrow/{slug}"
    RU_YEST = f"https://ru.astrologyk.com/horoscope/yesterday/{slug}"

    # --- эвристики «вчера» ---
    def _normalize_for_compare(s: str) -> str:
        s = re.sub(r'\s+', ' ', s.lower()).strip()
        s = re.sub(r'[«»"“”„…—–\-—:;,.!?()\[\]]+', '', s)
        return s

    def looks_like_yesterday(daily_text: str) -> bool:
        try:
            y_txt, _, _ = fetch_page(RU_YEST, "ru")
            if not y_txt or not daily_text:
                return False
            # сравниваем первые 400 символов (после нормализации)
            head_d = _normalize_for_compare(daily_text)[:400]
            head_y = _normalize_for_compare(y_txt)[:400]
            if not head_d or not head_y:
                return False
            if head_d == head_y:
                return True
            ratio = difflib.SequenceMatcher(a=head_d, b=head_y).ratio()
            if ratio >= 0.88:
                return True
            # доп. метрика: Jaccard по словам
            set_d = set(head_d.split())
            set_y = set(head_y.split())
            if set_d and (len(set_d & set_y) / len(set_d | set_y)) >= 0.8:
                return True
        except Exception:
            return False
        return False

    # 1) RU /daily
    txt, dt_src, src = fetch_page(RU_DAILY, "ru")

    # 1a) если даты нет или дата < today — проверяем схожесть с /yesterday
    stale = False
    if txt:
        if (dt_src and dt_src.date() < today) or (dt_src is None and looks_like_yesterday(txt)):
            stale = True

    # 2) если пусто или «вчера» — EN /daily (переведём в RU при наличии DEEPL_API_KEY)
    if (not txt) or stale:
        t2, d2, s2 = fetch_page(EN_DAILY, "en")
        if t2:
            txt = translate_to_ru(t2) or t2
            dt_src = d2 or dt_src
            src = s2 or src
            # после замены ещё раз проверим «вчера» (англ. daily иногда тоже отстаёт)
            if (dt_src and dt_src.date() < today) or (dt_src is None and looks_like_yesterday(txt)):
                stale = True
            else:
                stale = False

    # 3) если по-прежнему «вчера» — берём /tomorrow (RU; если нет — EN→перевод)
    if txt and stale:
        t3, d3, s3 = fetch_page(RU_TOMOR, "ru")
        if not t3:
            t3, d3, s3 = fetch_page(EN_TOMOR, "en")
            if t3:
                t3 = translate_to_ru(t3) or t3
        if t3:
            txt, dt_src, src = t3, (d3 or dt_src), (s3 or src)

    if not txt:
        return "Гороскоп временно недоступен."

    # финал
    if dt_src:
        txt += f"\n\nИсточник: {src}\nДата источника: {dt_src.strftime('%d.%m.%Y')}"
    else:
        txt += f"\n\nИсточник: {src}"
    return txt
    
# ---------------- НОВОСТИ: 3 ПОСЛЕДНИХ ПОСТА ИЗ ТГ-КАНАЛА ----------------
_TG_RSS_ENDPOINTS = [
    "https://rsshub.app/telegram/channel/{u}",
    "https://rsshub.io/telegram/channel/{u}",
    "https://rsshub.rssforever.com/telegram/channel/{u}",
    "https://tg.i-c-a.su/rss/{u}",
]

def strip_html(html: str) -> str:
    """Чистим HTML в простой текст (без parse_mode)."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    text = soup.get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text

def _parse_rss_generic(url: str) -> List[dict]:
    feed = feedparser.parse(url)
    items = []
    for e in feed.entries:
        title = strip_html(unescape(getattr(e, "title", "") or "").strip())
        summ  = strip_html(unescape(getattr(e, "summary", "") or "").strip())
        link  = (getattr(e, "link", "") or "").strip()
        dt = None
        try:
            raw = getattr(e, "published", None) or getattr(e, "updated", None)
            dt = parsedate_to_datetime(raw) if raw else None
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        except Exception:
            dt = None
        items.append({"title": title, "summary": summ, "link": link, "published": dt})
    return items

def fetch_tg_channel_latest(n: int = 3) -> List[dict]:
    u = NEWS_TG_CHANNEL
    if not u:
        return []
    for tpl in _TG_RSS_ENDPOINTS:
        url = tpl.format(u=u)
        try:
            items = _parse_rss_generic(url)
            if items:
                with_ts = [it for it in items if it.get("published")]
                if with_ts:
                    with_ts.sort(key=lambda x: x["published"], reverse=True)
                    items = with_ts + [it for it in items if not it.get("published")]
                return items[:n]
        except Exception as e:
            log.warning("TG RSS fetch failed for %s: %s", url, e)
            continue
    return []

def fmt_tg_news(items: List[dict]) -> str:
    if not items:
        ch = NEWS_TG_CHANNEL or "канала"
        return f"🗞 Не удалось получить новости из @{ch}."
    lines = ["🗞 Последние 3 новости из Telegram-канала:", ""]
    for it in items:
        ttl  = translate_to_ru(it.get("title", "")) or ""
        summ = translate_to_ru(it.get("summary", "")) or ""
        link = it.get("link", "")
        text = ttl if len(ttl) > len(summ) else summ
        text = text.strip() or ttl or summ or "Без текста"
        if len(text) > 400:
            text = text[:397].rstrip() + "…"
        piece = f"• {text}"
        if link:
            piece += f"\n  {link}"
        lines.append(piece)
    return "\n".join(lines)

# ---------------- ПЛАНИРОВЩИК (ежедневная рассылка) ----------------
user_daily_jobs: Dict[int, Job] = {}

async def send_daily_one(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data["chat_id"]
    u = ensure_defaults(chat_id)
    try:
        msg = await get_today_msg(context, chat_id)
        txt = "⏰ Ежедневный прогноз:\n\n" + msg

        if u.get("horo_enabled") and u.get("horo_sign"):
            sign = u["horo_sign"]
            htxt = fetch_horoscope(sign)
            sign_ru = [k for k, v in ZODIAC_MAP_RU_EN.items() if v == sign and k.isalpha() and len(k) > 2]
            sign_ru = sign_ru[0].capitalize() if sign_ru else sign.capitalize()
            txt += f"\n\n🔮 Гороскоп ({sign_ru}):\n{htxt}"

        if NEWS_TG_CHANNEL:
            items = fetch_tg_channel_latest(n=3)
            news_block = fmt_tg_news(items)
            txt += f"\n\n{news_block}"

        await context.bot.send_message(chat_id, txt)
    except Exception as e:
        log.error("Ошибка отправки ежедневного сообщения %s: %s", chat_id, e)

def schedule_daily_for(app, chat_id: int, hour: int):
    # Обёртка: если нет job_queue (не установлены extras), просто логируем
    jq = getattr(app, "job_queue", None)
    if jq is None:
        log.warning("JobQueue не доступна. Установи пакет с extras: pip install 'python-telegram-bot[job-queue]'.")
        return
    old = user_daily_jobs.get(chat_id)
    if old:
        old.schedule_removal()
    t = dtime(hour=hour, minute=0, tzinfo=TZ)
    job = jq.run_daily(send_daily_one, time=t, data={"chat_id": chat_id}, name=f"daily_{chat_id}")
    user_daily_jobs[chat_id] = job

# ---------------- СБОРКА ТЕКСТА ПОГОДЫ ----------------
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

# ---------------- ВСПОМОГАТЕЛЬНОЕ: парсер часа ----------------
def parse_hour(text: str) -> Optional[int]:
    """
    Извлекает час 0–23 из строки: '7', '07', '13:00', '13.00', '13 ч',
    допускает невидимые юникод-символы и пробелы.
    """
    if not text:
        return None
    cleaned = re.sub(r'[\u200b-\u200f\u202a-\u202e\u2060\s]+', ' ', text).strip().lower()
    m = re.search(r'(^|\D)(\d{1,2})(?:\D?\d{2})?(\D|$)', cleaned)
    if not m:
        return None
    try:
        h = int(m.group(2))
        if 0 <= h <= 23:
            return h
    except Exception:
        pass
    return None

# ---------------- КОМАНДЫ ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    u = ensure_defaults(chat_id)

    # сбрасываем, чтобы не пересекались режимы
    context.chat_data.pop("weather_mode", None)

    schedule_daily_for(context.application, chat_id, u["daily_hour"])

    if u.get("horo_enabled") is None:
        context.chat_data["awaiting_horo_yesno"] = True
        await update.message.reply_text(
            "Добавлять гороскоп в ежедневную рассылку?", reply_markup=yesno_kb()
        )
        return

    await update.message.reply_text(
        "Готово! Команды:\n"
        "/weather — погода (today/tomorrow)\n"
        "/news — 3 последние новости из канала\n"
        "/horoscope — гороскоп на сегодня\n"
        "/settings — настройки",
        reply_markup=ReplyKeyboardRemove(),
    )

async def cmd_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_defaults(update.effective_chat.id)
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
    if not NEWS_TG_CHANNEL:
        await update.message.reply_text("Источник канала не настроен. Укажи NEWS_TG_CHANNEL_USERNAME без @.")
        return
    items = fetch_tg_channel_latest(n=3)
    await update.message.reply_text(fmt_tg_news(items))

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
    context.chat_data.pop("weather_mode", None)
    await update.message.reply_text(
        f"⚙️ Настройки:\n• Время рассылки: {u['daily_hour']:02d}:00\n• Город: {u.get('city','Praha')}\n"
        f"• Гороскоп: {'включён' if u.get('horo_enabled') else 'выключен' if u.get('horo_enabled') is not None else 'не настроен'}",
        reply_markup=settings_kb(u),
    )

# ---------------- ОБРАБОТКА ТЕКСТА ----------------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    u = ensure_defaults(chat_id)

    # настройка гороскопа (Да/Нет)
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

    # выбор знака
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

    # настройки
    if context.chat_data.get("settings_mode"):
        # тумблер гороскопа
        if text.startswith("🔮"):
            if not u.get("horo_sign"):
                u["horo_enabled"] = True
                set_user(chat_id, u)
                context.chat_data["awaiting_zodiac_pick"] = True
                await update.message.reply_text("Выбери знак зодиака (для гороскопа):", reply_markup=zodiac_kb())
                return
            u["horo_enabled"] = not bool(u.get("horo_enabled"))
            set_user(chat_id, u)
            state = "включён ✅" if u["horo_enabled"] else "выключен 🚫"
            await update.message.reply_text(f"Гороскоп в ежедневной рассылке {state}.", reply_markup=settings_kb(u))
            return

        if text == "⏰ Время рассылки":
            context.chat_data["awaiting_hour"] = True
            await update.message.reply_text("Выбери час (0–23) или «Ввести вручную».", reply_markup=hours_kb())
            return

        if context.chat_data.get("awaiting_hour"):
            if text.lower() == "отмена":
                context.chat_data.pop("awaiting_hour", None)
                await update.message.reply_text("Ок, отменил.", reply_markup=settings_kb(u))
                return
            if text.lower() == "ввести вручную":
                await update.message.reply_text("Напиши час числом (0–23).", reply_markup=ReplyKeyboardRemove())
                return

            hour = parse_hour(text)
            if hour is None:
                await update.message.reply_text("Час должен быть числом от 0 до 23. Попробуй ещё раз.")
                return

            u["daily_hour"] = hour
            set_user(chat_id, u)
            schedule_daily_for(context.application, chat_id, hour)
            context.chat_data.pop("awaiting_hour", None)
            await update.message.reply_text(
                f"✅ Ежедневная рассылка в {hour:02d}:00 (Europe/Prague).",
                reply_markup=settings_kb(u)
            )
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
            await update.message.reply_text(f"Город установлен: {city}", reply_markup=settings_kb(u))
            return

        if text == "♈ Знак зодиака":
            context.chat_data["awaiting_zodiac_pick"] = True
            await update.message.reply_text("Выбери знак зодиака:", reply_markup=zodiac_kb())
            return

        if text == "🔙 Назад":
            context.chat_data.pop("settings_mode", None)
            await update.message.reply_text("Ок. Команды: /weather /news /horoscope /settings", reply_markup=ReplyKeyboardRemove())
            return

        await update.message.reply_text("Выбери пункт меню настроек.", reply_markup=settings_kb(u))
        return

    # weather режим
    if context.chat_data.get("weather_mode"):
        tl = text.lower()
        if tl == "today":
            await cmd_today(update, context); return
        if tl == "tomorrow":
            await cmd_tomorrow(update, context); return
        if tl == "praha":
            u["mode"] = "city"; u["city"] = "Praha"; set_user(chat_id, u)
            await update.message.reply_text("Источник: Praha ✅", reply_markup=weather_kb()); return
        if tl == "🔙 назад".lower():
            context.chat_data.pop("weather_mode", None)
            await update.message.reply_text("Ок. Команды: /weather /news /horoscope /settings", reply_markup=ReplyKeyboardRemove())
            return
        await update.message.reply_text("Нажми today / tomorrow или выбери источник ниже.", reply_markup=weather_kb())
        return

    await update.message.reply_text("Команды: /weather /news /horoscope /settings")

# ---------------- ЛОКАЦИЯ ----------------
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

# ---------------- ОБРАБОТЧИК ОШИБОК ----------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled error: %s", context.error)

# ---------------- РЕГИСТРАЦИЯ И ЗАПУСК ----------------
async def post_init(app):
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    await app.bot.set_my_commands([
        BotCommand("start", "запуск бота"),
        BotCommand("weather", "погода: сегодня/завтра"),
        BotCommand("news", "3 новости из канала"),
        BotCommand("horoscope", "гороскоп на сегодня"),
        BotCommand("settings", "настройки"),
    ])

def main():
    import telegram
    log.info("python-telegram-bot version: %s", getattr(telegram, "__version__", "unknown"))

    if not TOKEN or not OW_KEY:
        raise RuntimeError("Нет TELEGRAM_BOT_TOKEN или OPENWEATHER_API_KEY")

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("weather", cmd_weather))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("horoscope", cmd_horoscope))
    app.add_handler(CommandHandler("settings", cmd_settings))

    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.add_error_handler(on_error)

    # восстановим персональные задачи
    for cid in [int(cid) for cid in load_db()["users"].keys()]:
        u = ensure_defaults(cid)
        schedule_daily_for(app, cid, u["daily_hour"])

    log.info("Бот запущен ✅")
    app.run_polling()

if __name__ == "__main__":
    main()
