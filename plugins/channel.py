import re
import logging
import asyncio
from datetime import datetime
from collections import defaultdict
from plugins.helper.Imdbposter import get_movie_detailsx, fetch_image, get_movie_details
from database.users_chats_db import db
from pyrogram import Client, filters, enums
from info import CHANNELS, MOVIE_UPDATE_CHANNEL, LINK_PREVIEW, ABOVE_PREVIEW, LANDSCAPE_POSTER, TMDB_POSTER, FETCH_MOVIE_UPDATE
from Script import script
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp
from pymongo.errors import PyMongoError, DuplicateKeyError
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Precomputed sets for faster lookups
IGNORE_WORDS = {
    "rarbg", "dub", "sub", "sample", "mkv", "aac", "combined",
    "action", "adventure", "animation", "biography", "comedy", "crime", 
    "documentary", "drama", "family", "fantasy", "film-noir", "history", 
    "horror", "music", "musical", "mystery", "romance", "sci-fi", "sport", 
    "thriller", "war", "western", "hdcam", "hdtc", "camrip", "ts", "tc", 
    "telesync", "dvdscr", "dvdrip", "predvd", "webrip", "web-dl", "tvrip", 
    "hdtv", "web dl", "webdl", "bluray", "brrip", "bdrip", "360p", "480p", 
    "720p", "1080p", "2160p", "4k", "1440p", "540p", "240p", "140p", "hevc", 
    "hdrip", "hin", "hindi", "tam", "tamil", "kan", "kannada", "tel", "telugu", 
    "mal", "malayalam", "eng", "english", "pun", "punjabi", "ben", "bengali", 
    "mar", "marathi", "guj", "gujarati", "urd", "urdu", "kor", "korean", "jpn", 
    "japanese", "nf", "netflix", "sonyliv", "sony", "sliv", "amzn", "prime", 
    "primevideo", "hotstar", "zee5", "jio", "jhs", "aha", "hbo", "paramount", 
    "apple", "hoichoi", "sunnxt", "viki", "PrivateMovieZ", "toonworld4all", "themoviesboss", "1tamilmv", "tamilblasters",
    "1tamilblasters", "skymovieshd", "extraflix", "hdm2", "moviesmod", "hdhub4u", "mkvcinemas", "primefix", "join", "www", "villa", "tg", "original"
}

# Constants
CAPTION_LANGUAGES = {
    "hin": "Hindi", "hindi": "Hindi",
    "tam": "Tamil", "tamil": "Tamil",
    "kan": "Kannada", "kannada": "Kannada",
    "tel": "Telugu", "telugu": "Telugu",
    "mal": "Malayalam", "malayalam": "Malayalam",
    "eng": "English", "english": "English",
    "pun": "Punjabi", "punjabi": "Punjabi",
    "ben": "Bengali", "bengali": "Bengali",
    "mar": "Marathi", "marathi": "Marathi",
    "guj": "Gujarati", "gujarati": "Gujarati",
    "urd": "Urdu", "urdu": "Urdu",
    "kor": "Korean", "korean": "Korean",
    "jpn": "Japanese", "japanese": "Japanese",
}

OTT_PLATFORMS = {
    "nf": "Netflix", "netflix": "Netflix",
    "sonyliv": "SonyLiv", "sony": "SonyLiv", "sliv": "SonyLiv",
    "amzn": "Amazon Prime Video", "prime": "Amazon Prime Video", "primevideo": "Amazon Prime Video",
    "hotstar": "Disney+ Hotstar", "zee5": "Zee5",
    "jio": "JioHotstar", "jhs": "JioHotstar",
    "aha": "Aha", "hbo": "HBO Max", "paramount": "Paramount+",
    "apple": "Apple TV+", "hoichoi": "Hoichoi", "sunnxt": "Sun NXT", "viki": "Viki"
}

STANDARD_GENRES = {
    'Action', 'Adventure', 'Animation', 'Biography', 'Comedy', 'Crime', 'Documentary',
    'Drama', 'Family', 'Fantasy', 'Film-Noir', 'History', 'Horror', 'Music',
    'Musical', 'Mystery', 'Romance', 'Sci-Fi', 'Sport', 'Thriller', 'War', 'Western'
}

# Precompiled regex patterns
CLEAN_PATTERN = re.compile(r'@[^ \n\r\t\.,:;!?()\[\]{}<>\\/"\'=_%]+|\bwww\.[^\s\]\)]+|\([\@^]+\)|\[[\@^]+\]')
NORMALIZE_PATTERN = re.compile(r"[._]+|[()\[\]{}:;'–!,.?_]")
QUALITY_PATTERN = re.compile(
    r"\b(?:HDCam|HDTC|CamRip|TS|TC|TeleSync|DVDScr|DVDRip|PreDVD|"
    r"WEBRip|WEB-DL|TVRip|HDTV|WEB DL|WebDl|BluRay|BRRip|BDRip|"
    r"360p|480p|720p|1080p|2160p|4K|1440p|540p|240p|140p|HEVC|HDRip)\b", 
    re.IGNORECASE
)
# Separate patterns: source/quality vs resolution
SOURCE_PATTERN = re.compile(
    r"\b(?:HDCam|HDTC|CamRip|TS|TC|TeleSync|DVDScr|DVDRip|PreDVD|"
    r"WEBRip|WEB-DL|WEB DL|WebDl|TVRip|HDTV|BluRay|BRRip|BDRip|HDRip|HEVC)\b",
    re.IGNORECASE
)
RESOLUTION_PATTERN = re.compile(
    r"\b(?:360p|480p|540p|720p|960p|1080p|1440p|2160p|4K|240p|140p)\b",
    re.IGNORECASE
)
YEAR_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?![A-Za-z0-9])")
RANGE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,2})\s*(?:to|-)\s*(?:E(?:p(?:isode)?)?)?0*(\d{1,2})',re.IGNORECASE)
SINGLE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,3})', re.IGNORECASE)
NAMED_REGEX = re.compile(r'Season\s*0*(\d{1,2})[\s\-,:]*Ep(?:isode)?\s*0*(\d{1,3})', re.IGNORECASE)
EP_ONLY_RANGE = re.compile(r'\b(?:EP|Episode)0*(\d{1,3})\s*-\s*0*(\d{1,3})\b',re.IGNORECASE)


media_filter = filters.document | filters.video | filters.audio
locks = defaultdict(asyncio.Lock)
pending_updates = {}

@Client.on_message(filters.chat(CHANNELS) & media_filter)
async def media_handler(bot, message):
    media = next(
        (getattr(message, ft) for ft in ("document", "video", "audio")
         if getattr(message, ft, None)),
        None
    )
    if not media:
        return

    media.file_type = next(ft for ft in ("document", "video", "audio") if hasattr(message, ft))
    media.caption = message.caption or ""
    await save_file(media)

@Client.on_message(filters.chat(FETCH_MOVIE_UPDATE) & media_filter)
async def movie_update_fetcher(bot, message):
    media = next(
        (getattr(message, ft) for ft in ("document", "video", "audio")
         if getattr(message, ft, None)),
        None
    )
    if not media:
        return
    media.file_type = next(
        ft for ft in ("document", "video", "audio")
        if hasattr(message, ft)
    )
    media.caption = message.caption or ""
    try:
        if await db.movie_update_status(bot.me.id):
            await process_and_send_update(
                bot,
                media.file_name,
                media.caption,
                source_chat=message.chat
            )
    except Exception:
        logger.exception("Movie update fetch failed")

def clean_mentions_links(text: str) -> str:
    return CLEAN_PATTERN.sub("", text or "").strip()

def normalize(s: str) -> str:
    s = NORMALIZE_PATTERN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def remove_ignored_words(text: str) -> str:
    IGNORE_WORDS_LOWER = {w.lower() for w in IGNORE_WORDS}
    return " ".join(word for word in text.split() if word.lower() not in IGNORE_WORDS_LOWER)

def get_qualities(text: str) -> str:
    qualities = QUALITY_PATTERN.findall(text)
    return ", ".join(qualities) if qualities else "N/A"

def get_source_quality(text: str) -> str:
    """Return source/format tags only: WEBRip, BluRay, HDRip, etc."""
    found = SOURCE_PATTERN.findall(text)
    # Deduplicate while preserving order, normalise case
    seen = set()
    result = []
    for q in found:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            result.append(q)
    return ", ".join(result) if result else "N/A"

def get_resolution(text: str) -> str:
    """Return resolution tags only: 720p, 1080p, 4K, etc."""
    found = RESOLUTION_PATTERN.findall(text)
    seen = set()
    result = []
    for r in found:
        key = r.lower()
        if key not in seen:
            seen.add(key)
            result.append(r.upper() if r.lower() == "4k" else r)
    return ", ".join(result) if result else "N/A"

def extract_ott_platform(text: str) -> str:
    text = text.lower()
    platforms = {plat for key, plat in OTT_PLATFORMS.items() if key in text}
    return " | ".join(platforms) if platforms else "N/A"

def extract_season_episode(filename: str) -> Tuple[Optional[int], Optional[str]]:
    if m := EP_ONLY_RANGE.search(filename):
        return 1, f"{int(m.group(1))}-{int(m.group(2))}"
    for pattern in (RANGE_REGEX, SINGLE_REGEX, NAMED_REGEX):
        if m := pattern.search(filename):
            season = int(m.group(1))
            if pattern == RANGE_REGEX:
                ep = f"{m.group(2)}-{m.group(3)}"
            else:
                ep = m.group(2)
            return season, ep
    return None, None

def schedule_update(bot, base_name, delay=5):
    if handle := pending_updates.get(base_name):
        if not handle.cancelled():
            handle.cancel()
    
    loop = asyncio.get_event_loop()
    pending_updates[base_name] = loop.call_later(
        delay,
        lambda: asyncio.create_task(update_movie_message(bot, base_name))
    )

def extract_media_info(filename: str, caption: str):
    filename = normalize(clean_mentions_links(filename).title())
    caption_clean = clean_mentions_links(caption).lower() if caption else ""
    unified = f"{caption_clean} {filename.lower()}".strip()

    season = episode = year = None
    tag = "#MOVIE"
    processed_raw = base_raw = filename
    quality = get_source_quality(caption_clean) or get_source_quality(filename.lower()) or "N/A"
    resolution = get_resolution(caption_clean) or get_resolution(filename.lower()) or "N/A"
    ott_platform = extract_ott_platform(f"{filename} {caption_clean}")
    lang_keys = {k for k in CAPTION_LANGUAGES if k in caption_clean or k in filename.lower()}
    language = ", ".join(sorted({CAPTION_LANGUAGES[k] for k in lang_keys})) if lang_keys else "N/A"
    season, episode = extract_season_episode(filename)
    if season is not None:
        tag = "#SERIES"
        if m := (RANGE_REGEX.search(filename) or SINGLE_REGEX.search(filename) or NAMED_REGEX.search(filename) or EP_ONLY_RANGE.search(filename)):
            match_str = m.group(0)
            start_idx = filename.lower().find(match_str.lower())
            end_idx = start_idx + len(match_str)
            processed_raw = filename[:end_idx]
            base_raw = filename[:start_idx]
            if year_match := YEAR_PATTERN.search(filename.lower()[end_idx:]):
                y = year_match.group(0)
                yi = filename.lower().find(y, end_idx)
                if yi != -1:
                    processed_raw = filename[:yi+4]
                    base_raw += f" {y}"
    else:
        if year_match := YEAR_PATTERN.search(unified):
            year = year_match.group(0)
            year_idx = filename.lower().find(year.lower())
            if year_idx != -1:
                processed_raw = filename[:year_idx + 4]
                base_raw = processed_raw
        else:
            if qual_match := QUALITY_PATTERN.search(unified):
                qual_str = qual_match.group(0)
                qual_idx = filename.lower().find(qual_str.lower())
                if qual_idx != -1:
                    processed_raw = filename[:qual_idx]
                    base_raw = processed_raw

    base_name = normalize(remove_ignored_words(normalize(base_raw)))
    if year and year not in base_name:
        base_name += f" {year}"

    if base_name.endswith(")"):
        base_name = re.sub(r"\s+\(\d{4}\)$", "", base_name)
        if year:
            base_name += f" ({year})"
    return {
        "processed": normalize(processed_raw),
        "base_name": base_name,
        "tag": tag,
        "season": season,
        "episode": episode,
        "year": year,
        "quality": quality,
        "resolution": resolution,
        "ott_platform": ott_platform,
        "language": language
    }

async def process_and_send_update(bot, filename, caption, source_chat):
    try:
        media_info = extract_media_info(filename, caption)
        base_name = media_info["base_name"]
        processed = media_info["processed"]

        lock = locks[base_name]
        async with lock:
            await _process_with_lock(bot, filename, caption, media_info, base_name, processed, source_chat)
    except PyMongoError as e:
        logger.error("Database error: %s", e)
    except Exception as e:
        logger.exception("Processing failed: %s", e)

async def _process_with_lock(bot, filename, caption, media_info, base_name, processed, source_chat):
    if not hasattr(db, 'movie_updates'):
        db.movie_updates = db.db.movie_updates
    movie_doc = await db.movie_updates.find_one({"_id": base_name})

    if source_chat.username:
        channel_link = f"https://t.me/{source_chat.username}"
    else:
        channel_link = f"https://t.me/c/{str(source_chat.id)[4:]}"

    file_data = {
        "filename": filename,
        "processed": processed,
        "quality": media_info["quality"],
        "resolution": media_info["resolution"],
        "language": media_info["language"],
        "ott_platform": media_info["ott_platform"],
        "timestamp": datetime.now(),
        "tag": media_info["tag"],
        "season": media_info["season"],
        "episode": media_info["episode"],
        "source_channel": channel_link
    }

    if not movie_doc:
        details: dict = {}
        used_tmdb = False

        if TMDB_POSTER:
            tmdb_result = await get_movie_detailsx(base_name, year=media_info.get("year"))
            if tmdb_result and not tmdb_result.get("error"):
                details = tmdb_result
                used_tmdb = True
            else:
                logger.info("TMDB failed, trying OMDb for '%s'", base_name)
                details = await get_movie_details(base_name, file=filename) or {}
        else:
            details = await get_movie_details(base_name, file=filename) or {}

        if not details:
            logger.warning("All metadata sources failed for '%s' — sending without info", base_name)

        # Genre normalisation
        # Both TMDB and OMDb return comma-separated genre strings already.
        # We accept them as-is (no STANDARD_GENRES filtering) because OMDb
        # uses the exact same genre names as IMDb.
        raw_genres = details.get("genres", "") or ""
        if isinstance(raw_genres, list):
            genres = ", ".join(str(g) for g in raw_genres if g) or "N/A"
        elif isinstance(raw_genres, str) and raw_genres and raw_genres != "N/A":
            genres = ", ".join(g.strip() for g in raw_genres.split(",") if g.strip()) or "N/A"
        else:
            genres = "N/A"

        # Poster: prefer landscape backdrop (TMDB only) when enabled
        if used_tmdb and LANDSCAPE_POSTER and details.get("backdrop_url"):
            poster_url = details["backdrop_url"]
        else:
            poster_url = details.get("poster_url")

        # Info URL: both sources now expose a unified "url" key pointing to
        # the best available page (IMDb if known, else TMDB page)
        info_url = details.get("url") or details.get("tmdb_url") or ""

        movie_doc = {
            "_id": base_name,
            "files": [file_data],
            "poster_url": poster_url,
            "genres": genres,
            "rating": details.get("rating", "N/A"),
            "imdb_url": info_url,
            "year": media_info["year"] or details.get("year"),
            "tag": media_info["tag"],
            "ott_platform": media_info["ott_platform"],
            "message_id": None,
            "is_photo": False
        }
        try:
            await db.movie_updates.insert_one(movie_doc)
            await send_movie_update(bot, base_name)
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
        except DuplicateKeyError:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if movie_doc:
                if any(f["filename"] == filename for f in movie_doc["files"]):
                    return
                await db.movie_updates.update_one(
                    {"_id": base_name},
                    {"$push": {"files": file_data}}
                )
                movie_doc["files"].append(file_data)
                schedule_update(bot, base_name)
    else:
        if any(f["filename"] == filename for f in movie_doc["files"]):
            return
        await db.movie_updates.update_one(
            {"_id": base_name},
            {"$push": {"files": file_data}}
        )
        movie_doc["files"].append(file_data)
        schedule_update(bot, base_name)

async def send_movie_update(bot, base_name):
    max_retries = 3
    base_delay = 5
    for attempt in range(max_retries):
        try:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if not movie_doc:
                return None

            text = generate_movie_message(movie_doc, base_name)
            # Row 1: one button per source channel ("✨ Get Direct File ✨")
            channels = set()
            for f in movie_doc["files"]:
                link = f.get("source_channel")
                if link:
                    channels.add(link)
            buttons = [
                [InlineKeyboardButton("✨ ɢᴇᴛ ᴅɪʀᴇᴄᴛ ꜰɪʟᴇ ✨", url=link)]
                for link in sorted(channels)
            ]
            # Row 2: Watch & Download
            buttons.append([InlineKeyboardButton(
                "📥 Watch & Download",
                url=f"https://t.me/{temp.U_NAME}?start=getfile-{base_name.replace(' ', '-')}"
            )])
            # Row 3: Viral Stuff
            buttons.append([InlineKeyboardButton(
                "♨️ Viral Stuff ♨️",
                url="https://t.me/Reload_adultbot"
            )])
            reply_markup = InlineKeyboardMarkup(buttons)
            poster_url = movie_doc.get("poster_url")
            resized_poster = None
            if poster_url and not LINK_PREVIEW:
                is_landscape = LANDSCAPE_POSTER and TMDB_POSTER and poster_url and "original" in poster_url
                size = (2560, 1440) if is_landscape else (853, 1280)
                resized_poster = await fetch_image(poster_url, size=size)

            if resized_poster:
                msg = await bot.send_photo(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    photo=resized_poster,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=enums.ParseMode.HTML
                )
                is_photo = True
            else:
                send_params = {
                    "chat_id": MOVIE_UPDATE_CHANNEL,
                    "text": text,
                    "reply_markup": reply_markup,
                    "parse_mode": enums.ParseMode.HTML
                }
                if poster_url and LINK_PREVIEW:
                    send_params["url"] = poster_url
                    send_params["invert_media"] = ABOVE_PREVIEW
                msg = await bot.send_message(**send_params)
                is_photo = False

            await db.movie_updates.update_one(
                {"_id": base_name},
                {"$set": {"message_id": msg.id, "is_photo": is_photo}}
            )
            return msg
        except FloodWait as e:
            wait_time = e.value + 2
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Failed to send movie update: {e}")
            break
    return None

async def update_movie_message(bot, base_name):
    try:
        movie_doc = await db.movie_updates.find_one({"_id": base_name})
        if not movie_doc:
            return

        text = generate_movie_message(movie_doc, base_name)
        channels = set()
        for f in movie_doc["files"]:
            link = f.get("source_channel")
            if link:
                channels.add(link)
        buttons = [
            [InlineKeyboardButton("✨ ɢᴇᴛ ᴅɪʀᴇᴄᴛ ꜰɪʟᴇ ✨", url=link)]
            for link in sorted(channels)
        ]
        buttons.append([InlineKeyboardButton(
            "📥 Watch & Download",
            url=f"https://t.me/{temp.U_NAME}?start=getfile-{base_name.replace(' ', '-')}"
        )])
        buttons.append([InlineKeyboardButton(
            "♨️ Viral Stuff ♨️",
            url="https://t.me/Reload_adultbot"
        )])
        reply_markup = InlineKeyboardMarkup(buttons)
        message_id = movie_doc.get("message_id")
        is_photo = movie_doc.get("is_photo", False)

        if not message_id:
            await send_movie_update(bot, base_name)
            return

        try:
            if is_photo:
                await bot.edit_message_caption(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=enums.ParseMode.HTML
                )
            else:
                await bot.edit_message_text(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=enums.ParseMode.HTML,
                    invert_media=ABOVE_PREVIEW,
                    disable_web_page_preview=not LINK_PREVIEW
                )
            return
        except (MessageIdInvalid, MessageNotModified):
            pass
        except Exception:
            try:
                await bot.delete_messages(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_ids=message_id
                )
                await db.movie_updates.update_one(
                    {"_id": base_name},
                    {"$set": {"message_id": None, "is_photo": False}}
                )
            except Exception:
                pass
            await send_movie_update(bot, base_name)
    except Exception as e:
        logger.error(f"Failed to update movie message: {e}")

def generate_movie_message(movie_doc, base_name):
    all_qualities   = set()
    all_resolutions = set()
    all_languages   = set()
    all_ott_platforms = set()
    all_tags        = set()
    episodes_by_season = defaultdict(set)

    for file in movie_doc["files"]:
        q = file.get("quality", "N/A")
        if q and q != "N/A":
            all_qualities.update(x.strip() for x in q.split(",") if x.strip())
        r = file.get("resolution", "N/A")
        if r and r != "N/A":
            all_resolutions.update(x.strip() for x in r.split(",") if x.strip())
        # If resolution not stored (old docs), try to extract it from quality field
        # (backward-compat: old docs stored everything in "quality")
        if not r or r == "N/A":
            old_q = file.get("quality", "N/A")
            if old_q and old_q != "N/A":
                res = get_resolution(old_q)
                if res != "N/A":
                    all_resolutions.update(x.strip() for x in res.split(",") if x.strip())
                src = get_source_quality(old_q)
                if src != "N/A":
                    all_qualities.update(x.strip() for x in src.split(",") if x.strip())
        if file.get("language", "N/A") != "N/A":
            all_languages.update(l.strip() for l in file["language"].split(",") if l.strip())
        if file.get("ott_platform", "N/A") != "N/A":
            platforms = [p.strip() for p in file["ott_platform"].split("|") if p.strip()]
            all_ott_platforms.update(platforms)
        if file.get("tag"):
            all_tags.add(file["tag"])
        if file.get("season") and file.get("episode"):
            episodes_by_season[file["season"]].add(file["episode"])

    primary_tag = "#SERIES" if "#SERIES" in all_tags else "#MOVIE"
    epi_block = ""
    if episodes_by_season:
        episode_lines = []
        for season, episodes in sorted(episodes_by_season.items(), key=lambda x: int(x[0])):
            singles = []
            ranges = []

            for ep in episodes:
                if "-" in ep:
                    ranges.append(ep)
                else:
                    try:
                        singles.append(int(ep))
                    except ValueError:
                        ranges.append(ep)

            singles.sort()
            collapsed = []
            start = end = None
            for num in singles:
                if start is None:
                    start = end = num
                elif num == end + 1:
                    end = num
                else:
                    collapsed.append(str(start) if start == end else f"{start}-{end}")
                    start = end = num
            if start is not None:
                collapsed.append(str(start) if start == end else f"{start}-{end}")

            all_ep_parts = collapsed + sorted(ranges, key=lambda s: int(s.split("-")[0]))
            episode_lines.append(f"S{int(season)}: {', '.join(all_ep_parts)}")

        epi_str = " ".join(episode_lines)
        if epi_str:
            epi_block = f"📺 ᴇᴘɪsᴏᴅᴇs : <b>{epi_str}</b>"

    genres = movie_doc.get("genres", "N/A")
    quality_str    = ", ".join(sorted(all_qualities))    if all_qualities    else "N/A"
    resolution_str = ", ".join(sorted(all_resolutions))  if all_resolutions  else "N/A"
    language_str   = ", ".join(sorted(all_languages))   if all_languages    else "N/A"
    ott_str        = ", ".join(sorted(all_ott_platforms)) if all_ott_platforms else "N/A"

    rating_raw = movie_doc.get("rating", "N/A")
    try:
        rating_display = f"{float(rating_raw):.1f}" if rating_raw and rating_raw != "N/A" else "N/A"
    except (ValueError, TypeError):
        rating_display = str(rating_raw) if rating_raw else "N/A"

    return script.MOVIE_UPDATE_NOTIFY_TXT.format(
        filename   = base_name,
        tag        = primary_tag,
        genres     = genres,
        ott        = ott_str,
        quality    = quality_str,
        resolution = resolution_str,
        language   = language_str,
        episodes   = epi_block,
        rating     = rating_display,
    )


# ---------------------------------------------------------------------------
# /m  —  Manual movie update command (admin only)
# ---------------------------------------------------------------------------
# Usage:
#   /m pushpa 2
#   /m suits s02
#   /m from s10
#   /m ironman 2003
#   /m dark knight 2008
#
# Parsing rules (all case-insensitive):
#   • Trailing year:       "ironman 2003"   → title="ironman",   year="2003"
#   • Trailing SXX:        "suits s02"      → title="suits",     season=2
#   • Trailing SXXY / SXX YEAR: both parsed
#   • Bare number after title treated as year if 4 digits, else ignored
# ---------------------------------------------------------------------------

_M_SEASON_RE  = re.compile(r'\bs(\d{1,2})\b$', re.IGNORECASE)
_M_YEAR_RE    = re.compile(r'\b((?:19|20)\d{2})\b')


def _parse_m_query(raw: str):
    """
    Parse the argument of /m and return (title, year, season).
    Examples
    --------
    "pushpa 2"        → ("pushpa 2", None, None)   # "2" is too short to be a year
    "ironman 2003"    → ("ironman", "2003", None)
    "suits s02"       → ("suits", None, 2)
    "from s10"        → ("from", None, 10)
    "dark knight 2008"→ ("dark knight", "2008", None)
    "suits s02 2011"  → ("suits", "2011", 2)
    """
    text = raw.strip()
    season = None
    year   = None

    # 1. Strip trailing season token  e.g. "s02" / "S10"
    m = _M_SEASON_RE.search(text)
    if m:
        season = int(m.group(1))
        text = text[:m.start()].strip()

    # 2. Strip trailing 4-digit year
    m = _M_YEAR_RE.search(text)
    if m:
        year = m.group(1)
        text = (text[:m.start()] + text[m.end():]).strip()

    title = text.strip()
    return title, year, season


async def _build_manual_update_doc(title: str, year: str, season: int):
    """
    Search the DB for files matching the manual query and build a movie_doc
    dict in the same shape used by generate_movie_message.
    """
    from database.ia_filterdb import get_search_results

    # Build the search term: include season if given
    if season:
        search_term = f"{title} s{season:02d}"
    else:
        search_term = title
    if year:
        search_term_with_year = f"{title} {year}"
    else:
        search_term_with_year = search_term

    # Search DB — try with season/year first, fall back to bare title
    files, _, total = await get_search_results(search_term, max_results=50, offset=0)
    if not files and year:
        files, _, total = await get_search_results(search_term_with_year, max_results=50, offset=0)
    if not files:
        files, _, total = await get_search_results(title, max_results=50, offset=0)

    # Derive per-file metadata from the DB results
    all_qualities    = set()
    all_resolutions  = set()
    all_languages    = set()
    all_ott_platforms = set()
    all_tags         = set()
    episodes_by_season = defaultdict(set)

    for f in files:
        fname   = f.get("file_name", "")
        cap     = f.get("caption", "") or ""
        unified = f"{fname} {cap}".lower()

        src = get_source_quality(unified)
        if src != "N/A":
            all_qualities.update(x.strip() for x in src.split(",") if x.strip())

        res = get_resolution(unified)
        if res != "N/A":
            all_resolutions.update(x.strip() for x in res.split(",") if x.strip())

        lang_keys = {k for k in CAPTION_LANGUAGES if k in unified}
        for k in lang_keys:
            all_languages.add(CAPTION_LANGUAGES[k])

        ott = extract_ott_platform(unified)
        if ott != "N/A":
            all_ott_platforms.update(p.strip() for p in ott.split("|") if p.strip())

        s, ep = extract_season_episode(fname)
        if s is not None and ep is not None:
            all_tags.add("#SERIES")
            episodes_by_season[str(s)].add(str(ep))
        else:
            all_tags.add("#MOVIE")

    primary_tag = "#SERIES" if "#SERIES" in all_tags else "#MOVIE"

    # Collapse episode list exactly like generate_movie_message does
    epi_block = ""
    if episodes_by_season:
        episode_lines = []
        for s_key, episodes in sorted(episodes_by_season.items(), key=lambda x: int(x[0])):
            singles, ranges = [], []
            for ep in episodes:
                if "-" in ep:
                    ranges.append(ep)
                else:
                    try:
                        singles.append(int(ep))
                    except ValueError:
                        ranges.append(ep)
            singles.sort()
            collapsed = []
            start = end = None
            for num in singles:
                if start is None:
                    start = end = num
                elif num == end + 1:
                    end = num
                else:
                    collapsed.append(str(start) if start == end else f"{start}-{end}")
                    start = end = num
            if start is not None:
                collapsed.append(str(start) if start == end else f"{start}-{end}")
            all_ep_parts = collapsed + sorted(ranges, key=lambda s: int(s.split("-")[0]) if s.split("-")[0].isdigit() else 0)
            episode_lines.append(f"S{int(s_key)}: {', '.join(all_ep_parts)}")
        epi_str = " | ".join(episode_lines)
        if epi_str:
            epi_block = f"📺 ᴇᴘɪsᴏᴅᴇs : <b>{epi_str}</b>"

    # Synthesise a fake movie_doc so we can reuse existing render helpers
    pseudo_files = [{
        "quality":      ", ".join(sorted(all_qualities))   or "N/A",
        "resolution":   ", ".join(sorted(all_resolutions)) or "N/A",
        "language":     ", ".join(sorted(all_languages))   or "N/A",
        "ott_platform": " | ".join(sorted(all_ott_platforms)) or "N/A",
        "tag":          primary_tag,
        "season":       None,
        "episode":      None,
    }]

    return {
        "_id":         title,
        "files":       pseudo_files,
        "genres":      "N/A",          # filled in after metadata fetch
        "rating":      "N/A",
        "poster_url":  None,
        "imdb_url":    "",
        "tag":         primary_tag,
        "ott_platform": " | ".join(sorted(all_ott_platforms)) or "N/A",
        "message_id":  None,
        "is_photo":    False,
        "_epi_block":  epi_block,       # pre-computed, passed through
        "_total_files": total,
    }, files


@Client.on_message(filters.command("m") & filters.user(__import__("info").ADMINS))
async def manual_movie_update(bot, message):
    """
    /m <title> [year|SXX]
    Fetch metadata, search the DB, and post a manual update to MOVIE_UPDATE_CHANNEL.
    """
    try:
        raw_arg = message.text.split(None, 1)[1].strip()
    except IndexError:
        return await message.reply_text(
            "<b>⚠️ Usage:</b>\n"
            "<code>/m pushpa 2</code>\n"
            "<code>/m suits s02</code>\n"
            "<code>/m ironman 2003</code>\n"
            "<code>/m dark knight</code>",
            parse_mode=enums.ParseMode.HTML
        )

    title, year, season = _parse_m_query(raw_arg)
    if not title:
        return await message.reply_text("<b>❌ Could not parse a title from your input.</b>")

    status_msg = await message.reply_text("<b>⏳ Fetching metadata and searching database…</b>")

    try:
        # ── 1. Fetch metadata (TMDB → OMDb cascade) ──────────────────────────
        details: dict = {}
        used_tmdb = False
        tmdb_query = f"{title} s{season:02d}" if season else title

        if TMDB_POSTER:
            tmdb_result = await get_movie_detailsx(tmdb_query, year=year)
            if tmdb_result and not tmdb_result.get("error"):
                details = tmdb_result
                used_tmdb = True
            else:
                details = await get_movie_details(title, file=None) or {}
        else:
            details = await get_movie_details(title, file=None) or {}

        # ── 2. Build display title ────────────────────────────────────────────
        # Use metadata title if available, else clean up the admin's input.
        meta_title = details.get("title") or ""
        if meta_title:
            display_title = meta_title
            if year:
                display_title += f" ({year})"
            elif details.get("year"):
                display_title += f" ({details['year']})"
            if season:
                display_title += f" Season {season}"
        else:
            # Fallback: capitalise the raw input
            display_title = title.title()
            if season:
                display_title += f" Season {season}"
            if year:
                display_title += f" ({year})"

        # ── 3. Genres / rating / poster ───────────────────────────────────────
        raw_genres = details.get("genres", "") or ""
        if isinstance(raw_genres, list):
            genres = ", ".join(str(g) for g in raw_genres if g) or "N/A"
        elif isinstance(raw_genres, str) and raw_genres and raw_genres != "N/A":
            genres = ", ".join(g.strip() for g in raw_genres.split(",") if g.strip()) or "N/A"
        else:
            genres = "N/A"

        rating_raw = details.get("rating", "N/A")
        try:
            rating_display = f"{float(rating_raw):.1f}" if rating_raw and rating_raw != "N/A" else "N/A"
        except (ValueError, TypeError):
            rating_display = str(rating_raw) if rating_raw else "N/A"

        if used_tmdb and LANDSCAPE_POSTER and details.get("backdrop_url"):
            poster_url = details["backdrop_url"]
        else:
            poster_url = details.get("poster_url")

        # ── 4. Search DB for files ────────────────────────────────────────────
        pseudo_doc, db_files = await _build_manual_update_doc(title, year, season)
        total_files = pseudo_doc["_total_files"]
        epi_block   = pseudo_doc["_epi_block"]

        # Determine tag from DB results, fall back to metadata kind
        primary_tag = pseudo_doc["tag"]
        if primary_tag == "#MOVIE" and details.get("kind") == "tv":
            primary_tag = "#SERIES"
        if season:
            primary_tag = "#SERIES"

        # Aggregate quality / language / resolution from DB
        all_qualities  = set()
        all_resolutions = set()
        all_languages  = set()
        for f in pseudo_doc["files"]:
            q = f.get("quality", "N/A")
            if q and q != "N/A":
                all_qualities.update(x.strip() for x in q.split(",") if x.strip())
            r = f.get("resolution", "N/A")
            if r and r != "N/A":
                all_resolutions.update(x.strip() for x in r.split(",") if x.strip())
            lang = f.get("language", "N/A")
            if lang and lang != "N/A":
                all_languages.update(x.strip() for x in lang.split(",") if x.strip())

        quality_str    = ", ".join(sorted(all_qualities))    or "N/A"
        resolution_str = ", ".join(sorted(all_resolutions))  or "N/A"
        language_str   = ", ".join(sorted(all_languages))    or "N/A"

        # ── 5. Build caption ──────────────────────────────────────────────────
        text = script.MANUAL_UPDATE_NOTIFY_TXT.format(
            tag        = primary_tag,
            filename   = display_title,
            genres     = genres,
            quality    = quality_str,
            resolution = resolution_str,
            language   = language_str,
            rating     = rating_display,
            episodes   = epi_block,
        )

        # ── 6. Buttons ────────────────────────────────────────────────────────
        # Deep link format already supported by the /start handler:
        #   ?start=getfile-{query-with-dashes}  →  auto_filter(query)
        search_query = display_title.replace(" ", "-")
        if season:
            season_tag = f"S{season:02d}"
            if season_tag.lower() not in search_query.lower():
                search_query += f"-{season_tag}"

        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔍 ɢᴇᴛ ꜰɪʟᴇs",
                url=f"https://t.me/{temp.U_NAME}?start=getfile-{search_query}"
            )],
            [InlineKeyboardButton(
                "📥 Watch & Download",
                url=f"https://t.me/{temp.U_NAME}?start=getfile-{search_query}"
            )],
            [InlineKeyboardButton(
                "♨️ Viral Stuff ♨️",
                url="https://t.me/Reload_adultbot"
            )],
        ])

        # ── 7. Send to MOVIE_UPDATE_CHANNEL ───────────────────────────────────
        resized_poster = None
        if poster_url and not LINK_PREVIEW:
            is_landscape = used_tmdb and LANDSCAPE_POSTER and poster_url and "original" in poster_url
            size = (2560, 1440) if is_landscape else (853, 1280)
            resized_poster = await fetch_image(poster_url, size=size)

        if resized_poster:
            await bot.send_photo(
                chat_id     = MOVIE_UPDATE_CHANNEL,
                photo       = resized_poster,
                caption     = text,
                reply_markup= reply_markup,
                parse_mode  = enums.ParseMode.HTML,
            )
        else:
            send_params = {
                "chat_id":      MOVIE_UPDATE_CHANNEL,
                "text":         text,
                "reply_markup": reply_markup,
                "parse_mode":   enums.ParseMode.HTML,
            }
            if poster_url and LINK_PREVIEW:
                send_params["url"]          = poster_url
                send_params["invert_media"] = ABOVE_PREVIEW
            await bot.send_message(**send_params)

        # ── 8. Confirm to admin ───────────────────────────────────────────────
        files_note = f"({total_files} files in DB)" if total_files else "(no files found in DB yet)"
        await status_msg.edit_text(
            f"<b>✅ Update posted!</b>\n"
            f"<b>Title:</b> {display_title}\n"
            f"<b>DB:</b> {files_note}",
            parse_mode=enums.ParseMode.HTML
        )

    except Exception as exc:
        logger.exception("Manual movie update failed: %s", exc)
        await status_msg.edit_text(f"<b>❌ Failed:</b> <code>{exc}</code>", parse_mode=enums.ParseMode.HTML)
