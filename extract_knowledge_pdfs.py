#!/usr/bin/env python3
"""
Knowledge extraction from PDF and DOC text files.
Smart sampling: large books get strategic chunks, small files grouped by topic.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path("/Users/lihuidong/Astrologist/model")
OUTPUT_DIR = BASE_DIR / "output"
PDF_DIR = OUTPUT_DIR / "knowledge" / "pdfs"
DOC_DIR = OUTPUT_DIR / "knowledge" / "docs"
DB_PATH = BASE_DIR / "pipeline.db"

sys.path.insert(0, str(BASE_DIR))
from pipeline import (
    call_llm, parse_llm_json, validate_rule, EXTRACTION_PROMPT,
)


def get_topic_from_docname(filename):
    kw_map = {
        "行星": "行星专论", "星座": "星座专论", "宫位": "宫位专论",
        "相位": "相位专论", "古典": "古典占星", "现代": "现代占星学",
        "入门": "占星入门", "初级": "占星入门", "进阶": "占星进阶", "高阶": "占星高阶",
        "推运": "流年推运", "流年": "流年推运",
        "合盘": "人际合盘", "组合盘": "人际合盘",
        "卜卦": "卜卦占星", "校正": "生辰矫正",
        "世俗": "世俗占星", "金融": "金融占星",
        "灵魂": "演化占星", "冥王": "演化占星", "凯龙": "演化占星",
        "变异三王": "演化占星", "演化": "演化占星",
        "荣格": "占星与心理学", "心理": "占星与心理学",
        "生命四元素": "占星与心理学",
        "家族": "家族占星", "职业": "职业占星", "财经": "财经占星",
        "警句": "古典占星", "四书": "古典占星", "托勒密": "古典占星",
        "波纳提": "古典占星", "卡丹": "古典占星", "赫密斯": "古典占星",
        "莫林": "古典占星", "卡門": "古典占星", "Carmen": "古典占星",
        "Bonatti": "古典占星", "法达": "古典占星", "基督占星": "古典占星",
        "南北交": "月亮交点",
        "12宫飞星": "宫位专论", "十二宫": "宫位专论",
        "甘氏": "古典占星", "历史": "占星历史",
        "占星其实很简单": "占星入门", "当代占星研究": "现代占星学",
        "内在的天空": "占星入门",
    }
    for kw, topic in kw_map.items():
        if kw in filename:
            return topic
    return "占星综合"


def smart_extract(filepath, topic):
    """Extract knowledge from a text file with smart sampling for large files."""
    text = Path(filepath).read_text()
    text_len = len(text)

    # For large files (>50KB), sample strategic sections instead of chunking everything
    if text_len > 50000:
        chunk_size = 10000
        chunks = []

        # Beginning: first chunk_size chars
        chunks.append(text[:chunk_size])

        # Middle: find section boundaries
        mid_start = text_len // 2 - chunk_size // 2
        chunks.append(text[mid_start:mid_start + chunk_size])

        # Near end: last chunk_size chars
        chunks.append(text[-chunk_size:])

        # Additional strategic samples: search for key section markers
        section_markers = [
            "行星", "星座", "宫位", "相位", "入庙", "擢升", "失势", "陷落",
            "守护", "主宰", "尊贵", "解读", "原则", "法则", "规则",
            "第1宫", "第2宫", "第3宫", "第4宫", "第5宫", "第6宫",
            "第7宫", "第8宫", "第9宫", "第10宫", "第11宫", "第12宫",
            "火星", "金星", "水星", "木星", "土星", "太阳", "月亮",
            "上升", "天顶",
        ]
        # Track chunk start positions to avoid duplicates
        chunk_positions = {0, max(0, mid_start), max(0, text_len - chunk_size)}
        for marker in section_markers:
            pos = text.find(marker, text_len // 5)
            if pos > 0:
                # Check if this position is far enough from existing chunks
                too_close = any(abs(pos - cp) < chunk_size for cp in chunk_positions)
                if not too_close:
                    start = max(0, pos - 500)
                    chunk_positions.add(start)
                    chunks.append(text[start:start + chunk_size])
            if len(chunks) >= 8:
                break

        if len(chunks) <= 1:
            chunks = [text[:chunk_size], text[-chunk_size:]]

        all_rules = []
        for i, chunk in enumerate(chunks):
            if len(chunk) < 200:
                continue
            prompt = EXTRACTION_PROMPT.format(topic=topic, transcript=chunk)
            response = call_llm(prompt, disable_thinking=True)
            if response:
                try:
                    rules = parse_llm_json(response)
                    valid = [r for r in rules if validate_rule(r)]
                    all_rules.extend(valid)
                except Exception:
                    pass
        return all_rules

    # Medium file (15-50KB): process whole, possibly with minimal chunking
    elif text_len > 15000:
        chunk_size = 12000
        chunks = []
        start = 0
        while start < text_len:
            end = min(start + chunk_size, text_len)
            chunks.append(text[start:end])
            if end >= text_len:
                break
            start = end - 500
        # Cap at 5 chunks
        chunks = chunks[:5]

        all_rules = []
        for chunk in chunks:
            if len(chunk) < 200:
                continue
            prompt = EXTRACTION_PROMPT.format(topic=topic, transcript=chunk)
            response = call_llm(prompt, disable_thinking=True)
            if response:
                try:
                    rules = parse_llm_json(response)
                    valid = [r for r in rules if validate_rule(r)]
                    all_rules.extend(valid)
                except Exception:
                    pass
        return all_rules

    # Small file: single call
    else:
        prompt = EXTRACTION_PROMPT.format(topic=topic, transcript=text)
        response = call_llm(prompt, disable_thinking=True)
        if response:
            try:
                rules = parse_llm_json(response)
                return [r for r in rules if validate_rule(r)]
            except Exception:
                pass
    return []


def collect_text_files():
    files = []
    for d in [PDF_DIR, DOC_DIR]:
        if d.exists():
            for f in d.glob("*.txt"):
                size = f.stat().st_size
                files.append((f.name, str(f), size))
    return files


def main():
    files = collect_text_files()
    print(f"Total text files: {len(files)}")

    # Track processed files to allow resuming
    track_file = OUTPUT_DIR / "extraction_progress.json"
    processed = set()
    if track_file.exists():
        processed = set(json.loads(track_file.read_text()).get("processed", []))

    # Group by topic
    groups = {}
    for fname, fpath, size in files:
        if fname in processed:
            continue
        topic = get_topic_from_docname(fname)
        if topic not in groups:
            groups[topic] = []
        groups[topic].append((fname, fpath, size))

    print(f"Topics: {len(groups)}")
    for t, items in sorted(groups.items(), key=lambda x: -len(x[1])):
        total_kb = sum(s for _, _, s in items) / 1024
        print(f"  {t}: {len(items)} files, {total_kb:.0f}KB")

    db = sqlite3.connect(str(DB_PATH))
    all_rules = []
    total_calls = 0

    for gi, (topic, items) in enumerate(groups.items()):
        print(f"\n[{gi+1}/{len(groups)}] {topic} ({len(items)} files)")

        # Process large files (>15KB) individually with smart sampling
        # Process small files in combined groups
        large = [(n, p, s) for n, p, s in items if s > 15000]
        small = [(n, p, s) for n, p, s in items if s <= 15000]

        for fname, fpath, size in large:
            size_kb = size / 1024
            print(f"  {fname[:60]}... ({size_kb:.0f}KB)", end=" ", flush=True)
            rules = smart_extract(fpath, topic)
            total_calls += 1  # rough estimate (smart_extract makes multiple calls internally)
            if rules:
                print(f"→ {len(rules)} rules")
                all_rules.extend(rules)
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
                         f"pdf:{topic}"))
                db.commit()
            else:
                print("→ 0 rules")
            processed.add(fname)
            track_file.write_text(json.dumps({"processed": list(processed)}))

        # Group small files
        if small:
            combined = ""
            for fname, fpath, size in small:
                text = Path(fpath).read_text()
                sample_len = 8000 if len(text) > 10000 else len(text)
                combined += f"\n--- {fname[:40]} ---\n{text[:sample_len]}\n"
                if len(combined) > 50000:
                    break

            if len(combined) >= 200:
                prompt = EXTRACTION_PROMPT.format(topic=topic, transcript=combined)
                response = call_llm(prompt, disable_thinking=True)
                total_calls += 1
                if response:
                    try:
                        rules = parse_llm_json(response)
                        valid = [r for r in rules if validate_rule(r)]
                        print(f"  Group ({len(small)} files): {len(valid)} rules")
                        all_rules.extend(valid)
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
                                 f"pdf:{topic}"))
                        db.commit()
                    except Exception as e:
                        print(f"  Group parse error: {e}")
            for fname, fpath, size in small:
                processed.add(fname)
            track_file.write_text(json.dumps({"processed": list(processed)}))

    # Save all rules
    all_path = OUTPUT_DIR / "all_extracted_rules_pdf.json"
    with open(all_path, "w", encoding="utf-8") as f:
        json.dump(all_rules, f, ensure_ascii=False, indent=2)
    print(f"\nTotal PDF/DOC rules: {len(all_rules)} → {all_path}")
    print(f"Estimated LLM calls: ~{total_calls}")

    # Show total DB count
    total = db.execute("SELECT COUNT(*) FROM knowledge_rules").fetchone()[0]
    print(f"Total rules in DB: {total}")
    db.close()


if __name__ == "__main__":
    main()
