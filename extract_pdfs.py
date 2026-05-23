#!/usr/bin/env python3
"""
Extract text from all PDFs in the knowledge source directory.
Strategy:
  1. extract_text — fast: process all text-based PDFs (skip image-based)
  2. extract_ocr  — slow: OCR only unique books with no video backup
  3. list         — show all PDFs with dedup status
  4. stats        — show extraction progress
"""

import fitz
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE_DIR = Path("/Users/lihuidong/Astrologist/model")
SOURCE_DIR = Path("/Users/lihuidong/Astrologist/2")
OUTPUT_DIR = BASE_DIR / "output" / "knowledge" / "pdfs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SWIFT_OCR = "/tmp/ocr2.swift"


def dedup_key(filename, size):
    stem = Path(filename).stem
    size_mb = round(size / 1048576)
    return f"{stem}_{size_mb}"


def extract_text_pymupdf(pdf_path):
    """Returns (text, is_image_based)."""
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return f"[ERROR: {e}]", False

    pages = len(doc)
    all_text = []
    total_chars = 0
    for i in range(pages):
        try:
            t = doc[i].get_text()
            all_text.append(t)
            total_chars += len(t.strip())
        except Exception:
            all_text.append("")
    doc.close()

    avg = total_chars / max(pages, 1)
    is_image = avg < 50 and pages > 5
    return "\n".join(all_text), is_image


def ocr_page(img_path):
    try:
        r = subprocess.run(["swift", SWIFT_OCR, img_path],
                           capture_output=True, text=True, timeout=60)
        return r.stdout.strip() if r.returncode == 0 else f"[OCR err: {r.stderr}]"
    except subprocess.TimeoutExpired:
        return "[OCR timeout]"
    except Exception as e:
        return f"[OCR ex: {e}]"


def ocr_pdf(pdf_path):
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return f"[ERROR: {e}]"

    texts = []
    for i in range(len(doc)):
        try:
            pix = doc[i].get_pixmap(dpi=200)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                img = tmp.name
                pix.save(img)
            texts.append(ocr_page(img))
            os.unlink(img)
            if (i + 1) % 20 == 0:
                print(f"    OCR {i+1}/{len(doc)}", flush=True)
        except Exception as e:
            texts.append(f"[err p{i}: {e}]")
    doc.close()
    return "\n".join(texts)


def clean_text(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return "\n".join(lines)


def collect_unique():
    """Return list of (path, rel, size, fname) for unique PDFs, sorted by size desc."""
    seen = set()
    result = []
    for root, dirs, files in os.walk(SOURCE_DIR):
        for f in files:
            if not f.lower().endswith('.pdf'):
                continue
            path = os.path.join(root, f)
            size = os.path.getsize(path)
            dk = dedup_key(f, size)
            if dk in seen:
                continue
            seen.add(dk)
            rel = os.path.relpath(path, SOURCE_DIR)
            result.append((path, rel, size, f))
    result.sort(key=lambda x: -x[2])
    return result


# Video-backed course directories — PDFs from these are covered by video lectures
VIDEO_BACKED = [
    "若道占星", "大卫", "魯道夫", "鲁道夫", "梅蘭尼",
    "古典占星入门班", "古典占星（入门", "古占面授",
    "合盘", "卜卦", "流年", "推运", "生辰矫正", "生时校正", "生时效正",
    "杨国正-现代", "杨国正古典",
    "新古典占星",
    "十弟微生",
    "希思莉", "SATA",
    "吳坤", "吴坤",
]


def has_video_backup(rel_path):
    """Check if this PDF's content is covered by video lectures."""
    for kw in VIDEO_BACKED:
        if kw in rel_path:
            return True
    return False


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"

    if mode == "list":
        all_unique = collect_unique()
        text_ok = 0
        img_book = 0
        img_slide = 0
        for path, rel, size, fname in all_unique:
            text, is_img = extract_text_pymupdf(path)
            size_mb = size / 1048576
            if is_img:
                if size_mb > 5:
                    img_book += 1
                    tag = "[IMG-BOOK]"
                else:
                    img_slide += 1
                    tag = "[IMG-SLIDE]"
            else:
                text_ok += 1
                tag = "[TEXT]"
            vb = " [VIDEO]" if has_video_backup(rel) else ""
            print(f"  {tag}{vb} {size_mb:.0f}MB — {rel}")
        print(f"\nText-based: {text_ok}, Image books: {img_book}, Image slides: {img_slide}")

    elif mode == "extract_text":
        all_unique = collect_unique()
        status_file = OUTPUT_DIR / "_status.json"
        status = json.loads(status_file.read_text()) if status_file.exists() else {}

        extracted = 0
        skipped_img = 0
        errors = 0
        total = len(all_unique)

        for i, (path, rel, size, fname) in enumerate(all_unique):
            dk = dedup_key(fname, size)
            out_txt = OUTPUT_DIR / f"{Path(fname).stem}.txt"

            if dk in status and status[dk].get("ok"):
                continue

            size_mb = size / 1048576

            text, is_img = extract_text_pymupdf(path)
            if is_img:
                skipped_img += 1
                status[dk] = {"ok": False, "skipped": "image_based"}
                continue

            text = clean_text(text)
            if len(text.strip()) < 200:
                errors += 1
                status[dk] = {"ok": False, "error": "too_short"}
                continue

            out_txt.write_text(text)
            extracted += 1
            status[dk] = {"ok": True, "chars": len(text), "path": str(out_txt)}

            if (i + 1) % 50 == 0:
                print(f"[{i+1}/{total}] {extracted} text extracted, {skipped_img} img skipped", flush=True)

        status_file.write_text(json.dumps(status, indent=2))
        print(f"\nDone: {extracted} text extracted, {skipped_img} image-based skipped, {errors} errors")

    elif mode == "extract_ocr":
        # Only OCR image-based books WITHOUT video backup
        all_unique = collect_unique()
        status_file = OUTPUT_DIR / "_status.json"
        status = json.loads(status_file.read_text()) if status_file.exists() else {}

        ocr_done = 0
        skipped_video = 0
        skipped_small = 0

        candidates = []
        for path, rel, size, fname in all_unique:
            dk = dedup_key(fname, size)
            # Only OCR entries that were skipped as image-based by extract_text
            if dk in status and status[dk].get("skipped") != "image_based":
                continue
            size_mb = size / 1048576
            text, is_img = extract_text_pymupdf(path)
            if not is_img:
                continue
            vb = has_video_backup(rel)
            candidates.append((path, rel, size_mb, fname, dk, vb))

        for path, rel, size_mb, fname, dk, vb in candidates:
            if vb:
                print(f"  SKIP (video) {size_mb:.0f}MB — {rel}", flush=True)
                skipped_video += 1
                status[dk] = {"ok": False, "skipped": "video_backup"}
                continue
            if size_mb < 5:
                print(f"  SKIP (small) {size_mb:.0f}MB — {rel}", flush=True)
                skipped_small += 1
                status[dk] = {"ok": False, "skipped": "small"}
                continue

            out_txt = OUTPUT_DIR / f"{Path(fname).stem}.txt"
            print(f"  OCR {size_mb:.0f}MB — {rel}", flush=True)
            text = ocr_pdf(path)
            text = clean_text(text)

            if len(text.strip()) > 200:
                out_txt.write_text(text)
                ocr_done += 1
                status[dk] = {"ok": True, "chars": len(text), "ocr": True}
                print(f"    → {len(text):,} chars OK", flush=True)
            else:
                status[dk] = {"ok": False, "error": "ocr_insufficient"}
                print(f"    → too short ({len(text)} chars)", flush=True)

            status_file.write_text(json.dumps(status, indent=2))

        print(f"\nDone: {ocr_done} OCR'd, {skipped_video} video-backed, {skipped_small} small")

    elif mode == "stats":
        status_file = OUTPUT_DIR / "_status.json"
        if not status_file.exists():
            print("No status yet.")
            return
        status = json.loads(status_file.read_text())
        ok = sum(1 for v in status.values() if v.get("ok"))
        skipped = sum(1 for v in status.values() if v.get("skipped"))
        errs = sum(1 for v in status.values() if not v.get("ok") and not v.get("skipped"))
        total_chars = sum(v.get("chars", 0) for v in status.values())
        print(f"PDFs processed: {len(status)}")
        print(f"  Text extracted: {ok}")
        print(f"  Skipped (video/slide): {skipped}")
        print(f"  Errors: {errs}")
        print(f"  Total chars: {total_chars:,}")

        # Also count doc files
        docs_dir = BASE_DIR / "output" / "knowledge" / "docs"
        if docs_dir.exists():
            doc_files = list(docs_dir.glob("*.txt"))
            doc_chars = sum(len(f.read_text()) for f in doc_files)
            print(f"\nDOC/DOCX: {len(doc_files)} files, {doc_chars:,} chars")
            print(f"Combined: {total_chars + doc_chars:,} chars")

    else:
        print(f"Unknown: {mode}")


if __name__ == "__main__":
    main()
