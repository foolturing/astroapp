#!/usr/bin/env python3
"""
Astrologist Knowledge Distillation Pipeline
===========================================
Phases:
  1. Extract audio from videos (ffmpeg)
  2. Transcribe audio → text (faster-whisper)
  3. Extract structured knowledge from transcripts (LLM)
  4. Build knowledge graph
  5. Inference engine

Design: extensible, resumable, modular.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Optional

# ── Config ──────────────────────────────────────────────
BASE_DIR = Path("/Users/lihuidong/Astrologist")
VIDEO_DIR = BASE_DIR / "唐绮阳"
MODEL_DIR = BASE_DIR / "model"
TRANSCRIPT_DIR = MODEL_DIR / "transcripts"
KNOWLEDGE_DIR = MODEL_DIR / "knowledge"
OUTPUT_DIR = MODEL_DIR / "output"

# Ensure dirs exist
for d in [TRANSCRIPT_DIR, KNOWLEDGE_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Database for tracking state ─────────────────────────
DB_PATH = MODEL_DIR / "pipeline.db"


def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.execute("""CREATE TABLE IF NOT EXISTS transcripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT UNIQUE,
        title TEXT,
        category TEXT,        -- 精简版 or 全会员
        lesson_num INTEGER,
        topic TEXT,
        duration_sec REAL,
        status TEXT DEFAULT 'pending',  -- pending, extracted, transcribed, extracted_knowledge
        transcript_path TEXT,
        word_count INTEGER,
        error_msg TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,         -- planet, sign, house, aspect, combination
        entity_a TEXT,         -- e.g. "sun", "aries", "1st_house"
        entity_b TEXT,         -- secondary entity if applicable
        relationship TEXT,     -- e.g. "in_sign", "in_house", "aspect"
        condition TEXT,        -- full condition description
        interpretation TEXT,   -- the astrological interpretation
        weight REAL DEFAULT 0.5,
        source TEXT,           -- which transcript/book
        confidence REAL DEFAULT 0.5,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.commit()
    return db


# ═══════════════════════════════════════════════════════
# PHASE 1: Video → Audio → Transcript
# ═══════════════════════════════════════════════════════

@dataclass
class VideoInfo:
    filename: str
    title: str
    category: str
    lesson_num: Optional[int]
    topic: str
    duration_sec: float


def parse_filename(filename: str) -> VideoInfo:
    """Parse 唐绮阳's naming convention into structured info."""
    name = filename.replace(".mp4", "")

    # Category
    if "精简版" in name:
        category = "精简版"
    elif "全会员" in name:
        category = "全会员"
    else:
        category = "unknown"

    # Lesson number
    lesson_match = re.search(r"第(\d+)堂", name)
    lesson_num = int(lesson_match.group(1)) if lesson_match else None

    # Topic extraction (after ｜ or last meaningful segment)
    topic = ""
    if "｜" in name:
        parts = name.split("｜")
        topic = parts[-1].strip() if len(parts) > 1 else ""
    elif "：" in name:
        parts = name.split("：")
        topic = parts[-1].strip() if len(parts) > 1 else ""

    # Clean title
    title = name[:80] if len(name) > 80 else name

    return VideoInfo(
        filename=filename,
        title=title,
        category=category,
        lesson_num=lesson_num,
        topic=topic,
        duration_sec=0.0,
    )


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffmpeg."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", video_path],
            capture_output=True, text=True, timeout=30
        )
        for line in result.stderr.split("\n"):
            if "Duration" in line:
                # Format: Duration: 00:20:29.66
                time_str = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = time_str.split(":")
                return float(h) * 3600 + float(m) * 60 + float(s)
        return 0.0
    except Exception:
        return 0.0


def extract_audio(video_path: str, audio_path: str) -> bool:
    """Extract 16kHz mono audio from video."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
             audio_path],
            capture_output=True, text=True, timeout=600, check=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"  FFmpeg error: {e.stderr[-200:]}")
        return False


# Global model cache (lazy load, reuse across calls)
_whisper_model = None
_whisper_model_size = None


def transcribe_audio(audio_path: str, model_size: str = "small") -> Optional[str]:
    """Transcribe audio using openai-whisper. Returns text or None."""
    global _whisper_model, _whisper_model_size
    try:
        import whisper

        # Lazy-load and cache model
        if _whisper_model is None or _whisper_model_size != model_size:
            print(f"  Loading whisper model '{model_size}'...")
            _whisper_model = whisper.load_model(model_size)
            _whisper_model_size = model_size

        result = _whisper_model.transcribe(audio_path, language="zh", verbose=False)
        return result["text"].strip()
    except Exception as e:
        print(f"  Transcription error: {e}")
        return None


def scan_videos() -> list[VideoInfo]:
    """Scan all videos in the video directory."""
    videos = []
    for f in sorted(VIDEO_DIR.iterdir()):
        if f.suffix.lower() == ".mp4":
            info = parse_filename(f.name)
            info.duration_sec = get_video_duration(str(f))
            videos.append(info)
    return videos


def process_all_videos(model_size: str = "small", limit: int = 0,
                       resume: bool = True, category: str = None):
    """Main processing loop: extract audio + transcribe all videos.

    Args:
        model_size: whisper model size (tiny, small, medium)
        limit: max videos to process (0 = all)
        resume: skip already-transcribed videos
        category: filter by category ('精简版', '全会员', or None for all)
    """
    db = get_db()
    videos = scan_videos()
    print(f"Found {len(videos)} videos")

    # Register new videos
    for v in videos:
        db.execute("""INSERT OR IGNORE INTO transcripts (filename, title, category, lesson_num, topic, duration_sec)
                      VALUES (?, ?, ?, ?, ?, ?)""",
                   (v.filename, v.title, v.category, v.lesson_num, v.topic, v.duration_sec))
    db.commit()

    # Filter by category
    if category:
        videos = [v for v in videos if v.category == category]
        print(f"Filtered to '{category}': {len(videos)} videos")

    # Get pending jobs
    if resume:
        rows = db.execute(
            "SELECT filename FROM transcripts WHERE status='transcribed'"
        ).fetchall()
        done = {r[0] for r in rows}
        todo = [v for v in videos if v.filename not in done]
    else:
        todo = list(videos)

    if limit:
        todo = todo[:limit]

    print(f"Processing {len(todo)} videos (model={model_size})")
    total_duration = sum(v.duration_sec for v in todo) / 3600
    print(f"Total duration: {total_duration:.1f} hours")

    # Pipeline audio extraction and transcription in parallel
    # Producer: extract audio → queue, Consumer: transcribe from queue
    audio_queue = Queue(maxsize=2)

    def extract_worker():
        """Extract audio from videos, push to queue."""
        for v in todo:
            video_path = str(VIDEO_DIR / v.filename)
            audio_path = str(TRANSCRIPT_DIR / f"{v.filename}.wav")
            txt_path = str(TRANSCRIPT_DIR / f"{v.filename}.txt")

            # Skip if transcript already exists
            if os.path.exists(txt_path) and os.path.getsize(txt_path) > 100:
                audio_queue.put(("skip", v, None, txt_path))
                continue

            # Extract audio
            if not os.path.exists(audio_path):
                t0 = time.time()
                if not extract_audio(video_path, audio_path):
                    audio_queue.put(("error", v, "audio extraction failed", None))
                    continue
            audio_queue.put(("ready", v, audio_path, txt_path))
        audio_queue.put(None)  # sentinel

    extract_thread = Thread(target=extract_worker, daemon=True)
    extract_thread.start()

    processed = 0
    while True:
        item = audio_queue.get()
        if item is None:
            break

        status, v, audio_path_or_msg, txt_path = item

        if status == "skip":
            print(f"\n[{processed+1}/{len(todo)}] {v.title[:60]}...")
            print("  Transcript exists, skipping")
            db.execute("""UPDATE transcripts SET status='transcribed', transcript_path=?
                          WHERE filename=?""", (txt_path, v.filename))
            db.commit()
            processed += 1
            continue

        if status == "error":
            print(f"\n[{processed+1}/{len(todo)}] {v.title[:60]}...")
            print(f"  Error: {audio_path_or_msg}")
            db.execute("""UPDATE transcripts SET status='error', error_msg=?
                          WHERE filename=?""", (audio_path_or_msg, v.filename))
            db.commit()
            processed += 1
            continue

        # Transcribe
        print(f"\n[{processed+1}/{len(todo)}] {v.title[:60]}...")
        print(f"  Transcribing...")
        t0 = time.time()
        text = transcribe_audio(audio_path_or_msg, model_size)
        if text is None:
            db.execute("""UPDATE transcripts SET status='error', error_msg='transcription failed'
                          WHERE filename=?""", (v.filename,))
            db.commit()
            processed += 1
            continue

        # Save transcript
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)

        elapsed = time.time() - t0
        word_count = len(text)
        print(f"  Done in {elapsed:.1f}s, {word_count} chars, {word_count/max(elapsed,1):.0f} chars/s")

        db.execute("""UPDATE transcripts SET status='transcribed', transcript_path=?,
                      word_count=?, updated_at=CURRENT_TIMESTAMP WHERE filename=?""",
                   (txt_path, word_count, v.filename))
        db.commit()

        # Clean up audio
        if os.path.exists(audio_path_or_msg):
            os.remove(audio_path_or_msg)
        processed += 1

    extract_thread.join()
    print("\n=== Phase 1 complete ===")
    db.close()


# ═══════════════════════════════════════════════════════
# PHASE 2: Knowledge Extraction via LLM
# ═══════════════════════════════════════════════════════

EXTRACTION_PROMPT = """你是占星知识提取专家。从以下占星教学材料中提取所有明确的占星规则。

输出 JSON 数组，每条规则格式：
{{"category":"planet_in_sign|planet_in_house|planet_aspect|dignity|house_rulership|chart_pattern|synthesis","entity":"涉及的星体/星座/宫位","condition":"占星条件","interpretation":"具体解读","weight":"high|medium|low","keywords":["关键词"]}}

分类标准（严格互斥，每条规则只属于一个类别）：
- planet_in_sign: 某星体在某星座的表现（如"月亮在金牛座"）
- planet_in_house: 某星体在某宫位的表现（如"火星在第一宫"）
- planet_aspect: 星体之间的相位关系（如"金星与木星有相位"）
- dignity: 必然尊贵/无力（入庙、失势、擢升、陷落）或偶然尊贵（喜乐、角宫）
- house_rulership: 宫位守护关系（某宫的宫主星状态对宫位的影响）
- chart_pattern: 星盘格局（元素平衡、模式分布、大三角、星群等整体特征）
- synthesis: 综合解读原则（如何权衡矛盾、优先规则、全局判断方法）

质量要求（不满足的直接跳过，不输出）：
1. interpretation 必须是完整句子，能脱离上下文独立理解，至少15个字
2. 跳过：闲聊、案例故事细节、互动问答、开场白、结束语
3. 跳过：碎片表述（"稍好""短命""不错"等不成句判断）
4. 跳过：针对特定观众的个人化评论（"提问者""这位同学"等）
5. 矛盾规则都提取，标注不同权重
6. 老师反复强调的标 high，一笔带过的标 low
7. 术语保持原样（上升、合相、入庙等）

主题：{topic}

材料：
{transcript}

只输出 JSON 数组，不要 markdown 标记。"""


def validate_rule(rule: dict) -> bool:
    """Filter low-quality or noisy rules."""
    interp = rule.get("interpretation", "")
    condition = rule.get("condition", "")

    # Minimum length
    if len(interp) < 12:
        return False

    # Noise patterns — context-dependent or non-astrological
    noise = [
        "稍好", "短命", "戏弄", "提问者", "观众", "聊天室",
        "记得按赞", "订阅", "分享", "留言", "打赏",
        "这位同学", "上个礼拜", "下个礼拜", "今天时间",
    ]
    for p in noise:
        if p in interp:
            return False

    return True


def extract_knowledge_from_transcript(transcript_path: str, topic: str = "") -> list[dict]:
    """Use LLM to extract structured knowledge rules from a single transcript."""
    with open(transcript_path, "r", encoding="utf-8") as f:
        text = f.read()

    # For large texts, chunk properly
    if len(text) > 12000:
        chunk_size = 10000
        overlap = 500
    else:
        chunk_size = 12000
        overlap = 0

    all_rules = []

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap

    if len(chunks) > 1:
        print(f"  Chunking into {len(chunks)} parts ({len(text)} chars total)...")

    for i, chunk in enumerate(chunks):
        if len(chunk) < 200:
            continue

        prompt = EXTRACTION_PROMPT.format(topic=topic or "占星教学", transcript=chunk)
        response = call_llm(prompt, disable_thinking=True)

        if response:
            try:
                rules = parse_llm_json(response)
                if rules:
                    valid = [r for r in rules if validate_rule(r)]
                    all_rules.extend(valid)
                    skipped = len(rules) - len(valid)
                    msg = f"    Chunk {i+1}/{len(chunks)}: {len(valid)} rules"
                    if skipped:
                        msg += f" ({skipped} filtered)"
                    print(msg)
            except Exception as e:
                print(f"    Chunk {i+1}/{len(chunks)}: parse error - {e}")

    return all_rules


def call_llm(prompt: str, disable_thinking: bool = True) -> Optional[str]:
    """Call DeepSeek API (Anthropic-compatible endpoint)."""
    import requests

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY environment variable not set")

    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    payload = {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.1,
    }

    if disable_thinking:
        payload["thinking"] = {"type": "disabled"}

    try:
        resp = requests.post(
            f"{base_url}/v1/messages",
            headers=headers,
            json=payload,
            timeout=120,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Find the text block (skip thinking blocks)
            for block in data.get("content", []):
                if block.get("type") == "text":
                    return block["text"]
            return None
        else:
            print(f"    API error: {resp.status_code} - {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"    API call failed: {e}")
        return None


def parse_llm_json(response: str) -> list[dict]:
    """Parse JSON from LLM response, handling various formats and edge cases."""
    text = response.strip()

    # Remove markdown code block markers
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    # Try to find JSON array bounds
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        text = text[start:end]

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try fixing common issues: trailing commas, missing quotes
    try:
        # Remove trailing commas before ] or }
        fixed = re.sub(r",\s*([}\]])", r"\1", text)
        # Fix single quotes
        fixed = re.sub(r"'", '"', fixed)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Try extracting individual objects and parsing one by one
    try:
        objects = []
        # Find all {...} blocks
        for match in re.finditer(r"\{[^{}]*\}", text):
            obj_str = match.group()
            try:
                # Clean the object string
                obj_str = re.sub(r",\s*}", "}", obj_str)
                obj = json.loads(obj_str)
                objects.append(obj)
            except json.JSONDecodeError:
                continue
        if objects:
            return objects
    except Exception:
        pass

    print(f"    JSON parse failed, response preview: {text[:300]}")
    return []


def get_topic_from_filename(filename: str) -> str:
    """Extract the main astrological topic from filename."""
    name = filename.replace(".mp4", "").replace(".txt", "")

    # Book sources
    if "BOOK_" in name:
        if "基督占星第一册" in name:
            return "古典占星基础（行星/宫位/相位）"
        elif "基督占星第二册" in name:
            return "古典卜卦占星（判断法则）"
        elif "基督占星第三册" in name:
            return "古典本命占星（命盘解读）"
        elif "开元占经" in name:
            return "中国古典占星"

    # Known topic patterns
    topic_map = {
        "太阳": "太阳",
        "月亮": "月亮",
        "水星": "水星",
        "金星": "金星",
        "火星": "火星",
        "木星": "木星",
        "土星": "土星",
        "天王星": "天王星",
        "海王星": "海王星",
        "冥王星": "冥王星",
        "凯龙": "凯龙星",
        "北交": "北交点/南交点",
        "上升": "上升星座",
        "星座": "星座特质",
        "宫位": "宫位",
        "相位": "相位",
        "格局": "格局",
        "业力": "业力",
        "转化": "转化/行运",
        "星盘": "星盘综合",
        "敏感点": "敏感点",
        "阳性": "宫位",
        "阴性": "宫位",
        "元素": "元素与模式",
        "模式": "元素与模式",
        "火象": "元素与模式",
        "土象": "元素与模式",
        "风象": "元素与模式",
        "水象": "元素与模式",
        "开创": "元素与模式",
        "固定": "元素与模式",
        "变动": "元素与模式",
    }
    for key, val in topic_map.items():
        if key in name:
            return val
    return "综合"


def process_knowledge_extraction(limit: int = 0, category: str = None):
    """Extract knowledge from all transcribed sources, grouped by topic.

    Strategy:
    - Any transcript >15KB: chunk individually via extract_knowledge_from_transcript
    - Small transcripts (<15KB): sample and combine per topic group
    """
    db = get_db()

    if category:
        rows = db.execute(
            "SELECT filename, transcript_path, topic FROM transcripts WHERE status='transcribed' AND category=?",
            (category,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT filename, transcript_path, topic FROM transcripts WHERE status='transcribed'"
        ).fetchall()

    if not rows:
        print("No transcribed videos found. Run Phase 1 first.")
        return

    # Group by topic
    groups = {}
    for filename, tpath, topic in rows:
        if not tpath or not os.path.exists(tpath):
            continue
        group_key = get_topic_from_filename(filename)
        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append((filename, tpath))

    print(f"Extracting knowledge from {len(rows)} transcripts in {len(groups)} topic groups")
    if limit:
        group_keys = list(groups.keys())[:limit]
        groups = {k: groups[k] for k in group_keys}

    all_rules = []

    for i, (group_key, items) in enumerate(groups.items()):
        print(f"\n[{i+1}/{len(groups)}] Topic: {group_key} ({len(items)} sources)")

        # Auto-chunk any transcript >15KB individually, regardless of group size
        large_sources = []
        regular_sources = []
        for filename, tpath in items:
            size = os.path.getsize(tpath)
            if size > 15000:
                large_sources.append((filename, tpath, size))
            else:
                regular_sources.append((filename, tpath))

        # Process large sources individually with chunking
        for filename, tpath, size in large_sources:
            print(f"  Chunking: {filename[:50]}... ({size/1000:.0f}KB)")
            rules = extract_knowledge_from_transcript(tpath, topic=group_key)
            if rules:
                print(f"  → {len(rules)} rules extracted")
                all_rules.extend(rules)
                safe_key = group_key.replace("/", "_").replace(" ", "_")
                kpath = KNOWLEDGE_DIR / f"group_{safe_key}_rules.json"
                with open(kpath, "w", encoding="utf-8") as f:
                    json.dump(rules, f, ensure_ascii=False, indent=2)
                for rule in rules:
                    db.execute("""INSERT INTO knowledge_rules
                        (category, entity_a, entity_b, relationship, condition, interpretation, weight, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (rule.get("category", "unknown"),
                         rule.get("entity", ""),
                         rule.get("entity_b", ""),
                         rule.get("relationship", ""),
                         rule.get("condition", ""),
                         rule.get("interpretation", ""),
                         {"high": 0.8, "medium": 0.5, "low": 0.3}.get(rule.get("weight", "medium"), 0.5),
                         group_key))
                db.commit()

        # Process remaining small sources as a group
        if regular_sources:
            # Sample each source: up to 8000 chars each, cap group at 50000
            combined_text = ""
            for filename, tpath in regular_sources:
                with open(tpath, "r", encoding="utf-8") as f:
                    text = f.read()
                sample_len = 8000 if len(text) > 10000 else len(text)
                combined_text += f"\n--- {filename[:40]} ---\n{text[:sample_len]}\n"
                if len(combined_text) > 50000:
                    break

            if len(combined_text) < 200:
                print("  Not enough content, skipping")
                continue

            prompt = EXTRACTION_PROMPT.format(topic=group_key, transcript=combined_text)
            response = call_llm(prompt, disable_thinking=True)

            if response:
                try:
                    rules = parse_llm_json(response)
                    if rules:
                        valid = [r for r in rules if validate_rule(r)]
                        skipped = len(rules) - len(valid)
                        msg = f"  {len(valid)} rules"
                        if skipped:
                            msg += f" ({skipped} filtered)"
                        print(msg)
                        all_rules.extend(valid)

                        safe_key = group_key.replace("/", "_").replace(" ", "_")
                        kpath = KNOWLEDGE_DIR / f"group_{safe_key}_rules.json"
                        with open(kpath, "w", encoding="utf-8") as f:
                            json.dump(valid, f, ensure_ascii=False, indent=2)

                        for rule in valid:
                            db.execute("""INSERT INTO knowledge_rules
                                (category, entity_a, entity_b, relationship, condition, interpretation, weight, source)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                (rule.get("category", "unknown"),
                                 rule.get("entity", ""),
                                 rule.get("entity_b", ""),
                                 rule.get("relationship", ""),
                                 rule.get("condition", ""),
                                 rule.get("interpretation", ""),
                                 {"high": 0.8, "medium": 0.5, "low": 0.3}.get(rule.get("weight", "medium"), 0.5),
                                 group_key))
                        db.commit()
                except Exception as e:
                    print(f"  Parse error: {e}")

    # Mark all processed sources
    for group_key, items in groups.items():
        for filename, tpath in items:
            db.execute("""UPDATE transcripts SET status='extracted_knowledge',
                          updated_at=CURRENT_TIMESTAMP WHERE filename=?""",
                       (filename,))
    db.commit()

    # Save all rules
    all_path = OUTPUT_DIR / "all_extracted_rules.json"
    with open(all_path, "w", encoding="utf-8") as f:
        json.dump(all_rules, f, ensure_ascii=False, indent=2)
    print(f"\nTotal: {len(all_rules)} rules saved to {all_path}")

    print(f"\n=== Phase 2 complete ===")
    db.close()


# ═══════════════════════════════════════════════════════
# PHASE 3: Knowledge Graph Construction
# ═══════════════════════════════════════════════════════

def build_knowledge_graph():
    """Build a structured knowledge graph from extracted rules."""
    db = get_db()

    rows = db.execute(
        "SELECT category, condition, interpretation, weight, source FROM knowledge_rules"
    ).fetchall()

    graph = {
        "planets": {},
        "signs": {},
        "houses": {},
        "aspects": {},
        "rules": [],
    }

    for category, condition, interpretation, weight, source in rows:
        graph["rules"].append({
            "category": category,
            "condition": condition,
            "interpretation": interpretation,
            "weight": weight,
            "source": source,
        })

    # Aggregate statistics
    stats = {}
    for r in graph["rules"]:
        cat = r["category"]
        if cat not in stats:
            stats[cat] = 0
        stats[cat] += 1

    graph["stats"] = stats

    # Save graph
    gpath = OUTPUT_DIR / "knowledge_graph.json"
    with open(gpath, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    print(f"Knowledge graph: {len(graph['rules'])} rules saved to {gpath}")
    print(f"Rule categories: {stats}")
    db.close()
    return graph


# ═══════════════════════════════════════════════════════
# Phase 4: Inference Engine
# ═══════════════════════════════════════════════════════

def query_knowledge_graph(chart_data: dict, top_k: int = 15) -> list[dict]:
    """Given birth chart data, find matching interpretations from knowledge graph.

    chart_data format:
    {
        "planets": {"sun": {"sign": "aries", "house": 10}, ...},
        "aspects": [{"a": "sun", "b": "moon", "type": "conjunction", "orb": 3}, ...],
        "angles": {"asc": "scorpio", "mc": "leo"}
    }
    """
    gpath = OUTPUT_DIR / "knowledge_graph.json"
    if not gpath.exists():
        print("Knowledge graph not found. Run Phase 3 first.")
        return []

    with open(gpath, "r", encoding="utf-8") as f:
        graph = json.load(f)

    results = []

    # Build search terms from chart data
    search_terms = []

    # Planet in sign
    for planet, data in chart_data.get("planets", {}).items():
        if "sign" in data:
            search_terms.append(f"{planet}.*{data['sign']}")
        if "house" in data:
            search_terms.append(f"{planet}.*{data['house']}.*宫")

    # Aspects
    for aspect in chart_data.get("aspects", []):
        search_terms.append(f"{aspect['a']}.*{aspect['b']}.*{aspect['type']}")

    # Angle signs
    for angle, sign in chart_data.get("angles", {}).items():
        search_terms.append(f"{angle}.*{sign}")

    # Simple keyword-based matching (will be improved with embeddings later)
    for rule in graph["rules"]:
        score = 0
        condition = rule["condition"].lower()
        for term in search_terms:
            # Check if key terms appear in the condition
            term_parts = term.replace(".*", " ").split()
            matches = sum(1 for p in term_parts if p in condition)
            if matches >= len(term_parts) * 0.6:  # 60% match threshold
                score += matches / len(term_parts)

        if score > 0:
            results.append({
                "interpretation": rule["interpretation"],
                "condition": rule["condition"],
                "score": score * rule["weight"],
                "category": rule["category"],
                "source": rule.get("source", ""),
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def generate_reading(chart_data: dict) -> str:
    """Generate a complete astrological reading from chart data."""
    interpretations = query_knowledge_graph(chart_data, top_k=20)

    if not interpretations:
        return "知识图谱尚未构建，请先运行 Phase 3。"

    # Group by category for structured output
    sections = {}
    for interp in interpretations:
        cat = interp["category"]
        if cat not in sections:
            sections[cat] = []
        sections[cat].append(interp)

    # Generate reading
    lines = ["═══════════════════════════════════",
             "        星 盘 解 读 报 告",
             "═══════════════════════════════════\n"]

    for cat, items in sections.items():
        lines.append(f"【{cat}】")
        for item in items[:3]:  # Top 3 per category
            lines.append(f"  • {item['interpretation']}")
            if item.get("condition"):
                lines.append(f"    ({item['condition']})")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Astrologist Knowledge Distillation")
    parser.add_argument("--phase", type=int, choices=[1, 2, 3, 4], help="Pipeline phase")
    parser.add_argument("--model", default="small", choices=["tiny", "small", "medium"],
                        help="Whisper model size")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of videos")
    parser.add_argument("--category", choices=["精简版", "全会员"], help="Filter by video category")
    parser.add_argument("--no-resume", action="store_true", help="Don't resume from checkpoint")
    parser.add_argument("--scan", action="store_true", help="Scan videos and print summary")

    args = parser.parse_args()

    if args.scan:
        videos = scan_videos()
        print(f"\n=== {len(videos)} Videos ===")
        for cat in ["精简版", "全会员", "unknown"]:
            cat_vids = [v for v in videos if v.category == cat]
            dur = sum(v.duration_sec for v in cat_vids) / 3600
            print(f"\n{cat}: {len(cat_vids)} videos, {dur:.1f} hours")
            for v in cat_vids[:5]:
                print(f"  [{v.lesson_num or '?'}] {v.title[:70]}")
            if len(cat_vids) > 5:
                print(f"  ... and {len(cat_vids)-5} more")
        sys.exit(0)

    if args.phase == 1:
        process_all_videos(model_size=args.model, limit=args.limit,
                          resume=not args.no_resume, category=args.category)
    elif args.phase == 2:
        process_knowledge_extraction(limit=args.limit, category=args.category)
    elif args.phase == 3:
        build_knowledge_graph()
    elif args.phase == 4:
        # Demo with a sample chart
        sample_chart = {
            "planets": {
                "sun": {"sign": "leo", "house": 10},
                "moon": {"sign": "taurus", "house": 7},
                "mercury": {"sign": "virgo", "house": 11},
                "venus": {"sign": "cancer", "house": 9},
                "mars": {"sign": "scorpio", "house": 1},
            },
            "aspects": [
                {"a": "sun", "b": "mars", "type": "square", "orb": 3},
                {"a": "moon", "b": "venus", "type": "trine", "orb": 4},
            ],
            "angles": {"asc": "scorpio", "mc": "leo"},
        }
        reading = generate_reading(sample_chart)
        print(reading)
    else:
        parser.print_help()
