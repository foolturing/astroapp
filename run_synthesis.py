"""Reusable CLI synthesis script. Reads API key from .env file, never hardcodes it.

Usage:
    python3 run_synthesis.py --chart chart_yuki.json -q "想知道工作和财富..." --out yuki_career_20260524.md
    python3 run_synthesis.py --chart chart_yuki.json --house P -q "感情运势" --out yuki_love.md
    python3 run_synthesis.py --birth 1984/08/26 04:15 28.01 120.65 8 -q "事业方向" --out test.md
"""
import argparse, json, os, sys, time, requests
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# ── Load .env ──
env_path = BASE / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                if key.strip() not in os.environ:
                    os.environ[key.strip()] = val.strip()
else:
    print("Warning: .env not found at", env_path, "— using existing env vars")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")

if not DEEPSEEK_API_KEY:
    sys.exit("Error: DEEPSEEK_API_KEY not set. Create a .env file or export the variable.")

from calc_chart import calc_chart
from calc_transits import calc_firdaria, calc_profections, calc_transits
from retrieve_knowledge import retrieve_knowledge, format_knowledge_for_prompt
from chart_text import build_chart_text
from constants import PLANET_CN, SIGN_CN, ASPECT_CN

with open(BASE / "prompts" / "synthesis_v1.md", "r") as f:
    PROMPT_TEMPLATE = f.read()


def run(args):
    # Parse birth data — either from chart JSON or raw args
    if args.chart:
        with open(args.chart) as f:
            chart_data = json.load(f)
        birth = chart_data["birth"]
        y, m, d = [int(x) for x in birth["date"].split("/")]
        h, mn = [int(x) for x in birth["time"].split(":")]
        lat, lon = birth["lat"], birth["lon"]
        tz = birth.get("tz_offset", 8)
        location_name = birth.get("location", "")
    elif args.birth:
        parts = args.birth
        y, m, d = [int(x) for x in parts[0].split("/")]
        h, mn = [int(x) for x in parts[1].split(":")]
        lat, lon = float(parts[2]), float(parts[3])
        tz = int(parts[4]) if len(parts) > 4 else 8
        location_name = ""
    else:
        sys.exit("Either --chart or --birth is required")

    house_system = b"P" if args.house == "P" else b"W"

    print(f"Calculating chart for {y}/{m:02d}/{d:02d} {h:02d}:{mn:02d}...")
    chart = calc_chart(y, m, d, h, mn, lat, lon, tz, house_system=house_system)

    print("Retrieving knowledge...")
    knowledge_results = retrieve_knowledge(chart, top_k=25)
    knowledge_text = format_knowledge_for_prompt(knowledge_results, max_rules=20)

    print("Calculating transits...")
    firdaria = calc_firdaria(chart["birth_jd"], chart["sect"])
    profections = calc_profections(chart["birth_jd"], chart["asc"])
    transits = calc_transits(chart["birth_jd"], lat, lon)

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
        t_cn = PLANET_CN.get(ta["transit_planet"], ta["transit_planet"])
        t_sign = SIGN_CN.get(ta["transit_sign"], ta["transit_sign"])
        asp = ASPECT_CN.get(ta["aspect_type"], ta["aspect_type"])
        n_cn = PLANET_CN.get(ta["natal_planet"], ta["natal_planet"])
        transit_text += f"行运{t_cn} {t_sign} {ta['transit_degree']}° {asp} 本命{n_cn}（容许度 {ta['orb']}°）\n"

    chart_text = build_chart_text(chart, args.question, location_name)

    question_block = ""
    if args.question:
        question_block = f"""
## 客户问题

客户问：**{args.question}**

请在输出结构的"回应你的问题"章节直接回应。调用第三步的宫主星+飞星逻辑，找出问题对应宫位及其宫主星飞入的宫位，结合相关行星的四层解读，给出具体分析。
"""
    else:
        question_block = "\n## 客户问题\n\n客户无特定问题，请做全面解读。\n"

    user_message = f"""{question_block}
以下是客户的星盘数据和推运数据。

{chart_text}

{transit_text}

{knowledge_text}

请按照你的解读流程，为这位客户生成一份完整的本命盘+推运解读。

要求：
1. 命主星和 Sect Light 必须包含四层完整解读（基本特征、世俗优劣、业力焦点、解法），不能因输入内容增多而跳过任何一层
2. 每个核心张力必须给出具体的解法
3. 语言要具体、落地，用生活场景说话，不用抽象心理学术语
4. 用"你"称呼客户
5. 运势部分结合法达/小限/行运数据，指出当前所处的人生章节和行动窗口。行动窗口必须写具体：什么时间、适合做什么、为什么是这个时机
6. 关键相位章节必须列出最重要的 3-5 个相位并各附一句解读
7. 禁止任何只有标题没有内容的空段落——每个 ## 或 ### 标题下面必须有至少一段实质内容"""

    payload = {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "user", "content": PROMPT_TEMPLATE + "\n\n---\n\n## 客户星盘与推运数据\n\n" + user_message},
        ],
        "max_tokens": 12288,
        "temperature": 0.7,
        "thinking": {"type": "enabled"},
    }

    headers = {
        "x-api-key": DEEPSEEK_API_KEY,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    print("Calling DeepSeek API (this takes 2-4 minutes)...")
    t0 = time.time()
    for attempt in range(2):
        resp = requests.post(f"{DEEPSEEK_BASE_URL}/v1/messages", headers=headers, json=payload, timeout=600)
        if resp.status_code == 200:
            break
        if attempt == 0:
            time.sleep(2)

    elapsed = time.time() - t0
    if resp.status_code != 200:
        sys.exit(f"API error: {resp.status_code} — {resp.text[:500]}")

    data = resp.json()
    synthesis = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            synthesis = block["text"]
            break

    if not synthesis:
        sys.exit("No text in DeepSeek response")

    out_path = args.out
    if not out_path:
        out_path = f"output/synthesis_{int(time.time())}.md"

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(synthesis)

    print(f"Done in {elapsed:.0f}s. Saved to {out_path} ({len(synthesis)} chars)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a Hellenistic astrology synthesis report")
    parser.add_argument("--chart", help="Path to chart JSON (e.g. output/chart_yuki.json)")
    parser.add_argument("--birth", nargs="+", metavar=("DATE", "TIME", "LAT", "LON", "TZ"),
                        help="Raw birth data: '1984/08/26' '04:15' 28.01 120.65 8")
    parser.add_argument("-q", "--question", default="", help="Client question")
    parser.add_argument("--house", default="P", choices=["P", "W"], help="House system: P=Placidus, W=Whole Sign")
    parser.add_argument("--out", help="Output file path (default: output/synthesis_<ts>.md)")
    args = parser.parse_args()
    run(args)
