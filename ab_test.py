"""A/B test: knowledge base + max_tokens impact on synthesis quality and latency."""
import json, os, time, requests, sys
from pathlib import Path

BASE = Path("/Users/lihuidong/Astrologist/model")
sys.path.insert(0, str(BASE))

from retrieve_knowledge import retrieve_knowledge, format_knowledge_for_prompt
from calc_transits import calc_firdaria, calc_profections, calc_transits
from chart_text import build_chart_text
from constants import PLANET_CN, SIGN_CN, ASPECT_CN

api_key = os.environ.get("DEEPSEEK_API_KEY", "")
base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
if not api_key:
    print("ERROR: DEEPSEEK_API_KEY not set")
    sys.exit(1)

# Load chart
with open(BASE / "output/chart_lihui.json") as f:
    chart = json.load(f)

# Load prompt template
with open(BASE / "prompts/synthesis_v1.md") as f:
    prompt_template = f.read()

# Common data
birth_jd = chart["birth_jd"]
lat = chart["birth_lat"]
lon = chart["birth_lon"]

chart_text = build_chart_text(chart, "", "上海徐汇区")

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

### 当前行运 (Transits) — {transits['date']}
"""
for ta in transits["transit_aspects"][:15]:
    t_cn = PLANET_CN.get(ta['transit_planet'], ta['transit_planet'])
    t_sign = SIGN_CN.get(ta['transit_sign'], ta['transit_sign'])
    asp = ASPECT_CN.get(ta['aspect_type'], ta['aspect_type'])
    n_cn = PLANET_CN.get(ta['natal_planet'], ta['natal_planet'])
    transit_text += f"行运{t_cn} {t_sign} {ta['transit_degree']}° {asp} 本命{n_cn}（容许度 {ta['orb']}°）\n"

# Knowledge retrieval
knowledge_results = retrieve_knowledge(chart, top_k=25)
knowledge_text = format_knowledge_for_prompt(knowledge_results, max_rules=20)

# Base user message (without knowledge)
base_user_message = f"""以下是客户李慧的星盘数据和推运数据。

{chart_text}

{transit_text}

请按照你的解读流程，为这位客户生成一份完整的本命盘+推运解读。

客户问题说明：无特定问题，请做全面解读

要求：
1. 命主星和 Sect Light 必须包含四层完整解读（基本特征、世俗优劣、业力焦点、解法），不能因输入内容增多而跳过任何一层
2. 每个核心张力必须给出具体的解法
3. 语言要具体、落地，用生活场景说话，不用抽象心理学术语
4. 用"你"称呼客户
5. 运势部分结合法达/小限/行运数据，指出当前所处的人生章节和行动窗口
6. 知识库规则是你解读的技术参考——选中最核心的 2-3 条融入文字，不要逐条罗列
7. 关键相位章节必须列出最重要的 3-5 个相位并各附一句解读"""

# ── Variant A (current): knowledge ON, max_tokens 12288 ──
msg_a = base_user_message + f"""

{knowledge_text}"""

headers = {
    "x-api-key": api_key,
    "Content-Type": "application/json",
    "anthropic-version": "2023-06-01",
}

print("=" * 60)
print("A/B TEST: lihui chart, no specific question")
print(f"Prompt template: {len(prompt_template)} chars")
print(f"A: knowledge ON ({len(knowledge_text)} chars), max_tokens=12288")
print(f"B: knowledge OFF, max_tokens=6144")
print("=" * 60)

# Run A
print("\n[Sending A — current]...")
t0 = time.time()
resp_a = requests.post(
    f"{base_url}/v1/messages",
    headers=headers,
    json={
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "user", "content": prompt_template + "\n\n---\n\n## 客户星盘与推运数据\n\n" + msg_a},
        ],
        "max_tokens": 12288,
        "temperature": 0.7,
        "thinking": {"type": "enabled"},
    },
    timeout=600,
)
t_a = time.time() - t0

# Run B
print("[Sending B — lean]...")
t0 = time.time()
resp_b = requests.post(
    f"{base_url}/v1/messages",
    headers=headers,
    json={
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "user", "content": prompt_template + "\n\n---\n\n## 客户星盘与推运数据\n\n" + base_user_message},
        ],
        "max_tokens": 6144,
        "temperature": 0.7,
        "thinking": {"type": "enabled"},
    },
    timeout=600,
)
t_b = time.time() - t0

# ── Results ──
print("\n" + "=" * 60)
print("RESULTS")
print("=" * 60)

for label, resp, elapsed in [("A (knowledge ON, 12288)", resp_a, t_a), ("B (knowledge OFF, 6144)", resp_b, t_b)]:
    print(f"\n--- {label} ---")
    print(f"Status: {resp.status_code}, Time: {elapsed:.0f}s")
    if resp.status_code == 200:
        data = resp.json()
        for block in data.get("content", []):
            if block.get("type") == "text":
                text = block["text"]
                print(f"Output: {len(text)} chars")
                # Save
                variant = "A" if "12288" in label else "B"
                out_path = BASE / "output" / f"ab_test_{variant}_lihui.md"
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(text)
                print(f"Saved: {out_path}")
                # Show first 300 chars as preview
                print(f"Preview: {text[:300]}...")
        usage = data.get("usage", {})
        if usage:
            print(f"Usage: input={usage.get('input_tokens')}, output={usage.get('output_tokens')}")
    else:
        print(f"Error: {resp.text[:500]}")

print("\nDone.")
