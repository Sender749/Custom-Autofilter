import re
import json
import time
import hmac
import hashlib
import logging
import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, unquote
from aiohttp import web
from aiohttp.web_request import Request

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_executor = ThreadPoolExecutor(max_workers=6)

try:
    from database.ia_filterdb import (
        collection, second_collection,
        get_search_results, get_file_details,
        is_second_db_configured,
    )
    from utils import get_size, temp
    from info import TMDB_API_KEY, BOT_TOKEN
    DB_AVAILABLE = True
except ImportError as _ie:
    DB_AVAILABLE = False
    TMDB_API_KEY = ''
    BOT_TOKEN = ''
    logger.warning(f"DB not available for miniapp routes: {_ie}")

# ─── In-memory meta cache ─────────────────────────────────────────────────────
_META_CACHE: dict = {}
_META_CACHE_TTL = 3600
_CARD_CACHE: dict = {}
_CARD_CACHE_TTL = 300   # 5 min cache so new files appear quickly

def _cache_get(store, key, ttl):
    entry = store.get(key)
    if entry and (time.time() - entry[0]) < ttl:
        return entry[1]
    return None

def _cache_set(store, key, val, ttl):
    store[key] = (time.time(), val)

# ─── filename / title helpers ─────────────────────────────────────────────────

_EXT  = re.compile(r'\.(mkv|mp4|avi|mov|flv|wmv|webm|ts|m4v)$', re.IGNORECASE)
_PUNC = re.compile(r'[@\[\]()\-_\+\.]+')
_SPC  = re.compile(r'\s{2,}')
_YEAR = re.compile(r'\b(19[5-9]\d|20[0-3]\d)\b')
_SE   = re.compile(r'\b[Ss](\d{1,2})[Ee](\d{1,2})\b')
_S    = re.compile(r'\b[Ss](\d{1,2})\b')
_E    = re.compile(r'\b[Ee](\d{1,2})\b')

_JUNK = re.compile(
    r'\b(480p|720p|1080p|2160p|4k|uhd|hdr10?[\+]?|hdrip|bluray|bdrip|remux|'
    r'web[\-\s]?dl|webrip|webdl|hdtv|dvdrip|dvdscr|dvd|cam|telesync|'
    r'x264|x265|hevc|avc|aac|ac3|mp3|dts|ddp|atmos|eac3|'
    r'proper|repack|extended|unrated|theatrical|'
    r'retail|limited|internal|'
    r'sample|trailer)b.*',
    re.IGNORECASE,
)
_LANG_STRIP = re.compile(
    r'\b(hindi|english|tamil|telugu|kannada|malayalam|bengali|punjabi|'
    r'dual[\s.\-]?audio|multi[\s.\-]?audio|dubbed|subbed|hardsub|esub|'
    r'hin|eng|tam|tel|kan|mal|ben|pun)\b.*',
    re.IGNORECASE,
)
_TAG_STRIP = re.compile(r'@\w+|\[[\w.\-]+\]', re.IGNORECASE)

_QUAL_MAP = [
    ('2160p', '2160p'), ('4k', '4K'), ('uhd', '4K'),
    ('1080p', '1080p'), ('720p', '720p'), ('480p', '480p'),
    ('360p', '360p'), ('cam', 'CAM'),
]
_PRINT_MAP = [
    ('bluray', 'BluRay'), ('bdrip', 'BDRip'), ('remux', 'Remux'),
    ('web-dl', 'WEB-DL'), ('webdl', 'WEB-DL'), ('webrip', 'WEBRip'),
    ('hdrip', 'HDRip'), ('hdtv', 'HDTV'), ('dvdrip', 'DVDRip'),
    ('dvdscr', 'DVDScr'), ('cam', 'CAM'), ('ts', 'TS'),
]
_LANG_MAP = {
    'hindi': 'Hindi', 'english': 'English', 'tamil': 'Tamil',
    'telugu': 'Telugu', 'kannada': 'Kannada', 'malayalam': 'Malayalam',
    'bengali': 'Bengali', 'punjabi': 'Punjabi',
    'dual audio': 'Dual Audio', 'dual': 'Dual Audio',
    'multi audio': 'Multi Audio', 'multi': 'Multi Audio',
}
_SUB_MAP = [
    ('esub', 'English'), ('hardsub', 'Hardcoded'),
    ('subbed', 'Yes'), ('subtitle', 'Yes'),
]
_QUAL_SCORE = {
    '2160p': 100, '4k': 100, 'uhd': 100, '1080p': 80,
    '720p': 60, '480p': 30, '360p': 20, 'cam': 1, 'ts': 1,
}

# ─── Content-type detection patterns ─────────────────────────────────────────

# Anime: strong explicit signals only — avoid false positives
_ANIME_KEYWORDS = re.compile(
    r'\b(anime|hentai|ecchi|ova|oad|ona|manhwa|manhua|donghua|'
    r'shonen|seinen|shoujo|josei|isekai|mecha|yaoi|yuri|'
    r'dubbed\s*anime|sub\s*anime)\b|'
    r'[\u3040-\u30FF\u4E00-\u9FFF]',   # Japanese/Chinese characters
    re.IGNORECASE,
)

# Series: S01E01, S1E1, Season 1 Episode 1, or multi-episode patterns
# Note: is_series() uses extract_season_ep() which already handles S/E patterns

# Known series keywords that appear in file names even without S/E notation
_SERIES_KEYWORDS = re.compile(
    r'\b(web\s*series|mini\s*series|limited\s*series|tv\s*show|'
    r'complete\s*series|full\s*series|season\s*\d+|'
    r's\d{1,2}\s*complete|s\d{1,2}\s*pack|'
    r'episodes?\s*\d+[-–]\d+|ep\s*\d+\s*to\s*\d+)\b',
    re.IGNORECASE,
)

def extract_year(text):
    h = _YEAR.findall(text)
    return h[0] if h else ''

def extract_quality(text):
    t = text.lower()
    for key, label in _QUAL_MAP:
        if key in t:
            return label
    return ''

def extract_print_type(text):
    t = text.lower()
    for key, label in _PRINT_MAP:
        if key in t:
            return label
    return ''

def extract_language(text):
    t = text.lower()
    langs = []
    for key, label in _LANG_MAP.items():
        if key in t and label not in langs:
            langs.append(label)
    if 'Dual Audio' in langs or 'Multi Audio' in langs:
        langs = [l for l in langs if l not in ('Hindi', 'English', 'Tamil', 'Telugu',
                                                 'Kannada', 'Malayalam', 'Bengali', 'Punjabi')]
    return langs[:3]

def extract_subtitles(text):
    t = text.lower()
    for key, label in _SUB_MAP:
        if key in t:
            return label
    return ''

def extract_season_ep(text):
    m = _SE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    s = _S.search(text)
    e = _E.search(text)
    return (int(s.group(1)) if s else 0), (int(e.group(1)) if e else 0)

def quality_score(text):
    t = text.lower()
    for k, v in _QUAL_SCORE.items():
        if k in t:
            return v
    return 40

def clean_title(filename):
    n = _EXT.sub('', filename)
    n = _TAG_STRIP.sub(' ', n)
    n = _PUNC.sub(' ', n)
    m = _SE.search(n)
    if m:
        n = n[:m.start()]
    else:
        ms = _S.search(n)
        if ms:
            n = n[:ms.start()]
    my = _YEAR.search(n)
    if my:
        n = n[:my.start()]
    n = _JUNK.sub('', n)
    n = _LANG_STRIP.sub('', n)
    n = _SPC.sub(' ', n).strip()
    return n

def title_key(filename):
    return clean_title(filename).lower().strip()

def is_series(text):
    s, _ = extract_season_ep(text)
    return s > 0

def is_anime(text):
    """Detect anime by keywords or Japanese characters."""
    return bool(_ANIME_KEYWORDS.search(text))

def detect_content_type(text, tmdb_type=None, tmdb_genres=None, tmdb_origin_country=None, tmdb_original_language=None):
    """
    Detect movie | series | anime.
    Priority:
      1) Explicit anime keywords in filename always win
      2) TMDB genres contain Animation → classify as anime (both TV and movies)
      3) TMDB origin country is Japan (JP) or original language is Japanese → anime
      4) TMDB says TV show → series
      5) S01E01 / Season N pattern in filename → series
      6) Series keywords in filename → series
      7) Default → movie

    This ensures ALL animated content (animated movies, anime series, cartoons)
    goes to the Anime tab. TV shows go to Series. Everything else to Movies.
    """
    # Step 1: Explicit anime/animation signals in filename always win
    if is_anime(text):
        return 'anime'

    # Step 2: TMDB genres — Animation means anime tab regardless of movie/tv
    if tmdb_genres:
        genre_str = ' '.join(g.lower() for g in tmdb_genres)
        if 'animation' in genre_str or 'anime' in genre_str:
            return 'anime'

    # Step 3: Japanese origin → anime (covers anime that TMDB may list as just "Action")
    if tmdb_origin_country and 'JP' in (tmdb_origin_country if isinstance(tmdb_origin_country, list) else [tmdb_origin_country]):
        if tmdb_type == 'tv':
            return 'anime'
    if tmdb_original_language and tmdb_original_language == 'ja':
        if tmdb_type == 'tv':
            return 'anime'

    # Step 4: TMDB says it's a TV show → series
    if tmdb_type == 'tv':
        return 'series'

    # Step 5: S01E01 / Season N pattern in filename
    if is_series(text):
        return 'series'

    # Step 6: Series keywords in filename
    if _SERIES_KEYWORDS.search(text):
        return 'series'

    return 'movie'

def fmt_label(fname, size_str=''):
    parts = []
    q = extract_quality(fname)
    if q: parts.append(q)
    langs = extract_language(fname)
    if langs: parts.append('/'.join(langs))
    s, e = extract_season_ep(fname)
    if s and e: parts.append(f'S{s:02d}E{e:02d}')
    elif s: parts.append(f'Season {s}')
    if size_str: parts.append(size_str)
    return ' · '.join(parts) if parts else fname[:55]


# ─── Async-safe DB helpers ────────────────────────────────────────────────────

async def _run_sync(fn, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, fn, *args)


def _sync_browse_by_type(content_type, skip, limit):
    """
    Fetch docs sorted by _id DESCENDING (newest first).
    Filter by detected content_type.

    Uses the stored 'category' field (set at index time via TMDB) when available
    for instant accurate classification. Falls back to filename heuristics for
    older docs that don't have the category field yet.
    """
    try:
        # Fetch a large window — filtering reduces count significantly
        fetch_window = max(limit * 20, 400)
        docs = list(collection.find({}).sort('_id', -1).skip(skip).limit(fetch_window))
        if is_second_db_configured():
            extra = list(second_collection.find({}).sort('_id', -1)
                         .skip(max(0, skip - 10000)).limit(fetch_window))
            seen = {d['_id'] for d in docs}
            docs += [d for d in extra if d['_id'] not in seen]
        # Sort combined by _id descending (newest first)
        docs.sort(key=lambda d: d['_id'], reverse=True)

        def _classify(d):
            # Use pre-stored TMDB-derived category if available
            stored = d.get('category')
            if stored in ('movie', 'series', 'anime'):
                return stored
            # Fallback: filename heuristics for legacy docs
            return detect_content_type(d.get('caption') or d.get('file_name', ''))

        filtered = [d for d in docs if _classify(d) == content_type]
        return filtered
    except Exception as exc:
        logger.error(f'_sync_browse_by_type error: {exc}')
        return []


async def _browse_by_type(content_type, skip, limit):
    return await _run_sync(_sync_browse_by_type, content_type, skip, limit)


def _sync_paged_files(skip, limit):
    """Fetch ALL types, newest first."""
    try:
        docs = list(collection.find({}).sort('_id', -1).skip(skip).limit(limit * 3))
        if is_second_db_configured() and len(docs) < limit * 2:
            extra = list(
                second_collection.find({}).sort('_id', -1).skip(max(0, skip - 5000)).limit(limit * 3)
            )
            seen = {d['_id'] for d in docs}
            docs += [d for d in extra if d['_id'] not in seen]
        docs.sort(key=lambda d: d['_id'], reverse=True)
        return docs
    except Exception as exc:
        logger.error(f'_sync_paged_files error: {exc}')
        return []

async def _paged_files(skip, limit):
    return await _run_sync(_sync_paged_files, skip, limit)


def _fuzzy_normalize(q):
    """Collapse repeated chars and normalize spaces for fuzzy matching."""
    q = q.lower().strip()
    q = re.sub(r'(.)\1+', r'\1', q)       # "moovie" -> "movie"
    q = re.sub(r'[^a-z0-9\u0080-\uffff\s]', ' ', q)
    q = re.sub(r'\s{2,}', ' ', q).strip()
    return q


def _sync_search_by_title_key(key, limit, fuzzy=False):
    words = key.split()
    if not words:
        return []
    # Build pattern: if fuzzy, allow partial match on first word
    pattern = re.compile(re.escape(words[0]), re.IGNORECASE)
    results = []
    seen_ids = set()
    cols = [collection]
    if is_second_db_configured():
        cols.append(second_collection)
    for col in cols:
        try:
            for doc in col.find(
                {'$or': [{'file_name': pattern}, {'caption': pattern}]}
            ).sort('_id', -1).limit(limit * 6):
                fname = doc.get('caption') or doc.get('file_name', '')
                fk = title_key(fname)
                # fuzzy: partial match; exact: full key match
                match = (fk == key) if not fuzzy else (key in fk or fk in key or words[0] in fk.lower())
                if match and doc['_id'] not in seen_ids:
                    seen_ids.add(doc['_id'])
                    results.append(doc)
        except Exception as exc:
            logger.warning(f'_search_by_title_key col error: {exc}')
    # Sort newest first
    results.sort(key=lambda d: d['_id'], reverse=True)
    return results[:limit]

async def _search_by_title_key(key, limit=200, fuzzy=False):
    return await _run_sync(_sync_search_by_title_key, key, limit, fuzzy)


def _group_docs(docs):
    """Group files by title_key. Representative = newest (highest _id)."""
    groups = {}
    for doc in docs:
        fname = doc.get('caption') or doc.get('file_name', '')
        key = title_key(fname)
        if not key:
            continue
        if key not in groups:
            groups[key] = {'rep': doc, 'files': [], 'year': extract_year(fname)}
        groups[key]['files'].append(doc)
        # Keep newest doc as representative
        if doc['_id'] > groups[key]['rep']['_id']:
            groups[key]['rep'] = doc
    return groups

def _organise_files(docs):
    any_series = any(
        is_series(d.get('caption') or d.get('file_name', '')) for d in docs
    )
    if not any_series:
        return {
            'type': 'movie',
            'movie_files': sorted(
                docs, key=lambda d: -quality_score(d.get('caption') or d.get('file_name', ''))
            ),
            'seasons': None,
        }
    tree = defaultdict(lambda: defaultdict(list))
    for doc in docs:
        fname = doc.get('caption') or doc.get('file_name', '')
        s, e = extract_season_ep(fname)
        tree[s][e].append(doc)
    result = {}
    for s_num in sorted(tree):
        result[s_num] = {}
        for e_num in sorted(tree[s_num]):
            result[s_num][e_num] = sorted(
                tree[s_num][e_num],
                key=lambda d: -quality_score(d.get('caption') or d.get('file_name', ''))
            )
    return {'type': 'series', 'movie_files': None, 'seasons': result}

def doc_to_obj(doc):
    caption   = doc.get('caption', '').strip()
    fname     = doc.get('file_name', '')
    display   = caption if caption else fname
    raw_meta  = caption if caption else fname
    size_str  = get_size(doc.get('file_size', 0))
    s, e      = extract_season_ep(raw_meta)
    langs     = extract_language(raw_meta)
    subs      = extract_subtitles(raw_meta)
    quality   = extract_quality(raw_meta)
    print_type = extract_print_type(raw_meta)
    return {
        'id':         str(doc['_id']),
        'caption':    display,
        'raw_name':   fname,
        'label':      fmt_label(raw_meta, size_str),
        'size':       size_str,
        'quality':    quality,
        'print_type': print_type,
        'languages':  langs,
        'subtitles':  subs,
        'season':     s,
        'episode':    e,
    }


# ─── Metadata ─────────────────────────────────────────────────────────────────

TMDB_IMG  = 'https://image.tmdb.org/t/p/w500'
TMDB_BACK = 'https://image.tmdb.org/t/p/w1280'

async def _tmdb_fetch(title, year=''):
    api_key = TMDB_API_KEY
    if not api_key:
        return None
    try:
        import aiohttp, socket
        to = aiohttp.ClientTimeout(total=8, connect=4)
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        async with aiohttp.ClientSession(connector=connector, timeout=to) as sess:
            params = {'api_key': api_key, 'query': title, 'page': 1}
            if year:
                params['year'] = year
            async with sess.get('https://api.themoviedb.org/3/search/multi', params=params) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                results = [x for x in data.get('results', []) if x.get('media_type') != 'person']
            if not results and year:
                async with sess.get('https://api.themoviedb.org/3/search/multi',
                                    params={'api_key': api_key, 'query': title, 'page': 1}) as r2:
                    if r2.status == 200:
                        results = [x for x in (await r2.json()).get('results', []) if x.get('media_type') != 'person']
            if not results:
                return None
            tl = title.lower()
            item = next((x for x in results if (x.get('title') or x.get('name') or '').lower() == tl), results[0])
            mt = item.get('media_type', 'movie')
            iid = item.get('id')
            detail = None
            async with sess.get(f'https://api.themoviedb.org/3/{mt}/{iid}',
                                 params={'api_key': api_key, 'append_to_response': 'credits'}) as dr:
                if dr.status == 200:
                    detail = await dr.json()
        src = detail or item
        genres = [g['name'] for g in src.get('genres', [])] if detail else []
        cr = (detail or {}).get('credits', {})
        cast = [c['name'] for c in cr.get('cast', [])[:6]]
        dirs = [c['name'] for c in cr.get('crew', []) if c.get('job') == 'Director'][:2]
        rt = (detail or {}).get('runtime') or (((detail or {}).get('episode_run_time') or [None])[0])
        return {
            'title':    src.get('title') or src.get('name', title),
            'year':     (src.get('release_date') or src.get('first_air_date') or '')[:4],
            'poster':   f"{TMDB_IMG}{src['poster_path']}" if src.get('poster_path') else None,
            'backdrop': f"{TMDB_BACK}{src['backdrop_path']}" if src.get('backdrop_path') else None,
            'rating':   round(float(src.get('vote_average', 0) or 0), 1),
            'plot':     src.get('overview', ''),
            'genres':   genres,
            'cast':     cast,
            'director': ', '.join(dirs),
            'runtime':  f'{rt} min' if rt else '',
            'type':     mt,
            'seasons':  (detail or {}).get('number_of_seasons'),
            'episodes': (detail or {}).get('number_of_episodes'),
            'tagline':  (detail or {}).get('tagline', ''),
            'status':   (detail or {}).get('status', ''),
            'imdb_id':  (detail or {}).get('imdb_id', ''),
            'origin_country':    (detail or src).get('origin_country', []),
            'original_language': (detail or src).get('original_language', ''),
        }
    except Exception as exc:
        logger.warning(f'TMDB error for "{title}": {type(exc).__name__}: {exc}')
        return None

async def _imdb_fetch(title, year=''):
    try:
        from imdb import Cinemagoer
        ia = Cinemagoer()
        hits = await asyncio.wait_for(_run_sync(lambda: ia.search_movie(title, results=8)), timeout=6)
        if not hits:
            return None
        if year:
            filtered = [h for h in hits if str(h.get('year', '')) == year]
            hits = filtered or hits
        typed = [h for h in hits if h.get('kind') in ('movie', 'tv series')]
        pick = (typed or hits)[0]
        mv = await asyncio.wait_for(_run_sync(lambda: ia.get_movie(pick.movieID)), timeout=8)
        raw_plot = mv.get('plot', [])
        plot = ''
        if raw_plot:
            plot = raw_plot[0].split('::')[0] if '::' in raw_plot[0] else raw_plot[0]
        cast = [str(c) for c in (mv.get('cast') or [])[:6]]
        dirs_raw = mv.get('directors') or mv.get('director') or []
        dirs = [str(d) for d in (dirs_raw[:2] if isinstance(dirs_raw, list) else [])]
        rt = (mv.get('runtimes') or [''])[0]
        genres = mv.get('genres', [])[:4]
        poster = mv.get('full-size cover url') or mv.get('cover url')
        mt = 'tv' if mv.get('kind') == 'tv series' else 'movie'
        return {
            'title': mv.get('title', title), 'year': str(mv.get('year', year or '')),
            'poster': poster, 'backdrop': None,
            'rating': float(mv.get('rating', 0) or 0), 'plot': plot[:600],
            'genres': genres if isinstance(genres, list) else [],
            'cast': cast, 'director': ', '.join(dirs),
            'runtime': f'{rt} min' if rt else '', 'type': mt,
            'seasons': mv.get('number of seasons'), 'episodes': None,
            'tagline': '', 'status': '', 'imdb_id': f"tt{pick.movieID}",
        }
    except Exception as exc:
        logger.warning(f'IMDB fallback error for "{title}": {type(exc).__name__}: {exc}')
        return None

async def _get_meta(title, year=''):
    if not title:
        return None
    cache_key = f"{title.lower()}|{year}"
    cached = _cache_get(_META_CACHE, cache_key, _META_CACHE_TTL)
    if cached is not None:
        return cached
    tmdb = await _tmdb_fetch(title, year)
    if tmdb and tmdb.get('poster'):
        _cache_set(_META_CACHE, cache_key, tmdb, _META_CACHE_TTL)
        return tmdb
    imdb = await _imdb_fetch(title, year)
    if imdb:
        if tmdb:
            imdb['backdrop'] = imdb.get('backdrop') or tmdb.get('backdrop')
            imdb['genres']   = imdb.get('genres') or tmdb.get('genres', [])
        _cache_set(_META_CACHE, cache_key, imdb, _META_CACHE_TTL)
        return imdb
    _cache_set(_META_CACHE, cache_key, tmdb, _META_CACHE_TTL)
    return tmdb


# ─── CORS / response ──────────────────────────────────────────────────────────

CORS = {
    'Access-Control-Allow-Origin':  '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, X-Telegram-Init-Data',
}

def json_resp(data, status=200):
    return web.Response(
        text=json.dumps(data, default=str),
        status=status,
        content_type='application/json',
        headers=CORS,
    )

def cors_preflight():
    return web.Response(headers=CORS)


# ─── Route handlers ───────────────────────────────────────────────────────────

async def miniapp_health(request):
    return json_resp({'ok': True, 'db': DB_AVAILABLE, 'tmdb_key': bool(TMDB_API_KEY)})


async def _make_card(grp):
    """Build a card dict for a group of files. Uses TMDB/IMDB meta."""
    try:
        rep   = grp['rep']
        fname = rep.get('caption') or rep.get('file_name', '')
        title = clean_title(fname)
        year  = grp.get('year') or extract_year(fname)
        meta = await _get_meta(title, year)
        # Determine accurate type using TMDB signals + filename
        tmdb_type   = (meta or {}).get('type')          # 'tv' or 'movie'
        tmdb_genres = (meta or {}).get('genres', [])
        tmdb_origin_country    = (meta or {}).get('origin_country', [])
        tmdb_original_language = (meta or {}).get('original_language', '')
        mtype = detect_content_type(fname, tmdb_type=tmdb_type, tmdb_genres=tmdb_genres,
                                    tmdb_origin_country=tmdb_origin_country,
                                    tmdb_original_language=tmdb_original_language)

        cache_key = title.lower() + '|' + mtype
        cached_card = _cache_get(_CARD_CACHE, cache_key, _CARD_CACHE_TTL)
        if cached_card:
            base = {**cached_card}
            base['id']         = str(rep['_id'])
            base['file_count'] = len(grp['files'])
            return base
        card = {
            'group_title': title,
            'id':          str(rep['_id']),
            'name':        (meta or {}).get('title') or title,
            'year':        (meta or {}).get('year') or year,
            'poster':      (meta or {}).get('poster'),
            'backdrop':    (meta or {}).get('backdrop'),
            'rating':      (meta or {}).get('rating'),
            'genres':      tmdb_genres,
            'type':        mtype,
            'file_count':  len(grp['files']),
        }
        _cache_set(_CARD_CACHE, cache_key, card, _CARD_CACHE_TTL)
        return card
    except Exception as exc:
        logger.error(f'_make_card error: {exc}')
        return None




async def miniapp_browse(request):
    """
    GET /miniapp/browse?type=movies|series|anime&page=0&limit=24&ts=<timestamp>
    ts param triggers cache invalidation for fresh results on refresh.
    """
    if request.method == 'OPTIONS':
        return cors_preflight()
    if not DB_AVAILABLE:
        return json_resp({'ok': False, 'error': 'DB not available'}, 500)

    # If ts (timestamp) param provided, clear CARD_CACHE to force fresh data
    if request.rel_url.query.get('ts'):
        _CARD_CACHE.clear()
        logger.info('miniapp_browse: cache cleared by ts param (refresh)')

    try:
        content_type = request.rel_url.query.get('type', 'movies').lower().strip('s')
        # normalise: "movies" -> "movie", "series" -> "series", "anime" -> "anime"
        if content_type in ('movie', 'movies'):
            content_type = 'movie'
        elif content_type in ('serie', 'series'):
            content_type = 'series'
        elif content_type == 'anime':
            content_type = 'anime'
        else:
            content_type = 'movie'

        page  = max(0, int(request.rel_url.query.get('page', 0)))
        limit = min(int(request.rel_url.query.get('limit', 24)), 40)
    except (ValueError, TypeError):
        page, limit, content_type = 0, 24, 'movie'

    # Fetch enough docs to fill `limit` grouped cards.
    # We scan more docs per page since multiple files group into one card.
    # Use a generous scan window: page * limit * 6 docs scanned per page.
    scan_skip = page * limit * 6
    all_docs = await _browse_by_type(content_type, scan_skip, limit)


    groups = _group_docs(all_docs)
    # Sort groups by representative doc _id (newest first)
    sorted_groups = sorted(groups.values(), key=lambda g: g['rep']['_id'], reverse=True)
    target_groups = sorted_groups[:limit]

    cards = await asyncio.gather(*[_make_card(g) for g in target_groups], return_exceptions=True)
    results = [c for c in cards if isinstance(c, dict)]
    has_more = len(sorted_groups) > limit

    return json_resp({'ok': True, 'results': results, 'count': len(results),
                      'page': page, 'has_more': has_more})


async def miniapp_recent(request):
    """
    GET /miniapp/recent?page=0&limit=20
    Returns all types, newest first (kept for backward compat).
    """
    if request.method == 'OPTIONS':
        return cors_preflight()
    if not DB_AVAILABLE:
        return json_resp({'ok': False, 'error': 'DB not available'}, 500)

    try:
        page  = max(0, int(request.rel_url.query.get('page', 0)))
        limit = min(int(request.rel_url.query.get('limit', 20)), 40)
    except (ValueError, TypeError):
        page, limit = 0, 20

    skip = page * (limit * 3)
    all_docs = await _paged_files(skip=skip, limit=limit * 6)
    groups = _group_docs(all_docs)
    # Always newest first
    sorted_groups = sorted(groups.values(), key=lambda g: g['rep']['_id'], reverse=True)
    target_groups = sorted_groups[:limit]

    cards = await asyncio.gather(*[_make_card(g) for g in target_groups], return_exceptions=True)
    results = [c for c in cards if isinstance(c, dict)]
    has_more = len(sorted_groups) > limit

    return json_resp({'ok': True, 'results': results, 'count': len(results),
                      'page': page, 'has_more': has_more})


async def miniapp_search(request):
    if request.method == 'OPTIONS':
        return cors_preflight()
    if not DB_AVAILABLE:
        return json_resp({'ok': False, 'error': 'DB not available'}, 500)

    q     = request.rel_url.query.get('q', '').strip()
    fuzzy = request.rel_url.query.get('fuzzy', '0') in ('1', 'true', 'yes')
    if not q:
        return json_resp({'ok': False, 'error': "Missing 'q'"}, 400)

    # Fuzzy normalize query
    search_q = _fuzzy_normalize(q) if fuzzy else q

    try:
        files, _, total = await get_search_results(search_q, max_results=200)
    except Exception as exc:
        logger.error(f'get_search_results error: {exc}')
        # Fall back to manual title-key search
        files = await _search_by_title_key(_fuzzy_normalize(q), limit=200, fuzzy=fuzzy)
        total = len(files)

    # If still no results with exact, try fuzzy word-by-word
    if not files and not fuzzy:
        try:
            files, _, total = await get_search_results(_fuzzy_normalize(q), max_results=200)
        except Exception:
            pass

    groups = _group_docs(files)
    # Sort newest first for search results too
    sorted_groups = sorted(groups.values(), key=lambda g: g['rep']['_id'], reverse=True)[:40]

    async def make_card(grp):
        try:
            rep   = grp['rep']
            fname = rep.get('caption') or rep.get('file_name', '')
            title = clean_title(fname)
            year  = grp.get('year') or extract_year(fname)
            ck    = title.lower() + '|' + year
            meta  = _cache_get(_META_CACHE, ck, _META_CACHE_TTL)
            if meta is None:
                meta = await _get_meta(title, year)
            tmdb_t = (meta or {}).get('type')
            tmdb_g = (meta or {}).get('genres', [])
            tmdb_oc = (meta or {}).get('origin_country', [])
            tmdb_ol = (meta or {}).get('original_language', '')
            ctype  = detect_content_type(fname, tmdb_type=tmdb_t, tmdb_genres=tmdb_g,
                                         tmdb_origin_country=tmdb_oc,
                                         tmdb_original_language=tmdb_ol)
            return {
                'group_title': title,
                'id':          str(rep['_id']),
                'name':        (meta or {}).get('title') or title,
                'year':        (meta or {}).get('year') or year,
                'poster':      (meta or {}).get('poster'),
                'rating':      (meta or {}).get('rating'),
                'genres':      (meta or {}).get('genres', []),
                'type':        ctype,
                'file_count':  len(grp['files']),
            }
        except Exception as exc:
            logger.error(f'make_card (search) error: {exc}')
            return None

    cards = await asyncio.gather(*[make_card(g) for g in sorted_groups], return_exceptions=True)
    results = [c for c in cards if isinstance(c, dict)]
    return json_resp({'ok': True, 'results': results, 'total': total})


async def miniapp_group_details(request):
    if request.method == 'OPTIONS':
        return cors_preflight()
    if not DB_AVAILABLE:
        return json_resp({'ok': False, 'error': 'DB not available'}, 500)

    file_id = request.rel_url.query.get('id', '').strip()
    if not file_id:
        return json_resp({'ok': False, 'error': "Missing 'id'"}, 400)

    try:
        rep = await get_file_details(file_id)
    except Exception as exc:
        logger.error(f'get_file_details error for id="{file_id}": {type(exc).__name__}: {exc}')
        return json_resp({'ok': False, 'error': 'DB lookup failed'}, 500)

    if not rep:
        return json_resp({'ok': False, 'error': 'File not found'}, 404)

    caption = rep.get('caption', '').strip()
    fname   = rep.get('file_name', '')
    primary = caption if caption else fname

    title = clean_title(primary)
    year  = extract_year(primary)
    key   = title_key(primary)

    try:
        all_variants = await _search_by_title_key(key, fuzzy=False)
    except Exception as exc:
        logger.error(f'_search_by_title_key error: {exc}')
        all_variants = []

    if not all_variants:
        all_variants = [rep]

    # Sort variants newest first
    all_variants.sort(key=lambda d: d['_id'], reverse=True)

    meta = await _get_meta(title, year)
    organised = _organise_files(all_variants)

    def ser_season(ep_dict):
        out = {}
        for ep_num, ep_docs in sorted(ep_dict.items()):
            out[str(ep_num)] = {
                'label': f'Episode {ep_num}' if ep_num > 0 else 'Season Pack',
                'files': [doc_to_obj(d) for d in ep_docs],
            }
        return out

    combined_seasons = []
    if organised['type'] == 'series' and organised['seasons']:
        seasons_out = {
            str(s): ser_season(ep_dict)
            for s, ep_dict in sorted(organised['seasons'].items())
        }
        files_payload = {'seasons': seasons_out}
        for s_num in sorted(organised['seasons'].keys()):
            combined_seasons.append({
                'season':        s_num,
                'label':         f'Season {s_num}' if s_num > 0 else 'Extras',
                'episode_count': len(organised['seasons'][s_num]),
            })
    else:
        files_payload = {
            'movie_files': [doc_to_obj(d) for d in (organised['movie_files'] or [])]
        }

    sample_langs, sample_subs, sample_qual, sample_print = [], '', '', ''
    for doc in all_variants[:5]:
        fn = doc.get('caption') or doc.get('file_name', '')
        if not sample_langs:   sample_langs  = extract_language(fn)
        if not sample_subs:    sample_subs   = extract_subtitles(fn)
        if not sample_qual:    sample_qual   = extract_quality(fn)
        if not sample_print:   sample_print  = extract_print_type(fn)

    return json_resp({
        'ok':               True,
        'content_type':     organised['type'],
        'meta':             meta,
        'caption_title':    primary,
        'file_count':       len(all_variants),
        'combined_seasons': combined_seasons,
        'db_languages':     sample_langs,
        'db_subtitles':     sample_subs,
        'db_quality':       sample_qual,
        'db_print':         sample_print,
        **files_payload,
    })


async def miniapp_html(request):
    import os
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'miniapp.html')
    if os.path.exists(html_path):
        return web.FileResponse(html_path)
    return web.Response(text='miniapp.html not found', status=404)


# ─── Telegram initData validation ─────────────────────────────────────────────

def _validate_init_data(init_data: str, bot_token: str) -> dict | None:
    if not init_data or not bot_token:
        return None
    try:
        parsed = parse_qs(init_data, strict_parsing=True)
        received_hash = parsed.pop('hash', [None])[0]
        if not received_hash:
            return None
        data_check_string = '\n'.join(
            sorted(f'{k}={v[0]}' for k, v in parsed.items())
        )
        secret_key = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
        expected   = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            return None
        user_raw = parsed.get('user', [None])[0]
        if not user_raw:
            return None
        return json.loads(unquote(user_raw))
    except Exception as exc:
        logger.error(f'_validate_init_data exception: {exc}')
        return None


async def miniapp_send_file(request: Request):
    if request.method == 'OPTIONS':
        return cors_preflight()
    if not DB_AVAILABLE:
        return json_resp({'ok': False, 'error': 'server_error'}, 500)

    try:
        body = await request.json()
    except Exception:
        return json_resp({'ok': False, 'error': 'bad_request'}, 400)

    file_id   = (body.get('file_id') or '').strip()
    init_data = (body.get('initData') or '').strip()

    if not file_id:
        return json_resp({'ok': False, 'error': 'bad_request'}, 400)

    user_data = _validate_init_data(init_data, BOT_TOKEN)
    if not user_data:
        return json_resp({'ok': False, 'error': 'Unauthorized'}, 401)

    user_id = user_data.get('id')

    bot = getattr(temp, 'BOT', None)
    if bot is None:
        return json_resp({'ok': False, 'error': 'server_error'}, 500)

    try:
        file_doc = await get_file_details(file_id)
    except Exception:
        return json_resp({'ok': False, 'error': 'server_error'}, 500)

    if not file_doc:
        return json_resp({'ok': False, 'error': 'file_not_found'}, 404)

    try:
        from plugins.miniapp_plugin import _send_file_with_checks

        class _FakeUser:
            id = user_id
            mention = f'<a href="tg://user?id={user_id}">User</a>'

        class _FakeMsg:
            from_user = _FakeUser()
            async def reply_text(self, text, **kw):
                try:
                    await bot.send_message(chat_id=user_id, text=text, **{k: v for k, v in kw.items() if k != 'protect_content'})
                except Exception as e:
                    logger.error(f'reply_text failed: {e}')
            async def reply(self, text, reply_to_message_id=None, **kw):
                try:
                    await bot.send_message(chat_id=user_id, text=text, **kw)
                except Exception as e:
                    logger.error(f'reply failed: {e}')
            async def reply_photo(self, photo, caption='', **kw):
                try:
                    await bot.send_photo(chat_id=user_id, photo=photo, caption=caption, **kw)
                except Exception as e:
                    logger.error(f'reply_photo failed: {e}')
            async def delete(self):
                pass

        asyncio.ensure_future(_send_file_with_checks(bot, _FakeMsg(), user_id, file_id))
        return json_resp({'ok': True})

    except Exception as exc:
        logger.error(f'send_file dispatch exception: {type(exc).__name__}: {exc}', exc_info=True)
        return json_resp({'ok': False, 'error': 'server_error'}, 500)


routes = [
    web.route('GET',     '/miniapp',              miniapp_html),
    web.route('GET',     '/miniapp/health',        miniapp_health),
    web.route('GET',     '/miniapp/recent',        miniapp_recent),
    web.route('GET',     '/miniapp/browse',        miniapp_browse),
    web.route('GET',     '/miniapp/search',        miniapp_search),
    web.route('GET',     '/miniapp/group_details', miniapp_group_details),
    web.route('POST',    '/miniapp/send_file',     miniapp_send_file),
    web.route('OPTIONS', '/miniapp/recent',        miniapp_recent),
    web.route('OPTIONS', '/miniapp/browse',        miniapp_browse),
    web.route('OPTIONS', '/miniapp/search',        miniapp_search),
    web.route('OPTIONS', '/miniapp/group_details', miniapp_group_details),
    web.route('OPTIONS', '/miniapp/send_file',     miniapp_send_file),
]
