"""Test synthesis prompt with chart + knowledge + transits + client question."""
import json, os, requests, sys, argparse
from pathlib import Path

BASE = Path("/Users/lihuidong/Astrologist/model")
sys.path.insert(0, str(BASE))

from retrieve_knowledge import retrieve_knowledge, format_knowledge_for_prompt
from calc_transits import calc_firdaria, calc_profections, calc_transits
from chart_text import build_chart_text
from constants import RULERSHIP, PLANET_CN, SIGN_CN, ASPECT_CN

# ── Client question → domain mapping ──
# (house, natural significators, label)
DOMAIN_MAP = [
    {
        "keywords": ["感情", "情感", "婚姻", "恋爱", "伴侣", "对象", "正缘", "桃花", "分手", "离", "婚", "前任", "旧情", "遇到对的人", "脱单", "单身"],
        "houses": [7, 5],
        "significators": ["venus", "mars"],
        "label": "感情/婚恋",
    },
    {
        "keywords": ["事业", "工作", "职业", "跳槽", "转行", "升职", "创业", "老板", "领导", "同事", "自媒体"],
        "houses": [10, 6],
        "significators": ["sun", "saturn"],
        "label": "事业/工作",
    },
    {
        "keywords": ["财运", "钱", "收入", "投资", "理财", "资产", "负债", "工资", "赚钱", "财富", "股票", "基金", "财务"],
        "houses": [2, 8],
        "significators": ["venus"],
        "label": "财运/资源",
    },
    {
        "keywords": ["家庭", "父母", "爸妈", "父亲", "母亲", "爸", "妈", "家族", "原生家庭", "根", "房子", "搬家", "长辈", "亲人", "亲戚"],
        "houses": [4, 10],
        "significators": ["moon"],
        "label": "家庭/根基",
    },
    {
        "keywords": ["学习", "考试", "学业", "读书", "进修", "学历", "沟通", "表达", "写作", "说话", "自媒体"],
        "houses": [9, 3],
        "significators": ["mercury"],
        "label": "学习/沟通",
    },
    {
        "keywords": ["健康", "身体", "病", "疾病", "头疼", "睡眠", "肠胃", "慢性", "体检", "手术"],
        "houses": [1, 6],
        "significators": ["moon"],
        "label": "健康/身体",
    },
    {
        "keywords": ["人际", "社交", "朋友", "合伙", "合作", "搭档", "团队", "圈子", "粉丝", "人脉"],
        "houses": [11, 7],
        "significators": ["mercury"],
        "label": "人际/社交",
    },
    {
        "keywords": ["子女", "孩子", "孩子", "怀孕", "生育", "创作", "创造力", "兴趣爱好", "娱乐"],
        "houses": [5],
        "significators": ["jupiter"],
        "label": "子女/创造",
    },
    {
        "keywords": ["修行", "闭关", "业力", "因果", "修心", "灵性", "禅修", "打坐", "冥想"],
        "houses": [12, 9],
        "significators": ["saturn", "jupiter"],
        "label": "修行/灵性",
    },
]


def parse_question(question):
    """Parse a Chinese question into relevant life domains.

    Returns list of (label, houses, significators, matched_keywords).
    """
    if not question or question.strip() in ("", "无", "全面", "全部", "整体"):
        return []

    domains = []
    for dm in DOMAIN_MAP:
        matched = [kw for kw in dm["keywords"] if kw in question]
        if matched:
            domains.append((dm["label"], dm["houses"], dm["significators"], matched))
    return domains


def adjust_weights_for_question(base_weights, chart, domains):
    """Adjust planet weights based on client question domains.

    Rules (from prompt Step 3):
    - 命主星、Sect Light 始终不低于 +3
    - 客户指名领域的内行星 +2，外行星（土木天海冥）+1
    - 宫主星（飞星目标宫内的行星）+2
    - 飞星目标宫位内的其他行星 +1
    """
    INNER = {"sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn"}

    adjusted = dict(base_weights)
    explanations = []

    all_houses = set()
    all_significators = set()
    for _, houses, significators, _ in domains:
        all_houses.update(houses)
        all_significators.update(significators)

    if not all_houses:
        return adjusted, []

    houses_map = chart.get("houses", {})
    planets = chart.get("planets", {})

    # Find house rulers for each relevant house
    house_ruler_planets = set()
    for h_num in all_houses:
        h_sign = houses_map.get(str(h_num), "")
        if h_sign:
            ruler = RULERSHIP.get(h_sign)
            if ruler:
                house_ruler_planets.add(ruler)
                # Where does this ruler fly to?
                ruler_house = planets.get(ruler, {}).get("house", 0)
                explanations.append(f"{h_num}宫主星{ruler}飞入{ruler_house}宫")

    # Adjust weights
    for pname in INNER:
        boost = 0
        reasons = []

        # Natural significator boost
        if pname in all_significators:
            boost += 2
            reasons.append("自然征象星")

        # House ruler boost (宫主星)
        if pname in house_ruler_planets:
            boost += 2
            reasons.append("宫主星")

            # Also boost planets in the house that this ruler flies to
            ruler_house = planets.get(pname, {}).get("house", 0)
            for p2, pdata in planets.items():
                if p2 != pname and pdata.get("house") == ruler_house:
                    if p2 in adjusted:
                        adjusted[p2] += 1
                        explanations.append(f"{p2}因{pname}飞入{ruler_house}宫获得加权+1")

        if boost > 0:
            adjusted[pname] += boost
            explanations.append(f"{pname}: +{boost} ({', '.join(reasons)})")

    # Ensure floor: 命主星 and Sect Light at least +3
    chart_ruler = chart.get("chart_ruler", "")
    sect = chart.get("sect", "")
    sect_light = "sun" if sect == "diurnal" else "moon"

    for pname, floor_reason in [(chart_ruler, "命主星"), (sect_light, "Sect Light")]:
        if pname and adjusted.get(pname, 0) < 3:
            adjusted[pname] = max(adjusted.get(pname, 0), 3)
            explanations.append(f"{pname}: 保底+3 ({floor_reason})")

    return adjusted, explanations


# ═══════════════════════════════════════

# Parse args
parser = argparse.ArgumentParser()
parser.add_argument("--question", "-q", type=str, default="",
                    help="Client question (Chinese)")
parser.add_argument("--name", type=str, default="华净（龙华净，女）",
                    help="Client name")
parser.add_argument("--chart", type=str, default="chart_huajing.json",
                    help="Chart JSON filename in output/")
args = parser.parse_args()

# Load chart
chart_path = BASE / "output" / args.chart
if not chart_path.exists():
    print(f"Chart not found: {chart_path}")
    sys.exit(1)
with open(chart_path, "r") as f:
    chart = json.load(f)

# Load prompt
with open(BASE / "prompts/synthesis_v1.md", "r") as f:
    prompt_template = f.read()

# ── Parse client question ──
question = args.question.strip()
domains = parse_question(question)
if domains:
    print(f"Client question: {question}")
    for label, houses, sigs, keywords in domains:
        print(f"  → {label} (宫位: {houses}, 征象星: {sigs}, 匹配: {keywords})")
else:
    print("Client question: 全面解读")

# ── Knowledge retrieval ──
print("Retrieving knowledge...")
knowledge_results = retrieve_knowledge(chart, top_k=25)
knowledge_text = format_knowledge_for_prompt(knowledge_results, max_rules=20)
print(f"  → {len(knowledge_results)} relevant rules")

# ── Transit calculations ──
print("Calculating transits...")
birth_jd = chart["birth_jd"]
lat = chart["birth_lat"]
lon = chart["birth_lon"]

firdaria = calc_firdaria(birth_jd, chart["sect"])
profections = calc_profections(birth_jd, chart["asc"])
transits = calc_transits(birth_jd, lat, lon)

transit_text = f"""
## 推运数据

### 法达大运 (Firdaria)
当前年龄: {firdaria['age_years']} 岁
大运: {firdaria['major_planet']} ({firdaria['major_duration_years']}年, {firdaria['major_start'][0]}/{firdaria['major_start'][1]:02d} ~ {firdaria['major_end'][0]}/{firdaria['major_end'][1]:02d})
小运: {firdaria['sub_planet']} ({firdaria['sub_duration_years']}年, {firdaria['sub_start'][0]}/{firdaria['sub_start'][1]:02d} ~ {firdaria['sub_end'][0]}/{firdaria['sub_end'][1]:02d})

### 小限 (Annual Profections)
年龄: {profections['age']} 岁
激活宫位: {profections['profection_house']}宫 ({profections['activated_sign']})
时间主星: {profections['time_lord']}
区间: {profections['profection_start']} ~ {profections['profection_end']}

### 当前行运 (Transits) — {transits['date']}
"""
for ta in transits["transit_aspects"][:15]:
    t_cn = PLANET_CN.get(ta['transit_planet'], ta['transit_planet'])
    t_sign = SIGN_CN.get(ta['transit_sign'], ta['transit_sign'])
    asp = ASPECT_CN.get(ta['aspect_type'], ta['aspect_type'])
    n_cn = PLANET_CN.get(ta['natal_planet'], ta['natal_planet'])
    transit_text += f"行运{t_cn} {t_sign} {ta['transit_degree']}° {asp} 本命{n_cn}（容许度 {ta['orb']}°）\n"

# ── Build chart summary ──
planets = chart["planets"]
sect = chart["sect"]

house_rulers = {}
for h_num, h_sign in chart["houses"].items():
    ruler = RULERSHIP[h_sign]
    house_rulers[h_num] = {
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

if domains:
    final_weights, weight_explanations = adjust_weights_for_question(base_weights, chart, domains)
else:
    final_weights = dict(base_weights)
    weight_explanations = []

# ── Question-specific data ──
question_section = ""
if domains:
    domain_lines = []
    all_houses = set()
    for label, houses, sigs, keywords in domains:
        all_houses.update(houses)
        domain_lines.append(f"- **{label}**: 核心宫位 {houses}, 自然征象星 {sigs}")

    ruler_chain_lines = []
    for h_num in sorted(all_houses):
        hr = house_rulers.get(str(h_num))
        if hr:
            ruler_planet = hr["ruler"]
            ruler_data = planets[ruler_planet]
            ruler_chain_lines.append(
                f"  {h_num}宫 ({hr['sign']}) → 宫主星**{ruler_planet}**飞入{ruler_data['house']}宫{ruler_data['sign']}"
            )

    question_section = f"""
## 客户问题

> {question}

### 问题领域识别
{chr(10).join(domain_lines)}

### 相关宫主星链
{chr(10).join(ruler_chain_lines)}

### 权重调整说明
{chr(10).join(f'- {e}' for e in weight_explanations) if weight_explanations else '（无需调整）'}
"""

chart_text = build_chart_text(chart, "", chart['birth']['location'])

# ── Assemble user message ──
if domains:
    task_desc = f"客户的问题是关于{', '.join(d for d, _, _, _ in domains)}。请在全面解读的基础上，重点回应问题领域。"
else:
    task_desc = "无特定问题，请做全面解读。"

user_message = f"""以下是客户{args.name}的星盘数据和推运数据。

{chart_text}
{question_section}
{transit_text}

{knowledge_text}

请按照你的解读流程，为这位客户生成一份完整的本命盘+推运解读。

客户问题说明：{task_desc}

要求：
1. 命主星和 Sect Light 必须包含四层完整解读（基本特征、世俗优劣、业力焦点、解法），不能因输入内容增多而跳过任何一层
2. 每个核心张力必须给出具体的解法
3. 语言要具体、落地，用生活场景说话，不用抽象心理学术语
4. 用"你"称呼客户
5. 运势部分结合法达/小限/行运数据，指出当前所处的人生章节和行动窗口
6. 知识库规则是你解读的技术参考——选中最核心的 2-3 条融入文字，不要逐条罗列
7. 关键相位章节必须列出最重要的 3-5 个相位并各附一句解读
8. 如果客户有具体问题领域，在解读中重点回应这些领域"""

# ── Call API ──
api_key = os.environ.get("DEEPSEEK_API_KEY", "")
base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
if not api_key:
    raise RuntimeError("DEEPSEEK_API_KEY environment variable not set")

payload = {
    "model": "deepseek-v4-pro",
    "messages": [
        {"role": "user", "content": prompt_template + "\n\n---\n\n## 客户星盘与推运数据\n\n" + user_message},
    ],
    "max_tokens": 12288,
    "temperature": 0.7,
    "thinking": {"type": "enabled"},
}

headers = {
    "x-api-key": api_key,
    "Content-Type": "application/json",
    "anthropic-version": "2023-06-01",
}

chart_stem = Path(args.chart).stem  # e.g. "chart_huajing"
out_path = BASE / "output" / f"synthesis_{chart_stem}.md"

print("\nSending to DeepSeek...")
resp = requests.post(
    f"{base_url}/v1/messages",
    headers=headers,
    json=payload,
    timeout=600,
)

if resp.status_code == 200:
    data = resp.json()
    for block in data.get("content", []):
        if block.get("type") == "text":
            result = block["text"]
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(result)
            print(f"\n✓ Saved to {out_path}")
            print(f"Length: {len(result)} chars")
            print("\n" + "="*60)
            print(result)
else:
    print(f"API error: {resp.status_code}")
    print(resp.text[:1000])
