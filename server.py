"""Astrologist API server — wraps chart calc + knowledge + synthesis into a web service."""
import json, os, re, sys, logging, threading, uuid, time, html
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, request, jsonify, render_template_string, g

BASE = Path(__file__).resolve().parent
OUTPUT_DIR = BASE / "output"
sys.path.insert(0, str(BASE))

from calc_chart import calc_chart
from calc_transits import calc_firdaria, calc_profections, calc_transits
from retrieve_knowledge import retrieve_knowledge, format_knowledge_for_prompt
from china_cities import PROVINCES
from chart_text import build_chart_text
from constants import PLANET_CN, SIGN_CN, ASPECT_CN

log = logging.getLogger(__name__)

app = Flask(__name__)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
DISCOUNT_CODES = set(c.strip() for c in os.environ.get("DISCOUNT_CODES", "EARLY50,TESTER").split(",") if c.strip())

# ── In-memory task store ──
_task_store = {}  # task_id → {status, result, error, created_at}
_task_lock = threading.Lock()
_rate_buckets = {}  # key → [timestamps]
_valid_sessions = {}  # session_id → {"ip": str, "created_at": float}
_global_times = []  # [(timestamp, ip), ...] for global rate monitoring

# ── Prompt template ──
with open(BASE / "prompts" / "synthesis_v1.md", "r") as f:
    PROMPT_TEMPLATE = f.read()


# CORE PIPELINE
# ═══════════════════════════════════════════════════════════

def run_synthesis(birth, question, house_system="P"):
    """Run the full pipeline: calc → knowledge → transits → synthesis."""
    y, m, d = birth["date"].split("/")
    h, mn = birth["time"].split(":")
    lat, lon = birth["lat"], birth["lon"]
    tz = birth.get("tz", 8)

    hsys = b'W' if house_system == "W" else b'P'

    chart = calc_chart(int(y), int(m), int(d), int(h), int(mn), lat, lon, tz, house_system=hsys)

    knowledge_results = retrieve_knowledge(chart, top_k=25)
    knowledge_text = format_knowledge_for_prompt(knowledge_results, max_rules=20)

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
        t_cn = PLANET_CN.get(ta['transit_planet'], ta['transit_planet'])
        t_sign = SIGN_CN.get(ta['transit_sign'], ta['transit_sign'])
        asp = ASPECT_CN.get(ta['aspect_type'], ta['aspect_type'])
        n_cn = PLANET_CN.get(ta['natal_planet'], ta['natal_planet'])
        transit_text += f"行运{t_cn} {t_sign} {ta['transit_degree']}° {asp} 本命{n_cn}（容许度 {ta['orb']}°）\n"

    chart_text = build_chart_text(chart, question, birth.get("location_name", ""))

    user_message = f"""以下是客户的星盘数据和推运数据。

{chart_text}

{transit_text}

{knowledge_text}

请按照你的解读流程，为这位客户生成一份完整的本命盘+推运解读。

客户问题说明：{"客户的问题是" + question if question else "无特定问题，请做全面解读"}

要求：
1. 命主星和 Sect Light 必须包含四层完整解读（基本特征、世俗优劣、业力焦点、解法），不能因输入内容增多而跳过任何一层
2. 每个核心张力必须给出具体的解法
3. 语言要具体、落地，用生活场景说话，不用抽象心理学术语
4. 用"你"称呼客户
5. 运势部分结合法达/小限/行运数据，指出当前所处的人生章节和行动窗口。行动窗口必须写具体：什么时间、适合做什么、为什么是这个时机
6. 关键相位章节必须列出最重要的 3-5 个相位并各附一句解读
7. 如果客户有具体问题领域，在解读中重点回应这些领域
8. 禁止任何只有标题没有内容的空段落——每个 ## 或 ### 标题下面必须有至少一段实质内容"""

    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY environment variable not set")

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

    for attempt in range(2):
        resp = requests.post(
            f"{DEEPSEEK_BASE_URL}/v1/messages",
            headers=headers,
            json=payload,
            timeout=600,
        )
        if resp.status_code == 200:
            break
        if attempt == 0:
            time.sleep(2)

    if resp.status_code != 200:
        raise Exception(f"DeepSeek API error: {resp.status_code} — {resp.text[:500]}")

    data = resp.json()
    for block in data.get("content", []):
        if block.get("type") == "text":
            return block["text"]

    raise Exception("No text in DeepSeek response")


# ═══════════════════════════════════════════════════════════
# MARKDOWN → CLEAN HTML (handles synthesis output patterns)
# ═══════════════════════════════════════════════════════════

import re

def md_to_html(text):
    """Convert synthesis markdown to clean HTML, stripping # ** symbols."""
    lines = text.split("\n")
    out = []
    buf = []       # paragraph buffer
    list_buf = []  # unordered list item buffer
    ol_buf = []    # ordered list item buffer
    in_code = False

    def flush():
        nonlocal buf
        if buf:
            out.append("<p>" + " ".join(buf) + "</p>")
            buf = []

    def flush_list():
        nonlocal list_buf, ol_buf
        if list_buf:
            out.append("<ul>" + "".join(f"<li>{li}</li>" for li in list_buf) + "</ul>")
            list_buf = []
        if ol_buf:
            out.append("<ol>" + "".join(f"<li>{li}</li>" for li in ol_buf) + "</ol>")
            ol_buf = []

    for line in lines:
        # Code block toggle
        if line.strip() == "```":
            flush(); flush_list()
            in_code = not in_code
            continue
        if in_code:
            continue

        # Empty line → flush paragraphs
        if not line.strip():
            flush(); flush_list()
            continue

        # Horizontal rule
        if line.strip() == "---":
            flush(); flush_list()
            out.append("<hr>")
            continue

        # Headings
        if line.startswith("### "):
            flush(); flush_list()
            out.append(f"<h3>{_inline(line[4:])}</h3>")
            continue
        if line.startswith("## "):
            flush(); flush_list()
            out.append(f"<h2>{_inline(line[3:])}</h2>")
            continue

        # Ordered list items
        m = re.match(r"^\d+\.\s+(.+)$", line)
        if m:
            flush()
            if list_buf:
                flush_list()
            ol_buf.append(_inline(m.group(1)))
            continue

        # Unordered list items
        m = re.match(r"^[-*]\s+(.+)$", line)
        if m:
            flush()
            if ol_buf:
                flush_list()
            list_buf.append(_inline(m.group(1)))
            continue

        # Regular paragraph line
        buf.append(_inline(line))

    flush(); flush_list()
    return "\n".join(out)


def _inline(text):
    """Escape HTML, then apply inline markdown formatting."""
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    return text

FORM_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>星盘解读</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f0f0f; color: #e0e0e0; padding: 20px; max-width: 480px; margin: 0 auto; }
  h1 { font-size: 1.5em; text-align: center; margin: 24px 0 8px; }
  .sub { text-align: center; color: #888; font-size: .85em; margin-bottom: 24px; }
  label { display: block; font-size: .85em; color: #aaa; margin: 14px 0 4px; }
  input, select, textarea { width: 100%; padding: 10px 12px; border: 1px solid #333; border-radius: 8px; background: #1a1a1a; color: #e0e0e0; font-size: 1em; }
  input:focus, select:focus, textarea:focus { border-color: #c9a84c; outline: none; }
  .row { display: flex; gap: 10px; }
  .row > * { flex: 1; }
  button { width: 100%; padding: 14px; margin: 24px 0; border: none; border-radius: 8px; background: #c9a84c; color: #1a1a1a; font-size: 1.1em; font-weight: 600; cursor: pointer; }
  button:disabled { background: #555; cursor: not-allowed; }
  .disclaimer { font-size: .8em; color: #aaa; line-height: 1.6; margin: 16px 0; padding: 12px; border: 1px solid #333; border-radius: 6px; background: #1a1a1a; }
  .disclaimer p { margin: 0 0 6px; }
  .disclaimer label { font-size: 1em; color: #c9a84c; display: inline; cursor: pointer; }
  .disclaimer input[type=checkbox] { width: auto; margin-right: 6px; vertical-align: middle; accent-color: #c9a84c; }
  #result { background: #1a1a1a; border-radius: 8px; padding: 20px; margin-top: 20px; line-height: 1.85; font-size: .95em; display: none; }
  #result h2 { font-size: 1.2em; color: #c9a84c; margin: 24px 0 10px; padding-bottom: 6px; border-bottom: 1px solid #333; }
  #feedback { display: none; background: #1a1a1a; border-radius: 8px; padding: 20px; margin-top: 16px; }
  #feedback h3 { font-size: 1em; color: #c9a84c; margin: 0 0 12px; }
  #feedback .f-row { margin-bottom: 14px; }
  #feedback .f-label { font-size: .85em; color: #aaa; margin-bottom: 6px; display: block; }
  #feedback .stars { display: flex; gap: 8px; }
  #feedback .stars button { width: 40px; height: 40px; border-radius: 50%; border: 1px solid #555; background: transparent; color: #aaa; font-size: 1.1em; cursor: pointer; margin: 0; padding: 0; }
  #feedback .stars button:hover, #feedback .stars button.active { background: #c9a84c33; border-color: #c9a84c; color: #c9a84c; }
  #feedback .tags { display: flex; flex-wrap: wrap; gap: 6px; }
  #feedback .tags button { background: transparent; border: 1px solid #555; color: #aaa; padding: 6px 12px; border-radius: 16px; font-size: .8em; cursor: pointer; margin: 0; }
  #feedback .tags button:hover, #feedback .tags button.active { background: #c9a84c33; border-color: #c9a84c; color: #c9a84c; }
  #feedback textarea { width: 100%; background: #111; border: 1px solid #444; color: #ccc; padding: 10px; border-radius: 6px; font-size: .85em; resize: vertical; min-height: 60px; box-sizing: border-box; }
  #feedback .f-submit { width: 100%; padding: 10px; background: #c9a84c; color: #1a1a1a; border: none; border-radius: 6px; font-weight: 600; cursor: pointer; margin-top: 4px; }
  #feedback .f-submit:disabled { background: #555; cursor: not-allowed; }
  #feedback .f-thanks { text-align: center; color: #c9a84c; font-size: .9em; padding: 12px 0; display: none; }
  #result h3 { font-size: 1.05em; color: #d4b95e; margin: 18px 0 8px; }
  #result p { margin: 6px 0 12px; }
  #result ul { margin: 8px 0; padding-left: 20px; }
  #result li { margin: 4px 0; }
  #result strong { color: #f0d060; font-weight: 600; }
  #result hr { border: none; border-top: 1px solid #333; margin: 20px 0; }
  #loading { display: none; text-align: center; padding: 40px; color: #888; }
  .spinner { width: 40px; height: 40px; border: 3px solid #333; border-top-color: #c9a84c; border-radius: 50%; animation: spin .8s linear infinite; margin: 0 auto 16px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .error { color: #e05555; background: #2a1515; padding: 12px; border-radius: 8px; margin-top: 12px; }
  .note { font-size: .75em; color: #666; margin-top: 2px; }
  .save-bar { display: flex; gap: 10px; margin-top: 24px; padding-top: 16px; border-top: 1px solid #333; }
  .save-bar button { flex: 1; padding: 10px; font-size: .9em; background: #2a2a2a; color: #c9a84c; border: 1px solid #444; }
  .save-bar button:hover { background: #333; }
  .toast { position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%); background: #c9a84c; color: #1a1a1a; padding: 10px 24px; border-radius: 20px; font-size: .9em; font-weight: 600; z-index: 99; opacity: 0; transition: opacity .3s; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>

<h1>星盘解读</h1>
<p class="sub">基于希腊占星传统 + AI 生成</p>

<form id="form">
  <label>姓名 / 昵称</label>
  <input type="text" name="name" placeholder="例：小王" required>

  <label>出生日期</label>
  <input type="text" name="date" placeholder="yyyy/mm/dd" pattern="\\d{4}/\\d{2}/\\d{2}" inputmode="numeric" required>

  <label>出生时间</label>
  <input type="time" name="time" placeholder="尽量精确到分钟" required>

  <label>出生地点</label>
  <div class="row">
    <select name="province" id="province" required>
      <option value="">选择省份</option>
      {% for pname in provinces %}
      <option value="{{ pname }}">{{ pname }}</option>
      {% endfor %}
      <option value="custom">其他（手动输入）</option>
    </select>
    <select name="city" id="city" required>
      <option value="">选择城市</option>
    </select>
  </div>
	  <select name="district" id="district" style="margin-top:8px">
	    <option value="">选择区/县</option>
	  </select>
  <div id="custom_coords" style="display:none">
    <div class="row">
      <div><label>纬度 (lat)</label><input type="number" step="0.01" name="lat" placeholder="例 31.2"></div>
      <div><label>经度 (lon)</label><input type="number" step="0.01" name="lon" placeholder="例 121.5"></div>
    </div>
  </div>

  <label>时区</label>
  <select name="tz">
    <option value="8">UTC+8（中国标准时间）</option>
    <option value="9">UTC+9</option>
    <option value="7">UTC+7</option>
    <option value="0">UTC+0</option>
    <option value="-5">UTC-5（美东）</option>
    <option value="-8">UTC-8（美西）</option>
  </select>

  <label>宫位制</label>
  <select name="house">
    <option value="P">Placidus</option>
    <option value="W">整宫制 (Whole Sign)</option>
  </select>

  <label>想问什么？（选填）</label>
  <textarea name="question" rows="3" placeholder="例：事业发展、感情运势、财运方向...&#10;留空 = 全面解读"></textarea>

	  <label>折扣码</label>
	  <input type="text" name="discount" placeholder="输入折扣码解锁完整报告" autocomplete="off">

	  <label>访问令牌</label>
	  <input type="password" id="token" name="token" placeholder="输入访问令牌" autocomplete="off">

	  <div class="disclaimer">
	    <p>本工具由 AI 生成解读，仅供兴趣参考。相关内容不构成医疗、法律、投资或心理咨询建议。</p>
	    <p>隐私说明：出生信息仅用于生成星盘解读，处理后不会公开或分享。出生数据会发送至 DeepSeek（AI 服务商）以生成解读文本。解读内容可能被匿名化后用于服务质量改进。建议姓名一栏使用昵称，勿填真实姓名。勾选即表示您已了解并同意上述说明。</p>
	    <label><input type="checkbox" id="agree" required> 我已阅读并同意以上条款</label>
	  </div>

	  <button type="submit" id="btn">开始解读</button>
</form>

<div id="loading">
  <div class="spinner"></div>
  <p>正在生成解读，约需 30-60 秒…</p>
</div>

<div id="result">
  <div id="result-content"></div>
  <div class="save-bar" id="save-bar" style="display:none">
    <button onclick="copyResult()">复制全文</button>
    <button onclick="downloadResult()">下载文本</button>
  </div>
</div>
<div class="toast" id="toast"></div>

<div id="feedback">
  <h3>帮助我们做得更好（2-3 题，约 10 秒）</h3>
  <div class="f-row">
    <span class="f-label">解读的整体质量</span>
    <div class="stars" id="stars-quality">
      <button data-v="1">1</button><button data-v="2">2</button><button data-v="3">3</button><button data-v="4">4</button><button data-v="5">5</button>
    </div>
  </div>
  <div class="f-row">
    <span class="f-label">不满意的点（可多选，没有可不选）</span>
    <div class="tags" id="tags-issues">
      <button data-v="太抽象，不够落地">太抽象</button>
      <button data-v="内容太少，不够详细">内容太少</button>
      <button data-v="太啰嗦，抓不住重点">太啰嗦</button>
      <button data-v="术语太多，看不懂">术语太多</button>
      <button data-v="推运部分不够具体">推运不具体</button>
      <button data-v="缺少具体的行动建议">缺行动建议</button>
    </div>
  </div>
  <div class="f-row">
    <span class="f-label">还有什么想说的（选填）</span>
    <textarea id="f-text" placeholder="任何建议或吐槽…"></textarea>
  </div>
  <button class="f-submit" id="f-submit" onclick="submitFeedback()">提交反馈</button>
  <div class="f-thanks" id="f-thanks">感谢反馈！</div>
</div>

<script>
// Province → city data
var PROVINCES = {{ provinces | tojson }};

var provinceEl = document.getElementById('province');
var cityEl = document.getElementById('city');
var districtEl = document.getElementById('district');
var customDiv = document.getElementById('custom_coords');

provinceEl.addEventListener('change', function() {
  var p = this.value;
  cityEl.innerHTML = '<option value="">选择城市</option>';
  districtEl.innerHTML = '<option value="">选择区/县</option>';
  customDiv.style.display = 'none';
  if (p === 'custom') {
    customDiv.style.display = 'block';
    cityEl.innerHTML = '<option value="">（手动输入经纬度）</option>';
    return;
  }
  if (!PROVINCES[p]) return;
  var cities = PROVINCES[p];
  var names = Object.keys(cities).sort(function(a, b) {
    return a.localeCompare(b, 'zh-CN');
  });
  names.forEach(function(name) {
    var opt = document.createElement('option');
    opt.value = cities[name][0] + ',' + cities[name][1];
    opt.textContent = name;
    cityEl.appendChild(opt);
  });
});

cityEl.addEventListener('change', function() {
  var p = provinceEl.value;
  var c = this.selectedOptions[0] ? this.selectedOptions[0].textContent : '';
  districtEl.innerHTML = '<option value="">选择区/县</option>';
  if (!p || p === 'custom' || !PROVINCES[p] || !PROVINCES[p][c]) return;
  var districts = PROVINCES[p][c][2];
  districts.forEach(function(d) {
    var opt = document.createElement('option');
    if (Array.isArray(d)) {
      // d = [name, lat, lon] — district has its own coordinates
      opt.value = d[1] + ',' + d[2];
      opt.textContent = d[0];
    } else {
      // d = "name" — inherits city coordinates
      opt.value = d;
      opt.textContent = d;
    }
    districtEl.appendChild(opt);
  });
});

document.getElementById('form').addEventListener('submit', async function(e) {
  e.preventDefault();
  var btn = document.getElementById('btn');
  var loading = document.getElementById('loading');
  var result = document.getElementById('result');
  if (!document.getElementById('agree').checked) {
    document.getElementById('result-content').innerHTML = '<div class="error">请先阅读并同意免责条款</div>';
    document.getElementById('save-bar').style.display = 'none';
    result.style.display = 'block';
    return;
  }
  btn.disabled = true;
  loading.style.display = 'block';
  result.style.display = 'none';

  var fd = new FormData(this);
  var dateVal = fd.get('date').trim();
  if (!/^\\d{4}\\/\\d{2}\\/\\d{2}$/.test(dateVal)) {
    document.getElementById('result-content').innerHTML = '<div class="error">日期格式错误，请输入 yyyy/mm/dd（例：1987/12/21）</div>';
    document.getElementById('save-bar').style.display = 'none';
    btn.disabled = false;
    loading.style.display = 'none';
    result.style.display = 'block';
    return;
  }
  var lat, lon, districtName = '';
  if (fd.get('province') === 'custom') {
    lat = parseFloat(fd.get('lat'));
    lon = parseFloat(fd.get('lon'));
  } else {
    var cityVal = fd.get('city');
    if (!cityVal || cityVal.indexOf(',') === -1) {
      document.getElementById('result-content').innerHTML = '<div class="error">请选择出生城市</div>';
      document.getElementById('save-bar').style.display = 'none';
      btn.disabled = false;
      loading.style.display = 'none';
      result.style.display = 'block';
      return;
    }
    var parts = cityVal.split(',');
    lat = parseFloat(parts[0]);
    lon = parseFloat(parts[1]);
    // District may override coordinates (for municipalities with per-district coords)
    var districtVal = fd.get('district');
    if (districtVal && districtVal.indexOf(',') !== -1) {
      var dparts = districtVal.split(',');
      lat = parseFloat(dparts[0]);
      lon = parseFloat(dparts[1]);
      // district name is in the selected option's text
      districtName = districtEl.selectedOptions[0] ? districtEl.selectedOptions[0].textContent : '';
    } else {
      districtName = districtVal || '';
    }
  }

  const body = {
    name: fd.get('name'),
    discount: fd.get('discount').trim(),
    birth: {
      date: fd.get('date'),
      time: fd.get('time'),
      lat: lat,
      lon: lon,
      tz: parseInt(fd.get('tz')),
      district: districtName,
      location_name: (fd.get('province') === 'custom' ? '手动输入' : (fd.get('province') || '') + (cityEl.selectedOptions[0] ? cityEl.selectedOptions[0].textContent : '') + (districtName ? districtName : ''))
    },
    question: fd.get('question') || '',
    house_system: fd.get('house')
  };

  try {
    var authToken = document.getElementById('token').value;
    var sessionId = '{{ session_id }}';
    var headers = {'Content-Type': 'application/json', 'X-Session-ID': sessionId};
    if (authToken) headers['Authorization'] = 'Bearer ' + authToken;

    // Step 1: submit task
    var submitResp = await fetch('/api/synthesis', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(body)
    });
    if (!submitResp.ok) {
      var errData = await submitResp.json().catch(function(){ return {}; });
      throw new Error(errData.error || '提交失败 (' + submitResp.status + ')');
    }
    var submitData = await submitResp.json();
    var taskId = submitData.task_id;

    // Step 2: poll for result
    var attempts = 0;
    var maxAttempts = 120; // 4 minutes max
    while (attempts < maxAttempts) {
      await new Promise(function(r) { setTimeout(r, 2000); });
      attempts++;
      var pollResp = await fetch('/api/task/' + taskId, {headers: headers});
      if (!pollResp.ok) continue;
      var pollData = await pollResp.json();
      if (pollData.status === 'done') {
        document.getElementById('result-content').innerHTML = pollData.result.html;
        document.getElementById('save-bar').style.display = 'flex';
        window._synthesisRaw = pollData.result.synthesis;
        break;
      } else if (pollData.status === 'error') {
        document.getElementById('result-content').innerHTML = '<div class="error">' + (pollData.error || '未知错误') + '</div>';
        document.getElementById('save-bar').style.display = 'none';
        break;
      }
      // Show progress
      var dots = '.'.repeat(attempts % 4);
      document.getElementById('loading').querySelector('p').textContent = '正在生成解读，约需 30-60 秒' + dots;
    }
    if (attempts >= maxAttempts) {
      document.getElementById('result-content').innerHTML = '<div class="error">解读超时，请稍后重试</div>';
      document.getElementById('save-bar').style.display = 'none';
    }
  } catch(err) {
    document.getElementById('result-content').innerHTML = '<div class="error">请求失败: ' + err.message + '</div>';
    document.getElementById('save-bar').style.display = 'none';
  } finally {
    btn.disabled = false;
    loading.style.display = 'none';
    result.style.display = 'block';
    result.scrollIntoView({behavior: 'smooth'});
  }
});

function copyResult() {
  var text = window._synthesisRaw || '';
  if (!text) return;
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(function() { showToast('已复制到剪贴板'); });
    return;
  }
  // fallback for HTTP
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  ta.style.top = '-9999px';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand('copy');
    showToast('已复制到剪贴板');
  } catch(e) {
    showToast('复制失败，请长按手动复制');
  }
  document.body.removeChild(ta);
}

function downloadResult() {
  var text = window._synthesisRaw || '';
  if (!text) return;
  var blob = new Blob([text], {type: 'text/plain;charset=utf-8'});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = '星盘解读_' + new Date().toISOString().slice(0,10) + '.txt';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function() { URL.revokeObjectURL(url); }, 100);
  showToast('下载完成');
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2000);
}

// ── Feedback Survey ──
let feedbackState = { quality: 0, issues: [] };

document.querySelectorAll('#stars-quality button').forEach(btn => {
  btn.addEventListener('click', function() {
    feedbackState.quality = parseInt(this.dataset.v);
    document.querySelectorAll('#stars-quality button').forEach(b => b.classList.remove('active'));
    for (let b of document.querySelectorAll('#stars-quality button')) {
      if (parseInt(b.dataset.v) <= feedbackState.quality) b.classList.add('active');
    }
    document.getElementById('feedback').style.display = 'block';
  });
});

document.querySelectorAll('#tags-issues button').forEach(btn => {
  btn.addEventListener('click', function() {
    const v = this.dataset.v;
    if (feedbackState.issues.includes(v)) {
      feedbackState.issues = feedbackState.issues.filter(x => x !== v);
      this.classList.remove('active');
    } else {
      feedbackState.issues.push(v);
      this.classList.add('active');
    }
  });
});

async function submitFeedback() {
  const btn = document.getElementById('f-submit');
  btn.disabled = true;
  try {
    await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        quality: feedbackState.quality,
        issues: feedbackState.issues,
        comment: document.getElementById('f-text').value.trim()
      })
    });
    document.getElementById('f-thanks').style.display = 'block';
    document.getElementById('f-submit').style.display = 'none';
  } catch(e) {
    btn.disabled = false;
  }
}

// Show feedback when result appears
var resultObserver = new MutationObserver(function() {
  if (document.getElementById('result').style.display !== 'none') {
    document.getElementById('feedback').style.display = 'block';
  }
});
resultObserver.observe(document.getElementById('result'), { attributes: true, attributeFilter: ['style'] });
</script>
</body>
</html>"""


def _check_auth():
    if not AUTH_TOKEN:
        return True
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] == AUTH_TOKEN
    return request.args.get("token", "") == AUTH_TOKEN


def _check_rate(session_id, max_req=5, window=600):
    """Return (allowed: bool, retry_after: int seconds).

    Layer 1: Server-issued session ID required. Unknown sessions rejected.
    Layer 2: When global rate exceeds threshold, strict per-IP limiting engages.
    """
    now = time.time()
    ip = request.remote_addr

    with _task_lock:
        # Global monitoring
        _global_times.append((now, ip))
        global_rate = [t for t in _global_times if now - t[0] < 60]
        _global_times[:] = global_rate  # trim in-place
        strict_mode = len(global_rate) > 30  # >30 req/min → strict

        # Layer 1: validate session
        if session_id not in _valid_sessions:
            # Legacy: accept AUTH_TOKEN bearer as bypass
            auth = request.headers.get("Authorization", "")
            if not (AUTH_TOKEN and auth.startswith("Bearer ") and auth[7:] == AUTH_TOKEN):
                return False, 3600

        # Layer 2: choose rate-limit key
        if strict_mode:
            key = f"ip:{ip}"
            limit = 3  # strict
        else:
            key = f"sid:{session_id}"
            limit = max_req

        times = [t for t in _rate_buckets.get(key, []) if now - t < window]
        if len(times) >= limit:
            retry_after = int(times[0] + window - now) + 1
            return False, retry_after
        times.append(now)
        _rate_buckets[key] = times

    return True, 0


@app.route("/")
def index():
    session_id = str(uuid.uuid4())
    with _task_lock:
        _valid_sessions[session_id] = {"ip": request.remote_addr, "created_at": time.time()}
    return render_template_string(FORM_HTML, provinces=PROVINCES, session_id=session_id)


@app.route("/api/synthesis", methods=["POST"])
def api_synthesis():
    if not _check_auth():
        return jsonify({"error": "unauthorized"}), 403

    session_id = request.headers.get("X-Session-ID", "")
    allowed, retry_after = _check_rate(session_id)
    if not allowed:
        resp = jsonify({"error": f"请求太频繁，请 {retry_after} 秒后重试"})
        resp.headers["Retry-After"] = str(retry_after)
        return resp, 429

    data = request.get_json()
    if not data:
        return jsonify({"error": "需要 JSON body"}), 400

    birth = data.get("birth", {})
    if not birth.get("date") or not birth.get("time") or not birth.get("lat") or not birth.get("lon"):
        return jsonify({"error": "缺少必填字段: birth.date, birth.time, birth.lat, birth.lon"}), 400

    discount = data.get("discount", "").strip()
    if discount not in DISCOUNT_CODES:
        return jsonify({"error": "折扣码无效，请输入有效的折扣码"}), 403

    time_str = birth["time"]
    if len(time_str.split(":")) != 2:
        return jsonify({"error": "时间格式必须为 HH:MM"}), 400

    import re
    if not re.match(r"^\d{4}/\d{2}/\d{2}$", birth["date"]):
        return jsonify({"error": "日期格式必须为 yyyy/mm/dd"}), 400

    lat = birth.get("lat")
    lon = birth.get("lon")
    if not (-90 <= lat <= 90):
        return jsonify({"error": "纬度必须在 -90 到 90 之间"}), 400
    if not (-180 <= lon <= 180):
        return jsonify({"error": "经度必须在 -180 到 180 之间"}), 400

    question = data.get("question", "").strip()
    house_system = data.get("house_system", "P")
    task_id = str(uuid.uuid4())

    # Log user query for demand analysis
    try:
        query_log = {
            "timestamp": datetime.now().isoformat(),
            "name": data.get("name", "").strip(),
            "question": question,
            "location": birth.get("location_name", ""),
            "task_id": task_id,
        }
        log_path = OUTPUT_DIR / "user_queries.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(query_log, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Never block submission on logging failure

    with _task_lock:
        _task_store[task_id] = {"status": "pending", "result": None, "error": None, "created_at": time.time()}

    t = threading.Thread(target=_run_task, args=(task_id, birth, question, house_system), daemon=True)
    t.start()

    return jsonify({"task_id": task_id, "status": "pending"})


def _run_task(task_id, birth, question, house_system):
    with _task_lock:
        created_at = _task_store.get(task_id, {}).get("created_at", time.time())
        _task_store[task_id]["status"] = "running"
    try:
        synthesis = run_synthesis(birth, question, house_system)
        html = md_to_html(synthesis)
        with _task_lock:
            _task_store[task_id] = {
                "status": "done", "error": None,
                "result": {"synthesis": synthesis, "html": html},
                "created_at": created_at,
            }
    except Exception as e:
        log.exception("Synthesis failed for task %s", task_id)
        with _task_lock:
            _task_store[task_id] = {
                "status": "error", "result": None,
                "error": "解读生成失败，请稍后重试",
                "created_at": created_at,
            }


@app.route("/api/task/<task_id>")
def get_task(task_id):
    if not _check_auth():
        return jsonify({"error": "unauthorized"}), 403

    with _task_lock:
        task = _task_store.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    return jsonify(task)


# Cleanup old tasks periodically (keep 1 hour)
@app.route("/api/health")
def health():
    now = time.time()
    with _task_lock:
        for tid in list(_task_store):
            if now - _task_store[tid].get("created_at", 0) > 3600:
                del _task_store[tid]
        for key in list(_rate_buckets):
            _rate_buckets[key] = [t for t in _rate_buckets[key] if now - t < 600]
            if not _rate_buckets[key]:
                del _rate_buckets[key]
        for sid in list(_valid_sessions):
            if now - _valid_sessions[sid]["created_at"] > 3600:
                del _valid_sessions[sid]
    return jsonify({"status": "ok"})


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    """Receive user feedback survey."""
    data = request.get_json(silent=True) or {}
    feedback = {
        "timestamp": datetime.now().isoformat(),
        "quality": data.get("quality", 0),
        "issues": data.get("issues", []),
        "comment": data.get("comment", ""),
    }
    try:
        log_path = OUTPUT_DIR / "user_feedback.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(feedback, ensure_ascii=False) + "\n")
    except Exception:
        log.warning("Failed to write feedback")
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    print("Astrologist API server starting at http://localhost:5001")
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=5001, debug=debug)
