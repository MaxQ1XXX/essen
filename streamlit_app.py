# -*- coding: utf-8 -*-
"""
Alte Mensa Dresden 减脂/营养/CO2e 记录器 - Streamlit 网页版

运行：
    pip install -r requirements.txt
    streamlit run streamlit_app.py

功能：
- 导入 Alte Mensa URL / HTML / 粘贴文本
- 本地规则估算 kcal、蛋白、碳水、脂肪、纤维、CO2e
- 可选用 OpenAI API/ChatGPT 重新估算整周菜单
- 保存本周菜单、每天记录、食物数据库
- 食物搜索支持中文/英文/德文/拼音首字母
- 蛋白不足时可一键加入 40g 乳清蛋白粉
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import streamlit as st

APP_TITLE = "Alte Mensa 减脂营养记录器"
DEFAULT_URL = "https://www.studentenwerk-dresden.de/mensen/speiseplan/alte-mensa.html"
DEFAULT_MODEL = "gpt-4.1-mini"
DB_PATH = Path(__file__).with_name("mensa_streamlit.sqlite3")
MACRO_KEYS = ["kcal", "protein", "carbs", "fat", "fiber", "co2e_g"]

TARGET_PRESETS = {
    "训练日 2300 / P190 C210 F62": {"kcal": 2300.0, "protein": 190.0, "carbs": 210.0, "fat": 62.0},
    "休息日 2000 / P190 C120 F68": {"kcal": 2000.0, "protein": 190.0, "carbs": 120.0, "fat": 68.0},
    "高消耗 2500 / P190 C260 F65": {"kcal": 2500.0, "protein": 190.0, "carbs": 260.0, "fat": 65.0},
    "自定义": {"kcal": 2300.0, "protein": 190.0, "carbs": 210.0, "fat": 62.0},
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

DEFAULT_FOODS = {
    "干意面": {"kcal": 353, "protein": 13, "carbs": 72, "fat": 1.5, "fiber": 3, "co2e_g": 150, "category": "carb", "initials": "gym"},
    "干米": {"kcal": 360, "protein": 7, "carbs": 80, "fat": 0.7, "fiber": 1.3, "co2e_g": 270, "category": "carb", "initials": "gm"},
    "熟米饭": {"kcal": 130, "protein": 2.7, "carbs": 28, "fat": 0.3, "fiber": 0.4, "co2e_g": 100, "category": "carb", "initials": "smf"},
    "土豆/生重": {"kcal": 77, "protein": 2, "carbs": 17, "fat": 0.1, "fiber": 2.2, "co2e_g": 35, "category": "carb", "initials": "td"},
    "燕麦片": {"kcal": 370, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10, "co2e_g": 90, "category": "carb", "initials": "ymp"},
    "生鸡胸肉": {"kcal": 110, "protein": 23, "carbs": 0, "fat": 1.5, "fiber": 0, "co2e_g": 560, "category": "protein", "initials": "sjxr"},
    "鸡腿肉去皮/生重": {"kcal": 125, "protein": 20, "carbs": 0, "fat": 5, "fiber": 0, "co2e_g": 560, "category": "protein", "initials": "jtr"},
    "瘦牛肉/约5%脂肪": {"kcal": 137, "protein": 21, "carbs": 0, "fat": 5, "fiber": 0, "co2e_g": 2700, "category": "protein", "initials": "snr"},
    "牛肉末/约10%脂肪": {"kcal": 176, "protein": 20, "carbs": 0, "fat": 10, "fiber": 0, "co2e_g": 2700, "category": "protein", "initials": "nrm"},
    "三文鱼": {"kcal": 208, "protein": 20, "carbs": 0, "fat": 13, "fiber": 0, "co2e_g": 600, "category": "protein_fat", "initials": "swy"},
    "虾仁": {"kcal": 99, "protein": 24, "carbs": 0.2, "fat": 0.3, "fiber": 0, "co2e_g": 1000, "category": "protein", "initials": "xr"},
    "全蛋": {"kcal": 143, "protein": 12.6, "carbs": 0.7, "fat": 9.5, "fiber": 0, "co2e_g": 450, "category": "protein_fat", "initials": "qd"},
    "蛋清": {"kcal": 52, "protein": 11, "carbs": 0.7, "fat": 0.2, "fiber": 0, "co2e_g": 160, "category": "protein", "initials": "dq"},
    "Magerquark": {"kcal": 67, "protein": 12, "carbs": 4, "fat": 0.2, "fiber": 0, "co2e_g": 180, "category": "protein", "initials": "mq"},
    "Skyr natur": {"kcal": 63, "protein": 11, "carbs": 4, "fat": 0.2, "fiber": 0, "co2e_g": 180, "category": "protein", "initials": "skyr"},
    "乳清蛋白粉": {"kcal": 390, "protein": 78, "carbs": 8, "fat": 6, "fiber": 0, "co2e_g": 350, "category": "protein", "initials": "rqdbf"},
    "西兰花/蔬菜": {"kcal": 34, "protein": 2.8, "carbs": 7, "fat": 0.4, "fiber": 3, "co2e_g": 45, "category": "veg", "initials": "xlh"},
    "番茄/黄瓜/沙拉菜": {"kcal": 20, "protein": 1, "carbs": 4, "fat": 0.2, "fiber": 1.3, "co2e_g": 40, "category": "veg", "initials": "fqhgslc"},
    "橄榄油": {"kcal": 884, "protein": 0, "carbs": 0, "fat": 100, "fiber": 0, "co2e_g": 530, "category": "fat", "initials": "gly"},
    "花生酱": {"kcal": 588, "protein": 25, "carbs": 20, "fat": 50, "fiber": 6, "co2e_g": 320, "category": "fat", "initials": "hsj"},
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
    s = s.replace("\xa0", " ").replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


def safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
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

        price = ""
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
    conn.commit()
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


def get_record_rows(conn: sqlite3.Connection, record_date: str) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM daily_records WHERE record_date=? ORDER BY id", (record_date,)).fetchall()


def add_record(conn: sqlite3.Connection, record_date: str, meal: str, name: str, grams: float, macro: Dict[str, float], source: str) -> None:
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


def get_foods(conn: sqlite3.Connection, query: str = "") -> List[sqlite3.Row]:
    q = clean_text(query).lower()
    if not q:
        return conn.execute("SELECT * FROM foods ORDER BY name").fetchall()
    like = f"%{q}%"
    return conn.execute(
        """
        SELECT * FROM foods
        WHERE lower(name) LIKE ? OR lower(category) LIKE ? OR lower(initials) LIKE ?
        ORDER BY name
        """,
        (like, like, like),
    ).fetchall()


def upsert_food(conn: sqlite3.Connection, values: Dict[str, object]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO foods (name, kcal, protein, carbs, fat, fiber, co2e_g, category, initials, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            kcal=excluded.kcal, protein=excluded.protein, carbs=excluded.carbs, fat=excluded.fat,
            fiber=excluded.fiber, co2e_g=excluded.co2e_g, category=excluded.category,
            initials=excluded.initials, updated_at=excluded.updated_at
        """,
        (values["name"], values["kcal"], values["protein"], values["carbs"], values["fat"], values["fiber"], values["co2e_g"], values["category"], values["initials"], now),
    )
    conn.commit()


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
            "Estimate nutrition and climate footprint for Mensa dishes. Return JSON only. "
            "Use one normal cafeteria portion. Units: kcal, grams protein/carbs/fat/fiber, grams CO2e. "
            "Do not include markdown. Keep existing day/name/price."
        ),
        "schema": [
            {
                "day": "string",
                "name": "string",
                "price": "string",
                "kcal": "number",
                "protein": "number",
                "carbs": "number",
                "fat": "number",
                "fiber": "number",
                "co2e_g": "number",
                "source": "ChatGPT估算"
            }
        ],
        "dishes": [
            {"day": r.day, "name": r.name, "price": r.price, "local_estimate": macro_from_obj(r)} for r in rows
        ],
    }
    payload = json.dumps({"model": model, "input": json.dumps(prompt, ensure_ascii=False)}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
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
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    data = [
        ("kcal", "热量"), ("protein", "蛋白 g"), ("carbs", "碳水 g"),
        ("fat", "脂肪 g"), ("fiber", "纤维 g"), ("co2e_g", "CO₂e g"),
    ]
    cols = [c1, c2, c3, c4, c5, c6]
    for col, (key, label) in zip(cols, data):
        if target and key in target:
            delta = total.get(key, 0) - target[key]
            col.metric(label, f"{total.get(key, 0):.0f}", f"{delta:+.0f}")
        else:
            col.metric(label, f"{total.get(key, 0):.0f}")


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🍽️", layout="wide")
    st.title(APP_TITLE)
    st.caption("Streamlit 网页版：导入本周菜单 → 估算营养/CO₂e → 加入每天记录。")

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
        st.caption("留空时只使用本地规则估算。部署到 Streamlit Cloud 时建议放在 Secrets。")

    tab_import, tab_week, tab_day, tab_food = st.tabs(["1 导入菜单", "2 本周菜单", "3 每天记录", "4 食物数据库"])

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
                st.dataframe([
                    {"day": r.day, "name": r.name, "price": r.price, **macro_from_obj(r), "source": r.source}
                    for r in local_rows
                ], use_container_width=True)

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
            menu_csv = rows_to_csv(menu_rows, ["day", "name", "price", *MACRO_KEYS, "source"])
            st.download_button("导出本周菜单 CSV", menu_csv, file_name=f"mensa_menu_{week_key}.csv", mime="text/csv")
            day_filter = st.selectbox("按日期筛选", ["全部"] + sorted({r["day"] for r in menu_rows}))
            rows_show = [r for r in menu_rows if day_filter == "全部" or r["day"] == day_filter]
            for r in rows_show:
                with st.container(border=True):
                    top_cols = st.columns([3, 1, 1, 1, 1, 1, 1, 1])
                    top_cols[0].markdown(f"**{r['name']}**  \n{r['day']} · {r['price']} · {r['source']}")
                    top_cols[1].metric("kcal", f"{r['kcal']:.0f}")
                    top_cols[2].metric("P", f"{r['protein']:.0f}")
                    top_cols[3].metric("C", f"{r['carbs']:.0f}")
                    top_cols[4].metric("F", f"{r['fat']:.0f}")
                    top_cols[5].metric("Fiber", f"{r['fiber']:.0f}")
                    top_cols[6].metric("CO₂e", f"{r['co2e_g']:.0f}")
                    with top_cols[7]:
                        meal = st.selectbox("餐次", ["午餐", "晚餐", "早餐", "加餐"], key=f"meal_menu_{r['id']}")
                        if st.button("加入当天", key=f"add_menu_{r['id']}"):
                            add_record(conn, record_date, meal, r["name"], 1.0, macro_from_obj(r), f"Mensa/{week_key}")
                            st.success("已加入当天记录。")
                            rerun()

    with tab_day:
        st.subheader(f"每天记录：{record_date}")
        records = get_record_rows(conn, record_date)
        total = total_for_records(records)
        render_metric_row(total, target)
        if records:
            rec_csv = rows_to_csv(records, ["record_date", "meal", "name", "grams", *MACRO_KEYS, "source"])
            st.download_button("导出当天记录 CSV", rec_csv, file_name=f"daily_record_{record_date}.csv", mime="text/csv")
            for r in records:
                cols = st.columns([1, 3, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8])
                cols[0].write(r["meal"])
                cols[1].write(r["name"])
                cols[2].write(f"{r['grams']:.0f}" if r["grams"] else "-")
                cols[3].write(f"{r['kcal']:.0f}")
                cols[4].write(f"P {r['protein']:.0f}")
                cols[5].write(f"C {r['carbs']:.0f}")
                cols[6].write(f"F {r['fat']:.0f}")
                cols[7].write(f"CO₂e {r['co2e_g']:.0f}")
                if cols[8].button("删除", key=f"del_rec_{r['id']}"):
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
                add_record(conn, record_date, "加餐", "乳清蛋白粉", 40, macro, "食物数据库/40g")
                st.success("已加入 40g 乳清蛋白粉。")
                rerun()

        st.divider()
        st.subheader("从食物数据库添加")
        q = st.text_input("搜索食物：中文/英文/德文/拼音首字母", placeholder="例如 sjxr, ymp, rqdbf, Magerquark")
        foods = get_foods(conn, q)
        meal_food = st.selectbox("餐次", ["晚餐", "早餐", "午餐", "加餐"], key="meal_food")
        grams = st.number_input("克数", min_value=0.0, value=100.0, step=10.0)
        for f in foods[:30]:
            cols = st.columns([3, 1, 1, 1, 1, 1, 1])
            cols[0].write(f"**{f['name']}** · {f['category']} · {f['initials']}")
            macro = scale_food(f, grams)
            cols[1].write(f"{macro['kcal']:.0f} kcal")
            cols[2].write(f"P {macro['protein']:.0f}")
            cols[3].write(f"C {macro['carbs']:.0f}")
            cols[4].write(f"F {macro['fat']:.0f}")
            cols[5].write(f"CO₂e {macro['co2e_g']:.0f}")
            if cols[6].button("添加", key=f"add_food_{f['id']}"):
                add_record(conn, record_date, meal_food, f["name"], grams, macro, "食物数据库")
                st.success("已添加。")
                rerun()

    with tab_food:
        st.subheader("食物数据库")
        q2 = st.text_input("搜索数据库", key="db_search")
        foods = get_foods(conn, q2)
        st.dataframe([
            {"name": f["name"], "kcal/100g": f["kcal"], "P": f["protein"], "C": f["carbs"], "F": f["fat"], "fiber": f["fiber"], "CO₂e/100g": f["co2e_g"], "category": f["category"], "initials": f["initials"]}
            for f in foods
        ], use_container_width=True)

        st.divider()
        st.subheader("添加或更新食物，单位：每 100g")
        with st.form("food_form"):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("名称")
            category = c2.text_input("类别", value="protein/carb/fat/veg/custom")
            initials = c3.text_input("拼音首字母/缩写")
            c4, c5, c6, c7, c8, c9 = st.columns(6)
            kcal = c4.number_input("kcal", value=100.0)
            protein = c5.number_input("蛋白", value=0.0)
            carbs = c6.number_input("碳水", value=0.0)
            fat = c7.number_input("脂肪", value=0.0)
            fiber = c8.number_input("纤维", value=0.0)
            co2e_g = c9.number_input("CO₂e g", value=0.0)
            submitted = st.form_submit_button("保存食物")
            if submitted:
                if not clean_text(name):
                    st.error("名称不能为空。")
                else:
                    upsert_food(conn, {
                        "name": clean_text(name), "kcal": kcal, "protein": protein, "carbs": carbs,
                        "fat": fat, "fiber": fiber, "co2e_g": co2e_g,
                        "category": clean_text(category), "initials": clean_text(initials).lower(),
                    })
                    st.success("食物已保存。")
                    rerun()

    st.caption("提示：Mensa 菜品没有固定称重，营养/CO₂e 是估算值。减脂期建议对奶油酱、炸物、汉堡、披萨偏高估。")


if __name__ == "__main__":
    main()
