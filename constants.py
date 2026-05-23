"""Shared constants for Astrologist — import from here instead of duplicating."""

SIGNS = ["aries", "taurus", "gemini", "cancer", "leo", "virgo",
         "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces"]

RULERSHIP = {
    "aries": "mars", "taurus": "venus", "gemini": "mercury",
    "cancer": "moon", "leo": "sun", "virgo": "mercury",
    "libra": "venus", "scorpio": "mars", "sagittarius": "jupiter",
    "capricorn": "saturn", "aquarius": "saturn", "pisces": "jupiter",
}

EXALTATION = {
    "aries": "sun", "taurus": "moon", "gemini": None,
    "cancer": "jupiter", "leo": None, "virgo": "mercury",
    "libra": "saturn", "scorpio": None, "sagittarius": None,
    "capricorn": "mars", "aquarius": None, "pisces": "venus",
}

PLANET_CN = {
    "sun": "太阳", "moon": "月亮", "mercury": "水星", "venus": "金星",
    "mars": "火星", "jupiter": "木星", "saturn": "土星",
    "uranus": "天王星", "neptune": "海王星", "pluto": "冥王星",
    "north_node": "北交点", "south_node": "南交点",
}

ASPECT_CN = {
    "conjunction": "合", "sextile": "六分", "square": "四分",
    "trine": "三分", "opposition": "对冲",
}

RECEPTION_CN = {
    "domicile": "入庙", "exaltation": "擢升", "triplicity": "三分",
    "term": "界", "face": "面",
}

SIGN_CN = {
    "aries": "白羊座", "taurus": "金牛座", "gemini": "双子座",
    "cancer": "巨蟹座", "leo": "狮子座", "virgo": "处女座",
    "libra": "天秤座", "scorpio": "天蝎座", "sagittarius": "射手座",
    "capricorn": "摩羯座", "aquarius": "水瓶座", "pisces": "双鱼座",
}

ASPECT_FULL_CN = {
    "conjunction": "合相", "sextile": "六分相", "square": "四分相",
    "trine": "三分相", "opposition": "对冲",
}
