"""Calculate natal chart using Swiss Ephemeris."""
import json
import swisseph as swe
from datetime import datetime
from constants import SIGNS, RULERSHIP, EXALTATION

# Planet IDs
PLANETS = {
    swe.SUN: "sun", swe.MOON: "moon", swe.MERCURY: "mercury",
    swe.VENUS: "venus", swe.MARS: "mars", swe.JUPITER: "jupiter",
    swe.SATURN: "saturn", swe.URANUS: "uranus", swe.NEPTUNE: "neptune",
    swe.PLUTO: "pluto", swe.MEAN_NODE: "north_node",
}

# Detriment: planet is in sign opposite its rulership
DETRIMENT = {}
for sign, ruler in RULERSHIP.items():
    opp_idx = (SIGNS.index(sign) + 6) % 12
    DETRIMENT[SIGNS[opp_idx]] = ruler

# Fall: planet is in sign opposite its exaltation
FALL = {}
for sign, exalted in EXALTATION.items():
    if exalted:
        opp_idx = (SIGNS.index(sign) + 6) % 12
        FALL[SIGNS[opp_idx]] = exalted

# Triplicity rulers (Dorothean, day/night)
TRIPLICITY = {
    "aries": ("sun", "jupiter", "saturn"),
    "leo": ("sun", "jupiter", "saturn"),
    "sagittarius": ("sun", "jupiter", "saturn"),
    "taurus": ("venus", "moon", "mars"),
    "virgo": ("venus", "moon", "mars"),
    "capricorn": ("venus", "moon", "mars"),
    "gemini": ("saturn", "mercury", "jupiter"),
    "libra": ("saturn", "mercury", "jupiter"),
    "aquarius": ("saturn", "mercury", "jupiter"),
    "cancer": ("venus", "mars", "moon"),
    "scorpio": ("venus", "mars", "moon"),
    "pisces": ("venus", "mars", "moon"),
}


def get_triplicity_ruler(sign, sect):
    """Return the triplicity ruler for a sign based on sect (day/night).

    TRIPLICITY tuples are (day_ruler, night_ruler, participating_ruler).
    """
    rulers = TRIPLICITY.get(sign)
    if not rulers:
        return None
    if sect == "diurnal":
        return rulers[0]
    elif sect == "nocturnal":
        return rulers[1]
    return rulers[2]


# Egyptian Term boundaries (Ptolemaic)
# Each sign has 5 term segments: (start_degree, end_degree, ruler)
TERMS = {
    "aries": [(0, 6, "jupiter"), (6, 12, "venus"), (12, 20, "mercury"), (20, 25, "mars"), (25, 30, "saturn")],
    "taurus": [(0, 8, "venus"), (8, 14, "mercury"), (14, 22, "jupiter"), (22, 27, "saturn"), (27, 30, "mars")],
    "gemini": [(0, 6, "mercury"), (6, 12, "jupiter"), (12, 17, "venus"), (17, 24, "mars"), (24, 30, "saturn")],
    "cancer": [(0, 7, "mars"), (7, 13, "venus"), (13, 19, "mercury"), (19, 26, "jupiter"), (26, 30, "saturn")],
    "leo": [(0, 6, "jupiter"), (6, 11, "venus"), (11, 18, "saturn"), (18, 24, "mercury"), (24, 30, "mars")],
    "virgo": [(0, 7, "mercury"), (7, 17, "venus"), (17, 21, "jupiter"), (21, 28, "mars"), (28, 30, "saturn")],
    "libra": [(0, 6, "saturn"), (6, 14, "mercury"), (14, 21, "jupiter"), (21, 28, "venus"), (28, 30, "mars")],
    "scorpio": [(0, 7, "mars"), (7, 11, "venus"), (11, 19, "mercury"), (19, 24, "jupiter"), (24, 30, "saturn")],
    "sagittarius": [(0, 12, "jupiter"), (12, 17, "venus"), (17, 21, "mercury"), (21, 26, "saturn"), (26, 30, "mars")],
    "capricorn": [(0, 7, "mercury"), (7, 14, "jupiter"), (14, 22, "venus"), (22, 26, "saturn"), (26, 30, "mars")],
    "aquarius": [(0, 7, "mercury"), (7, 13, "venus"), (13, 20, "jupiter"), (20, 25, "mars"), (25, 30, "saturn")],
    "pisces": [(0, 12, "venus"), (12, 16, "jupiter"), (16, 19, "mercury"), (19, 28, "mars"), (28, 30, "saturn")],
}

# Chaldean Face/Decan assignments
# Each sign has 3 decans of 10°, rulers follow Chaldean order repeating
_FACE_ORDER = ["mars", "sun", "venus", "mercury", "moon", "saturn", "jupiter"]
FACES = {}
for i, sign in enumerate(SIGNS):
    FACES[sign] = [
        (0, 10, _FACE_ORDER[(i * 3) % 7]),
        (10, 20, _FACE_ORDER[(i * 3 + 1) % 7]),
        (20, 30, _FACE_ORDER[(i * 3 + 2) % 7]),
    ]

# Joy of houses (planetary joys)
JOY = {
    "sun": 9, "moon": 3, "mercury": 1, "venus": 5,
    "mars": 6, "jupiter": 11, "saturn": 12,
}



# China DST periods (1986-1991)
# Clocks were set forward 1 hour during these intervals.
# Source: Chinese government announcements, each year roughly Apr–Sep.
_CHINA_DST = {
    1986: ((5, 4), (9, 14)),
    1987: ((4, 12), (9, 13)),
    1988: ((4, 10), (9, 11)),
    1989: ((4, 16), (9, 17)),
    1990: ((4, 15), (9, 16)),
    1991: ((4, 14), (9, 15)),
}


def is_china_dst(year, month, day):
    """Return True if the date falls within China's DST period."""
    if year not in _CHINA_DST:
        return False
    (sm, sd), (em, ed) = _CHINA_DST[year]
    return (month > sm or (month == sm and day >= sd)) and \
           (month < em or (month == em and day <= ed))


def calc_chart(year, month, day, hour, minute, lat, lon, tz_offset=8.0, house_system=b'W'):
    """Calculate a natal chart.

    house_system: b'W' = Whole Sign, b'P' = Placidus, b'K' = Koch, b'E' = Equal
    """

    # Save birth coordinates before lon gets overwritten by planet calcs
    birth_lat = lat
    birth_lon = lon

    # Save original clock hour before DST adjustment
    clock_hour = hour

    # Apply China DST correction: if DST was in effect, the clock
    # reading is 1 hour ahead of standard time, so we must subtract
    # an extra hour to get UT.
    if is_china_dst(year, month, day):
        hour -= 1

    # Julian day
    jd = swe.julday(year, month, day, hour + minute/60.0 - tz_offset)

    # Calculate house cusps
    cusps, ascmc = swe.houses(jd, lat, lon, house_system)

    asc = ascmc[0]
    mc = ascmc[1]

    asc_sign_num = int(asc / 30)
    asc_sign = SIGNS[asc_sign_num]

    # Build house sign map
    houses = {}
    planet_house = {}  # pname → house number (for non-whole-sign systems)

    if house_system == b'W':
        # Whole Sign: each house spans one entire sign
        for i in range(12):
            sign_idx = (asc_sign_num + i) % 12
            houses[i + 1] = SIGNS[sign_idx]
    else:
        # Quadrant house systems (Placidus, Koch, Equal, etc.)
        # houses[i] = sign of the cusp at the start of house i+1
        # cusps[0] = house 1 cusp, cusps[1] = house 2, ..., cusps[11] = house 12
        for i in range(12):
            cusp_lon = cusps[i]
            cusp_sign_num = int(cusp_lon / 30)
            houses[i + 1] = SIGNS[cusp_sign_num]

        # For house placement, each planet's house is determined by
        # which cusp interval its longitude falls into.
        # We'll compute this in the planet loop below.

    # Get Sun longitude once for combust/sect calculations
    sun_lon = swe.calc_ut(jd, swe.SUN)[0][0]

    # Calculate planets
    planets = {}
    for pid, pname in PLANETS.items():
        result = swe.calc_ut(jd, pid)
        lon = result[0][0]  # longitude
        lat_ecl = result[0][1]  # ecliptic latitude
        dist = result[0][2]  # distance

        sign_num = int(lon / 30)
        sign = SIGNS[sign_num]
        degree = lon % 30
        degree_str = f"{sign} {degree:.1f}°"

        # Determine house
        if house_system == b'W':
            house_num = ((sign_num - asc_sign_num) % 12) + 1
        else:
            # Quadrant: find which cusp interval contains the planet longitude
            # Houses: cusps[0] starts H1, cusps[1] starts H2, ..., cusps[11] starts H12
            # The next cusp after cusps[11] wraps back to cusps[0]
            plon = lon  # planet longitude (already normalized 0-360)
            house_num = 1
            for i in range(12):
                start = cusps[i]
                end = cusps[(i + 1) % 12]
                # Handle wrap-around
                if start < end:
                    if start <= plon < end:
                        house_num = i + 1
                        break
                else:
                    # Cusp interval wraps across 0°
                    if plon >= start or plon < end:
                        house_num = i + 1
                        break
        house = houses[house_num]

        # Check if retrograde
        speed = result[0][3]
        is_retro = speed < 0

        # Essential dignity
        dignity = "peregrine"
        dignity_score = 0

        if RULERSHIP[sign] == pname:
            dignity = "rulership"
            dignity_score = 5
        elif EXALTATION[sign] == pname:
            dignity = "exaltation"
            dignity_score = 4
        elif DETRIMENT.get(sign) == pname:
            dignity = "detriment"
            dignity_score = -3
        elif FALL.get(sign) == pname:
            dignity = "fall"
            dignity_score = -4
        elif pname in TRIPLICITY[sign]:
            dignity = "triplicity"
            dignity_score = 3
        else:
            # Check term (界)
            in_term = False
            for start_d, end_d, ruler in TERMS[sign]:
                if start_d <= degree < end_d:
                    if ruler == pname:
                        dignity = "term"
                        dignity_score = 2
                        in_term = True
                    break
            # Check face (面) if not in own term
            if not in_term:
                for start_d, end_d, ruler in FACES[sign]:
                    if start_d <= degree < end_d:
                        if ruler == pname:
                            dignity = "face"
                            dignity_score = 1
                        break
                # Peregrine: no essential dignity at all
                if dignity == "peregrine":
                    dignity_score = -1

        # Accidental dignity
        acc_score = 0
        acc_details = []

        # Angular/succedent/cadent
        if house_num in [1, 4, 7, 10]:
            acc_score += 4
            acc_details.append("angular")
        elif house_num in [2, 5, 8, 11]:
            acc_score += 2
            acc_details.append("succedent")

        # Conjunct an angle (within 3°)
        for angle_name, angle_val in [("asc", asc), ("mc", mc), ("dsc", (asc + 180) % 360), ("ic", (mc + 180) % 360)]:
            orb = abs(lon - angle_val)
            if orb > 180:
                orb = 360 - orb
            if orb <= 3:
                acc_score += 3
                acc_details.append(f"conjunct_{angle_name}")
                break

        # Retrograde
        if is_retro:
            acc_score -= 2
            acc_details.append("retrograde")

        # Combust / Cazimi (Sun aspects)
        if pname != "sun":
            sun_orb = abs(lon - sun_lon)
            if sun_orb > 180:
                sun_orb = 360 - sun_orb
            # Cazimi: within 17 arc-minutes (0.283°) of Sun
            if sun_orb <= 0.283:
                acc_score += 3
                acc_details.append("cazimi")
            # Combust: within 8.5° of Sun
            elif sun_orb <= 8.5:
                acc_score -= 3
                acc_details.append("combust")

        # Planetary joy
        if JOY.get(pname) == house_num:
            acc_score += 1
            acc_details.append("in_joy")

        planets[pname] = {
            "sign": sign,
            "degree": round(degree, 1),
            "degree_str": degree_str,
            "house": house_num,
            "dignity": dignity,
            "dignity_score": dignity_score,
            "accidental_score": acc_score,
            "accidental_details": acc_details,
            "retrograde": is_retro,
        }

    # Sect determination: Sun above horizon = day chart
    # ASC-DSC axis: above is between DSC (asc+180) and ASC
    dsc = (asc + 180) % 360

    # Check if Sun is above horizon (houses 7-12)
    if asc < dsc:
        sun_above = sun_lon >= dsc or sun_lon <= asc
    else:
        sun_above = dsc <= sun_lon <= asc

    sect = "diurnal" if sun_above else "nocturnal"

    # Calculate Lots
    # Fortune = ASC + Moon - Sun (day) or ASC + Sun - Moon (night)
    moon_lon = swe.calc_ut(jd, swe.MOON)[0][0]
    if sect == "diurnal":
        fortune_lon = (asc + moon_lon - sun_lon) % 360
    else:
        fortune_lon = (asc + sun_lon - moon_lon) % 360

    # Helper: determine house from a longitude
    def get_house(lon_val):
        if house_system == b'W':
            return ((int(lon_val / 30) - asc_sign_num) % 12) + 1
        else:
            for i in range(12):
                start = cusps[i]
                end = cusps[(i + 1) % 12]
                if start < end:
                    if start <= lon_val < end:
                        return i + 1
                else:
                    if lon_val >= start or lon_val < end:
                        return i + 1
            return 1

    fortune_sign = SIGNS[int(fortune_lon / 30)]
    fortune_house = get_house(fortune_lon)
    fortune_degree = fortune_lon % 30

    # Spirit = ASC + Sun - Moon (day) or ASC + Moon - Sun (night)
    if sect == "diurnal":
        spirit_lon = (asc + sun_lon - moon_lon) % 360
    else:
        spirit_lon = (asc + moon_lon - sun_lon) % 360

    spirit_sign = SIGNS[int(spirit_lon / 30)]
    spirit_house = get_house(spirit_lon)
    spirit_degree = spirit_lon % 30

    # ── Reception (接纳) & Mutual Reception (互溶) ──
    receptions = []
    for gname, gdata in planets.items():
        gsign = gdata["sign"]
        gdegree = gdata["degree"]
        received_by = []
        for hname in planets:
            if hname == gname:
                continue
            levels = []
            if RULERSHIP.get(gsign) == hname:
                levels.append("domicile")
            if EXALTATION.get(gsign) == hname:
                levels.append("exaltation")
            tri_ruler = get_triplicity_ruler(gsign, sect)
            if tri_ruler == hname:
                levels.append("triplicity")
            for start_d, end_d, ruler in TERMS.get(gsign, []):
                if start_d <= gdegree < end_d and ruler == hname:
                    levels.append("term")
                    break
            for start_d, end_d, ruler in FACES.get(gsign, []):
                if start_d <= gdegree < end_d and ruler == hname:
                    levels.append("face")
                    break
            if levels:
                received_by.append({"planet": hname, "levels": levels})
        if received_by:
            receptions.append({"guest": gname, "received_by": received_by})

    mutual_receptions = []
    for i in range(len(receptions)):
        a = receptions[i]["guest"]
        for j in range(i + 1, len(receptions)):
            b = receptions[j]["guest"]
            # Mutual reception requires domicile or exaltation in both directions
            a_receives_b = any(
                r["planet"] == a and (set(r["levels"]) & {"domicile", "exaltation"})
                for r in receptions[j]["received_by"]
            )
            b_receives_a = any(
                r["planet"] == b and (set(r["levels"]) & {"domicile", "exaltation"})
                for r in receptions[i]["received_by"]
            )
            if a_receives_b and b_receives_a:
                mutual_receptions.append([a, b])

    # Calculate aspects
    aspects = []
    planet_list = list(planets.keys())
    for i in range(len(planet_list)):
        for j in range(i + 1, len(planet_list)):
            p1 = planet_list[i]
            p2 = planet_list[j]
            lon1 = swe.calc_ut(jd, [k for k, v in PLANETS.items() if v == p1][0])[0][0]
            lon2 = swe.calc_ut(jd, [k for k, v in PLANETS.items() if v == p2][0])[0][0]

            angle = abs(lon1 - lon2)
            if angle > 180:
                angle = 360 - angle

            aspect_type = None
            orb = 0
            for target, name, max_orb in [
                (0, "conjunction", 8), (60, "sextile", 6), (90, "square", 8),
                (120, "trine", 8), (180, "opposition", 8)
            ]:
                diff = abs(angle - target)
                if diff <= max_orb:
                    aspect_type = name
                    orb = round(diff, 1)
                    break

            if aspect_type:
                aspects.append({
                    "planet_a": p1, "planet_b": p2,
                    "type": aspect_type, "orb": orb,
                })

    # Chart ruler
    chart_ruler = RULERSHIP[asc_sign]

    # Build dispositor chain for chart ruler
    dispositor_chain = [chart_ruler]
    current = chart_ruler
    visited = {current}
    while True:
        current_sign = planets[current]["sign"]
        next_planet = RULERSHIP[current_sign]
        if next_planet == current or next_planet in visited:
            break
        dispositor_chain.append(next_planet)
        visited.add(next_planet)
        current = next_planet
        if len(dispositor_chain) > 7:
            break

    return {
        "birth": {
            "date": f"{year}/{month:02d}/{day:02d}",
            "time": f"{clock_hour:02d}:{minute:02d}",
            "location": f"lat={birth_lat}, lon={birth_lon}",
            "tz_offset": tz_offset,
        },
        "birth_jd": jd,
        "birth_lat": birth_lat,
        "birth_lon": birth_lon,
        "house_system": "whole_sign" if house_system == b'W' else "placidus",
        "sect": sect,
        "asc": asc_sign,
        "asc_degree": round(asc % 30, 1),
        "mc": SIGNS[int(mc / 30)],
        "mc_degree": round(mc % 30, 1),
        "chart_ruler": chart_ruler,
        "dispositor_chain": dispositor_chain,
        "planets": planets,
        "houses": houses,
        "aspects": aspects,
        "receptions": receptions,
        "mutual_receptions": mutual_receptions,
        "lots": {
            "fortune": {"sign": fortune_sign, "house": fortune_house, "degree": round(fortune_degree, 1)},
            "spirit": {"sign": spirit_sign, "house": spirit_house, "degree": round(spirit_degree, 1)},
        },
    }


if __name__ == "__main__":
    # 华净/阿派: 1987/12/21 11:55 武汉青山
    # Wuhan coords: ~114.3°E, 30.6°N
    chart = calc_chart(1987, 12, 21, 11, 55, 30.6, 114.3)

    # Score weights
    sect = chart["sect"]
    print(f"Sect: {sect}")
    print(f"ASC: {chart['asc']} {chart['asc_degree']}°")
    print(f"Chart Ruler: {chart['chart_ruler']}")
    print(f"Dispositor: {' → '.join(chart['dispositor_chain'])}")
    print()

    print("Planet weights:")
    for pname in ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn"]:
        p = chart["planets"][pname]
        w = score_planet_weight(chart, pname, sect)
        retro = " ℞" if p["retrograde"] else ""
        print(f"  {pname:10s}: {p['sign']:12s} {p['degree']:5.1f}°  H{p['house']}  "
              f"{p['dignity']:12s}  acc={p['accidental_score']:+d}  "
              f"weight={w:+d}{retro}")

    print(f"\nHouses: {chart['houses']}")
    print(f"Fortune: {chart['lots']['fortune']}")
    print(f"Spirit: {chart['lots']['spirit']}")
    print(f"\nAspects: {len(chart['aspects'])} found")

    # Save
    with open("/Users/lihuidong/Astrologist/model/output/chart_huajing.json", "w", encoding="utf-8") as f:
        json.dump(chart, f, ensure_ascii=False, indent=2)
    print("\nSaved to chart_huajing.json")
