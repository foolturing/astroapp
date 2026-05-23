#!/usr/bin/env python3
"""
Process all video/audio content from 2/ directory.
Phase 1: Scan → Extract audio (videos only) → Transcribe
Phase 2: Extract knowledge from transcripts

Handles: mp4, flv, avi, wmv, mkv, mov, ts, webm (video)
         mp3, m4a, wma, wav, aac, ogg (audio direct)
"""

# MUST be set before importing torch/whisper — prevents thread explosion
# that causes system-wide hangs and swap thrashing on macOS
import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from queue import Queue
from threading import Thread

BASE_DIR = Path("/Users/lihuidong/Astrologist/model")
SOURCE_DIR = Path("/Users/lihuidong/Astrologist/2")
TRANSCRIPT_DIR = BASE_DIR / "transcripts"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = BASE_DIR / "pipeline.db"

TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_EXT = {'.mp4', '.flv', '.avi', '.wmv', '.mkv', '.mov', '.ts', '.webm'}
AUDIO_EXT = {'.mp3', '.m4a', '.wma', '.wav', '.aac', '.ogg'}

# ── Topic from directory path ─────────────────────────────

def get_topic_from_path(rel_path: str) -> str:
    """Derive astrological topic from the directory structure."""
    parts = rel_path.replace("\\", "/").split("/")

    topic_map = {
        "01": "现代占星学",
        "若道": "现代占星学",
        "大卫": "现代占星学",
        "魯道夫": "现代占星学",
        "鲁道夫": "现代占星学",
        "胡因梦": "人际合盘",
        "02": "古典占星",
        "古典": "古典占星",
        "SATA": "古典占星",
        "希思莉": "古典占星",
        "十弟微生": "古典占星",
        "新古典": "古典占星",
        "古占": "古典占星",
        "03": "人际合盘",
        "合盘": "人际合盘",
        "爱情": "人际合盘",
        "组合盘": "人际合盘",
        "04": "卜卦占星",
        "卜卦": "卜卦占星",
        "吴坤": "卜卦占星",
        "05": "流年推运",
        "流年": "流年推运",
        "推运": "流年推运",
        "07": "生辰矫正",
        "校正": "生辰矫正",
        "矫正": "生辰矫正",
        "杨国正": "现代占星学",
    }

    for part in parts:
        for kw, topic in topic_map.items():
            if kw in part:
                return topic
    return "占星综合"


# ── Media scanning ─────────────────────────────────────────

def scan_media() -> list[dict]:
    """Recursively scan SOURCE_DIR for all media files."""
    files = []
    for root, dirs, filenames in os.walk(SOURCE_DIR):
        for f in filenames:
            ext = Path(f).suffix.lower()
            if ext in VIDEO_EXT or ext in AUDIO_EXT:
                path = os.path.join(root, f)
                rel = os.path.relpath(path, SOURCE_DIR)
                size = os.path.getsize(path)
                media_type = "video" if ext in VIDEO_EXT else "audio"
                files.append({
                    "path": path,
                    "rel": rel,
                    "filename": f,
                    "size": size,
                    "type": media_type,
                    "topic": get_topic_from_path(rel),
                })
    return files


# ── Audio extraction ───────────────────────────────────────

def extract_audio(video_path: str, audio_path: str) -> bool:
    """Extract 16kHz mono wav from video."""
    try:
        # text=False: avoid UnicodeDecodeError from ffmpeg stderr with CJK metadata
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
             audio_path],
            capture_output=True, timeout=600, check=True
        )
        return True
    except Exception as e:
        print(f"  FFmpeg error: {str(e)[:200]}")
        return False


# ── Transcription ──────────────────────────────────────────

_whisper_model = None
_whisper_model_size = None


def transcribe_audio(audio_path: str, model_size: str = "tiny", timeout: int = 1800) -> str | None:
    """Transcribe using openai-whisper with timeout. Returns text or None."""
    global _whisper_model, _whisper_model_size
    try:
        import whisper
        import torch
        if _whisper_model is None or _whisper_model_size != model_size:
            print(f"  Loading whisper model '{model_size}'...")
            _whisper_model = whisper.load_model(model_size)
            _whisper_model_size = model_size
            # Set torch threads ONCE — calling again after model load raises RuntimeError
            try:
                torch.set_num_threads(1)
                torch.set_num_interop_threads(1)
            except RuntimeError:
                pass

        # Use signal-based timeout to handle hung transcriptions
        import signal
        def handler(signum, frame):
            raise TimeoutError(f"Transcription timed out after {timeout}s")
        old_handler = signal.signal(signal.SIGALRM, handler)
        signal.alarm(timeout)
        try:
            result = _whisper_model.transcribe(audio_path, language="zh", verbose=False)
            return result["text"].strip()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    except TimeoutError as e:
        print(f"  Timeout: {e}")
        return None
    except Exception as e:
        print(f"  Transcription error: {e}")
        return None


# ── Transcript key ─────────────────────────────────────────

def transcript_key(rel: str) -> str:
    """Convert relative path to safe transcript filename."""
    return rel.replace("/", "_").replace("\\", "_")


# ── Phase 1: Process all media ─────────────────────────────

def process_all_media(model_size: str = "tiny", limit: int = 0, resume: bool = True, cooldown: int = 15):
    """Extract audio + transcribe all media files."""
    db = sqlite3.connect(str(DB_PATH))
    # Ensure table exists
    db.execute("""CREATE TABLE IF NOT EXISTS transcripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT UNIQUE,
        title TEXT,
        category TEXT,
        lesson_num INTEGER,
        topic TEXT,
        duration_sec REAL,
        status TEXT DEFAULT 'pending',
        transcript_path TEXT,
        word_count INTEGER,
        error_msg TEXT,
        source_dir TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    # Add source_dir column if missing
    try:
        db.execute("ALTER TABLE transcripts ADD COLUMN source_dir TEXT")
    except sqlite3.OperationalError:
        pass
    db.commit()

    all_files = scan_media()
    print(f"Found {len(all_files)} media files")
    videos = [f for f in all_files if f["type"] == "video"]
    audios = [f for f in all_files if f["type"] == "audio"]
    print(f"  Videos: {len(videos)}, Audio-only: {len(audios)}")

    # Track progress in JSON for faster resumability
    progress_file = OUTPUT_DIR / "video_progress.json"
    progress = {}
    if resume and progress_file.exists():
        progress = json.loads(progress_file.read_text())
        # Auto-skip files that were in_progress when previous run crashed
        stuck = [k for k, v in progress.items() if v.get("status") == "in_progress"]
        if stuck:
            for k in stuck:
                progress[k] = {"status": "error", "msg": "auto-skipped: previous run crashed", "topic": progress[k].get("topic", "")}
            progress_file.write_text(json.dumps(progress))
            print(f"Auto-skipped {len(stuck)} files that hung in previous run")

    # Filter already-done
    todo = []
    for f in all_files:
        key = transcript_key(f["rel"])
        txt_path = TRANSCRIPT_DIR / f"{key}.txt"
        if resume and key in progress:
            s = progress[key].get("status")
            if s == "transcribed":
                if txt_path.exists() and txt_path.stat().st_size > 100:
                    continue
            elif s == "error":
                continue
        todo.append(f)

    # Sort: audio first (shorter, higher knowledge density), then video
    todo.sort(key=lambda f: (0 if f["type"] == "audio" else 1, f["size"]))

    if limit:
        todo = todo[:limit]

    total_size_gb = sum(f["size"] for f in todo) / 1e9
    print(f"\nProcessing {len(todo)} files ({total_size_gb:.1f} GB)")
    print(f"Model: whisper {model_size}")

    # Register in DB
    for f in todo:
        key = transcript_key(f["rel"])
        db.execute("""INSERT OR IGNORE INTO transcripts (filename, title, topic, source_dir, status)
                      VALUES (?, ?, ?, ?, 'pending')""",
                   (key, f["filename"], f["topic"], f["rel"]))
    db.commit()

    # Producer-consumer: extract audio in background, transcribe in foreground
    audio_queue = Queue(maxsize=2)
    stats = {"extracted": 0, "transcribed": 0, "errors": 0, "skipped": 0}

    def extract_worker():
        for f in todo:
            try:
                key = transcript_key(f["rel"])
                txt_path = TRANSCRIPT_DIR / f"{key}.txt"

                # Skip if already done
                if txt_path.exists() and txt_path.stat().st_size > 100:
                    audio_queue.put(("skip", f, None, str(txt_path)))
                    continue

                if f["type"] == "audio":
                    # Audio file → transcribe directly
                    audio_queue.put(("ready", f, f["path"], str(txt_path)))
                else:
                    # Video → extract audio first
                    audio_path = TRANSCRIPT_DIR / f"{key}.wav"
                    if not audio_path.exists():
                        if not extract_audio(f["path"], str(audio_path)):
                            audio_queue.put(("error", f, "audio extraction failed", str(txt_path)))
                            continue
                    audio_queue.put(("ready", f, str(audio_path), str(txt_path)))
            except Exception as e:
                audio_queue.put(("error", f, f"extract worker: {e}", ""))
        audio_queue.put(None)  # sentinel

    extract_thread = Thread(target=extract_worker, daemon=True)
    extract_thread.start()

    processed = 0
    while True:
        item = audio_queue.get()
        if item is None:
            break

        status, f, audio_path_or_msg, txt_path = item

        if status == "skip":
            processed += 1
            stats["skipped"] += 1
            key = transcript_key(f["rel"])
            progress[key] = {"status": "transcribed", "topic": f["topic"]}
            progress_file.write_text(json.dumps(progress))
            db.execute("""UPDATE transcripts SET status='transcribed', transcript_path=?, source_dir=?
                          WHERE filename=?""", (txt_path, f["rel"], key))
            db.commit()
            continue

        if status == "error":
            processed += 1
            stats["errors"] += 1
            key = transcript_key(f["rel"])
            progress[key] = {"status": "error", "msg": audio_path_or_msg, "topic": f["topic"]}
            progress_file.write_text(json.dumps(progress))
            db.execute("""UPDATE transcripts SET status='error', error_msg=?, source_dir=?
                          WHERE filename=?""", (audio_path_or_msg, f["rel"], key))
            db.commit()
            continue

        # Mark in_progress so crash → auto-skip on restart
        key = transcript_key(f["rel"])
        progress[key] = {"status": "in_progress", "topic": f["topic"]}
        progress_file.write_text(json.dumps(progress))

        # Transcribe
        print(f"\n[{processed+1}/{len(todo)}] {f['rel'][:80]}")
        print(f"  Transcribing...")
        t0 = time.time()
        text = transcribe_audio(audio_path_or_msg, model_size)

        if text is None:
            stats["errors"] += 1
            progress[key] = {"status": "error", "msg": "transcription failed", "topic": f["topic"]}
            db.execute("""UPDATE transcripts SET status='error', error_msg='transcription failed',
                          source_dir=? WHERE filename=?""", (f["rel"], key))
        else:
            Path(txt_path).write_text(text, encoding="utf-8")
            elapsed = time.time() - t0
            word_count = len(text)
            print(f"  Done in {elapsed:.1f}s, {word_count} chars ({word_count/max(elapsed,1):.0f} c/s)")
            stats["transcribed"] += 1
            progress[key] = {"status": "transcribed", "chars": word_count, "topic": f["topic"]}
            db.execute("""UPDATE transcripts SET status='transcribed', transcript_path=?,
                          word_count=?, source_dir=?, updated_at=CURRENT_TIMESTAMP WHERE filename=?""",
                       (txt_path, word_count, f["rel"], key))

        db.commit()
        progress_file.write_text(json.dumps(progress))

        # Clean up extracted audio
        if f["type"] == "video" and os.path.exists(audio_path_or_msg):
            os.remove(audio_path_or_msg)

        processed += 1

        # Cooldown between files to prevent thermal throttling on fanless Macs
        if cooldown > 0 and processed < len(todo):
            time.sleep(cooldown)

    extract_thread.join()
    db.close()

    print(f"\n=== Phase 1 complete ===")
    print(f"  Transcribed: {stats['transcribed']}")
    print(f"  Skipped: {stats['skipped']}")
    print(f"  Errors: {stats['errors']}")
    print(f"Progress saved to {progress_file}")


# ── Phase 2: Knowledge extraction ──────────────────────────

# Reuse from pipeline.py
sys.path.insert(0, str(BASE_DIR))
from pipeline import (
    call_llm, parse_llm_json, validate_rule, EXTRACTION_PROMPT,
    extract_knowledge_from_transcript,
)


def process_knowledge_extraction(limit: int = 0):
    """Extract knowledge from all transcribed sources from the 2/ directory."""
    db = sqlite3.connect(str(DB_PATH))

    rows = db.execute(
        "SELECT filename, transcript_path, topic, source_dir FROM transcripts "
        "WHERE status='transcribed' AND source_dir IS NOT NULL AND source_dir != ''"
    ).fetchall()

    if not rows:
        print("No transcribed files found from 2/ directory. Run Phase 1 first.")
        db.close()
        return

    print(f"Found {len(rows)} transcribed files from 2/")

    # Group by topic
    groups = {}
    for filename, tpath, topic, source_dir in rows:
        if not tpath or not os.path.exists(tpath):
            continue
        if topic not in groups:
            groups[topic] = []
        groups[topic].append((filename, tpath))

    if limit:
        groups = {k: groups[k] for k in list(groups.keys())[:limit]}

    print(f"Topics: {len(groups)}")
    for t, items in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"  {t}: {len(items)} files")

    all_rules = []

    for gi, (topic, items) in enumerate(groups.items()):
        print(f"\n[{gi+1}/{len(groups)}] {topic} ({len(items)} files)")

        # Split large and small
        large = []
        small = []
        for filename, tpath in items:
            size = os.path.getsize(tpath)
            if size > 15000:
                large.append((filename, tpath, size))
            else:
                small.append((filename, tpath))

        # Process large individually
        for filename, tpath, size in large:
            print(f"  {filename[:60]}... ({size/1000:.0f}KB)", end=" ", flush=True)
            rules = extract_knowledge_from_transcript(tpath, topic=topic)
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
                         f"video:{topic}"))
                db.commit()
            else:
                print("→ 0 rules")

        # Combine small files
        if small:
            combined = ""
            for filename, tpath in small:
                try:
                    text = Path(tpath).read_text()
                except Exception:
                    continue
                sample_len = 8000 if len(text) > 10000 else len(text)
                combined += f"\n--- {filename[:40]} ---\n{text[:sample_len]}\n"
                if len(combined) > 50000:
                    break

            if len(combined) >= 200:
                prompt = EXTRACTION_PROMPT.format(topic=topic, transcript=combined)
                response = call_llm(prompt, disable_thinking=True)
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
                                 f"video:{topic}"))
                        db.commit()
                    except Exception as e:
                        print(f"  Parse error: {e}")

        # Mark as processed
        for filename, tpath in items:
            db.execute("""UPDATE transcripts SET status='extracted_knowledge',
                          updated_at=CURRENT_TIMESTAMP WHERE filename=?""", (filename,))
        db.commit()

    # Save combined rules
    all_path = OUTPUT_DIR / "all_extracted_rules_video.json"
    with open(all_path, "w", encoding="utf-8") as f:
        json.dump(all_rules, f, ensure_ascii=False, indent=2)
    print(f"\nTotal video rules: {len(all_rules)} → {all_path}")

    total_db = db.execute("SELECT COUNT(*) FROM knowledge_rules").fetchone()[0]
    print(f"Total rules in DB: {total_db}")
    db.close()


# ── Main ───────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Process video/audio from 2/ directory")
    parser.add_argument("--phase", type=int, choices=[1, 2], required=True)
    parser.add_argument("--model", default="tiny", choices=["tiny", "small", "medium"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--cooldown", type=int, default=15, help="Cooldown seconds between files (0=off)")
    args = parser.parse_args()

    if args.phase == 1:
        process_all_media(model_size=args.model, limit=args.limit,
                         resume=not args.no_resume, cooldown=args.cooldown)
    elif args.phase == 2:
        process_knowledge_extraction(limit=args.limit)
