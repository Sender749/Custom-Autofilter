import logging
from struct import pack
import re
import base64
from pyrogram.file_id import FileId
from pymongo import MongoClient, TEXT
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError, OperationFailure
from info import USE_CAPTION_FILTER, FILES_DATABASE_URL, SECOND_FILES_DATABASE_URL, DATABASE_NAME, COLLECTION_NAME, MAX_BTN

logger = logging.getLogger(__name__)
async_client = AsyncIOMotorClient(FILES_DATABASE_URL)
async_db = async_client[DATABASE_NAME]
filter_words_collection = async_db["filter_words"]

client = MongoClient(FILES_DATABASE_URL)
db = client[DATABASE_NAME]
collection = db[COLLECTION_NAME]
second_collection = None  
try:
    collection.create_index([("file_name", TEXT)])
except OperationFailure as e:
    if 'quota' in str(e).lower():
        if not SECOND_FILES_DATABASE_URL:
            logger.error(f'your FILES_DATABASE_URL is already full, add SECOND_FILES_DATABASE_URL')
        else:
            logger.info('FILES_DATABASE_URL is full, now using SECOND_FILES_DATABASE_URL')
    else:
        logger.exception(e)

if SECOND_FILES_DATABASE_URL:
    second_client = MongoClient(SECOND_FILES_DATABASE_URL)
    second_db = second_client[DATABASE_NAME]
    second_collection = second_db[COLLECTION_NAME]
    second_collection.create_index([("file_name", TEXT)])

def is_second_db_configured() -> bool:
    return bool(SECOND_FILES_DATABASE_URL and 'second_collection' in globals() and second_collection is not None)

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

async def save_file(media):
    file_id = unpack_new_file_id(media.file_id)
    file_name = re.sub(r"@\w+|(_|\-|\.|\+)", " ", str(media.file_name))
    file_caption = re.sub(r"@\w+|(_|\-|\.|\+)", " ", str(media.caption))
    
    # Auto-classify using TMDB metadata (async, best-effort)
    category = await _classify_file(file_name, file_caption)
    
    document = {
        '_id': file_id,
        'file_name': file_name,
        'file_size': media.file_size,
        'caption': file_caption,
        'category': category,  # 'movie' | 'series' | 'anime'
    }
    
    try:
        collection.insert_one(document)
        logger.info(f'Saved [{category}] - {file_name}')
        return 'suc'
    except DuplicateKeyError:
        logger.warning(f'Already Saved - {file_name}')
        return 'dup'
    except OperationFailure:
        if SECOND_FILES_DATABASE_URL:
            try:
                second_collection.insert_one(document)
                logger.info(f'Saved to 2nd db [{category}] - {file_name}')
                return 'suc'
            except DuplicateKeyError:
                logger.warning(f'Already Saved in 2nd db - {file_name}')
                return 'dup'
        else:
            logger.error(f'your FILES_DATABASE_URL is already full, add SECOND_FILES_DATABASE_URL')
            return 'err'


async def _classify_file(file_name: str, caption: str) -> str:
    """
    Smart TMDB-based auto-classification.
    Decision order:
      1. Anime keywords in filename → 'anime'
      2. TMDB genres contain Animation → 'anime' (covers animated movies too)
      3. TMDB origin country JP or language 'ja' + TV → 'anime'
      4. TMDB media_type = 'tv' → 'series'
      5. S01E01 / Season pattern in filename → 'series'
      6. Default → 'movie'
    Results are best-effort; failures fall back to filename heuristics.
    """
    import re as _re
    try:
        # Step 1: explicit anime keywords in filename
        _ANIME_KW = _re.compile(
            r'\b(anime|hentai|ova|oad|ona|manhwa|manhua|donghua|'
            r'shonen|seinen|shoujo|isekai|mecha|yaoi|yuri)\b|'
            r'[\u3040-\u30FF\u4E00-\u9FFF]', _re.IGNORECASE)
        text = f"{caption} {file_name}"
        if _ANIME_KW.search(text):
            return 'anime'

        # Step 2: Extract clean title + year for TMDB query
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
        # check S/E before cleaning
        has_season = bool(_SE2.search(raw) or _S2.search(raw))
        raw = _JUNK2.sub('', raw)
        raw = _re.sub(r'\s{2,}', ' ', raw).strip()

        year_m = _YEAR2.search(raw)
        year = year_m.group(0) if year_m else ''
        title = _YEAR2.sub('', raw).strip()
        if not title:
            return 'series' if has_season else 'movie'

        # Step 3: TMDB lookup with caching (reuse module-level cache if available)
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
                # Fetch full details for genres, origin_country, original_language
                async with sess.get(
                    f'https://api.themoviedb.org/3/{mt}/{iid}',
                    params={'api_key': TMDB_API_KEY}
                ) as dr:
                    detail = await dr.json() if dr.status == 200 else item

            genres = [g['name'].lower() for g in detail.get('genres', [])]
            origin_country = detail.get('origin_country', [])
            original_language = detail.get('original_language', '')

            # Animation genre → always anime (movies + TV)
            if 'animation' in genres or 'anime' in genres:
                return 'anime'

            # Japanese origin TV show → anime
            if mt == 'tv' and ('JP' in origin_country or original_language == 'ja'):
                return 'anime'

            # TV type → series
            if mt == 'tv':
                return 'series'

            # Movie type → movie
            return 'movie'

        except Exception as tmdb_err:
            logger.debug(f"TMDB classify error for '{title}': {tmdb_err}")
            # Fallback to filename heuristics
            return 'series' if has_season else 'movie'

    except Exception as e:
        logger.error(f"_classify_file error: {e}")
        return 'movie'

def clean_query(query, filter_words):
    if not query:
        return query
    query = re.sub(r"[^\w\s]", " ", query)
    if filter_words:
        pattern = r'\b(?:' + '|'.join(map(re.escape, filter_words)) + r')\b'
        query = re.sub(pattern, '', query, flags=re.IGNORECASE)
    return ' '.join(query.split()).strip()

async def get_search_results(query, max_results=MAX_BTN, offset=0, lang=None):
    query = str(query).strip()
    filter_words = await get_filter_words()
    query = clean_query(query, filter_words)
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.\+\-_])' + query + r'(\b|[\.\+\-_])'
    else:
        raw_pattern = query.replace(' ', r'.*[\s\.\+\-_]')
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except:
        regex = query

    if USE_CAPTION_FILTER:
        filter = {'$or': [{'file_name': regex}, {'caption': regex}]}
    else:
        filter = {'file_name': regex}

    result_map = {}
    for doc in collection.find(filter):
        result_map[doc['_id']] = doc
    if SECOND_FILES_DATABASE_URL:
        for doc in second_collection.find(filter):
            result_map.setdefault(doc['_id'], doc)
    results = list(result_map.values())

    if lang:
        lang_files = [file for file in results if lang in file['file_name'].lower()]
        total_results = len(lang_files)
        files = lang_files[offset:offset + max_results]
        next_offset = offset + max_results
        if next_offset >= total_results:
            next_offset = ''
        return files, next_offset, total_results

    ranked = rank_results(query, results)
    total_results = len(ranked)
    files = ranked[offset:offset + max_results]
    next_offset = offset + max_results
    if next_offset >= total_results:
        next_offset = ''
    return files, next_offset, total_results

QUALITY_RANK = {
    "bluray": 100,
    "bdrip": 95,
    "remux": 98,
    "web-dl": 90,
    "webdl": 90,
    "webrip": 85,
    "hdrip": 75,
    "hd": 70,
    "dvdrip": 65,
    "cam": 10,
    "ts": 15
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
    for q, score in QUALITY_RANK.items():
        if q in t:
            return score
    return 0

def has_multi_audio(text: str):
    t = text.lower()
    return any(x in t for x in ["dual", "multi", "multi-audio", "dual-audio"])

def normalize_title(text: str):
    text = text.lower()
    text = re.sub(r"(19\d{2}|20\d{2})", "", text)
    text = re.sub(r"s\d{1,2}e\d{1,2}", "", text)
    text = re.sub(r"s\d{1,2}", "", text)
    return re.sub(r"[^a-z0-9 ]", "", text).strip()
    
def rank_results(query, files):
    q_norm = normalize_title(query)
    def score(f):
        text = f.get("caption") or f.get("file_name", "")
        t_norm = normalize_title(text)
        exact = 1000 if t_norm == q_norm else 0
        starts = 300 if t_norm.startswith(q_norm) else 0
        contains = 150 if q_norm in t_norm else 0
        year = extract_year(text) * 5
        quality = extract_quality(text)
        season, episode = extract_season_episode(text)
        season_score = season * 50
        episode_score = -episode
        audio = 200 if has_multi_audio(text) else 0
        return exact + starts + contains + year + quality + season_score + episode_score + audio
    return sorted(files, key=score, reverse=True)

async def delete_files(query):
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.\+\-_])' + query + r'(\b|[\.\+\-_])'
    else:
        raw_pattern = query.replace(' ', r'.*[\s\.\+\-_]')
    
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except:
        regex = query
        
    filter = {'file_name': regex}
    
    result1 = collection.delete_many(filter)
    
    result2 = None
    if SECOND_FILES_DATABASE_URL:
        result2 = second_collection.delete_many(filter)
    
    total_deleted = result1.deleted_count
    if result2:
        total_deleted += result2.deleted_count
    
    return total_deleted

async def get_file_details(query):
    file_details = collection.find_one({'_id': query})
    if not file_details and SECOND_FILES_DATABASE_URL:
        file_details = second_collection.find_one({'_id': query})
    return file_details

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
