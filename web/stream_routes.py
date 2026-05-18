import math
import re
import json
import asyncio
import os
import random
import time
import secrets
import mimetypes
from collections import defaultdict
from aiohttp import web
from pyrogram import enums
from info import BIN_CHANNEL
from utils import temp
from web.utils.custom_dl import TGCustomYield, chunk_size, offset_fix
from web.utils.render_template import media_watch

routes = web.RouteTableDef()

# ─── EXISTING STREAM ROUTES ──────────────────────────────────────────────────

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.Response(
        text='<h1 align="center"><a href="https://t.me/Navex_Movies"><b>Movie Zone</b></a></h1>',
        content_type='text/html'
    )

@routes.get("/watch/{message_id}")
async def watch_handler(request):
    try:
        message_id = int(request.match_info['message_id'])
        return web.Response(text=await media_watch(message_id), content_type='text/html')
    except Exception:
        return web.Response(text="<h1>Something went wrong</h1>", content_type='text/html')

@routes.get("/download/{message_id}")
async def download_handler(request):
    try:
        message_id = int(request.match_info['message_id'])
        return await media_download(request, message_id)
    except Exception:
        return web.Response(text="<h1>Something went wrong</h1>", content_type='text/html')

async def media_download(request, message_id: int):
    range_header = request.headers.get('Range', 0)
    media_msg = await temp.BOT.get_messages(BIN_CHANNEL, message_id)
    media = getattr(media_msg, media_msg.media.value, None)
    file_size = media.file_size
    if range_header:
        from_bytes, until_bytes = range_header.replace('bytes=', '').split('-')
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes else file_size - 1
    else:
        from_bytes = request.http_range.start or 0
        until_bytes = request.http_range.stop or file_size - 1
    req_length = until_bytes - from_bytes
    new_chunk_size = await chunk_size(req_length)
    offset = await offset_fix(from_bytes, new_chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = (until_bytes % new_chunk_size) + 1
    part_count = math.ceil(req_length / new_chunk_size)
    body = TGCustomYield().yield_file(media_msg, offset, first_part_cut, last_part_cut,
                                      part_count, new_chunk_size)
    file_name = media.file_name if media.file_name else f"{secrets.token_hex(2)}.jpeg"
    mime_type = media.mime_type if media.mime_type else f"{mimetypes.guess_type(file_name)}"
    return_resp = web.Response(
        status=206 if range_header else 200,
        body=body,
        headers={
            "Content-Type": mime_type,
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Accept-Ranges": "bytes",
        }
    )
    if return_resp.status == 200:
        return_resp.headers.add("Content-Length", str(file_size))
    return return_resp

# ─── MINI APP  ───────────────────────────────────────────────────────────────
import logging as _log
_logger = _log.getLogger(__name__)

try:
    from database.ia_filterdb import (
        collection as _col, second_collection as _scol,
        get_search_results as _search, get_file_details as _get_file,
        is_second_db_configured as _is2db,
    )
    from utils import get_size as _get_size
    from info import TMDB_API_KEY as _TMDB_KEY
    _DB_OK = True
except Exception as _e:
    _DB_OK = False
    _logger.warning(f"miniapp DB imports failed: {_e}")

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
}

def _jresp(data, status=200):
    return web.Response(
        text=json.dumps(data, default=str),
        status=status,
        content_type="application/json",
        headers=_CORS,
    )

# ── filename / caption helpers ────────────────────────────────────────────────

_EXT  = re.compile(r'\.(mkv|mp4|avi|mov|flv|wmv|webm|ts|m4v)$', re.IGNORECASE)
_PUNC = re.compile(r'[@\[\]()\-_\+\.]+')
_SPC  = re.compile(r'\s{2,}')
_YEAR = re.compile(r'\b(19[5-9]\d|20[0-4]\d)\b')
_SE   = re.compile(r'\b[Ss](\d{1,2})[Ee](\d{1,2})\b')
_S_RE = re.compile(r'\b[Ss](\d{1,2})\b')

_JUNK = re.compile(
    r'\b(480p|720p|1080p|2160p|4k|uhd|hdr10?|hdrip|bluray|bdrip|remux|'
    r'web[\-\s]?dl|webrip|webdl|hdtv|dvdrip|dvdscr|cam|telesync|ts|'
    r'x264|x265|hevc|avc|aac|ac3|mp3|ddp|dts|atmos|dolby|'
    r'proper|repack|extended|unrated|amzn|nf|hulu|voot|zee5|sony|'
    r'encoded|by|www|tg|@\w+)\b',
    re.IGNORECASE,
)
_LANG_STRIP = re.compile(
    r'\b(hindi|english|tamil|telugu|kannada|malayalam|bengali|punjabi|marathi|'
    r'dual[\s\-]?audio|multi[\s\-]?audio|dubbed|subbed|hardsub|esub|hin|eng|tam|tel)\b',
    re.IGNORECASE,
)

_QUAL_SCORE = {'2160p': 100, '4k': 100, 'uhd': 100, '1080p': 80,
               '720p': 60, '480p': 30, 'cam': 1}

_QUAL_MAP = [
    ('2160p','2160p'),('4k','4K'),('uhd','4K'),
    ('1080p','1080p'),('720p','720p'),('480p','480p'),('360p','360p'),('cam','CAM')
]

_PRINT_MAP = [
    ('bluray','BluRay'),('blu-ray','BluRay'),('bdrip','BDRip'),('remux','Remux'),
    ('web-dl','WEB-DL'),('webdl','WEB-DL'),('webrip','WEBRip'),
    ('hdrip','HDRip'),('hdtv','HDTV'),('dvdrip','DVDRip'),('dvdscr','DVDScr'),
    ('telesync','TS'),('cam','CAM'),
]

_LANG_MAP = {
    'hindi':'Hindi','english':'English','tamil':'Tamil','telugu':'Telugu',
    'kannada':'Kannada','malayalam':'Malayalam','bengali':'Bengali','punjabi':'Punjabi',
    'marathi':'Marathi',
    'dual audio':'Dual Audio','dual':'Dual Audio',
    'multi audio':'Multi Audio','multi':'Multi Audio',
}

_SUB_MAP = [
    ('esub','English'),('hardsub','Hardcoded'),('subbed','Yes'),('subtitle','Yes'),
]

# In-memory meta cache: title|year -> (timestamp, result)
_META_CACHE = {}
_META_CACHE_TTL = 3600  # 1 hour


def _extract_year(text):
    h = _YEAR.findall(text)
    return h[0] if h else ''

def _extract_quality(text):
    t = text.lower()
    for k, v in _QUAL_MAP:
        if k in t:
            return v
    return ''

def _extract_print_type(text):
    t = text.lower()
    for k, v in _PRINT_MAP:
        if k in t:
            return v
    return ''

def _extract_langs(text):
    """Return list of detected languages from caption/filename."""
    t = text.lower()
    langs = []
    # Check multi-word keys first
    for k, v in sorted(_LANG_MAP.items(), key=lambda x: -len(x[0])):
        if k in t and v not in langs:
            langs.append(v)
    # Collapse: if Dual Audio or Multi Audio detected, remove individual langs
    if 'Dual Audio' in langs or 'Multi Audio' in langs:
        langs = [l for l in langs if l in ('Dual Audio', 'Multi Audio')]
    return langs[:4]

def _extract_lang(text):
    """Single language string (for backwards compat)."""
    langs = _extract_langs(text)
    return langs[0] if langs else ''

def _extract_subs(text):
    t = text.lower()
    for k, v in _SUB_MAP:
        if k in t:
            return v
    return ''

def _extract_se(text):
    m = _SE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    s = _S_RE.search(text)
    return (int(s.group(1)) if s else 0), 0

def _qs(text):
    t = text.lower()
    for k, v in _QUAL_SCORE.items():
        if k in t:
            return v
    return 40

def _clean_title(filename):
    """Return human-readable title stripped of year/quality/lang/season/episode."""
    n = _EXT.sub('', filename)
    n = _PUNC.sub(' ', n)
    m = _SE.search(n)
    if m:
        n = n[:m.start()]
    else:
        s = _S_RE.search(n)
        if s:
            n = n[:s.start()]
    y = _YEAR.search(n)
    if y:
        n = n[:y.start()]
    n = _JUNK.sub('', n)
    n = _LANG_STRIP.sub('', n)
    return _SPC.sub(' ', n).strip()

def _title_key(filename):
    return _clean_title(filename).lower().strip()

def _is_series(text):
    s, _ = _extract_se(text)
    return s > 0

def _caption_display(doc):
    """
    Clean caption for display — strips @tags and URLs but keeps quality/lang/SE info.
    This is shown as the file label in the Available Files section.
    """
    raw = doc.get('caption') or doc.get('file_name', '')
    n = _EXT.sub('', raw)
    n = re.sub(r'@\w+', '', n)
    n = re.sub(r'https?://\S+', '', n)
    n = _PUNC.sub(' ', n)
    n = _SPC.sub(' ', n).strip()
    return n

# ── DB helpers ────────────────────────────────────────────────────────────────

import concurrent.futures as _cf
_thread_pool = _cf.ThreadPoolExecutor(max_workers=6)

def _all_files(limit=300):
    try:
        # ---- 1️⃣ Get latest files ----
        docs = list(_col.find({}).sort('_id', -1).limit(limit))

        if _is2db() and len(docs) < limit:
            extra = list(_scol.find({}).sort('_id', -1).limit(limit - len(docs)))
            seen = {d['_id'] for d in docs}
            docs += [d for d in extra if d['_id'] not in seen]

        # ---- 2️⃣ Keep first 6 as latest ----
        latest = docs[:6]

        # ---- 3️⃣ Shuffle remaining ----
        remaining = docs[6:]
        random.shuffle(remaining)

        # ---- 4️⃣ Combine ----
        return latest + remaining

    except Exception as e:
        _logger.error(f'_all_files: {type(e).__name__}: {e}')
        return []

def _all_files_paged(skip, limit):
    """Paged DB fetch using skip/limit — avoids loading the entire collection."""
    try:
        docs = list(_col.find({}).sort('_id', -1).skip(skip).limit(limit))
        if _is2db() and len(docs) < limit:
            extra = list(_scol.find({}).sort('_id', -1).skip(max(0, skip - 5000)).limit(limit))
            seen = {d['_id'] for d in docs}
            docs += [d for d in extra if d['_id'] not in seen]
        return docs
    except Exception as e:
        _logger.error(f'_all_files_paged: {e}')
        return []

def _files_by_key(key, limit=120):
    if not key:
        return []
    words = [w for w in key.split() if len(w) >= 3]
    if not words:
        return []
    results, seen_ids = [], set()
    cols = [_col] + ([_scol] if _is2db() else [])
    patterns = [re.compile(re.escape(w), re.IGNORECASE) for w in words]
    for col in cols:
        query = {'$or': [{'file_name': patterns[0]}, {'caption': patterns[0]}]}
        for doc in col.find(query).limit(limit * 8):
            if doc['_id'] in seen_ids:
                continue
            fname = doc.get('caption') or doc.get('file_name', '')
            if _title_key(fname) == key:
                seen_ids.add(doc['_id'])
                results.append(doc)
    if len(results) < 3 and len(words) > 1:
        for col in cols:
            for pat in patterns[1:]:
                query = {'$or': [{'file_name': pat}, {'caption': pat}]}
                for doc in col.find(query).limit(limit * 4):
                    if doc['_id'] in seen_ids:
                        continue
                    fname = doc.get('caption') or doc.get('file_name', '')
                    if _title_key(fname) == key:
                        seen_ids.add(doc['_id'])
                        results.append(doc)
    return results[:limit]

async def _async_all_files(limit=300):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_thread_pool, _all_files, limit)

async def _async_all_files_paged(skip, limit):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_thread_pool, _all_files_paged, skip, limit)

async def _async_files_by_key(key, limit=120):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_thread_pool, _files_by_key, key, limit)

def _group(docs):
    """
    Group docs by title key AND sort groups by newest upload.
    """
    groups = {}

    for doc in docs:
        fname = doc.get('caption') or doc.get('file_name', '')
        key = _title_key(fname)
        if not key:
            continue

        if key not in groups:
            groups[key] = {
                'rep': doc,
                'files': [],
                'latest_id': doc['_id']   # ⭐ store newest file id
            }

        groups[key]['files'].append(doc)

        # ⭐ Update latest upload inside group
        if doc['_id'] > groups[key]['latest_id']:
            groups[key]['latest_id'] = doc['_id']
            groups[key]['rep'] = doc  # representative becomes newest

    # ⭐ Sort groups by newest upload
    sorted_groups = dict(
        sorted(groups.items(),
               key=lambda item: item[1]['latest_id'],
               reverse=True)
    )

    return sorted_groups

def _organise(docs):
    """Organise docs into movie or series tree."""
    any_series = any(_is_series(d.get('caption') or d.get('file_name', '')) for d in docs)
    if not any_series:
        return {
            'type': 'movie',
            'movie_files': sorted(docs, key=lambda d: -_qs(d.get('caption') or d.get('file_name', ''))),
            'seasons': None,
        }
    tree = defaultdict(lambda: defaultdict(list))
    for doc in docs:
        fname = doc.get('caption') or doc.get('file_name', '')
        s, e = _extract_se(fname)
        tree[max(s, 0)][e].append(doc)
    result = {}
    for sn in sorted(tree):
        result[sn] = {}
        for en in sorted(tree[sn]):
            result[sn][en] = sorted(tree[sn][en],
                                    key=lambda d: -_qs(d.get('caption') or d.get('file_name', '')))
    return {'type': 'series', 'movie_files': None, 'seasons': result}

def _doc_obj(doc):
    """
    Build the file object sent to the frontend.
    Returns ALL fields that miniapp.html JS expects:
      id, caption, size, quality, print_type, languages (list), subtitles, season, episode
    """
    raw    = doc.get('caption') or doc.get('file_name', '')
    s, e   = _extract_se(raw)
    size   = _get_size(doc.get('file_size', 0))
    q      = _extract_quality(raw)
    ptype  = _extract_print_type(raw)
    langs  = _extract_langs(raw)
    subs   = _extract_subs(raw)
    # caption: clean display version (no @tags, no URLs) — used by buildFileLabel in JS
    caption = _caption_display(doc)

    return {
        'id':         str(doc['_id']),
        'caption':    caption,       # JS reads f.caption
        'size':       size,          # JS reads f.size
        'quality':    q,             # JS reads f.quality
        'print_type': ptype,         # JS reads f.print_type
        'languages':  langs,         # JS reads f.languages (array)
        'subtitles':  subs,          # JS reads f.subtitles
        'season':     s,             # JS reads f.season
        'episode':    e,             # JS reads f.episode
    }

# ── TMDB / IMDB fetch ────────────────────────────────────────────────────────

_TMDB_IMG  = 'https://image.tmdb.org/t/p/w500'
_TMDB_BACK = 'https://image.tmdb.org/t/p/w1280'


async def _tmdb_fetch(title, year=''):
    """Fetch from TMDB. Results cached in _META_CACHE."""
    api_key = _TMDB_KEY if _DB_OK else ''
    if not api_key or not title:
        return None

    cache_key = f"tmdb|{title.lower().strip()}|{year}"
    cached = _META_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _META_CACHE_TTL:
        return cached[1]

    async def _search_tmdb(sess, query):
        params = {'api_key': api_key, 'query': query, 'page': 1}
        async with sess.get('https://api.themoviedb.org/3/search/multi', params=params) as r:
            if r.status != 200:
                return []
            data = await r.json()
            return [x for x in data.get('results', []) if x.get('media_type') != 'person']

    try:
        import aiohttp, socket
        to = aiohttp.ClientTimeout(total=8, connect=4)
        connector = aiohttp.TCPConnector(family=socket.AF_INET, ssl=False)
        async with aiohttp.ClientSession(connector=connector, timeout=to) as sess:
            all_results = await _search_tmdb(sess, title)
            if not all_results:
                _META_CACHE[cache_key] = (time.time(), None)
                return None

            item = None
            if year:
                for x in all_results:
                    d = (x.get('release_date') or x.get('first_air_date') or '')[:4]
                    t_name = (x.get('title') or x.get('name') or '').lower()
                    if d == year and t_name == title.lower():
                        item = x; break
                if not item:
                    for x in all_results:
                        d = (x.get('release_date') or x.get('first_air_date') or '')[:4]
                        if d == year:
                            item = x; break
            if not item:
                tl = title.lower()
                item = next(
                    (x for x in all_results if (x.get('title') or x.get('name') or '').lower() == tl),
                    all_results[0]
                )

            mt  = item.get('media_type', 'movie')
            iid = item.get('id')

            async with sess.get(
                f'https://api.themoviedb.org/3/{mt}/{iid}',
                params={'api_key': api_key, 'append_to_response': 'credits'},
            ) as dr:
                detail = await dr.json() if dr.status == 200 else None

        src    = detail or item
        genres = [g['name'] for g in src.get('genres', [])] if detail else []
        cr     = (detail or {}).get('credits', {})
        cast   = [c['name'] for c in cr.get('cast', [])[:8]]
        dirs   = [c['name'] for c in cr.get('crew', []) if c.get('job') == 'Director'][:2]
        rt     = (detail or {}).get('runtime') or \
                 ((detail or {}).get('episode_run_time') or [None])[0]

        result = {
            'title':    src.get('title') or src.get('name', title),
            'year':     (src.get('release_date') or src.get('first_air_date') or '')[:4],
            'poster':   f"{_TMDB_IMG}{src['poster_path']}" if src.get('poster_path') else None,
            'backdrop': f"{_TMDB_BACK}{src['backdrop_path']}" if src.get('backdrop_path') else None,
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
        }
        _META_CACHE[cache_key] = (time.time(), result)
        return result

    except asyncio.TimeoutError:
        _logger.debug(f'TMDB timeout for "{title}"')
        _META_CACHE[cache_key] = (time.time(), None)
        return None
    except Exception as e:
        _logger.debug(f'TMDB error for "{title}": {type(e).__name__}: {e}')
        _META_CACHE[cache_key] = (time.time(), None)
        return None


async def _imdb_fetch(title, year=''):
    """
    IMDB fallback via Cinemagoer.
    NOTE: IMDB is currently returning HTTP 500 errors from their servers.
    This function is only called for group_details (single title), NOT for /recent.
    """
    cache_key = f"imdb|{title.lower().strip()}|{year}"
    cached = _META_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _META_CACHE_TTL:
        return cached[1]

    try:
        from imdb import Cinemagoer
        loop = asyncio.get_event_loop()

        def _do_imdb():
            ia   = Cinemagoer()
            hits = ia.search_movie(title, results=8)
            if not hits:
                return None
            if year:
                fy = [h for h in hits if str(h.get('year', '')) == year]
                hits = fy or hits
            typed = [h for h in hits if h.get('kind') in ('movie', 'tv series')]
            pick  = (typed or hits)[0]
            mv    = ia.get_movie(pick.movieID)
            try:
                ia.update(mv, info=['main'])
            except Exception:
                pass  # IMDB returning 500s — use whatever we have
            return mv, pick

        result = await asyncio.wait_for(
            loop.run_in_executor(_thread_pool, _do_imdb), timeout=10
        )
        if result is None:
            _META_CACHE[cache_key] = (time.time(), None)
            return None
        mv, pick = result
        raw_plot = mv.get('plot', [])
        plot     = ''
        if raw_plot:
            plot = raw_plot[0].split('::')[0] if '::' in raw_plot[0] else raw_plot[0]
        cast   = [str(c) for c in (mv.get('cast') or [])[:8]]
        dirs_r = mv.get('directors') or mv.get('director') or []
        dirs   = [str(d) for d in (dirs_r[:2] if isinstance(dirs_r, list) else [])]
        rt     = (mv.get('runtimes') or [''])[0]
        genres = (mv.get('genres') or [])[:5]
        poster = mv.get('full-size cover url') or mv.get('cover url')
        mt     = 'tv' if mv.get('kind') == 'tv series' else 'movie'
        res = {
            'title':    mv.get('title', title),
            'year':     str(mv.get('year', year or '')),
            'poster':   poster,
            'backdrop': None,
            'rating':   float(mv.get('rating', 0) or 0),
            'plot':     plot[:600],
            'genres':   genres if isinstance(genres, list) else [],
            'cast':     cast,
            'director': ', '.join(dirs),
            'runtime':  f'{rt} min' if rt else '',
            'type':     mt,
            'seasons':  mv.get('number of seasons'),
            'episodes': None,
            'tagline':  '',
            'status':   '',
            'imdb_id':  f"tt{pick.movieID}",
        }
        _META_CACHE[cache_key] = (time.time(), res)
        return res
    except Exception as e:
        _logger.debug(f'IMDB error for "{title}": {type(e).__name__}: {e}')
        _META_CACHE[cache_key] = (time.time(), None)
        return None


async def _get_meta(title, year='', tmdb_only=False):
    """
    TMDB first; IMDB fallback only when explicitly needed (not for /recent).
    Uses _META_CACHE to avoid duplicate requests.
    """
    if not title:
        return None
    tmdb = await _tmdb_fetch(title, year)
    if tmdb and tmdb.get('poster'):
        return tmdb
    if not tmdb_only:
        imdb = await _imdb_fetch(title, year)
        if imdb:
            if tmdb:
                imdb['backdrop'] = imdb.get('backdrop') or tmdb.get('backdrop')
                imdb['genres']   = imdb.get('genres') or tmdb.get('genres', [])
            return imdb
    return tmdb  # may be None or poster-less TMDB result

# ── MINI APP ROUTES ───────────────────────────────────────────────────────────

_ANIME_RE = re.compile(
    r'\b(anime|hentai|ova|ona|oad|manhwa|manhua|donghua|'
    r'shonen|seinen|shoujo|josei|isekai|mecha|'
    r'dubbed\s*anime|sub\s*anime)\b|'
    r'[\u3040-\u30FF\u4E00-\u9FFF]',
    re.IGNORECASE,
)

def _detect_content_type(fname, tmdb_type=None, tmdb_genres=None):
    """Classify a file as 'movie', 'series', or 'anime'."""
    if _ANIME_RE.search(fname):
        return 'anime'
    if tmdb_genres:
        genre_str = ' '.join(g.lower() for g in tmdb_genres)
        if 'animation' in genre_str or 'anime' in genre_str:
            return 'anime'
    if tmdb_type == 'tv' or _is_series(fname):
        return 'series'
    return 'movie'


@routes.get("/miniapp/browse")
async def miniapp_browse(request):
    """
    GET /miniapp/browse?type=movies|series|anime&page=0&limit=24
    Filters DB files by content type and returns grouped cards.
    """
    if not _DB_OK:
        return _jresp({"ok": False, "error": "DB unavailable"}, 500)

    try:
        raw_type = request.rel_url.query.get("type", "movies").lower().rstrip("s")
        if raw_type in ("movie", "movies"):
            content_type = "movie"
        elif raw_type in ("serie", "series"):
            content_type = "series"
        elif raw_type == "anime":
            content_type = "anime"
        else:
            content_type = "movie"

        page  = max(0, int(request.rel_url.query.get("page", 0)))
        limit = min(int(request.rel_url.query.get("limit", 24)), 40)
    except (ValueError, TypeError):
        page, limit, content_type = 0, 24, "movie"

    # Scan a large window and filter — more docs needed since we discard many
    scan_skip  = page * limit * 6
    scan_limit = limit * 20
    all_docs   = await _async_all_files_paged(scan_skip, scan_limit)

    # Filter by content type using filename heuristics
    def _classify(doc):
        stored = doc.get("category")
        if stored in ("movie", "series", "anime"):
            return stored
        fname = doc.get("caption") or doc.get("file_name", "")
        return _detect_content_type(fname)

    filtered = [d for d in all_docs if _classify(d) == content_type]

    groups = _group(filtered)
    sorted_groups = sorted(groups.values(), key=lambda g: g["rep"]["_id"], reverse=True)
    target   = sorted_groups[:limit]
    has_more = len(sorted_groups) > limit

    results = []
    for grp in target:
        rep   = grp["rep"]
        fname = rep.get("caption") or rep.get("file_name", "")
        title = _clean_title(fname)
        year  = grp.get("year") or _extract_year(fname)

        # Use cached meta only — no blocking network calls during browse
        ck  = f"tmdb|{title.lower().strip()}|{year}"
        ci  = f"imdb|{title.lower().strip()}|{year}"
        meta = None
        for ck2 in (ck, ci):
            c = _META_CACHE.get(ck2)
            if c and (time.time() - c[0]) < _META_CACHE_TTL and c[1]:
                meta = c[1]
                break

        results.append({
            "group_title": title,
            "id":          str(rep["_id"]),
            "name":        (meta or {}).get("title") or title,
            "year":        (meta or {}).get("year") or year,
            "poster":      (meta or {}).get("poster"),
            "backdrop":    (meta or {}).get("backdrop"),
            "rating":      (meta or {}).get("rating"),
            "genres":      (meta or {}).get("genres", []),
            "type":        content_type,
            "file_count":  len(grp["files"]),
        })

    return _jresp({"ok": True, "results": results, "count": len(results),
                   "page": page, "has_more": has_more})


@routes.options("/miniapp/browse")
async def miniapp_browse_options(request):
    return web.Response(headers=_CORS)


@routes.get("/miniapp")
async def miniapp_serve(request):
    html = os.path.join(os.path.dirname(os.path.dirname(__file__)), "miniapp.html")
    if os.path.exists(html):
        return web.FileResponse(html)
    return web.Response(text="<h1>Deploy miniapp.html to your server root</h1>",
                        content_type="text/html")

@routes.options("/miniapp/recent")
@routes.options("/miniapp/search")
@routes.options("/miniapp/group_details")
@routes.options("/miniapp/poster")
async def miniapp_cors(request):
    return web.Response(headers=_CORS)


@routes.get("/miniapp/recent")
async def miniapp_recent(request):
    """
    TRUE Recently Added Titles endpoint
    """

    if not _DB_OK:
        return _jresp({"ok": False, "error": "DB unavailable"}, 500)

    try:
        page  = max(0, int(request.rel_url.query.get("page", 0)))
        limit = min(int(request.rel_url.query.get("limit", 20)), 40)
    except Exception:
        page, limit = 0, 20

    # ⭐ ALWAYS GET LATEST FILES FROM DB
    latest_docs = await _async_all_files(limit=500)

    # ⭐ GROUP BY TITLE
    groups = _group(latest_docs)

    # ⭐ SORT TITLES BY NEWEST UPLOAD
    sorted_groups = sorted(
        groups.values(),
        key=lambda g: g['latest_id'],
        reverse=True
    )

    # ⭐ PAGINATION AFTER GROUPING
    start = page * limit
    target = sorted_groups[start:start + limit]

    has_more = len(sorted_groups) > start + limit

    results = []

    for grp in target:
        rep   = grp['rep']
        fname = rep.get('caption') or rep.get('file_name', '')

        title = _clean_title(fname)
        year  = _extract_year(fname)

        results.append({
            'group_title': title,
            'id':          str(rep['_id']),
            'name':        title,
            'year':        year,
            'poster':      None,
            'rating':      None,
            'genres':      [],
            'type':        'series' if _is_series(fname) else 'movie',
            'file_count':  len(grp['files']),
        })

    return _jresp({
        'ok': True,
        'results': results,
        'count': len(results),
        'page': page,
        'has_more': has_more
    })

@routes.get("/miniapp/poster")
async def miniapp_poster(request):
    """
    Fetch TMDB meta for a SINGLE card lazily (called per card after grid renders).
    ?title=Elio&year=2025
    Returns {ok, poster, backdrop, rating, genres, name, year, type}
    """
    title = request.rel_url.query.get("title", "").strip()
    year  = request.rel_url.query.get("year", "").strip()
    if not title:
        return _jresp({"ok": False, "error": "Missing title"}, 400)
    try:
        # TMDB only for poster endpoint — fast, no IMDB
        meta = await _get_meta(title, year, tmdb_only=True)
        if not meta:
            return _jresp({"ok": True, "meta": None})
        return _jresp({"ok": True, "meta": {
            'title':    meta.get('title') or title,
            'year':     meta.get('year') or year,
            'poster':   meta.get('poster'),
            'backdrop': meta.get('backdrop'),
            'rating':   meta.get('rating'),
            'genres':   meta.get('genres', []),
            'type':     meta.get('type'),
        }})
    except Exception as e:
        return _jresp({"ok": False, "error": str(e)}, 500)


@routes.get("/miniapp/search")
async def miniapp_search(request):
    if not _DB_OK:
        return _jresp({"ok": False, "error": "DB unavailable"}, 500)
    q = request.rel_url.query.get("q", "").strip()
    if not q:
        return _jresp({"ok": False, "error": "Missing q"}, 400)
    files, _, total = await _search(q, max_results=80)
    groups        = _group(files)
    sorted_groups = list(groups.values())[:20]

    # TMDB only (fast, no IMDB), concurrency limited to avoid rate limits
    sem = asyncio.Semaphore(5)

    async def make_card(grp):
        rep   = grp['rep']
        fname = rep.get('caption') or rep.get('file_name', '')
        title = _clean_title(fname)
        year  = grp.get('year') or _extract_year(fname)
        async with sem:
            try:
                meta = await _get_meta(title, year, tmdb_only=True)
            except Exception:
                meta = None
        return {
            'group_title': title,
            'id':          str(rep['_id']),
            'name':        (meta or {}).get('title') or title,
            'year':        (meta or {}).get('year') or year,
            'poster':      (meta or {}).get('poster'),
            'rating':      (meta or {}).get('rating'),
            'genres':      (meta or {}).get('genres', []),
            'type':        (meta or {}).get('type') or ('series' if _is_series(fname) else 'movie'),
            'file_count':  len(grp['files']),
        }

    cards   = await asyncio.gather(*[make_card(g) for g in sorted_groups], return_exceptions=True)
    results = [c for c in cards if isinstance(c, dict)]
    return _jresp({'ok': True, 'results': results, 'total': total})


@routes.get("/miniapp/group_details")
async def miniapp_group_details(request):
    """
    Returns full metadata + ALL files for a title grouped by season/episode.
    """
    if not _DB_OK:
        return _jresp({"ok": False, "error": "DB unavailable"}, 500)
    file_id = request.rel_url.query.get("id", "").strip()
    if not file_id:
        return _jresp({"ok": False, "error": "Missing id"}, 400)

    rep = await _get_file(file_id)
    if not rep:
        return _jresp({"ok": False, "error": "File not found"}, 404)

    fname = rep.get('caption') or rep.get('file_name', '')
    title = _clean_title(fname)
    year  = _extract_year(fname)
    key   = _title_key(fname)

    variants = await _async_files_by_key(key)
    if not variants:
        variants = [rep]

    # Full meta for detail page — TMDB + IMDB fallback
    try:
        meta = await _get_meta(title, year)
    except Exception:
        meta = None

    organised = _organise(variants)

    def ser_season(ep_dict):
        out = {}
        for en, ep_docs in sorted(ep_dict.items()):
            out[str(en)] = {
                'label': f'Episode {en}' if en > 0 else 'Season Pack',
                'files': [_doc_obj(d) for d in ep_docs],
            }
        return out

    if organised['type'] == 'series':
        seasons_out = {
            str(sn): ser_season(ep_dict)
            for sn, ep_dict in sorted(organised['seasons'].items())
        }
        payload = {'seasons': seasons_out}
    else:
        payload = {'movie_files': [_doc_obj(d) for d in organised['movie_files']]}

    # Aggregate file-level info from sample files for the info panel
    db_langs, db_subs, db_qual, db_print = [], '', '', ''
    for doc in variants[:8]:
        fn = doc.get('caption') or doc.get('file_name', '')
        if not db_langs: db_langs = _extract_langs(fn)
        if not db_subs:  db_subs  = _extract_subs(fn)
        if not db_qual:  db_qual  = _extract_quality(fn)
        if not db_print: db_print = _extract_print_type(fn)

    # Build combined_seasons list for the bottom navigation widget
    combined_seasons = []
    if organised['type'] == 'series' and organised.get('seasons'):
        for sn in sorted(organised['seasons'].keys()):
            ep_count = len(organised['seasons'][sn])
            combined_seasons.append({
                'season':        sn,
                'label':         f'Season {sn}' if sn > 0 else 'Extras',
                'episode_count': ep_count,
            })

    # caption_title: use the raw caption_display of the representative file
    # so the detail page shows the actual DB caption, not the over-stripped clean title
    caption_title = _caption_display(rep)

    return _jresp({
        'ok':               True,
        'content_type':     organised['type'],
        'caption_title':    caption_title,
        'file_count':       len(variants),
        'meta':             meta,
        'combined_seasons': combined_seasons,
        'db_languages':     db_langs,
        'db_subtitles':     db_subs,
        'db_quality':       db_qual,
        'db_print':         db_print,
        **payload,
    })


@routes.get("/miniapp/details")
async def miniapp_details_legacy(request):
    return await miniapp_group_details(request)


# ─── /miniapp/send_file  (POST) ───────────────────────────────────────────────
# Called by miniapp.html when user taps a file button.
# Validates Telegram initData, runs all checks (force-sub, limit, verify,
# premium), then uses temp.BOT to send the file directly to the user.

@routes.options("/miniapp/send_file")
async def miniapp_send_file_cors(request):
    return web.Response(headers={
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    })


@routes.post("/miniapp/send_file")
async def miniapp_send_file(request):
    """
    Send a file to the user from the Mini App.
    Logic is a direct port of the `start` command handler in commands.py.

    Exact flow (matching commands.py lines 187-458):

    A) Force-sub check — non-premium only (commands.py lines 187-246)
       If user hasn't joined required channels → send join photo to DM → stop.

    B) Premium check (commands.py line 248)
       Premium users skip B/C/D entirely → send file directly.

    C) File-limit block — non-premium only, IS_FILE_LIMIT + FILES_LIMIT > 0
       (commands.py lines 257-323)
         current = silicon_file_limit(user_id)
         if current < FILES_LIMIT:
             increment counter
             send file with "X/Y free files" counter in caption
             ← RETURN (no verify check here — mirrors commands.py exactly)
         # if current >= FILES_LIMIT → fall through to D

    D) Verify check (commands.py lines 325-355)
       Runs when: (a) file limit was exceeded, OR (b) IS_FILE_LIMIT disabled.
       if is_verify AND (not user_verified OR is_second_shortener OR is_third_shortener):
           send verify message → stop
       else (user is currently verified AND shorteners haven't expired):
           → fall through to E  (unlimited files while verified)

    E) Send file (commands.py lines 403-458)
       Reached when: under limit and limit disabled, OR verified user over limit.
    """
    import hashlib, hmac, urllib.parse, random, string
    from utils import (temp, get_settings, is_subscribed, is_req_subscribed,
                       get_shortlink, get_status, formate_file_name, get_size)
    from database.users_chats_db import db
    from database.extra_db import silicondb
    from info import (BOT_TOKEN, AUTH_CHANNELS, AUTH_REQ_CHANNELS,
                      IS_FILE_LIMIT, FILES_LIMIT, IS_VERIFY,
                      TWO_VERIFY_GAP, THREE_VERIFY_GAP,
                      FILE_AUTO_DEL_TIMER, FSUB_PICS,
                      TUTORIAL, TUTORIAL2, TUTORIAL3)
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from Script import script as _script

    _CORS = {
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

    def _resp(payload, status=200):
        return web.Response(
            text=json.dumps(payload), status=status,
            content_type="application/json", headers=_CORS,
        )

    # ── 0. Parse + validate body ───────────────────────────────────────────────
    try:
        body = await request.json()
    except Exception:
        return _resp({"ok": False, "error": "bad_request"}, 400)

    file_id       = (body.get("file_id") or "").strip()
    init_data_raw = body.get("initData", "")

    if not file_id:
        _logger.warning("miniapp send_file: missing file_id")
        return _resp({"ok": False, "error": "bad_request"}, 400)

    # Validate Telegram WebApp initData
    user_id = None
    if init_data_raw:
        try:
            parsed     = dict(urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True))
            hash_      = parsed.pop("hash", "")
            data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
            secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
            expected   = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, hash_):
                user_obj = json.loads(parsed.get("user", "{}"))
                user_id  = user_obj.get("id")
            else:
                _logger.warning("miniapp send_file: initData hash mismatch")
        except Exception as _e:
            _logger.warning(f"miniapp send_file: initData parse error: {_e}")

    if not user_id:
        _logger.warning(f"miniapp send_file: Unauthorized — file_id={file_id}")
        return _resp({"ok": False, "error": "Unauthorized"}, 401)

    user_id = int(user_id)
    _logger.info(f"miniapp send_file: user={user_id} file_id={file_id}")

    bot = temp.BOT
    if not bot:
        _logger.error("miniapp send_file: temp.BOT is None")
        return _resp({"ok": False, "error": "server_error"}, 503)

    # grp_id=0 → global/default settings (miniapp has no group context)
    grp_id   = 0
    settings = await get_settings(grp_id)

    # ── A. Force-subscribe check — non-premium users only ─────────────────────
    # Mirrors commands.py lines 187-246.
    # Premium users skip this block entirely.
    if not await db.has_premium_access(user_id):
        try:
            sub_btns = []
            fsub_channels = list(dict.fromkeys(
                (settings.get("fsub", []) if settings else []) + AUTH_CHANNELS
            ))
            if fsub_channels:
                sub_btns += await is_subscribed(bot, user_id, fsub_channels)
            if AUTH_REQ_CHANNELS:
                sub_btns += await is_req_subscribed(bot, user_id, AUTH_REQ_CHANNELS)
            if sub_btns:
                _logger.info(f"miniapp send_file: user={user_id} failed force-sub")
                sub_btns.append([InlineKeyboardButton(
                    "♻️ ᴛʀʏ ᴀɢᴀɪɴ ♻️",
                    callback_data=f"checksub#miniapp#{file_id}",
                )])
                photo = random.choice(FSUB_PICS) if FSUB_PICS else \
                    "https://graph.org/file/7478ff3eac37f4329c3d8.jpg"
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo,
                    caption=(
                        "🛑 ʏᴏᴜ ᴍᴜsᴛ ᴊᴏɪɴ ᴛʜᴇ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟs ᴛᴏ ᴄᴏɴᴛɪɴᴜᴇ.\n"
                        "👉 ᴊᴏɪɴ ᴀʟʟ ᴛʜᴇ ʙᴇʟᴏᴡ ᴄʜᴀɴɴᴇʟs ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ."
                    ),
                    reply_markup=InlineKeyboardMarkup(sub_btns),
                )
                return _resp({"ok": False, "error": "force_sub"})
        except Exception as _fe:
            _logger.error(f"miniapp send_file: force-sub error: {_fe}")

    # ── B. Premium users → send file directly, skip all limits/verify ─────────
    # Mirrors commands.py line 248: `if not await db.has_premium_access(user_id):`
    # (premium users skip the entire non-premium block and reach the send at line 445)
    if await db.has_premium_access(user_id):
        file_doc = await _get_file(file_id)
        if not file_doc:
            _logger.warning(f"miniapp send_file: file not found: {file_id}")
            return _resp({"ok": False, "error": "file_not_found"}, 404)
        _logger.info(f"miniapp send_file: user={user_id} PREMIUM — sending directly")
        asyncio.ensure_future(
            _actually_send(bot, user_id, file_id, file_doc, settings, None)
        )
        return _resp({"ok": True})

    # ── Non-premium user from here on ─────────────────────────────────────────
    # Compute verify state — same three variables used in commands.py lines 250-253
    user_verified       = await db.is_user_verified(user_id)
    is_second_shortener = await db.use_second_shortener(
        user_id, settings.get("verify_time", TWO_VERIFY_GAP))
    is_third_shortener  = await db.use_third_shortener(
        user_id, settings.get("third_verify_time", THREE_VERIFY_GAP))

    _logger.info(
        f"miniapp send_file: user={user_id} "
        f"verified={user_verified} 2nd={is_second_shortener} 3rd={is_third_shortener}"
    )

    # Fetch file doc — needed for building caption and sending
    file_doc = await _get_file(file_id)
    if not file_doc:
        _logger.warning(f"miniapp send_file: file not found: {file_id}")
        return _resp({"ok": False, "error": "file_not_found"}, 404)

    # Helper — build and send verification message.
    # Mirrors commands.py lines 326-355 exactly.
    async def _send_verify_msg():
        verify_id  = "".join(random.choices(string.ascii_uppercase + string.digits, k=7))
        await db.create_verify_id(user_id, verify_id)
        temp.CHAT[user_id] = grp_id
        verify_url = await get_shortlink(
            f"https://telegram.me/{temp.U_NAME}?start=notcopy_{user_id}_{verify_id}_{file_id}",
            grp_id, is_second_shortener, is_third_shortener,
        )
        if is_third_shortener:
            tutorial = settings.get("tutorial_three", TUTORIAL3)
        elif is_second_shortener:
            tutorial = settings.get("tutorial_two", TUTORIAL2)
        else:
            tutorial = settings.get("tutorial", TUTORIAL)
        # commands.py line 340: pick message text based on which verification stage
        is_third_ver = await db.user_verified(user_id)
        msg = (_script.THIRDT_VERIFICATION_TEXT if is_third_ver
               else (_script.SECOND_VERIFICATION_TEXT if is_second_shortener
                     else _script.VERIFICATION_TEXT))
        await bot.send_message(
            chat_id=user_id,
            text=msg.format(f"<a href='tg://user?id={user_id}'>User</a>", get_status()),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("♻️ ᴠᴇʀɪғʏ ♻️",          url=verify_url)],
                [InlineKeyboardButton("❗️ ʜᴏᴡ ᴛᴏ ᴠᴇʀɪғʏ ❓", url=tutorial)],
            ]),
            parse_mode=enums.ParseMode.HTML,
        )
        _logger.info(f"miniapp send_file: verify msg sent to user={user_id}")

    # ── C. File-limit block — mirrors commands.py lines 257-323 EXACTLY ───────
    #
    # commands.py:
    #   if not is_allfiles_request and IS_FILE_LIMIT and FILES_LIMIT > 0:
    #       current_file_count = silicondb.silicon_file_limit(user_id)
    #       if current_file_count < FILES_LIMIT:
    #           silicondb.increment_silicon_limit(user_id)
    #           current_file_count += 1
    #           ... send file ...
    #           return                    ← returns immediately, NO verify check here
    #   # reaches here only when current >= FILES_LIMIT OR file limit disabled
    #   if settings.get("is_verify", IS_VERIFY) and ...:  ← verify check (block D)
    #
    # CRITICAL: There is NO verify check inside the current < FILES_LIMIT block.
    # The verify check (D) only runs when the limit is exceeded or disabled.
    #
    current_file_count = None  # tracks count for caption; None = no counter in caption
    if IS_FILE_LIMIT and FILES_LIMIT > 0:
        current = silicondb.silicon_file_limit(user_id)
        _logger.info(f"miniapp send_file: user={user_id} count={current}/{FILES_LIMIT}")

        if current < FILES_LIMIT:
            # User is under limit: increment then send immediately.
            # DO NOT run verify check here — commands.py does not.
            silicondb.increment_silicon_limit(user_id)
            current_file_count = current + 1
            _logger.info(
                f"miniapp send_file: user={user_id} incremented → {current_file_count}/{FILES_LIMIT}"
            )
            asyncio.ensure_future(
                _actually_send(bot, user_id, file_id, file_doc, settings, current_file_count)
            )
            return _resp({"ok": True})

        # current >= FILES_LIMIT → fall through to verify check (block D)
        _logger.info(
            f"miniapp send_file: user={user_id} limit exceeded "
            f"({current}/{FILES_LIMIT}) — checking verify"
        )

    # ── D. Verify check — commands.py lines 325-355 ───────────────────────────
    # Runs when: (a) file limit exceeded (fell through from C), OR
    #            (b) IS_FILE_LIMIT is disabled.
    #
    # If is_verify is ON and user is NOT currently verified (or needs re-verify):
    #   → send verify message, stop.
    # If is_verify is ON and user IS verified (all three shortener checks clean):
    #   → fall through to E, send file (unlimited while verified).
    # If is_verify is OFF:
    #   → fall through to E, send file (no verification system at all).
    if settings.get("is_verify", IS_VERIFY) and \
            (not user_verified or is_second_shortener or is_third_shortener):
        _logger.info(f"miniapp send_file: user={user_id} needs verification — sending verify msg")
        await _send_verify_msg()
        return _resp({"ok": False, "error": "verify_required"})

    # ── E. All checks passed → send file ──────────────────────────────────────
    # Reached when:
    #   - IS_FILE_LIMIT disabled and is_verify disabled (or user already verified)
    #   - OR file limit exceeded but user IS verified → unlimited while verified
    _logger.info(f"miniapp send_file: user={user_id} all checks passed — sending file")
    asyncio.ensure_future(
        _actually_send(bot, user_id, file_id, file_doc, settings, current_file_count)
    )
    return _resp({"ok": True})


async def _actually_send(bot, user_id, file_id, file_doc, settings, current_file_count):
    """
    Send the file via send_cached_media and handle auto-delete timer.
    Mirrors commands.py lines 293-323 (under-limit path) and 432-458 (fallthrough path).
    Runs as a background asyncio task — never blocks the HTTP response.
    """
    from info import FILES_LIMIT, FILE_AUTO_DEL_TIMER
    from utils import formate_file_name, get_size
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from pyrogram.errors import FloodWait

    _logger.info(f"_actually_send: user={user_id} file_id={file_id} count={current_file_count}")

    fname    = file_doc.get("file_name", "")
    fcaption = file_doc.get("caption", "")

    # Add "X/Y free files" info to caption when sending under limit (commands.py line 295)
    file_limit_info = ""
    if current_file_count is not None and FILES_LIMIT > 0:
        file_limit_info = (
            f"\n\n📊 ʏᴏᴜ ʜᴀᴠᴇ ʀᴇᴄᴇɪᴠᴇᴅ "
            f"{current_file_count}/{FILES_LIMIT} ꜰʀᴇᴇ ꜰɪʟᴇs"
        )

    try:
        f_caption = settings["caption"].format(
            file_name=formate_file_name(fname),
            file_size=get_size(file_doc.get("file_size", 0)),
            file_caption=fcaption,
        ) + file_limit_info
    except Exception:
        f_caption = (
            f"<b>📁 {formate_file_name(fname or fcaption)}</b>\n"
            f"<b>💾 Size:</b> <code>{get_size(file_doc.get('file_size', 0))}</code>"
            + file_limit_info
        )

    btn = [[InlineKeyboardButton(
        "✛ ᴡᴀᴛᴄʜ & ᴅᴏᴡɴʟᴏᴀᴅ ✛",
        callback_data=f"stream#{file_id}"
    )]]

    toDel = None
    for attempt in range(3):
        try:
            toDel = await bot.send_cached_media(
                chat_id=user_id,
                file_id=file_id,
                caption=f_caption,
                reply_markup=InlineKeyboardMarkup(btn),
            )
            _logger.info(f"_actually_send: SUCCESS user={user_id} attempt={attempt + 1}")
            break
        except FloodWait as fw:
            _logger.warning(f"_actually_send: FloodWait {fw.value}s — user={user_id}")
            await asyncio.sleep(fw.value)
        except Exception as exc:
            _logger.error(
                f"_actually_send: FAILED user={user_id}: {type(exc).__name__}: {exc}"
            )
            return

    if not toDel:
        _logger.error(f"_actually_send: gave up after retries for user={user_id}")
        return

    # Auto-delete timer — mirrors commands.py lines 316-323
    time_text = (
        f"{FILE_AUTO_DEL_TIMER / 60} ᴍɪɴᴜᴛᴇs"
        if FILE_AUTO_DEL_TIMER >= 60
        else f"{FILE_AUTO_DEL_TIMER} sᴇᴄᴏɴᴅs"
    )
    del_cap   = (
        f"<b>ʏᴏᴜʀ ғɪʟᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ᴀғᴛᴇʀ {time_text} "
        f"ᴛᴏ ᴀᴠᴏɪᴅ ᴄᴏᴘʏʀɪɢʜᴛ ᴠɪᴏʟᴀᴛɪᴏɴs!</b>"
    )
    after_del = (
        f"<b>ʏᴏᴜʀ ғɪʟᴇ ɪs ᴅᴇʟᴇᴛᴇᴅ ᴀғᴛᴇʀ {time_text} "
        f"ᴛᴏ ᴀᴠᴏɪᴅ ᴄᴏᴘʏʀɪɢʜᴛ ᴠɪᴏʟᴀᴛɪᴏɴs!</b>"
    )

    try:
        rem = await bot.send_message(
            chat_id=user_id,
            text=del_cap,
            reply_to_message_id=toDel.id,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        rem = None

    await asyncio.sleep(FILE_AUTO_DEL_TIMER)

    try:
        await toDel.delete()
    except Exception:
        pass
    if rem:
        try:
            await rem.edit(after_del, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
