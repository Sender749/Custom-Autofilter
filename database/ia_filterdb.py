import asyncio
import logging
import re
import base64
import time
from struct import pack
from pyrogram.file_id import FileId
from pymongo import MongoClient, TEXT
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError, OperationFailure
from info import USE_CAPTION_FILTER, FILES_DATABASE_URL, SECOND_FILES_DATABASE_URL, DATABASE_NAME, COLLECTION_NAME, MAX_BTN

logger = logging.getLogger(__name__)

# ── Async client (motor — for filter_words only) ──────────────────────────────
async_client = AsyncIOMotorClient(FILES_DATABASE_URL)
async_db = async_client[DATABASE_NAME]
filter_words_collection = async_db["filter_words"]

# ── Sync clients (pymongo — all file operations) ──────────────────────────────
client = MongoClient(FILES_DATABASE_URL)
db = client[DATABASE_NAME]
collection = db[COLLECTION_NAME]
second_collection = None

try:
    collection.create_index([("file_name", TEXT)])
except OperationFailure as e:
    if 'quota' in str(e).lower():
        if not SECOND_FILES_DATABASE_URL:
            logger.error('FILES_DATABASE_URL is full, add SECOND_FILES_DATABASE_URL')
        else:
            logger.info('FILES_DATABASE_URL is full, using SECOND_FILES_DATABASE_URL')
    else:
        logger.exception(e)

if SECOND_FILES_DATABASE_URL:
    second_client = MongoClient(SECOND_FILES_DATABASE_URL)
    second_db = second_client[DATABASE_NAME]
    second_collection = second_db[COLLECTION_NAME]
    try:
        second_collection.create_index([("file_name", TEXT)])
    except Exception:
        pass


def is_second_db_configured() -> bool:
    return bool(SECOND_FILES_DATABASE_URL and second_collection is not None)


def second_db_count_documents():
    return second_collection.count_documents({})


def db_count_documents():
    return collection.count_documents({})


def get_primary_db_storage():
    stats = db.command("dbStats")
    return stats.get('storageSize', 0)


def get_secondary_db_storage():
    if not is_second_db_configured():
        return 0
    stats = second_db.command("dbStats")
    return stats.get('storageSize', 0)


# ══════════════════════════════════════════════════════════════════════════════
# TITLE CACHE — built once, refreshed every 5 min
# Avoids repeated collection.find().limit(5000) on every Stage-2 call
# ══════════════════════════════════════════════════════════════════════════════

_TITLE_CACHE: list = []          # list of (stripped_lower, original_file_name)
_TITLE_CACHE_TIME: float = 0.0
_TITLE_CACHE_TTL: float = 300.0  # 5 minutes

_JUNK_RE = re.compile(
    r'\b(S\d{1,2}E?\d{0,3}|Season\s*\d+|Episode\s*\d+|\d{3,4}p'
    r'|BluRay|BDRip|Remux|WEBRip|WEB-DL|HDRip|DVDRip|CAM|TS'
    r'|HEVC|x264|x265|AVC|AV1|AAC|DDP5?\.?\d?|FLAC|MP3|AC3|DTS'
    r'|ESub|Subs?|Hindi|Tamil|Telugu|Malayalam|Kannada|Punjabi'
    r'|English|Dual|Multi|HQ|HD|UHD|SDR|HDR|Dolby)\b.*',
    re.IGNORECASE
)


def _base_title(fn: str) -> str:
    """Strip technical tags and punctuation for fuzzy cache indexing."""
    fn = _JUNK_RE.sub('', fn)
    fn = re.sub(r"[._\-\+\[\]()'\"]+", ' ', fn)
    fn = re.sub(r'\s{2,}', ' ', fn)
    return fn.strip().lower()


def _build_title_cache_sync() -> list:
    result = []
    seen = set()
    try:
        for doc in collection.find({}, {"file_name": 1, "_id": 0}).limit(6000):
            fn = doc.get("file_name", "")
            b = _base_title(fn)
            if b and b not in seen:
                seen.add(b)
                result.append((b, fn))
    except Exception as exc:
        logger.debug("title cache primary error: %s", exc)
    if is_second_db_configured():
        try:
            for doc in second_collection.find({}, {"file_name": 1, "_id": 0}).limit(3000):
                fn = doc.get("file_name", "")
                b = _base_title(fn)
                if b and b not in seen:
                    seen.add(b)
                    result.append((b, fn))
        except Exception as exc:
            logger.debug("title cache secondary error: %s", exc)
    return result


async def get_title_cache() -> list:
    global _TITLE_CACHE, _TITLE_CACHE_TIME
    now = time.monotonic()
    if _TITLE_CACHE and (now - _TITLE_CACHE_TIME) < _TITLE_CACHE_TTL:
        return _TITLE_CACHE
    _TITLE_CACHE = await asyncio.to_thread(_build_title_cache_sync)
    _TITLE_CACHE_TIME = now
    return _TITLE_CACHE


# ══════════════════════════════════════════════════════════════════════════════
# QUERY PARSING — extract intent from user query
# ══════════════════════════════════════════════════════════════════════════════

# Known languages for priority ranking
_KNOWN_LANGS = [
    'hindi', 'english', 'tamil', 'telugu', 'malayalam',
    'kannada', 'punjabi', 'bengali', 'gujarati', 'marathi',
    'dual', 'multi',
]

_LANG_RE = re.compile(
    r'\b(' + '|'.join(_KNOWN_LANGS) + r')\b', re.IGNORECASE
)

_YEAR_RE = re.compile(r'\b(19[5-9]\d|20[0-3]\d)\b')

_SE_RE = re.compile(
    r'\b[Ss](?:eason\s*)?(\d{1,2})\s*[Ee](?:pisode\s*|p\s*)?(\d{1,3})\b'
    r'|\b[Ss](\d{1,2})[Ee](\d{1,3})\b'
)
_S_RE  = re.compile(r'\b[Ss](?:eason\s*)?(\d{1,2})\b')
_E_RE  = re.compile(r'\b[Ee](?:pisode\s*|p\s*)(\d{1,3})\b')


class ParsedQuery:
    """Holds the decomposed user query intent."""
    __slots__ = ('raw', 'normalized', 'title_only', 'season', 'episode',
                 'year', 'languages', 'title_words', 'is_series')

    def __init__(self, raw, normalized, title_only, season, episode,
                 year, languages, title_words):
        self.raw = raw
        self.normalized = normalized
        self.title_only = title_only
        self.season = season
        self.episode = episode
        self.year = year
        self.languages = languages
        self.title_words = title_words
        self.is_series = season > 0 or bool(re.search(r'\b[Ss]\d{1,2}[Ee]\d{1,3}\b', normalized))


def parse_query(query: str) -> ParsedQuery:
    """
    Full decomposition of user query:
      - extracts season/episode numbers
      - extracts year
      - extracts language hints
      - produces a clean title-only string (no SE/year/lang)
      - normalises SE notation to SxxExx
    """
    q = query.strip()

    # Extract languages
    langs = [m.group(0).lower() for m in _LANG_RE.finditer(q)]
    q_no_lang = _LANG_RE.sub(' ', q)

    # Extract year
    year_m = _YEAR_RE.search(q_no_lang)
    year = int(year_m.group(0)) if year_m else 0
    q_no_year = _YEAR_RE.sub(' ', q_no_lang)

    # Normalise "season N episode M" / "s01 e05" → "s01e05"
    q_norm = q_no_year

    # Written form "season N episode M"
    q_norm = re.sub(
        r'\bseason\s*(\d{1,2})\s+(?:episode|ep)\s*(\d{1,3})\b',
        lambda m: f"s{int(m.group(1)):02d}e{int(m.group(2)):02d}",
        q_norm, flags=re.IGNORECASE
    )
    # "season N"
    q_norm = re.sub(
        r'\bseason\s*(\d{1,2})\b',
        lambda m: f"s{int(m.group(1)):02d}",
        q_norm, flags=re.IGNORECASE
    )
    # "episode M" / "ep M"
    q_norm = re.sub(
        r'\b(?:episode|ep)\s*(\d{1,3})\b',
        lambda m: f"e{int(m.group(2) if m.lastindex == 2 else m.group(1)):02d}",
        q_norm, flags=re.IGNORECASE
    )
    # Merge spaced "s01 e05" → "s01e05"
    q_norm = re.sub(r'\b(s\d{1,2})\s+(e\d{1,3})\b', r'\1\2', q_norm, flags=re.IGNORECASE)

    # Drop filler
    q_norm = re.sub(r'\b(?:all\s+episodes?|complete\s+series|full\s+season)\b', '', q_norm, flags=re.IGNORECASE)
    q_norm = ' '.join(q_norm.split()).strip()

    # Extract SE from normalised
    season = episode = 0
    se_m = re.search(r's(\d{1,2})e(\d{1,3})', q_norm, re.IGNORECASE)
    if se_m:
        season, episode = int(se_m.group(1)), int(se_m.group(2))
    else:
        s_m = re.search(r's(\d{1,2})', q_norm, re.IGNORECASE)
        e_m = re.search(r'e(\d{1,3})', q_norm, re.IGNORECASE)
        if s_m:
            season = int(s_m.group(1))
        if e_m:
            episode = int(e_m.group(1))

    # Title only — strip SE tokens, year, lang
    title_only = re.sub(r'\bs\d{1,2}(?:e\d{1,3})?\b', '', q_norm, flags=re.IGNORECASE)
    title_only = re.sub(r'\be\d{1,3}\b', '', title_only, flags=re.IGNORECASE)
    title_only = ' '.join(title_only.split()).strip()

    title_words = [w for w in _strip_tech(title_only).split() if w]

    return ParsedQuery(
        raw=query,
        normalized=q_norm,
        title_only=title_only,
        season=season,
        episode=episode,
        year=year,
        languages=langs,
        title_words=title_words,
    )


# ══════════════════════════════════════════════════════════════════════════════
# QUERY NORMALISATION for Stage 1 DB search
# ══════════════════════════════════════════════════════════════════════════════

def normalize_query(query: str) -> str:
    """Public entry point: return normalised query string for Stage 1."""
    return parse_query(query).normalized


def clean_query(query: str, filter_words: set) -> str:
    if not query:
        return query
    # Remove punctuation except alphanumeric and spaces
    query = re.sub(r"[^\w\s]", " ", query)
    if filter_words:
        pattern = r'\b(?:' + '|'.join(map(re.escape, filter_words)) + r')\b'
        query = re.sub(pattern, '', query, flags=re.IGNORECASE)
    return ' '.join(query.split()).strip()


# ══════════════════════════════════════════════════════════════════════════════
# TITLE NORMALISATION helpers (shared by ranking + spell check)
# ══════════════════════════════════════════════════════════════════════════════

_TECH_RE = re.compile(
    r'\b(480p|720p|1080p|2160p|4k|hdr|bluray|bdrip|remux|web[\-\s]?dl|webrip|'
    r'hdrip|dvdrip|cam|ts|hdts|pdvd|scr|x264|x265|hevc|avc|av1|aac|ac3|dts|'
    r'flac|mp3|ddp5?\.?\d?|esub|subs?|subbed|dubbed|'
    r'hindi|english|tamil|telugu|malayalam|kannada|punjabi|bengali|gujarati|marathi|'
    r'dual|multi|hq)\b',
    re.IGNORECASE
)


def _strip_tech(text: str) -> str:
    """Remove technical/quality/language tokens and return lowercase clean text."""
    t = text.lower()
    t = re.sub(r's\d{1,2}e\d{1,3}', ' ', t)
    t = re.sub(r's\d{1,2}\b', ' ', t)
    t = re.sub(r'\b(19|20)\d{2}\b', ' ', t)
    t = _TECH_RE.sub(' ', t)
    # Strip ALL punctuation (handles dom's → doms, S.W.A.T → swat)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r'\s{2,}', ' ', t)
    return t.strip()


def _collapse(text: str) -> str:
    """Remove ALL spaces and punctuation — matches 'B A S S' == 'BASS'."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


# ══════════════════════════════════════════════════════════════════════════════
# RANKING — smart multi-factor sort
# ══════════════════════════════════════════════════════════════════════════════

RESOLUTION_RANK = {
    "2160p": 200, "4k": 200, "uhd": 200,
    "1080p": 150,
    "720p": 100,
    "480p": 50,
    "360p": 20,
}
QUALITY_RANK = {
    "remux": 98,
    "bluray": 96, "bdrip": 94,
    "web-dl": 90, "webdl": 90,
    "webrip": 85,
    "hdrip": 75,
    "hd": 70,
    "dvdrip": 60,
    "ts": 15, "hdts": 14, "cam": 10,
}
# Languages that should be promoted (user explicitly named them)
_LANG_RANK = {
    'hindi': 30, 'english': 25, 'tamil': 30, 'telugu': 30,
    'malayalam': 30, 'kannada': 30, 'punjabi': 25,
    'bengali': 25, 'gujarati': 25, 'marathi': 25,
    'dual': 20, 'multi': 20,
}


def _extract_year(text: str) -> int:
    m = re.findall(r'\b(19[5-9]\d|20[0-3]\d)\b', text)
    return max(map(int, m)) if m else 0


def _extract_season_episode(text: str):
    season = episode = 0
    se_m = re.search(r's(\d{1,2})e(\d{1,3})', text, re.I)
    if se_m:
        return int(se_m.group(1)), int(se_m.group(2))
    s_m = re.search(r's(\d{1,2})', text, re.I)
    e_m = re.search(r'e(\d{1,3})', text, re.I)
    if s_m:
        season = int(s_m.group(1))
    if e_m:
        episode = int(e_m.group(1))
    return season, episode


def _extract_quality(text: str) -> int:
    t = text.lower()
    for q, s in QUALITY_RANK.items():
        if q in t:
            return s
    return 0


def _extract_resolution(text: str) -> int:
    t = text.lower()
    for r, s in RESOLUTION_RANK.items():
        if r in t:
            return s
    return 0


def _has_language_tag(text: str) -> bool:
    return bool(_LANG_RE.search(text))


def _language_score(text: str, preferred_langs: list) -> int:
    """Score based on whether the file matches user's preferred language."""
    t = text.lower()
    score = 0
    for lang in preferred_langs:
        if lang in t:
            score += _LANG_RANK.get(lang, 20)
    return score


def rank_results(query: str, files: list, pq: 'ParsedQuery | None' = None) -> list:
    """
    Multi-factor smart ranking:

    Tier 1 — Exact title match (highest priority, always on top)
    Tier 2 — Series ordering: highest season first, then ascending episode
    Tier 3 — Quality/resolution (for movies), or same season ordering (series)
    Tier 4 — Year (latest first)
    Tier 5 — Language preference (user-specified languages on top)
    Tier 6 — Files with a language tag above files without one
    Tier 7 — Multi-audio bonus
    """
    if pq is None:
        pq = parse_query(query)

    q_collapsed = _collapse(pq.title_only or query)
    q_stripped  = _strip_tech(pq.title_only or query)
    q_words     = [w for w in q_stripped.split() if w]
    q_year      = pq.year
    q_season    = pq.season
    q_episode   = pq.episode
    q_langs     = pq.languages
    is_series   = pq.is_series

    def _word_overlap(file_text: str) -> float:
        """Fraction of query words found in file text (0.0–1.0)."""
        if not q_words:
            return 1.0
        f_stripped = _strip_tech(file_text)
        f_words = set(f_stripped.split())
        matched = sum(1 for w in q_words if w in f_words)
        return matched / len(q_words)

    def score(f: dict):
        text = (f.get("caption") or f.get("file_name", "")).strip()
        f_collapsed = _collapse(text)
        f_stripped  = _strip_tech(text)
        f_words     = set(f_stripped.split())

        # ── Tier 1: exact title match ────────────────────────────────────────
        # Collapsed match handles: dom's==doms, S.W.A.T==SWAT, B A S S==BASS
        exact = 0
        if f_collapsed == q_collapsed:
            exact = 10000
        elif q_collapsed and f_collapsed.startswith(q_collapsed):
            # e.g. query="you" file="you s01e01..." → prefix match
            exact = 8000
        elif q_collapsed in f_collapsed:
            exact = 5000

        # Word overlap as base score (0–3000)
        overlap = _word_overlap(text)
        word_score = int(overlap * 3000)

        # ── Tier 2: year match ───────────────────────────────────────────────
        f_year = _extract_year(text)
        year_bonus = 0
        if q_year and f_year == q_year:
            year_bonus = 500       # exact year match
        elif f_year:
            year_bonus = f_year - 1900  # recency (max ~125 for 2025)

        # ── Tier 3: language priority ────────────────────────────────────────
        lang_bonus = _language_score(text, q_langs) if q_langs else 0
        has_lang_tag = _has_language_tag(text)
        # If user specified a language, unlabelled files go below labelled ones
        # If user did NOT specify, files with no lang tag are neutral (not penalised)
        lang_label_bonus = 0
        if q_langs:
            lang_label_bonus = 10 if has_lang_tag else 0
        else:
            # No language specified by user — no penalty for having/not having lang
            lang_label_bonus = 0

        # ── Tier 4: quality & resolution ────────────────────────────────────
        quality    = _extract_quality(text)
        resolution = _extract_resolution(text)
        multi_audio = 30 if re.search(r'\b(dual|multi)\b', text, re.I) else 0

        # ── Tier 5: series-specific ordering ────────────────────────────────
        f_season, f_episode = _extract_season_episode(text)
        season_score  = 0
        episode_score = 0

        if is_series:
            if q_season:
                # User asked for a specific season
                if f_season == q_season:
                    season_score = 2000
                    # Within that season, ascending episode order
                    episode_score = max(0, 500 - f_episode * 10)
                else:
                    # Penalise other seasons proportionally
                    season_score = max(0, 500 - abs(f_season - q_season) * 100)
                    episode_score = max(0, 100 - f_episode * 5)
            else:
                # No specific season → latest season first, then ep ascending
                season_score  = f_season * 200     # higher season = higher score
                episode_score = max(0, 500 - f_episode * 10)  # ep01 before ep12

        # ── Assemble final score ─────────────────────────────────────────────
        return (
            exact
            + word_score
            + year_bonus
            + lang_bonus + lang_label_bonus
            + quality * 5
            + resolution * 3
            + multi_audio
            + season_score
            + episode_score
        )

    return sorted(files, key=score, reverse=True)


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — DB SEARCH with multi-pattern regex
# ══════════════════════════════════════════════════════════════════════════════

def _build_regex_patterns(pq: ParsedQuery) -> list:
    """
    Build a list of regex patterns (broadest → narrowest) that Stage 1 tries.
    Having multiple patterns means we can do a single DB call with $or,
    capturing fuzzy-ish matches (punctuation variants, letter-spacing, etc.)
    without any network round-trip.

    Patterns generated:
      P1 – collapsed (no punctuation/spaces): matches dom's / S.W.A.T / B A S S
      P2 – word-boundary lookahead for each word (order-independent)
      P3 – exact normalised string
    """
    patterns = []
    norm = pq.normalized

    if not norm:
        return [re.compile('.', re.IGNORECASE)]

    # ── P1: collapsed match (handles punctuation variants and letter-spacing) ─
    # e.g. query "swat" matches "S.W.A.T", "S W A T", "SWAT"
    # e.g. query "doms" matches "dom's"
    # e.g. query "B.A.S.S" matches "BASS", "B A S S", "B.A.S.S"
    title_collapsed = _collapse(pq.title_only or norm)
    if title_collapsed:
        # Build a regex that ignores all non-alnum chars between each character
        spaced = r'[\W_]*'.join(re.escape(c) for c in title_collapsed)
        patterns.append(re.compile(spaced, re.IGNORECASE))

    # ── P2: each word must appear somewhere (order-independent, word-boundary) ─
    words = [w for w in _strip_tech(norm).split() if len(w) > 1]
    if words:
        lookaheads = ''.join(r'(?=.*\b' + re.escape(w) + r'\b)' for w in words)
        try:
            patterns.append(re.compile(lookaheads + '.*', re.IGNORECASE))
        except Exception:
            pass

    # ── P3: direct normalised string (fast exact path) ───────────────────────
    if ' ' not in norm:
        try:
            patterns.append(re.compile(
                r'(\b|[._\-+\[\]()\s])' + re.escape(norm) + r'(\b|[._\-+\[\]()\s]|$)',
                re.IGNORECASE
            ))
        except Exception:
            pass
    else:
        try:
            patterns.append(re.compile(re.escape(norm), re.IGNORECASE))
        except Exception:
            pass

    return patterns if patterns else [re.compile(re.escape(norm), re.IGNORECASE)]


def _do_search(filter_dict) -> list:
    """Blocking MongoDB search — always run via asyncio.to_thread."""
    result_map = {}
    for doc in collection.find(filter_dict):
        result_map[doc['_id']] = doc
    if is_second_db_configured():
        for doc in second_collection.find(filter_dict):
            result_map.setdefault(doc['_id'], doc)
    return list(result_map.values())


async def get_search_results(query, max_results=MAX_BTN, offset=0, lang=None):
    """
    Stage 1 search.

    Strategy:
      1. Parse user query → extract title, season, episode, year, lang.
      2. Build multi-pattern regex ($or) that handles:
           • Missing/extra punctuation  (dom's ↔ doms, S.W.A.T ↔ SWAT)
           • Letter spacing             (B A S S ↔ BASS)
           • Missing/extra spaces       (ironman ↔ iron man)
           • Word reorder               (all words must be present)
      3. Run in thread pool (non-blocking).
      4. Rank with multi-factor scoring.
    """
    query = str(query).strip()
    filter_words = await get_filter_words()

    pq = parse_query(query)
    norm = clean_query(pq.normalized, filter_words)
    if not norm:
        return [], '', 0

    # Rebuild pq with cleaned norm
    pq = parse_query(norm)

    patterns = _build_regex_patterns(pq)

    # Build a single $or across all patterns (both file_name and caption)
    or_clauses = []
    for pat in patterns:
        if USE_CAPTION_FILTER:
            or_clauses.append({'file_name': pat})
            or_clauses.append({'caption': pat})
        else:
            or_clauses.append({'file_name': pat})

    filter_dict = {'$or': or_clauses} if or_clauses else ({'file_name': patterns[0]} if patterns else {})

    results = await asyncio.to_thread(_do_search, filter_dict)

    if lang:
        lang_files = [f for f in results if lang in (f.get('file_name') or '').lower()]
        total_results = len(lang_files)
        files = lang_files[offset:offset + max_results]
    else:
        ranked = rank_results(norm, results, pq)
        total_results = len(ranked)
        files = ranked[offset:offset + max_results]

    next_offset = offset + max_results
    if next_offset >= total_results:
        next_offset = ''

    return files, next_offset, total_results


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — AI SPELL CHECK (fast DB fuzzy + TMDB fallback)
# ══════════════════════════════════════════════════════════════════════════════

def _fuzzy_score(a: str, b: str) -> int:
    """
    Fast multi-metric fuzzy score without fuzzywuzzy (avoids import overhead
    for every call).  Returns 0-100.

    Combines:
      • character-level ratio (handles typos: shidat→shiddat, yuth→youth)
      • token_set style (word reorder + extra words)
      • collapsed equality (punctuation-stripped)
    """
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b:
        return 0

    # Collapsed equality: "swat" == "s.w.a.t"
    if _collapse(a) == _collapse(b):
        return 100

    try:
        from fuzzywuzzy import fuzz as _fuzz
        return max(
            _fuzz.ratio(a, b),
            _fuzz.partial_ratio(a, b),
            _fuzz.token_set_ratio(a, b),
        )
    except ImportError:
        pass

    # Fallback: simple character overlap ratio
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 0
    common = sum(1 for c in a if c in b)
    return int(100 * 2 * common / (la + lb))


async def ai_spell_check(wrong_name: str) -> str | None:
    """
    Stage 2 — Two-phase spell correction.

    Phase A (fast, no network):
      Uses the in-memory title cache (base titles stripped of tech tags).
      Handles:
        • Spelling typos          (shidat → shiddat, yuth → youth, yoou → you)
        • Missing/extra spaces    (ironman → iron man, rabb da radio)
        • Punctuation variants    (doms → dom's, swat → s.w.a.t)
        • Letter spacing          (B.A.S.S → BASS)
        • Word reorder/miss       (warniings2021 → warning 2021)
        • 1-2 letter insertions/deletions (losst here → lost here)
      Minimum score: 72 to avoid false positives.

    Phase B (TMDB fallback):
      Fetches title candidates for queries Phase A couldn't resolve.
      Minimum score: 55.
    """
    raw = wrong_name.strip()
    if not raw:
        return None

    pq = parse_query(raw)
    query_base = _strip_tech(pq.title_only or raw)
    query_collapsed = _collapse(query_base)

    def _strip_year_suffix(t: str) -> str:
        return re.sub(r'\s*\(\d{4}\)\s*$', '', t).strip()

    # ── Phase A: DB fuzzy ────────────────────────────────────────────────────
    try:
        title_cache = await get_title_cache()  # list of (stripped_lower, original_fn)

        candidates = []
        for stripped, original in title_cache:
            # Collapsed match first (zero-cost)
            if _collapse(stripped) == query_collapsed:
                # Direct collapsed match → test immediately
                files, _, _ = await get_search_results(original)
                if files:
                    logger.info("ai_spell_check[collapsed]: '%s' → '%s'", raw, original)
                    return original

            score = _fuzzy_score(query_base, stripped)
            if score >= 72:
                candidates.append((score, stripped, original))

        # Sort by score desc, try each
        candidates.sort(key=lambda x: -x[0])
        for score, stripped, original in candidates[:20]:
            files, _, _ = await get_search_results(original)
            if files:
                logger.info("ai_spell_check[DB fuzzy=%d]: '%s' → '%s'", score, raw, original)
                return original

    except Exception as exc:
        logger.debug("ai_spell_check Phase A error: %s", exc)

    # ── Phase B: TMDB ────────────────────────────────────────────────────────
    try:
        import aiohttp, socket
        from info import TMDB_API_KEY
        if not TMDB_API_KEY:
            raise ValueError("no key")

        clean_title = pq.title_only or raw
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        timeout = aiohttp.ClientTimeout(total=6, connect=3)
        tmdb_candidates = []
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as sess:
            for endpoint in ("movie", "tv"):
                params = {"api_key": TMDB_API_KEY, "query": clean_title, "page": 1}
                if pq.year:
                    params["year"] = pq.year
                async with sess.get(
                    f"https://api.themoviedb.org/3/search/{endpoint}", params=params
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        key = "name" if endpoint == "tv" else "title"
                        for item in (data.get("results") or [])[:10]:
                            t = item.get(key) or item.get(f"original_{key}")
                            if t:
                                tmdb_candidates.append(t)

        scored = sorted(
            [(t, _fuzzy_score(query_base, _strip_tech(t))) for t in tmdb_candidates],
            key=lambda x: -x[1]
        )
        for title, score in scored:
            if score < 55:
                break
            for candidate in list(dict.fromkeys([title, _strip_year_suffix(title)])):
                files, _, _ = await get_search_results(candidate)
                if files:
                    logger.info("ai_spell_check[TMDB score=%d]: '%s' → '%s'", score, raw, candidate)
                    return candidate

    except Exception as exc:
        logger.debug("ai_spell_check Phase B error: %s", exc)

    return None


# ══════════════════════════════════════════════════════════════════════════════
# FILE SAVE / DELETE / FETCH
# ══════════════════════════════════════════════════════════════════════════════

async def save_file(media):
    file_id = unpack_new_file_id(media.file_id)
    file_name = re.sub(r"@\w+|(_|\-|\.|\+)", " ", str(media.file_name))
    file_caption = re.sub(r"@\w+|(_|\-|\.|\+)", " ", str(media.caption))
    category = await _classify_file(file_name, file_caption)

    document = {
        '_id': file_id,
        'file_name': file_name,
        'file_size': media.file_size,
        'caption': file_caption,
        'category': category,
    }

    def _insert():
        try:
            collection.insert_one(document)
            return 'suc'
        except DuplicateKeyError:
            return 'dup'
        except OperationFailure:
            if SECOND_FILES_DATABASE_URL:
                try:
                    second_collection.insert_one(document)
                    return 'suc'
                except DuplicateKeyError:
                    return 'dup'
            return 'err'

    result = await asyncio.to_thread(_insert)
    if result == 'suc':
        global _TITLE_CACHE_TIME
        _TITLE_CACHE_TIME = 0.0   # invalidate cache
    logger.info(f'Save [{category}] {file_name}: {result}')
    return result


async def _classify_file(file_name: str, caption: str) -> str:
    import re as _re
    try:
        _ANIME_KW = _re.compile(
            r'\b(anime|hentai|ova|oad|ona|manhwa|manhua|donghua|'
            r'shonen|seinen|shoujo|isekai|mecha|yaoi|yuri)\b|'
            r'[\u3040-\u30FF\u4E00-\u9FFF]', _re.IGNORECASE)
        text = f"{caption} {file_name}"
        if _ANIME_KW.search(text):
            return 'anime'

        _EXT2 = _re.compile(r'\.(mkv|mp4|avi|mov)$', _re.IGNORECASE)
        _JUNK2 = _re.compile(
            r'\b(480p|720p|1080p|2160p|4k|hdr|bluray|bdrip|web[\-\s]?dl|webrip|'
            r'x264|x265|hevc|aac|ac3|dts|hindi|english|tamil|dubbed|subbed)\b.*',
            _re.IGNORECASE)
        _SE2 = _re.compile(r'\b[Ss](\d{1,2})[Ee](\d{1,2})\b')
        _S2 = _re.compile(r'\b[Ss](\d{1,2})\b')
        _YEAR2 = _re.compile(r'\b(19[5-9]\d|20[0-3]\d)\b')

        raw = _EXT2.sub('', file_name)
        raw = _re.sub(r'[@\[\]()\-_\+\.]+', ' ', raw)
        has_season = bool(_SE2.search(raw) or _S2.search(raw))
        raw = _JUNK2.sub('', raw)
        raw = _re.sub(r'\s{2,}', ' ', raw).strip()
        year_m = _YEAR2.search(raw)
        year = year_m.group(0) if year_m else ''
        title = _YEAR2.sub('', raw).strip()
        if not title:
            return 'series' if has_season else 'movie'

        try:
            from info import TMDB_API_KEY
            import aiohttp, socket
            if not TMDB_API_KEY:
                raise ValueError("no key")
            to = aiohttp.ClientTimeout(total=8, connect=4)
            connector = aiohttp.TCPConnector(family=socket.AF_INET)
            params = {'api_key': TMDB_API_KEY, 'query': title, 'page': 1}
            if year:
                params['year'] = year
            async with aiohttp.ClientSession(connector=connector, timeout=to) as sess:
                async with sess.get('https://api.themoviedb.org/3/search/multi', params=params) as r:
                    if r.status != 200:
                        raise ValueError(f"TMDB {r.status}")
                    data = await r.json()
                results = [x for x in data.get('results', []) if x.get('media_type') != 'person']
                if not results:
                    return 'series' if has_season else 'movie'
                item = results[0]
                mt = item.get('media_type', 'movie')
                iid = item.get('id')
                async with sess.get(
                    f'https://api.themoviedb.org/3/{mt}/{iid}',
                    params={'api_key': TMDB_API_KEY}
                ) as dr:
                    detail = await dr.json() if dr.status == 200 else item

            genres = [g['name'].lower() for g in detail.get('genres', [])]
            if 'animation' in genres:
                return 'anime'
            if mt == 'tv' and detail.get('original_language') == 'ja':
                return 'anime'
            return 'series' if mt == 'tv' else 'movie'
        except Exception:
            return 'series' if has_season else 'movie'

    except Exception as e:
        logger.error(f"_classify_file error: {e}")
        return 'movie'


async def delete_files(query):
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.+\-_])' + re.escape(query) + r'(\b|[\.+\-_])'
    else:
        raw_pattern = re.escape(query).replace(r'\ ', r'[\s._\-+]+')
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except Exception:
        regex = re.compile(re.escape(query), re.IGNORECASE)

    filter_dict = {'file_name': regex}

    def _del():
        r1 = collection.delete_many(filter_dict)
        total = r1.deleted_count
        if is_second_db_configured():
            r2 = second_collection.delete_many(filter_dict)
            total += r2.deleted_count
        return total

    return await asyncio.to_thread(_del)


async def get_file_details(query):
    def _find():
        fd = collection.find_one({'_id': query})
        if not fd and is_second_db_configured():
            fd = second_collection.find_one({'_id': query})
        return fd
    return await asyncio.to_thread(_find)


# ══════════════════════════════════════════════════════════════════════════════
# CLASSIFY TMDB (for save_file)
# ══════════════════════════════════════════════════════════════════════════════

def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0
            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")


def unpack_new_file_id(new_file_id):
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash
        )
    )
    return file_id


async def get_filter_words():
    try:
        doc = await filter_words_collection.find_one({"_id": "filter_words"})
        return set(doc["words"]) if doc else set()
    except Exception as e:
        logger.error(f"Error getting filter words: {e}")
        return set()


async def set_filter_words(words):
    try:
        await filter_words_collection.update_one(
            {"_id": "filter_words"},
            {"$set": {"words": list(words)}},
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error setting filter words: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# FULL RESULT FETCH — for smart cache (returns all ranked results at once)
# ══════════════════════════════════════════════════════════════════════════════

async def get_all_results(query: str) -> list:
    """
    Fetch and rank ALL results for a query in one shot.
    Called once per unique query; result list is stored in RESULT_CACHE.
    Subsequent page requests just slice this list — zero extra DB calls.
    """
    query = str(query).strip()
    filter_words = await get_filter_words()

    pq = parse_query(query)
    norm = clean_query(pq.normalized, filter_words)
    if not norm:
        return []

    pq = parse_query(norm)
    patterns = _build_regex_patterns(pq)

    or_clauses = []
    for pat in patterns:
        if USE_CAPTION_FILTER:
            or_clauses.append({'file_name': pat})
            or_clauses.append({'caption': pat})
        else:
            or_clauses.append({'file_name': pat})

    filter_dict = {'$or': or_clauses} if or_clauses else ({'file_name': patterns[0]} if patterns else {})

    results = await asyncio.to_thread(_do_search, filter_dict)
    return rank_results(norm, results, pq)
