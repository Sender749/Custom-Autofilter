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
    
    document = {
        '_id': file_id,
        'file_name': file_name,
        'file_size': media.file_size,
        'caption': file_caption
    }
    
    try:
        collection.insert_one(document)
        logger.info(f'Saved - {file_name}')
        return 'suc'
    except DuplicateKeyError:
        logger.warning(f'Already Saved - {file_name}')
        return 'dup'
    except OperationFailure:
        if SECOND_FILES_DATABASE_URL:
            try:
                second_collection.insert_one(document)
                logger.info(f'Saved to 2nd db - {file_name}')
                return 'suc'
            except DuplicateKeyError:
                logger.warning(f'Already Saved in 2nd db - {file_name}')
                return 'dup'
        else:
            logger.error(f'your FILES_DATABASE_URL is already full, add SECOND_FILES_DATABASE_URL')
            return 'err'

def clean_query(query, filter_words):
    if not query or not filter_words:
        return query
    pattern = r'\b(?:' + '|'.join(map(re.escape, filter_words)) + r')\b'
    cleaned = re.sub(pattern, '', query, flags=re.IGNORECASE)
    return ' '.join(cleaned.split()).strip()

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

    cursor = collection.find(filter)
    results = [doc for doc in cursor]

    if SECOND_FILES_DATABASE_URL:
        cursor2 = second_collection.find(filter)
        results.extend([doc for doc in cursor2])

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

async def smart_search(query, limit=MAX_BTN):
    query = str(query).strip()
    filter_words = await get_filter_words()
    clean_q = clean_query(query, filter_words)
    if not clean_q:
        return [], []
    exact_regex = re.compile(
        r'(^|[\s\.\+\-_])' + re.escape(clean_q) + r'($|[\s\.\+\-_])',
        flags=re.IGNORECASE
    )
    loose_regex = re.compile(
        clean_q.replace(" ", ".*"),
        flags=re.IGNORECASE
    )
    base_filter_exact = {'file_name': exact_regex}
    base_filter_loose = {'file_name': loose_regex}
    exact = list(collection.find(base_filter_exact))
    loose = list(collection.find(base_filter_loose))
    if SECOND_FILES_DATABASE_URL:
        exact += list(second_collection.find(base_filter_exact))
        loose += list(second_collection.find(base_filter_loose))
    exact_ids = {f['_id'] for f in exact}
    related = [f for f in loose if f['_id'] not in exact_ids]
    exact_ranked = rank_results(clean_q, exact)
    related_ranked = rank_results(clean_q, related)
    return exact_ranked[:limit], related_ranked[:limit]

