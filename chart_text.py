"""Shared chart-text builder — used by server.py and test_synthesis.py.

Assembles chart data into the structured prompt text the LLM consumes.
"""

from constants import RULERSHIP, SIGNS, PLANET_CN, ASPECT_CN, RECEPTION_CN


def _build_reception_lines(chart):
    """Build reception/mutual reception text lines from chart data."""
    receptions = chart.get("receptions", [])
    mutual = chart.get("mutual_receptions", [])
    lines = []
    if not receptions:
        return ["  （无）"]

    for r in receptions:
        guest_cn = PLANET_CN.get(r["guest"], r["guest"])
        for host in r["received_by"]:
            host_cn = PLANET_CN.get(host["planet"], host["planet"])
            levels_cn = "/".join(RECEPTION_CN.get(lv, lv) for lv in host["levels"])
            strong = "★" if set(host["levels"]) & {"domicile", "exaltation"} else " "
            lines.append(
                f"  {strong} {guest_cn} ← 被{host_cn}接纳 ({levels_cn})"
            )

    if mutual:
        mutual_pairs = []
        for a, b in mutual:
            a_cn = PLANET_CN.get(a, a)
            b_cn = PLANET_CN.get(b, b)
            mutual_pairs.append(f"{a_cn}⇄{b_cn}")
        lines.append(f"\n  互溶 (Mutual Reception): {', '.join(mutual_pairs)}")

    return lines


def build_chart_text(chart, question="", location_name=""):
    """Assemble chart data into the structured prompt section.

    Returns a string ready for insertion into the LLM user message.
    """
    planets = chart["planets"]
    sect = chart["sect"]
    sect_cn = "日生盘" if sect == "diurnal" else "夜生盘"
    birth_loc = location_name if location_name else chart["birth"]["location"]

    # House rulers
    house_rulers = {}
    for h_num, h_sign in chart["houses"].items():
        ruler = RULERSHIP[h_sign]
        house_rulers[str(h_num)] = {
            "sign": h_sign, "ruler": ruler,
            "ruler_house": planets[ruler]["house"],
            "ruler_sign": planets[ruler]["sign"],
        }

    def score_weight(p, pname):
        score = p["dignity_score"] + p["accidental_score"]
        if sect == "diurnal":
            if pname == "sun": score += 3
            elif pname in ["jupiter", "saturn"]: score += 2
        else:
            if pname == "moon": score += 3
            elif pname in ["venus", "mars"]: score += 2
        if pname == chart["chart_ruler"]: score += 3
        return score

    base_weights = {}
    for pname in ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn"]:
        base_weights[pname] = score_weight(planets[pname], pname)

    sorted_planets = sorted(base_weights.items(), key=lambda x: -x[1])
    planet_summary = []
    for pname, w in sorted_planets:
        p = planets[pname]
        retro = " 逆行" if p["retrograde"] else ""
        acc = ", ".join(p.get("accidental_details", []))
        planet_summary.append(
            f"{pname}: {p['sign']} {p['degree']}° {p['house']}宫 "
            f"({p['dignity']}{retro}, 偶然: {acc or '无'}, 权重{w:+d})"
        )

    aspect_summary = []
    for a in chart["aspects"]:
        aspect_summary.append(
            f"{a['planet_a']} {a['type']} {a['planet_b']} (orb {a['orb']}°)"
        )

    house_summary = []
    for h in sorted(chart["houses"].keys(), key=int):
        sign = chart["houses"][h]
        hr = house_rulers[str(h)]
        house_summary.append(
            f"H{h}: {sign} (宫主星 {hr['ruler']} 飞入 {hr['ruler_house']}宫 {hr['ruler_sign']})"
        )

    h4 = house_rulers["4"]
    h10 = house_rulers["10"]
    h12 = house_rulers["12"]

    nn = planets["north_node"]
    nn_sign_idx = SIGNS.index(nn["sign"])
    sn_sign = SIGNS[(nn_sign_idx + 6) % 12]
    sn_house = (nn["house"] - 1 + 6) % 12 + 1
    sn_degree = nn["degree"]

    node_aspects = [a for a in chart["aspects"]
                    if a["planet_a"] == "north_node" or a["planet_b"] == "north_node"]
    node_aspect_lines = []
    for a in node_aspects:
        other = a["planet_a"] if a["planet_b"] == "north_node" else a["planet_b"]
        if other in PLANET_CN:
            atype_cn = ASPECT_CN.get(a["type"], a["type"])
            node_aspect_lines.append(
                f"{PLANET_CN[other]}{atype_cn}北交 (orb {a['orb']}°), "
                f"该行星在{planets[other]['sign']} {planets[other]['house']}宫"
            )

    reception_lines = _build_reception_lines(chart)

    question_block = ""
    if question and question.strip():
        question_block = f"""
## 客户问题

> {question}

请在全面解读的基础上，重点回应客户的问题领域。
"""

    return f"""
## 星盘数据

出生: {chart['birth']['date']} {chart['birth']['time']} {birth_loc}
Sect: {sect} ({sect_cn})
上升: {chart['asc']} {chart['asc_degree']}°
命主星: {chart['chart_ruler']}
定位链: {' → '.join(chart['dispositor_chain'])}

### 行星配置（按权重排序）
{chr(10).join(planet_summary)}

### 宫位（含宫主星飞星）
{chr(10).join(house_summary)}

### 业力追溯关键宫位
- 4宫（父亲/家族根基）: {h4['sign']}, 宫主星 {h4['ruler']} 飞入 {h4['ruler_house']}宫 {h4['ruler_sign']}
- 10宫（母亲/社会期望）: {h10['sign']}, 宫主星 {h10['ruler']} 飞入 {h10['ruler_house']}宫 {h10['ruler_sign']}
- 12宫（代际业力/潜意识）: {h12['sign']}, 宫主星 {h12['ruler']} 飞入 {h12['ruler_house']}宫 {h12['ruler_sign']}
- 土星（业力标记）: {planets['saturn']['sign']} {planets['saturn']['degree']}° {planets['saturn']['house']}宫
- 月亮（母亲/养育经验）: {planets['moon']['sign']} {planets['moon']['degree']}° {planets['moon']['house']}宫

### 南北交点（业力轴线）
- 南交点: {sn_sign} {sn_degree}° {sn_house}宫（惯性模式）
- 北交点: {nn['sign']} {nn['degree']}° {nn['house']}宫（成长方向）
- 交点轴线: {sn_house}宫 ↔ {nn['house']}宫
- 与交点产生相位的行星:
{chr(10).join(f'  - {l}' for l in node_aspect_lines) if node_aspect_lines else '  （无）'}

### 关键相位
{chr(10).join(aspect_summary)}

### 接纳关系 (Reception)
{chr(10).join(reception_lines) if reception_lines else '  （无）'}

### 特殊点
福点: {chart['lots']['fortune']['sign']} {chart['lots']['fortune']['degree']}° {chart['lots']['fortune']['house']}宫
精神点: {chart['lots']['spirit']['sign']} {chart['lots']['spirit']['degree']}° {chart['lots']['spirit']['house']}宫
{question_block}
"""


