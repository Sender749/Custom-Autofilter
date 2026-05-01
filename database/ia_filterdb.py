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

# ── Async client (for filter_words only) ─────────────────────────────────────
async_client = AsyncIOMotorClient(FILES_DATABASE_URL)
async_db = async_client[DATABASE_NAME]
filter_words_collection = async_db["filter_words"]

# ── Sync clients (pymongo) ────────────────────────────────────────────────────
client = MongoClient(FILES_DATABASE_URL)
db = client[DATABASE_NAME]
collection = db[COLLECTION_NAME]
second_collection = None

try:
    collection.create_index([("file_name", TEXT)])
except OperationFailure as e:
    if 'quota' in str(e).lower():
        if not SECOND_FILES_DATABASE_URL:
            logger.error('your FILES_DATABASE_URL is already full, add SECOND_FILES_DATABASE_URL')
        else:
            logger.info('FILES_DATABASE_URL is full, now using SECOND_FILES_DATABASE_URL')
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

# ── In-memory title cache ─────────────────────────────────────────────────────
_TITLE_CACHE: dict = {}
_TITLE_CACHE_TIME: float = 0.0
_TITLE_CACHE_TTL: float = 300.0   # 5-minute refresh


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


# ── Base-title stripper ───────────────────────────────────────────────────────
_JUNK_RE = re.compile(
    r'\b(S\d{1,2}E?\d{0,3}|Season\s*\d+|Episode\s*\d+|\d{3,4}p'
    r'|BluRay|BDRip|Remux|WEBRip|WEB-DL|HDRip|DVDRip|CAM|TS'
    r'|HEVC|x264|x265|AVC|AV1|AAC|DDP5?\.?\d?|FLAC|MP3|AC3|DTS'
    r'|ESub|Subs?|Hindi|Tamil|Telugu|Malayalam|Kannada|Punjabi'
    r'|English|Dual|Multi|HQ|HD|UHD|SDR|HDR|Dolby)\b.*',
    re.IGNORECASE
)


def _base_title(fn: str) -> str:
    fn = _JUNK_RE.sub('', fn)
    fn = re.sub(r'[._\-\+\[\]()]+', ' ', fn)
    fn = re.sub(r'\s{2,}', ' ', fn)
    return fn.strip().lower()


def _build_title_cache_sync() -> dict:
    result = {}
    try:
        for doc in collection.find({}, {"file_name": 1, "_id": 0}).limit(5000):
            b = _base_title(doc.get("file_name", ""))
            if b and b not in result:
                result[b] = b
    except Exception as exc:
        logger.debug("title cache primary error: %s", exc)
    if is_second_db_configured():
        try:
            for doc in second_collection.find({}, {"file_name": 1, "_id": 0}).limit(2000):
                b = _base_title(doc.get("file_name", ""))
                if b and b not in result:
                    result[b] = b
        except Exception as exc:
            logger.debug("title cache secondary error: %s", exc)
    return result


async def get_title_cache() -> dict:
    global _TITLE_CACHE, _TITLE_CACHE_TIME
    now = time.monotonic()
    if _TITLE_CACHE and (now - _TITLE_CACHE_TIME) < _TITLE_CACHE_TTL:
        return _TITLE_CACHE
    _TITLE_CACHE = await asyncio.to_thread(_build_title_cache_sync)
    _TITLE_CACHE_TIME = now
    return _TITLE_CACHE


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
        _TITLE_CACHE_TIME = 0.0   # invalidate cache on new file
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
                        raise ValueError(f"TMDB status {r.status}")
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
            origin_country = detail.get('origin_country', [])
            original_language = detail.get('original_language', '')

            if 'animation' in genres or 'anime' in genres:
                return 'anime'
            if mt == 'tv' and ('JP' in origin_country or original_language == 'ja'):
                return 'anime'
            if mt == 'tv':
                return 'series'
            return 'movie'

        except Exception as tmdb_err:
            logger.debug(f"TMDB classify error for '{title}': {tmdb_err}")
            return 'series' if has_season else 'movie'

    except Exception as e:
        logger.error(f"_classify_file error: {e}")
        return 'movie'


def normalize_query(query: str) -> str:
    q = query.strip()
    # Merge "s01 e05" → "s01e05"
    q = re.sub(r'\b(s\d{1,2})\s+(e\d{1,3})\b', r'\1\2', q, flags=re.IGNORECASE)
    # "season N episode M" → "sNNeMM"
    q = re.sub(r'\bseason\s*(\d{1,2})\s+episode\s*(\d{1,3})\b',
               lambda m: f"s{int(m.group(1)):02d}e{int(m.group(2)):02d}", q, flags=re.IGNORECASE)
    # "season N ep M" → "sNNeMM"
    q = re.sub(r'\bseason\s*(\d{1,2})\s+ep\s*(\d{1,3})\b',
               lambda m: f"s{int(m.group(1)):02d}e{int(m.group(2)):02d}", q, flags=re.IGNORECASE)
    # "season N all/complete" → "sNN"
    q = re.sub(r'\bseason\s*(\d{1,2})\s+(?:all\s+episodes?|complete|episodes?)\b',
               lambda m: f"s{int(m.group(1)):02d}", q, flags=re.IGNORECASE)
    # bare "season N" → "sNN"
    q = re.sub(r'\bseason\s*(\d{1,2})\b',
               lambda m: f"s{int(m.group(1)):02d}", q, flags=re.IGNORECASE)
    # bare "episode M" / "ep M" → "eMM"
    q = re.sub(r'\b(?:episode|ep)\s*(\d{1,3})\b',
               lambda m: f"e{int(m.group(1)):02d}", q, flags=re.IGNORECASE)
    # drop trailing filler
    q = re.sub(r'\b(?:all\s+episodes?|complete\s+series|full\s+season)\b', '', q, flags=re.IGNORECASE)
    return ' '.join(q.split()).strip()


def clean_query(query, filter_words):
    if not query:
        return query
    query = re.sub(r"[^\w\s]", " ", query)
    if filter_words:
        pattern = r'\b(?:' + '|'.join(map(re.escape, filter_words)) + r')\b'
        query = re.sub(pattern, '', query, flags=re.IGNORECASE)
    return ' '.join(query.split()).strip()


# ── Ranking helpers ───────────────────────────────────────────────────────────
RESOLUTION_RANK = {"2160p": 200, "4k": 200, "1080p": 150, "720p": 100, "480p": 50, "360p": 20}
QUALITY_RANK = {
    "bluray": 100, "bdrip": 95, "remux": 98,
    "web-dl": 90, "webdl": 90, "webrip": 85,
    "hdrip": 75, "hd": 70, "dvdrip": 65, "cam": 10, "ts": 15,
}


def extract_year(text: str):
    m = re.findall(r"(19\d{2}|20\d{2})", text)
    return max(map(int, m)) if m else 0


def extract_season_episode(text: str):
    season = episode = 0
    sm = re.search(r"s(\d{1,2})", text, re.I)
    em = re.search(r"e(\d{1,2})", text, re.I)
    if sm:
        season = int(sm.group(1))
    if em:
        episode = int(em.group(1))
    return season, episode


def extract_quality(text: str):
    t = text.lower()
    for q, s in QUALITY_RANK.items():
        if q in t:
            return s
    return 0


def extract_resolution(text: str) -> int:
    t = text.lower()
    for r, s in RESOLUTION_RANK.items():
        if r in t:
            return s
    return 0


def has_multi_audio(text: str):
    t = text.lower()
    return any(x in t for x in ["dual", "multi", "multi-audio", "dual-audio"])


def normalize_title(text: str) -> str:
    text = text.lower()
    text = re.sub(r"(19\d{2}|20\d{2})", "", text)
    text = re.sub(r"s\d{1,2}e\d{1,2}", "", text)
    text = re.sub(r"s\d{1,2}", "", text)
    text = re.sub(
        r"\b(480p|720p|1080p|2160p|4k|hdr|bluray|bdrip|remux|web[\-\s]?dl|webrip|"
        r"hdrip|dvdrip|cam|ts|x264|x265|hevc|aac|ac3|dts|flac|mp3|ddp5|"
        r"hindi|english|tamil|telugu|malayalam|kannada|punjabi|"
        r"dual|multi|esub|subbed|dubbed)\b", "", text
    )
    return re.sub(r"[^a-z0-9 ]", "", text).strip()


def _title_words(text: str) -> list:
    return [w for w in normalize_title(text).split() if w]


def _word_match_score(query_words: list, file_text: str) -> int:
    if not query_words:
        return 0
    file_words = _title_words(file_text)
    if not file_words:
        return 0
    matched = sum(1 for w in query_words if w in file_words)
    base = int((matched / len(query_words)) * 1000)
    if matched == len(query_words):
        base += 200
    file_str = " ".join(file_words)
    query_str = " ".join(query_words)
    if file_str.startswith(query_str):
        base += 300
    elif query_str in file_str:
        base += 100
    return base


def rank_results(query: str, files: list) -> list:
    q_words = _title_words(query)
    q_norm = normalize_title(query)

    def score(f):
        text = f.get("caption") or f.get("file_name", "")
        t_norm = normalize_title(text)
        word_score = _word_match_score(q_words, text)
        exact_bonus = 2000 if t_norm == q_norm else 0
        quality = extract_quality(text)
        resolution = extract_resolution(text)
        year = extract_year(text) * 2
        audio = 50 if has_multi_audio(text) else 0
        season, episode = extract_season_episode(text)
        return (
            exact_bonus + word_score
            + quality * 10 + resolution * 5
            + year + audio
            + season * 10 - episode
        )

    return sorted(files, key=score, reverse=True)


def _do_search(filter_dict) -> list:
    """Sync helper: query both DBs and merge results. Runs in a thread."""
    result_map = {}
    for doc in collection.find(filter_dict):
        result_map[doc['_id']] = doc
    if is_second_db_configured():
        for doc in second_collection.find(filter_dict):
            result_map.setdefault(doc['_id'], doc)
    return list(result_map.values())


async def get_search_results(query, max_results=MAX_BTN, offset=0, lang=None):
    """
    Stage 1: exact/regex search in DB.
    Runs the blocking MongoDB query in a thread pool to avoid blocking the event loop.
    """
    query = str(query).strip()
    query = normalize_query(query)
    filter_words = await get_filter_words()
    query = clean_query(query, filter_words)

    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.+\-_])' + re.escape(query) + r'(\b|[\.+\-_])'
    else:
        words = query.split()
        # All words must be present, word-boundary aware, order-independent
        lookaheads = ''.join(r'(?=.*\b' + re.escape(w) + r'\b)' for w in words)
        raw_pattern = lookaheads + '.*'

    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except Exception:
        try:
            regex = re.compile(re.escape(query), flags=re.IGNORECASE)
        except Exception:
            regex = query

    if USE_CAPTION_FILTER:
        filter_dict = {'$or': [{'file_name': regex}, {'caption': regex}]}
    else:
        filter_dict = {'file_name': regex}

    # Offload blocking MongoDB call to thread pool
    results = await asyncio.to_thread(_do_search, filter_dict)

    if lang:
        lang_files = [f for f in results if lang in f['file_name'].lower()]
        total_results = len(lang_files)
        files = lang_files[offset:offset + max_results]
    else:
        ranked = rank_results(query, results)
        total_results = len(ranked)
        files = ranked[offset:offset + max_results]

    next_offset = offset + max_results
    if next_offset >= total_results:
        next_offset = ''

    return files, next_offset, total_results


async def delete_files(query):
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.+\-_])' + query + r'(\b|[\.+\-_])'
    else:
        raw_pattern = query.replace(' ', r'.*[\s\.+\-_]')

    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except Exception:
        regex = query

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
