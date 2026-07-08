# -*- coding: utf-8 -*-
"""
Alte Mensa Dresden 减脂营养记录器 - Streamlit 网页版 v4 手动数据库版

运行：
    pip install -r requirements.txt
    streamlit run streamlit_app.py

v4 核心逻辑：
- 移除联网搜索 / OpenAI 自动搜索功能。
- 食物库采用稳定 SQLite id；id 只读，名称可修改，默认食物也可删除。
- 默认食物只在第一次建库时写入；删除后不会自动恢复，除非手动点击“恢复默认食物库”。
- 食物库编辑、复制、删除、新增全部走明确的数据库操作，不再依赖表格行号。
- 界面不显示 source/数据来源列，也不在每项食物后显示 CO2e。
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import streamlit as st

APP_TITLE = "Alte Mensa 减脂营养记录器"
DEFAULT_URL = "https://www.studentenwerk-dresden.de/mensen/speiseplan/alte-mensa.html"
DB_PATH = Path(__file__).with_name("mensa_streamlit.sqlite3")

MACRO_KEYS = ["kcal", "protein", "carbs", "fat", "fiber", "co2e_g"]
DISPLAY_KEYS = ["kcal", "protein", "carbs", "fat", "fiber"]

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

# 已根据用户上传 CSV 的营养值内置；乱码名称按 initials 和数值尽量还原。
DEFAULT_FOODS = [
    {"name": "乳清蛋白粉", "kcal": 390, "protein": 78, "carbs": 8, "fat": 6, "fiber": 0, "co2e_g": 350, "category": "protein", "initials": "rqdbf"},
    {"name": "全蛋", "kcal": 143, "protein": 12.6, "carbs": 0.7, "fat": 9.5, "fiber": 0, "co2e_g": 450, "category": "protein_fat", "initials": "qd"},
    {"name": "农夫面包 Bauernbrot", "kcal": 225, "protein": 6, "carbs": 45, "fat": 1, "fiber": 0, "co2e_g": 0, "category": "carb", "initials": "nfmb"},
    {"name": "土豆/生重", "kcal": 77, "protein": 2, "carbs": 17, "fat": 0.1, "fiber": 2.2, "co2e_g": 35, "category": "carb", "initials": "td"},
    {"name": "牛肋排/熟", "kcal": 256, "protein": 28, "carbs": 0.1, "fat": 16, "fiber": 0, "co2e_g": 0, "category": "protein_fat", "initials": "nlp"},
    {"name": "西红柿", "kcal": 20, "protein": 1, "carbs": 6, "fat": 0.2, "fiber": 1.5, "co2e_g": 0, "category": "veg", "initials": "xhs"},
    {"name": "干意面", "kcal": 353, "protein": 13, "carbs": 72, "fat": 1.5, "fiber": 3, "co2e_g": 150, "category": "carb", "initials": "gym"},
    {"name": "干米", "kcal": 360, "protein": 7, "carbs": 80, "fat": 0.7, "fiber": 1.3, "co2e_g": 270, "category": "carb", "initials": "gm"},
    {"name": "熟米饭", "kcal": 130, "protein": 2.7, "carbs": 28, "fat": 0.3, "fiber": 0.4, "co2e_g": 100, "category": "carb", "initials": "smf"},
    {"name": "橄榄油", "kcal": 884, "protein": 0, "carbs": 0, "fat": 100, "fiber": 0, "co2e_g": 530, "category": "fat", "initials": "gly"},
    {"name": "洋葱", "kcal": 42, "protein": 1, "carbs": 9.3, "fat": 0.1, "fiber": 1.5, "co2e_g": 0, "category": "veg", "initials": "yc"},
    {"name": "燕麦片", "kcal": 370, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10, "co2e_g": 90, "category": "carb", "initials": "ymp"},
    {"name": "牛肉馅", "kcal": 230, "protein": 16, "carbs": 0.1, "fat": 10, "fiber": 0, "co2e_g": 0, "category": "protein_fat", "initials": "nrx"},
    {"name": "生鸡胸肉", "kcal": 110, "protein": 23, "carbs": 0, "fat": 1.5, "fiber": 0, "co2e_g": 560, "category": "protein", "initials": "sjxr"},
    {"name": "瘦牛肉/约5%脂肪", "kcal": 137, "protein": 21, "carbs": 0, "fat": 5, "fiber": 0, "co2e_g": 2700, "category": "protein", "initials": "snr"},
    {"name": "白菜/生菜/菠菜", "kcal": 15, "protein": 1.1, "carbs": 1.2, "fat": 0.3, "fiber": 2, "co2e_g": 0, "category": "veg", "initials": "bcsc"},
    {"name": "导入食物_wynxcdz", "kcal": 213, "protein": 15.7, "carbs": 1.5, "fat": 16, "fiber": 0, "co2e_g": 0, "category": "custom", "initials": "wynxcdz"},
    {"name": "虾仁", "kcal": 99, "protein": 24, "carbs": 0.2, "fat": 0.3, "fiber": 0, "co2e_g": 1000, "category": "protein", "initials": "xr"},
    {"name": "西兰花/蔬菜", "kcal": 34, "protein": 2.8, "carbs": 7, "fat": 0.4, "fiber": 3, "co2e_g": 45, "category": "veg", "initials": "xlh"},
    {"name": "西瓜", "kcal": 30, "protein": 0.6, "carbs": 7.6, "fat": 0.2, "fiber": 0.4, "co2e_g": 0, "category": "fruit", "initials": "xg"},
    {"name": "鸡腿肉去皮/生重", "kcal": 125, "protein": 20, "carbs": 0, "fat": 5, "fiber": 0, "co2e_g": 560, "category": "protein", "initials": "jtr"},
    {"name": "Magerquark", "kcal": 67, "protein": 12, "carbs": 4, "fat": 0.2, "fiber": 0, "co2e_g": 180, "category": "protein", "initials": "mq"},
    {"name": "Skyr natur", "kcal": 63, "protein": 11, "carbs": 4, "fat": 0.2, "fiber": 0, "co2e_g": 180, "category": "protein", "initials": "skyr"},
]

DAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
PRICE_RE = re.compile(r"(\d+[,.]\d{2}\s*€|ausverkauft)", re.I)
HTML_TAG_RE = re.compile(r"<[^>]+>")
EXCLUDE_PHRASES = [
    "Studentenwerk", "Datenschutz", "Impressum", "Barrierefreiheit", "Drucken", "Vorherige Woche",
    "Nächste Woche", "Mensa wählen", "Suchbegriff", "Raster", "Liste", "Cookie", "Öffnungszeiten",
    "Image:", "Info", "Infos", "KlimaTeller", "Leider keine Angebote", "Ihre Position", "Navigation",
    "Speiseplan", "Startseite", "Legende", "Zusatzstoffe", "Allergene", "Campus", "Dresden", "CO₂", "CO2",
]


# ----------------------------- 基础工具 -----------------------------

def today_str() -> str:
    return date.today().isoformat()


def current_week_key() -> str:
    y, w, _ = date.today().isocalendar()
    return f"{y}-KW{w:02d}"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def clean_text(s: object) -> str:
    if s is None:
        return ""
    s = str(s).replace("\xa0", " ").replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def safe_float(v: object, default: float = 0.0) -> float:
    try:
        if v is None or v == "" or (isinstance(v, float) and pd.isna(v)):
            return default
        return float(str(v).replace(",", "."))
    except Exception:
        return default


def safe_int(v: object, default: Optional[int] = None) -> Optional[int]:
    try:
        if v is None or v == "" or (isinstance(v, float) and pd.isna(v)):
            return default
        return int(float(str(v)))
    except Exception:
        return default


def macro_dict(row: object) -> Dict[str, float]:
    d = {}
    for k in MACRO_KEYS:
        try:
            if isinstance(row, sqlite3.Row):
                d[k] = safe_float(row[k])
            elif isinstance(row, dict):
                d[k] = safe_float(row.get(k, 0))
            else:
                d[k] = safe_float(getattr(row, k, 0))
        except Exception:
            d[k] = 0.0
    return d


def add_macros(rows: Iterable[object]) -> Dict[str, float]:
    total = {k: 0.0 for k in MACRO_KEYS}
    for r in rows:
        m = macro_dict(r)
        for k in MACRO_KEYS:
            total[k] += m[k]
    return total


def scale_per100(row: object, grams: float) -> Dict[str, float]:
    m = macro_dict(row)
    factor = grams / 100.0
    return {k: m[k] * factor for k in MACRO_KEYS}


def display_macro_line(m: Dict[str, float]) -> str:
    return f"{m['kcal']:.0f} kcal | P {m['protein']:.1f}g / C {m['carbs']:.1f}g / F {m['fat']:.1f}g / 纤维 {m['fiber']:.1f}g"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ----------------------------- 数据库 -----------------------------

def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS foods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            kcal REAL DEFAULT 0,
            protein REAL DEFAULT 0,
            carbs REAL DEFAULT 0,
            fat REAL DEFAULT 0,
            fiber REAL DEFAULT 0,
            co2e_g REAL DEFAULT 0,
            category TEXT DEFAULT 'custom',
            initials TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_key TEXT NOT NULL,
            day TEXT NOT NULL,
            name TEXT NOT NULL,
            price TEXT DEFAULT '',
            kcal REAL DEFAULT 0,
            protein REAL DEFAULT 0,
            carbs REAL DEFAULT 0,
            fat REAL DEFAULT 0,
            fiber REAL DEFAULT 0,
            co2e_g REAL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date TEXT NOT NULL,
            meal TEXT NOT NULL,
            name TEXT NOT NULL,
            grams REAL DEFAULT 1,
            kcal REAL DEFAULT 0,
            protein REAL DEFAULT 0,
            carbs REAL DEFAULT 0,
            fat REAL DEFAULT 0,
            fiber REAL DEFAULT 0,
            co2e_g REAL DEFAULT 0,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS weights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            weigh_date TEXT NOT NULL UNIQUE,
            weight_kg REAL NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profile (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()

    food_count = cur.execute("SELECT COUNT(*) FROM foods").fetchone()[0]
    seeded = cur.execute("SELECT value FROM meta WHERE key='foods_seeded_v4'").fetchone()
    if seeded is None:
        if food_count == 0:
            seed_default_foods(conn)
        # 无论之前是否已有食物，都设置已初始化，避免用户删空后下一次又自动恢复。
        cur.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('foods_seeded_v4', '1')")
        conn.commit()

    for k, v in USER_PROFILE_DEFAULT.items():
        cur.execute("INSERT OR IGNORE INTO user_profile(key, value) VALUES(?, ?)", (k, str(v)))
    conn.commit()
    conn.close()


def seed_default_foods(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for f in DEFAULT_FOODS:
        cur.execute(
            """
            INSERT INTO foods(name, kcal, protein, carbs, fat, fiber, co2e_g, category, initials, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f["name"], f["kcal"], f["protein"], f["carbs"], f["fat"], f["fiber"], f["co2e_g"],
                f.get("category", "custom"), f.get("initials", ""), now_iso(), now_iso()
            ),
        )
    conn.commit()


def reset_default_foods() -> None:
    conn = get_conn()
    conn.execute("DELETE FROM foods")
    seed_default_foods(conn)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('foods_seeded_v4', '1')")
    conn.commit()
    conn.close()


def load_foods(search: str = "") -> pd.DataFrame:
    conn = get_conn()
    q = clean_text(search).lower()
    if q:
        like = f"%{q}%"
        df = pd.read_sql_query(
            """
            SELECT id, name, kcal, protein, carbs, fat, fiber, category, initials
            FROM foods
            WHERE lower(name) LIKE ? OR lower(initials) LIKE ? OR lower(category) LIKE ?
            ORDER BY id
            """,
            conn,
            params=(like, like, like),
        )
    else:
        df = pd.read_sql_query(
            "SELECT id, name, kcal, protein, carbs, fat, fiber, category, initials FROM foods ORDER BY id",
            conn,
        )
    conn.close()
    return df


def get_food(food_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM foods WHERE id=?", (food_id,)).fetchone()
    conn.close()
    return row


def insert_food(data: Dict[str, object]) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO foods(name, kcal, protein, carbs, fat, fiber, co2e_g, category, initials, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_text(data.get("name")) or "未命名食物",
            safe_float(data.get("kcal")), safe_float(data.get("protein")), safe_float(data.get("carbs")),
            safe_float(data.get("fat")), safe_float(data.get("fiber")), safe_float(data.get("co2e_g")),
            clean_text(data.get("category")) or "custom", clean_text(data.get("initials")), now_iso(), now_iso(),
        ),
    )
    new_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return new_id


def update_food(food_id: int, data: Dict[str, object]) -> None:
    conn = get_conn()
    conn.execute(
        """
        UPDATE foods
        SET name=?, kcal=?, protein=?, carbs=?, fat=?, fiber=?, category=?, initials=?, updated_at=?
        WHERE id=?
        """,
        (
            clean_text(data.get("name")) or "未命名食物",
            safe_float(data.get("kcal")), safe_float(data.get("protein")), safe_float(data.get("carbs")),
            safe_float(data.get("fat")), safe_float(data.get("fiber")),
            clean_text(data.get("category")) or "custom", clean_text(data.get("initials")), now_iso(), int(food_id),
        ),
    )
    conn.commit()
    conn.close()


def delete_food(food_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM foods WHERE id=?", (int(food_id),))
    conn.commit()
    conn.close()


def duplicate_food(food_id: int, edited_data: Optional[Dict[str, object]] = None) -> int:
    if edited_data is None:
        row = get_food(food_id)
        if row is None:
            raise ValueError("食物不存在")
        data = dict(row)
    else:
        data = dict(edited_data)
    base_name = clean_text(data.get("name")) or "未命名食物"
    data["name"] = next_copy_name(base_name)
    return insert_food(data)


def next_copy_name(base_name: str) -> str:
    conn = get_conn()
    pattern = f"{base_name}_复制%"
    rows = conn.execute("SELECT name FROM foods WHERE name=? OR name LIKE ?", (base_name, pattern)).fetchall()
    conn.close()
    existing = {r["name"] for r in rows}
    i = 1
    while f"{base_name}_复制{i}" in existing:
        i += 1
    return f"{base_name}_复制{i}"


def add_daily_log(log_date: str, meal: str, name: str, grams: float, macro: Dict[str, float]) -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO daily_logs(log_date, meal, name, grams, kcal, protein, carbs, fat, fiber, co2e_g, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            log_date, meal, name, grams,
            safe_float(macro.get("kcal")), safe_float(macro.get("protein")), safe_float(macro.get("carbs")),
            safe_float(macro.get("fat")), safe_float(macro.get("fiber")), safe_float(macro.get("co2e_g")), now_iso(),
        ),
    )
    conn.commit()
    conn.close()


def load_daily_logs(log_date: str) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT id, meal, name, grams, kcal, protein, carbs, fat, fiber FROM daily_logs WHERE log_date=? ORDER BY id",
        conn,
        params=(log_date,),
    )
    conn.close()
    return df


def delete_daily_log(log_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM daily_logs WHERE id=?", (int(log_id),))
    conn.commit()
    conn.close()


def clear_daily_logs(log_date: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM daily_logs WHERE log_date=?", (log_date,))
    conn.commit()
    conn.close()


def save_weight(weigh_date: str, weight_kg: float, note: str = "") -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO weights(weigh_date, weight_kg, note, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(weigh_date) DO UPDATE SET weight_kg=excluded.weight_kg, note=excluded.note, updated_at=excluded.updated_at
        """,
        (weigh_date, float(weight_kg), clean_text(note), now_iso(), now_iso()),
    )
    conn.commit()
    conn.close()


def load_weights(start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
    conn = get_conn()
    if start and end:
        df = pd.read_sql_query("SELECT weigh_date, weight_kg, note FROM weights WHERE weigh_date BETWEEN ? AND ? ORDER BY weigh_date", conn, params=(start, end))
    else:
        df = pd.read_sql_query("SELECT weigh_date, weight_kg, note FROM weights ORDER BY weigh_date", conn)
    conn.close()
    return df


def load_logs_range(start: str, end: str) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(
        """
        SELECT log_date, meal, name, grams, kcal, protein, carbs, fat, fiber
        FROM daily_logs
        WHERE log_date BETWEEN ? AND ?
        ORDER BY log_date, id
        """,
        conn,
        params=(start, end),
    )
    conn.close()
    return df


def save_menu_items(week_key: str, items: List[Dict[str, object]]) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM menu_items WHERE week_key=?", (week_key,))
    for item in items:
        conn.execute(
            """
            INSERT INTO menu_items(week_key, day, name, price, kcal, protein, carbs, fat, fiber, co2e_g, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                week_key, clean_text(item.get("day")), clean_text(item.get("name")), clean_text(item.get("price")),
                safe_float(item.get("kcal")), safe_float(item.get("protein")), safe_float(item.get("carbs")),
                safe_float(item.get("fat")), safe_float(item.get("fiber")), safe_float(item.get("co2e_g")), now_iso(), now_iso(),
            ),
        )
    conn.commit()
    conn.close()


def load_menu_items(week_key: str) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT id, day, name, price, kcal, protein, carbs, fat, fiber FROM menu_items WHERE week_key=? ORDER BY id",
        conn,
        params=(week_key,),
    )
    conn.close()
    return df


def get_menu_item(menu_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM menu_items WHERE id=?", (int(menu_id),)).fetchone()
    conn.close()
    return row


def clear_menu_week(week_key: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM menu_items WHERE week_key=?", (week_key,))
    conn.commit()
    conn.close()


def get_profile() -> Dict[str, str]:
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM user_profile").fetchall()
    conn.close()
    d = {r["key"]: r["value"] for r in rows}
    for k, v in USER_PROFILE_DEFAULT.items():
        d.setdefault(k, str(v))
    return d


def save_profile(d: Dict[str, object]) -> None:
    conn = get_conn()
    for k, v in d.items():
        conn.execute("INSERT OR REPLACE INTO user_profile(key, value) VALUES(?, ?)", (k, str(v)))
    conn.commit()
    conn.close()


# ----------------------------- Mensa 解析与估算 -----------------------------

def fetch_url_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MensaFatLossApp/4.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    for enc in ["utf-8", "latin-1"]:
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")


def strip_html_to_lines(html_or_text: str) -> List[str]:
    text = html_or_text or ""
    text = re.sub(r"(?is)<script.*?</script>", "\n", text)
    text = re.sub(r"(?is)<style.*?</style>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|td|th|h\d|section|article|a|span|button)>", "\n", text)
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
    s = clean_text(line)
    return any(s.startswith(d) for d in DAY_NAMES)


def looks_like_food_line(line: str) -> bool:
    s = clean_text(line)
    if len(s) < 5:
        return False
    if any(p.lower() in s.lower() for p in EXCLUDE_PHRASES):
        return False
    if PRICE_RE.search(s):
        return False
    if looks_like_day(s):
        return False
    if re.fullmatch(r"[\d\s,\.€]+", s):
        return False
    if len(s.split()) > 22:
        return False
    return True


def parse_menu_text(raw: str) -> List[Dict[str, object]]:
    lines = strip_html_to_lines(raw)
    items: List[Dict[str, object]] = []
    seen = set()
    current_day = "Unbekannt"

    for idx, line in enumerate(lines):
        if looks_like_day(line):
            current_day = line
            continue
        if PRICE_RE.search(line):
            price = PRICE_RE.search(line).group(1)
            name = ""
            # 通常菜名在价格前 1-8 行内。
            for j in range(idx - 1, max(-1, idx - 9), -1):
                cand = lines[j]
                if looks_like_food_line(cand):
                    name = cand
                    break
            if name:
                key = (current_day, name, price)
                if key not in seen:
                    seen.add(key)
                    macro = estimate_mensa_dish(name)
                    items.append({"day": current_day, "name": name, "price": price, **macro})
    return items


def estimate_mensa_dish(name: str) -> Dict[str, float]:
    """按菜名关键词拆成常见 Mensa 份量组件估算，比单纯类型归类更稳定。"""
    n = name.lower()
    parts: List[Dict[str, float]] = []

    def add(kcal=0, protein=0, carbs=0, fat=0, fiber=0, co2e_g=0):
        parts.append({"kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat, "fiber": fiber, "co2e_g": co2e_g})

    # 明确甜品/饮品先处理。
    if any(x in n for x in ["softeis", "eis", "kuchen", "dessert", "pudding"]):
        add(320, 6, 48, 11, 1, 350)
        return add_macros(parts)
    if any(x in n for x in ["milchgrieß", "grieß", "reisbrei"]):
        add(620, 17, 105, 15, 4, 550)
        return add_macros(parts)

    # 主食。
    if any(x in n for x in ["pasta", "nudel", "spaghetti", "makkaroni", "tortell", "lasagne", "spätzle", "gnocchi"]):
        add(380, 13, 75, 4, 4, 180)
    if any(x in n for x in ["reis", "basmati", "duftreis"]):
        add(300, 6, 65, 1, 1, 230)
    if any(x in n for x in ["kartoffel", "püree", "stampfkartoffeln"]):
        add(230, 6, 42, 5, 4, 90)
    if any(x in n for x in ["bratkartoffeln", "pommes", "wedges"]):
        add(420, 6, 55, 18, 5, 160)
    if any(x in n for x in ["baguette", "focaccia", "brötchen", "bun", "brot"]):
        add(230, 7, 42, 4, 3, 130)

    # 蛋白质和主菜。
    if any(x in n for x in ["hähnchen", "huhn", "pute", "chicken"]):
        add(250, 42, 0, 7, 0, 800)
    if any(x in n for x in ["rind", "beef", "gulasch", "leber"]):
        add(330, 36, 5, 18, 1, 2300)
    if any(x in n for x in ["schwein", "schweine", "schnitzel", "speck", "bacon"]):
        add(360, 32, 12, 22, 1, 1200)
    if any(x in n for x in ["seelachs", "fisch", "kabeljau", "forelle", "lachs"]):
        add(260, 36, 8, 9, 0, 1100)
    if any(x in n for x in ["halloumi"]):
        add(330, 22, 3, 26, 0, 700)
    if any(x in n for x in ["tofu", "soja", "brew bites", "vegan", "jackfruit"]):
        add(230, 22, 18, 8, 5, 350)
    if any(x in n for x in ["kichererb", "bohnen", "linsen", "chili sin"]):
        add(260, 18, 42, 4, 13, 250)
    if any(x in n for x in ["ei", "egg"]):
        add(150, 12, 2, 10, 0, 400)

    # 酱、奶制品和脂肪。
    if any(x in n for x in ["sahne", "cream", "rahm", "crème"]):
        add(180, 3, 8, 15, 0, 250)
    if any(x in n for x in ["käse", "cheese", "gouda", "mozzarella", "parmesan", "ricotta"]):
        add(160, 10, 2, 12, 0, 350)
    if any(x in n for x in ["pesto", "cashew"]):
        add(230, 5, 8, 20, 2, 250)
    if any(x in n for x in ["kokos", "curry"]):
        add(180, 3, 12, 13, 2, 170)
    if any(x in n for x in ["tomaten", "arrabbiata", "bolognese"]):
        add(120, 5, 15, 4, 4, 120)
    if any(x in n for x in ["frittiert", "frittierte", "paniert", "knusper", "teigtaschen"]):
        add(180, 4, 18, 10, 1, 120)

    # 蔬菜。
    veg_hits = ["gemüse", "salat", "spinat", "zucchini", "möhre", "karotte", "gurke", "rote bete", "erbsen", "pilz", "paprika", "rucola"]
    if any(x in n for x in veg_hits):
        add(120, 5, 18, 3, 7, 80)

    # 汉堡/披萨整体修正。
    if "burger" in n:
        add(260, 9, 30, 11, 3, 260)
    if "pizza" in n:
        add(520, 18, 65, 22, 4, 550)

    if not parts:
        # 无法识别时用中等 Mensa 份量兜底。
        add(680, 28, 82, 24, 6, 700)

    total = add_macros(parts)
    # Mensa 一份边界控制，避免关键词叠加过度。
    total["kcal"] = min(max(total["kcal"], 250), 1150)
    total["protein"] = min(max(total["protein"], 6), 65)
    total["carbs"] = min(max(total["carbs"], 10), 135)
    total["fat"] = min(max(total["fat"], 3), 60)
    total["fiber"] = min(max(total["fiber"], 0), 18)
    return total


# ----------------------------- 界面组件 -----------------------------

def sidebar_controls() -> Tuple[str, Dict[str, float]]:
    st.sidebar.header("当天设置")
    selected_date = st.sidebar.date_input("记录日期", value=date.today()).isoformat()
    preset_name = st.sidebar.selectbox("每日目标", list(TARGET_PRESETS.keys()), index=0)
    preset = TARGET_PRESETS[preset_name]
    if preset_name == "自定义":
        kcal = st.sidebar.number_input("目标 kcal", value=float(preset["kcal"]), step=50.0)
        protein = st.sidebar.number_input("目标蛋白 g", value=float(preset["protein"]), step=5.0)
        carbs = st.sidebar.number_input("目标碳水 g", value=float(preset["carbs"]), step=5.0)
        fat = st.sidebar.number_input("目标脂肪 g", value=float(preset["fat"]), step=5.0)
        target = {"kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat}
    else:
        target = dict(preset)
        st.sidebar.caption(f"{target['kcal']:.0f} kcal | P {target['protein']:.0f} / C {target['carbs']:.0f} / F {target['fat']:.0f}")
    return selected_date, target


def show_daily_summary(log_date: str, target: Dict[str, float]) -> Dict[str, float]:
    df = load_daily_logs(log_date)
    if df.empty:
        total = {k: 0.0 for k in DISPLAY_KEYS}
    else:
        total = {k: float(df[k].sum()) for k in DISPLAY_KEYS}
    remaining = {k: target.get(k, 0) - total.get(k, 0) for k in ["kcal", "protein", "carbs", "fat"]}

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("已摄入 kcal", f"{total['kcal']:.0f}", f"剩 {remaining['kcal']:.0f}")
    c2.metric("蛋白 g", f"{total['protein']:.1f}", f"剩 {remaining['protein']:.1f}")
    c3.metric("碳水 g", f"{total['carbs']:.1f}", f"剩 {remaining['carbs']:.1f}")
    c4.metric("脂肪 g", f"{total['fat']:.1f}", f"剩 {remaining['fat']:.1f}")
    return total


def tab_menu(log_date: str) -> None:
    st.subheader("1. 本周 Mensa 菜单")
    week_key = st.text_input("周标识", value=current_week_key(), help="例如 2026-KW28。保存后这一周可以反复选择。")

    with st.expander("导入菜单", expanded=True):
        url = st.text_input("Mensa URL", value=DEFAULT_URL)
        col1, col2 = st.columns([1, 1])
        if col1.button("从 URL 读取并估算", type="primary"):
            try:
                raw = fetch_url_text(url)
                items = parse_menu_text(raw)
                if not items:
                    st.warning("没有识别到菜品。可以尝试上传 HTML/TXT 或粘贴网页文本。")
                else:
                    save_menu_items(week_key, items)
                    st.success(f"已导入 {len(items)} 个菜品。")
                    st.rerun()
            except Exception as e:
                st.error(f"URL 读取失败：{e}")

        uploaded = st.file_uploader("上传网页 HTML/TXT", type=["html", "htm", "txt"])
        if uploaded is not None and col2.button("解析上传文件并估算"):
            raw = uploaded.read().decode("utf-8", errors="ignore")
            items = parse_menu_text(raw)
            save_menu_items(week_key, items)
            st.success(f"已导入 {len(items)} 个菜品。")
            st.rerun()

        pasted = st.text_area("或直接粘贴网页文本/HTML", height=120)
        if st.button("解析粘贴内容并估算"):
            items = parse_menu_text(pasted)
            save_menu_items(week_key, items)
            st.success(f"已导入 {len(items)} 个菜品。")
            st.rerun()

    menu_df = load_menu_items(week_key)
    if menu_df.empty:
        st.info("当前周还没有菜单。")
    else:
        show_df = menu_df.copy()
        show_df.insert(0, "加入", False)
        day_options = ["全部"] + list(dict.fromkeys(show_df["day"].tolist()))
        day_filter = st.selectbox("筛选日期", day_options)
        if day_filter != "全部":
            show_df = show_df[show_df["day"] == day_filter]
        edited = st.data_editor(
            show_df,
            hide_index=True,
            disabled=["id", "day", "name", "price", "kcal", "protein", "carbs", "fat", "fiber"],
            column_config={
                "id": st.column_config.NumberColumn("ID", width="small"),
                "加入": st.column_config.CheckboxColumn("加入", help="勾选后点击下方按钮加入当天记录"),
                "day": "日期", "name": "菜品", "price": "价格",
                "kcal": st.column_config.NumberColumn("kcal", format="%.0f"),
                "protein": st.column_config.NumberColumn("蛋白", format="%.1f"),
                "carbs": st.column_config.NumberColumn("碳水", format="%.1f"),
                "fat": st.column_config.NumberColumn("脂肪", format="%.1f"),
                "fiber": st.column_config.NumberColumn("纤维", format="%.1f"),
            },
            key=f"menu_editor_{week_key}_{day_filter}",
            use_container_width=True,
        )
        meal = st.selectbox("加入餐次", ["午餐", "早餐", "晚餐", "加餐"], index=0, key="menu_meal")
        if st.button("把勾选菜品加入当天记录"):
            selected = edited[edited["加入"] == True]
            if selected.empty:
                st.warning("没有勾选菜品。")
            else:
                for _, r in selected.iterrows():
                    row = get_menu_item(int(r["id"]))
                    if row:
                        add_daily_log(log_date, meal, row["name"], 1, macro_dict(row))
                st.success(f"已加入 {len(selected)} 个菜品到 {log_date}。")
                st.rerun()

        with st.expander("清空当前周菜单"):
            ok = st.checkbox("确认删除当前 week_key 的所有菜单", key="confirm_clear_menu")
            if st.button("删除当前周菜单", disabled=not ok):
                clear_menu_week(week_key)
                st.success("已删除当前周菜单。")
                st.rerun()


def tab_daily(log_date: str, target: Dict[str, float]) -> None:
    st.subheader("2. 每天记录")
    show_daily_summary(log_date, target)

    st.markdown("### 当前记录")
    df = load_daily_logs(log_date)
    if df.empty:
        st.info("当天还没有记录。")
    else:
        editable = df.copy()
        editable.insert(0, "删除", False)
        edited = st.data_editor(
            editable,
            hide_index=True,
            disabled=["id", "meal", "name", "grams", "kcal", "protein", "carbs", "fat", "fiber"],
            column_config={
                "删除": st.column_config.CheckboxColumn("删除"),
                "id": st.column_config.NumberColumn("ID", width="small"),
                "meal": "餐次", "name": "食物", "grams": st.column_config.NumberColumn("克数/份数", format="%.1f"),
                "kcal": st.column_config.NumberColumn("kcal", format="%.0f"),
                "protein": st.column_config.NumberColumn("蛋白", format="%.1f"),
                "carbs": st.column_config.NumberColumn("碳水", format="%.1f"),
                "fat": st.column_config.NumberColumn("脂肪", format="%.1f"),
                "fiber": st.column_config.NumberColumn("纤维", format="%.1f"),
            },
            key=f"daily_editor_{log_date}",
            use_container_width=True,
        )
        col_a, col_b = st.columns(2)
        if col_a.button("删除勾选记录"):
            selected = edited[edited["删除"] == True]
            for _, r in selected.iterrows():
                delete_daily_log(int(r["id"]))
            st.success(f"已删除 {len(selected)} 条记录。")
            st.rerun()
        with col_b:
            confirm = st.checkbox("确认清空当天", key="confirm_clear_day")
            if st.button("清空当天记录", disabled=not confirm):
                clear_daily_logs(log_date)
                st.success("已清空当天记录。")
                st.rerun()

    st.markdown("### 从食物库添加")
    q = st.text_input("搜索食物：支持名称 / 类别 / 拼音首字母", key="daily_food_search")
    foods = load_foods(q)
    if foods.empty:
        st.warning("没有匹配食物。可以到“食物数据库”新增。")
    else:
        food_names = [f"#{int(r.id)} {r.name} | {r.kcal:.0f} kcal/100g | P {r.protein:.1f}" for r in foods.itertuples()]
        choice = st.selectbox("选择食物", food_names)
        selected_id = int(re.search(r"#(\d+)", choice).group(1))
        food = get_food(selected_id)
        c1, c2, c3 = st.columns([1, 1, 1])
        meal = c1.selectbox("餐次", ["早餐", "午餐", "晚餐", "加餐"], index=2, key="manual_meal")
        grams = c2.number_input("克数", min_value=0.0, value=100.0, step=10.0)
        if food:
            macro = scale_per100(food, grams)
            c3.metric("估算", f"{macro['kcal']:.0f} kcal", f"P {macro['protein']:.1f}g")
            if st.button("添加选中食物到当天记录", type="primary"):
                add_daily_log(log_date, meal, food["name"], grams, macro)
                st.success("已添加。")
                st.rerun()

    st.markdown("### 蛋白不足补 40g 乳清")
    day_df = load_daily_logs(log_date)
    total_p = 0 if day_df.empty else float(day_df["protein"].sum())
    need_p = target["protein"] - total_p
    st.caption(f"当前蛋白 {total_p:.1f}g，目标 {target['protein']:.1f}g，还差 {need_p:.1f}g。")
    if st.button("如果蛋白不足，加入 40g 乳清蛋白粉"):
        whey_df = load_foods("rqdbf")
        if whey_df.empty:
            st.error("食物库里没有乳清蛋白粉。请先添加。")
        elif need_p <= 0:
            st.info("当前蛋白已经达标，未添加乳清。")
        else:
            whey_id = int(whey_df.iloc[0]["id"])
            whey = get_food(whey_id)
            macro = scale_per100(whey, 40)
            add_daily_log(log_date, "加餐", "乳清蛋白粉 40g", 40, macro)
            st.success("已加入 40g 乳清蛋白粉。")
            st.rerun()

    if not df.empty:
        csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("导出当天记录 CSV", csv_bytes, file_name=f"daily_{log_date}.csv", mime="text/csv")


def tab_food_db() -> None:
    st.subheader("3. 食物数据库：手动添加 / 直接编辑 / 复制 / 删除")
    st.info("这版不再使用联网搜索。ID 是数据库主键，只读；名称和营养值可以直接改。默认基础食物也可以删除，删除后不会自动恢复。")

    st.markdown("### 新增食物")
    with st.form("add_food_form", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 1, 1])
        name = c1.text_input("名称")
        category = c2.text_input("类别", value="custom")
        initials = c3.text_input("拼音首字母/检索码")
        n1, n2, n3, n4, n5 = st.columns(5)
        kcal = n1.number_input("kcal/100g", min_value=0.0, value=0.0, step=1.0)
        protein = n2.number_input("蛋白/100g", min_value=0.0, value=0.0, step=0.1)
        carbs = n3.number_input("碳水/100g", min_value=0.0, value=0.0, step=0.1)
        fat = n4.number_input("脂肪/100g", min_value=0.0, value=0.0, step=0.1)
        fiber = n5.number_input("纤维/100g", min_value=0.0, value=0.0, step=0.1)
        submitted = st.form_submit_button("添加到食物库", type="primary")
        if submitted:
            if not clean_text(name):
                st.warning("名称不能为空。")
            else:
                new_id = insert_food({"name": name, "kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat, "fiber": fiber, "category": category, "initials": initials})
                st.success(f"已添加：#{new_id} {name}")
                st.rerun()

    st.markdown("### 编辑已有食物")
    q = st.text_input("搜索", key="food_db_search", placeholder="例如 sjxr、鸡胸、protein、ymp")
    df = load_foods(q)
    if df.empty:
        st.warning("没有匹配食物。")
    else:
        edit_df = df.copy()
        edit_df["复制"] = False
        edit_df["删除"] = False
        edited = st.data_editor(
            edit_df,
            hide_index=True,
            disabled=["id"],
            use_container_width=True,
            num_rows="fixed",
            key=f"food_db_editor_{q}",
            column_config={
                "id": st.column_config.NumberColumn("ID", help="固定数据库 ID，只读；不会因为排序或编辑改变。", width="small"),
                "name": st.column_config.TextColumn("名称", required=True),
                "kcal": st.column_config.NumberColumn("kcal/100g", min_value=0.0, format="%.1f"),
                "protein": st.column_config.NumberColumn("蛋白/100g", min_value=0.0, format="%.1f"),
                "carbs": st.column_config.NumberColumn("碳水/100g", min_value=0.0, format="%.1f"),
                "fat": st.column_config.NumberColumn("脂肪/100g", min_value=0.0, format="%.1f"),
                "fiber": st.column_config.NumberColumn("纤维/100g", min_value=0.0, format="%.1f"),
                "category": st.column_config.TextColumn("类别"),
                "initials": st.column_config.TextColumn("检索码"),
                "复制": st.column_config.CheckboxColumn("复制"),
                "删除": st.column_config.CheckboxColumn("删除"),
            },
        )
        st.caption("保存规则：先按 ID 更新名称和营养值；勾选“复制”会插入一条新记录；勾选“删除”会直接删除该 ID。")
        if st.button("保存表格修改 / 复制 / 删除", type="primary"):
            deleted = updated = copied = 0
            for _, row in edited.iterrows():
                fid = safe_int(row.get("id"))
                if fid is None:
                    continue
                if bool(row.get("删除", False)):
                    delete_food(fid)
                    deleted += 1
                    continue
                data = {
                    "name": row.get("name"),
                    "kcal": row.get("kcal"),
                    "protein": row.get("protein"),
                    "carbs": row.get("carbs"),
                    "fat": row.get("fat"),
                    "fiber": row.get("fiber"),
                    "category": row.get("category"),
                    "initials": row.get("initials"),
                    "co2e_g": 0,
                }
                update_food(fid, data)
                updated += 1
                if bool(row.get("复制", False)):
                    duplicate_food(fid, data)
                    copied += 1
            st.success(f"保存完成：更新 {updated} 条，复制 {copied} 条，删除 {deleted} 条。")
            st.rerun()

        csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("导出当前食物库 CSV", csv_bytes, file_name="food_database.csv", mime="text/csv")

    st.markdown("### CSV 批量导入")
    st.caption("支持列名：name, kcal, protein/P, carbs/C, fat/F, fiber, category, initials。不会自动覆盖已有记录，而是新增。")
    up = st.file_uploader("上传食物 CSV", type=["csv"], key="food_csv_upload")
    if up is not None and st.button("导入 CSV 为新食物"):
        raw = up.read()
        text = raw.decode("utf-8-sig", errors="ignore")
        sample = text[:2048]
        delimiter = "\t" if "\t" in sample else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        count = 0
        for r in reader:
            name = r.get("name") or r.get("名称") or r.get("食物")
            if not clean_text(name):
                continue
            data = {
                "name": name,
                "kcal": r.get("kcal") or r.get("kcal/100g") or r.get("热量"),
                "protein": r.get("protein") or r.get("P") or r.get("蛋白"),
                "carbs": r.get("carbs") or r.get("C") or r.get("碳水"),
                "fat": r.get("fat") or r.get("F") or r.get("脂肪"),
                "fiber": r.get("fiber") or r.get("纤维"),
                "category": r.get("category") or r.get("类别") or "custom",
                "initials": r.get("initials") or r.get("检索码") or "",
                "co2e_g": 0,
            }
            insert_food(data)
            count += 1
        st.success(f"已导入 {count} 条食物。")
        st.rerun()

    st.markdown("### 危险操作")
    c1, c2 = st.columns(2)
    with c1:
        confirm_clear = st.checkbox("确认清空全部食物库", key="confirm_clear_foods")
        if st.button("清空食物库，不自动恢复", disabled=not confirm_clear):
            conn = get_conn()
            conn.execute("DELETE FROM foods")
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('foods_seeded_v4', '1')")
            conn.commit()
            conn.close()
            st.success("已清空。下次启动也不会自动恢复默认食物。")
            st.rerun()
    with c2:
        confirm_reset = st.checkbox("确认恢复默认食物库", key="confirm_reset_foods")
        if st.button("清空并恢复默认食物", disabled=not confirm_reset):
            reset_default_foods()
            st.success("已恢复默认食物库。")
            st.rerun()


def tab_history(target: Dict[str, float]) -> None:
    st.subheader("4. 历史 / 周月总结 / 体重")
    today = date.today()
    start_default = today - timedelta(days=6)
    c1, c2 = st.columns(2)
    start = c1.date_input("开始日期", value=start_default, key="hist_start").isoformat()
    end = c2.date_input("结束日期", value=today, key="hist_end").isoformat()

    logs = load_logs_range(start, end)
    if logs.empty:
        st.info("这个日期范围内还没有饮食记录。")
    else:
        daily = logs.groupby("log_date", as_index=False)[DISPLAY_KEYS].sum()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("平均 kcal", f"{daily['kcal'].mean():.0f}")
        c2.metric("平均蛋白", f"{daily['protein'].mean():.1f}g")
        c3.metric("平均碳水", f"{daily['carbs'].mean():.1f}g")
        c4.metric("平均脂肪", f"{daily['fat'].mean():.1f}g")
        st.line_chart(daily.set_index("log_date")[["kcal", "protein", "carbs", "fat"]])
        st.dataframe(daily, use_container_width=True)
        st.download_button("导出汇总 CSV", daily.to_csv(index=False).encode("utf-8-sig"), file_name="summary.csv", mime="text/csv")

    st.markdown("### 每周体重记录")
    with st.form("weight_form"):
        w_date = st.date_input("称重日期", value=today)
        profile = get_profile()
        default_w = safe_float(profile.get("current_weight_kg"), 91.0)
        weight = st.number_input("体重 kg", min_value=30.0, max_value=200.0, value=default_w, step=0.1)
        note = st.text_input("备注", placeholder="例如：晨起空腹、训练后、水肿明显等")
        if st.form_submit_button("保存/更新体重"):
            save_weight(w_date.isoformat(), weight, note)
            save_profile({"current_weight_kg": weight})
            st.success("体重已保存。")
            st.rerun()

    weights = load_weights(start, end)
    if not weights.empty:
        st.line_chart(weights.set_index("weigh_date")[["weight_kg"]])
        st.dataframe(weights, use_container_width=True)

    st.markdown("### 摄入调整建议")
    advice = build_advice(load_logs_range(start, end), load_weights(start, end), target)
    st.write(advice)


def build_advice(logs: pd.DataFrame, weights: pd.DataFrame, target: Dict[str, float]) -> str:
    lines = []
    if logs.empty:
        lines.append("当前饮食记录不足，先连续记录至少 7 天再判断摄入是否需要调整。")
    else:
        daily = logs.groupby("log_date", as_index=False)[DISPLAY_KEYS].sum()
        avg_kcal = float(daily["kcal"].mean())
        avg_p = float(daily["protein"].mean())
        lines.append(f"最近记录日平均摄入约 {avg_kcal:.0f} kcal，蛋白 {avg_p:.0f} g。")
        if avg_p < target["protein"] - 20:
            lines.append("蛋白明显偏低：优先把蛋白补到 180–190 g，再考虑调整碳水和脂肪。")
        elif avg_p < target["protein"] - 5:
            lines.append("蛋白略低：可用 40 g 乳清、Magerquark、鸡胸或虾仁补足。")
        else:
            lines.append("蛋白基本达标。")

    if weights is None or weights.empty or len(weights) < 2:
        lines.append("体重记录少于 2 次，暂时无法判断下降速度。建议每周固定 1 次晨起空腹称重。")
    else:
        w = weights.sort_values("weigh_date")
        first = float(w.iloc[0]["weight_kg"])
        last = float(w.iloc[-1]["weight_kg"])
        days = max(1, (pd.to_datetime(w.iloc[-1]["weigh_date"]) - pd.to_datetime(w.iloc[0]["weigh_date"])).days)
        weekly_change = (last - first) / days * 7
        lines.append(f"区间体重变化约 {weekly_change:+.2f} kg/周。")
        if weekly_change > -0.25:
            lines.append("下降偏慢：如果记录准确，可以把每日平均热量下调 150–200 kcal，优先减少油脂、甜品、薯条/炸物。")
        elif weekly_change < -1.0:
            lines.append("下降偏快：若训练状态下降或饥饿明显，训练日增加 100–150 kcal，优先加碳水。")
        else:
            lines.append("下降速度合理：保持当前目标，继续观察 1–2 周。")
    return "\n\n".join(lines)


def tab_profile() -> None:
    st.subheader("5. 身体数据与计划")
    profile = get_profile()
    with st.form("profile_form"):
        c1, c2, c3, c4 = st.columns(4)
        height = c1.number_input("身高 cm", value=safe_float(profile.get("height_cm"), 176), step=1.0)
        age = c2.number_input("年龄", value=safe_float(profile.get("age"), 30), step=1.0)
        current_w = c3.number_input("当前体重 kg", value=safe_float(profile.get("current_weight_kg"), 91), step=0.1)
        target_w = c4.number_input("目标体重 kg", value=safe_float(profile.get("target_weight_kg"), 85), step=0.1)
        note = st.text_area("训练计划/备注", value=profile.get("training_note", USER_PROFILE_DEFAULT["training_note"]))
        if st.form_submit_button("保存身体数据"):
            save_profile({
                "height_cm": height,
                "age": age,
                "current_weight_kg": current_w,
                "target_weight_kg": target_w,
                "training_note": note,
            })
            st.success("已保存。")
            st.rerun()

    st.markdown("### 当前默认摄入目标")
    st.table(pd.DataFrame([
        {"类型": "训练日", "kcal": 2300, "蛋白": 190, "碳水": 210, "脂肪": 62},
        {"类型": "休息日", "kcal": 2000, "蛋白": 190, "碳水": 120, "脂肪": 68},
        {"类型": "高消耗日", "kcal": 2500, "蛋白": 190, "碳水": 260, "脂肪": 65},
    ]))


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    init_db()
    st.title(APP_TITLE)
    st.caption("v4 手动数据库版：无联网搜索；食物库支持稳定 ID 下的直接编辑、复制、删除和新增。")
    log_date, target = sidebar_controls()

    tabs = st.tabs(["本周 Mensa 菜单", "每天记录", "食物数据库", "历史/周月总结", "身体数据/计划"])
    with tabs[0]:
        tab_menu(log_date)
    with tabs[1]:
        tab_daily(log_date, target)
    with tabs[2]:
        tab_food_db()
    with tabs[3]:
        tab_history(target)
    with tabs[4]:
        tab_profile()


if __name__ == "__main__":
    main()
