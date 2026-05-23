"""Batch calculate charts for test cases. Supports Whole Sign and Placidus."""
import json, sys, argparse
sys.path.insert(0, "/Users/lihuidong/Astrologist/model")
from calc_chart import calc_chart
from pathlib import Path

CASES = [
    # (name, year, month, day, hour, minute, lat, lon, tz)
    ("drg",   1949, 4,  2,  5, 23, 31.27, 121.45, 8),  # 上海闸北
    ("lihui", 1986, 8,  8,  9, 33, 31.19, 121.44, 8),  # 上海徐汇
    ("lu",    1989, 5, 22, 20,  0, 31.99, 118.77, 8),  # 南京雨花台
    ("jinghao", 1987, 7, 16, 8, 40, 31.23, 121.45, 8), # 上海静安
    ("5bin",  1984, 10, 17, 9, 30, 35.22, 106.65, 8),  # 平凉华亭
    ("yuki",  1984, 8, 26,  4, 15, 28.01, 120.65, 8),  # 温州鹿城
]

HSYS_NAMES = {b'W': 'Whole Sign', b'P': 'Placidus'}

parser = argparse.ArgumentParser()
parser.add_argument("--house", choices=["W", "P"], default="P",
                    help="House system: W=Whole Sign, P=Placidus (default: P)")
args = parser.parse_args()
hsys = b'W' if args.house == "W" else b'P'
hsys_name = HSYS_NAMES[hsys]
suffix = "_ws" if hsys == b'W' else ""

OUT = Path("/Users/lihuidong/Astrologist/model/output")

for name, y, m, d, h, mn, lat, lon, tz in CASES:
    chart = calc_chart(y, m, d, h, mn, lat, lon, tz, house_system=hsys)
    fname = f"chart_{name}{suffix}.json"
    fpath = OUT / fname
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(chart, f, ensure_ascii=False, indent=2)
    asc = chart["asc"]
    sect = "日生" if chart["sect"] == "diurnal" else "夜生"
    ruler = chart["chart_ruler"]
    print(f"✓ {name}: ASC {asc}, {sect}盘, 命主星 {ruler} ({hsys_name}) → {fname}")
