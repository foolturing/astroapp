"""Transit, Profection, and Firdaria calculations using Swiss Ephemeris."""
import swisseph as swe
from datetime import datetime, timedelta
from constants import SIGNS, RULERSHIP

# Planet IDs for transit calculation
TRANSIT_PLANETS = {
    swe.SUN: "sun", swe.MOON: "moon", swe.MERCURY: "mercury",
    swe.VENUS: "venus", swe.MARS: "mars", swe.JUPITER: "jupiter",
    swe.SATURN: "saturn", swe.URANUS: "uranus", swe.NEPTUNE: "neptune",
    swe.PLUTO: "pluto",
}

# Firdaria periods (in years)
# Night chart order
FIRDARIA_NIGHT = [
    ("moon", 9), ("saturn", 11), ("jupiter", 12), ("mars", 7),
    ("sun", 10), ("venus", 8), ("mercury", 13),
    ("north_node", 3), ("south_node", 2),
]
# Day chart order
FIRDARIA_DAY = [
    ("sun", 10), ("venus", 8), ("mercury", 13), ("moon", 9),
    ("saturn", 11), ("jupiter", 12), ("mars", 7),
    ("north_node", 3), ("south_node", 2),
]


def _jd_to_ymd(jd):
    """Convert Julian day to (year, month, day)."""
    year, month, day, _ = swe.revjul(jd)
    return int(year), int(month), int(day)


def calc_firdaria(birth_jd, sect, target_date=None):
    """Calculate Firdaria periods.

    Returns the current major period (大运) and sub-period (小运) for the target date.
    """
    if target_date is None:
        dt = datetime.now(); target_jd = swe.julday(dt.year, dt.month, dt.day, dt.hour + dt.minute/60.0)
    elif isinstance(target_date, datetime):
        target_jd = swe.julday(target_date.year, target_date.month, target_date.day,
                               target_date.hour + target_date.minute / 60.0)
    else:
        target_jd = target_date

    order = FIRDARIA_DAY if sect == "diurnal" else FIRDARIA_NIGHT
    total_years = sum(dur for _, dur in order)

    # Age at target date
    age_days = target_jd - birth_jd
    age_years = age_days / 365.25

    # Find current major period
    cum_years = 0.0
    major_planet = None
    major_duration = 0
    major_start_age = 0

    for planet, duration in order:
        if cum_years + duration > age_years:
            major_planet = planet
            major_duration = duration
            major_start_age = cum_years
            break
        cum_years += duration
    else:
        # Cycle repeats
        cycle = int(age_years / total_years)
        age_in_cycle = age_years - cycle * total_years
        cum_years = 0.0
        for planet, duration in order:
            if cum_years + duration > age_in_cycle:
                major_planet = planet
                major_duration = duration
                major_start_age = cycle * total_years + cum_years
                break
            cum_years += duration

    # Find sub-period (fractional division within major period)
    major_elapsed = age_years - major_start_age
    major_fraction = major_elapsed / major_duration

    # Sub-period planets follow the same order, starting from the major planet
    start_idx = next(i for i, (p, _) in enumerate(order) if p == major_planet)
    sub_periods = []
    for i in range(len(order)):
        p, dur = order[(start_idx + i) % len(order)]
        sub_duration = dur / 75.0 * major_duration  # proportional
        sub_periods.append((p, sub_duration))

    cum_sub = 0.0
    sub_planet = None
    sub_duration = 0
    sub_start_age = major_start_age
    for planet, duration in sub_periods:
        if cum_sub + duration > major_elapsed:
            sub_planet = planet
            sub_duration = duration
            sub_start_age = major_start_age + cum_sub
            break
        cum_sub += duration

    # Calculate dates
    major_start_jd = birth_jd + major_start_age * 365.25
    major_end_jd = major_start_jd + major_duration * 365.25
    sub_start_jd = birth_jd + sub_start_age * 365.25
    sub_end_jd = sub_start_jd + sub_duration * 365.25

    return {
        "age_years": round(age_years, 2),
        "major_planet": major_planet,
        "major_duration_years": major_duration,
        "major_start": _jd_to_ymd(major_start_jd),
        "major_end": _jd_to_ymd(major_end_jd),
        "sub_planet": sub_planet,
        "sub_duration_years": round(sub_duration, 2),
        "sub_start": _jd_to_ymd(sub_start_jd),
        "sub_end": _jd_to_ymd(sub_end_jd),
        "firdaria_order": [p for p, _ in order],
    }


def calc_profections(birth_jd, asc_sign, target_date=None):
    """Calculate Annual Profections.

    Returns the current profection year, activated house, and Time Lord.
    """
    if target_date is None:
        target = datetime.now()
    elif isinstance(target_date, datetime):
        target = target_date
    else:
        y, m, d = _jd_to_ymd(target_date)
        target = datetime(y, m, d)

    # Birth date
    by, bm, bd = _jd_to_ymd(birth_jd)

    # Age: profection year starts at birthday
    age = target.year - by
    if (target.month, target.day) < (bm, bd):
        age -= 1

    # Profection: 1st year = 1st house, 2nd year = 2nd house, etc.
    profection_house = ((age) % 12) + 1

    # Time Lord = ruler of the sign on that house cusp (Whole Sign)
    asc_sign_idx = SIGNS.index(asc_sign)
    activated_sign_idx = (asc_sign_idx + profection_house - 1) % 12
    activated_sign = SIGNS[activated_sign_idx]
    time_lord = RULERSHIP[activated_sign]

    # Birthday start for current profection year
    prof_start = datetime(target.year if (target.month, target.day) >= (bm, bd) else target.year - 1, bm, bd)
    prof_end = datetime(prof_start.year + 1, bm, bd)

    return {
        "age": age,
        "profection_house": profection_house,
        "activated_sign": activated_sign,
        "time_lord": time_lord,
        "profection_start": f"{prof_start.year}/{prof_start.month:02d}/{prof_start.day:02d}",
        "profection_end": f"{prof_end.year}/{prof_end.month:02d}/{prof_end.day:02d}",
    }


def calc_transits(birth_jd, lat, lon, target_date=None):
    """Calculate current transits vs natal positions.

    Returns transiting planets with aspects to natal positions.
    """
    if target_date is None:
        dt = datetime.now(); target_jd = swe.julday(dt.year, dt.month, dt.day, dt.hour + dt.minute/60.0)
        target_dt = datetime.now()
    elif isinstance(target_date, datetime):
        target_jd = swe.julday(target_date.year, target_date.month, target_date.day,
                               target_date.hour + target_date.minute / 60.0)
        target_dt = target_date
    else:
        target_jd = target_date
        y, m, d = _jd_to_ymd(target_date)
        target_dt = datetime(y, m, d)

    # Calculate transiting planets
    transits = {}
    for pid, pname in TRANSIT_PLANETS.items():
        result = swe.calc_ut(target_jd, pid)
        lon = result[0][0]
        sign_num = int(lon / 30)
        transits[pname] = {
            "sign": SIGNS[sign_num],
            "degree": round(lon % 30, 1),
            "longitude": lon,
        }

    # Calculate natal planet longitudes
    natal_positions = {}
    natal_planets = {
        swe.SUN: "sun", swe.MOON: "moon", swe.MERCURY: "mercury",
        swe.VENUS: "venus", swe.MARS: "mars", swe.JUPITER: "jupiter",
        swe.SATURN: "saturn", swe.URANUS: "uranus", swe.NEPTUNE: "neptune",
        swe.PLUTO: "pluto", swe.MEAN_NODE: "north_node",
    }
    for pid, pname in natal_planets.items():
        result = swe.calc_ut(birth_jd, pid)
        natal_positions[pname] = result[0][0]

    # Find transit-to-natal aspects
    transit_aspects = []
    for tpname, tdata in transits.items():
        if tpname == "moon":
            continue  # Moon moves ~13°/day, transit aspects last hours — skip for natal consultation
        for npname, nlon in natal_positions.items():
            if tpname == npname:
                # Transit of a planet to its own natal position
                angle = abs(tdata["longitude"] - nlon)
                if angle > 180:
                    angle = 360 - angle

                for target, name, max_orb in [
                    (0, "return", 1), (60, "sextile", 4), (90, "square", 4),
                    (120, "trine", 4), (180, "opposition", 4),
                ]:
                    diff = abs(angle - target)
                    if diff <= max_orb:
                        transit_aspects.append({
                            "transit_planet": tpname,
                            "aspect_type": "本命回归" if target == 0 else name,
                            "natal_planet": npname,
                            "orb": round(diff, 1),
                            "transit_sign": tdata["sign"],
                            "transit_degree": tdata["degree"],
                        })
                        break
            else:
                # Transit of one planet to another natal planet
                angle = abs(tdata["longitude"] - nlon)
                if angle > 180:
                    angle = 360 - angle

                for target, name, max_orb in [
                    (0, "conjunction", 4), (60, "sextile", 4), (90, "square", 4),
                    (120, "trine", 4), (180, "opposition", 4),
                ]:
                    diff = abs(angle - target)
                    if diff <= max_orb:
                        transit_aspects.append({
                            "transit_planet": tpname,
                            "aspect_type": name,
                            "natal_planet": npname,
                            "orb": round(diff, 1),
                            "transit_sign": tdata["sign"],
                            "transit_degree": tdata["degree"],
                        })
                        break

    # Sort by orb (tightest first)
    transit_aspects.sort(key=lambda x: x["orb"])

    return {
        "date": f"{target_dt.year}/{target_dt.month:02d}/{target_dt.day:02d}",
        "transits": transits,
        "transit_aspects": transit_aspects,
    }


if __name__ == "__main__":
    # Test with 华净 chart
    birth_jd = swe.julday(1987, 12, 21, 11 + 55/60.0 - 8)
    lat, lon = 30.6, 114.3
    sect = "nocturnal"
    asc_sign = "pisces"

    print("=== 法达大运 (Firdaria) ===")
    firdaria = calc_firdaria(birth_jd, sect)
    print(f"  当前年龄: {firdaria['age_years']} 岁")
    print(f"  大运: {firdaria['major_planet']} ({firdaria['major_duration_years']}年)")
    print(f"    开始: {firdaria['major_start']}")
    print(f"    结束: {firdaria['major_end']}")
    print(f"  小运: {firdaria['sub_planet']} ({firdaria['sub_duration_years']}年)")
    print(f"    开始: {firdaria['sub_start']}")
    print(f"    结束: {firdaria['sub_end']}")

    print("\n=== 小限 (Annual Profections) ===")
    prof = calc_profections(birth_jd, asc_sign)
    print(f"  年龄: {prof['age']} 岁")
    print(f"  小限宫位: {prof['profection_house']}宫 ({prof['activated_sign']})")
    print(f"  时间主星: {prof['time_lord']}")
    print(f"  小限区间: {prof['profection_start']} ~ {prof['profection_end']}")

    print("\n=== 当前行运 (Transits) ===")
    transits = calc_transits(birth_jd, lat, lon)
    print(f"  日期: {transits['date']}")
    for ta in transits["transit_aspects"][:15]:
        print(f"  TR {ta['transit_planet']} {ta['transit_sign']} {ta['transit_degree']}° "
              f"{ta['aspect_type']} NA {ta['natal_planet']} (orb {ta['orb']}°)")
