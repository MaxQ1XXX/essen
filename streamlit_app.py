# -*- coding: utf-8 -*-
"""
Alte Mensa 减脂营养记录器 - Streamlit v7

核心逻辑：
1. 食物数据库完全本地手动维护，不联网。
2. 食堂菜单可以导入 ChatGPT 已估算好的 CSV/JSON。
3. 食物库支持原表格内直接修改；修改后自动保存到 SQLite。
4. 食物库可导出为 foods_master.csv，放到 GitHub 后作为下次部署/重启的种子数据。

运行：
    pip install -r requirements.txt
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import streamlit as st

APP_TITLE = "Alte Mensa 减脂营养记录器"
APP_VERSION = "v7 食物库自动保存 + GitHub seed CSV"
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "mensa_streamlit.sqlite3"
FOODS_SEED_PATH = BASE_DIR / "foods_master.csv"
MACRO_KEYS = ["kcal", "protein", "carbs", "fat", "fiber"]

TARGET_PRESETS = {
    "训练日 2300 / P190 C210 F62": {"kcal": 2300.0, "protein": 190.0, "carbs": 210.0, "fat": 62.0},
    "休息日 2000 / P190 C120 F68": {"kcal": 2000.0, "protein": 190.0, "carbs": 120.0, "fat": 68.0},
    "高消耗 2500 / P190 C260 F65": {"kcal": 2500.0, "protein": 190.0, "carbs": 260.0, "fat": 65.0},
    "自定义": {"kcal": 2300.0, "protein": 190.0, "carbs": 210.0, "fat": 62.0},
}

DEFAULT_FOODS: List[Dict[str, object]] = [
    {"name": "乳清蛋白粉", "kcal": 390, "protein": 78, "carbs": 8, "fat": 6, "fiber": 0, "category": "protein", "initials": "rqdbf"},
    {"name": "全蛋", "kcal": 143, "protein": 12.6, "carbs": 0.7, "fat": 9.5, "fiber": 0, "category": "protein_fat", "initials": "qd"},
    {"name": "蛋清", "kcal": 52, "protein": 11, "carbs": 0.7, "fat": 0.2, "fiber": 0, "category": "protein", "initials": "dq"},
    {"name": "农夫面包 Bauernbrot", "kcal": 240, "protein": 7.8, "carbs": 47, "fat": 1.7, "fiber": 5.5, "category": "carb", "initials": "nfmb"},
    {"name": "干意面", "kcal": 353, "protein": 13, "carbs": 72, "fat": 1.5, "fiber": 3, "category": "carb", "initials": "gym"},
    {"name": "干米", "kcal": 360, "protein": 7, "carbs": 80, "fat": 0.7, "fiber": 1.3, "category": "carb", "initials": "gm"},
    {"name": "熟米饭", "kcal": 130, "protein": 2.7, "carbs": 28.2, "fat": 0.3, "fiber": 0.4, "category": "carb", "initials": "smf"},
    {"name": "土豆/生重", "kcal": 77, "protein": 2, "carbs": 17, "fat": 0.1, "fiber": 2.2, "category": "carb", "initials": "td"},
    {"name": "燕麦片", "kcal": 370, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10, "category": "carb", "initials": "ymp"},
    {"name": "橄榄油", "kcal": 884, "protein": 0, "carbs": 0, "fat": 100, "fiber": 0, "category": "fat", "initials": "gly"},
    {"name": "洋葱", "kcal": 40, "protein": 1.1, "carbs": 9.3, "fat": 0.1, "fiber": 1.7, "category": "veg", "initials": "yc"},
    {"name": "西红柿", "kcal": 18, "protein": 0.9, "carbs": 3.9, "fat": 0.2, "fiber": 1.2, "category": "veg", "initials": "xhs"},
    {"name": "番茄/黄瓜/沙拉菜", "kcal": 20, "protein": 1, "carbs": 4, "fat": 0.2, "fiber": 1.3, "category": "veg", "initials": "fqhgslc"},
    {"name": "白菜/生菜/菠菜", "kcal": 20, "protein": 1.7, "carbs": 2.5, "fat": 0.3, "fiber": 2.0, "category": "veg", "initials": "bcsc"},
    {"name": "西兰花/蔬菜", "kcal": 34, "protein": 2.8, "carbs": 7, "fat": 0.4, "fiber": 3, "category": "veg", "initials": "xlh"},
    {"name": "西瓜", "kcal": 30, "protein": 0.6, "carbs": 7.6, "fat": 0.2, "fiber": 0.4, "category": "fruit", "initials": "xg"},
    {"name": "生鸡胸肉", "kcal": 120, "protein": 22.5, "carbs": 0, "fat": 2.6, "fiber": 0, "category": "protein", "initials": "sjxr"},
    {"name": "鸡腿肉去皮/生重", "kcal": 119, "protein": 19.5, "carbs": 0, "fat": 4.5, "fiber": 0, "category": "protein", "initials": "jtr"},
    {"name": "瘦牛肉/约5%脂肪", "kcal": 137, "protein": 21, "carbs": 0, "fat": 5, "fiber": 0, "category": "protein", "initials": "snr"},
    {"name": "牛肉末/约10%脂肪", "kcal": 176, "protein": 20, "carbs": 0, "fat": 10, "fiber": 0, "category": "protein", "initials": "nrm"},
    {"name": "牛肉馅/约20%脂肪", "kcal": 254, "protein": 17.2, "carbs": 0, "fat": 20, "fiber": 0, "category": "protein_fat", "initials": "nrx"},
    {"name": "牛肋排", "kcal": 291, "protein": 16.8, "carbs": 0, "fat": 25.2, "fiber": 0, "category": "protein_fat", "initials": "nlp"},
    {"name": "五香牛腱/熟肉", "kcal": 180, "protein": 29, "carbs": 1.5, "fat": 7, "fiber": 0, "category": "protein", "initials": "wxnj"},
    {"name": "三文鱼", "kcal": 208, "protein": 20, "carbs": 0, "fat": 13, "fiber": 0, "category": "protein_fat", "initials": "swy"},
    {"name": "虾仁", "kcal": 99, "protein": 24, "carbs": 0.2, "fat": 0.3, "fiber": 0, "category": "protein", "initials": "xr"},
    {"name": "Magerquark", "kcal": 67, "protein": 12, "carbs": 4, "fat": 0.2, "fiber": 0, "category": "protein", "initials": "mq"},
    {"name": "Skyr natur", "kcal": 63, "protein": 11, "carbs": 4, "fat": 0.2, "fiber": 0, "category": "protein", "initials": "skyr"},
    {"name": "花生酱", "kcal": 588, "protein": 25, "carbs": 20, "fat": 50, "fiber": 6, "category": "fat", "initials": "hsj"},
]

FIELD_ALIASES = {
    "kcal/100g": "kcal",
    "P": "protein",
    "C": "carbs",
    "F": "fat",
    "蛋白": "protein",
    "碳水": "carbs",
    "脂肪": "fat",
    "纤维": "fiber",
    "类别": "category",
    "拼音": "initials",
    "首字母": "initials",
}


def clean_text(x: object) -> str:
    if x is None:
        return ""
    return re.sub(r"\s+", " ", str(x).replace("\xa0", " ")).strip()


def safe_float(x: object, default: float = 0.0) -> float:
    try:
        if x is None or str(x).strip() == "" or str(x).lower() == "nan":
            return default
        return float(str(x).replace(",", "."))
    except Exception:
        return default


def today_str() -> str:
    return date.today().isoformat()


def current_week_key() -> str:
    y, w, _ = date.today().isocalendar()
    return f"{y}-KW{w:02d}"


def row_value(row, key: str, default=""):
    try:
        if isinstance(row, sqlite3.Row):
            v = row[key]
        elif isinstance(row, dict):
            v = row.get(key, default)
        else:
            v = getattr(row, key, default)
        return default if v is None else v
    except Exception:
        return default


def macro_from_row(row) -> Dict[str, float]:
    return {k: safe_float(row_value(row, k, 0)) for k in MACRO_KEYS}


def scale_food(row, grams: float) -> Dict[str, float]:
    factor = grams / 100.0
    return {k: safe_float(row_value(row, k, 0)) * factor for k in MACRO_KEYS}


def sum_macros(rows: Iterable[Dict[str, float]]) -> Dict[str, float]:
    total = {k: 0.0 for k in MACRO_KEYS}
    for row in rows:
        for k in MACRO_KEYS:
            total[k] += safe_float(row_value(row, k, 0))
    return total


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_col(conn: sqlite3.Connection, table: str, col: str, definition: str) -> None:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
        conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS foods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            kcal REAL DEFAULT 0,
            protein REAL DEFAULT 0,
            carbs REAL DEFAULT 0,
            fat REAL DEFAULT 0,
            fiber REAL DEFAULT 0,
            category TEXT DEFAULT 'custom',
            initials TEXT DEFAULT '',
            updated_at TEXT
        )
        """
    )
    for col, definition in [
        ("kcal", "REAL DEFAULT 0"), ("protein", "REAL DEFAULT 0"), ("carbs", "REAL DEFAULT 0"),
        ("fat", "REAL DEFAULT 0"), ("fiber", "REAL DEFAULT 0"), ("category", "TEXT DEFAULT 'custom'"),
        ("initials", "TEXT DEFAULT ''"), ("updated_at", "TEXT"),
    ]:
        ensure_col(conn, "foods", col, definition)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_key TEXT NOT NULL,
            date TEXT,
            weekday TEXT,
            day TEXT,
            name TEXT NOT NULL,
            price TEXT,
            kcal REAL DEFAULT 0,
            protein REAL DEFAULT 0,
            carbs REAL DEFAULT 0,
            fat REAL DEFAULT 0,
            fiber REAL DEFAULT 0,
            note TEXT DEFAULT '',
            created_at TEXT
        )
        """
    )
    for col, definition in [("date", "TEXT"), ("weekday", "TEXT"), ("note", "TEXT DEFAULT ''"), ("fiber", "REAL DEFAULT 0")]:
        ensure_col(conn, "menu_items", col, definition)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_date TEXT NOT NULL,
            meal TEXT NOT NULL,
            name TEXT NOT NULL,
            grams REAL DEFAULT 0,
            kcal REAL DEFAULT 0,
            protein REAL DEFAULT 0,
            carbs REAL DEFAULT 0,
            fat REAL DEFAULT 0,
            fiber REAL DEFAULT 0,
            note TEXT DEFAULT '',
            created_at TEXT
        )
        """
    )
    for col, definition in [("fiber", "REAL DEFAULT 0"), ("note", "TEXT DEFAULT ''")]:
        ensure_col(conn, "daily_records", col, definition)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weight_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_key TEXT UNIQUE NOT NULL,
            log_date TEXT NOT NULL,
            weight_kg REAL NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT
        )
        """
    )
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM foods").fetchone()[0]
    if count == 0:
        seed_rows = read_food_seed_file() if FOODS_SEED_PATH.exists() else DEFAULT_FOODS
        insert_food_seed(conn, seed_rows)


def normalize_food_dict(d: Dict[str, object]) -> Optional[Dict[str, object]]:
    renamed = {}
    for k, v in d.items():
        key = FIELD_ALIASES.get(clean_text(k), clean_text(k))
        renamed[key] = v
    name = clean_text(renamed.get("name", ""))
    if not name:
        return None
    return {
        "name": name,
        "kcal": safe_float(renamed.get("kcal", 0)),
        "protein": safe_float(renamed.get("protein", 0)),
        "carbs": safe_float(renamed.get("carbs", 0)),
        "fat": safe_float(renamed.get("fat", 0)),
        "fiber": safe_float(renamed.get("fiber", 0)),
        "category": clean_text(renamed.get("category", "custom")) or "custom",
        "initials": clean_text(renamed.get("initials", "")).lower(),
    }


def read_food_seed_file() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    try:
        df = pd.read_csv(FOODS_SEED_PATH, encoding="utf-8-sig")
    except Exception:
        return []
    for rec in df.to_dict("records"):
        item = normalize_food_dict(rec)
        if item:
            rows.append(item)
    return rows


def insert_food_seed(conn: sqlite3.Connection, rows: List[Dict[str, object]]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    for raw in rows:
        item = normalize_food_dict(raw)
        if not item:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO foods
            (name, kcal, protein, carbs, fat, fiber, category, initials, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item["name"], item["kcal"], item["protein"], item["carbs"], item["fat"], item["fiber"], item["category"], item["initials"], now),
        )
    conn.commit()


def rebuild_foods_from_seed(conn: sqlite3.Connection, rows: Optional[List[Dict[str, object]]] = None) -> int:
    if rows is None:
        rows = read_food_seed_file() if FOODS_SEED_PATH.exists() else DEFAULT_FOODS
    conn.execute("DELETE FROM foods")
    conn.commit()
    insert_food_seed(conn, rows)
    return conn.execute("SELECT COUNT(*) FROM foods").fetchone()[0]


def foods_df(conn: sqlite3.Connection, query: str = "") -> pd.DataFrame:
    q = clean_text(query).lower()
    if q:
        like = f"%{q}%"
        rows = conn.execute(
            """
            SELECT id, name, kcal, protein, carbs, fat, fiber, category, initials
            FROM foods
            WHERE lower(name) LIKE ? OR lower(category) LIKE ? OR lower(initials) LIKE ?
            ORDER BY name
            """,
            (like, like, like),
        ).fetchall()
    else:
        rows = conn.execute("SELECT id, name, kcal, protein, carbs, fat, fiber, category, initials FROM foods ORDER BY name").fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return pd.DataFrame(columns=["删除", "复制", "id", "name", *MACRO_KEYS, "category", "initials"])
    df.insert(0, "复制", False)
    df.insert(0, "删除", False)
    return df


def all_foods_as_csv(conn: sqlite3.Connection) -> str:
    df = foods_df(conn, "").drop(columns=["删除", "复制"], errors="ignore")
    ordered = ["name", "kcal", "protein", "carbs", "fat", "fiber", "category", "initials"]
    return df[ordered].to_csv(index=False, encoding="utf-8-sig")


def get_food_by_id(conn: sqlite3.Connection, food_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM foods WHERE id=?", (int(food_id),)).fetchone()


def sync_food_editor(conn: sqlite3.Connection, edited_df: pd.DataFrame) -> Tuple[int, List[str]]:
    """自动保存编辑表格中的普通字段。删除/复制不在这里处理。"""
    changed = 0
    errors: List[str] = []
    now = datetime.now().isoformat(timespec="seconds")
    if edited_df.empty:
        return 0, []

    for rec in edited_df.to_dict("records"):
        food_id_raw = rec.get("id")
        if pd.isna(food_id_raw) or str(food_id_raw).strip() == "":
            continue
        try:
            food_id = int(food_id_raw)
        except Exception:
            continue
        current = get_food_by_id(conn, food_id)
        if current is None:
            continue
        name = clean_text(rec.get("name", ""))
        if not name:
            errors.append(f"id={food_id} 名称为空，已跳过。")
            continue
        values = {
            "name": name,
            "kcal": safe_float(rec.get("kcal", 0)),
            "protein": safe_float(rec.get("protein", 0)),
            "carbs": safe_float(rec.get("carbs", 0)),
            "fat": safe_float(rec.get("fat", 0)),
            "fiber": safe_float(rec.get("fiber", 0)),
            "category": clean_text(rec.get("category", "custom")) or "custom",
            "initials": clean_text(rec.get("initials", "")).lower(),
        }
        is_changed = False
        for key, val in values.items():
            old = row_value(current, key, "")
            if key in MACRO_KEYS:
                if abs(safe_float(old) - safe_float(val)) > 1e-9:
                    is_changed = True
                    break
            else:
                if clean_text(old) != clean_text(val):
                    is_changed = True
                    break
        if not is_changed:
            continue
        try:
            conn.execute(
                """
                UPDATE foods
                SET name=?, kcal=?, protein=?, carbs=?, fat=?, fiber=?, category=?, initials=?, updated_at=?
                WHERE id=?
                """,
                (
                    values["name"], values["kcal"], values["protein"], values["carbs"], values["fat"],
                    values["fiber"], values["category"], values["initials"], now, food_id,
                ),
            )
            changed += 1
        except sqlite3.IntegrityError:
            errors.append(f"id={food_id} 名称“{name}”与已有食物重复，未保存。")
    if changed:
        conn.commit()
    return changed, errors


def delete_food_ids(conn: sqlite3.Connection, ids: Iterable[int]) -> int:
    ids = [int(x) for x in ids]
    if not ids:
        return 0
    conn.executemany("DELETE FROM foods WHERE id=?", [(i,) for i in ids])
    conn.commit()
    return len(ids)


def copy_food_ids(conn: sqlite3.Connection, ids: Iterable[int]) -> int:
    count = 0
    now = datetime.now().isoformat(timespec="seconds")
    for food_id in ids:
        row = get_food_by_id(conn, int(food_id))
        if row is None:
            continue
        base = clean_text(row["name"])
        n = 1
        while True:
            new_name = f"{base}_复制{n}"
            exists = conn.execute("SELECT 1 FROM foods WHERE name=?", (new_name,)).fetchone()
            if not exists:
                break
            n += 1
        conn.execute(
            """
            INSERT INTO foods (name, kcal, protein, carbs, fat, fiber, category, initials, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_name, row["kcal"], row["protein"], row["carbs"], row["fat"], row["fiber"], row["category"], row["initials"], now),
        )
        count += 1
    if count:
        conn.commit()
    return count


def upsert_food_by_name(conn: sqlite3.Connection, item: Dict[str, object]) -> None:
    values = normalize_food_dict(item)
    if not values:
        raise ValueError("名称不能为空")
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO foods (name, kcal, protein, carbs, fat, fiber, category, initials, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            kcal=excluded.kcal, protein=excluded.protein, carbs=excluded.carbs, fat=excluded.fat,
            fiber=excluded.fiber, category=excluded.category, initials=excluded.initials, updated_at=excluded.updated_at
        """,
        (
            values["name"], values["kcal"], values["protein"], values["carbs"], values["fat"],
            values["fiber"], values["category"], values["initials"], now,
        ),
    )
    conn.commit()


def parse_imported_menu(file_bytes: bytes, filename: str) -> List[Dict[str, object]]:
    text = file_bytes.decode("utf-8-sig", errors="replace")
    fn = filename.lower()
    rows: List[Dict[str, object]] = []
    if fn.endswith(".json") or text.strip().startswith("["):
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("items") or data.get("dishes") or []
        if not isinstance(data, list):
            raise ValueError("JSON 必须是数组，或包含 items/dishes 数组。")
        for item in data:
            if isinstance(item, dict):
                rows.append(item)
    else:
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(r) for r in reader]
    out = []
    for r in rows:
        name = clean_text(r.get("name", ""))
        if not name:
            continue
        day = clean_text(r.get("day", ""))
        rec = {
            "week_key": clean_text(r.get("week_key", "")),
            "date": clean_text(r.get("date", "")),
            "weekday": clean_text(r.get("weekday", "")),
            "day": day,
            "name": name,
            "price": clean_text(r.get("price", "")),
            "kcal": safe_float(r.get("kcal", 0)),
            "protein": safe_float(r.get("protein", 0)),
            "carbs": safe_float(r.get("carbs", 0)),
            "fat": safe_float(r.get("fat", 0)),
            "fiber": safe_float(r.get("fiber", 0)),
            "note": clean_text(r.get("note", "")),
        }
        if not rec["date"]:
            m = re.search(r"(20\d{2}-\d{2}-\d{2})", day)
            if m:
                rec["date"] = m.group(1)
        out.append(rec)
    return out


def save_menu_rows(conn: sqlite3.Connection, week_key: str, rows: List[Dict[str, object]], replace: bool = True) -> int:
    if replace:
        conn.execute("DELETE FROM menu_items WHERE week_key=?", (week_key,))
    now = datetime.now().isoformat(timespec="seconds")
    count = 0
    for r in rows:
        wk = clean_text(r.get("week_key", "")) or week_key
        conn.execute(
            """
            INSERT INTO menu_items
            (week_key, date, weekday, day, name, price, kcal, protein, carbs, fat, fiber, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                wk, clean_text(r.get("date", "")), clean_text(r.get("weekday", "")), clean_text(r.get("day", "")),
                clean_text(r.get("name", "")), clean_text(r.get("price", "")), safe_float(r.get("kcal", 0)),
                safe_float(r.get("protein", 0)), safe_float(r.get("carbs", 0)), safe_float(r.get("fat", 0)),
                safe_float(r.get("fiber", 0)), clean_text(r.get("note", "")), now,
            ),
        )
        count += 1
    conn.commit()
    return count


def get_menu_rows(conn: sqlite3.Connection, week_key: str) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, week_key, date, weekday, day, name, price, kcal, protein, carbs, fat, fiber, note
        FROM menu_items WHERE week_key=?
        ORDER BY COALESCE(date, day), id
        """,
        (week_key,),
    ).fetchall()


def add_daily_record(conn: sqlite3.Connection, record_date: str, meal: str, name: str, grams: float, macro: Dict[str, float], note: str = "") -> None:
    conn.execute(
        """
        INSERT INTO daily_records
        (record_date, meal, name, grams, kcal, protein, carbs, fat, fiber, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_date, meal, name, grams, macro["kcal"], macro["protein"], macro["carbs"],
            macro["fat"], macro["fiber"], note, datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()


def get_daily_records(conn: sqlite3.Connection, record_date: str) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM daily_records WHERE record_date=? ORDER BY id", (record_date,)).fetchall()


def delete_daily_record(conn: sqlite3.Connection, rec_id: int) -> None:
    conn.execute("DELETE FROM daily_records WHERE id=?", (int(rec_id),))
    conn.commit()


def daily_total(rows: Iterable[sqlite3.Row]) -> Dict[str, float]:
    return sum_macros(macro_from_row(r) for r in rows)


def dataframe_csv(df: pd.DataFrame) -> str:
    return df.to_csv(index=False, encoding="utf-8-sig")


def render_macro_metrics(total: Dict[str, float], target: Optional[Dict[str, float]] = None) -> None:
    labels = [("kcal", "kcal"), ("protein", "蛋白"), ("carbs", "碳水"), ("fat", "脂肪"), ("fiber", "纤维")]
    cols = st.columns(len(labels))
    for col, (key, label) in zip(cols, labels):
        val = total.get(key, 0.0)
        if target and key in target:
            col.metric(label, f"{val:.0f}", f"{val - target[key]:+.0f}")
        else:
            col.metric(label, f"{val:.0f}")


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def render_summary(conn: sqlite3.Connection) -> None:
    st.subheader("历史/周月总结")
    mode = st.radio("范围", ["本周", "本月", "自定义"], horizontal=True)
    today = date.today()
    if mode == "本周":
        start = week_start(today)
        end = start + timedelta(days=6)
    elif mode == "本月":
        start = today.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(days=1)
    else:
        c1, c2 = st.columns(2)
        start = c1.date_input("开始日期", value=week_start(today), key="sum_start")
        end = c2.date_input("结束日期", value=today, key="sum_end")
    rows = conn.execute(
        """
        SELECT record_date, SUM(kcal) AS kcal, SUM(protein) AS protein, SUM(carbs) AS carbs,
               SUM(fat) AS fat, SUM(fiber) AS fiber
        FROM daily_records
        WHERE record_date BETWEEN ? AND ?
        GROUP BY record_date
        ORDER BY record_date
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    if not rows:
        st.info("这个范围没有记录。")
        return
    df = pd.DataFrame([dict(r) for r in rows])
    st.dataframe(df, use_container_width=True)
    avg = {k: df[k].mean() for k in MACRO_KEYS if k in df.columns}
    st.write("平均每日摄入")
    render_macro_metrics(avg)
    st.line_chart(df.set_index("record_date")[["kcal", "protein", "carbs", "fat"]])
    st.download_button("导出范围汇总 CSV", dataframe_csv(df), file_name=f"summary_{start}_{end}.csv", mime="text/csv")


def render_food_database(conn: sqlite3.Connection) -> None:
    st.subheader("食物数据库：原处编辑，自动保存")
    st.caption("修改名称、kcal、蛋白、碳水、脂肪、纤维、类别或拼音首字母后，按 Enter 或单击其他格/空白处触发表格提交；App 会自动写入 SQLite。删除和复制需勾选后点击按钮，避免误操作。")

    q = st.text_input("搜索：中文 / 德文 / 英文 / 拼音首字母", key="food_editor_search", placeholder="例如 sjxr, rqdbf, Magerquark")
    df = foods_df(conn, q)
    if df.empty:
        st.info("没有匹配食物。")
    else:
        edited = st.data_editor(
            df,
            key="foods_editor_v7",
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
            disabled=["id"],
            column_config={
                "删除": st.column_config.CheckboxColumn("删除", help="勾选后点击下方删除按钮"),
                "复制": st.column_config.CheckboxColumn("复制", help="勾选后点击下方复制按钮"),
                "id": st.column_config.NumberColumn("id", disabled=True),
                "name": st.column_config.TextColumn("名称", required=True),
                "kcal": st.column_config.NumberColumn("kcal/100g", step=1.0, format="%.1f"),
                "protein": st.column_config.NumberColumn("蛋白/100g", step=0.1, format="%.1f"),
                "carbs": st.column_config.NumberColumn("碳水/100g", step=0.1, format="%.1f"),
                "fat": st.column_config.NumberColumn("脂肪/100g", step=0.1, format="%.1f"),
                "fiber": st.column_config.NumberColumn("纤维/100g", step=0.1, format="%.1f"),
                "category": st.column_config.TextColumn("类别"),
                "initials": st.column_config.TextColumn("检索首字母"),
            },
        )
        changed, errors = sync_food_editor(conn, edited)
        if changed:
            st.toast(f"已自动保存 {changed} 行修改", icon="✅")
        for e in errors:
            st.error(e)

        c1, c2, c3 = st.columns([1, 1, 2])
        del_ids = edited.loc[edited["删除"] == True, "id"].dropna().astype(int).tolist() if "删除" in edited.columns else []
        copy_ids = edited.loc[edited["复制"] == True, "id"].dropna().astype(int).tolist() if "复制" in edited.columns else []
        if c1.button(f"删除勾选行 ({len(del_ids)})", disabled=len(del_ids) == 0, type="secondary"):
            n = delete_food_ids(conn, del_ids)
            st.success(f"已删除 {n} 个食物。")
            st.rerun()
        if c2.button(f"复制勾选行 ({len(copy_ids)})", disabled=len(copy_ids) == 0):
            n = copy_food_ids(conn, copy_ids)
            st.success(f"已复制 {n} 个食物。")
            st.rerun()
        if c3.button("刷新表格"):
            st.rerun()

    st.divider()
    with st.expander("新增食物", expanded=False):
        with st.form("add_food_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("名称")
            category = c2.text_input("类别", value="custom")
            initials = c3.text_input("拼音首字母/缩写")
            c4, c5, c6, c7, c8 = st.columns(5)
            kcal = c4.number_input("kcal/100g", value=0.0, step=1.0)
            protein = c5.number_input("蛋白/100g", value=0.0, step=0.1)
            carbs = c6.number_input("碳水/100g", value=0.0, step=0.1)
            fat = c7.number_input("脂肪/100g", value=0.0, step=0.1)
            fiber = c8.number_input("纤维/100g", value=0.0, step=0.1)
            if st.form_submit_button("新增/按名称覆盖"):
                try:
                    upsert_food_by_name(conn, {"name": name, "kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat, "fiber": fiber, "category": category, "initials": initials})
                    st.success("已保存。")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

    st.divider()
    st.subheader("持久化：把食物库保存进 GitHub")
    st.warning("Streamlit Community Cloud 的本地 SQLite 文件不是长期存储。编辑后的数据请下载 foods_master.csv，然后上传/提交到 GitHub 仓库根目录。下次部署或重启后，App 会用仓库里的 foods_master.csv 初始化食物库。")
    current_csv = all_foods_as_csv(conn)
    st.download_button("下载当前食物库为 foods_master.csv", current_csv, file_name="foods_master.csv", mime="text/csv")
    uploaded = st.file_uploader("导入 foods_master.csv 并重建食物库", type=["csv"], key="food_seed_upload")
    if uploaded is not None:
        try:
            text = uploaded.read().decode("utf-8-sig", errors="replace")
            df_seed = pd.read_csv(io.StringIO(text))
            rows = []
            for rec in df_seed.to_dict("records"):
                item = normalize_food_dict(rec)
                if item:
                    rows.append(item)
            if st.button(f"确认用上传的 CSV 重建食物库：{len(rows)} 项", type="primary"):
                n = rebuild_foods_from_seed(conn, rows)
                st.success(f"已重建食物库：{n} 项。")
                st.rerun()
        except Exception as e:
            st.error(f"CSV 读取失败：{e}")
    col_a, col_b = st.columns(2)
    if col_a.button("用 GitHub 中的 foods_master.csv 重建", disabled=not FOODS_SEED_PATH.exists()):
        n = rebuild_foods_from_seed(conn)
        st.success(f"已从 foods_master.csv 重建：{n} 项。")
        st.rerun()
    if col_b.button("清空并恢复程序内置默认食物"):
        n = rebuild_foods_from_seed(conn, DEFAULT_FOODS)
        st.success(f"已恢复内置默认食物：{n} 项。")
        st.rerun()


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption(APP_VERSION)
    conn = get_conn()
    init_db(conn)

    with st.sidebar:
        st.header("记录设置")
        week_key = st.text_input("本周编号", value=current_week_key(), help="例如 2026-KW28。导入菜单 CSV 里有 week_key 时也会保存。")
        record_date = st.date_input("记录日期", value=date.today()).isoformat()
        preset_name = st.selectbox("每日目标", list(TARGET_PRESETS.keys()))
        preset = TARGET_PRESETS[preset_name]
        target = {
            "kcal": st.number_input("目标 kcal", value=float(preset["kcal"]), step=50.0),
            "protein": st.number_input("目标蛋白 g", value=float(preset["protein"]), step=5.0),
            "carbs": st.number_input("目标碳水 g", value=float(preset["carbs"]), step=5.0),
            "fat": st.number_input("目标脂肪 g", value=float(preset["fat"]), step=1.0),
        }

    tab_import, tab_menu, tab_day, tab_food, tab_summary = st.tabs(["1 导入菜单结果", "2 本周菜单", "3 每天记录", "4 食物数据库", "5 历史总结"])

    with tab_import:
        st.subheader("导入 ChatGPT 已估算菜单 CSV/JSON")
        st.write("推荐字段：`day,date,weekday,week_key,name,price,kcal,protein,carbs,fat,fiber,note`。`co2e_g` 可以存在，但本版不显示也不用于计算。")
        f = st.file_uploader("上传 ChatGPT 生成的菜单 CSV 或 JSON", type=["csv", "json"])
        if f is not None:
            try:
                rows = parse_imported_menu(f.read(), f.name)
                st.success(f"识别到 {len(rows)} 条菜单。")
                if rows:
                    preview = pd.DataFrame(rows)
                    st.dataframe(preview, use_container_width=True)
                    replace = st.checkbox("替换当前 week_key 的旧菜单", value=True)
                    if st.button("保存导入菜单", type="primary"):
                        n = save_menu_rows(conn, week_key, rows, replace=replace)
                        st.success(f"已保存 {n} 条菜单。")
                        st.rerun()
            except Exception as e:
                st.error(f"导入失败：{e}")
        st.divider()
        st.info("食堂菜单仍按你的流程：网页菜单 → 发给 ChatGPT 估算 → 生成 CSV → 上传到这里。食物数据库不联网。")

    with tab_menu:
        st.subheader(f"本周菜单：{week_key}")
        rows = get_menu_rows(conn, week_key)
        if not rows:
            st.warning("当前 week_key 没有菜单。请先导入 CSV/JSON。")
        else:
            df = pd.DataFrame([dict(r) for r in rows])
            st.download_button("导出本周菜单 CSV", dataframe_csv(df), file_name=f"mensa_menu_{week_key}.csv", mime="text/csv")
            dates = ["全部"] + sorted({clean_text(r["date"] or r["day"]) for r in rows if clean_text(r["date"] or r["day"])})
            selected_date = st.selectbox("筛选日期", dates)
            show_rows = [r for r in rows if selected_date == "全部" or clean_text(r["date"] or r["day"]) == selected_date]
            for r in show_rows:
                with st.container(border=True):
                    c = st.columns([4, 0.8, 0.8, 0.8, 0.8, 0.8, 1.2])
                    date_label = r["date"] or r["day"]
                    c[0].markdown(f"**{r['name']}**  \n{date_label} {r['weekday'] or ''} · {r['price'] or ''}  \n{r['note'] or ''}")
                    c[1].metric("kcal", f"{r['kcal']:.0f}")
                    c[2].metric("P", f"{r['protein']:.0f}")
                    c[3].metric("C", f"{r['carbs']:.0f}")
                    c[4].metric("F", f"{r['fat']:.0f}")
                    c[5].metric("纤维", f"{r['fiber']:.0f}")
                    with c[6]:
                        meal = st.selectbox("餐次", ["午餐", "晚餐", "早餐", "加餐"], key=f"meal_menu_{r['id']}")
                        if st.button("加入当天", key=f"add_menu_{r['id']}"):
                            add_daily_record(conn, record_date, meal, r["name"], 1.0, macro_from_row(r), f"Mensa {week_key}")
                            st.success("已加入当天记录。")
                            st.rerun()

    with tab_day:
        st.subheader(f"每天记录：{record_date}")
        records = get_daily_records(conn, record_date)
        total = daily_total(records)
        render_macro_metrics(total, target)
        if records:
            rec_df = pd.DataFrame([dict(r) for r in records])
            st.download_button("导出当天记录 CSV", dataframe_csv(rec_df), file_name=f"daily_record_{record_date}.csv", mime="text/csv")
            for r in records:
                c = st.columns([1, 3, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8])
                c[0].write(r["meal"])
                c[1].write(r["name"])
                c[2].write(f"{r['grams']:.0f}" if r["grams"] else "-")
                c[3].write(f"{r['kcal']:.0f} kcal")
                c[4].write(f"P {r['protein']:.0f}")
                c[5].write(f"C {r['carbs']:.0f}")
                c[6].write(f"F {r['fat']:.0f}")
                if c[7].button("删除", key=f"del_rec_{r['id']}"):
                    delete_daily_record(conn, int(r["id"]))
                    st.rerun()
        else:
            st.info("当天没有记录。")

        st.divider()
        st.subheader("从食物数据库添加")
        fq = st.text_input("搜索食物", placeholder="例如 sjxr, rqdbf, Magerquark", key="add_food_search")
        food_df = foods_df(conn, fq).drop(columns=["删除", "复制"], errors="ignore")
        meal = st.selectbox("餐次", ["晚餐", "早餐", "午餐", "加餐"], key="meal_food_add")
        grams = st.number_input("克数", value=100.0, min_value=0.0, step=10.0)
        for _, frow in food_df.head(40).iterrows():
            c = st.columns([3, 0.8, 0.8, 0.8, 0.8, 1])
            macro = scale_food(frow.to_dict(), grams)
            c[0].write(f"**{frow['name']}** · {frow['category']} · {frow['initials']}")
            c[1].write(f"{macro['kcal']:.0f} kcal")
            c[2].write(f"P {macro['protein']:.0f}")
            c[3].write(f"C {macro['carbs']:.0f}")
            c[4].write(f"F {macro['fat']:.0f}")
            if c[5].button("添加", key=f"add_food_{int(frow['id'])}"):
                add_daily_record(conn, record_date, meal, frow["name"], grams, macro, "食物数据库")
                st.success("已添加。")
                st.rerun()

        st.divider()
        whey = conn.execute("SELECT * FROM foods WHERE name=?", ("乳清蛋白粉",)).fetchone()
        protein_gap = max(0.0, target["protein"] - total.get("protein", 0.0))
        st.write(f"当前蛋白缺口：**{protein_gap:.0f} g**")
        if st.button("蛋白不足时加入 40g 乳清", disabled=whey is None or protein_gap <= 0):
            macro = scale_food(whey, 40)
            add_daily_record(conn, record_date, "加餐", "乳清蛋白粉", 40, macro, "40g 乳清")
            st.success("已加入 40g 乳清。")
            st.rerun()

    with tab_food:
        render_food_database(conn)

    with tab_summary:
        render_summary(conn)

    st.caption("说明：营养数据是估算/手动维护值。Streamlit Cloud 本地 SQLite 不保证持久，重要数据请导出 CSV 并提交到 GitHub 或接外部数据库。")


if __name__ == "__main__":
    main()
