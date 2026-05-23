#!/usr/bin/env python3
"""
Astrologist Knowledge Graph & Inference Engine
==============================================
Phase 3: Normalize extracted rules into structured knowledge graph
Phase 4: Given birth chart, match rules and generate reading

MECE Architecture (7 layers):
  1. Atomic Placements — planet×sign, planet×house
  2. Planetary Dignities — essential + accidental (hardcoded classical rules)
  3. Relationships — aspects, house rulerships
  4. Patterns — element/mode balance, aspect configs, stelliums
  5. Synthesis — priority rules, conflict resolution
  6. Lack Detection — missing elements, modes, empty houses
  7. Reading Generation — narrative from all layers
"""

import json
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path("/Users/lihuidong/Astrologist/model")
OUTPUT_DIR = BASE_DIR / "output"

# ═══════════════════════════════════════════════════════
# ENTITY NORMALIZATION TABLES
# ═══════════════════════════════════════════════════════

PLANET_NAMES = {
    "太阳": "sun", "日": "sun",
    "月亮": "moon", "月": "moon",
    "水星": "mercury",
    "金星": "venus",
    "火星": "mars",
    "木星": "jupiter",
    "土星": "saturn",
    "天王星": "uranus",
    "海王星": "neptune",
    "冥王星": "pluto", "冥王": "pluto",
    "凯龙星": "chiron", "凯龙": "chiron",
    "上升": "asc", "上升星座": "asc",
    "天顶": "mc", "中天": "mc",
    "北交": "north_node", "北交点": "north_node",
    "南交": "south_node", "南交点": "south_node",
}

SIGN_NAMES = {
    "白羊座": "aries", "牡羊座": "aries", "白羊": "aries", "牡羊": "aries",
    "金牛座": "taurus", "金牛": "taurus",
    "双子座": "gemini", "双子": "gemini",
    "巨蟹座": "cancer", "巨蟹": "cancer",
    "狮子座": "leo", "狮子": "leo",
    "处女座": "virgo", "处女": "virgo", "处理做": "virgo",
    "天秤座": "libra", "天秤": "libra",
    "天蠍座": "scorpio", "天蝎座": "scorpio", "天蠍": "scorpio", "天蝎": "scorpio", "天卸做": "scorpio",
    "射手座": "sagittarius", "射手": "sagittarius", "社手": "sagittarius",
    "摩羯座": "capricorn", "魔羯座": "capricorn", "摩羯": "capricorn", "魔羯": "capricorn",
    "水瓶座": "aquarius", "水瓶": "aquarius", "水平": "aquarius",
    "双鱼座": "pisces", "双鱼": "pisces",
}

HOUSE_NUMBERS = {
    "一宫": 1, "1宫": 1, "第一宫": 1,
    "二宫": 2, "2宫": 2, "第二宫": 2,
    "三宫": 3, "3宫": 3, "第三宫": 3,
    "四宫": 4, "4宫": 4, "第四宫": 4,
    "五宫": 5, "5宫": 5, "第五宫": 5,
    "六宫": 6, "6宫": 6, "第六宫": 6,
    "七宫": 7, "7宫": 7, "第七宫": 7,
    "八宫": 8, "8宫": 8, "第八宫": 8,
    "九宫": 9, "9宫": 9, "第九宫": 9,
    "十宫": 10, "10宫": 10, "第十宫": 10,
    "十一宫": 11, "11宫": 11, "第十一宫": 11,
    "十二宫": 12, "12宫": 12, "第十二宫": 12,
}

ASPECT_TYPES = {
    "合相": "conjunction", "0度": "conjunction",
    "六合": "sextile", "六分相": "sextile", "60度": "sextile",
    "四分相": "square", "刑": "square", "90度": "square",
    "三分相": "trine", "拱": "trine", "120度": "trine",
    "对分相": "opposition", "冲": "opposition", "对分": "opposition", "180度": "opposition",
    "大三角": "grand_trine", "风筝": "kite", "大十字": "grand_cross",
    "T三角": "t_square", "神秘矩形": "mystic_rectangle",
}

# ═══════════════════════════════════════════════════════
# LAYER 2: DIGNITY TABLES (hardcoded classical rules)
# ═══════════════════════════════════════════════════════

# Essential dignity: each sign's ruling planet, detriment, exaltation, fall
ESSENTIAL_DIGNITY = {
    "aries":      {"ruler": "mars",      "detriment": "venus",    "exaltation": "sun",       "fall": "saturn"},
    "taurus":     {"ruler": "venus",     "detriment": "mars",     "exaltation": "moon",      "fall": "uranus"},
    "gemini":     {"ruler": "mercury",   "detriment": "jupiter",  "exaltation": "north_node","fall": "south_node"},
    "cancer":     {"ruler": "moon",      "detriment": "saturn",   "exaltation": "jupiter",   "fall": "mars"},
    "leo":        {"ruler": "sun",       "detriment": "saturn",   "exaltation": "pluto",     "fall": "mercury"},
    "virgo":      {"ruler": "mercury",   "detriment": "jupiter",  "exaltation": "mercury",   "fall": "venus"},
    "libra":      {"ruler": "venus",     "detriment": "mars",     "exaltation": "saturn",    "fall": "sun"},
    "scorpio":    {"ruler": "pluto",     "detriment": "venus",    "exaltation": "uranus",    "fall": "moon"},
    "sagittarius":{"ruler": "jupiter",   "detriment": "mercury",  "exaltation": "south_node","fall": "north_node"},
    "capricorn":  {"ruler": "saturn",    "detriment": "moon",     "exaltation": "mars",      "fall": "jupiter"},
    "aquarius":   {"ruler": "uranus",    "detriment": "sun",      "exaltation": "mercury",   "fall": "pluto"},
    "pisces":     {"ruler": "neptune",   "detriment": "mercury",  "exaltation": "venus",     "fall": "mercury"},
}

# Accidental dignity: house joys (planet's natural affinity with a house)
PLANETARY_JOY = {
    "sun": 9, "moon": 3, "mercury": 1, "venus": 5,
    "mars": 6, "jupiter": 11, "saturn": 12,
}

# Angular houses (1,4,7,10) give accidental dignity
ANGULAR_HOUSES = {1, 4, 7, 10}
SUCCEDENT_HOUSES = {2, 5, 8, 11}
CADENT_HOUSES = {3, 6, 9, 12}

# ── Triplicity rulers (三分主星) ──
# Fire/Earth/Air/Water — each element has a Day ruler, Night ruler, and Participating ruler
# Sect: Day = Sun above horizon (houses 7-12), Night = Sun below (houses 1-6)
TRIPLICITY = {
    "fire":   {"day": "sun",       "night": "jupiter",  "participating": "saturn"},
    "earth":  {"day": "venus",     "night": "moon",     "participating": "mars"},
    "air":    {"day": "saturn",    "night": "mercury",  "participating": "jupiter"},
    "water":  {"day": "venus",     "night": "mars",     "participating": "moon"},
}

# ── Ptolemaic terms (托勒密界) ──
# Each sign has 5 unequal degree segments, each ruled by a planet
# Format: list of (end_degree, ruler) — last segment always ends at 30°
PTOLEMAIC_TERMS = {
    "aries":      [(6, "jupiter"), (12, "venus"),   (20, "mercury"), (25, "mars"),    (30, "saturn")],
    "taurus":     [(8, "venus"),   (14, "mercury"), (22, "jupiter"), (27, "saturn"),  (30, "mars")],
    "gemini":     [(6, "mercury"), (12, "jupiter"), (17, "venus"),   (24, "mars"),    (30, "saturn")],
    "cancer":     [(7, "mars"),    (13, "venus"),   (19, "mercury"), (26, "jupiter"), (30, "saturn")],
    "leo":        [(6, "jupiter"), (11, "mercury"), (18, "saturn"),  (24, "venus"),   (30, "mars")],
    "virgo":      [(7, "mercury"), (13, "venus"),   (18, "jupiter"), (24, "saturn"),  (30, "mars")],
    "libra":      [(6, "saturn"),  (14, "mercury"), (21, "jupiter"), (28, "venus"),   (30, "mars")],
    "scorpio":    [(7, "mars"),    (11, "venus"),   (19, "mercury"), (24, "jupiter"), (30, "saturn")],
    "sagittarius":[(12, "jupiter"),(17, "venus"),   (21, "mercury"), (26, "saturn"),  (30, "mars")],
    "capricorn":  [(7, "mercury"), (14, "jupiter"), (22, "venus"),   (26, "saturn"),  (30, "mars")],
    "aquarius":   [(7, "mercury"), (13, "venus"),   (20, "jupiter"), (25, "mars"),    (30, "saturn")],
    "pisces":     [(12, "venus"),  (16, "jupiter"), (19, "mercury"), (28, "mars"),    (30, "saturn")],
}

# ── Chaldean faces/decans (迦勒底面) ──
# Each sign has 3 faces of 10°, rulers follow Chaldean order: Mars→Sun→Venus→Mercury→Moon→Saturn→Jupiter
CHALDEAN_ORDER = ["mars", "sun", "venus", "mercury", "moon", "saturn", "jupiter"]
CHALDEAN_FACES = {}
# Start: Aries 0-10° = Mars, then cycle
_signs_order = ["aries", "taurus", "gemini", "cancer", "leo", "virgo",
                "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces"]
_chaldean_idx = 0  # Mars for Aries face 1
for _sign in _signs_order:
    CHALDEAN_FACES[_sign] = []
    for _face in range(3):
        _ruler = CHALDEAN_ORDER[_chaldean_idx % 7]
        CHALDEAN_FACES[_sign].append(_ruler)
        _chaldean_idx += 1


def get_triplicity_ruler(sign: str, sect: str) -> str | None:
    """Get the triplicity ruler for a sign based on sect (day/night)."""
    elem = SIGN_ELEMENT.get(sign, "")
    if not elem or elem not in TRIPLICITY:
        return None
    t = TRIPLICITY[elem]
    if sect == "day":
        return t["day"]
    elif sect == "night":
        return t["night"]
    return None


def get_term_ruler(sign: str, degree: float) -> str | None:
    """Get the Ptolemaic term ruler for a planet's degree in a sign."""
    if sign not in PTOLEMAIC_TERMS:
        return None
    for end_deg, ruler in PTOLEMAIC_TERMS[sign]:
        if degree <= end_deg:
            return ruler
    return None


def get_face_ruler(sign: str, degree: float) -> str | None:
    """Get the Chaldean face/decan ruler for a planet's degree in a sign."""
    if sign not in CHALDEAN_FACES:
        return None
    face_idx = min(2, int(degree / 10))  # 0-10° → 0, 10-20° → 1, 20-30° → 2
    return CHALDEAN_FACES[sign][face_idx]


DIGNITY_LABELS = {
    "ruler": "入庙",
    "exaltation": "擢升",
    "detriment": "失势",
    "fall": "陷落",
    "triplicity": "三分主星",
    "term": "界",
    "face": "面",
    "joy": "喜乐",
    "angular": "角宫",
}

# ═══════════════════════════════════════════════════════
# LAYER 4: ELEMENT & MODE CLASSIFICATION
# ═══════════════════════════════════════════════════════

SIGN_ELEMENT = {
    "aries": "fire", "leo": "fire", "sagittarius": "fire",
    "taurus": "earth", "virgo": "earth", "capricorn": "earth",
    "gemini": "air", "libra": "air", "aquarius": "air",
    "cancer": "water", "scorpio": "water", "pisces": "water",
}

SIGN_MODE = {
    "aries": "cardinal", "cancer": "cardinal", "libra": "cardinal", "capricorn": "cardinal",
    "taurus": "fixed", "leo": "fixed", "scorpio": "fixed", "aquarius": "fixed",
    "gemini": "mutable", "virgo": "mutable", "sagittarius": "mutable", "pisces": "mutable",
}

ELEMENT_CN = {"fire": "火", "earth": "土", "air": "风", "water": "水"}
MODE_CN = {"cardinal": "开创", "fixed": "固定", "mutable": "变动"}

# Signs for house cusp rulership mapping
SIGN_ORDER = ["aries", "taurus", "gemini", "cancer", "leo", "virgo",
              "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces"]


def get_house_ruler(house_cusp_sign: str) -> str:
    """Get the planet that rules a sign on a house cusp."""
    return ESSENTIAL_DIGNITY.get(house_cusp_sign, {}).get("ruler", "")


# ═══════════════════════════════════════════════════════
# ENTITY NORMALIZATION
# ═══════════════════════════════════════════════════════

def normalize_entities(rule: dict) -> dict:
    """Resolve Chinese entity names to standardized keys and enrich rule."""
    text = rule.get("condition", "") + " " + rule.get("interpretation", "")
    text += " " + rule.get("entity", "")

    rule["planets"] = []
    rule["signs"] = []
    rule["houses"] = []
    rule["aspects"] = []
    rule["dignities"] = []
    rule["tags"] = set()

    for cn, en in PLANET_NAMES.items():
        if cn in text:
            rule["planets"].append(en)
            rule["tags"].add(f"planet:{en}")

    for cn, en in SIGN_NAMES.items():
        if cn in text:
            rule["signs"].append(en)
            rule["tags"].add(f"sign:{en}")

    for cn, num in HOUSE_NUMBERS.items():
        if cn in text:
            rule["houses"].append(num)
            rule["tags"].add(f"house:{num}")

    for cn, en in ASPECT_TYPES.items():
        if cn in text:
            rule["aspects"].append(en)
            rule["tags"].add(f"aspect:{en}")

    # Detect generic aspect references
    if not rule["aspects"]:
        for p in ["有相位", "的相位", "成相位", "形成相位", "相位关系"]:
            if p in text:
                rule["aspects"].append("general")
                rule["tags"].add("aspect:general")
                break

    # Detect dignity references
    dignity_cn = {
        "入庙": "ruler", "失势": "detriment", "擢升": "exaltation",
        "陷落": "fall", "喜乐": "joy", "入旺": "exaltation",
        "三分主星": "triplicity", "三位一体": "triplicity",
        "托勒密界": "term", "埃及界": "term",
        "十度区分": "face", "外观": "face",
    }
    for cn, en in dignity_cn.items():
        if cn in text:
            rule["dignities"].append(en)
            rule["tags"].add(f"dignity:{en}")

    # Deduplicate entity lists (text may mention same entity multiple times)
    rule["planets"] = list(dict.fromkeys(rule["planets"]))
    rule["signs"] = list(dict.fromkeys(rule["signs"]))
    rule["houses"] = list(dict.fromkeys(rule["houses"]))
    rule["aspects"] = list(dict.fromkeys(rule["aspects"]))
    rule["dignities"] = list(dict.fromkeys(rule["dignities"]))
    rule["tags"] = list(rule["tags"])
    return rule


# ═══════════════════════════════════════════════════════
# LAYER 4: CHART META COMPUTATION
# ═══════════════════════════════════════════════════════

def compute_chart_meta(chart_data: dict) -> dict:
    """Compute derived chart properties: elements, modes, dignities, patterns, lacks."""
    planets = chart_data.get("planets", {})
    asc_sign = chart_data.get("angles", {}).get("asc", "")

    # Count elements and modes
    elements = defaultdict(int)
    modes = defaultdict(int)
    planet_dignities = {}

    for pname, pdata in planets.items():
        sign = pdata.get("sign", "")
        house = pdata.get("house", 0)
        if sign:
            elem = SIGN_ELEMENT.get(sign)
            mode = SIGN_MODE.get(sign)
            if elem:
                elements[elem] += 1
            if mode:
                modes[mode] += 1

            # Determine sect for triplicity
            sun_house = planets.get("sun", {}).get("house", 0)
            sect = "day" if 7 <= sun_house <= 12 else "night"
            degree = pdata.get("degree", 15.0)  # Default mid-sign if unknown

            # Compute dignity for this planet
            dignities = []
            dignity_info = ESSENTIAL_DIGNITY.get(sign, {})
            if dignity_info.get("ruler") == pname:
                dignities.append("ruler")
            elif dignity_info.get("detriment") == pname:
                dignities.append("detriment")
            if dignity_info.get("exaltation") == pname:
                dignities.append("exaltation")
            elif dignity_info.get("fall") == pname:
                dignities.append("fall")

            # Triplicity (三分主星)
            triplicity_ruler = get_triplicity_ruler(sign, sect)
            if triplicity_ruler == pname:
                dignities.append("triplicity")

            # Ptolemaic term (界)
            term_ruler = get_term_ruler(sign, degree)
            if term_ruler == pname:
                dignities.append("term")

            # Chaldean face/decan (面)
            face_ruler = get_face_ruler(sign, degree)
            if face_ruler == pname:
                dignities.append("face")

            # Accidental dignity: joy
            if PLANETARY_JOY.get(pname) == house:
                dignities.append("joy")

            # Accidental dignity: angularity
            if house in ANGULAR_HOUSES:
                dignities.append("angular")
            elif house in CADENT_HOUSES:
                dignities.append("cadent")

            if dignities:
                planet_dignities[pname] = dignities

    # Add ascendant to element/mode counts
    if asc_sign:
        elem = SIGN_ELEMENT.get(asc_sign)
        mode = SIGN_MODE.get(asc_sign)
        if elem:
            elements[elem] += 1
        if mode:
            modes[mode] += 1

    # Detect excess (>3 planets in one element/mode) and lack (0 planets)
    element_excess = [e for e, c in elements.items() if c >= 3]
    element_lack = [e for e in ["fire", "earth", "air", "water"] if elements.get(e, 0) == 0]
    mode_excess = [m for m, c in modes.items() if c >= 4]
    mode_lack = [m for m in ["cardinal", "fixed", "mutable"] if modes.get(m, 0) == 0]

    # Detect stelliums (3+ planets in one sign or house)
    sign_counts = defaultdict(int)
    house_counts = defaultdict(int)
    for pname, pdata in planets.items():
        sign = pdata.get("sign", "")
        house = pdata.get("house", 0)
        if sign:
            sign_counts[sign] += 1
        if house:
            house_counts[house] += 1

    stellium_signs = [s for s, c in sign_counts.items() if c >= 3]
    stellium_houses = [h for h, c in house_counts.items() if c >= 3]

    # Hemisphere emphasis
    above = sum(1 for p in planets.values() if 7 <= p.get("house", 0) <= 12)
    below = sum(1 for p in planets.values() if 1 <= p.get("house", 0) <= 6)
    east = sum(1 for p in planets.values() if p.get("house", 0) in (10, 11, 12, 1, 2, 3))
    west = sum(1 for p in planets.values() if p.get("house", 0) in (4, 5, 6, 7, 8, 9))

    return {
        "elements": dict(elements),
        "modes": dict(modes),
        "element_excess": element_excess,
        "element_lack": element_lack,
        "mode_excess": mode_excess,
        "mode_lack": mode_lack,
        "stellium_signs": stellium_signs,
        "stellium_houses": stellium_houses,
        "planet_dignities": planet_dignities,
        "hemisphere": {"above": above, "below": below, "east": east, "west": west},
    }


def detect_aspect_configs(chart_data: dict) -> list[dict]:
    """Detect aspect configurations (grand trine, t-square, etc.) from chart aspects."""
    aspects = chart_data.get("aspects", [])
    planets = chart_data.get("planets", {})

    # Build adjacency: planet -> {connected_planet: aspect_type}
    adj = defaultdict(dict)
    for asp in aspects:
        a, b, t = asp.get("a", ""), asp.get("b", ""), asp.get("type", "")
        if a and b and t:
            adj[a][b] = t
            adj[b][a] = t

    configs = []

    # Grand Trine: 3 planets in same element, all mutually in trine
    planet_list = list(planets.keys())
    for i in range(len(planet_list)):
        for j in range(i + 1, len(planet_list)):
            for k in range(j + 1, len(planet_list)):
                pi, pj, pk = planet_list[i], planet_list[j], planet_list[k]
                # Must share same element
                si = planets.get(pi, {}).get("sign", "")
                sj = planets.get(pj, {}).get("sign", "")
                sk = planets.get(pk, {}).get("sign", "")
                if not (si and sj and sk):
                    continue
                if SIGN_ELEMENT.get(si) != SIGN_ELEMENT.get(sj) or \
                   SIGN_ELEMENT.get(sj) != SIGN_ELEMENT.get(sk):
                    continue
                tij = adj.get(pi, {}).get(pj, "")
                tjk = adj.get(pj, {}).get(pk, "")
                tki = adj.get(pk, {}).get(pi, "")
                trines = sum(1 for t in (tij, tjk, tki) if t == "trine")
                if trines >= 2:
                    elem = SIGN_ELEMENT.get(si, "")
                    configs.append({"type": "grand_trine", "planets": [pi, pj, pk],
                                    "element": elem})

    # T-Square: 2 planets in opposition, both square a third
    for pi in planet_list:
        for pj in planet_list:
            if pi >= pj:
                continue
            if adj.get(pi, {}).get(pj) == "opposition":
                # Find common square planet
                for pk in planet_list:
                    if pk in (pi, pj):
                        continue
                    tik = adj.get(pi, {}).get(pk, "")
                    tjk = adj.get(pj, {}).get(pk, "")
                    if tik == "square" and tjk == "square":
                        configs.append({"type": "t_square", "planets": [pi, pj, pk],
                                        "apex": pk})

    # Grand Cross: 2 oppositions, 4 squares
    for i in range(len(planet_list)):
        for j in range(i + 1, len(planet_list)):
            pi, pj = planet_list[i], planet_list[j]
            if adj.get(pi, {}).get(pj) == "opposition":
                for k in range(len(planet_list)):
                    for l in range(k + 1, len(planet_list)):
                        pk, pl = planet_list[k], planet_list[l]
                        if adj.get(pk, {}).get(pl) == "opposition":
                            # All 4 planets involved in a cross
                            involved = {pi, pj, pk, pl}
                            if len(involved) == 4:
                                square_count = 0
                                for a in involved:
                                    for b in involved:
                                        if a < b and adj.get(a, {}).get(b) == "square":
                                            square_count += 1
                                if square_count >= 4:
                                    configs.append({"type": "grand_cross",
                                                    "planets": list(involved)})

    return configs


# ═══════════════════════════════════════════════════════
# LAYER 3b: HOUSE RULERSHIP CHAINS
# ═══════════════════════════════════════════════════════

# Natural sign-house affinity (sign = natural ruler of house)
SIGN_HOUSE_AFFINITY = {
    "aries": 1, "taurus": 2, "gemini": 3, "cancer": 4,
    "leo": 5, "virgo": 6, "libra": 7, "scorpio": 8,
    "sagittarius": 9, "capricorn": 10, "aquarius": 11, "pisces": 12,
}

HOUSE_AREAS = {
    1: "自我形象、人格面具、人生方向",
    2: "财务、价值观、自我价值",
    3: "沟通、学习、兄弟姐妹、短途旅行",
    4: "家庭、根源、父亲、不动产",
    5: "创造力、恋爱、子女、投机",
    6: "健康、日常工作、服务、宠物",
    7: "婚姻、合伙、一对一关系、公开敌人",
    8: "他人资源、深度心理、生死、转化",
    9: "高等教育、哲学、长途旅行、信仰",
    10: "事业、社会地位、母亲、人生目标",
    11: "社群、朋友、理想、团体",
    12: "潜意识、隐秘、灵性、业力、孤立",
}

PLANET_CN_REVERSE = {v: k for k, v in PLANET_NAMES.items()}
# Only keep the standard 1-char forms
PLANET_DISPLAY = {
    "sun": "太阳", "moon": "月亮", "mercury": "水星", "venus": "金星",
    "mars": "火星", "jupiter": "木星", "saturn": "土星",
    "uranus": "天王星", "neptune": "海王星", "pluto": "冥王星",
    "north_node": "北交点", "south_node": "南交点",
    "asc": "上升", "mc": "天顶", "chiron": "凯龙星",
}
SIGN_DISPLAY = {v: k for k, v in SIGN_NAMES.items() if "座" in k}

HOUSE_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
            7: "七", 8: "八", 9: "九", 10: "十", 11: "十一", 12: "十二"}


def get_whole_sign_houses(asc_sign: str) -> dict:
    """Compute Whole Sign house cusps. House N = asc_sign_index + N - 1."""
    if asc_sign not in SIGN_ORDER:
        return {}
    asc_idx = SIGN_ORDER.index(asc_sign)
    houses = {}
    for h in range(1, 13):
        sign_idx = (asc_idx + h - 1) % 12
        houses[h] = SIGN_ORDER[sign_idx]
    return houses


def compute_rulerships(chart_data: dict) -> list[dict]:
    """Trace house rulership chains and generate interpretations.

    For each house: sign on cusp → ruler planet → where is that ruler?
    Generates a rulership analysis showing how each life area is managed.
    """
    asc_sign = chart_data.get("angles", {}).get("asc", "")
    if not asc_sign:
        return []

    houses = get_whole_sign_houses(asc_sign)
    planets = chart_data.get("planets", {})
    aspects = chart_data.get("aspects", [])

    # Build aspect lookup for ruler
    ruler_aspects = defaultdict(list)
    for asp in aspects:
        a, b, t = asp.get("a", ""), asp.get("b", ""), asp.get("type", "")
        if a and b:
            ruler_aspects[a].append({"with": b, "type": t})
            ruler_aspects[b].append({"with": a, "type": t})

    rulerships = []
    for house_num in range(1, 13):
        cusp_sign = houses.get(house_num, "")
        ruler = ESSENTIAL_DIGNITY.get(cusp_sign, {}).get("ruler", "")
        if not ruler:
            continue

        # Where is the ruler placed?
        ruler_data = planets.get(ruler, {})
        ruler_sign = ruler_data.get("sign", "")
        ruler_house = ruler_data.get("house", 0)
        ruler_display = PLANET_DISPLAY.get(ruler, ruler)
        cusp_display = SIGN_DISPLAY.get(cusp_sign, cusp_sign)
        area = HOUSE_AREAS.get(house_num, "")

        if not ruler_sign:
            # Ruler not in known planets — show what we know but note limitation
            interpretation = (
                f"第{HOUSE_CN.get(house_num, house_num)}宫（{area}）宫头落{cusp_display}，"
                f"宫主星{ruler_display}未在已知星体列表中，落点待查。"
            )
            rulerships.append({
                "house": house_num,
                "cusp_sign": cusp_sign,
                "ruler": ruler,
                "ruler_sign": "",
                "ruler_house": 0,
                "dignities": [],
                "quality": "未知",
                "interpretation": interpretation,
            })
            continue

        # Dignity of the ruler
        ruler_dignities = []
        if ruler_sign:
            d_info = ESSENTIAL_DIGNITY.get(ruler_sign, {})
            if d_info.get("ruler") == ruler:
                ruler_dignities.append("入庙")
            elif d_info.get("detriment") == ruler:
                ruler_dignities.append("失势")
            if d_info.get("exaltation") == ruler:
                ruler_dignities.append("擢升")
            elif d_info.get("fall") == ruler:
                ruler_dignities.append("陷落")

            # Triplicity/term/face for ruler
            sun_house = planets.get("sun", {}).get("house", 0)
            sect = "day" if 7 <= sun_house <= 12 else "night"
            ruler_degree = ruler_data.get("degree", 15.0)
            if get_triplicity_ruler(ruler_sign, sect) == ruler:
                ruler_dignities.append("三分主星")
            if get_term_ruler(ruler_sign, ruler_degree) == ruler:
                ruler_dignities.append("界")
            if get_face_ruler(ruler_sign, ruler_degree) == ruler:
                ruler_dignities.append("面")

        if ruler_house in ANGULAR_HOUSES:
            ruler_dignities.append("居角宫")
        elif ruler_house in CADENT_HOUSES:
            ruler_dignities.append("居果宫")

        # Aspects to the ruler
        ruler_asp_list = ruler_aspects.get(ruler, [])
        beneficial = [asp for asp in ruler_asp_list
                      if asp["type"] in ("trine", "sextile")]
        challenging = [asp for asp in ruler_asp_list
                       if asp["type"] in ("square", "opposition")]

        # Generate interpretation
        ruler_sign_display = SIGN_DISPLAY.get(ruler_sign, ruler_sign)


        # Quality assessment — include triplicity/term/face in evaluation
        if "入庙" in ruler_dignities or "擢升" in ruler_dignities:
            quality = "强健"
        elif "失势" in ruler_dignities or "陷落" in ruler_dignities:
            quality = "受困"
        elif "三分主星" in ruler_dignities or "界" in ruler_dignities:
            quality = "有力"
        elif "居角宫" in ruler_dignities:
            quality = "有力"
        elif "面" in ruler_dignities:
            quality = "尚可"
        else:
            quality = "中性"

        interpretation = (
            f"第{HOUSE_CN.get(house_num, house_num)}宫（{area}）宫头落{cusp_display}，"
            f"宫主星{ruler_display}飞入{ruler_sign_display}第{HOUSE_CN.get(ruler_house, ruler_house)}宫"
        )

        if ruler_dignities:
            interpretation += f"，{'、'.join(ruler_dignities)}"
        if beneficial:
            asp_names = [f"{PLANET_DISPLAY.get(a['with'], a['with'])}{ASPECT_TYPES.get(a['type'], a['type'])}"
                         for a in beneficial[:2]]
            interpretation += f"，得{'、'.join(asp_names)}相助"
        if challenging:
            asp_names = [f"{PLANET_DISPLAY.get(a['with'], a['with'])}{ASPECT_TYPES.get(a['type'], a['type'])}"
                         for a in challenging[:2]]
            interpretation += f"，受{'、'.join(asp_names)}挑战"

        interpretation += "。"

        rulerships.append({
            "house": house_num,
            "cusp_sign": cusp_sign,
            "ruler": ruler,
            "ruler_sign": ruler_sign,
            "ruler_house": ruler_house,
            "dignities": ruler_dignities,
            "quality": quality,
            "interpretation": interpretation,
        })

    return rulerships


# ═══════════════════════════════════════════════════════
# LAYER 5b: CONFLICT MEDIATOR
# ═══════════════════════════════════════════════════════

def mediate_conflicts(matched: list[dict], chart_data: dict) -> list[dict]:
    """Resolve contradictions between matched interpretations.

    Priority: essential dignity > accidental dignity > benefic aspect > malefic aspect > sign traits

    Returns a list of conflict notes highlighting where interpretations diverge.
    """
    planets = chart_data.get("planets", {})
    aspects = chart_data.get("aspects", [])

    # Build per-planet dignity and aspect context
    planet_context = {}
    for pname, pdata in planets.items():
        sign = pdata.get("sign", "")
        house = pdata.get("house", 0)
        ctx = {"sign": sign, "house": house, "positive": 0, "challenging": 0,
               "dignities": [], "tags": []}

        if sign:
            d_info = ESSENTIAL_DIGNITY.get(sign, {})
            if d_info.get("ruler") == pname:
                ctx["dignities"].append("入庙")
                ctx["positive"] += 3
            elif d_info.get("detriment") == pname:
                ctx["dignities"].append("失势")
                ctx["challenging"] += 3
            if d_info.get("exaltation") == pname:
                ctx["dignities"].append("擢升")
                ctx["positive"] += 2
            elif d_info.get("fall") == pname:
                ctx["dignities"].append("陷落")
                ctx["challenging"] += 2
        if house in ANGULAR_HOUSES:
            ctx["tags"].append("角宫")
            ctx["positive"] += 1
        if PLANETARY_JOY.get(pname) == house:
            ctx["tags"].append("喜乐")
            ctx["positive"] += 1

        planet_context[pname] = ctx

    # Score aspects (only for planets in the chart)
    for asp in aspects:
        a, b, t = asp.get("a", ""), asp.get("b", ""), asp.get("type", "")
        if a not in planet_context or b not in planet_context:
            continue
        if t in ("trine", "sextile"):
            planet_context[a]["positive"] += 1
            planet_context[b]["positive"] += 1
        elif t in ("square", "opposition"):
            planet_context[a]["challenging"] += 1
            planet_context[b]["challenging"] += 1

    # Find planets with mixed signals (any positive + challenging combination)
    conflicts = []
    for pname, ctx in planet_context.items():
        if ctx["positive"] >= 1 and ctx["challenging"] >= 1:
            display = PLANET_DISPLAY.get(pname, pname)
            pos_factors = ctx["dignities"] + ctx["tags"]
            net = ctx["positive"] - ctx["challenging"]

            if net >= 3:
                resolution = (f"{display}虽有挑战因素，但{'、'.join(pos_factors)}的优势更为根本，"
                              f"应以正面表达为主，挑战面为成长契机。")
            elif net >= 1:
                resolution = (f"{display}总体上{'、'.join(pos_factors)}的正面基础稳固，"
                              f"但需注意相关挑战带来的张力，适当调整期待和表达方式。")
            elif net >= -1:
                resolution = (f"{display}正反力量均衡，关键在于当事人的意识选择，"
                              f"可通过自我觉察将张力转化为创造力。")
            elif net >= -3:
                resolution = (f"{display}挑战稍占上风，{'、'.join(pos_factors)}的基础需要更多耐心耕耘，"
                              f"建议在该领域保持清醒认知，不回避困难。")
            else:
                resolution = (f"{display}面临的挑战较重，{'、'.join(pos_factors)}的基础被削弱，"
                              f"需要在相关领域付出更多努力才能发挥正面特质。")

            conflicts.append({
                "planet": pname,
                "display": display,
                "positive_score": ctx["positive"],
                "challenging_score": ctx["challenging"],
                "dignities": ctx["dignities"],
                "resolution": resolution,
            })

    # Sort by conflict intensity (highest |positive - challenging| first for clear cases,
    # then balanced cases)
    conflicts.sort(key=lambda c: (abs(c["positive_score"] - c["challenging_score"]) < 2,
                                   -(c["positive_score"] + c["challenging_score"])))

    return conflicts


# ═══════════════════════════════════════════════════════
# PHASE 3: KNOWLEDGE GRAPH CONSTRUCTION
# ═══════════════════════════════════════════════════════

def build_graph():
    """Build MECE-structured knowledge graph from extracted rules."""
    rules_path = OUTPUT_DIR / "all_extracted_rules.json"
    if not rules_path.exists():
        print("No extracted rules found. Run Phase 2 first.")
        return None

    with open(rules_path, "r", encoding="utf-8") as f:
        raw_rules = json.load(f)

    rules = [normalize_entities(r) for r in raw_rules
             if r.get("interpretation") and len(r.get("interpretation", "")) >= 25]

    graph = {
        "meta": {
            "total_rules": len(rules),
            "categories": list(set(r["category"] for r in rules)),
        },
        # Layer 1: Atomic placements
        "planet_in_sign": defaultdict(list),
        "planet_in_house": defaultdict(list),
        "sign_rules": defaultdict(list),
        "house_rules": defaultdict(list),
        "planet_rules": defaultdict(list),
        # Layer 2: Dignities (extracted rules)
        "dignity_rules": defaultdict(list),
        # Layer 3: Relationships
        "aspect_specific": defaultdict(list),
        "aspect_planet_pair": defaultdict(list),
        # Layer 4: Patterns (extracted rules)
        "element_rules": defaultdict(list),
        "mode_rules": defaultdict(list),
        "pattern_rules": defaultdict(list),
        # Layer 5: Synthesis
        "synthesis_rules": [],
        # Layer 6: Lack detection
        "lack_rules": defaultdict(list),
        # All rules
        "rules": rules,
    }

    for idx, rule in enumerate(rules):
        # Layer 1: planet_in_sign, planet_in_house
        # Use primary entity planets (not all detected planets) to avoid
        # cross-product false indexing (e.g., a Saturn rule mentioning Sun
        # should not be indexed under sun_in_X)
        entity = rule.get("entity", "")
        primary_planets = [en for cn, en in PLANET_NAMES.items() if cn in entity]
        idx_planets = primary_planets if primary_planets else rule["planets"]

        for p in idx_planets:
            for s in rule["signs"]:
                graph["planet_in_sign"][f"{p}_in_{s}"].append(idx)
            graph["planet_rules"][p].append(idx)
        for p in idx_planets:
            for h in rule["houses"]:
                graph["planet_in_house"][f"{p}_in_{h}"].append(idx)
        # sign_rules / house_rules: only for rules without a specific planet entity
        if not primary_planets:
            for s in rule["signs"]:
                graph["sign_rules"][s].append(idx)
        if not primary_planets:
            for h in rule["houses"]:
                graph["house_rules"][str(h)].append(idx)

        # Layer 2: Dignities
        for d in rule["dignities"]:
            graph["dignity_rules"][d].append(idx)

        # Layer 3: Aspects
        for a in rule["aspects"]:
            graph["aspect_specific"][a].append(idx)
        if rule["aspects"]:
            planets = rule["planets"]
            for pi in range(len(planets)):
                for pj in range(pi + 1, len(planets)):
                    pair = "_".join(sorted([planets[pi], planets[pj]]))
                    graph["aspect_planet_pair"][pair].append(idx)

        # Layer 4: Element/mode rules
        text = rule.get("condition", "") + rule.get("interpretation", "")
        for cn in ["火象", "火元素", "火"]:
            if cn in text:
                graph["element_rules"]["fire"].append(idx)
                break
        for cn in ["土象", "土元素"]:
            if cn in text:
                graph["element_rules"]["earth"].append(idx)
                break
        for cn in ["风象", "风元素", "風象"]:
            if cn in text:
                graph["element_rules"]["air"].append(idx)
                break
        for cn in ["水象", "水元素"]:
            if cn in text:
                graph["element_rules"]["water"].append(idx)
                break
        for cn in ["开创", "基本宫"]:
            if cn in text:
                graph["mode_rules"]["cardinal"].append(idx)
                break
        for cn in ["固定"]:
            if cn in text:
                graph["mode_rules"]["fixed"].append(idx)
                break
        for cn in ["变动", "變動"]:
            if cn in text:
                graph["mode_rules"]["mutable"].append(idx)
                break
        for cn in ["大三角", "大十字", "T三角", "风筝", "神秘矩形"]:
            if cn in text:
                graph["pattern_rules"][cn].append(idx)
                break

        # Layer 5: Synthesis (principles that guide interpretation priority)
        cat = rule.get("category", "")
        if cat in ("synthesis", "chart_pattern"):
            graph["synthesis_rules"].append(idx)

        # Layer 6: Lack detection — rules about missing elements/modes
        # Check if a lack keyword appears near an element/mode keyword
        lack_kw = ["缺乏", "缺少", "不足", "缺失", "欠缺", "缺"]
        elem_kw_map = {
            "fire": ["火象", "火元素"],
            "earth": ["土象", "土元素"],
            "air": ["风象", "风元素", "風象"],
            "water": ["水象", "水元素"],
            "cardinal": ["开创", "基本宫"],
            "fixed": ["固定"],
            "mutable": ["变动", "變動"],
        }
        for elem_type, elem_keywords in elem_kw_map.items():
            for ek in elem_keywords:
                ek_pos = text.find(ek)
                if ek_pos < 0:
                    continue
                # Check if any lack keyword is within 30 chars before ek
                snippet_start = max(0, ek_pos - 30)
                snippet = text[snippet_start:ek_pos + len(ek)]
                if any(lk in snippet for lk in lack_kw):
                    graph["lack_rules"][elem_type].append(idx)
                    break

    # Serialize
    graph_serializable = {
        "meta": graph["meta"],
        "planet_in_sign": dict(graph["planet_in_sign"]),
        "planet_in_house": dict(graph["planet_in_house"]),
        "sign_rules": dict(graph["sign_rules"]),
        "house_rules": dict(graph["house_rules"]),
        "planet_rules": dict(graph["planet_rules"]),
        "dignity_rules": dict(graph["dignity_rules"]),
        "aspect_specific": dict(graph["aspect_specific"]),
        "aspect_planet_pair": dict(graph["aspect_planet_pair"]),
        "element_rules": dict(graph["element_rules"]),
        "mode_rules": dict(graph["mode_rules"]),
        "pattern_rules": dict(graph["pattern_rules"]),
        "synthesis_rules": graph["synthesis_rules"],
        "lack_rules": dict(graph["lack_rules"]),
        "rules": graph["rules"],
    }

    gpath = OUTPUT_DIR / "knowledge_graph.json"
    with open(gpath, "w", encoding="utf-8") as f:
        json.dump(graph_serializable, f, ensure_ascii=False, indent=2)

    # Stats
    print(f"Knowledge graph: {len(rules)} rules → {gpath}")
    print(f"  Layer 1 — Planet-in-sign: {len(graph['planet_in_sign'])} keys")
    print(f"  Layer 1 — Planet-in-house: {len(graph['planet_in_house'])} keys")
    print(f"  Layer 1 — Sign rules: {len(graph['sign_rules'])}")
    print(f"  Layer 1 — House rules: {len(graph['house_rules'])}")
    print(f"  Layer 2 — Dignity rules: {len(graph['dignity_rules'])} types")
    print(f"  Layer 3 — Aspect types: {len(graph['aspect_specific'])}")
    print(f"  Layer 3 — Planet-pair aspects: {len(graph['aspect_planet_pair'])}")
    print(f"  Layer 4 — Element rules: {len(graph['element_rules'])}")
    print(f"  Layer 4 — Mode rules: {len(graph['mode_rules'])}")
    print(f"  Layer 4 — Pattern rules: {len(graph['pattern_rules'])}")
    print(f"  Layer 5 — Synthesis rules: {len(graph['synthesis_rules'])}")
    print(f"  Layer 6 — Lack rules: {len(graph['lack_rules'])} types, {sum(len(v) for v in graph['lack_rules'].values())} rules")

    return graph


# ═══════════════════════════════════════════════════════
# PHASE 4: INFERENCE ENGINE
# ═══════════════════════════════════════════════════════

def load_graph():
    """Load knowledge graph from disk."""
    gpath = OUTPUT_DIR / "knowledge_graph.json"
    if not gpath.exists():
        return None
    with open(gpath, "r", encoding="utf-8") as f:
        return json.load(f)


def match_chart(chart_data: dict, top_k: int = 20) -> list[dict]:
    """Layered MECE matching of birth chart against knowledge graph.

    Scoring principle: specificity > generality.
    Exact planet×sign matches score higher than same-sign-any-planet matches.
    """
    graph = load_graph()
    if not graph:
        print("Knowledge graph not found. Run Phase 3 first.")
        return []

    rules = graph["rules"]
    matches = {}
    chart_planets = set(chart_data.get("planets", {}).keys())

    def add_score(indices, base_score):
        for idx in indices:
            matches[idx] = matches.get(idx, 0) + base_score

    def weighted_score(indices, base_score):
        """Add score with rule weight factored in."""
        for idx in indices:
            rule = rules[idx]
            weight = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(
                rule.get("weight", "medium"), 0.5)
            matches[idx] = matches.get(idx, 0) + base_score * weight

    # ── Layer 1: Atomic Placements ──
    for planet, data in chart_data.get("planets", {}).items():
        sign = data.get("sign", "")
        house = data.get("house", 0)

        if sign:
            # Exact planet_in_sign match (highest specificity)
            key = f"{planet}_in_{sign}"
            if key in graph.get("planet_in_sign", {}):
                weighted_score(graph["planet_in_sign"][key], 3.0)

        if house:
            # Exact planet_in_house match (same specificity as planet_in_sign)
            key = f"{planet}_in_{house}"
            if key in graph.get("planet_in_house", {}):
                weighted_score(graph["planet_in_house"][key], 3.0)

        # Broader sign traits (lower specificity)
        if sign and sign in graph.get("sign_rules", {}):
            weighted_score(graph["sign_rules"][sign], 1.0)

        # Broader house meanings
        if house and str(house) in graph.get("house_rules", {}):
            weighted_score(graph["house_rules"][str(house)], 0.8)

    # Ascendant sign
    asc_sign = chart_data.get("angles", {}).get("asc", "")
    if asc_sign and asc_sign in graph.get("sign_rules", {}):
        weighted_score(graph["sign_rules"][asc_sign], 1.2)

    # ── Layer 2: Dignities (deterministic + extracted) ──
    meta = compute_chart_meta(chart_data)
    for pname, dignities in meta.get("planet_dignities", {}).items():
        for d in dignities:
            if d in graph.get("dignity_rules", {}):
                # Weight by dignity type (classical hierarchy)
                dignity_weight = {
                    "ruler": 2.0, "exaltation": 1.8, "triplicity": 1.5,
                    "term": 1.3, "face": 1.1,
                    "joy": 1.3, "angular": 1.2, "detriment": 0.8, "fall": 0.8,
                }.get(d, 1.0)
                for idx in graph["dignity_rules"][d]:
                    rule = rules[idx]
                    rule_planets = set(rule.get("planets", []))
                    if not rule_planets or (rule_planets & chart_planets):
                        weighted_score([idx], dignity_weight)

    # ── Layer 3: Relationships (Aspects) ──
    for aspect in chart_data.get("aspects", []):
        aspect_type = aspect.get("type", "")
        orb_score = max(0, 1.0 - aspect.get("orb", 5) / 10)

        # Specific type match
        if aspect_type in graph.get("aspect_specific", {}):
            weighted_score(graph["aspect_specific"][aspect_type], 2.0 * orb_score)

        # Planet-pair match — highest specificity for aspects
        a, b = aspect.get("a", ""), aspect.get("b", "")
        if a and b:
            pair_key = "_".join(sorted([a, b]))
            if pair_key in graph.get("aspect_planet_pair", {}):
                for rule_idx in graph["aspect_planet_pair"][pair_key]:
                    rule = rules[rule_idx]
                    rule_aspects = rule.get("aspects", [])
                    weight = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(
                        rule.get("weight", "medium"), 0.5)
                    if aspect_type in rule_aspects:
                        matches[rule_idx] = matches.get(rule_idx, 0) + 3.5 * orb_score * weight
                    elif "general" in rule_aspects:
                        matches[rule_idx] = matches.get(rule_idx, 0) + 2.5 * orb_score * weight

    # ── Layer 4: Patterns ──
    # Element/mode balance
    for elem in meta.get("element_excess", []):
        if elem in graph.get("element_rules", {}):
            weighted_score(graph["element_rules"][elem], 1.8)
    for elem in meta.get("element_lack", []):
        if elem in graph.get("element_rules", {}):
            weighted_score(graph["element_rules"][elem], 1.5)
    for mode in meta.get("mode_excess", []):
        if mode in graph.get("mode_rules", {}):
            weighted_score(graph["mode_rules"][mode], 1.5)
    for mode in meta.get("mode_lack", []):
        if mode in graph.get("mode_rules", {}):
            weighted_score(graph["mode_rules"][mode], 1.2)

    # ── Layer 6: Lack Detection ──
    for elem in meta.get("element_lack", []):
        if elem in graph.get("lack_rules", {}):
            weighted_score(graph["lack_rules"][elem], 2.5)
    for mode in meta.get("mode_lack", []):
        if mode in graph.get("lack_rules", {}):
            weighted_score(graph["lack_rules"][mode], 2.0)

    # Aspect configurations
    configs = detect_aspect_configs(chart_data)
    for cfg in configs:
        cfg_type = cfg.get("type", "")
        if cfg_type in graph.get("pattern_rules", {}):
            weighted_score(graph["pattern_rules"][cfg_type], 2.0)

    # Stelliums
    for sign in meta.get("stellium_signs", []):
        for pname in chart_data.get("planets", {}):
            key = f"{pname}_in_{sign}"
            if key in graph.get("planet_in_sign", {}):
                # Stellium: boost existing planet_in_sign matches
                weighted_score(graph["planet_in_sign"][key], 1.5)

    # ── Layer 5: Synthesis ──
    # Only match synthesis rules that are actually relevant to this chart
    for idx in graph.get("synthesis_rules", []):
        rule = rules[idx]
        # Check planet overlap
        rule_planets = set(rule.get("planets", []))
        if rule_planets & chart_planets:
            weighted_score([idx], 0.8)
            continue
        # Check sign overlap
        chart_signs = set()
        for pdata in chart_data.get("planets", {}).values():
            if pdata.get("sign"):
                chart_signs.add(pdata["sign"])
        if chart_data.get("angles", {}).get("asc"):
            chart_signs.add(chart_data["angles"]["asc"])
        if set(rule.get("signs", [])) & chart_signs:
            weighted_score([idx], 0.8)
            continue
        # Check house overlap
        chart_houses = set(str(pdata.get("house", 0)) for pdata in chart_data.get("planets", {}).values())
        if set(str(h) for h in rule.get("houses", [])) & chart_houses:
            weighted_score([idx], 0.8)
            continue
        # Check aspect overlap
        rule_aspects = set(rule.get("aspects", []))
        chart_aspects = set(a.get("type", "") for a in chart_data.get("aspects", []))
        if "general" in rule_aspects and chart_aspects:
            weighted_score([idx], 0.5)
        elif rule_aspects & chart_aspects:
            weighted_score([idx], 0.8)
        # else: rule has no entity overlap with chart → skip entirely

    # Sort and deduplicate
    scored = sorted(matches.items(), key=lambda x: x[1], reverse=True)

    # Layer-aware diversity: ensure each MECE layer has minimum representation
    # Define layer buckets
    layer_buckets = {
        "planet_in_sign": [],
        "planet_in_house": [],
        "dignity": [],
        "planet_aspect": [],
        "house_rulership": [],
        "chart_pattern": [],
        "synthesis": [],
    }
    seen = set()
    for idx, score in scored[:top_k * 5]:
        rule = rules[idx]
        rule_planets = set(rule.get("planets", []))
        if rule_planets and not (rule_planets & chart_planets):
            continue
        interp_key = rule.get("interpretation", "")[:60]
        if interp_key in seen:
            continue
        seen.add(interp_key)
        item = {
            "interpretation": rule.get("interpretation", ""),
            "condition": rule.get("condition", ""),
            "category": rule.get("category", ""),
            "entity": rule.get("entity", ""),
            "score": round(score, 2),
            "weight": rule.get("weight", "medium"),
            "tags": rule.get("tags", []),
        }
        cat = rule.get("category", "")
        if cat in layer_buckets:
            layer_buckets[cat].append(item)
        else:
            layer_buckets.setdefault("_other", []).append(item)

    # Quotas per layer (for top_k=20)
    quota = {
        "planet_in_sign": max(1, top_k // 8),
        "planet_in_house": max(1, top_k // 8),
        "dignity": max(1, top_k // 8),
        "planet_aspect": max(1, top_k // 8),
        "house_rulership": max(1, top_k // 10),
        "chart_pattern": max(1, top_k // 10),
        "synthesis": max(1, top_k // 10),
    }

    results = []
    # First pass: fill quotas
    for cat, q in quota.items():
        for item in layer_buckets.get(cat, [])[:q]:
            results.append(item)

    # Second pass: fill remaining by score from unscored items
    already_in = {r["interpretation"][:60] for r in results}
    for idx, score in scored:
        if len(results) >= top_k:
            break
        rule = rules[idx]
        rule_planets = set(rule.get("planets", []))
        if rule_planets and not (rule_planets & chart_planets):
            continue
        interp_key = rule.get("interpretation", "")[:60]
        if interp_key in already_in:
            continue
        already_in.add(interp_key)
        results.append({
            "interpretation": rule.get("interpretation", ""),
            "condition": rule.get("condition", ""),
            "category": rule.get("category", ""),
            "entity": rule.get("entity", ""),
            "score": round(score, 2),
            "weight": rule.get("weight", "medium"),
            "tags": rule.get("tags", []),
        })

    # Sort final results by score (keep diversity but show best first)
    results.sort(key=lambda x: x["score"], reverse=True)

    return results[:top_k]


# ═══════════════════════════════════════════════════════
# LAYER 7: READING GENERATION
# ═══════════════════════════════════════════════════════

def generate_reading(chart_data: dict, style: str = "detailed") -> str:
    """Generate a complete MECE-structured astrological reading."""
    interpretations = match_chart(chart_data, top_k=30)
    meta = compute_chart_meta(chart_data)
    configs = detect_aspect_configs(chart_data)

    if not interpretations:
        return "知识图谱未构建。请先运行 Phase 3。"

    lines = [
        "╔══════════════════════════════════╗",
        "║       星  盘  解  读  报  告      ║",
        "╚══════════════════════════════════╝",
        "",
    ]

    # ── Section 1: 整体格局 (Chart Overview) ──
    lines.append("【整体格局】")
    lines.append("")

    # Element balance
    elems = meta.get("elements", {})
    elem_str = "  ".join(f"{ELEMENT_CN.get(k, k)}: {v} 颗星" for k, v in sorted(elems.items()))
    lines.append(f"  元素分布：{elem_str}")

    if meta.get("element_excess"):
        excess_cn = [ELEMENT_CN.get(e, e) for e in meta["element_excess"]]
        lines.append(f"  元素强调：{', '.join(excess_cn)}元素突出，相关特质在人生中显著。")
    if meta.get("element_lack"):
        lack_cn = [ELEMENT_CN.get(e, e) for e in meta["element_lack"]]
        lines.append(f"  元素缺失：缺乏{', '.join(lack_cn)}元素，需要在相关领域有意识发展。")

    # Mode balance
    mods = meta.get("modes", {})
    mode_str = "  ".join(f"{MODE_CN.get(k, k)}: {v} 颗星" for k, v in sorted(mods.items()))
    lines.append(f"  模式分布：{mode_str}")

    if meta.get("mode_excess"):
        excess_cn = [MODE_CN.get(m, m) for m in meta["mode_excess"]]
        lines.append(f"  模式强调：{', '.join(excess_cn)}星座能量集中，行动风格鲜明。")
    if meta.get("mode_lack"):
        lack_cn = [MODE_CN.get(m, m) for m in meta["mode_lack"]]
        lines.append(f"  模式缺失：缺乏{', '.join(lack_cn)}模式，该行动风格可能需要刻意练习。")

    # Hemisphere
    hemi = meta.get("hemisphere", {})
    above = hemi.get("above", 0)
    below = hemi.get("below", 0)
    if above > below + 2:
        lines.append("  半球强调：星体集中在上半球（7-12宫），人生重心在社会参与和公共领域。")
    elif below > above + 2:
        lines.append("  半球强调：星体集中在下半球（1-6宫），人生重心在个人发展和内在世界。")
    else:
        lines.append("  半球分布：星体均匀分布，在个人与社会之间保持平衡。")

    # Stelliums
    if meta.get("stellium_signs"):
        for s in meta["stellium_signs"]:
            lines.append(f"  星群格局：{s} 座有 3 颗以上星体聚集（星群），该星座特质被强烈放大。")
    if meta.get("stellium_houses"):
        for h in meta["stellium_houses"]:
            cn_map = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
                      7: "七", 8: "八", 9: "九", 10: "十", 11: "十一", 12: "十二"}
            lines.append(f"  星群格局：第{cn_map.get(h, h)}宫有 3 颗以上星体聚集，该宫位领域是人生重点。")

    # Aspect configurations
    if configs:
        for cfg in configs:
            cfg_type = cfg.get("type", "")
            cfg_names = {"grand_trine": "大三角", "t_square": "T三角", "grand_cross": "大十字",
                         "kite": "风筝", "mystic_rectangle": "神秘矩形"}
            cfg_planets = ", ".join(cfg.get("planets", []))
            extra = ""
            if cfg.get("element"):
                extra = f"（{ELEMENT_CN.get(cfg['element'], cfg['element'])}象）"
            elif cfg.get("apex"):
                extra = f"（顶点：{cfg['apex']}）"
            lines.append(f"  相位格局：{cfg_names.get(cfg_type, cfg_type)}{extra}（{cfg_planets}），是星盘的重要动力结构。")

    lines.append("")

    # ── Section 2: 行星尊严 (Planetary Dignities) ──
    dignities = meta.get("planet_dignities", {})
    if dignities:
        lines.append("【行星尊严】")
        lines.append("")
        for pname, digs in dignities.items():
            pname_cn_map = {"sun": "太阳", "moon": "月亮", "mercury": "水星", "venus": "金星",
                            "mars": "火星", "jupiter": "木星", "saturn": "土星",
                            "uranus": "天王星", "neptune": "海王星", "pluto": "冥王星",
                            "north_node": "北交点", "south_node": "南交点",
                            "asc": "上升", "mc": "天顶", "chiron": "凯龙星"}
            pname_cn = pname_cn_map.get(pname, pname)
            dig_cn = [DIGNITY_LABELS.get(d, d) for d in digs]
            lines.append(f"  {pname_cn}：{'、'.join(dig_cn)}")
        lines.append("")

    # ── Section 3: 宫位守护 (House Rulerships) ──
    rulerships = compute_rulerships(chart_data)
    if rulerships:
        lines.append("【宫位守护】")
        lines.append("")
        # Show angular houses first, then key houses with strong/weak rulers
        ordered = sorted(rulerships, key=lambda r: (
            r["house"] not in ANGULAR_HOUSES,
            r["quality"] != "强健",
            r["house"],
        ))
        for r in ordered[:6]:
            quality_mark = {"强健": "◎", "有力": "○", "受困": "△", "中性": "—"}.get(r["quality"], "")
            lines.append(f"  {quality_mark} {r['interpretation']}")
        lines.append("")

    # ── Section 4: 核心解读 (Key Placements) ──
    lines.append("【核心解读】")
    lines.append("")
    placement_items = [i for i in interpretations
                       if i["category"] in ("planet_in_sign", "planet_in_house", "dignity")]
    for item in placement_items[:8]:
        entity = item.get("entity", "")
        prefix = f"  [{entity}] " if entity else "  "
        lines.append(f"{prefix}{item['interpretation']}")
    lines.append("")

    # ── Section 4: 宫位重点 (House Emphasis) ──
    house_items = [i for i in interpretations if i["category"] in ("house_rulership", "planet_in_house")]
    if house_items:
        lines.append("【宫位重点】")
        lines.append("")
        entity_counts = defaultdict(int)
        for item in house_items:
            entity_counts[item.get("entity", "")] += 1
        for item in house_items[:5]:
            lines.append(f"  {item['interpretation']}")
        lines.append("")

    # ── Section 5: 相位关系 (Aspects) ──
    aspect_items = [i for i in interpretations if i["category"] == "planet_aspect"]
    if aspect_items:
        lines.append("【相位关系】")
        lines.append("")
        for item in aspect_items[:4]:
            lines.append(f"  {item['interpretation']}")
        lines.append("")

    # ── Section 6: 矛盾裁决 (Conflict Resolution) ──
    conflicts = mediate_conflicts(interpretations, chart_data)
    if conflicts:
        lines.append("【矛盾裁决】")
        lines.append("")
        for c in conflicts[:4]:
            lines.append(f"  {c['display']}：{c['resolution']}")
        lines.append("")

    # ── Section 7: 缺失提示 (Lack Detection) ──
    # Pull lack rules directly from graph, not via match ranking
    graph = load_graph()
    lack_rules_graph = graph.get("lack_rules", {}) if graph else {}
    all_lacks = meta.get("element_lack", []) + meta.get("mode_lack", [])
    lack_lines = []
    for lack_type in all_lacks:
        lack_cn = {**ELEMENT_CN, **MODE_CN}.get(lack_type, lack_type)
        for idx in lack_rules_graph.get(lack_type, []):
            rule = graph["rules"][idx]
            lack_lines.append(f"  [{lack_cn}缺失] {rule.get('interpretation', '')}")
            if len(lack_lines) >= 4:
                break
        if len(lack_lines) >= 4:
            break
    if lack_lines:
        lines.append("【缺失提示】")
        lines.append("")
        lines.extend(lack_lines)
        lines.append("")

    # ── Section 8: 综合提示 (Synthesis) ──
    principle_items = [i for i in interpretations
                       if i["category"] in ("synthesis", "chart_pattern")]
    if principle_items:
        lines.append("【综合提示】")
        lines.append("")
        for item in principle_items[:4]:
            lines.append(f"  {item['interpretation']}")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python astro_graph.py <phase>")
        print("  phase 3: Build knowledge graph")
        print("  phase 4: Test inference with sample chart")
        sys.exit(1)

    phase = sys.argv[1]

    if phase == "3":
        build_graph()

    elif phase == "4":
        sample = {
            "planets": {
                "sun": {"sign": "leo", "house": 10},
                "moon": {"sign": "taurus", "house": 7},
                "mercury": {"sign": "virgo", "house": 11},
                "venus": {"sign": "cancer", "house": 9},
                "mars": {"sign": "scorpio", "house": 1},
            },
            "angles": {"asc": "scorpio", "mc": "leo"},
            "aspects": [
                {"a": "sun", "b": "mars", "type": "square", "orb": 3},
                {"a": "moon", "b": "venus", "type": "sextile", "orb": 4},
                {"a": "sun", "b": "saturn", "type": "trine", "orb": 2},
            ],
        }
        reading = generate_reading(sample)
        print(reading)
        print(f"\n(Generated from {len(match_chart(sample))} matched rules)")

    else:
        print(f"Unknown phase: {phase}")
