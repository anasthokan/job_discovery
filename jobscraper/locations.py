import re

STATE_ABBREV = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
    "District of Columbia": "DC",
}

STATE_CITIES = {
    "California": [
        "san francisco", "los angeles", "san jose", "oakland", "sacramento",
        "san diego", "palo alto", "mountain view", "sunnyvale", "irvine",
        "santa clara", "berkeley", "pasadena", "foster city", "redwood city",
    ],
    "Washington": ["seattle", "bellevue", "redmond", "tacoma", "kirkland", "spokane"],
    "Oregon": ["portland", "eugene", "beaverton", "hillsboro", "salem"],
    "New York": ["new york", "brooklyn", "manhattan", "queens", "buffalo", "albany"],
    "Texas": ["austin", "dallas", "houston", "san antonio", "plano", "fort worth"],
    "Massachusetts": ["boston", "cambridge", "somerville", "worcester"],
    "Colorado": ["denver", "boulder", "colorado springs", "fort collins"],
    "Illinois": ["chicago", "evanston", "naperville"],
    "Georgia": ["atlanta", "alpharetta", "savannah"],
    "Florida": ["miami", "tampa", "orlando", "jacksonville"],
    "Arizona": ["phoenix", "scottsdale", "tempe", "tucson"],
    "North Carolina": ["raleigh", "durham", "charlotte", "cary"],
    "Virginia": ["arlington", "alexandria", "reston", "richmond", "mclean"],
    "Utah": ["salt lake city", "provo", "lehi", "park city"],
    "District of Columbia": ["washington dc", "washington, dc", "washington, d.c."],
}

MUSE_LOCATION_HINTS = {
    "California": "San Francisco, CA",
    "Washington": "Seattle, WA",
    "Oregon": "Portland, OR",
    "New York": "New York, NY",
    "Texas": "Austin, TX",
    "Massachusetts": "Boston, MA",
    "Colorado": "Denver, CO",
    "Illinois": "Chicago, IL",
    "Georgia": "Atlanta, GA",
    "Florida": "Miami, FL",
    "Arizona": "Phoenix, AZ",
    "North Carolina": "Raleigh, NC",
    "Virginia": "Arlington, VA",
    "Utah": "Salt Lake City, UT",
    "District of Columbia": "Washington, DC",
}

CITY_MUSE_HINTS = {
    "seattle": "Seattle, WA",
    "redmond": "Seattle, WA",
    "bellevue": "Seattle, WA",
    "portland": "Portland, OR",
    "san francisco": "San Francisco, CA",
    "sf": "San Francisco, CA",
    "bay area": "San Francisco, CA",
    "palo alto": "San Francisco, CA",
    "mountain view": "San Francisco, CA",
    "san jose": "San Jose, CA",
    "los angeles": "Los Angeles, CA",
    "la": "Los Angeles, CA",
    "san diego": "San Diego, CA",
    "new york": "New York, NY",
    "nyc": "New York, NY",
    "brooklyn": "New York, NY",
    "austin": "Austin, TX",
    "dallas": "Dallas, TX",
    "houston": "Houston, TX",
    "boston": "Boston, MA",
    "cambridge": "Boston, MA",
    "chicago": "Chicago, IL",
    "denver": "Denver, CO",
    "boulder": "Denver, CO",
    "atlanta": "Atlanta, GA",
    "miami": "Miami, FL",
    "phoenix": "Phoenix, AZ",
    "raleigh": "Raleigh, NC",
    "charlotte": "Charlotte, NC",
    "arlington": "Arlington, VA",
    "washington": "Washington, DC",
    "dc": "Washington, DC",
    "salt lake city": "Salt Lake City, UT",
    "remote": "Remote",
}


def muse_location(city: str, state: str) -> str:
    city = (city or "").strip()
    if city:
        if "," in city:
            return city
        return CITY_MUSE_HINTS.get(city.lower(), city)
    return MUSE_LOCATION_HINTS.get(state or "", "")


def matches_city_filter(job_location: str, city: str) -> bool:
    if not city:
        return True
    needle = city.lower().strip()
    haystack = (job_location or "").lower()
    if needle in haystack:
        return True
    hint = CITY_MUSE_HINTS.get(needle, "")
    if hint and hint.split(",")[0].lower() in haystack:
        return True
    return False

DROPDOWN_STATES = sorted(STATE_ABBREV.keys())

ABBREV_TO_STATE = {abbrev: state for state, abbrev in STATE_ABBREV.items()}

US_STATE_VALUES = set(STATE_ABBREV.keys()) | {"Multiple US", "United States", "Remote"}

_FOREIGN_HINTS = (
    "india", "indian", "bengaluru", "bangalore", "hyderabad", "mumbai", "pune",
    "chennai", "gurgaon", "gurugram", "noida", "kolkata", "kochi", "ahmedabad",
    "united kingdom", "uk", "england", "scotland", "wales", "london", "manchester",
    "germany", "berlin", "munich", "france", "paris",
    "canada", "toronto", "vancouver", "montreal",
    "australia", "sydney", "melbourne",
    "netherlands", "amsterdam", "spain", "madrid", "barcelona",
    "portugal", "lisbon", "italy", "rome", "milan",
    "poland", "warsaw", "brazil", "mexico", "singapore",
    "japan", "tokyo", "china", "shanghai", "beijing",
    "philippines", "manila", "pakistan", "uae", "dubai", "ireland", "dublin",
    "sweden", "stockholm", "norway", "denmark", "finland", "switzerland", "zurich",
    "austria", "vienna", "croatia", "hungary", "romania", "ukraine",
    "south africa", "nigeria", "israel", "tel aviv",
    "south korea", "seoul", "taiwan", "vietnam", "indonesia", "malaysia",
    "thailand", "new zealand", "belgium", "czech", "greece",
    "europe", "emea", "latam", "apac", "asia", "africa",
    "argentina", "colombia", "chile", "peru", "tbilisi",
    "kenya", "ghana", "egypt", "turkey", "istanbul",
)
_FOREIGN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(hint) for hint in _FOREIGN_HINTS) + r")\b",
    re.I,
)
_US_TOKEN_RE = re.compile(
    r"\b(united states(?: of america)?|usa|u\.s\.a\.?|u\.s\.|us-only|us only|north america|us)\b",
    re.I,
)


def parse_state(location: str) -> str:
    if not location:
        return "Remote"

    abbrevs = re.findall(r",\s*([A-Z]{2})\b", location)
    found = []
    for abbr in abbrevs:
        state = ABBREV_TO_STATE.get(abbr)
        if state and state not in found:
            found.append(state)
    if len(found) >= 3:
        return "Multiple US"
    if len(found) == 2:
        return f"{found[0]} / {found[1]}"
    if len(found) == 1:
        return found[0]

    loc = location.lower().strip()
    for state, abbrev in STATE_ABBREV.items():
        if state.lower() in loc:
            return state
        for city in STATE_CITIES.get(state, []):
            if city in loc:
                return state
        if loc == abbrev.lower():
            return state

    remote_tokens = ("worldwide", "remote", "anywhere", "global", "distributed")
    if _US_TOKEN_RE.search(loc):
        return "United States"
    if any(token in loc for token in remote_tokens):
        return "Remote"
    return location.strip()[:48] or "Remote"


def _is_known_us_state(job_state: str) -> bool:
    state = (job_state or "").strip()
    if state in US_STATE_VALUES:
        return True
    if " / " in state:
        parts = [part.strip() for part in state.split("/")]
        return bool(parts) and all(part in STATE_ABBREV for part in parts)
    return False


def is_usa_job(job_state: str, job_location: str = "") -> bool:
    """Keep US states, US-wide, and Remote — drop India/EU/other countries."""
    state = (job_state or "").strip()
    hay = f"{state} {job_location or ''}"
    foreign = bool(_FOREIGN_RE.search(hay))
    us_token = bool(_US_TOKEN_RE.search(hay))
    known = _is_known_us_state(state)

    if foreign:
        if state == "Remote":
            return us_token
        if known and state not in {"Remote", "United States"}:
            return True
        return us_token

    return known or us_token


def matches_state_filter(job_state: str, job_location: str, selected: str) -> bool:
    if not is_usa_job(job_state, job_location):
        return False
    if not selected or selected == "All":
        return True
    haystack = f"{job_state} {job_location}".lower()
    if selected == "Remote":
        return job_state in {"Remote", "United States"} or "remote" in haystack
    if job_state == selected or selected in (job_state or ""):
        return True
    abbrev = STATE_ABBREV.get(selected, "")
    if selected.lower() in haystack:
        return True
    if abbrev and f", {abbrev.lower()}" in haystack:
        return True
    for city in STATE_CITIES.get(selected, []):
        if city in haystack:
            return True
    return False
