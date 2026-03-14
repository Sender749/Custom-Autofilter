"""
miniapp_routes.py — Fully Reconstructed
========================================
Key behaviours
--------------
• NO card cache — every browse hits MongoDB fresh.
• Files ordered by MongoDB $natural (insertion order = newest first).
• mode=random  → $sample aggregation for genuine randomness (refresh button).
• Strict title dedup — a movie/show appears ONCE per page, ever.
• Only cards with a resolved poster are returned.
• Smart caption cleaner removes channel names, quality tags, codecs, emoji,
  language labels, S/E patterns, then picks the longest clean fragment as title.
• TMDB meta cache retained (1 hr) to avoid API hammering.
"""

import re
import json
import time
import hmac
import hashlib
import logging
import asyncio
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, unquote
from aiohttp import web
from aiohttp.web_request import Request

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_executor = ThreadPoolExecutor(max_workers=8)

try:
    from database.ia_filterdb import (
        collection, second_collection,
        get_search_results, get_file_details,
        is_second_db_configured,
    )
    from utils import temp
    from info import TMDB_API_KEY, BOT_TOKEN
    DB_AVAILABLE = True
except ImportError as _ie:
    DB_AVAILABLE = False
    TMDB_API_KEY = ''
    BOT_TOKEN    = ''
    logger.warning(f"DB not available: {_ie}")

# ─── TMDB meta cache ─────────────────────────────────────────────────────────
# Short TTL: 10 minutes. Posters rarely change but we want fresh data quickly.
_META_CACHE: dict = {}
_META_CACHE_TTL   = 600   # 10 minutes

def _mcache_get(key):
    e = _META_CACHE.get(key)
    return e[1] if e and (time.time() - e[0]) < _META_CACHE_TTL else None

def _mcache_set(key, val):
    _META_CACHE[key] = (time.time(), val)


# =============================================================================
#  SMART CAPTION / FILENAME TITLE EXTRACTOR
# =============================================================================

_RX_EMOJI = re.compile(
    "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
    "\U00002500-\U00002BEF]+", flags=re.UNICODE)
_RX_URL   = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)
_RX_CTAG  = re.compile(r'@\w+')
_RX_HTAG  = re.compile(r'#\w+')
_RX_EXT   = re.compile(r'\.(mkv|mp4|avi|mov|flv|wmv|webm|ts|m4v|mpg|mpeg)$', re.IGNORECASE)

# Brackets/parens that contain only technical keywords → strip
_RX_TECH_BRACKET = re.compile(
    r'[\[\(]([^\]\)]*(?:480p|720p|1080p|2160p|4k|hdr|hevc|x264|x265|aac|dts|'
    r'webrip|web.dl|bluray|dvdrip|hdrip|hdtv|esub|hardsub|dubbed|subbed|'
    r'multi|dual|hindi|english|tamil|telugu|kannada|malayalam|bengali|'
    r'mkv|mp4|avi)[^\]\)]*)[\]\)]',
    re.IGNORECASE)

_RX_QUALITY  = re.compile(r'\b(240p|360p|480p|540p|576p|720p|900p|1080p|1440p|2160p|4k|uhd|hd|sd)\b', re.IGNORECASE)
_RX_CODEC    = re.compile(r'\b(x264|x265|h264|h265|hevc|avc|xvid|vp9|av1|aac|ac3|dts|mp3|eac3|ddp|atmos|opus|flac|truehd|10bit|8bit|hdr10\+?|hlg|sdr|dv|dolby)\b', re.IGNORECASE)
_RX_PRINT    = re.compile(r'\b(bluray|blu.?ray|bdrip|bdremux|remux|web.?dl|webrip|webdl|hdrip|hdtv|dvdrip|dvdscr|dvd|cam|telesync|ts|tc|r5|scr|proper|repack|retail|limited|extended|unrated|theatrical|directors?.?cut|collectors?|sample|trailer|extras?|bonus)\b', re.IGNORECASE)
_RX_LANG     = re.compile(r'\b(hindi|english|tamil|telugu|kannada|malayalam|bengali|punjabi|marathi|gujarati|odia|urdu|arabic|french|german|spanish|italian|korean|japanese|chinese|russian|portuguese|turkish|dual.?audio|multi.?audio|multi|dubbed|subbed|esub|hardsub|hin|eng|tam|tel|kan|mal|ben|pun|jpn|kor|chi|ara)\b', re.IGNORECASE)
_RX_BOTNAME  = re.compile(r'\b(navex|siliconbotz|tgmovies|moviezwap|filmywap|bolly4u|moviesbaba|khatrimaza|jalshamoviez|7starhd|filmyhit|1337x|yts|rarbg|ettv|eztv|mkvcage|telegram|t\.me)\b', re.IGNORECASE)
_RX_YEAR     = re.compile(r'\b(19[4-9]\d|20[0-3]\d)\b')
_RX_SE       = re.compile(r'\b[Ss](\d{1,2})\s*[Ee](\d{1,2})\b')
_RX_S_ONLY   = re.compile(r'\b[Ss]eason\s*(\d{1,2})\b|\b[Ss](\d{1,2})\b(?!\s*[Ee]\d)')
_RX_E_ONLY   = re.compile(r'\b[Ee]p(?:isode)?\s*(\d{1,3})\b|\b[Ee](\d{1,3})\b')
_RX_SEP      = re.compile(r'[\|\-–—•·~:]+')
_RX_PUNC     = re.compile(r'[_\+\.\[\](){}<>!@#$%^&*=,;\'\"\\\/]+')
_RX_LEAD_NUM = re.compile(r'^\d+[\s\.\-]+')
_RX_SPACES   = re.compile(r'\s{2,}')

# Fancy Unicode letter normaliser (small caps, superscripts, etc.)
try:
    _FANCY_SRC = 'ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘQʀsᴛᴜᴠᴡxʏᴢᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿˢᵀᵁᵛᵂ'
    _FANCY_DST = 'abcdefghijklmnopqrstuvwxyzABDEGHIJKLMNOPRSTUVW'
    # Build only as many pairs as the shorter string
    _min = min(len(_FANCY_SRC), len(_FANCY_DST))
    _FANCY_MAP = str.maketrans(_FANCY_SRC[:_min], _FANCY_DST[:_min])
except Exception:
    _FANCY_MAP = {}


def smart_clean_title(raw: str) -> str:
    """Return the clean title from a messy Telegram caption or filename."""
    if not raw:
        return ''
    t = raw

    # Normalise fancy unicode chars
    if _FANCY_MAP:
        t = t.translate(_FANCY_MAP)

    # Remove noise
    t = _RX_EMOJI.sub(' ', t)
    t = _RX_URL.sub(' ', t)
    t = _RX_CTAG.sub(' ', t)
    t = _RX_HTAG.sub(' ', t)
    t = _RX_BOTNAME.sub(' ', t)
    t = _RX_EXT.sub('', t)
    t = _RX_TECH_BRACKET.sub(' ', t)

    # Strip tech tokens
    t = _RX_QUALITY.sub(' ', t)
    t = _RX_CODEC.sub(' ', t)
    t = _RX_PRINT.sub(' ', t)
    t = _RX_LANG.sub(' ', t)
    t = _RX_YEAR.sub(' ', t)
    t = _RX_SE.sub(' ', t)
    t = _RX_S_ONLY.sub(' ', t)
    t = _RX_E_ONLY.sub(' ', t)

    # Split on separators, keep longest clean fragment
    parts = _RX_SEP.split(t)
    cleaned = []
    for p in parts:
        p = _RX_PUNC.sub(' ', p)
        p = _RX_SPACES.sub(' ', p).strip()
        if len(p) >= 2:
            cleaned.append(p)

    if not cleaned:
        return ''

    title = max(cleaned, key=len)
    title = _RX_LEAD_NUM.sub('', title)
    title = _RX_SPACES.sub(' ', title).strip()
    return title


def extract_year(text: str) -> str:
    m = _RX_YEAR.search(text)
    return m.group(0) if m else ''


def extract_season_ep(text: str):
    m = _RX_SE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    sm = _RX_S_ONLY.search(text)
    em = _RX_E_ONLY.search(text)
    s  = int((sm.group(1) or sm.group(2) or 0)) if sm else 0
    e  = int((em.group(1) or em.group(2) or 0)) if em else 0
    return s, e


def title_key(text: str) -> str:
    """Normalised dedup key: lowercase alphanum, no spaces."""
    t = smart_clean_title(text).lower()
    return re.sub(r'[^a-z0-9\u0080-\uffff]', '', t)


# ─── Quality helpers ──────────────────────────────────────────────────────────
_QUAL_MAP = [('2160p','2160p'),('4k','4K'),('uhd','4K'),('1080p','1080p'),('720p','720p'),('480p','480p'),('360p','360p'),('cam','CAM')]
_PRINT_MAP= [('bluray','BluRay'),('bdrip','BDRip'),('remux','Remux'),('web-dl','WEB-DL'),('webdl','WEB-DL'),('webrip','WEBRip'),('hdrip','HDRip'),('hdtv','HDTV'),('dvdrip','DVDRip'),('cam','CAM'),('ts','TS')]
_LANG_MAP = {'hindi':'Hindi','english':'English','tamil':'Tamil','telugu':'Telugu','kannada':'Kannada','malayalam':'Malayalam','bengali':'Bengali','punjabi':'Punjabi','dual audio':'Dual Audio','multi audio':'Multi Audio','dual':'Dual Audio','multi':'Multi Audio'}
_SUB_MAP  = [('esub','English'),('hardsub','Hardcoded'),('subbed','Yes')]
_QSCORE   = {'2160p':100,'4k':100,'uhd':100,'1080p':80,'720p':60,'480p':30,'360p':20,'cam':1,'ts':1}

def _find(text, mapping):
    t = text.lower()
    for k, v in mapping:
        if k in t: return v
    return ''

def extract_quality(t):  return _find(t, _QUAL_MAP)
def extract_print(t):    return _find(t, _PRINT_MAP)
def quality_score(t):
    tl = t.lower()
    for k, v in _QSCORE.items():
        if k in tl: return v
    return 40

def extract_languages(text):
    t = text.lower(); langs = []
    for k, v in _LANG_MAP.items():
        if k in t and v not in langs: langs.append(v)
    if 'Dual Audio' in langs or 'Multi Audio' in langs:
        langs = [l for l in langs if l not in ('Hindi','English','Tamil','Telugu','Kannada','Malayalam','Bengali','Punjabi')]
    return langs[:3]

def extract_subtitles(text):
    t = text.lower()
    for k, v in _SUB_MAP:
        if k in t: return v
    return ''

def _fmt_size(size):
    units = ['B','KB','MB','GB','TB']
    size  = float(size)
    for u in units:
        if size < 1024: return f'{size:.2f} {u}'
        size /= 1024
    return f'{size:.2f} PB'


# ─── Content-type detector ────────────────────────────────────────────────────
_ANIME_KW  = re.compile(r'\b(anime|hentai|ecchi|ova|oad|ona|manhwa|manhua|donghua|shonen|seinen|shoujo|josei|isekai|mecha|yaoi|yuri)\b|[\u3040-\u30FF\u4E00-\u9FFF]', re.IGNORECASE)
_SERIES_KW = re.compile(r'\b(web\s*series|mini\s*series|limited\s*series|complete\s*series|season\s*\d+|s\d{1,2}\s*complete)\b', re.IGNORECASE)

def detect_type(text, tmdb_type=None, tmdb_genres=None, origin_country=None, orig_lang=None):
    if _ANIME_KW.search(text): return 'anime'
    gl = ' '.join(tmdb_genres or []).lower()
    if 'animation' in gl or 'anime' in gl: return 'anime'
    if tmdb_type == 'tv' and orig_lang == 'ja': return 'anime'
    if tmdb_type == 'tv': return 'series'
    s, _ = extract_season_ep(text)
    if s > 0: return 'series'
    if _SERIES_KW.search(text): return 'series'
    return 'movie'


# ─── doc_to_obj ───────────────────────────────────────────────────────────────
def doc_to_obj(doc):
    raw = (doc.get('caption') or '').strip() or doc.get('file_name', '')
    s, e = extract_season_ep(raw)
    return {
        'id':         str(doc['_id']),
        'caption':    raw,
        'raw_name':   doc.get('file_name', ''),
        'size':       _fmt_size(doc.get('file_size', 0)),
        'quality':    extract_quality(raw),
        'print_type': extract_print(raw),
        'languages':  extract_languages(raw),
        'subtitles':  extract_subtitles(raw),
        'season':     s,
        'episode':    e,
    }


# ─── Async DB helpers ─────────────────────────────────────────────────────────
async def _run_sync(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(_executor, fn, *args)


def _sync_fetch_recent(content_type: str, limit: int, skip: int = 0):
    """
    Fetch docs newest-first using MongoDB natural insertion order.
    Uses a generous scan window so filtering by type still yields `limit` results.
    A small random offset is applied on page 0 to add variety on repeated loads.
    """
    try:
        # On page 0 add a small random skip so repeated opens don't always show
        # the exact same set (still shows recent files, just with minor variation)
        if skip == 0:
            rand_offset = random.randint(0, 20)
        else:
            rand_offset = 0

        window = max(limit * 20, 800)
        docs   = list(
            collection.find({}).sort([('$natural', -1)])
            .skip(skip + rand_offset).limit(window)
        )
        if is_second_db_configured() and second_collection is not None:
            extra = list(
                second_collection.find({}).sort([('$natural', -1)])
                .skip(max(0, skip - 5000)).limit(window)
            )
            seen  = {d['_id'] for d in docs}
            docs += [d for d in extra if d['_id'] not in seen]

        def _cls(d):
            stored = d.get('category')
            if stored in ('movie','series','anime'): return stored
            return detect_type(d.get('caption') or d.get('file_name',''))

        return [d for d in docs if _cls(d) == content_type]
    except Exception as exc:
        logger.error(f'_sync_fetch_recent: {exc}'); return []


def _sync_fetch_random(content_type: str, limit: int):
    """Return a random sample using MongoDB $sample."""
    try:
        window   = limit * 20
        pipeline = [{'$sample': {'size': window}}]
        docs     = list(collection.aggregate(pipeline))
        if is_second_db_configured() and second_collection is not None:
            extra = list(second_collection.aggregate(pipeline))
            seen  = {d['_id'] for d in docs}
            docs += [d for d in extra if d['_id'] not in seen]

        def _cls(d):
            stored = d.get('category')
            if stored in ('movie','series','anime'): return stored
            return detect_type(d.get('caption') or d.get('file_name',''))

        filtered = [d for d in docs if _cls(d) == content_type]
        random.shuffle(filtered)
        return filtered
    except Exception as exc:
        logger.error(f'_sync_fetch_random: {exc}'); return []


def _sync_search_raw(q: str, limit: int = 300):
    try:
        word    = q.split()[0] if q else '.'
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        results = []; seen = set()
        cols    = [collection]
        if is_second_db_configured() and second_collection is not None:
            cols.append(second_collection)
        for col in cols:
            for doc in col.find({'$or': [{'file_name': pattern}, {'caption': pattern}]}).sort([('$natural',-1)]).limit(limit * 6):
                if doc['_id'] not in seen:
                    seen.add(doc['_id']); results.append(doc)
        return results
    except Exception as exc:
        logger.error(f'_sync_search_raw: {exc}'); return []


def _group_docs(docs):
    """Group by clean title key. First doc seen = representative (newest)."""
    groups = {}
    for doc in docs:
        raw = (doc.get('caption') or '').strip() or doc.get('file_name','')
        key = title_key(raw)
        if not key or len(key) < 2: continue
        if key not in groups:
            groups[key] = {'rep': doc, 'files': [doc],
                           'title': smart_clean_title(raw), 'year': extract_year(raw)}
        else:
            groups[key]['files'].append(doc)
    return groups


def _organise_files(docs):
    has_s = any(extract_season_ep(d.get('caption') or d.get('file_name',''))[0] > 0 for d in docs)
    if not has_s:
        return {'type':'movie','movie_files':sorted(docs,key=lambda d:-quality_score(d.get('caption') or d.get('file_name',''))),'seasons':None}
    tree = defaultdict(lambda: defaultdict(list))
    for doc in docs:
        raw = doc.get('caption') or doc.get('file_name','')
        s,e = extract_season_ep(raw)
        tree[s][e].append(doc)
    result = {}
    for s_num in sorted(tree):
        result[s_num] = {}
        for e_num in sorted(tree[s_num]):
            result[s_num][e_num] = sorted(tree[s_num][e_num],key=lambda d:-quality_score(d.get('caption') or d.get('file_name','')))
    return {'type':'series','movie_files':None,'seasons':result}


# ─── TMDB / IMDB fetchers ─────────────────────────────────────────────────────
TMDB_IMG  = 'https://image.tmdb.org/t/p/w500'
TMDB_BACK = 'https://image.tmdb.org/t/p/w1280'

async def _tmdb_fetch(title: str, year: str = '') -> dict | None:
    if not TMDB_API_KEY or not title: return None
    try:
        import aiohttp, socket
        to  = aiohttp.ClientTimeout(total=10, connect=5)
        con = aiohttp.TCPConnector(family=socket.AF_INET)
        async with aiohttp.ClientSession(connector=con, timeout=to) as sess:
            results = []
            for params in [
                {'api_key': TMDB_API_KEY, 'query': title, 'page': 1, **(({'year': year}) if year else {})},
                {'api_key': TMDB_API_KEY, 'query': title, 'page': 1},
            ]:
                async with sess.get('https://api.themoviedb.org/3/search/multi', params=params) as r:
                    if r.status == 200:
                        data    = await r.json()
                        results = [x for x in data.get('results',[]) if x.get('media_type') != 'person']
                        if results: break
            if not results: return None

            tl   = title.lower()
            item = next((x for x in results if (x.get('title') or x.get('name','')).lower() == tl), results[0])
            mt   = item.get('media_type','movie')
            iid  = item.get('id')

            async with sess.get(f'https://api.themoviedb.org/3/{mt}/{iid}',
                                params={'api_key': TMDB_API_KEY, 'append_to_response': 'credits'}) as dr:
                detail = await dr.json() if dr.status == 200 else {}

        src    = detail or item
        genres = [g['name'] for g in src.get('genres',[])]
        cr     = detail.get('credits',{})
        cast   = [c['name'] for c in cr.get('cast',[])[:6]]
        dirs   = [c['name'] for c in cr.get('crew',[]) if c.get('job') == 'Director'][:2]
        rt     = detail.get('runtime') or ((detail.get('episode_run_time') or [None])[0])
        return {
            'title':    src.get('title') or src.get('name', title),
            'year':     (src.get('release_date') or src.get('first_air_date') or '')[:4],
            'poster':   f"{TMDB_IMG}{src['poster_path']}"   if src.get('poster_path')   else None,
            'backdrop': f"{TMDB_BACK}{src['backdrop_path']}" if src.get('backdrop_path') else None,
            'rating':   round(float(src.get('vote_average',0) or 0), 1),
            'plot':     src.get('overview',''),
            'genres':   genres, 'cast': cast, 'director': ', '.join(dirs),
            'runtime':  f'{rt} min' if rt else '',
            'type':     mt,
            'seasons':  detail.get('number_of_seasons'),
            'episodes': detail.get('number_of_episodes'),
            'tagline':  detail.get('tagline',''),
            'status':   detail.get('status',''),
            'imdb_id':  detail.get('imdb_id',''),
            'origin_country':    (detail or src).get('origin_country',[]),
            'original_language': (detail or src).get('original_language',''),
        }
    except Exception as exc:
        logger.warning(f'TMDB "{title}": {type(exc).__name__}: {exc}'); return None


async def _imdb_fetch(title: str, year: str = '') -> dict | None:
    try:
        from imdb import Cinemagoer
        ia   = Cinemagoer()
        hits = await asyncio.wait_for(_run_sync(lambda: ia.search_movie(title, results=8)), timeout=6)
        if not hits: return None
        if year:
            filtered = [h for h in hits if str(h.get('year','')) == year]
            hits = filtered or hits
        typed = [h for h in hits if h.get('kind') in ('movie','tv series')]
        pick  = (typed or hits)[0]
        mv    = await asyncio.wait_for(_run_sync(lambda: ia.get_movie(pick.movieID)), timeout=8)
        rp    = mv.get('plot',[]); plot = ''
        if rp: plot = rp[0].split('::')[0] if '::' in rp[0] else rp[0]
        cast  = [str(c) for c in (mv.get('cast') or [])[:6]]
        drs   = mv.get('directors') or mv.get('director') or []
        dirs  = [str(d) for d in (drs[:2] if isinstance(drs,list) else [])]
        rt    = (mv.get('runtimes') or [''])[0]
        genres= mv.get('genres',[])[:4]
        poster= mv.get('full-size cover url') or mv.get('cover url')
        mt    = 'tv' if mv.get('kind') == 'tv series' else 'movie'
        return {
            'title': mv.get('title',title), 'year': str(mv.get('year',year or '')),
            'poster': poster, 'backdrop': None,
            'rating': float(mv.get('rating',0) or 0), 'plot': plot[:600],
            'genres': genres if isinstance(genres,list) else [], 'cast': cast,
            'director': ', '.join(dirs), 'runtime': f'{rt} min' if rt else '',
            'type': mt, 'seasons': mv.get('number of seasons'), 'episodes': None,
            'tagline': '', 'status': '', 'imdb_id': f"tt{pick.movieID}",
        }
    except Exception as exc:
        logger.warning(f'IMDB "{title}": {type(exc).__name__}: {exc}'); return None


async def _get_meta(title: str, year: str = '') -> dict | None:
    if not title: return None
    ck     = f'{title.lower()}|{year}'
    cached = _mcache_get(ck)
    if cached is not None: return cached
    tmdb = await _tmdb_fetch(title, year)
    if tmdb and tmdb.get('poster'):
        _mcache_set(ck, tmdb); return tmdb
    imdb = await _imdb_fetch(title, year)
    if imdb:
        if tmdb:
            imdb['backdrop'] = imdb.get('backdrop') or tmdb.get('backdrop')
            imdb['genres']   = imdb.get('genres') or tmdb.get('genres',[])
        _mcache_set(ck, imdb); return imdb
    _mcache_set(ck, tmdb); return tmdb


# ─── Card builder (requires poster) ──────────────────────────────────────────
async def _build_card(grp: dict) -> dict | None:
    try:
        rep   = grp['rep']
        raw   = (rep.get('caption') or '').strip() or rep.get('file_name','')
        title = grp['title'] or smart_clean_title(raw)
        if not title or len(title) < 2: return None
        year  = grp['year'] or extract_year(raw)
        meta  = await _get_meta(title, year)
        if not meta or not meta.get('poster'): return None   # ← no poster = skip

        ctype = detect_type(raw,
                            tmdb_type=meta.get('type'),
                            tmdb_genres=meta.get('genres',[]),
                            origin_country=meta.get('origin_country',[]),
                            orig_lang=meta.get('original_language',''))
        return {
            'group_title': title,
            'id':          str(rep['_id']),
            'name':        meta.get('title') or title,
            'year':        meta.get('year') or year,
            'poster':      meta.get('poster'),
            'backdrop':    meta.get('backdrop'),
            'rating':      meta.get('rating'),
            'genres':      (meta.get('genres') or [])[:3],
            'type':        ctype,
            'file_count':  len(grp['files']),
        }
    except Exception as exc:
        logger.error(f'_build_card: {exc}'); return None


# ─── CORS / helpers ───────────────────────────────────────────────────────────
CORS = {
    'Access-Control-Allow-Origin':  '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, X-Telegram-Init-Data',
}

def json_resp(data, status=200):
    return web.Response(text=json.dumps(data, default=str), status=status,
                        content_type='application/json', headers=CORS)

def cors_preflight(): return web.Response(headers=CORS)


# ─── Route handlers ───────────────────────────────────────────────────────────

async def miniapp_health(request):
    return json_resp({'ok': True, 'db': DB_AVAILABLE, 'tmdb_key': bool(TMDB_API_KEY)})


async def miniapp_browse(request):
    """
    GET /miniapp/browse?type=movies|series|anime&page=0&limit=24&mode=recent|random

    mode=recent (default) — newest files first (MongoDB natural insertion order).
    mode=random            — MongoDB $sample gives a genuinely random set.
                             Meta cache is bypassed so posters are re-fetched fresh.

    • No card cache — always reads fresh docs from MongoDB.
    • Deduplication by title: one card per unique movie/show.
    • Only cards with a resolved poster are returned.
    """
    if request.method == 'OPTIONS': return cors_preflight()
    if not DB_AVAILABLE: return json_resp({'ok': False, 'error': 'DB not available'}, 500)

    try:
        raw_type = request.rel_url.query.get('type','movies').lower().rstrip('s')
        content_type = {'movie':'movie','movies':'movie','serie':'series','series':'series','anime':'anime'}.get(raw_type,'movie')
        page  = max(0, int(request.rel_url.query.get('page', 0)))
        limit = min(int(request.rel_url.query.get('limit', 24)), 48)
        mode  = request.rel_url.query.get('mode', 'recent')
    except (ValueError, TypeError):
        page, limit, content_type, mode = 0, 24, 'movie', 'recent'

    is_random = (mode == 'random')

    if is_random:
        # Wipe meta cache for these requests so different posters are actually fetched
        _META_CACHE.clear()
        raw_docs = await _run_sync(_sync_fetch_random, content_type, limit * 6)
    else:
        raw_docs = await _run_sync(_sync_fetch_recent, content_type, limit, page * limit * 8)

    groups        = _group_docs(raw_docs)
    target_groups = list(groups.values())[:limit * 4]

    # Shuffle groups on random mode so even the title resolution order varies
    if is_random:
        random.shuffle(target_groups)

    cards_raw = await asyncio.gather(*[_build_card(g) for g in target_groups], return_exceptions=True)

    seen_posters = set(); seen_titles = set(); results = []
    for c in cards_raw:
        if not isinstance(c, dict): continue
        p = c.get('poster','')
        n = (c.get('name') or c.get('group_title','')).lower().strip()
        # Deduplicate by BOTH poster URL and title name
        if p in seen_posters or n in seen_titles: continue
        if p: seen_posters.add(p)
        if n: seen_titles.add(n)
        results.append(c)
        if len(results) >= limit: break

    return json_resp({'ok': True, 'results': results, 'count': len(results),
                      'page': page, 'mode': mode, 'has_more': len(groups) > limit})


async def miniapp_search(request):
    if request.method == 'OPTIONS': return cors_preflight()
    if not DB_AVAILABLE: return json_resp({'ok': False, 'error': 'DB not available'}, 500)

    q = request.rel_url.query.get('q','').strip()
    if not q: return json_resp({'ok': False, 'error': "Missing 'q'"}, 400)

    try:
        files, _, total = await get_search_results(q, max_results=200)
    except Exception:
        files = await _run_sync(_sync_search_raw, q, 200); total = len(files)

    if not files:
        cq = smart_clean_title(q)
        if cq and cq.lower() != q.lower():
            try:
                files, _, total = await get_search_results(cq, max_results=200)
            except Exception:
                pass

    groups    = _group_docs(files)
    targets   = list(groups.values())[:60]
    cards_raw = await asyncio.gather(*[_build_card(g) for g in targets], return_exceptions=True)

    seen = set(); results = []
    for c in cards_raw:
        if not isinstance(c, dict): continue
        p = c.get('poster','')
        if p in seen: continue
        seen.add(p); results.append(c)

    return json_resp({'ok': True, 'results': results, 'total': len(results)})


async def miniapp_group_details(request):
    if request.method == 'OPTIONS': return cors_preflight()
    if not DB_AVAILABLE: return json_resp({'ok': False, 'error': 'DB not available'}, 500)

    file_id = request.rel_url.query.get('id','').strip()
    if not file_id: return json_resp({'ok': False, 'error': "Missing 'id'"}, 400)

    try:
        rep = await get_file_details(file_id)
    except Exception:
        return json_resp({'ok': False, 'error': 'DB lookup failed'}, 500)

    if not rep: return json_resp({'ok': False, 'error': 'Not found'}, 404)

    raw   = (rep.get('caption','').strip()) or rep.get('file_name','')
    title = smart_clean_title(raw)
    year  = extract_year(raw)
    key   = title_key(raw)

    try:
        raw_files = await _run_sync(_sync_search_raw, title.split()[0] if title else '', 300)
    except Exception:
        raw_files = [rep]

    groups      = _group_docs(raw_files)
    grp         = groups.get(key, {'rep':rep,'files':[rep],'title':title,'year':year})
    all_variants= grp['files'] or [rep]

    meta      = await _get_meta(title, year)
    organised = _organise_files(all_variants)

    def ser_season(ep_dict):
        return {str(ep): {'label': f'Episode {ep}' if ep > 0 else 'Season Pack',
                           'files': [doc_to_obj(d) for d in sorted(docs,key=lambda d:-quality_score(d.get('caption') or d.get('file_name','')))]}
                for ep, docs in sorted(ep_dict.items())}

    combined_seasons = []
    if organised['type'] == 'series' and organised['seasons']:
        seasons_out   = {str(s): ser_season(ep_d) for s, ep_d in sorted(organised['seasons'].items())}
        files_payload = {'seasons': seasons_out}
        for s_num in sorted(organised['seasons'].keys()):
            combined_seasons.append({'season': s_num,
                                     'label': f'Season {s_num}' if s_num > 0 else 'Extras',
                                     'episode_count': len(organised['seasons'][s_num])})
    else:
        files_payload = {'movie_files': [doc_to_obj(d) for d in (organised['movie_files'] or [])]}

    sample_langs,sample_subs,sample_qual,sample_print = [],[],[],''
    for doc in all_variants[:5]:
        fn = doc.get('caption') or doc.get('file_name','')
        if not sample_langs:  sample_langs  = extract_languages(fn)
        if not sample_subs:   sample_subs   = extract_subtitles(fn)
        if not sample_qual:   sample_qual   = extract_quality(fn)
        if not sample_print:  sample_print  = extract_print(fn)

    return json_resp({'ok': True, 'content_type': organised['type'], 'meta': meta,
                      'caption_title': raw, 'file_count': len(all_variants),
                      'combined_seasons': combined_seasons, 'db_languages': sample_langs,
                      'db_subtitles': sample_subs, 'db_quality': sample_qual,
                      'db_print': sample_print, **files_payload})


async def miniapp_poster(request):
    """GET /miniapp/poster?title=<title>&year=<year>"""
    if request.method == 'OPTIONS': return cors_preflight()

    title   = request.rel_url.query.get('title','').strip()
    year    = request.rel_url.query.get('year','').strip()
    file_id = request.rel_url.query.get('id','').strip()

    if not title and file_id and DB_AVAILABLE:
        try:
            doc = await get_file_details(file_id)
            if doc:
                raw   = (doc.get('caption') or '').strip() or doc.get('file_name','')
                title = smart_clean_title(raw)
                year  = year or extract_year(raw)
        except Exception: pass

    if not title: return json_resp({'ok': False, 'error': "Missing 'title'"}, 400)

    meta = await _get_meta(title, year)
    if not meta: return json_resp({'ok': False, 'error': 'No metadata'}, 404)

    return json_resp({'ok': True, 'meta': {
        'title': meta.get('title',title), 'year': meta.get('year',year),
        'poster': meta.get('poster'), 'backdrop': meta.get('backdrop'),
        'rating': meta.get('rating'), 'plot': meta.get('plot',''),
        'genres': meta.get('genres',[]), 'cast': meta.get('cast',[]),
        'director': meta.get('director',''), 'runtime': meta.get('runtime',''),
        'tagline': meta.get('tagline',''), 'status': meta.get('status',''),
        'type': meta.get('type','movie'),
    }})


async def miniapp_html(request):
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'miniapp.html')
    return web.FileResponse(path) if os.path.exists(path) else web.Response(text='miniapp.html not found', status=404)


# ─── Telegram initData validation ─────────────────────────────────────────────
def _validate_init_data(init_data: str, bot_token: str) -> dict | None:
    if not init_data or not bot_token: return None
    try:
        parsed   = parse_qs(init_data, strict_parsing=True)
        recv     = parsed.pop('hash',[None])[0]
        if not recv: return None
        dcs      = '\n'.join(sorted(f'{k}={v[0]}' for k,v in parsed.items()))
        secret   = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, recv): return None
        ur       = parsed.get('user',[None])[0]
        return json.loads(unquote(ur)) if ur else None
    except Exception as exc:
        logger.error(f'_validate_init_data: {exc}'); return None


async def miniapp_send_file(request: Request):
    if request.method == 'OPTIONS': return cors_preflight()
    if not DB_AVAILABLE: return json_resp({'ok': False, 'error': 'server_error'}, 500)

    try:   body = await request.json()
    except Exception: return json_resp({'ok': False, 'error': 'bad_request'}, 400)

    file_id   = (body.get('file_id') or '').strip()
    init_data = (body.get('initData') or '').strip()
    if not file_id: return json_resp({'ok': False, 'error': 'bad_request'}, 400)

    user_data = _validate_init_data(init_data, BOT_TOKEN)
    if not user_data: return json_resp({'ok': False, 'error': 'Unauthorized'}, 401)

    user_id = user_data.get('id')
    bot     = getattr(temp, 'BOT', None)
    if bot is None: return json_resp({'ok': False, 'error': 'server_error'}, 500)

    try:   file_doc = await get_file_details(file_id)
    except Exception: return json_resp({'ok': False, 'error': 'server_error'}, 500)

    if not file_doc: return json_resp({'ok': False, 'error': 'file_not_found'}, 404)

    try:
        from plugins.miniapp_plugin import _send_file_with_checks
        class _FU:
            id = user_id
            mention = f'<a href="tg://user?id={user_id}">User</a>'
        class _FM:
            from_user = _FU()
            async def reply_text(self, text, **kw):
                try: await bot.send_message(user_id, text, **{k:v for k,v in kw.items() if k!='protect_content'})
                except Exception as e: logger.error(f'reply_text: {e}')
            async def reply(self, text, **kw):
                try: await bot.send_message(user_id, text, **kw)
                except Exception as e: logger.error(f'reply: {e}')
            async def reply_photo(self, photo, caption='', **kw):
                try: await bot.send_photo(user_id, photo=photo, caption=caption, **kw)
                except Exception as e: logger.error(f'reply_photo: {e}')
            async def delete(self): pass

        asyncio.ensure_future(_send_file_with_checks(bot, _FM(), user_id, file_id))
        return json_resp({'ok': True})
    except Exception as exc:
        logger.error(f'send_file: {type(exc).__name__}: {exc}', exc_info=True)
        return json_resp({'ok': False, 'error': 'server_error'}, 500)


# ─── Route table ──────────────────────────────────────────────────────────────
routes = [
    web.route('GET',     '/miniapp',               miniapp_html),
    web.route('GET',     '/miniapp/health',        miniapp_health),
    web.route('GET',     '/miniapp/browse',        miniapp_browse),
    web.route('GET',     '/miniapp/search',        miniapp_search),
    web.route('GET',     '/miniapp/group_details', miniapp_group_details),
    web.route('GET',     '/miniapp/poster',        miniapp_poster),
    web.route('POST',    '/miniapp/send_file',     miniapp_send_file),
    web.route('OPTIONS', '/miniapp/browse',        miniapp_browse),
    web.route('OPTIONS', '/miniapp/search',        miniapp_search),
    web.route('OPTIONS', '/miniapp/group_details', miniapp_group_details),
    web.route('OPTIONS', '/miniapp/poster',        miniapp_poster),
    web.route('OPTIONS', '/miniapp/send_file',     miniapp_send_file),
]
