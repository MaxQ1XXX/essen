# -*- coding: utf-8 -*-
"""
Alte Mensa 减脂营养记录器 - Streamlit 手动数据库 + ChatGPT菜单导入版

运行：
    pip install -r requirements.txt
    streamlit run streamlit_app.py

设计逻辑：
1. 食物数据库完全本地手动维护，不联网，不自动恢复已删除的默认食物。
2. Mensa 菜单可以抓取/上传/粘贴后解析菜名。
3. App 生成一段提示词，用户复制到 ChatGPT 估算一周菜单营养。
4. 用户把 ChatGPT 返回的 JSON/CSV 粘贴回 App，保存成本周菜单。
5. 每天记录、食物库、体重和周/月汇总都保存到 SQLite。
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import streamlit as st

APP_TITLE = "Alte Mensa 减脂营养记录器"
DEFAULT_URL = "https://www.studentenwerk-dresden.de/mensen/speiseplan/alte-mensa.html"
DB_PATH = Path(__file__).with_name("mensa_streamlit.sqlite3")
MACRO_KEYS = ["kcal", "protein", "carbs", "fat", "fiber", "co2e_g"]
DISPLAY_MACRO_KEYS = ["kcal", "protein", "carbs", "fat", "fiber"]

TARGET_PRESETS = {
    "训练日 2300 / P190 C210 F62": {"kcal": 2300.0, "protein": 190.0, "carbs": 210.0, "fat": 62.0},
    "休息日 2000 / P190 C120 F68": {"kcal": 2000.0, "protein": 190.0, "carbs": 120.0, "fat": 68.0},
    "高消耗 2500 / P190 C260 F65": {"kcal": 2500.0, "protein": 190.0, "carbs": 260.0, "fat": 65.0},
    "自定义": {"kcal": 2300.0, "protein": 190.0, "carbs": 210.0, "fat": 62.0},
}

# 只用于菜单未经过 ChatGPT 估算时的占位估算；最终推荐用 ChatGPT JSON/CSV 导入。
DISH_PROFILES = {
    "鸡肉/瘦肉+主食": {"kcal": 680, "protein": 45, "carbs": 75, "fat": 20, "fiber": 6, "co2e_g": 1350},
    "鱼+主食": {"kcal": 650, "protein": 38, "carbs": 65, "fat": 22, "fiber": 5, "co2e_g": 1600},
    "牛肉/猪肉高脂": {"kcal": 850, "protein": 40, "carbs": 80, "fat": 38, "fiber": 5, "co2e_g": 2500},
    "意面/千层面/焗面": {"kcal": 780, "protein": 28, "carbs": 95, "fat": 28, "fiber": 6, "co2e_g": 900},
    "豆类/素肉/咖喱+主食": {"kcal": 650, "protein": 30, "carbs": 85, "fat": 18, "fiber": 12, "co2e_g": 650},
    "炸物/汉堡/披萨/薯条": {"kcal": 980, "protein": 35, "carbs": 105, "fat": 45, "fiber": 7, "co2e_g": 1800},
    "汤/小份/清淡餐": {"kcal": 380, "protein": 14, "carbs": 45, "fat": 12, "fiber": 7, "co2e_g": 500},
    "沙拉吧含调味汁": {"kcal": 360, "protein": 15, "carbs": 30, "fat": 20, "fiber": 8, "co2e_g": 450},
    "甜品/奶米糊/冰淇淋/蛋糕": {"kcal": 360, "protein": 8, "carbs": 58, "fat": 10, "fiber": 2, "co2e_g": 650},
}

DEFAULT_FOODS = [
    {"name": "乳清蛋白粉", "kcal": 390, "protein": 78, "carbs": 8, "fat": 6, "fiber": 0, "co2e_g": 350, "category": "protein", "initials": "rqdbf", "note": "每100g，40g约31g蛋白"},
    {"name": "全蛋", "kcal": 143, "protein": 12.6, "carbs": 0.7, "fat": 9.5, "fiber": 0, "co2e_g": 450, "category": "protein_fat", "initials": "qd", "note": "每100g"},
    {"name": "农夫面包 Bauernbrot", "kcal": 225, "protein": 6, "carbs": 45, "fat": 1, "fiber": 0, "co2e_g": 0, "category": "carb", "initials": "nfmb", "note": "来自用户数据"},
    {"name": "土豆/生重", "kcal": 77, "protein": 2, "carbs": 17, "fat": 0.1, "fiber": 2.2, "co2e_g": 35, "category": "carb", "initials": "td", "note": "生重"},
    {"name": "牛肋排", "kcal": 256, "protein": 28, "carbs": 0.1, "fat": 16, "fiber": 0, "co2e_g": 0, "category": "protein_fat", "initials": "nlp", "note": "来自用户数据"},
    {"name": "西红柿", "kcal": 20, "protein": 1, "carbs": 6, "fat": 0.2, "fiber": 1.5, "co2e_g": 0, "category": "veg", "initials": "xhs", "note": "来自用户数据"},
    {"name": "干意面", "kcal": 353, "protein": 13, "carbs": 72, "fat": 1.5, "fiber": 3, "co2e_g": 150, "category": "carb", "initials": "gym", "note": "干重"},
    {"name": "干米", "kcal": 360, "protein": 7, "carbs": 80, "fat": 0.7, "fiber": 1.3, "co2e_g": 270, "category": "carb", "initials": "gm", "note": "干重"},
    {"name": "橄榄油", "kcal": 884, "protein": 0, "carbs": 0, "fat": 100, "fiber": 0, "co2e_g": 530, "category": "fat", "initials": "gly", "note": "每100g"},
    {"name": "洋葱", "kcal": 42, "protein": 1, "carbs": 9.3, "fat": 0.1, "fiber": 1.5, "co2e_g": 0, "category": "veg", "initials": "yc", "note": "来自用户数据"},
    {"name": "燕麦片", "kcal": 370, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10, "co2e_g": 90, "category": "carb", "initials": "ymp", "note": "每100g"},
    {"name": "牛肉馅", "kcal": 230, "protein": 16, "carbs": 0.1, "fat": 10, "fiber": 0, "co2e_g": 0, "category": "protein_fat", "initials": "nrx", "note": "来自用户数据"},
    {"name": "生鸡胸肉", "kcal": 110, "protein": 23, "carbs": 0, "fat": 1.5, "fiber": 0, "co2e_g": 560, "category": "protein", "initials": "sjxr", "note": "生重"},
    {"name": "瘦牛肉/约5%脂肪", "kcal": 137, "protein": 21, "carbs": 0, "fat": 5, "fiber": 0, "co2e_g": 2700, "category": "protein", "initials": "snr", "note": "生重估算"},
    {"name": "白菜/生菜/菠菜", "kcal": 15, "protein": 1.1, "carbs": 1.2, "fat": 0.3, "fiber": 2, "co2e_g": 0, "category": "veg", "initials": "bcsc", "note": "来自用户数据"},
    {"name": "五香牛腱/熟肉", "kcal": 213, "protein": 15.7, "carbs": 1.5, "fat": 16, "fiber": 0, "co2e_g": 0, "category": "protein_fat", "initials": "wynxcdz", "note": "原CSV名称损坏，根据拼音暂定"},
    {"name": "虾仁", "kcal": 99, "protein": 24, "carbs": 0.2, "fat": 0.3, "fiber": 0, "co2e_g": 1000, "category": "protein", "initials": "xr", "note": "每100g"},
    {"name": "西兰花/蔬菜", "kcal": 34, "protein": 2.8, "carbs": 7, "fat": 0.4, "fiber": 3, "co2e_g": 45, "category": "veg", "initials": "xlh", "note": "每100g"},
    {"name": "西瓜", "kcal": 30, "protein": 0.6, "carbs": 7.6, "fat": 0.2, "fiber": 0.4, "co2e_g": 0, "category": "fruit", "initials": "xg", "note": "来自用户数据"},
    {"name": "鸡腿肉去皮/生重", "kcal": 125, "protein": 20, "carbs": 0, "fat": 5, "fiber": 0, "co2e_g": 560, "category": "protein", "initials": "jtr", "note": "生重"},
]

DAY_WORD_RE = re.compile(r"^(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag)$", re.I)
PRICE_RE = re.compile(r"(\d+[,.]\d{2}\s*€|ausverkauft)", re.I)
CO2_RE = re.compile(r"(\d+(?:[,.]\d+)?)\s*(g|kg)?\s*(CO₂e?|CO2e?)", re.I)
HTML_TAG_RE = re.compile(r"<[^>]+>")
EXCLUDE_PHRASES = [
    "Studentenwerk", "Datenschutz", "Impressum", "Barrierefreiheit", "Drucken", "Vorherige Woche",
    "Nächste Woche", "Mensa wählen", "Suchbegriff", "Raster", "Liste", "Cookie", "Öffnungszeiten",
    "Image:", "Info", "Infos", "KlimaTeller", "Leider keine Angebote", "Ihre Position", "Navigation",
    "Speiseplan", "Startseite", "Legende", "Zusatzstoffe", "Allergene", "Campus", "Dresden",
]


@dataclass
class MenuDish:
    week_key: str
    day: str
    name: str
    price: str = ""
    kcal: float = 0.0
    protein: float = 0.0
    carbs: float = 0.0
    fat: float = 0.0
    fiber: float = 0.0
    co2e_g: float = 0.0
    note: str = ""


def today_str() -> str:
    return date.today().isoformat()


def current_week_key() -> str:
    y, w, _ = date.today().isocalendar()
    return f"{y}-KW{w:02d}"


def week_key_from_date(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-KW{w:02d}"


def clean_text(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = str(s).replace("\xa0", " ").replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


def safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None or str(v).strip() == "":
            return default
        return float(str(v).replace(",", "."))
    except Exception:
        return default


def row_value(obj, key, default=0):
    try:
        if obj is None:
            return default
        if isinstance(obj, sqlite3.Row):
            val = obj[key]
            return default if val is None else val
        if isinstance(obj, dict):
            val = obj.get(key, default)
            return default if val is None else val
        val = getattr(obj, key, default)
        return default if val is None else val
    except Exception:
        return default


def macro_from_obj(obj) -> Dict[str, float]:
    return {k: safe_float(row_value(obj, k, 0), 0) for k in MACRO_KEYS}


def display_macro_from_obj(obj) -> Dict[str, float]:
    return {k: safe_float(row_value(obj, k, 0), 0) for k in DISPLAY_MACRO_KEYS}


def add_macros(rows: Iterable[Dict[str, float]]) -> Dict[str, float]:
    total = {k: 0.0 for k in MACRO_KEYS}
    for row in rows:
        for k in MACRO_KEYS:
            total[k] += safe_float(row_value(row, k, 0), 0)
    return total


def scale_food(food, grams: float) -> Dict[str, float]:
    factor = grams / 100.0
    return {k: safe_float(row_value(food, k, 0), 0) * factor for k in MACRO_KEYS}


def strip_html_to_lines(html_or_text: str) -> List[str]:
    text = html_or_text or ""
    text = re.sub(r"(?is)<script.*?</script>", "\n", text)
    text = re.sub(r"(?is)<style.*?</style>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|td|th|h\d|section|article|a|span)>", "\n", text)
    text = HTML_TAG_RE.sub(" ", text)
    replacements = {
        "&euro;": "€", "&auml;": "ä", "&ouml;": "ö", "&uuml;": "ü", "&Auml;": "Ä", "&Ouml;": "Ö",
        "&Uuml;": "Ü", "&szlig;": "ß", "&quot;": '"', "&#039;": "'", "&#x20ac;": "€", "&nbsp;": " ",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    lines = [clean_text(x) for x in text.splitlines()]
    return [x for x in lines if x]


def looks_like_day(line: str) -> bool:
    line = clean_text(line)
    if DAY_WORD_RE.match(line):
        return True
    if any(line.startswith(d) for d in ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]):
        return bool(re.search(r"\d{1,2}\.\s*", line) or len(line.split()) <= 6)
    return False


def looks_like_food_line(line: str) -> bool:
    s = clean_text(line)
    if len(s) < 6 or PRICE_RE.search(s) or CO2_RE.search(s):
        return False
    if any(p.lower() in s.lower() for p in EXCLUDE_PHRASES):
        return False
    if looks_like_day(s) or re.fullmatch(r"[\d,. €]+", s):
        return False
    return len(re.findall(r"[A-Za-zÄÖÜäöüß\u4e00-\u9fff]", s)) >= 4


def classify_dish(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ["softeis", "milchgrieß", "milchgriess", "kuchen", "dessert", "zimt", "pudding", "waffel", "streusel", "eis"]):
        return "甜品/奶米糊/冰淇淋/蛋糕"
    if any(k in n for k in ["burger", "pizza", "pommes", "schnitzel", "frittiert", "frittierte", "teigtaschen", "bratkartoffeln", "cordon"]):
        return "炸物/汉堡/披萨/薯条"
    if any(k in n for k in ["suppe", "terrine", "eintopf", "kaltschale"]):
        return "汤/小份/清淡餐"
    if "salat" in n and not any(k in n for k in ["lasagne", "auflauf", "dazu", "pasta"]):
        return "沙拉吧含调味汁"
    if any(k in n for k in ["seelachs", "fisch", "lachs", "kabeljau", "forelle", "fish"]):
        return "鱼+主食"
    if any(k in n for k in ["rind", "beef", "schwein", "bacon", "speck", "leber", "gulasch"]):
        return "牛肉/猪肉高脂"
    if any(k in n for k in ["pasta", "lasagne", "tortell", "nudel", "makkaroni", "spätzle", "spaetzle", "spaghetti", "auflauf", "gratin", "gnocchi"]):
        return "意面/千层面/焗面"
    if any(k in n for k in ["soja", "kichererbs", "chili sin", "brew bites", "gemüse", "gemuese", "linsen", "bohnen", "curry", "tofu", "falafel", "jackfruit", "vegan", "vegetar"]):
        return "豆类/素肉/咖喱+主食"
    if any(k in n for k in ["hähnchen", "haehnchen", "huhn", "pute", "chicken", "geschnetzeltes"]):
        return "鸡肉/瘦肉+主食"
    return "意面/千层面/焗面"


def rule_estimate(name: str) -> Tuple[Dict[str, float], str]:
    profile = classify_dish(name)
    return dict(DISH_PROFILES[profile]), profile


def parse_menu_from_text(text: str, week_key: str) -> List[MenuDish]:
    lines = strip_html_to_lines(text)
    rows: List[MenuDish] = []
    current_day = "未识别日期"
    seen = set()

    for i, line in enumerate(lines):
        if looks_like_day(line):
            current_day = line
            continue
        price_match = PRICE_RE.search(line)
        if not price_match:
            continue
        price = price_match.group(1)
        food = ""
        prefix = clean_text(line[: price_match.start()])
        if looks_like_food_line(prefix):
            food = prefix
        else:
            for j in range(i - 1, max(-1, i - 9), -1):
                if looks_like_food_line(lines[j]):
                    food = lines[j]
                    break
        if not food:
            continue
        macro, profile = rule_estimate(food)
        co2 = 0.0
        for j in range(i, min(len(lines), i + 5)):
            m = CO2_RE.search(lines[j])
            if m:
                val = safe_float(m.group(1), 0)
                co2 = val * 1000 if (m.group(2) or "").lower() == "kg" else val
                break
        if co2 > 0:
            macro["co2e_g"] = co2
        key = (current_day, food)
        if key not in seen:
            seen.add(key)
            rows.append(MenuDish(week_key=week_key, day=current_day, name=food, price=price, note=f"本地粗估/{profile}", **macro))

    if not rows:
        for line in lines:
            if looks_like_day(line):
                current_day = line
                continue
            if looks_like_food_line(line):
                macro, profile = rule_estimate(line)
                key = (current_day, line)
                if key not in seen:
                    seen.add(key)
                    rows.append(MenuDish(week_key=week_key, day=current_day, name=line, note=f"本地粗估/{profile}", **macro))
    return rows


def fetch_url_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MensaNutritionStreamlit/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
        conn.commit()


def meta_get(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_key TEXT NOT NULL,
            day TEXT NOT NULL,
            name TEXT NOT NULL,
            price TEXT,
            kcal REAL, protein REAL, carbs REAL, fat REAL, fiber REAL, co2e_g REAL,
            note TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_date TEXT NOT NULL,
            meal TEXT NOT NULL,
            name TEXT NOT NULL,
            grams REAL,
            kcal REAL, protein REAL, carbs REAL, fat REAL, fiber REAL, co2e_g REAL,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS foods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            kcal REAL, protein REAL, carbs REAL, fat REAL, fiber REAL, co2e_g REAL,
            category TEXT,
            initials TEXT,
            note TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weekly_weights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_key TEXT UNIQUE NOT NULL,
            record_date TEXT,
            weight_kg REAL,
            waist_cm REAL,
            note TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()

    # 兼容旧版数据库。旧列存在也不删除，界面不显示即可。
    for col, typ in [("fiber", "REAL"), ("co2e_g", "REAL"), ("note", "TEXT"), ("created_at", "TEXT")]:
        ensure_column(conn, "menu_items", col, typ)
    for col, typ in [("fiber", "REAL"), ("co2e_g", "REAL"), ("created_at", "TEXT")]:
        ensure_column(conn, "daily_records", col, typ)
    for col, typ in [("fiber", "REAL"), ("co2e_g", "REAL"), ("category", "TEXT"), ("initials", "TEXT"), ("note", "TEXT"), ("updated_at", "TEXT")]:
        ensure_column(conn, "foods", col, typ)

    seeded = meta_get(conn, "seeded_default_foods", "0")
    food_count = conn.execute("SELECT COUNT(*) AS n FROM foods").fetchone()["n"]
    if seeded != "1":
        if food_count == 0:
            insert_default_foods(conn)
        meta_set(conn, "seeded_default_foods", "1")


def insert_default_foods(conn: sqlite3.Connection) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    for v in DEFAULT_FOODS:
        conn.execute(
            """
            INSERT OR IGNORE INTO foods
            (name, kcal, protein, carbs, fat, fiber, co2e_g, category, initials, note, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (v["name"], v["kcal"], v["protein"], v["carbs"], v["fat"], v["fiber"], v["co2e_g"], v["category"], v["initials"], v["note"], now),
        )
    conn.commit()


def reset_default_foods(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM foods")
    conn.commit()
    insert_default_foods(conn)
    meta_set(conn, "seeded_default_foods", "1")


def save_week_menu(conn: sqlite3.Connection, week_key: str, rows: List[MenuDish], replace: bool = True) -> None:
    if replace:
        conn.execute("DELETE FROM menu_items WHERE week_key=?", (week_key,))
    now = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        conn.execute(
            """
            INSERT INTO menu_items
            (week_key, day, name, price, kcal, protein, carbs, fat, fiber, co2e_g, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (week_key, r.day, r.name, r.price, r.kcal, r.protein, r.carbs, r.fat, r.fiber, r.co2e_g, r.note, now),
        )
    conn.commit()


def get_week_menu(conn: sqlite3.Connection, week_key: str) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM menu_items WHERE week_key=? ORDER BY id", (week_key,)).fetchall()


def delete_week_menu(conn: sqlite3.Connection, week_key: str) -> None:
    conn.execute("DELETE FROM menu_items WHERE week_key=?", (week_key,))
    conn.commit()


def update_menu_item(conn: sqlite3.Connection, item_id: int, vals: Dict[str, object]) -> None:
    conn.execute(
        """
        UPDATE menu_items
        SET day=?, name=?, price=?, kcal=?, protein=?, carbs=?, fat=?, fiber=?, co2e_g=?, note=?
        WHERE id=?
        """,
        (vals["day"], vals["name"], vals["price"], vals["kcal"], vals["protein"], vals["carbs"], vals["fat"], vals["fiber"], vals["co2e_g"], vals["note"], item_id),
    )
    conn.commit()


def delete_menu_item(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("DELETE FROM menu_items WHERE id=?", (item_id,))
    conn.commit()


def get_record_rows(conn: sqlite3.Connection, record_date: str) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM daily_records WHERE record_date=? ORDER BY id", (record_date,)).fetchall()


def add_record(conn: sqlite3.Connection, record_date: str, meal: str, name: str, grams: float, macro: Dict[str, float]) -> None:
    conn.execute(
        """
        INSERT INTO daily_records
        (record_date, meal, name, grams, kcal, protein, carbs, fat, fiber, co2e_g, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (record_date, meal, name, grams, macro["kcal"], macro["protein"], macro["carbs"], macro["fat"], macro["fiber"], macro["co2e_g"], datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def delete_record(conn: sqlite3.Connection, record_id: int) -> None:
    conn.execute("DELETE FROM daily_records WHERE id=?", (record_id,))
    conn.commit()


def clear_day_records(conn: sqlite3.Connection, record_date: str) -> None:
    conn.execute("DELETE FROM daily_records WHERE record_date=?", (record_date,))
    conn.commit()


def get_foods(conn: sqlite3.Connection, query: str = "") -> List[sqlite3.Row]:
    q = clean_text(query).lower()
    if not q:
        return conn.execute("SELECT * FROM foods ORDER BY id").fetchall()
    like = f"%{q}%"
    return conn.execute(
        """
        SELECT * FROM foods
        WHERE lower(name) LIKE ? OR lower(category) LIKE ? OR lower(initials) LIKE ? OR lower(note) LIKE ?
        ORDER BY id
        """,
        (like, like, like, like),
    ).fetchall()


def get_food_by_id(conn: sqlite3.Connection, food_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM foods WHERE id=?", (food_id,)).fetchone()


def insert_food(conn: sqlite3.Connection, values: Dict[str, object]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO foods (name, kcal, protein, carbs, fat, fiber, co2e_g, category, initials, note, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (values["name"], values["kcal"], values["protein"], values["carbs"], values["fat"], values["fiber"], values["co2e_g"], values["category"], values["initials"], values["note"], now),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_food_by_id(conn: sqlite3.Connection, food_id: int, values: Dict[str, object]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE foods
        SET name=?, kcal=?, protein=?, carbs=?, fat=?, fiber=?, co2e_g=?, category=?, initials=?, note=?, updated_at=?
        WHERE id=?
        """,
        (values["name"], values["kcal"], values["protein"], values["carbs"], values["fat"], values["fiber"], values["co2e_g"], values["category"], values["initials"], values["note"], now, food_id),
    )
    conn.commit()


def delete_food_by_id(conn: sqlite3.Connection, food_id: int) -> None:
    conn.execute("DELETE FROM foods WHERE id=?", (food_id,))
    conn.commit()


def copy_food_by_id(conn: sqlite3.Connection, food_id: int) -> Optional[int]:
    f = get_food_by_id(conn, food_id)
    if f is None:
        return None
    base = f["name"] + "_复制"
    existing = {r["name"] for r in conn.execute("SELECT name FROM foods WHERE name LIKE ?", (base + "%",)).fetchall()}
    new_name = base
    idx = 1
    while new_name in existing:
        idx += 1
        new_name = f"{base}{idx}"
    return insert_food(conn, {
        "name": new_name,
        "kcal": f["kcal"], "protein": f["protein"], "carbs": f["carbs"], "fat": f["fat"],
        "fiber": f["fiber"], "co2e_g": f["co2e_g"], "category": f["category"],
        "initials": f["initials"], "note": f"复制自 ID {food_id}",
    })


def rows_to_csv(rows: Iterable[sqlite3.Row | Dict[str, object]], fields: List[str]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row_value(row, f, "") for f in fields})
    return output.getvalue()


def total_for_records(rows: Iterable[sqlite3.Row]) -> Dict[str, float]:
    return add_macros(macro_from_obj(r) for r in rows)


def extract_code_block(text: str) -> str:
    text = clean_text(text)
    if "```" not in text:
        return text
    m = re.search(r"```(?:json|csv)?\s*(.*?)\s*```", text, re.S | re.I)
    return m.group(1).strip() if m else text.replace("```", "").strip()


def normalize_key(k: str) -> str:
    k = clean_text(k).lower().replace(" ", "").replace("_", "").replace("-", "")
    return k.replace("₂", "2")


def map_value(row: Dict[str, object], aliases: List[str], default=""):
    normalized = {normalize_key(k): v for k, v in row.items()}
    for a in aliases:
        key = normalize_key(a)
        if key in normalized:
            return normalized[key]
    return default


def parse_chatgpt_menu_result(text: str, week_key: str) -> List[MenuDish]:
    raw = extract_code_block(text)
    if not raw:
        return []

    rows_raw: List[Dict[str, object]] = []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = parsed.get("dishes") or parsed.get("items") or parsed.get("menu") or []
        if isinstance(parsed, list):
            rows_raw = [x for x in parsed if isinstance(x, dict)]
    except Exception:
        rows_raw = []

    if not rows_raw:
        try:
            sample = raw[:2048]
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(raw), dialect=dialect)
        rows_raw = [dict(r) for r in reader]

    out: List[MenuDish] = []
    for r in rows_raw:
        day = clean_text(str(map_value(r, ["day", "date", "日期", "星期", "Wochentag"], "未识别日期")))
        name = clean_text(str(map_value(r, ["name", "dish", "food", "菜品", "名称", "Gericht", "Speise"], "")))
        if not name:
            continue
        price = clean_text(str(map_value(r, ["price", "价格", "Preis"], "")))
        note = clean_text(str(map_value(r, ["note", "备注", "依据", "估算依据", "reason"], "ChatGPT估算")))
        out.append(MenuDish(
            week_key=week_key,
            day=day,
            name=name,
            price=price,
            kcal=safe_float(map_value(r, ["kcal", "calories", "热量", "卡路里"], 0)),
            protein=safe_float(map_value(r, ["protein", "p", "蛋白", "蛋白质"], 0)),
            carbs=safe_float(map_value(r, ["carbs", "carbohydrate", "c", "碳水", "碳水化合物"], 0)),
            fat=safe_float(map_value(r, ["fat", "f", "脂肪"], 0)),
            fiber=safe_float(map_value(r, ["fiber", "fibre", "膳食纤维", "纤维"], 0)),
            co2e_g=safe_float(map_value(r, ["co2e_g", "co2e", "co2", "co₂e", "co₂eg", "碳排", "碳排放"], 0)),
            note=note,
        ))
    return out


def make_chatgpt_prompt(rows: List[MenuDish]) -> str:
    compact = [
        {"day": r.day, "name": r.name, "price": r.price, "local_rough_estimate": display_macro_from_obj(r)}
        for r in rows
    ]
    return (
        "请你根据下面的德国学生食堂一周菜单，逐个菜名估算一份普通食堂份量的营养成分。\n"
        "要求：\n"
        "1. 不要只按食物大类粗略归类；请根据菜名里的具体食材、常见德国/欧洲食堂做法、类似菜谱营养数据来估算。\n"
        "2. 单位：kcal 为千卡；protein/carbs/fat/fiber 为克；co2e_g 为克 CO2e。\n"
        "3. CO2e 只作为内部参考字段，不要写在菜名后面，不要额外解释每个菜的 CO2。\n"
        "4. 输出必须是 JSON 数组，不要 markdown，不要代码块，不要中文表格。\n"
        "5. 每个元素必须包含这些字段：day, name, price, kcal, protein, carbs, fat, fiber, co2e_g, note。\n"
        "6. note 简短写估算依据，例如：鸡胸+土豆球+酱汁，普通Mensa一份。\n\n"
        "菜单数据：\n"
        + json.dumps(compact, ensure_ascii=False, indent=2)
    )


def rerun() -> None:
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()


def render_metric_row(total: Dict[str, float], target: Optional[Dict[str, float]] = None) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    data = [("kcal", "热量"), ("protein", "蛋白 g"), ("carbs", "碳水 g"), ("fat", "脂肪 g"), ("fiber", "纤维 g")]
    cols = [c1, c2, c3, c4, c5]
    for col, (key, label) in zip(cols, data):
        if target and key in target:
            delta = total.get(key, 0) - target[key]
            col.metric(label, f"{total.get(key, 0):.0f}", f"{delta:+.0f}")
        else:
            col.metric(label, f"{total.get(key, 0):.0f}")


def df_from_rows(rows: Iterable[sqlite3.Row | Dict[str, object]], fields: List[str]) -> pd.DataFrame:
    return pd.DataFrame([{f: row_value(r, f, "") for f in fields} for r in rows])


def parse_date_string(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def get_daily_summary(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT record_date, SUM(kcal) AS kcal, SUM(protein) AS protein, SUM(carbs) AS carbs,
               SUM(fat) AS fat, SUM(fiber) AS fiber, SUM(co2e_g) AS co2e_g
        FROM daily_records
        WHERE record_date BETWEEN ? AND ?
        GROUP BY record_date
        ORDER BY record_date
        """,
        (start, end),
    ).fetchall()
    return df_from_rows(rows, ["record_date", "kcal", "protein", "carbs", "fat", "fiber", "co2e_g"])


def upsert_weight(conn: sqlite3.Connection, week_key: str, record_date: str, weight_kg: float, waist_cm: float, note: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO weekly_weights (week_key, record_date, weight_kg, waist_cm, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(week_key) DO UPDATE SET
            record_date=excluded.record_date, weight_kg=excluded.weight_kg, waist_cm=excluded.waist_cm,
            note=excluded.note, created_at=excluded.created_at
        """,
        (week_key, record_date, weight_kg, waist_cm, note, now),
    )
    conn.commit()


def get_weights(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM weekly_weights ORDER BY week_key").fetchall()


def make_recommendation(summary_df: pd.DataFrame, weights: List[sqlite3.Row], target: Dict[str, float], body: Dict[str, float]) -> List[str]:
    advice = []
    if summary_df.empty:
        return ["还没有足够的每日记录。先连续记录 7 天，再判断是否需要调整摄入。"]
    avg_kcal = float(summary_df["kcal"].mean()) if "kcal" in summary_df else 0
    avg_p = float(summary_df["protein"].mean()) if "protein" in summary_df else 0
    advice.append(f"当前统计区间平均摄入约 {avg_kcal:.0f} kcal/天，蛋白约 {avg_p:.0f} g/天。")
    if avg_p < target.get("protein", 190) - 15:
        advice.append("蛋白明显低于目标：优先补到 180–190 g/天，再考虑调碳水或脂肪。")
    elif avg_p >= target.get("protein", 190) - 5:
        advice.append("蛋白基本达标：继续保持。")

    valid_weights = [w for w in weights if row_value(w, "weight_kg", 0)]
    if len(valid_weights) >= 2:
        last = float(valid_weights[-1]["weight_kg"])
        prev = float(valid_weights[-2]["weight_kg"])
        delta = last - prev
        advice.append(f"最近两次周体重变化：{delta:+.2f} kg。")
        if delta > -0.2:
            advice.append("体重下降偏慢或上升：下周每日平均热量可减少 150–200 kcal，优先从脂肪和精制碳水中减。")
        elif delta < -1.0:
            advice.append("体重下降过快：训练日可增加 100–150 kcal，避免训练表现下降。")
        else:
            advice.append("体重下降速度合理：维持当前摄入结构一周再评估。")
    else:
        advice.append("体重数据不足：建议每周固定一天早晨空腹记录体重。")

    target_weight = body.get("target_weight", 85.0)
    current_weight = body.get("current_weight", 0) or (float(valid_weights[-1]["weight_kg"]) if valid_weights else 0)
    if current_weight and current_weight <= target_weight:
        advice.append("当前体重已接近或低于目标体重：可以考虑从减脂期切换到维持/小幅增肌阶段。")
    return advice


def render_menu_import_tab(conn: sqlite3.Connection, week_key: str) -> None:
    st.subheader("导入网页菜单 → 生成 ChatGPT 估算提示 → 导入结果")
    st.info("食物数据库保持本地手动维护；这里的 ChatGPT 流程只用于一周 Mensa 菜单估算。App 不调用 API，你把提示词复制到 ChatGPT，再把 JSON/CSV 粘贴回来即可。")

    source_mode = st.radio("菜单导入方式", ["URL", "上传 HTML/TXT", "粘贴网页文本"], horizontal=True)
    if source_mode == "URL":
        url = st.text_input("Mensa URL", value=DEFAULT_URL)
        if st.button("抓取 URL 并解析", type="primary"):
            try:
                st.session_state["raw_menu_text"] = fetch_url_text(url)
                st.success("URL 抓取成功。")
            except Exception as e:
                st.error(f"抓取失败：{e}")
    elif source_mode == "上传 HTML/TXT":
        file = st.file_uploader("上传浏览器保存的网页 HTML 或 TXT", type=["html", "htm", "txt"])
        if file is not None:
            st.session_state["raw_menu_text"] = file.read().decode("utf-8", errors="replace")
            st.success("文件读取成功。")
    else:
        pasted = st.text_area("粘贴网页文本/HTML", height=220)
        if st.button("使用粘贴文本"):
            st.session_state["raw_menu_text"] = pasted
            st.success("已载入粘贴内容。")

    raw_loaded = st.session_state.get("raw_menu_text", "")
    if not raw_loaded:
        return

    local_rows = parse_menu_from_text(raw_loaded, week_key)
    st.write(f"已解析到 **{len(local_rows)}** 个菜品。")
    if not local_rows:
        st.warning("没有解析到菜品。可以尝试复制网页的纯文本，或上传 HTML 文件。")
        return

    parsed_df = pd.DataFrame([{"day": r.day, "name": r.name, "price": r.price} for r in local_rows])
    st.dataframe(parsed_df, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("### A. 生成给 ChatGPT 的提示词")
    prompt = make_chatgpt_prompt(local_rows)
    st.text_area("复制下面内容到 ChatGPT，让它返回 JSON 数组", value=prompt, height=320)
    st.caption("ChatGPT 返回后，复制完整 JSON 或 CSV 到下面的导入框。")

    st.divider()
    st.markdown("### B. 导入 ChatGPT 返回结果")
    result_text = st.text_area("粘贴 ChatGPT 返回的 JSON/CSV", height=260, placeholder='例如 [{"day":"Montag", "name":"...", "kcal":650, ...}]')
    uploaded_result = st.file_uploader("或上传 ChatGPT 结果 CSV/JSON", type=["csv", "json", "txt"], key="gpt_result_upload")
    if uploaded_result is not None:
        result_text = uploaded_result.read().decode("utf-8", errors="replace")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("预览 ChatGPT 结果"):
            try:
                parsed = parse_chatgpt_menu_result(result_text, week_key)
                st.session_state["parsed_gpt_menu"] = parsed
                st.success(f"解析成功：{len(parsed)} 个菜品。")
            except Exception as e:
                st.error(f"解析失败：{e}")
    with c2:
        if st.button("保存 ChatGPT 结果为本周菜单", type="primary"):
            try:
                parsed = parse_chatgpt_menu_result(result_text, week_key)
                if not parsed:
                    st.error("没有可保存的菜品。")
                else:
                    save_week_menu(conn, week_key, parsed, replace=True)
                    st.success(f"已保存 {len(parsed)} 个菜品到 {week_key}。")
                    rerun()
            except Exception as e:
                st.error(f"保存失败：{e}")

    parsed_preview = st.session_state.get("parsed_gpt_menu")
    if parsed_preview:
        st.dataframe(pd.DataFrame([{"day": r.day, "name": r.name, "price": r.price, **display_macro_from_obj(r), "note": r.note} for r in parsed_preview]), use_container_width=True, hide_index=True)

    st.divider()
    with st.expander("仅作为备用：保存本地粗估结果"):
        st.warning("这只是按关键词粗估，不推荐作为最终菜单数据。")
        if st.button("保存本地粗估到本周菜单"):
            save_week_menu(conn, week_key, local_rows, replace=True)
            st.success("已保存本地粗估。")
            rerun()


def render_week_menu_tab(conn: sqlite3.Connection, week_key: str, record_date: str) -> None:
    st.subheader(f"本周菜单：{week_key}")
    menu_rows = get_week_menu(conn, week_key)
    if not menu_rows:
        st.warning("还没有本周菜单。先到“导入菜单/ChatGPT结果”页保存。")
        return

    csv_text = rows_to_csv(menu_rows, ["day", "name", "price", *DISPLAY_MACRO_KEYS, "co2e_g", "note"])
    st.download_button("导出本周菜单 CSV", csv_text, file_name=f"mensa_menu_{week_key}.csv", mime="text/csv")

    days = ["全部"] + sorted({r["day"] for r in menu_rows})
    day_filter = st.selectbox("按日期筛选", days)
    rows_show = [r for r in menu_rows if day_filter == "全部" or r["day"] == day_filter]

    for r in rows_show:
        cols = st.columns([4, 0.8, 0.8, 0.8, 0.8, 0.8, 1.0, 1.2])
        cols[0].markdown(f"**{r['name']}**  \n{r['day']} · {r['price']}")
        cols[1].write(f"{r['kcal']:.0f} kcal")
        cols[2].write(f"P {r['protein']:.0f}")
        cols[3].write(f"C {r['carbs']:.0f}")
        cols[4].write(f"F {r['fat']:.0f}")
        cols[5].write(f"纤维 {r['fiber']:.0f}")
        meal = cols[6].selectbox("餐次", ["午餐", "晚餐", "早餐", "加餐"], key=f"meal_menu_{r['id']}")
        if cols[7].button("加入当天", key=f"add_menu_{r['id']}"):
            add_record(conn, record_date, meal, r["name"], 1.0, macro_from_obj(r))
            st.success("已加入当天记录。")
            rerun()
        if row_value(r, "note", ""):
            st.caption(f"估算依据：{r['note']}")
        st.markdown("---")

    st.subheader("编辑/删除本周菜单项")
    options = {f"ID {r['id']} | {r['day']} | {r['name'][:50]}": int(r["id"]) for r in menu_rows}
    selected_label = st.selectbox("选择要编辑的菜品", list(options.keys()))
    item_id = options[selected_label]
    item = conn.execute("SELECT * FROM menu_items WHERE id=?", (item_id,)).fetchone()
    if item:
        with st.form("edit_menu_item_form"):
            c1, c2, c3 = st.columns([1.3, 2, 0.7])
            day = c1.text_input("日期/星期", value=str(item["day"]))
            name = c2.text_input("菜品名称", value=str(item["name"]))
            price = c3.text_input("价格", value=str(row_value(item, "price", "")))
            c4, c5, c6, c7, c8, c9 = st.columns(6)
            kcal = c4.number_input("kcal", value=float(row_value(item, "kcal", 0)))
            protein = c5.number_input("蛋白", value=float(row_value(item, "protein", 0)))
            carbs = c6.number_input("碳水", value=float(row_value(item, "carbs", 0)))
            fat = c7.number_input("脂肪", value=float(row_value(item, "fat", 0)))
            fiber = c8.number_input("纤维", value=float(row_value(item, "fiber", 0)))
            co2e_g = c9.number_input("CO2e内部字段", value=float(row_value(item, "co2e_g", 0)))
            note = st.text_input("估算依据/备注", value=str(row_value(item, "note", "")))
            save_btn = st.form_submit_button("保存菜单项修改")
            if save_btn:
                update_menu_item(conn, item_id, {"day": clean_text(day), "name": clean_text(name), "price": clean_text(price), "kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat, "fiber": fiber, "co2e_g": co2e_g, "note": clean_text(note)})
                st.success("菜单项已保存。")
                rerun()
        cdel1, cdel2 = st.columns([1, 3])
        confirm_delete = cdel1.checkbox("确认删除该菜单项")
        if cdel2.button("删除选中菜单项", disabled=not confirm_delete):
            delete_menu_item(conn, item_id)
            st.success("已删除。")
            rerun()

    st.divider()
    confirm_clear = st.checkbox(f"确认清空 {week_key} 的全部菜单")
    if st.button("清空本周菜单", disabled=not confirm_clear):
        delete_week_menu(conn, week_key)
        st.success("本周菜单已清空。")
        rerun()


def render_daily_tab(conn: sqlite3.Connection, record_date: str, target: Dict[str, float]) -> None:
    st.subheader(f"每天记录：{record_date}")
    records = get_record_rows(conn, record_date)
    total = total_for_records(records)
    render_metric_row(total, target)

    if records:
        rec_csv = rows_to_csv(records, ["record_date", "meal", "name", "grams", *DISPLAY_MACRO_KEYS, "co2e_g"])
        st.download_button("导出当天记录 CSV", rec_csv, file_name=f"daily_record_{record_date}.csv", mime="text/csv")
        for r in records:
            cols = st.columns([0.8, 3, 0.8, 0.9, 0.8, 0.8, 0.8, 0.8, 0.8])
            cols[0].write(r["meal"])
            cols[1].write(r["name"])
            cols[2].write(f"{r['grams']:.0f}" if r["grams"] else "-")
            cols[3].write(f"{r['kcal']:.0f} kcal")
            cols[4].write(f"P {r['protein']:.0f}")
            cols[5].write(f"C {r['carbs']:.0f}")
            cols[6].write(f"F {r['fat']:.0f}")
            cols[7].write(f"纤维 {r['fiber']:.0f}")
            if cols[8].button("删除", key=f"del_rec_{r['id']}"):
                delete_record(conn, int(r["id"]))
                rerun()
        confirm_clear = st.checkbox("确认清空当天记录")
        if st.button("清空当天记录", disabled=not confirm_clear):
            clear_day_records(conn, record_date)
            st.success("当天记录已清空。")
            rerun()
    else:
        st.info("当天还没有记录。可以从“本周菜单”加入，或下面从食物库添加。")

    st.divider()
    st.subheader("蛋白不足时加入 40g 乳清")
    protein_short = max(0.0, target["protein"] - total.get("protein", 0.0))
    st.write(f"当前蛋白缺口：**{protein_short:.0f} g**")
    whey = conn.execute("SELECT * FROM foods WHERE name=?", ("乳清蛋白粉",)).fetchone()
    if st.button("如果蛋白不足，加入 40g 乳清"):
        if protein_short <= 0:
            st.info("当前蛋白已经达到目标，不需要加入乳清。")
        elif whey is None:
            st.error("数据库里没有“乳清蛋白粉”。可以在食物数据库中手动添加。")
        else:
            add_record(conn, record_date, "加餐", "乳清蛋白粉", 40, scale_food(whey, 40))
            st.success("已加入 40g 乳清蛋白粉。")
            rerun()

    st.divider()
    st.subheader("从食物数据库添加")
    q = st.text_input("搜索食物：中文/英文/德文/拼音首字母", placeholder="例如 sjxr, ymp, rqdbf, Magerquark")
    foods = get_foods(conn, q)
    meal_food = st.selectbox("餐次", ["晚餐", "早餐", "午餐", "加餐"], key="meal_food")
    grams = st.number_input("克数", min_value=0.0, value=100.0, step=10.0)
    for f in foods[:50]:
        cols = st.columns([0.6, 3, 1, 1, 1, 1, 1, 1])
        cols[0].write(f"ID {f['id']}")
        cols[1].write(f"**{f['name']}** · {f['category']} · {f['initials']}")
        macro = scale_food(f, grams)
        cols[2].write(f"{macro['kcal']:.0f} kcal")
        cols[3].write(f"P {macro['protein']:.0f}")
        cols[4].write(f"C {macro['carbs']:.0f}")
        cols[5].write(f"F {macro['fat']:.0f}")
        cols[6].write(f"纤维 {macro['fiber']:.0f}")
        if cols[7].button("添加", key=f"add_food_{f['id']}"):
            add_record(conn, record_date, meal_food, f["name"], grams, macro)
            st.success("已添加。")
            rerun()


def render_food_db_tab(conn: sqlite3.Connection) -> None:
    st.subheader("食物数据库：本地手动维护")
    st.caption("这部分不联网。默认食物也可以删除；删除后不会自动恢复，除非你点击“清空并恢复默认食物”。")

    q = st.text_input("搜索数据库", key="db_search", placeholder="名称、类别、拼音首字母、备注")
    foods = get_foods(conn, q)
    table_df = df_from_rows(foods, ["id", "name", "kcal", "protein", "carbs", "fat", "fiber", "category", "initials", "note"])
    st.dataframe(table_df, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("### 新增食物")
    with st.form("add_food_form", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 1, 1])
        name = c1.text_input("名称")
        category = c2.text_input("类别", value="custom")
        initials = c3.text_input("拼音首字母/缩写")
        c4, c5, c6, c7, c8, c9 = st.columns(6)
        kcal = c4.number_input("kcal/100g", value=100.0)
        protein = c5.number_input("蛋白/100g", value=0.0)
        carbs = c6.number_input("碳水/100g", value=0.0)
        fat = c7.number_input("脂肪/100g", value=0.0)
        fiber = c8.number_input("纤维/100g", value=0.0)
        co2e_g = c9.number_input("CO2e内部字段", value=0.0)
        note = st.text_input("备注")
        submitted = st.form_submit_button("添加食物")
        if submitted:
            if not clean_text(name):
                st.error("名称不能为空。")
            else:
                try:
                    insert_food(conn, {"name": clean_text(name), "kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat, "fiber": fiber, "co2e_g": co2e_g, "category": clean_text(category), "initials": clean_text(initials).lower(), "note": clean_text(note)})
                    st.success("食物已添加。")
                    rerun()
                except sqlite3.IntegrityError:
                    st.error("名称已存在。请改名，或在下面选择该食物进行编辑。")

    st.divider()
    st.markdown("### 编辑 / 复制 / 删除食物")
    all_foods = get_foods(conn, "")
    if not all_foods:
        st.info("食物库为空。可以新增食物，或点击下方恢复默认食物。")
    else:
        options = {f"ID {f['id']} | {f['name']}": int(f["id"]) for f in all_foods}
        label = st.selectbox("选择食物", list(options.keys()), key="food_edit_selector")
        food_id = options[label]
        food = get_food_by_id(conn, food_id)
        if food:
            with st.form("edit_food_form"):
                c1, c2, c3 = st.columns([2, 1, 1])
                name_e = c1.text_input("名称", value=str(food["name"]))
                category_e = c2.text_input("类别", value=str(row_value(food, "category", "custom")))
                initials_e = c3.text_input("拼音首字母/缩写", value=str(row_value(food, "initials", "")))
                c4, c5, c6, c7, c8, c9 = st.columns(6)
                kcal_e = c4.number_input("kcal/100g", value=float(row_value(food, "kcal", 0)), key="edit_kcal")
                protein_e = c5.number_input("蛋白/100g", value=float(row_value(food, "protein", 0)), key="edit_p")
                carbs_e = c6.number_input("碳水/100g", value=float(row_value(food, "carbs", 0)), key="edit_c")
                fat_e = c7.number_input("脂肪/100g", value=float(row_value(food, "fat", 0)), key="edit_f")
                fiber_e = c8.number_input("纤维/100g", value=float(row_value(food, "fiber", 0)), key="edit_fiber")
                co2_e = c9.number_input("CO2e内部字段", value=float(row_value(food, "co2e_g", 0)), key="edit_co2")
                note_e = st.text_input("备注", value=str(row_value(food, "note", "")))
                saved = st.form_submit_button("保存修改")
                if saved:
                    if not clean_text(name_e):
                        st.error("名称不能为空。")
                    else:
                        try:
                            update_food_by_id(conn, food_id, {"name": clean_text(name_e), "kcal": kcal_e, "protein": protein_e, "carbs": carbs_e, "fat": fat_e, "fiber": fiber_e, "co2e_g": co2_e, "category": clean_text(category_e), "initials": clean_text(initials_e).lower(), "note": clean_text(note_e)})
                            st.success("修改已保存。")
                            rerun()
                        except sqlite3.IntegrityError:
                            st.error("该名称已被其他食物使用。请换一个名称。")
            c1, c2, c3 = st.columns(3)
            if c1.button("复制选中食物"):
                new_id = copy_food_by_id(conn, food_id)
                st.success(f"已复制，新 ID：{new_id}")
                rerun()
            confirm_del = c2.checkbox("确认删除", key=f"confirm_food_delete_{food_id}")
            if c3.button("删除选中食物", disabled=not confirm_del):
                delete_food_by_id(conn, food_id)
                st.success("已删除。")
                rerun()

    st.divider()
    st.markdown("### 批量导入食物 CSV/TXT")
    st.caption("支持逗号、分号、Tab 分隔。建议列名：name,kcal,protein,carbs,fat,fiber,co2e_g,category,initials,note。已有名称不会覆盖，避免误改；需要修改请用上方编辑表单。")
    upload = st.file_uploader("上传食物 CSV/TXT", type=["csv", "txt", "tsv"], key="food_bulk_upload")
    if upload is not None and st.button("导入上传的食物"):
        text = upload.read().decode("utf-8", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t|")
        except Exception:
            dialect = csv.excel_tab if "\t" in text[:200] else csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        added, skipped = 0, 0
        for r in reader:
            name = clean_text(str(map_value(r, ["name", "名称", "food", "食物"], "")))
            if not name or "?" in name:
                skipped += 1
                continue
            vals = {
                "name": name,
                "kcal": safe_float(map_value(r, ["kcal", "kcal/100g", "热量"], 0)),
                "protein": safe_float(map_value(r, ["protein", "p", "P", "蛋白"], 0)),
                "carbs": safe_float(map_value(r, ["carbs", "c", "C", "碳水"], 0)),
                "fat": safe_float(map_value(r, ["fat", "f", "F", "脂肪"], 0)),
                "fiber": safe_float(map_value(r, ["fiber", "纤维"], 0)),
                "co2e_g": safe_float(map_value(r, ["co2e_g", "co2e", "CO?e/100g", "CO2e/100g"], 0)),
                "category": clean_text(str(map_value(r, ["category", "类别"], "custom"))),
                "initials": clean_text(str(map_value(r, ["initials", "首字母", "拼音"], ""))).lower(),
                "note": clean_text(str(map_value(r, ["note", "备注"], "批量导入"))),
            }
            try:
                insert_food(conn, vals)
                added += 1
            except sqlite3.IntegrityError:
                skipped += 1
        st.success(f"导入完成：新增 {added} 条，跳过 {skipped} 条。")
        rerun()

    st.divider()
    confirm_reset = st.checkbox("确认清空全部食物并恢复默认食物")
    if st.button("清空并恢复默认食物", disabled=not confirm_reset):
        reset_default_foods(conn)
        st.success("已恢复默认食物库。")
        rerun()


def render_history_tab(conn: sqlite3.Connection, target: Dict[str, float]) -> None:
    st.subheader("历史记录 / 周月总结 / 每周体重")
    today = date.today()
    mode = st.radio("统计范围", ["本周", "本月", "自定义"], horizontal=True)
    if mode == "本周":
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
    elif mode == "本月":
        start_date = today.replace(day=1)
        next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        end_date = next_month - timedelta(days=1)
    else:
        c1, c2 = st.columns(2)
        start_date = c1.date_input("开始日期", value=today - timedelta(days=30), key="hist_start")
        end_date = c2.date_input("结束日期", value=today, key="hist_end")

    df = get_daily_summary(conn, start_date.isoformat(), end_date.isoformat())
    st.write(f"统计区间：{start_date.isoformat()} 至 {end_date.isoformat()}")
    if df.empty:
        st.info("这个区间还没有每日记录。")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)
        avg = {k: float(df[k].mean()) for k in ["kcal", "protein", "carbs", "fat", "fiber"] if k in df}
        render_metric_row(avg, target)
        chart_df = df.set_index("record_date")[["kcal", "protein", "carbs", "fat"]]
        st.line_chart(chart_df)
        st.download_button("导出汇总 CSV", df.to_csv(index=False), file_name="nutrition_summary.csv", mime="text/csv")

    st.divider()
    st.markdown("### 每周体重记录")
    with st.form("weight_form"):
        c1, c2, c3, c4 = st.columns(4)
        w_date = c1.date_input("测量日期", value=today)
        w_key = c2.text_input("周编号", value=week_key_from_date(w_date))
        weight_kg = c3.number_input("体重 kg", min_value=0.0, value=0.0, step=0.1)
        waist_cm = c4.number_input("腰围 cm，可选", min_value=0.0, value=0.0, step=0.5)
        note = st.text_input("备注")
        if st.form_submit_button("保存/更新本周体重"):
            if weight_kg <= 0:
                st.error("体重必须大于 0。")
            else:
                upsert_weight(conn, clean_text(w_key), w_date.isoformat(), weight_kg, waist_cm, clean_text(note))
                st.success("体重已保存。")
                rerun()

    weights = get_weights(conn)
    if weights:
        st.dataframe(df_from_rows(weights, ["week_key", "record_date", "weight_kg", "waist_cm", "note"]), use_container_width=True, hide_index=True)
        weight_df = df_from_rows(weights, ["week_key", "weight_kg"])
        st.line_chart(weight_df.set_index("week_key"))
    else:
        st.info("还没有体重记录。")

    st.divider()
    st.markdown("### 摄入调整建议")
    c1, c2, c3, c4 = st.columns(4)
    height_cm = c1.number_input("身高 cm", value=174.0, step=1.0)
    age = c2.number_input("年龄", value=30.0, step=1.0)
    current_weight = c3.number_input("当前体重 kg", value=float(weights[-1]["weight_kg"]) if weights else 85.0, step=0.1)
    target_weight = c4.number_input("目标体重 kg", value=85.0, step=0.1)
    body = {"height_cm": height_cm, "age": age, "current_weight": current_weight, "target_weight": target_weight}
    for item in make_recommendation(df, weights, target, body):
        st.write("- " + item)


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🍽️", layout="wide")
    st.title(APP_TITLE)
    st.caption("本地食物库手动维护；Mensa 菜单可先交给 ChatGPT 估算，再导入网页 App。")

    conn = get_conn()
    init_db(conn)

    with st.sidebar:
        st.header("基础设置")
        week_key = st.text_input("本周编号", value=current_week_key(), help="例如 2026-KW28")
        record_date = st.date_input("记录日期", value=date.today()).isoformat()
        preset_name = st.selectbox("每日目标", list(TARGET_PRESETS.keys()))
        preset = TARGET_PRESETS[preset_name]
        target = {
            "kcal": st.number_input("目标 kcal", value=float(preset["kcal"]), step=50.0),
            "protein": st.number_input("目标蛋白 g", value=float(preset["protein"]), step=5.0),
            "carbs": st.number_input("目标碳水 g", value=float(preset["carbs"]), step=5.0),
            "fat": st.number_input("目标脂肪 g", value=float(preset["fat"]), step=1.0),
        }
        st.caption("当前版不需要 OpenAI API key。")

    tab_import, tab_week, tab_day, tab_food, tab_hist = st.tabs([
        "1 导入菜单/ChatGPT结果",
        "2 本周菜单",
        "3 每天记录",
        "4 食物数据库",
        "5 历史/体重/建议",
    ])

    with tab_import:
        render_menu_import_tab(conn, week_key)
    with tab_week:
        render_week_menu_tab(conn, week_key, record_date)
    with tab_day:
        render_daily_tab(conn, record_date, target)
    with tab_food:
        render_food_db_tab(conn)
    with tab_hist:
        render_history_tab(conn, target)

    st.caption("提示：Mensa 菜品没有固定称重，营养值是估算值。减脂期建议对奶油酱、炸物、汉堡、披萨偏高估。")


if __name__ == "__main__":
    main()
