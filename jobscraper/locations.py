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

_FOREIGN_COUNTRIES = (
    "india", "united kingdom", "uk", "england", "scotland", "wales",
    "germany", "france", "canada", "australia", "netherlands", "spain",
    "portugal", "italy", "poland", "brazil", "mexico", "singapore",
    "japan", "china", "philippines", "pakistan", "uae",
    "united arab emirates", "ireland", "sweden", "norway", "denmark",
    "finland", "switzerland", "austria", "croatia", "hungary", "romania",
    "ukraine", "south africa", "nigeria", "israel", "south korea", "taiwan",
    "vietnam", "indonesia", "malaysia", "thailand", "new zealand", "belgium",
    "czech", "greece", "argentina", "colombia", "chile", "peru", "kenya",
    "ghana", "egypt", "turkey", "hong kong", "saudi arabia", "qatar",
    "bangladesh", "sri lanka", "nepal", "russia",
)
_FOREIGN_CITIES = (
    "bengaluru", "bangalore", "hyderabad", "mumbai", "pune", "chennai",
    "gurgaon", "gurugram", "noida", "kolkata", "kochi", "ahmedabad",
    "london", "manchester", "berlin", "munich", "paris", "toronto",
    "vancouver", "montreal", "ottawa", "calgary", "ontario", "quebec",
    "sydney", "melbourne", "brisbane", "amsterdam", "madrid", "barcelona",
    "lisbon", "rome", "milan", "warsaw", "tokyo", "shanghai", "beijing",
    "manila", "dubai", "dublin", "stockholm", "zurich", "vienna", "seoul",
    "tbilisi", "istanbul", "auckland", "prague", "krakow", "kyiv", "kiev",
    "lagos", "nairobi", "tel aviv",
)
_FOREIGN_COUNTRY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(hint) for hint in _FOREIGN_COUNTRIES) + r")\b",
    re.I,
)
_FOREIGN_CITY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(hint) for hint in _FOREIGN_CITIES) + r")\b",
    re.I,
)
# Worldwide / regional scopes are not United States, even if "remote" is also set.
_NON_US_SCOPE_RE = re.compile(
    r"\b(?:worldwide|world-wide|anywhere|global|international|multi-?country|"
    r"north america|emea|apac|latam|europe|european union|asia|africa|latin america)\b",
    re.I,
)
_USA_RE = re.compile(
    r"\b(?:united states(?: of america)?|u\.s\.a\.?|u\.s\.|usa|us-only|us only|us)\b",
    re.I,
)
# Descriptions say "join us" constantly. Do not treat that bare word as a country.
_USA_PHRASE_RE = re.compile(
    r"\b(?:united states(?: of america)?|u\.s\.a\.?|u\.s\.|usa|us-only|us only)\b",
    re.I,
)
_US_TOKEN_RE = _USA_RE
_STATE_ABBREV_RE = re.compile(r"(?:,\s*|\b)([A-Z]{2})\b")
# These postal codes are also country codes (India, Canada, Colombia, Germany, Indonesia).
_AMBIGUOUS_ABBREVS = {"IN", "CA", "CO", "DE", "ID"}
_PLAIN_REMOTE_WORDS = {
    "remote", "hybrid", "distributed", "wfh", "work", "from", "home",
    "flexible", "multiple", "locations", "location", "various", "not",
    "specified", "unspecified", "na",
}

# Searches already locked to the United States. A bare "Remote" there is still a US job.
US_SCOPED_PLATFORMS = frozenset({
    "LinkedIn",
    "Dice",
    "Indeed",
    "Glassdoor",
    "ZipRecruiter",
    "Google Jobs",
})


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


def _without_new_mexico(text: str) -> str:
    """'Mexico' is a country hint, but New Mexico is a US state."""
    return re.sub(r"\bnew mexico\b", " ", text or "", flags=re.I)


def _has_state_abbrev(text: str) -> bool:
    return any(abbr in ABBREV_TO_STATE for abbr in _STATE_ABBREV_RE.findall(text or ""))


def _has_us_city(text: str) -> bool:
    lower = (text or "").lower()
    for cities in STATE_CITIES.values():
        if any(re.search(rf"\b{re.escape(city)}\b", lower) for city in cities):
            return True
    return False


def _has_state_name(text: str) -> bool:
    lower = (text or "").lower()
    return any(re.search(rf"\b{re.escape(state.lower())}\b", lower) for state in STATE_ABBREV)


def _us_anchor(text: str) -> bool:
    """USA token, postal abbreviation, or known US city. A bare state name is not enough."""
    return bool(_USA_RE.search(text or "")) or _has_state_abbrev(text) or _has_us_city(text)


def _foreign_cities_are_us_places(text: str) -> bool:
    """Keep 'Paris, TX'. Drop 'Paris', 'London', and 'Bangalore, IN'."""
    matched = False
    for city in _FOREIGN_CITIES:
        for match in re.finditer(rf"\b{re.escape(city)}\b", text or "", re.I):
            matched = True
            tail = (text or "")[match.end(): match.end() + 16]
            abbr = re.match(r"\s*,\s*([A-Za-z]{2})\b", tail)
            if not abbr:
                return False
            code = abbr.group(1).upper()
            if code not in ABBREV_TO_STATE or code in _AMBIGUOUS_ABBREVS:
                return False
    return matched


def _hard_us_signal(text: str) -> bool:
    return _us_anchor(text) or _has_state_name(text)


def _is_known_us_state(job_state: str) -> bool:
    state = (job_state or "").strip()
    if state in US_STATE_VALUES:
        return True
    if " / " in state:
        parts = [part.strip() for part in state.split("/")]
        return bool(parts) and all(part in STATE_ABBREV for part in parts)
    return False


def _is_plain_remote(job_state: str, job_location: str) -> bool:
    raw = f"{job_state or ''} {job_location or ''}".strip().lower()
    if not raw:
        return True
    words = re.sub(r"[^a-z]+", " ", raw).split()
    return bool(words) and all(word in _PLAIN_REMOTE_WORDS for word in words)


def is_usa_job(job_state: str, job_location: str = "", *, allow_bare_remote: bool = True) -> bool:
    """Keep United States listings. Drop other countries and worldwide scopes.

    Bare "Remote" counts only when allow_bare_remote is set. US-scoped searches
    (LinkedIn, Dice, Indeed, and the other JobSpy boards) use that because the
    query itself was already limited to the United States. Global boards must
    name a US state, city, or the United States.
    """
    state = (job_state or "").strip()
    location = job_location or ""
    text = f"{state} {location}".strip()
    cleaned = _without_new_mexico(text)

    if _NON_US_SCOPE_RE.search(cleaned):
        return False
    if _FOREIGN_COUNTRY_RE.search(cleaned):
        return False
    if _FOREIGN_CITY_RE.search(cleaned):
        return _foreign_cities_are_us_places(text)
    if _hard_us_signal(text) or (_is_known_us_state(state) and state not in {"Remote", ""}):
        return True
    if allow_bare_remote and _is_plain_remote(state, location):
        return True
    return False


def normalize_scraped_location(
    job_state: str,
    job_location: str,
    platform: str = "",
    description: str = "",
) -> tuple[str, str] | None:
    """Return state/location for a US listing, or None when it should not be saved."""
    state = (job_state or "").strip()
    location = job_location or ""
    us_scoped = (platform or "") in US_SCOPED_PLATFORMS
    if (
        not us_scoped
        and _is_plain_remote(state, location)
        and not _NON_US_SCOPE_RE.search(_without_new_mexico(f"{state} {location}"))
        and _USA_PHRASE_RE.search(description or "")
    ):
        state = "United States"
        location = "Remote, United States"
    if not is_usa_job(state, location, allow_bare_remote=us_scoped):
        return None
    return state, location


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
