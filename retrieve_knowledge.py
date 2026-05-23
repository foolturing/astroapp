"""Knowledge retrieval for chart synthesis.

Matches chart data against all_extracted_rules.json (16K+ rules extracted
from astrological texts) and returns the most relevant rules.
"""

import json
import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

from constants import SIGN_CN, PLANET_CN, ASPECT_FULL_CN

logger = logging.getLogger(__name__)

# ── Lazy-loaded knowledge cache ──
_rules_cache = None


def _load_rules():
    """Load all rules from the flat extracted rules file. Cached in memory."""
    global _rules_cache
    if _rules_cache is not None:
        return _rules_cache

    path = OUTPUT_DIR / "all_extracted_rules.json"
    if not path.exists():
        logger.warning("all_extracted_rules.json not found at %s", path)
        _rules_cache = []
        return _rules_cache

    try:
        with open(path, "r", encoding="utf-8") as f:
            _rules_cache = json.load(f)
        return _rules_cache
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load all_extracted_rules.json: %s", e)
        _rules_cache = []
        return _rules_cache


def generate_lookup_keys(chart_data):
    """Generate lookup keys from chart data for matching against rules.

    Returns a list of (key_string, weight) tuples.
    """
    keys = []
    planets = chart_data.get("planets", {})
    aspects = chart_data.get("aspects", [])
    houses = chart_data.get("houses", {})
    sect = chart_data.get("sect", "")

    for pname, pdata in planets.items():
        cn_name = PLANET_CN.get(pname, pname)
        sign_cn = SIGN_CN.get(pdata.get("sign", ""), "")
        house = pdata.get("house", 0)
        dignity = pdata.get("dignity", "")

        if sign_cn:
            keys.append((f"{cn_name}在{sign_cn}", 1.0))
            if house:
                keys.append((f"{cn_name}在{sign_cn}{house}宫", 0.9))
        if house:
            keys.append((f"{cn_name}在第{house}宫", 0.8))
        if dignity:
            dignity_cn = {
                "rulership": "入庙", "exaltation": "擢升", "detriment": "失势",
                "fall": "陷落", "triplicity": "三分", "term": "界", "face": "面",
            }.get(dignity, dignity)
            keys.append((f"{cn_name}{dignity_cn}", 0.6))

        acc_details = pdata.get("accidental_details", [])
        if "combust" in acc_details:
            keys.append((f"{cn_name}燃烧", 0.5))
        if "cazimi" in acc_details:
            keys.append((f"{cn_name}日核", 0.5))
        if pdata.get("retrograde"):
            keys.append((f"{cn_name}逆行", 0.5))

    for asp in aspects:
        p1_cn = PLANET_CN.get(asp["planet_a"], asp["planet_a"])
        p2_cn = PLANET_CN.get(asp["planet_b"], asp["planet_b"])
        atype_cn = ASPECT_FULL_CN.get(asp["type"], asp["type"])
        keys.append((f"{p1_cn}{atype_cn}{p2_cn}", 0.9))
        keys.append((f"{p1_cn}与{p2_cn}有相位", 0.8))

    chart_ruler = chart_data.get("chart_ruler", "")
    if chart_ruler:
        ruler_cn = PLANET_CN.get(chart_ruler, chart_ruler)
        keys.append((f"命主星{ruler_cn}", 0.8))

    if sect == "diurnal":
        keys.append(("日生盘", 0.5))
    elif sect == "nocturnal":
        keys.append(("夜生盘", 0.5))

    asc_sign = chart_data.get("asc", "")
    if asc_sign:
        asc_cn = SIGN_CN.get(asc_sign, asc_sign)
        keys.append((f"上升{asc_cn}", 0.6))

    for lot_name, lot_data in chart_data.get("lots", {}).items():
        sign_cn = SIGN_CN.get(lot_data.get("sign", ""), "")
        if sign_cn:
            lot_cn = {"fortune": "福点", "spirit": "精神点"}.get(lot_name, lot_name)
            keys.append((f"{lot_cn}在{sign_cn}", 0.4))

    # Stellium detection
    sign_counts = {}
    for pdata in planets.values():
        sign = pdata.get("sign", "")
        sign_counts[sign] = sign_counts.get(sign, 0) + 1
    for sign, count in sign_counts.items():
        if count >= 3:
            sign_cn = SIGN_CN.get(sign, sign)
            keys.append((f"{sign_cn}星群", 0.7))

    return keys


def match_rule(rule, chart_keys):
    """Score a rule against chart keys.

    Returns (score, matched_keys) or (0, []) if no match.
    """
    entity = rule.get("entity", "")
    condition = rule.get("condition", "")
    keywords = rule.get("keywords", "")
    category = rule.get("category", "")
    weight = rule.get("weight", "medium")

    search_text = f"{entity} {condition} {keywords}".lower()
    score = 0.0
    matches = []

    for key_str, key_weight in chart_keys:
        if key_str.lower() in search_text:
            score += key_weight
            matches.append(key_str)

    if score == 0:
        return 0, []

    # Boost exact entity match
    if entity:
        for key_str, key_weight in chart_keys:
            if key_str.lower() == entity.lower():
                score += 2.0
                break

    # Category preference
    if category in ("planet_in_sign", "planet_aspect"):
        score *= 1.2
    elif category in ("planet_in_house", "dignity"):
        score *= 1.1
    elif category == "chart_pattern":
        score *= 0.8

    # Rule weight multiplier
    if isinstance(weight, (int, float)):
        score *= weight
    elif isinstance(weight, str):
        score *= {"high": 1.0, "medium": 0.7, "low": 0.4}.get(weight, 0.5)

    return round(score, 2), matches


def retrieve_knowledge(chart_data, top_k=30):
    """Retrieve relevant knowledge rules for a chart."""
    rules = _load_rules()
    if not rules:
        return []

    chart_keys = generate_lookup_keys(chart_data)

    scored = []
    for rule in rules:
        score, matches = match_rule(rule, chart_keys)
        if score > 0:
            scored.append({
                "rule": rule,
                "score": score,
                "matches": matches[:3],
                "interpretation": rule.get("interpretation", ""),
                "condition": rule.get("condition", ""),
                "category": rule.get("category", ""),
            })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Deduplicate: at most 3 per entity
    entity_groups = {}
    for item in scored:
        entity = item["rule"].get("entity", "")
        if entity not in entity_groups:
            entity_groups[entity] = []
        entity_groups[entity].append(item)

    unique = []
    seen_entities = set()
    while len(unique) < top_k:
        added = False
        for entity, items in list(entity_groups.items()):
            if entity in seen_entities:
                continue
            if not items:
                continue
            unique.append(items[0])
            items.pop(0)
            added = True
            taken = sum(1 for u in unique if u["rule"].get("entity") == entity)
            if taken >= 3 or not items:
                seen_entities.add(entity)
            if len(unique) >= top_k:
                break
        if not added:
            break

    return unique


def format_knowledge_for_prompt(retrieved, max_rules=20):
    """Format retrieved knowledge rules into a compact prompt section."""
    if not retrieved:
        return "（无匹配知识库规则）"

    lines = ["## 知识库匹配规则（按相关度排序，融入解读时勿逐条罗列）", ""]
    for i, item in enumerate(retrieved[:max_rules]):
        lines.append(f"{i+1}. [{item['category']}] {item['condition']}")
        lines.append(f"   → {item['interpretation']}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    with open(OUTPUT_DIR / "chart_huajing.json", "r") as f:
        chart = json.load(f)

    results = retrieve_knowledge(chart, top_k=20)
    print(f"Retrieved {len(results)} rules\n")

    for i, item in enumerate(results[:15]):
        print(f"{i+1}. [{item['category']}] score={item['score']:.1f}")
        print(f"   Condition: {item['condition']}")
        print(f"   Matches: {item['matches']}")
        print(f"   → {item['interpretation'][:120]}")
        print()
