# -*- coding: utf-8 -*-
"""
Alte Mensa Dresden 减脂/营养记录器 - Streamlit 网页版增强版

运行：
    pip install -r requirements.txt
    streamlit run streamlit_app.py

本版重点：
- Mensa 菜名采用“配料关键词 + 常见份量”的详细估算；不再只按大类粗分。
- 可选 OpenAI Responses API + web_search_preview 联网搜索估算 Mensa 菜品营养。
- CO2e 只作为内部参考/导出字段，不再显示在每个菜品后面。
- 食物库支持 st.data_editor 直接编辑、复制、删除、新增。
- 食物库支持 Open Food Facts / USDA FoodData Central 在线搜索并导入。
- 保存每天记录；历史页可按周/月汇总；可记录每周体重。
- 内置用户身体数据和当前减脂计划，根据每周体重变化给出摄入调整建议。
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import sqlite3
import time
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
MACRO_KEYS = ["kcal", "protein", "carbs", "fat", "fiber", "co2e_g"]
DISPLAY_MACRO_KEYS = ["kcal", "protein", "carbs", "fat", "fiber"]

USER_PROFILE = {
    "sex": "male",
    "age": 30,
    "height_cm": 176.0,
    "start_weight_kg": 91.0,
    "target_weight_kg": 85.0,
    "deadline_weeks": 4,
    "training": "每周约5次健身房：1小时无氧 + 20分钟爬楼；不做长时间跑步，可做HIIT/Hyrox/游泳。",
    "notes": "腰突史、跟腱手术史；建议减脂时保留高蛋白和力量训练，不建议极端低热量。",
}

TARGET_PRESETS = {
    "训练日 2300 / P190 C210 F62": {"day_type": "训练日", "kcal": 2300.0, "protein": 190.0, "carbs": 210.0, "fat": 62.0},
    "休息日 2000 / P190 C120 F68": {"day_type": "休息日", "kcal": 2000.0, "protein": 190.0, "carbs": 120.0, "fat": 68.0},
    "高消耗 2500 / P190 C260 F65": {"day_type": "高消耗", "kcal": 2500.0, "protein": 190.0, "carbs": 260.0, "fat": 65.0},
    "自定义": {"day_type": "自定义", "kcal": 2300.0, "protein": 190.0, "carbs": 210.0, "fat": 62.0},
}

# 每 100g；co2e_g 仅内部参考/导出，不在每项记录后展示。
DEFAULT_FOODS = {
    "乳清蛋白粉": {"kcal": 390, "protein": 78, "carbs": 8, "fat": 6, "fiber": 0, "co2e_g": 350, "category": "protein", "initials": "rqdbf"},
    "全蛋": {"kcal": 143, "protein": 12.6, "carbs": 0.7, "fat": 9.5, "fiber": 0, "co2e_g": 450, "category": "protein_fat", "initials": "qd"},
    "农夫面包 Bauernbrot": {"kcal": 225, "protein": 6, "carbs": 45, "fat": 1, "fiber": 0, "co2e_g": 0, "category": "carb", "initials": "nfmb"},
    "土豆/生重": {"kcal": 77, "protein": 2, "carbs": 17, "fat": 0.1, "fiber": 2.2, "co2e_g": 35, "category": "carb", "initials": "td"},
    "牛肋排/估算": {"kcal": 256, "protein": 28, "carbs": 0.1, "fat": 16, "fiber": 0, "co2e_g": 0, "category": "protein_fat", "initials": "nlp"},
    "西红柿": {"kcal": 20, "protein": 1, "carbs": 6, "fat": 0.2, "fiber": 1.5, "co2e_g": 0, "category": "veg", "initials": "xhs"},
    "干意面": {"kcal": 353, "protein": 13, "carbs": 72, "fat": 1.5, "fiber": 3, "co2e_g": 150, "category": "carb", "initials": "gym"},
    "干米": {"kcal": 360, "protein": 7, "carbs": 80, "fat": 0.7, "fiber": 1.3, "co2e_g": 270, "category": "carb", "initials": "gm"},
    "橄榄油": {"kcal": 884, "protein": 0, "carbs": 0, "fat": 100, "fiber": 0, "co2e_g": 530, "category": "fat", "initials": "gly"},
    "洋葱": {"kcal": 42, "protein": 1, "carbs": 9.3, "fat": 0.1, "fiber": 1.5, "co2e_g": 0, "category": "veg", "initials": "yc"},
    "燕麦片": {"kcal": 370, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10, "co2e_g": 90, "category": "carb", "initials": "ymp"},
    "牛肉馅/约10%脂肪": {"kcal": 230, "protein": 16, "carbs": 0.1, "fat": 10, "fiber": 0, "co2e_g": 0, "category": "protein_fat", "initials": "nrx"},
    "生鸡胸肉": {"kcal": 110, "protein": 23, "carbs": 0, "fat": 1.5, "fiber": 0, "co2e_g": 560, "category": "protein", "initials": "sjxr"},
    "瘦牛肉/约5%脂肪": {"kcal": 137, "protein": 21, "carbs": 0, "fat": 5, "fiber": 0, "co2e_g": 2700, "category": "protein", "initials": "snr"},
    "白菜/生菜/菠菜": {"kcal": 15, "protein": 1.1, "carbs": 1.2, "fat": 0.3, "fiber": 2, "co2e_g": 0, "category": "veg", "initials": "bcsc"},
    "导入食物_wynxcdz": {"kcal": 213, "protein": 15.7, "carbs": 1.5, "fat": 16, "fiber": 0, "co2e_g": 0, "category": "protein_fat", "initials": "wynxcdz"},
    "虾仁": {"kcal": 99, "protein": 24, "carbs": 0.2, "fat": 0.3, "fiber": 0, "co2e_g": 1000, "category": "protein", "initials": "xr"},
    "西兰花/蔬菜": {"kcal": 34, "protein": 2.8, "carbs": 7, "fat": 0.4, "fiber": 3, "co2e_g": 45, "category": "veg", "initials": "xlh"},
    "西瓜": {"kcal": 30, "protein": 0.6, "carbs": 7.6, "fat": 0.2, "fiber": 0.4, "co2e_g": 0, "category": "fruit", "initials": "xg"},
    "鸡腿肉去皮/生重": {"kcal": 125, "protein": 20, "carbs": 0, "fat": 5, "fiber": 0, "co2e_g": 560, "category": "protein", "initials": "jtr"},
    "熟米饭": {"kcal": 130, "protein": 2.7, "carbs": 28, "fat": 0.3, "fiber": 0.4, "co2e_g": 100, "category": "carb", "initials": "smf"},
    "Magerquark": {"kcal": 67, "protein": 12, "carbs": 4, "fat": 0.2, "fiber": 0, "co2e_g": 180, "category": "protein", "initials": "mq"},
    "Skyr natur": {"kcal": 63, "protein": 11, "carbs": 4, "fat": 0.2, "fiber": 0, "co2e_g": 180, "category": "protein", "initials": "skyr"},
    "三文鱼": {"kcal": 208, "protein": 20, "carbs": 0, "fat": 13, "fiber": 0, "co2e_g": 600, "category": "protein_fat", "initials": "swy"},
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

COMPONENTS: Dict[str, Dict[str, float]] = {
    "veg_base": {"kcal": 80, "protein": 4, "carbs": 12, "fat": 2, "fiber": 5, "co2e_g": 120},
    "mixed_veg_large": {"kcal": 130, "protein": 6, "carbs": 22, "fat": 3, "fiber": 8, "co2e_g": 180},
    "rice_250g": {"kcal": 325, "protein": 7, "carbs": 70, "fat": 1, "fiber": 1, "co2e_g": 250},
    "pasta_260g": {"kcal": 410, "protein": 14, "carbs": 82, "fat": 3, "fiber": 4, "co2e_g": 180},
    "wholegrain_bonus": {"kcal": 0, "protein": 1, "carbs": 0, "fat": 0, "fiber": 3, "co2e_g": 0},
    "potato_300g": {"kcal": 230, "protein": 6, "carbs": 51, "fat": 0.5, "fiber": 6, "co2e_g": 100},
    "mashed_potato": {"kcal": 280, "protein": 7, "carbs": 45, "fat": 9, "fiber": 4, "co2e_g": 230},
    "kroketten": {"kcal": 330, "protein": 5, "carbs": 45, "fat": 14, "fiber": 4, "co2e_g": 250},
    "fries": {"kcal": 560, "protein": 7, "carbs": 70, "fat": 28, "fiber": 7, "co2e_g": 500},
    "bread": {"kcal": 220, "protein": 7, "carbs": 45, "fat": 2, "fiber": 3, "co2e_g": 120},
    "focaccia": {"kcal": 300, "protein": 8, "carbs": 48, "fat": 9, "fiber": 3, "co2e_g": 180},
    "burger_bun": {"kcal": 260, "protein": 8, "carbs": 48, "fat": 4, "fiber": 3, "co2e_g": 160},
    "pizza_base": {"kcal": 580, "protein": 20, "carbs": 80, "fat": 20, "fiber": 4, "co2e_g": 450},
    "lasagne_base": {"kcal": 610, "protein": 24, "carbs": 70, "fat": 25, "fiber": 5, "co2e_g": 550},
    "spaetzle": {"kcal": 430, "protein": 15, "carbs": 72, "fat": 10, "fiber": 3, "co2e_g": 300},
    "chicken_150g": {"kcal": 240, "protein": 43, "carbs": 0, "fat": 6, "fiber": 0, "co2e_g": 850},
    "fish_150g": {"kcal": 220, "protein": 35, "carbs": 0, "fat": 8, "fiber": 0, "co2e_g": 900},
    "beef_150g": {"kcal": 320, "protein": 36, "carbs": 0, "fat": 19, "fiber": 0, "co2e_g": 4050},
    "pork_150g": {"kcal": 330, "protein": 34, "carbs": 0, "fat": 21, "fiber": 0, "co2e_g": 1650},
    "liver_150g": {"kcal": 230, "protein": 32, "carbs": 5, "fat": 8, "fiber": 0, "co2e_g": 3000},
    "meatballs": {"kcal": 380, "protein": 28, "carbs": 10, "fat": 24, "fiber": 1, "co2e_g": 2200},
    "halloumi_100g": {"kcal": 320, "protein": 22, "carbs": 2, "fat": 25, "fiber": 0, "co2e_g": 850},
    "tofu_soy": {"kcal": 240, "protein": 28, "carbs": 12, "fat": 9, "fiber": 6, "co2e_g": 300},
    "legumes": {"kcal": 300, "protein": 18, "carbs": 48, "fat": 4, "fiber": 14, "co2e_g": 250},
    "jackfruit_patty": {"kcal": 300, "protein": 12, "carbs": 36, "fat": 12, "fiber": 8, "co2e_g": 260},
    "egg_dairy": {"kcal": 180, "protein": 14, "carbs": 4, "fat": 12, "fiber": 0, "co2e_g": 450},
    "cream_sauce": {"kcal": 220, "protein": 5, "carbs": 8, "fat": 19, "fiber": 0, "co2e_g": 350},
    "tomato_sauce": {"kcal": 100, "protein": 3, "carbs": 16, "fat": 3, "fiber": 4, "co2e_g": 120},
    "cheese": {"kcal": 170, "protein": 11, "carbs": 1, "fat": 14, "fiber": 0, "co2e_g": 500},
    "pesto": {"kcal": 290, "protein": 5, "carbs": 6, "fat": 28, "fiber": 2, "co2e_g": 300},
    "coconut_curry": {"kcal": 230, "protein": 4, "carbs": 12, "fat": 18, "fiber": 4, "co2e_g": 180},
    "honey_sauce": {"kcal": 110, "protein": 1, "carbs": 18, "fat": 4, "fiber": 0, "co2e_g": 80},
    "fried_breading": {"kcal": 250, "protein": 5, "carbs": 28, "fat": 14, "fiber": 2, "co2e_g": 250},
    "sweet_dish": {"kcal": 620, "protein": 16, "carbs": 105, "fat": 15, "fiber": 4, "co2e_g": 500},
    "ice_cream": {"kcal": 180, "protein": 4, "carbs": 28, "fat": 6, "fiber": 0, "co2e_g": 180},
    "cake": {"kcal": 360, "protein": 5, "carbs": 55, "fat": 13, "fiber": 3, "co2e_g": 250},
    "soup_base": {"kcal": 260, "protein": 10, "carbs": 35, "fat": 8, "fiber": 7, "co2e_g": 220},
    "salad_dressing": {"kcal": 250, "protein": 6, "carbs": 25, "fat": 14, "fiber": 7, "co2e_g": 150},
}


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
    source: str = "配料估算"


def today_str() -> str:
    return date.today().isoformat()


def current_week_key() -> str:
    y, w, _ = date.today().isocalendar()
    return f"{y}-KW{w:02d}"


def clean_text(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = s.replace("\xa0", " ").replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t\r\f\v]+", " ", str(s)).strip()


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


def add_component(total: Dict[str, float], component: str, factor: float = 1.0) -> None:
    comp = COMPONENTS[component]
    for k in MACRO_KEYS:
        total[k] += comp.get(k, 0) * factor


def scale_food(food, grams: float) -> Dict[str, float]:
    factor = grams / 100.0
    return {k: safe_float(row_value(food, k, 0), 0) * factor for k in MACRO_KEYS}


def estimate_dish_by_ingredients(name: str) -> Tuple[Dict[str, float], str]:
    """Ingredient-aware estimate for one normal German cafeteria portion."""
    n = clean_text(name).lower()
    m = {k: 0.0 for k in MACRO_KEYS}
    reasons: List[str] = []

    def has(*words: str) -> bool:
        return any(w in n for w in words)

    # First handle simple dessert/salad/soup cases.
    if has("softeis", "eis"):
        add_component(m, "ice_cream"); return m, "配料估算: soft ice"
    if has("kuchen", "streusel"):
        add_component(m, "cake"); return m, "配料估算: cake"
    if has("milchgrieß", "milchgriess", "grieß", "griess"):
        add_component(m, "sweet_dish"); return m, "配料估算: milk semolina"
    if has("suppe", "terrine", "eintopf", "kaltschale") and not has("pasta"):
        add_component(m, "soup_base"); reasons.append("soup")
        if has("rind", "gulasch", "fleisch"):
            add_component(m, "beef_150g", 0.55); reasons.append("beef")
        if has("kicher", "bohnen", "linsen"):
            add_component(m, "legumes", 0.55); reasons.append("legumes")
        return m, "配料估算: " + "+".join(reasons)
    if "salat" in n and not has("lasagne", "pasta", "auflauf", "burger"):
        add_component(m, "salad_dressing"); return m, "配料估算: salad+dressing"

    # Staple/carbohydrate components.
    if has("reis", "basmati", "rice"):
        add_component(m, "rice_250g"); reasons.append("rice")
    if has("pasta", "nudel", "makkaroni", "spaghetti", "tortell", "gnocchi"):
        add_component(m, "pasta_260g"); reasons.append("pasta")
    if has("vollkorn"):
        add_component(m, "wholegrain_bonus"); reasons.append("wholegrain")
    if has("lasagne"):
        add_component(m, "lasagne_base"); reasons.append("lasagne")
    if has("spätzle", "spaetzle"):
        add_component(m, "spaetzle"); reasons.append("spätzle")
    if has("kartoffel", "potato"):
        if has("püree", "pueree", "brei", "mash"):
            add_component(m, "mashed_potato"); reasons.append("mash")
        elif has("kroket", "krokette"):
            add_component(m, "kroketten"); reasons.append("kroketten")
        elif has("bratkartoff", "pommes"):
            add_component(m, "fries" if has("pommes") else "potato_300g"); reasons.append("fried potato")
        else:
            add_component(m, "potato_300g"); reasons.append("potato")
    if has("pommes", "fries") and "fried potato" not in reasons:
        add_component(m, "fries"); reasons.append("fries")
    if has("baguette", "brot"):
        add_component(m, "bread"); reasons.append("bread")
    if has("focaccia"):
        add_component(m, "focaccia"); reasons.append("focaccia")
    if has("burger"):
        add_component(m, "burger_bun"); reasons.append("bun")
    if has("pizza"):
        add_component(m, "pizza_base"); reasons.append("pizza")

    # Protein components.
    if has("hähnchen", "haehnchen", "huhn", "chicken", "pute"):
        add_component(m, "chicken_150g"); reasons.append("chicken")
    if has("seelachs", "fisch", "kabeljau", "forelle", "lachs"):
        add_component(m, "fish_150g"); reasons.append("fish")
    if has("rind", "beef"):
        add_component(m, "beef_150g"); reasons.append("beef")
    if has("schwein", "bacon", "speck", "schinken"):
        add_component(m, "pork_150g"); reasons.append("pork/ham")
    if has("leber"):
        add_component(m, "liver_150g"); reasons.append("liver")
    if has("frikad", "klops", "bällchen", "baellchen", "meatball"):
        add_component(m, "meatballs"); reasons.append("meatballs")
    if has("halloumi"):
        add_component(m, "halloumi_100g"); reasons.append("halloumi")
    if has("soja", "tofu", "brew bites", "veganes", "vegane", "vegani"):
        add_component(m, "tofu_soy"); reasons.append("soy")
    if has("kicher", "bohnen", "linsen", "chili sin"):
        add_component(m, "legumes"); reasons.append("legumes")
    if has("jackfruit"):
        add_component(m, "jackfruit_patty"); reasons.append("jackfruit")
    if has("ei", "ricotta"):
        add_component(m, "egg_dairy"); reasons.append("egg/dairy")

    # Sauce/fat/vegetable modifiers.
    if has("sahne", "cream", "rahm", "käse", "kaese", "gouda", "mozzarella", "cheese", "ricotta"):
        if has("sahne", "cream", "rahm"):
            add_component(m, "cream_sauce"); reasons.append("cream")
        if has("käse", "kaese", "gouda", "mozzarella", "cheese", "ricotta"):
            add_component(m, "cheese", 0.75); reasons.append("cheese")
    if has("tomaten", "tomate", "arrabbiata", "bolognese"):
        add_component(m, "tomato_sauce"); reasons.append("tomato sauce")
    if has("pesto"):
        add_component(m, "pesto"); reasons.append("pesto")
    if has("kokos", "curry"):
        add_component(m, "coconut_curry"); reasons.append("curry/coconut")
    if has("honig", "balsamico", "apfel", "erdbeer", "zucker"):
        add_component(m, "honey_sauce"); reasons.append("sweet sauce")
    if has("schnitzel", "frittiert", "frittierte", "teigtaschen", "knusper", "paniert"):
        add_component(m, "fried_breading"); reasons.append("fried/breaded")
    if has("gemüse", "gemuese", "spinat", "karotte", "möhre", "mohre", "zucchini", "gurke", "paprika", "rote bete"):
        add_component(m, "mixed_veg_large"); reasons.append("vegetables")

    # If no carbohydrate basis was found, add a general cafeteria portion base.
    if m["kcal"] < 180:
        add_component(m, "veg_base"); reasons.append("base")
    if m["kcal"] < 450 and not has("suppe", "salat"):
        add_component(m, "bread", 0.6); reasons.append("portion adjustment")

    # Bounds for normal Mensa serving.
    m["kcal"] = min(max(m["kcal"], 250), 1250)
    m["protein"] = min(max(m["protein"], 5), 70)
    m["carbs"] = min(max(m["carbs"], 5), 150)
    m["fat"] = min(max(m["fat"], 2), 70)
    m["fiber"] = min(max(m["fiber"], 0), 25)
    m["co2e_g"] = min(max(m["co2e_g"], 0), 5500)
    return m, "配料估算: " + "+".join(reasons[:6])


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
                macro, source = estimate_dish_by_ingredients(food)
                # Official Mensa CO2 if present: keep internally, do not display per item.
                for j in range(i, min(len(lines), i + 5)):
                    m = CO2_RE.search(lines[j])
                    if m:
                        val = safe_float(m.group(1), 0)
                        co2 = val * 1000 if (m.group(2) or "").lower() == "kg" else val
                        if co2 > 0:
                            macro["co2e_g"] = co2
                        break
                key = (current_day, food)
                if key not in seen:
                    seen.add(key)
                    rows.append(MenuDish(week_key=week_key, day=current_day, name=food, price=price, source=source, **macro))

    if not rows:
        for line in lines:
            if looks_like_day(line):
                current_day = line
                continue
            if looks_like_food_line(line):
                macro, source = estimate_dish_by_ingredients(line)
                key = (current_day, line)
                if key not in seen:
                    seen.add(key)
                    rows.append(MenuDish(week_key=week_key, day=current_day, name=line, source=source, **macro))
    return rows


def fetch_url_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MensaFatLossStreamlit/2.0"})
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
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
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
        CREATE TABLE IF NOT EXISTS body_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_date TEXT UNIQUE NOT NULL,
            weight_kg REAL,
            waist_cm REAL,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_settings (
            record_date TEXT PRIMARY KEY,
            day_type TEXT,
            kcal_target REAL,
            protein_target REAL,
            carbs_target REAL,
            fat_target REAL,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    ensure_column(conn, "foods", "source", "TEXT DEFAULT ''")

    now = datetime.now().isoformat(timespec="seconds")
    for name, v in DEFAULT_FOODS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO foods
            (name, kcal, protein, carbs, fat, fiber, co2e_g, category, initials, updated_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, v["kcal"], v["protein"], v["carbs"], v["fat"], v["fiber"], v["co2e_g"], v["category"], v["initials"], now, "默认/用户导入"),
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


def update_menu_items_from_df(conn: sqlite3.Connection, df: pd.DataFrame, week_key: str) -> None:
    required = ["id", "day", "name", "price", *MACRO_KEYS, "source"]
    for _, row in df.iterrows():
        name = clean_text(row.get("name", ""))
        if not name:
            continue
        item_id = safe_float(row.get("id"), 0)
        values = (
            week_key, clean_text(row.get("day", "")), name, clean_text(row.get("price", "")),
            safe_float(row.get("kcal")), safe_float(row.get("protein")), safe_float(row.get("carbs")),
            safe_float(row.get("fat")), safe_float(row.get("fiber")), safe_float(row.get("co2e_g")),
            clean_text(row.get("source", "手动编辑")), datetime.now().isoformat(timespec="seconds"),
        )
        if item_id > 0:
            conn.execute(
                """
                UPDATE menu_items SET week_key=?, day=?, name=?, price=?, kcal=?, protein=?, carbs=?, fat=?, fiber=?, co2e_g=?, source=?, created_at=?
                WHERE id=?
                """,
                (*values, int(item_id)),
            )
        else:
            conn.execute(
                """
                INSERT INTO menu_items (week_key, day, name, price, kcal, protein, carbs, fat, fiber, co2e_g, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
    conn.commit()


def get_record_rows(conn: sqlite3.Connection, record_date: str) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM daily_records WHERE record_date=? ORDER BY id", (record_date,)).fetchall()


def get_records_between(conn: sqlite3.Connection, start_date: str, end_date: str) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM daily_records WHERE record_date BETWEEN ? AND ? ORDER BY record_date, id",
        (start_date, end_date),
    ).fetchall()


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


def save_day_settings(conn: sqlite3.Connection, record_date: str, target: Dict[str, float], day_type: str) -> None:
    conn.execute(
        """
        INSERT INTO daily_settings (record_date, day_type, kcal_target, protein_target, carbs_target, fat_target, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(record_date) DO UPDATE SET
            day_type=excluded.day_type,
            kcal_target=excluded.kcal_target,
            protein_target=excluded.protein_target,
            carbs_target=excluded.carbs_target,
            fat_target=excluded.fat_target,
            updated_at=excluded.updated_at
        """,
        (record_date, day_type, target["kcal"], target["protein"], target["carbs"], target["fat"], datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def get_foods(conn: sqlite3.Connection, query: str = "") -> List[sqlite3.Row]:
    q = clean_text(query).lower()
    if not q:
        return conn.execute("SELECT * FROM foods ORDER BY name").fetchall()
    like = f"%{q}%"
    return conn.execute(
        """
        SELECT * FROM foods
        WHERE lower(name) LIKE ? OR lower(category) LIKE ? OR lower(initials) LIKE ? OR lower(source) LIKE ?
        ORDER BY name
        """,
        (like, like, like, like),
    ).fetchall()


def upsert_food(conn: sqlite3.Connection, values: Dict[str, object]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO foods (name, kcal, protein, carbs, fat, fiber, co2e_g, category, initials, updated_at, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            kcal=excluded.kcal, protein=excluded.protein, carbs=excluded.carbs, fat=excluded.fat,
            fiber=excluded.fiber, co2e_g=excluded.co2e_g, category=excluded.category,
            initials=excluded.initials, updated_at=excluded.updated_at, source=excluded.source
        """,
        (values["name"], values["kcal"], values["protein"], values["carbs"], values["fat"], values["fiber"], values["co2e_g"], values["category"], values["initials"], now, values.get("source", "手动")),
    )
    conn.commit()


def delete_food(conn: sqlite3.Connection, food_id: int) -> None:
    conn.execute("DELETE FROM foods WHERE id=?", (food_id,))
    conn.commit()


def duplicate_food(conn: sqlite3.Connection, food_id: int) -> None:
    row = conn.execute("SELECT * FROM foods WHERE id=?", (food_id,)).fetchone()
    if not row:
        return
    base_name = row["name"] + "_复制"
    name = base_name
    i = 2
    while conn.execute("SELECT 1 FROM foods WHERE name=?", (name,)).fetchone():
        name = f"{base_name}{i}"
        i += 1
    upsert_food(conn, {
        "name": name, "kcal": row["kcal"], "protein": row["protein"], "carbs": row["carbs"], "fat": row["fat"],
        "fiber": row["fiber"], "co2e_g": row["co2e_g"], "category": row["category"], "initials": row["initials"],
        "source": "复制",
    })


def save_foods_from_editor(conn: sqlite3.Connection, df: pd.DataFrame) -> Tuple[int, int, int]:
    deleted = copied = saved = 0
    for _, row in df.iterrows():
        food_id = int(safe_float(row.get("id", 0), 0)) if str(row.get("id", "")).strip() not in ["", "nan", "None"] else 0
        if bool(row.get("删除", False)) and food_id > 0:
            delete_food(conn, food_id)
            deleted += 1
            continue
        if bool(row.get("复制", False)) and food_id > 0:
            duplicate_food(conn, food_id)
            copied += 1
        name = clean_text(row.get("name", ""))
        if name:
            if food_id > 0:
                conn.execute(
                    """
                    UPDATE foods SET name=?, kcal=?, protein=?, carbs=?, fat=?, fiber=?, co2e_g=?, category=?, initials=?, source=?, updated_at=?
                    WHERE id=?
                    """,
                    (name, safe_float(row.get("kcal")), safe_float(row.get("protein")), safe_float(row.get("carbs")),
                     safe_float(row.get("fat")), safe_float(row.get("fiber")), safe_float(row.get("co2e_g")),
                     clean_text(row.get("category", "custom")), clean_text(row.get("initials", "")).lower(),
                     clean_text(row.get("source", "编辑")), datetime.now().isoformat(timespec="seconds"), food_id),
                )
            else:
                upsert_food(conn, {
                    "name": name, "kcal": safe_float(row.get("kcal")), "protein": safe_float(row.get("protein")),
                    "carbs": safe_float(row.get("carbs")), "fat": safe_float(row.get("fat")), "fiber": safe_float(row.get("fiber")),
                    "co2e_g": safe_float(row.get("co2e_g")), "category": clean_text(row.get("category", "custom")),
                    "initials": clean_text(row.get("initials", "")).lower(), "source": clean_text(row.get("source", "编辑")),
                })
            saved += 1
    conn.commit()
    return saved, copied, deleted


def upsert_body_metric(conn: sqlite3.Connection, metric_date: str, weight_kg: float, waist_cm: float, notes: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO body_metrics (metric_date, weight_kg, waist_cm, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(metric_date) DO UPDATE SET
            weight_kg=excluded.weight_kg, waist_cm=excluded.waist_cm, notes=excluded.notes, updated_at=excluded.updated_at
        """,
        (metric_date, weight_kg, waist_cm, notes, now, now),
    )
    conn.commit()


def get_body_metrics(conn: sqlite3.Connection, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[sqlite3.Row]:
    if start_date and end_date:
        return conn.execute("SELECT * FROM body_metrics WHERE metric_date BETWEEN ? AND ? ORDER BY metric_date", (start_date, end_date)).fetchall()
    return conn.execute("SELECT * FROM body_metrics ORDER BY metric_date").fetchall()


def rows_to_csv(rows: Iterable[sqlite3.Row | Dict[str, object]], fields: List[str]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row_value(row, f, "") for f in fields})
    return output.getvalue()


def total_for_records(rows: Iterable[sqlite3.Row]) -> Dict[str, float]:
    return add_macros(macro_from_obj(r) for r in rows)


def records_to_daily_df(records: List[sqlite3.Row]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=["record_date", *MACRO_KEYS])
    df = pd.DataFrame([dict(r) for r in records])
    return df.groupby("record_date", as_index=False)[MACRO_KEYS].sum()


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


def parse_openai_output(data: dict) -> str:
    text = data.get("output_text", "")
    if text:
        return text
    chunks = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if "text" in content:
                chunks.append(content["text"])
    return "\n".join(chunks)


def call_openai_estimator(api_key: str, model: str, rows: List[MenuDish], use_web_search: bool = True) -> List[MenuDish]:
    payload_obj = {
        "model": model,
        "input": (
            "You are estimating nutrition for German Studentenwerk/Mensa dishes. "
            "For each dish, use web search when available to find comparable recipes/products/nutrition pages, "
            "then infer one normal cafeteria portion. Do not simply categorize by food type; consider ingredients in the dish name "
            "such as rice, pasta, sauce, meat, cheese, frying, potatoes, legumes, cream, coconut, etc. "
            "Return JSON only with key dishes. Units: kcal and grams for protein/carbs/fat/fiber; co2e_g may be estimated or copied from local_estimate. "
            "Do not include markdown. Preserve day/name/price.\n\n"
            + json.dumps({
                "schema": {"dishes": [{"day": "string", "name": "string", "price": "string", "kcal": "number", "protein": "number", "carbs": "number", "fat": "number", "fiber": "number", "co2e_g": "number", "source_note": "short string"}]},
                "dishes": [{"day": r.day, "name": r.name, "price": r.price, "local_estimate": macro_from_obj(r)} for r in rows],
            }, ensure_ascii=False)
        ),
    }
    if use_web_search:
        payload_obj["tools"] = [{"type": "web_search_preview"}]
    payload = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API 请求失败：HTTP {e.code}\n{msg}")

    parsed = extract_json_from_text(parse_openai_output(data))
    if isinstance(parsed, dict) and "dishes" in parsed:
        parsed = parsed["dishes"]
    if not isinstance(parsed, list):
        raise ValueError("OpenAI 返回 JSON 不是列表。")

    out: List[MenuDish] = []
    for item in parsed:
        name = clean_text(str(item.get("name", "")))
        if not name:
            continue
        source_note = clean_text(str(item.get("source_note", "")))
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
                source="OpenAI联网搜索" + (f"/{source_note[:60]}" if source_note else ""),
            )
        )
    return out


def search_openfoodfacts(query: str, page_size: int = 10) -> List[Dict[str, object]]:
    params = urllib.parse.urlencode({
        "search_terms": query,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": page_size,
        "fields": "product_name,brands,nutriments,categories_tags,code",
    })
    url = f"https://world.openfoodfacts.org/cgi/search.pl?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "MaxMensaNutritionApp/1.0 (personal use)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    out = []
    for p in data.get("products", []):
        n = p.get("nutriments", {}) or {}
        name = clean_text(p.get("product_name") or p.get("brands") or p.get("code") or "OpenFoodFacts product")
        if not name:
            continue
        out.append({
            "name": name,
            "kcal": safe_float(n.get("energy-kcal_100g"), safe_float(n.get("energy-kcal"), 0)),
            "protein": safe_float(n.get("proteins_100g"), 0),
            "carbs": safe_float(n.get("carbohydrates_100g"), 0),
            "fat": safe_float(n.get("fat_100g"), 0),
            "fiber": safe_float(n.get("fiber_100g"), 0),
            "co2e_g": 0,
            "category": "openfoodfacts",
            "initials": "off",
            "source": "Open Food Facts",
        })
    return out


def search_usda(query: str, api_key: str, page_size: int = 10) -> List[Dict[str, object]]:
    key = api_key.strip() or "DEMO_KEY"
    params = urllib.parse.urlencode({"query": query, "pageSize": page_size, "api_key": key})
    url = f"https://api.nal.usda.gov/fdc/v1/foods/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "MaxMensaNutritionApp/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    out = []
    for food in data.get("foods", []):
        nutrients = {clean_text(n.get("nutrientName", "")).lower(): n.get("value", 0) for n in food.get("foodNutrients", [])}
        kcal = safe_float(nutrients.get("energy", nutrients.get("energy (atwater general factors)", 0)), 0)
        # USDA energy may occasionally be kJ in a separate nutrient; prefer kcal-looking values below 900.
        if kcal > 900:
            kcal = kcal / 4.184
        out.append({
            "name": clean_text(food.get("description", "USDA food")).title(),
            "kcal": kcal,
            "protein": safe_float(nutrients.get("protein", 0), 0),
            "carbs": safe_float(nutrients.get("carbohydrate, by difference", nutrients.get("carbohydrate, by summation", 0)), 0),
            "fat": safe_float(nutrients.get("total lipid (fat)", 0), 0),
            "fiber": safe_float(nutrients.get("fiber, total dietary", 0), 0),
            "co2e_g": 0,
            "category": "usda",
            "initials": "usda",
            "source": f"USDA FDC {food.get('fdcId', '')}",
        })
    return out


def import_foods_from_uploaded_csv(conn: sqlite3.Connection, file) -> int:
    raw = file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:1024], delimiters=",;\t")
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    count = 0
    for row in reader:
        name = clean_text(row.get("name") or row.get("名称") or row.get("food") or "")
        initials = clean_text(row.get("initials") or row.get("拼音") or "").lower()
        if not name or set(name) <= {"?"}:
            name = f"导入食物_{initials or count+1}"
        values = {
            "name": name,
            "kcal": safe_float(row.get("kcal/100g") or row.get("kcal") or row.get("热量")),
            "protein": safe_float(row.get("P") or row.get("protein") or row.get("蛋白")),
            "carbs": safe_float(row.get("C") or row.get("carbs") or row.get("碳水")),
            "fat": safe_float(row.get("F") or row.get("fat") or row.get("脂肪")),
            "fiber": safe_float(row.get("fiber") or row.get("纤维")),
            "co2e_g": safe_float(row.get("CO2e/100g") or row.get("CO₂e/100g") or row.get("CO?e/100g") or row.get("co2e_g")),
            "category": clean_text(row.get("category") or row.get("类别") or "custom"),
            "initials": initials,
            "source": "CSV导入",
        }
        upsert_food(conn, values)
        count += 1
    return count


def rerun():
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()


def render_metric_row(total: Dict[str, float], target: Optional[Dict[str, float]] = None, show_co2_total: bool = False) -> None:
    keys = DISPLAY_MACRO_KEYS + (["co2e_g"] if show_co2_total else [])
    labels = {"kcal": "热量", "protein": "蛋白 g", "carbs": "碳水 g", "fat": "脂肪 g", "fiber": "纤维 g", "co2e_g": "CO₂e g"}
    cols = st.columns(len(keys))
    for col, key in zip(cols, keys):
        label = labels[key]
        if target and key in target:
            delta = total.get(key, 0) - target[key]
            col.metric(label, f"{total.get(key, 0):.0f}", f"{delta:+.0f}")
        else:
            col.metric(label, f"{total.get(key, 0):.0f}")


def make_weekly_advice(daily_df: pd.DataFrame, weights: List[sqlite3.Row], target: Dict[str, float]) -> List[str]:
    advice: List[str] = []
    profile = USER_PROFILE
    current_weight = profile["start_weight_kg"]
    weekly_change = None
    if weights:
        current_weight = safe_float(weights[-1]["weight_kg"], current_weight)
    if len(weights) >= 2:
        weekly_change = safe_float(weights[-2]["weight_kg"], current_weight) - safe_float(weights[-1]["weight_kg"], current_weight)

    bmr = 10 * current_weight + 6.25 * profile["height_cm"] - 5 * profile["age"] + 5
    tdee_est = bmr * 1.55
    advice.append(f"当前按 {current_weight:.1f} kg 估算，BMR 约 {bmr:.0f} kcal，训练频率下 TDEE 粗估约 {tdee_est:.0f} kcal。")
    advice.append(f"当前计划目标：训练日 2300 kcal / P190 C210 F62；休息日 2000 kcal / P190 C120 F68。目标从约 91 kg 到 85 kg，属于偏激进减脂。")

    if not daily_df.empty:
        last7 = daily_df.tail(7)
        avg_kcal = last7["kcal"].mean()
        avg_p = last7["protein"].mean()
        advice.append(f"最近 {len(last7)} 个记录日平均：{avg_kcal:.0f} kcal，蛋白 {avg_p:.0f} g。")
        if avg_p < 170:
            advice.append("蛋白偏低：先把每日蛋白拉回 180–190 g，再考虑继续降热量；优先用鸡胸、虾仁、Skyr/Magerquark 或 40g 乳清补足。")
        elif avg_p >= 180:
            advice.append("蛋白基本达标：后续调整主要看体重周均变化，而不是再提高蛋白。")

    if weekly_change is None:
        advice.append("还没有至少两次体重记录。建议每周固定同一天早晨空腹称重，先记录 2 周再做热量调整。")
    else:
        advice.append(f"最近两次体重记录变化：约 -{weekly_change:.2f} kg/周。")
        if weekly_change < 0.4:
            advice.append("下降太慢：每日平均热量可下调 150–200 kcal，或每周增加 2 次 20–30 分钟低冲击有氧/爬楼。")
        elif 0.4 <= weekly_change <= 1.1:
            advice.append("下降速度合理：保持当前摄入，优先保证训练表现和睡眠。")
        elif 1.1 < weekly_change <= 1.6:
            advice.append("下降较快：可以继续短期执行，但注意力量明显下降、睡眠变差、饥饿过强时，把训练日提高 100–150 kcal。")
        else:
            advice.append("下降过快：建议增加 150–250 kcal/日，避免肌肉和训练表现损失。")
    return advice


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🍽️", layout="wide")
    st.title(APP_TITLE)
    st.caption("Streamlit 网页版：导入本周菜单 → 配料/联网估算营养 → 加入每天记录 → 周/月总结与摄入建议。")

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
        if st.button("保存当天目标"):
            save_day_settings(conn, record_date, target, preset.get("day_type", preset_name))
            st.success("已保存当天目标。")
        st.divider()
        st.header("身体数据/计划")
        st.write(f"{USER_PROFILE['height_cm']:.0f} cm · {USER_PROFILE['age']} 岁 · 当前约 {USER_PROFILE['start_weight_kg']:.0f} kg → 目标 {USER_PROFILE['target_weight_kg']:.0f} kg")
        st.caption(USER_PROFILE["training"])
        st.divider()
        st.header("OpenAI / ChatGPT")
        model = st.text_input("模型", value=DEFAULT_MODEL)
        api_key_input = st.text_input("OpenAI API key，可留空", value="", type="password")
        st.caption("留空时只使用本地配料估算。联网搜索估算需要 OpenAI API key。")

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
            st.write(f"本地解析到 **{len(local_rows)}** 个菜品。下面是配料关键词估算，不再按单一食物类型粗分。")
            if local_rows:
                preview_df = pd.DataFrame([
                    {"day": r.day, "name": r.name, "price": r.price, **{k: getattr(r, k) for k in DISPLAY_MACRO_KEYS}, "source": r.source}
                    for r in local_rows
                ])
                st.dataframe(preview_df, use_container_width=True)

                c1, c2, c3 = st.columns(3)
                with c1:
                    if st.button("保存配料估算到本周菜单", type="primary"):
                        save_week_menu(conn, week_key, local_rows, replace=True)
                        st.success("已保存到本周菜单。")
                        rerun()
                with c2:
                    if st.button("OpenAI联网搜索估算并保存"):
                        key = api_key_input or get_openai_key()
                        if not key:
                            st.error("缺少 OpenAI API key。可以先用本地配料估算。")
                        else:
                            with st.spinner("正在调用 OpenAI API + web search 估算整周菜单……"):
                                try:
                                    ai_rows = call_openai_estimator(key, model, local_rows, use_web_search=True)
                                    save_week_menu(conn, week_key, ai_rows, replace=True)
                                    st.success(f"联网估算完成，已保存 {len(ai_rows)} 个菜品。")
                                    rerun()
                                except Exception as e:
                                    st.error(str(e))
                with c3:
                    if st.button("OpenAI不联网精细估算并保存"):
                        key = api_key_input or get_openai_key()
                        if not key:
                            st.error("缺少 OpenAI API key。")
                        else:
                            with st.spinner("正在调用 OpenAI API 估算整周菜单……"):
                                try:
                                    ai_rows = call_openai_estimator(key, model, local_rows, use_web_search=False)
                                    save_week_menu(conn, week_key, ai_rows, replace=True)
                                    st.success(f"AI估算完成，已保存 {len(ai_rows)} 个菜品。")
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
            st.caption("CO₂e 不在每个菜品卡片里显示；仍保存在数据库和导出 CSV 中，作为内部参考。")

            with st.expander("编辑本周菜单营养值", expanded=False):
                menu_df = pd.DataFrame([dict(r) for r in menu_rows])[["id", "day", "name", "price", *MACRO_KEYS, "source"]]
                edited_menu = st.data_editor(menu_df, num_rows="dynamic", use_container_width=True, key="menu_editor")
                if st.button("保存本周菜单编辑"):
                    update_menu_items_from_df(conn, edited_menu, week_key)
                    st.success("本周菜单已更新。")
                    rerun()

            day_filter = st.selectbox("按日期筛选", ["全部"] + sorted({r["day"] for r in menu_rows}))
            rows_show = [r for r in menu_rows if day_filter == "全部" or r["day"] == day_filter]
            for r in rows_show:
                with st.container(border=True):
                    top_cols = st.columns([3, 1, 1, 1, 1, 1, 1])
                    top_cols[0].markdown(f"**{r['name']}**  \n{r['day']} · {r['price']} · {r['source']}")
                    top_cols[1].metric("kcal", f"{r['kcal']:.0f}")
                    top_cols[2].metric("P", f"{r['protein']:.0f}")
                    top_cols[3].metric("C", f"{r['carbs']:.0f}")
                    top_cols[4].metric("F", f"{r['fat']:.0f}")
                    top_cols[5].metric("Fiber", f"{r['fiber']:.0f}")
                    with top_cols[6]:
                        meal = st.selectbox("餐次", ["午餐", "晚餐", "早餐", "加餐"], key=f"meal_menu_{r['id']}")
                        if st.button("加入当天", key=f"add_menu_{r['id']}"):
                            add_record(conn, record_date, meal, r["name"], 1.0, macro_from_obj(r), f"Mensa/{week_key}")
                            save_day_settings(conn, record_date, target, preset.get("day_type", preset_name))
                            st.success("已加入当天记录。")
                            rerun()

    with tab_day:
        st.subheader(f"每天记录：{record_date}")
        records = get_record_rows(conn, record_date)
        total = total_for_records(records)
        render_metric_row(total, target, show_co2_total=False)
        if records:
            rec_csv = rows_to_csv(records, ["record_date", "meal", "name", "grams", *MACRO_KEYS, "source"])
            st.download_button("导出当天记录 CSV", rec_csv, file_name=f"daily_record_{record_date}.csv", mime="text/csv")
            for r in records:
                cols = st.columns([1, 3, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8])
                cols[0].write(r["meal"])
                cols[1].write(r["name"])
                cols[2].write(f"{r['grams']:.0f}" if r["grams"] else "-")
                cols[3].write(f"{r['kcal']:.0f}")
                cols[4].write(f"P {r['protein']:.0f}")
                cols[5].write(f"C {r['carbs']:.0f}")
                cols[6].write(f"F {r['fat']:.0f}")
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
                add_record(conn, record_date, "加餐", "乳清蛋白粉", 40, macro, "食物数据库/40g")
                save_day_settings(conn, record_date, target, preset.get("day_type", preset_name))
                st.success("已加入 40g 乳清蛋白粉。")
                rerun()

        st.divider()
        st.subheader("从食物数据库添加")
        q = st.text_input("搜索食物：中文/英文/德文/拼音首字母", placeholder="例如 sjxr, ymp, rqdbf, Magerquark")
        foods = get_foods(conn, q)
        meal_food = st.selectbox("餐次", ["晚餐", "早餐", "午餐", "加餐"], key="meal_food")
        grams = st.number_input("克数", min_value=0.0, value=100.0, step=10.0)
        for f in foods[:30]:
            cols = st.columns([3, 1, 1, 1, 1, 1])
            cols[0].write(f"**{f['name']}** · {f['category']} · {f['initials']}")
            macro = scale_food(f, grams)
            cols[1].write(f"{macro['kcal']:.0f} kcal")
            cols[2].write(f"P {macro['protein']:.0f}")
            cols[3].write(f"C {macro['carbs']:.0f}")
            cols[4].write(f"F {macro['fat']:.0f}")
            if cols[5].button("添加", key=f"add_food_{f['id']}"):
                add_record(conn, record_date, meal_food, f["name"], grams, macro, "食物数据库")
                save_day_settings(conn, record_date, target, preset.get("day_type", preset_name))
                st.success("已添加。")
                rerun()

    with tab_food:
        st.subheader("食物数据库")
        st.info("你上传的 CSV 已作为默认种子数据导入。原 CSV 里部分中文名已经变成问号，无法100%还原的条目保留为“导入食物_xxx”，可在下面直接改名。")
        q2 = st.text_input("搜索数据库", key="db_search")
        foods = get_foods(conn, q2)
        food_df = pd.DataFrame([dict(f) for f in foods])
        if not food_df.empty:
            edit_cols = ["id", "name", "kcal", "protein", "carbs", "fat", "fiber", "co2e_g", "category", "initials", "source"]
            food_df = food_df[edit_cols]
            food_df.insert(0, "复制", False)
            food_df.insert(0, "删除", False)
            edited_foods = st.data_editor(
                food_df,
                num_rows="dynamic",
                use_container_width=True,
                key="foods_editor",
                column_config={
                    "id": st.column_config.NumberColumn("id", disabled=True),
                    "删除": st.column_config.CheckboxColumn("删除"),
                    "复制": st.column_config.CheckboxColumn("复制"),
                    "kcal": st.column_config.NumberColumn("kcal/100g"),
                    "protein": st.column_config.NumberColumn("蛋白/100g"),
                    "carbs": st.column_config.NumberColumn("碳水/100g"),
                    "fat": st.column_config.NumberColumn("脂肪/100g"),
                    "fiber": st.column_config.NumberColumn("纤维/100g"),
                    "co2e_g": st.column_config.NumberColumn("CO₂e/100g", help="内部参考/导出字段"),
                },
            )
            if st.button("保存食物库编辑/复制/删除", type="primary"):
                saved, copied, deleted = save_foods_from_editor(conn, edited_foods)
                st.success(f"已保存 {saved} 行，复制 {copied} 行，删除 {deleted} 行。")
                rerun()
        else:
            st.warning("食物库为空或没有搜索结果。")

        st.divider()
        st.subheader("在线搜索并导入食物")
        st.caption("Open Food Facts 更适合有包装/品牌的食品；USDA 更适合基础食材。Mensa 熟菜仍建议用 OpenAI 联网估算或手动校正。")
        online_query = st.text_input("搜索关键词", placeholder="例如 whey protein, Magerquark, chicken breast, rice, Bauernbrot")
        usda_key = st.text_input("USDA API key，可留空用 DEMO_KEY", value="", type="password")
        c1, c2 = st.columns(2)
        if c1.button("搜索 Open Food Facts") and online_query:
            try:
                st.session_state["online_food_results"] = search_openfoodfacts(online_query, 12)
                st.session_state["online_food_source"] = "Open Food Facts"
            except Exception as e:
                st.error(f"Open Food Facts 搜索失败：{e}")
        if c2.button("搜索 USDA FoodData Central") and online_query:
            try:
                st.session_state["online_food_results"] = search_usda(online_query, usda_key, 12)
                st.session_state["online_food_source"] = "USDA"
            except Exception as e:
                st.error(f"USDA 搜索失败：{e}")
        results = st.session_state.get("online_food_results", [])
        if results:
            st.write(f"搜索结果：{st.session_state.get('online_food_source', '')}")
            for i, item in enumerate(results):
                cols = st.columns([4, 1, 1, 1, 1, 1])
                cols[0].write(f"**{item['name']}** · {item.get('source','')}")
                cols[1].write(f"{item['kcal']:.0f} kcal")
                cols[2].write(f"P {item['protein']:.1f}")
                cols[3].write(f"C {item['carbs']:.1f}")
                cols[4].write(f"F {item['fat']:.1f}")
                if cols[5].button("导入", key=f"import_online_{i}"):
                    upsert_food(conn, item)
                    st.success(f"已导入：{item['name']}")
                    rerun()

        st.divider()
        st.subheader("从 CSV 导入食物库")
        csv_file = st.file_uploader("上传食物库 CSV/TSV", type=["csv", "txt", "tsv"], key="food_csv_uploader")
        if csv_file is not None and st.button("导入这个 CSV"):
            try:
                count = import_foods_from_uploaded_csv(conn, csv_file)
                st.success(f"已导入/更新 {count} 个食物。")
                rerun()
            except Exception as e:
                st.error(f"CSV 导入失败：{e}")

    with tab_history:
        st.subheader("历史记录、周/月总结与体重反馈")
        c1, c2, c3, c4 = st.columns(4)
        metric_date = c1.date_input("体重记录日期", value=date.today(), key="metric_date").isoformat()
        weight_kg = c2.number_input("体重 kg", value=float(USER_PROFILE["start_weight_kg"]), step=0.1)
        waist_cm = c3.number_input("腰围 cm，可选", value=0.0, step=0.5)
        notes = c4.text_input("备注", value="")
        if st.button("保存/更新体重记录"):
            upsert_body_metric(conn, metric_date, weight_kg, waist_cm, notes)
            st.success("体重记录已保存。")
            rerun()

        today = date.fromisoformat(record_date)
        summary_mode = st.radio("汇总范围", ["本周", "本月", "自定义"], horizontal=True)
        if summary_mode == "本周":
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
        elif summary_mode == "本月":
            start = today.replace(day=1)
            if start.month == 12:
                end = date(start.year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(start.year, start.month + 1, 1) - timedelta(days=1)
        else:
            cc1, cc2 = st.columns(2)
            start = cc1.date_input("开始日期", value=today - timedelta(days=7), key="hist_start")
            end = cc2.date_input("结束日期", value=today, key="hist_end")
        records_range = get_records_between(conn, start.isoformat(), end.isoformat())
        daily_df = records_to_daily_df(records_range)
        weights = get_body_metrics(conn, start.isoformat(), end.isoformat())

        if daily_df.empty:
            st.info("这个范围还没有饮食记录。")
        else:
            st.write(f"范围：{start.isoformat()} 至 {end.isoformat()}")
            st.dataframe(daily_df, use_container_width=True)
            avg = {k: daily_df[k].mean() for k in MACRO_KEYS}
            st.write("平均每日摄入")
            render_metric_row(avg, target, show_co2_total=False)
            st.line_chart(daily_df.set_index("record_date")[["kcal", "protein", "carbs", "fat"]])
            summary_csv = daily_df.to_csv(index=False)
            st.download_button("导出汇总 CSV", summary_csv, file_name=f"summary_{start}_{end}.csv", mime="text/csv")

        if weights:
            weight_df = pd.DataFrame([dict(w) for w in weights])
            st.write("体重记录")
            st.dataframe(weight_df[["metric_date", "weight_kg", "waist_cm", "notes"]], use_container_width=True)
            st.line_chart(weight_df.set_index("metric_date")[["weight_kg"]])
        else:
            st.info("这个范围还没有体重记录。")

        st.divider()
        st.subheader("每周摄入修改建议")
        all_weights = get_body_metrics(conn)
        advice = make_weekly_advice(daily_df, all_weights, target)
        for a in advice:
            st.write("- " + a)

    st.caption("提示：Mensa 菜品没有固定称重。联网搜索和数据库导入只能提高参考质量，最终仍建议结合体重趋势修正。")


if __name__ == "__main__":
    main()
