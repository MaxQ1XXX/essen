# -*- coding: utf-8 -*-
"""
Alte Mensa Dresden 减脂/营养记录器 - Streamlit 网页版 v3

运行：
    pip install -r requirements.txt
    streamlit run streamlit_app.py

v3 修复点：
- 食物库支持真正的表格内编辑、复制、删除、新增。
- 食物 id 固定为 SQLite 主键，只读，不再由界面重排导致“乱变”。
- 不再在界面和 CSV 中显示 source/数据来源列。
- 菜品/记录界面不再把 CO2e 跟在每个食物后面；CO2e 保留在数据库内部。
- 增加删除本周菜单、清空当天记录、历史周/月汇总、每周体重记录。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import streamlit as st

APP_TITLE = "Alte Mensa 减脂营养记录器"
DEFAULT_URL = "https://www.studentenwerk-dresden.de/mensen/speiseplan/alte-mensa.html"
DEFAULT_MODEL = "gpt-4.1-mini"
DB_PATH = Path(__file__).with_name("mensa_streamlit.sqlite3")

# CO2e 保留为内部字段，但默认不在每个食物后面显示。
MACRO_KEYS = ["kcal", "protein", "carbs", "fat", "fiber", "co2e_g"]
DISPLAY_MACRO_KEYS = ["kcal", "protein", "carbs", "fat", "fiber"]

TARGET_PRESETS = {
    "训练日 2300 / P190 C210 F62": {"kcal": 2300.0, "protein": 190.0, "carbs": 210.0, "fat": 62.0},
    "休息日 2000 / P190 C120 F68": {"kcal": 2000.0, "protein": 190.0, "carbs": 120.0, "fat": 68.0},
    "高消耗 2500 / P190 C260 F65": {"kcal": 2500.0, "protein": 190.0, "carbs": 260.0, "fat": 65.0},
    "自定义": {"kcal": 2300.0, "protein": 190.0, "carbs": 210.0, "fat": 62.0},
}

USER_PROFILE_DEFAULT = {
    "height_cm": 176.0,
    "age": 30.0,
    "current_weight_kg": 91.0,
    "target_weight_kg": 85.0,
    "training_note": "每周约 5 次健身房：1 小时无氧 + 20 分钟爬楼",
}

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

# 默认食物库：已把用户上传 CSV 中能识别的项目内置进来。
DEFAULT_FOODS = {
    "乳清蛋白粉": {"kcal": 390, "protein": 78, "carbs": 8, "fat": 6, "fiber": 0, "co2e_g": 350, "category": "protein", "initials": "rqdbf"},
    "全蛋": {"kcal": 143, "protein": 12.6, "carbs": 0.7, "fat": 9.5, "fiber": 0, "co2e_g": 450, "category": "protein_fat", "initials": "qd"},
    "农夫面包 Bauernbrot": {"kcal": 225, "protein": 6, "carbs": 45, "fat": 1, "fiber": 0, "co2e_g": 0, "category": "carb", "initials": "nfmb"},
    "土豆/生重": {"kcal": 77, "protein": 2, "carbs": 17, "fat": 0.1, "fiber": 2.2, "co2e_g": 35, "category": "carb", "initials": "td"},
    "牛肋排/熟": {"kcal": 256, "protein": 28, "carbs": 0.1, "fat": 16, "fiber": 0, "co2e_g": 0, "category": "protein_fat", "initials": "nlp"},
    "西红柿": {"kcal": 20, "protein": 1, "carbs": 6, "fat": 0.2, "fiber": 1.5, "co2e_g": 0, "category": "veg", "initials": "xhs"},
    "干意面": {"kcal": 353, "protein": 13, "carbs": 72, "fat": 1.5, "fiber": 3, "co2e_g": 150, "category": "carb", "initials": "gym"},
    "干米": {"kcal": 360, "protein": 7, "carbs": 80, "fat": 0.7, "fiber": 1.3, "co2e_g": 270, "category": "carb", "initials": "gm"},
    "熟米饭": {"kcal": 130, "protein": 2.7, "carbs": 28, "fat": 0.3, "fiber": 0.4, "co2e_g": 100, "category": "carb", "initials": "smf"},
    "橄榄油": {"kcal": 884, "protein": 0, "carbs": 0, "fat": 100, "fiber": 0, "co2e_g": 530, "category": "fat", "initials": "gly"},
    "洋葱": {"kcal": 42, "protein": 1, "carbs": 9.3, "fat": 0.1, "fiber": 1.5, "co2e_g": 0, "category": "veg", "initials": "yc"},
    "燕麦片": {"kcal": 370, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10, "co2e_g": 90, "category": "carb", "initials": "ymp"},
    "牛肉馅": {"kcal": 230, "protein": 16, "carbs": 0.1, "fat": 10, "fiber": 0, "co2e_g": 0, "category": "protein_fat", "initials": "nrx"},
    "生鸡胸肉": {"kcal": 110, "protein": 23, "carbs": 0, "fat": 1.5, "fiber": 0, "co2e_g": 560, "category": "protein", "initials": "sjxr"},
    "瘦牛肉/约5%脂肪": {"kcal": 137, "protein": 21, "carbs": 0, "fat": 5, "fiber": 0, "co2e_g": 2700, "category": "protein", "initials": "snr"},
    "白菜/生菜/菠菜": {"kcal": 15, "protein": 1.1, "carbs": 1.2, "fat": 0.3, "fiber": 2, "co2e_g": 0, "category": "veg", "initials": "bcsc"},
    "导入食物_wynxcdz": {"kcal": 213, "protein": 15.7, "carbs": 1.5, "fat": 16, "fiber": 0, "co2e_g": 0, "category": "custom", "initials": "wynxcdz"},
    "虾仁": {"kcal": 99, "protein": 24, "carbs": 0.2, "fat": 0.3, "fiber": 0, "co2e_g": 1000, "category": "protein", "initials": "xr"},
    "西兰花/蔬菜": {"kcal": 34, "protein": 2.8, "carbs": 7, "fat": 0.4, "fiber": 3, "co2e_g": 45, "category": "veg", "initials": "xlh"},
    "西瓜": {"kcal": 30, "protein": 0.6, "carbs": 7.6, "fat": 0.2, "fiber": 0.4, "co2e_g": 0, "category": "fruit", "initials": "xg"},
    "鸡腿肉去皮/生重": {"kcal": 125, "protein": 20, "carbs": 0, "fat": 5, "fiber": 0, "co2e_g": 560, "category": "protein", "initials": "jtr"},
    "Magerquark": {"kcal": 67, "protein": 12, "carbs": 4, "fat": 0.2, "fiber": 0, "co2e_g": 180, "category": "protein", "initials": "mq"},
    "Skyr natur": {"kcal": 63, "protein": 11, "carbs": 4, "fat": 0.2, "fiber": 0, "co2e_g": 180, "category": "protein", "initials": "skyr"},
}

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
    source: str = "规则估算"


def today_str() -> str:
    return date.today().isoformat()


def current_week_key() -> str:
    y, w, _ = date.today().isocalendar()
    return f"{y}-KW{w:02d}"


def clean_text(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = str(s).replace("\xa0", " ").replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


def safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None or v == "" or (isinstance(v, float) and pd.isna(v)):
            return default
        return float(str(v).replace(",", "."))
    except Exception:
        return default


def safe_int(v, default: Optional[int] = None) -> Optional[int]:
    try:
        if v is None or v == "" or (isinstance(v, float) and pd.isna(v)):
            return default
        return int(float(str(v)))
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
        return bool(re.search(r"\d{1,2}\.\s*", line) or len(line.split()) <= 5)
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
        if price_match:
            price = price_match.group(1)
            food = ""
            prefix = clean_text(line[: price_match.start()])
            if looks_like_food_line(prefix):
                food = prefix
            else:
                for j in range(i - 1, max(-1, i - 8), -1):
                    if looks_like_food_line(lines[j]):
                        food = lines[j]
                        break
            if food:
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
                    rows.append(MenuDish(week_key=week_key, day=current_day, name=food, price=price, source=f"规则估算/{profile}", **macro))

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
                    rows.append(MenuDish(week_key=week_key, day=current_day, name=line, source=f"规则估算/{profile}", **macro))
    return rows


def fetch_url_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MensaFatLossStreamlit/1.0"})
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


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_key TEXT NOT NULL,
            day TEXT NOT NULL,
            name TEXT NOT NULL,
            price TEXT,
            kcal REAL, protein REAL, carbs REAL, fat REAL, fiber REAL, co2e_g REAL,
            source TEXT,
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
            source TEXT,
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
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS body_weights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_key TEXT UNIQUE NOT NULL,
            weight_kg REAL NOT NULL,
            waist_cm REAL,
            note TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profile (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()
    # 兼容旧数据库。
    for table in ["menu_items", "daily_records", "foods"]:
        ensure_column(conn, table, "co2e_g", "REAL DEFAULT 0")
        ensure_column(conn, table, "fiber", "REAL DEFAULT 0")
    for k, v in USER_PROFILE_DEFAULT.items():
        conn.execute("INSERT OR IGNORE INTO user_profile (key, value) VALUES (?, ?)", (k, str(v)))
    for name, v in DEFAULT_FOODS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO foods
            (name, kcal, protein, carbs, fat, fiber, co2e_g, category, initials, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, v["kcal"], v["protein"], v["carbs"], v["fat"], v["fiber"], v["co2e_g"], v["category"], v["initials"], datetime.now().isoformat(timespec="seconds")),
        )
    conn.commit()


def save_week_menu(conn: sqlite3.Connection, week_key: str, rows: List[MenuDish], replace: bool = True) -> None:
    if replace:
        conn.execute("DELETE FROM menu_items WHERE week_key=?", (week_key,))
    now = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        conn.execute(
            """
            INSERT INTO menu_items
            (week_key, day, name, price, kcal, protein, carbs, fat, fiber, co2e_g, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (week_key, r.day, r.name, r.price, r.kcal, r.protein, r.carbs, r.fat, r.fiber, r.co2e_g, r.source, now),
        )
    conn.commit()


def get_week_menu(conn: sqlite3.Connection, week_key: str) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM menu_items WHERE week_key=? ORDER BY id", (week_key,)).fetchall()


def delete_week_menu(conn: sqlite3.Connection, week_key: str) -> None:
    conn.execute("DELETE FROM menu_items WHERE week_key=?", (week_key,))
    conn.commit()


def get_record_rows(conn: sqlite3.Connection, record_date: str) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM daily_records WHERE record_date=? ORDER BY id", (record_date,)).fetchall()


def add_record(conn: sqlite3.Connection, record_date: str, meal: str, name: str, grams: float, macro: Dict[str, float], source: str = "") -> None:
    conn.execute(
        """
        INSERT INTO daily_records
        (record_date, meal, name, grams, kcal, protein, carbs, fat, fiber, co2e_g, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (record_date, meal, name, grams, macro["kcal"], macro["protein"], macro["carbs"], macro["fat"], macro["fiber"], macro["co2e_g"], source, datetime.now().isoformat(timespec="seconds")),
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
        WHERE lower(name) LIKE ? OR lower(category) LIKE ? OR lower(initials) LIKE ? OR CAST(id AS TEXT) LIKE ?
        ORDER BY id
        """,
        (like, like, like, like),
    ).fetchall()


def get_food_by_id(conn: sqlite3.Connection, food_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM foods WHERE id=?", (food_id,)).fetchone()


def make_unique_food_name(conn: sqlite3.Connection, base_name: str) -> str:
    base_name = clean_text(base_name) or "未命名食物"
    existing = conn.execute("SELECT 1 FROM foods WHERE name=?", (base_name,)).fetchone()
    if not existing:
        return base_name
    for i in range(1, 1000):
        candidate = f"{base_name}_复制{i}"
        if conn.execute("SELECT 1 FROM foods WHERE name=?", (candidate,)).fetchone() is None:
            return candidate
    return f"{base_name}_{datetime.now().strftime('%H%M%S')}"


def insert_food(conn: sqlite3.Connection, values: Dict[str, object]) -> int:
    name = make_unique_food_name(conn, clean_text(str(values.get("name", ""))))
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO foods (name, kcal, protein, carbs, fat, fiber, co2e_g, category, initials, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            safe_float(values.get("kcal")), safe_float(values.get("protein")), safe_float(values.get("carbs")),
            safe_float(values.get("fat")), safe_float(values.get("fiber")), safe_float(values.get("co2e_g")),
            clean_text(str(values.get("category", "custom"))), clean_text(str(values.get("initials", ""))).lower(), now,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_food_by_id(conn: sqlite3.Connection, food_id: int, values: Dict[str, object]) -> None:
    name = clean_text(str(values.get("name", "")))
    if not name:
        raise ValueError("食物名称不能为空。")
    duplicate = conn.execute("SELECT id FROM foods WHERE name=? AND id<>?", (name, food_id)).fetchone()
    if duplicate is not None:
        raise ValueError(f"食物名称已存在：{name}")
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE foods SET
            name=?, kcal=?, protein=?, carbs=?, fat=?, fiber=?, co2e_g=?, category=?, initials=?, updated_at=?
        WHERE id=?
        """,
        (
            name, safe_float(values.get("kcal")), safe_float(values.get("protein")), safe_float(values.get("carbs")),
            safe_float(values.get("fat")), safe_float(values.get("fiber")), safe_float(values.get("co2e_g")),
            clean_text(str(values.get("category", "custom"))), clean_text(str(values.get("initials", ""))).lower(), now, food_id,
        ),
    )
    conn.commit()


def delete_food_by_id(conn: sqlite3.Connection, food_id: int) -> None:
    conn.execute("DELETE FROM foods WHERE id=?", (food_id,))
    conn.commit()


def reset_default_foods(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM foods")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='foods'")
    conn.commit()
    for name, v in DEFAULT_FOODS.items():
        insert_food(conn, {"name": name, **v})


def rows_to_csv(rows: Iterable[sqlite3.Row | Dict[str, object]], fields: List[str]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row_value(row, f, "") for f in fields})
    return output.getvalue()


def total_for_records(rows: Iterable[sqlite3.Row]) -> Dict[str, float]:
    return add_macros(macro_from_obj(r) for r in rows)


def get_openai_key() -> str:
    key = ""
    try:
        key = st.secrets.get("OPENAI_API_KEY", "")
    except Exception:
        key = ""
    return os.environ.get("OPENAI_API_KEY", "") or key


def extract_json_from_text(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("模型返回内容不是可解析 JSON。")


def call_openai_estimator(api_key: str, model: str, rows: List[MenuDish]) -> List[MenuDish]:
    prompt = {
        "instruction": (
            "Estimate nutrition for Mensa dishes. Return JSON only. Use one normal cafeteria portion. "
            "Units: kcal, grams protein/carbs/fat/fiber, grams CO2e. Keep existing day/name/price."
        ),
        "schema": [
            {"day": "string", "name": "string", "price": "string", "kcal": "number", "protein": "number", "carbs": "number", "fat": "number", "fiber": "number", "co2e_g": "number"}
        ],
        "dishes": [{"day": r.day, "name": r.name, "price": r.price, "local_estimate": macro_from_obj(r)} for r in rows],
    }
    payload = json.dumps({"model": model, "input": json.dumps(prompt, ensure_ascii=False)}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API 请求失败：HTTP {e.code}\n{msg}")

    text = data.get("output_text", "")
    if not text:
        chunks = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if "text" in content:
                    chunks.append(content["text"])
        text = "\n".join(chunks)
    parsed = extract_json_from_text(text)
    if isinstance(parsed, dict) and "dishes" in parsed:
        parsed = parsed["dishes"]
    if not isinstance(parsed, list):
        raise ValueError("OpenAI 返回 JSON 不是列表。")

    out: List[MenuDish] = []
    for item in parsed:
        name = clean_text(str(item.get("name", "")))
        if not name:
            continue
        out.append(
            MenuDish(
                week_key=rows[0].week_key if rows else current_week_key(),
                day=clean_text(str(item.get("day", "未识别日期"))),
                name=name,
                price=clean_text(str(item.get("price", ""))),
                kcal=safe_float(item.get("kcal"), 0),
                protein=safe_float(item.get("protein"), 0),
                carbs=safe_float(item.get("carbs"), 0),
                fat=safe_float(item.get("fat"), 0),
                fiber=safe_float(item.get("fiber"), 0),
                co2e_g=safe_float(item.get("co2e_g"), 0),
                source="ChatGPT估算",
            )
        )
    return out


def rerun():
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()


def render_metric_row(total: Dict[str, float], target: Optional[Dict[str, float]] = None) -> None:
    cols = st.columns(5)
    data = [("kcal", "热量"), ("protein", "蛋白 g"), ("carbs", "碳水 g"), ("fat", "脂肪 g"), ("fiber", "纤维 g")]
    for col, (key, label) in zip(cols, data):
        if target and key in target:
            delta = total.get(key, 0) - target[key]
            col.metric(label, f"{total.get(key, 0):.0f}", f"{delta:+.0f}")
        else:
            col.metric(label, f"{total.get(key, 0):.0f}")


def df_from_rows(rows: Iterable[sqlite3.Row], include_id: bool = False, include_co2: bool = False) -> pd.DataFrame:
    data = []
    for r in rows:
        item = {}
        if include_id:
            item["id"] = row_value(r, "id", "")
        for f in ["day", "record_date", "meal", "name", "price", "grams"]:
            try:
                _ = r[f]
                item[f] = r[f]
            except Exception:
                pass
        labels = {"kcal": "kcal", "protein": "蛋白", "carbs": "碳水", "fat": "脂肪", "fiber": "纤维", "co2e_g": "CO2e"}
        for k in DISPLAY_MACRO_KEYS + (["co2e_g"] if include_co2 else []):
            if k in r.keys():
                item[labels[k]] = safe_float(r[k])
        data.append(item)
    return pd.DataFrame(data)


def get_records_between(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM daily_records WHERE record_date BETWEEN ? AND ? ORDER BY record_date, id", (start, end)
    ).fetchall()


def aggregate_daily(records: List[sqlite3.Row]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    rows = []
    for r in records:
        rows.append({
            "date": r["record_date"], "kcal": safe_float(r["kcal"]), "protein": safe_float(r["protein"]),
            "carbs": safe_float(r["carbs"]), "fat": safe_float(r["fat"]), "fiber": safe_float(r["fiber"]),
        })
    df = pd.DataFrame(rows)
    return df.groupby("date", as_index=False).sum()


def save_weight(conn: sqlite3.Connection, week_key: str, weight_kg: float, waist_cm: float, note: str) -> None:
    conn.execute(
        """
        INSERT INTO body_weights (week_key, weight_kg, waist_cm, note, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(week_key) DO UPDATE SET
            weight_kg=excluded.weight_kg, waist_cm=excluded.waist_cm, note=excluded.note, created_at=excluded.created_at
        """,
        (week_key, weight_kg, waist_cm if waist_cm > 0 else None, note, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def get_weights(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM body_weights ORDER BY week_key").fetchall()


def get_profile(conn: sqlite3.Connection) -> Dict[str, str]:
    rows = conn.execute("SELECT key, value FROM user_profile").fetchall()
    return {r["key"]: r["value"] for r in rows}


def save_profile(conn: sqlite3.Connection, values: Dict[str, object]) -> None:
    for k, v in values.items():
        conn.execute("INSERT OR REPLACE INTO user_profile (key, value) VALUES (?, ?)", (k, str(v)))
    conn.commit()


def advice_from_data(summary_df: pd.DataFrame, weights: List[sqlite3.Row], target: Dict[str, float]) -> str:
    lines = []
    if not summary_df.empty:
        last = summary_df.tail(7)
        avg_kcal = last["kcal"].mean()
        avg_p = last["protein"].mean()
        lines.append(f"最近 {len(last)} 个记录日平均：{avg_kcal:.0f} kcal，蛋白 {avg_p:.0f} g。")
        if avg_p < target["protein"] - 15:
            lines.append("蛋白明显低于目标：优先把蛋白补到 180–190 g；可以用鸡胸、虾仁、Magerquark、Skyr 或 40 g 乳清补足。")
        if avg_kcal > target["kcal"] + 200:
            lines.append("平均热量高于当前目标较多：先检查 Mensa 炸物、奶油酱、披萨、汉堡和晚餐油脂。")
    if len(weights) >= 2:
        w0 = safe_float(weights[-2]["weight_kg"])
        w1 = safe_float(weights[-1]["weight_kg"])
        diff = w1 - w0
        lines.append(f"最近两次体重变化：{diff:+.2f} kg。")
        if diff > -0.2:
            lines.append("体重下降偏慢或上升：若连续两周如此，建议每日平均热量下调 150–200 kcal，先从碳水或脂肪减少。")
        elif diff < -1.0:
            lines.append("体重下降较快：如果训练表现下降或饥饿感明显，训练日增加 100–150 kcal。")
        else:
            lines.append("体重下降速度基本合理：维持当前摄入，继续观察一周。")
    if not lines:
        return "数据还不够。至少记录 3–7 天饮食和 2 次周体重后，建议会更可靠。"
    return "\n\n".join(lines)


def import_food_from_openfoodfacts(search_text: str) -> Optional[Dict[str, object]]:
    q = urllib.parse.urlencode({"search_terms": search_text, "search_simple": 1, "action": "process", "json": 1, "page_size": 1})
    url = f"https://world.openfoodfacts.org/cgi/search.pl?{q}"
    with urllib.request.urlopen(url, timeout=25) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    products = data.get("products", [])
    if not products:
        return None
    p = products[0]
    nutr = p.get("nutriments", {})
    name = clean_text(p.get("product_name") or p.get("generic_name") or search_text)
    return {
        "name": name,
        "kcal": safe_float(nutr.get("energy-kcal_100g")),
        "protein": safe_float(nutr.get("proteins_100g")),
        "carbs": safe_float(nutr.get("carbohydrates_100g")),
        "fat": safe_float(nutr.get("fat_100g")),
        "fiber": safe_float(nutr.get("fiber_100g")),
        "co2e_g": 0.0,
        "category": "imported",
        "initials": "",
    }


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🍽️", layout="wide")
    st.title(APP_TITLE)
    st.caption("Streamlit 网页版：导入本周菜单 → 估算营养 → 加入每天记录。食物 ID 使用 SQLite 固定主键，界面只读。")

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
        st.divider()
        st.header("OpenAI / ChatGPT")
        model = st.text_input("模型", value=DEFAULT_MODEL)
        api_key_input = st.text_input("OpenAI API key，可留空", value="", type="password")
        st.caption("留空时只使用本地估算。")

    tab_import, tab_week, tab_day, tab_food, tab_history = st.tabs(["1 导入菜单", "2 本周菜单", "3 每天记录", "4 食物数据库", "5 历史/周月总结"])

    with tab_import:
        st.subheader("导入一周 Mensa 菜单")
        source_mode = st.radio("导入方式", ["URL", "上传 HTML/TXT", "粘贴网页文本"], horizontal=True)
        raw_text = ""
        if source_mode == "URL":
            url = st.text_input("Mensa URL", value=DEFAULT_URL)
            if st.button("抓取 URL 并解析", type="primary"):
                try:
                    raw_text = fetch_url_text(url)
                    st.session_state["raw_menu_text"] = raw_text
                    st.success("URL 抓取成功。")
                except Exception as e:
                    st.error(f"抓取失败：{e}")
        elif source_mode == "上传 HTML/TXT":
            file = st.file_uploader("上传从浏览器保存的网页 HTML 或 TXT", type=["html", "htm", "txt"])
            if file is not None:
                raw_text = file.read().decode("utf-8", errors="replace")
                st.session_state["raw_menu_text"] = raw_text
                st.success("文件读取成功。")
        else:
            raw_text = st.text_area("粘贴网页文本/HTML", height=260)
            if st.button("使用粘贴文本"):
                st.session_state["raw_menu_text"] = raw_text
                st.success("粘贴文本已载入。")

        raw_loaded = st.session_state.get("raw_menu_text", "")
        if raw_loaded:
            st.info(f"已载入文本长度：{len(raw_loaded)} 字符")
            local_rows = parse_menu_from_text(raw_loaded, week_key)
            st.write(f"本地解析到 **{len(local_rows)}** 个菜品。")
            if local_rows:
                preview = [{"日期": r.day, "菜品": r.name, "价格": r.price, "kcal": r.kcal, "蛋白": r.protein, "碳水": r.carbs, "脂肪": r.fat, "纤维": r.fiber} for r in local_rows]
                st.dataframe(preview, use_container_width=True, hide_index=True)
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("保存本地估算到本周菜单", type="primary"):
                        save_week_menu(conn, week_key, local_rows, replace=True)
                        st.success("已保存到本周菜单。")
                        rerun()
                with c2:
                    if st.button("用 ChatGPT 重新估算并保存"):
                        key = api_key_input or get_openai_key()
                        if not key:
                            st.error("缺少 OpenAI API key。可以先用本地估算。")
                        else:
                            with st.spinner("正在调用 OpenAI API 估算整周菜单……"):
                                try:
                                    ai_rows = call_openai_estimator(key, model, local_rows)
                                    save_week_menu(conn, week_key, ai_rows, replace=True)
                                    st.success(f"ChatGPT 估算完成，已保存 {len(ai_rows)} 个菜品。")
                                    rerun()
                                except Exception as e:
                                    st.error(str(e))

    with tab_week:
        st.subheader(f"本周菜单：{week_key}")
        menu_rows = get_week_menu(conn, week_key)
        if not menu_rows:
            st.warning("还没有本周菜单。先到“导入菜单”页保存。")
        else:
            menu_csv = rows_to_csv(menu_rows, ["day", "name", "price", *DISPLAY_MACRO_KEYS])
            st.download_button("导出本周菜单 CSV", menu_csv, file_name=f"mensa_menu_{week_key}.csv", mime="text/csv")
            with st.expander("删除本周菜单数据"):
                st.warning("只会删除当前 week_key 的菜单，不会删除每天记录。")
                if st.button(f"删除 {week_key} 的全部菜单", type="secondary"):
                    delete_week_menu(conn, week_key)
                    st.success("本周菜单已删除。")
                    rerun()

            day_filter = st.selectbox("按日期筛选", ["全部"] + sorted({r["day"] for r in menu_rows}))
            rows_show = [r for r in menu_rows if day_filter == "全部" or r["day"] == day_filter]
            for r in rows_show:
                with st.container(border=True):
                    top_cols = st.columns([3, 1, 1, 1, 1, 1, 1])
                    top_cols[0].markdown(f"**{r['name']}**  \n{r['day']} · {r['price']}")
                    top_cols[1].metric("kcal", f"{r['kcal']:.0f}")
                    top_cols[2].metric("P", f"{r['protein']:.0f}")
                    top_cols[3].metric("C", f"{r['carbs']:.0f}")
                    top_cols[4].metric("F", f"{r['fat']:.0f}")
                    top_cols[5].metric("Fiber", f"{r['fiber']:.0f}")
                    with top_cols[6]:
                        meal = st.selectbox("餐次", ["午餐", "晚餐", "早餐", "加餐"], key=f"meal_menu_{r['id']}")
                        if st.button("加入当天", key=f"add_menu_{r['id']}"):
                            add_record(conn, record_date, meal, r["name"], 1.0, macro_from_obj(r), "")
                            st.success("已加入当天记录。")
                            rerun()

    with tab_day:
        st.subheader(f"每天记录：{record_date}")
        records = get_record_rows(conn, record_date)
        total = total_for_records(records)
        render_metric_row(total, target)
        if records:
            rec_csv = rows_to_csv(records, ["record_date", "meal", "name", "grams", *DISPLAY_MACRO_KEYS])
            st.download_button("导出当天记录 CSV", rec_csv, file_name=f"daily_record_{record_date}.csv", mime="text/csv")
            with st.expander("清空当天记录"):
                st.warning("会删除当前日期的所有记录。")
                if st.button("清空这一天", key="clear_day_records"):
                    clear_day_records(conn, record_date)
                    st.success("当天记录已清空。")
                    rerun()

            header = st.columns([1, 3, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8])
            for col, text in zip(header, ["餐次", "名称", "克/份", "kcal", "蛋白", "碳水", "脂肪", "操作"]):
                col.markdown(f"**{text}**")
            for r in records:
                cols = st.columns([1, 3, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8])
                cols[0].write(r["meal"])
                cols[1].write(r["name"])
                cols[2].write(f"{safe_float(r['grams']):.0f}" if r["grams"] else "-")
                cols[3].write(f"{safe_float(r['kcal']):.0f}")
                cols[4].write(f"{safe_float(r['protein']):.0f}")
                cols[5].write(f"{safe_float(r['carbs']):.0f}")
                cols[6].write(f"{safe_float(r['fat']):.0f}")
                if cols[7].button("删除", key=f"del_rec_{r['id']}"):
                    delete_record(conn, int(r["id"]))
                    rerun()
        else:
            st.info("当天还没有记录。可以从“本周菜单”加入，或下面手动添加食物。")

        st.divider()
        st.subheader("蛋白不足时加入 40g 乳清")
        protein_short = max(0.0, target["protein"] - total.get("protein", 0.0))
        st.write(f"当前蛋白缺口：**{protein_short:.0f} g**")
        whey = conn.execute("SELECT * FROM foods WHERE name=?", ("乳清蛋白粉",)).fetchone()
        if st.button("如果蛋白不足，加入 40g 乳清"):
            if protein_short <= 0:
                st.info("当前蛋白已经达到目标，不需要加入乳清。")
            elif whey is None:
                st.error("数据库里没有“乳清蛋白粉”。")
            else:
                macro = scale_food(whey, 40)
                add_record(conn, record_date, "加餐", "乳清蛋白粉", 40, macro, "")
                st.success("已加入 40g 乳清蛋白粉。")
                rerun()

        st.divider()
        st.subheader("从食物数据库添加")
        q = st.text_input("搜索食物：中文/英文/德文/拼音首字母", placeholder="例如 sjxr, ymp, rqdbf, Magerquark")
        foods = get_foods(conn, q)
        meal_food = st.selectbox("餐次", ["晚餐", "早餐", "午餐", "加餐"], key="meal_food")
        grams = st.number_input("克数", min_value=0.0, value=100.0, step=10.0)
        for f in foods[:50]:
            cols = st.columns([3, 1, 1, 1, 1, 1])
            cols[0].write(f"**{f['name']}** · {f['category']} · {f['initials']}")
            macro = scale_food(f, grams)
            cols[1].write(f"{macro['kcal']:.0f} kcal")
            cols[2].write(f"P {macro['protein']:.0f}")
            cols[3].write(f"C {macro['carbs']:.0f}")
            cols[4].write(f"F {macro['fat']:.0f}")
            if cols[5].button("添加", key=f"add_food_{f['id']}"):
                add_record(conn, record_date, meal_food, f["name"], grams, macro, "")
                st.success("已添加。")
                rerun()

    with tab_food:
        st.subheader("食物数据库：直接编辑 / 新增 / 复制 / 删除")
        st.info("id 是 SQLite 固定主键，只读；新增行留空 id 即可。勾选复制会创建新 id，勾选删除会删除该 id。界面不再显示数据来源列。")
        q2 = st.text_input("搜索数据库", key="db_search")
        food_rows = get_foods(conn, q2)
        table_rows = []
        for f in food_rows:
            table_rows.append({
                "删除": False,
                "复制": False,
                "id": int(f["id"]),
                "name": f["name"],
                "kcal": safe_float(f["kcal"]),
                "protein": safe_float(f["protein"]),
                "carbs": safe_float(f["carbs"]),
                "fat": safe_float(f["fat"]),
                "fiber": safe_float(f["fiber"]),
                "co2e_g": safe_float(f["co2e_g"]),
                "category": f["category"],
                "initials": f["initials"],
            })
        df = pd.DataFrame(table_rows, columns=["删除", "复制", "id", "name", "kcal", "protein", "carbs", "fat", "fiber", "co2e_g", "category", "initials"])
        edited = st.data_editor(
            df,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            disabled=["id"],
            column_config={
                "删除": st.column_config.CheckboxColumn("删除", help="保存后删除这一行"),
                "复制": st.column_config.CheckboxColumn("复制", help="保存后复制这一行，新食物自动生成新 id"),
                "id": st.column_config.NumberColumn("固定ID", help="数据库主键，只读，不要手动改"),
                "name": st.column_config.TextColumn("名称", required=True),
                "kcal": st.column_config.NumberColumn("kcal/100g", min_value=0.0),
                "protein": st.column_config.NumberColumn("蛋白/100g", min_value=0.0),
                "carbs": st.column_config.NumberColumn("碳水/100g", min_value=0.0),
                "fat": st.column_config.NumberColumn("脂肪/100g", min_value=0.0),
                "fiber": st.column_config.NumberColumn("纤维/100g", min_value=0.0),
                "co2e_g": st.column_config.NumberColumn("CO2e/100g", min_value=0.0),
                "category": st.column_config.TextColumn("类别"),
                "initials": st.column_config.TextColumn("拼音/缩写"),
            },
            key="food_editor_v3",
        )
        c_save, c_reset = st.columns([1, 1])
        with c_save:
            if st.button("保存食物库编辑 / 新增 / 复制 / 删除", type="primary"):
                try:
                    delete_count = update_count = insert_count = copy_count = 0
                    for _, row in edited.iterrows():
                        name = clean_text(row.get("name", ""))
                        fid = safe_int(row.get("id"), None)
                        do_delete = bool(row.get("删除", False))
                        do_copy = bool(row.get("复制", False))
                        values = {
                            "name": name,
                            "kcal": safe_float(row.get("kcal")), "protein": safe_float(row.get("protein")),
                            "carbs": safe_float(row.get("carbs")), "fat": safe_float(row.get("fat")),
                            "fiber": safe_float(row.get("fiber")), "co2e_g": safe_float(row.get("co2e_g")),
                            "category": clean_text(row.get("category", "custom")),
                            "initials": clean_text(row.get("initials", "")).lower(),
                        }
                        if fid is not None and do_delete:
                            delete_food_by_id(conn, fid)
                            delete_count += 1
                            continue
                        if fid is not None:
                            if name:
                                update_food_by_id(conn, fid, values)
                                update_count += 1
                            if do_copy and name:
                                copy_values = dict(values)
                                copy_values["name"] = f"{name}_复制"
                                insert_food(conn, copy_values)
                                copy_count += 1
                        else:
                            if name and not do_delete:
                                insert_food(conn, values)
                                insert_count += 1
                    st.success(f"已保存：更新 {update_count}，新增 {insert_count}，复制 {copy_count}，删除 {delete_count}。")
                    rerun()
                except Exception as e:
                    st.error(f"保存失败：{e}")
        with c_reset:
            with st.expander("重置食物库"):
                st.warning("会删除当前 foods 表并重新导入默认食物，包括你上传 CSV 中能识别的数据。")
                if st.button("清空并恢复默认食物库"):
                    reset_default_foods(conn)
                    st.success("已恢复默认食物库。")
                    rerun()

        st.divider()
        st.subheader("更便捷：从 Open Food Facts 搜索导入包装食品")
        off_query = st.text_input("产品关键词/条码", placeholder="例如 whey protein, skyr, magerquark")
        if st.button("从 Open Food Facts 导入第一条结果"):
            if not clean_text(off_query):
                st.error("请输入关键词。")
            else:
                try:
                    item = import_food_from_openfoodfacts(off_query)
                    if item is None:
                        st.warning("没有找到结果。")
                    else:
                        insert_food(conn, item)
                        st.success(f"已导入：{item['name']}")
                        rerun()
                except Exception as e:
                    st.error(f"导入失败：{e}")

    with tab_history:
        st.subheader("历史记录、周/月总结、体重")
        profile = get_profile(conn)
        with st.expander("身体数据 / 计划设置", expanded=False):
            c1, c2, c3, c4 = st.columns(4)
            height_cm = c1.number_input("身高 cm", value=safe_float(profile.get("height_cm"), 176), step=1.0)
            age = c2.number_input("年龄", value=safe_float(profile.get("age"), 30), step=1.0)
            current_weight = c3.number_input("当前体重 kg", value=safe_float(profile.get("current_weight_kg"), 91), step=0.1)
            target_weight = c4.number_input("目标体重 kg", value=safe_float(profile.get("target_weight_kg"), 85), step=0.1)
            training_note = st.text_input("训练计划", value=profile.get("training_note", USER_PROFILE_DEFAULT["training_note"]))
            if st.button("保存身体数据/计划"):
                save_profile(conn, {"height_cm": height_cm, "age": age, "current_weight_kg": current_weight, "target_weight_kg": target_weight, "training_note": training_note})
                st.success("已保存。")
                rerun()

        st.markdown("### 每周体重")
        cw1, cw2, cw3 = st.columns(3)
        weight_kg = cw1.number_input("本周体重 kg", value=safe_float(profile.get("current_weight_kg"), 91), step=0.1)
        waist_cm = cw2.number_input("腰围 cm，可选", value=0.0, step=0.5)
        weight_note = cw3.text_input("备注", value="")
        if st.button("保存/更新本周体重"):
            save_weight(conn, week_key, weight_kg, waist_cm, weight_note)
            st.success("本周体重已保存。")
            rerun()
        weights = get_weights(conn)
        if weights:
            wdf = pd.DataFrame([{"week": w["week_key"], "weight_kg": w["weight_kg"], "waist_cm": w["waist_cm"], "note": w["note"]} for w in weights])
            st.dataframe(wdf, use_container_width=True, hide_index=True)
            st.line_chart(wdf.set_index("week")[["weight_kg"]])

        st.markdown("### 摄入汇总")
        today = date.fromisoformat(record_date)
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        mode = st.radio("汇总范围", ["本周", "本月", "自定义"], horizontal=True)
        if mode == "本周":
            start_date, end_date = week_start, week_start + timedelta(days=6)
        elif mode == "本月":
            start_date = month_start
            next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            end_date = next_month - timedelta(days=1)
        else:
            c1, c2 = st.columns(2)
            start_date = c1.date_input("开始日期", value=week_start, key="hist_start")
            end_date = c2.date_input("结束日期", value=today, key="hist_end")
        hist_records = get_records_between(conn, start_date.isoformat(), end_date.isoformat())
        daily_df = aggregate_daily(hist_records)
        if daily_df.empty:
            st.info("这个时间范围内没有饮食记录。")
        else:
            totals = {k: float(daily_df[k].sum()) for k in ["kcal", "protein", "carbs", "fat", "fiber"]}
            avg = {k: float(daily_df[k].mean()) for k in ["kcal", "protein", "carbs", "fat", "fiber"]}
            st.write(f"范围：{start_date.isoformat()} 到 {end_date.isoformat()}，记录天数：{len(daily_df)}")
            st.markdown("**每日平均**")
            render_metric_row({**avg, "co2e_g": 0})
            st.dataframe(daily_df, use_container_width=True, hide_index=True)
            st.line_chart(daily_df.set_index("date")[["kcal", "protein", "carbs", "fat"]])
            csv_text = daily_df.to_csv(index=False)
            st.download_button("导出汇总 CSV", csv_text, file_name=f"summary_{start_date}_{end_date}.csv", mime="text/csv")
            st.markdown("### 摄入调整建议")
            st.write(advice_from_data(daily_df, weights, target))

    st.caption("提示：Mensa 菜品没有固定称重，营养为估算值。食物库 ID 是数据库内部主键，删除后不会重用已删除 ID；这属于正常行为，不是随机。")


if __name__ == "__main__":
    main()
