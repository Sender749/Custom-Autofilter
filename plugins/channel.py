import re
import logging
import asyncio
from datetime import datetime
from collections import defaultdict
from plugins.helper.Imdbposter import get_movie_detailsx, fetch_image, get_movie_details
from database.users_chats_db import db
from pyrogram import Client, filters, enums
from info import CHANNELS, MOVIE_UPDATE_CHANNEL, LINK_PREVIEW, ABOVE_PREVIEW, LANDSCAPE_POSTER, TMDB_POSTER, FETCH_MOVIE_UPDATE, ADMINS
from Script import script
from database.ia_filterdb import save_file, get_search_results
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
    success = await save_file(media)
    if not success:
        return
    return

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
    quality = get_qualities(caption_clean) or get_qualities(filename.lower()) or "N/A"
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
    global error_tmdb
    error_tmdb=False
    if source_chat.username:
        channel_link = f"https://t.me/{source_chat.username}"
    else:
        channel_link = f"https://t.me/c/{str(source_chat.id)[4:]}"
    file_data = {
        "filename": filename,
        "processed": processed,
        "quality": media_info["quality"],
        "language": media_info["language"],
        "ott_platform": media_info["ott_platform"],
        "timestamp": datetime.now(),
        "tag": media_info["tag"],
        "season": media_info["season"],
        "episode": media_info["episode"],
        "source_channel": channel_link
    }
    if not movie_doc:
        if TMDB_POSTER:
            details = await get_movie_detailsx(base_name)
            if details.get("error"):
                error_tmdb=True
                logger.info("TMDB error switching to IMDB")
                details = await get_movie_details(base_name) or {}
        else:
            details = await get_movie_details(base_name) or {}

        raw_genres = details.get("genres", "N/A")
        if isinstance(raw_genres, str):
            genre_list = [g.strip() for g in raw_genres.split(",")]
            genres = ", ".join(g for g in genre_list if g in STANDARD_GENRES) or "N/A"
        else:
            genres = ", ".join(g for g in raw_genres if g in STANDARD_GENRES) or "N/A"
        movie_doc = {
            "_id": base_name,
            "files": [file_data],
            "poster_url": details.get("backdrop_url") if LANDSCAPE_POSTER and TMDB_POSTER and not error_tmdb else details.get("poster_url"),
            "genres": genres,
            "rating": details.get("rating", "N/A"),
            "imdb_url": details.get("url", "")if not TMDB_POSTER else details.get("tmdb_url"),
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
            channels = set()
            for f in movie_doc["files"]:
                link = f.get("source_channel")
                if link:
                    channels.add(link)
            buttons = [
                [InlineKeyboardButton("✨Get Direct File✨", url=link)]
                for link in sorted(channels)
            ]
            reply_markup = InlineKeyboardMarkup(buttons)
            if movie_doc.get("poster_url") and not LINK_PREVIEW:
                resized_poster = await fetch_image(movie_doc["poster_url"], size=(2560, 1440) if LANDSCAPE_POSTER and TMDB_POSTER and not error_tmdb else (853, 1280))
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
                if movie_doc.get("poster_url") and LINK_PREVIEW:
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
            [InlineKeyboardButton("✨ Get Direct File ✨", url=link)]
            for link in sorted(channels)
        ]
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
    all_qualities = set()
    all_languages = set()
    all_ott_platforms = set()
    all_tags = set()
    episodes_by_season = defaultdict(set)

    for file in movie_doc["files"]:
        if file["quality"] != "N/A":
            all_qualities.update(q.strip() for q in file["quality"].split(",") if q.strip())
        if file["language"] != "N/A":
            all_languages.update(l.strip() for l in file["language"].split(",") if l.strip())
        if file["ott_platform"] != "N/A":
            platforms = [p.strip() for p in file["ott_platform"].split("|") if p.strip()]
            all_ott_platforms.update(platforms)
        if file["tag"]:
            all_tags.add(file["tag"])
        if file.get("season") and file.get("episode"):
            season = file["season"]
            episode = file["episode"]
            episodes_by_season[season].add(episode)

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
    quality_str = ", ".join(sorted(all_qualities)) if all_qualities else "N/A"
    language_str = ", ".join(sorted(all_languages)) if all_languages else "N/A"
    ott_str = ", ".join(sorted(all_ott_platforms)) if all_ott_platforms else "N/A"

    return script.MOVIE_UPDATE_NOTIFY_TXT.format(
        poster_url=movie_doc.get("poster_url", ""),
        imdb_url=movie_doc.get("imdb_url", ""),
        filename=base_name,
        tag=primary_tag,
        genres=genres,
        ott=ott_str,
        quality=quality_str,
        language=language_str,
        episodes=epi_block,
        rating=movie_doc.get("rating", "N/A"),
        search_link=temp.B_LINK
    )


# ──────────────────────────────────────────────────────────────────────────────
# /m {movie/series name} [{year} | {s01}]  — admin-only manual notification
#
# Completely independent from the auto-update flow.
# Steps:
#   1. Parse query — optional trailing year OR season tag (s01, s1, s02 …)
#   2. Search the main file database (file_name + caption)
#   3. Build the notification message on-the-fly from matched files
#   4. Delete any previous notification for that title from MOVIE_UPDATE_CHANNEL
#      (looked up in movie_updates collection)
#   5. Send a fresh notification and record its message_id
# ──────────────────────────────────────────────────────────────────────────────

# Regex to detect a trailing season hint like s01 / s1 / s02
_SEASON_HINT_RE = re.compile(r'\bs(\d{1,2})\s*$', re.IGNORECASE)
# Regex to detect a trailing year hint like 2023 / 2024
_YEAR_HINT_RE   = re.compile(r'\b((?:19|20)\d{2})\s*$')

@Client.on_message(filters.command("m") & filters.user(ADMINS))
async def manual_movie_update(bot, message):
    """
    Usage:
      /m <movie name> [year]     — e.g.  /m Pushpa 2025  or  /m Interstellar
      /m <series name> [sNN]     — e.g.  /m Mirzapur s02
    """
    args = message.text.strip().split(None, 1)
    if len(args) < 2 or not args[1].strip():
        return await message.reply_text(
            "<b>ʜᴏᴡ ᴛᴏ ᴜsᴇ:</b>\n"
            "<code>/m &lt;movie name&gt; [year]</code>\n"
            "<code>/m &lt;series name&gt; [sNN]</code>\n\n"
            "<b>ᴇxᴀᴍᴘʟᴇs:</b>\n"
            "• <code>/m Pushpa 2025</code>\n"
            "• <code>/m Interstellar</code>\n"
            "• <code>/m Mirzapur s02</code>\n"
            "• <code>/m Dark s01</code>",
            parse_mode=enums.ParseMode.HTML
        )

    raw_input = args[1].strip()

    # ── Parse optional trailing season hint  e.g. s01 / s1 / s02 ─────────────
    season_hint = None
    season_num  = None
    year_hint   = None   # always initialise here to avoid UnboundLocalError
    sm = _SEASON_HINT_RE.search(raw_input)
    if sm:
        season_num  = int(sm.group(1))
        season_hint = sm.group(0)                     # e.g. "s02"
        title_query = raw_input[:sm.start()].strip()
    else:
        # ── Parse optional trailing year hint  e.g. 2024 ──────────────────────
        ym = _YEAR_HINT_RE.search(raw_input)
        if ym:
            year_hint   = ym.group(1)
            title_query = raw_input[:ym.start()].strip()
        else:
            title_query = raw_input

    if not title_query:
        return await message.reply_text(
            "<b>❌ ᴘʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ᴀ ᴍᴏᴠɪᴇ ᴏʀ sᴇʀɪᴇs ɴᴀᴍᴇ.</b>",
            parse_mode=enums.ParseMode.HTML
        )

    # Build the DB search query  (title  +  season-hint  or  year-hint)
    if season_hint:
        db_query = f"{title_query} {season_hint}"
    elif year_hint:
        db_query = f"{title_query} {year_hint}"
    else:
        db_query = title_query

    status_msg = await message.reply_text(
        f"<b>🔍 sᴇᴀʀᴄʜɪɴɢ ᴅᴀᴛᴀʙᴀsᴇ ꜰᴏʀ:</b> <code>{db_query}</code>",
        parse_mode=enums.ParseMode.HTML
    )

    # ── Search the file database ───────────────────────────────────────────────
    try:
        files, _, total = await get_search_results(db_query, max_results=300, offset=0)
    except Exception as e:
        logger.exception("Manual /m – DB search failed: %s", e)
        return await status_msg.edit_text(
            "<b>❌ ᴅᴀᴛᴀʙᴀsᴇ sᴇᴀʀᴄʜ ꜰᴀɪʟᴇᴅ.</b>",
            parse_mode=enums.ParseMode.HTML
        )

    if not files:
        return await status_msg.edit_text(
            f"<b>😕 ɴᴏ ꜰɪʟᴇs ꜰᴏᴜɴᴅ ꜰᴏʀ:</b> <code>{db_query}</code>",
            parse_mode=enums.ParseMode.HTML
        )

    # ── Filter: apply season/year constraints only (DB search already matched title) ──
    # NOTE: We do NOT re-check title words here.
    # The DB stores raw file names like "The Boys S05E01 720p Hindi..." — the
    # search already filtered by title. Re-checking individual words causes false
    # negatives because save_file() strips punctuation/symbols from stored names.
    def _file_matches_safe(file_doc):
        fname    = (file_doc.get("file_name") or "").lower()
        cap      = (file_doc.get("caption")   or "").lower()
        combined = f"{fname} {cap}"
        if season_num is not None:
            # must contain S<N> pattern  (handles S1 / S01 / S02 etc.)
            if not re.search(rf'\bs0*{season_num}\b', combined, re.IGNORECASE):
                return False
        elif year_hint:
            if year_hint not in combined:
                return False
        return True

    matched_files = [f for f in files if _file_matches_safe(f)]

    if not matched_files:
        return await status_msg.edit_text(
            f"<b>😕 ɴᴏ ᴍᴀᴛᴄʜɪɴɢ ꜰɪʟᴇs ꜰᴏᴜɴᴅ ꜰᴏʀ:</b> <code>{db_query}</code>",
            parse_mode=enums.ParseMode.HTML
        )

    await status_msg.edit_text(
        f"<b>⚙️ ꜰᴏᴜɴᴅ {len(matched_files)} ꜰɪʟᴇ(s). ʙᴜɪʟᴅɪɴɢ ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ…</b>",
        parse_mode=enums.ParseMode.HTML
    )

    # ── Build notification data on-the-fly from the matched files ─────────────
    # We do NOT touch movie_updates tracking — this is a fresh standalone send.
    all_qualities    = set()
    all_languages    = set()
    all_ott_platforms= set()
    all_tags         = set()
    episodes_by_season = defaultdict(set)
    base_name_used   = None
    _local_error_tmdb = False

    for file_doc in matched_files:
        filename = file_doc.get("file_name") or ""
        caption  = file_doc.get("caption")  or ""
        try:
            info = extract_media_info(filename, caption)
        except Exception:
            continue

        # Anchor to the first base_name we derive; allow slight variations
        # (different qualities of same movie resolve to the same base_name)
        if base_name_used is None:
            base_name_used = info["base_name"]

        if info["quality"] != "N/A":
            all_qualities.update(q.strip() for q in info["quality"].split(",") if q.strip())
        if info["language"] != "N/A":
            all_languages.update(l.strip() for l in info["language"].split(",") if l.strip())
        if info["ott_platform"] != "N/A":
            all_ott_platforms.update(p.strip() for p in info["ott_platform"].split("|") if p.strip())
        if info["tag"]:
            all_tags.add(info["tag"])
        if info.get("season") and info.get("episode"):
            episodes_by_season[info["season"]].add(info["episode"])

    if not base_name_used:
        return await status_msg.edit_text(
            "<b>😕 ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴇsᴏʟᴠᴇ ᴛɪᴛʟᴇ ꜰʀᴏᴍ ꜰɪʟᴇs.</b>",
            parse_mode=enums.ParseMode.HTML
        )

    # ── Fetch poster / details — always default to {} if API returns None ──────
    # The TMDB/IMDB API can return None (e.g. HTTP 402 payment required).
    # Calling .get() on None crashes with AttributeError, so guard here.
    if TMDB_POSTER:
        details = await get_movie_detailsx(base_name_used) or {}
        if details.get("error"):
            _local_error_tmdb = True
            details = await get_movie_details(base_name_used) or {}
    else:
        details = await get_movie_details(base_name_used) or {}

    raw_genres = details.get("genres", "N/A")
    if isinstance(raw_genres, str):
        genres = ", ".join(g.strip() for g in raw_genres.split(",") if g.strip() in STANDARD_GENRES) or "N/A"
    else:
        genres = ", ".join(g for g in raw_genres if g in STANDARD_GENRES) or "N/A"

    poster_url = (
        details.get("backdrop_url")
        if LANDSCAPE_POSTER and TMDB_POSTER and not _local_error_tmdb
        else details.get("poster_url")
    )
    imdb_url = details.get("url", "") if not TMDB_POSTER else details.get("tmdb_url", "")
    rating   = details.get("rating", "N/A")

    # ── Build episode block ────────────────────────────────────────────────────
    epi_block = ""
    if episodes_by_season:
        episode_lines = []
        for sn, episodes in sorted(episodes_by_season.items(), key=lambda x: int(x[0])):
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
            collapsed, start, end = [], None, None
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
            episode_lines.append(f"S{int(sn)}: {', '.join(all_ep_parts)}")
        epi_str = " ".join(episode_lines)
        if epi_str:
            epi_block = f"📺 ᴇᴘɪsᴏᴅᴇs : <b>{epi_str}</b>"

    primary_tag  = "#SERIES" if "#SERIES" in all_tags else "#MOVIE"
    quality_str  = ", ".join(sorted(all_qualities))  if all_qualities   else "N/A"
    language_str = ", ".join(sorted(all_languages))  if all_languages   else "N/A"
    ott_str      = ", ".join(sorted(all_ott_platforms)) if all_ott_platforms else "N/A"

    # Build a temporary doc just for generate_movie_message
    temp_doc = {
        "files":      [],          # not needed — we aggregate above
        "poster_url": poster_url or "",
        "imdb_url":   imdb_url,
        "genres":     genres,
        "rating":     rating,
        "tag":        primary_tag,
    }

    notify_text = script.MOVIE_UPDATE_NOTIFY_TXT.format(
        poster_url  = poster_url or "",
        imdb_url    = imdb_url,
        filename    = base_name_used,
        tag         = primary_tag,
        genres      = genres,
        ott         = ott_str,
        quality     = quality_str,
        language    = language_str,
        episodes    = epi_block,
        rating      = rating,
        search_link = temp.B_LINK
    )

    # Button pointing to MOVIE_UPDATE_CHANNEL (where users fetch files)
    channel_link = (
        f"https://t.me/c/{str(MOVIE_UPDATE_CHANNEL)[4:]}"
        if str(MOVIE_UPDATE_CHANNEL).startswith("-100")
        else f"https://t.me/c/{MOVIE_UPDATE_CHANNEL}"
    )
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ ɢᴇᴛ ᴅɪʀᴇᴄᴛ ꜰɪʟᴇ ✨", url=channel_link)]
    ])

    # ── Delete the previous notification for this title (if any) ──────────────
    if not hasattr(db, 'movie_updates'):
        db.movie_updates = db.db.movie_updates

    old_doc = await db.movie_updates.find_one({"_id": base_name_used})
    if old_doc and old_doc.get("message_id"):
        try:
            await bot.delete_messages(
                chat_id=MOVIE_UPDATE_CHANNEL,
                message_ids=old_doc["message_id"]
            )
        except Exception:
            pass  # already deleted or invalid — no problem

    # ── Send the fresh notification ────────────────────────────────────────────
    try:
        if poster_url and not LINK_PREVIEW:
            resized = await fetch_image(
                poster_url,
                size=(2560, 1440) if LANDSCAPE_POSTER and TMDB_POSTER and not _local_error_tmdb else (853, 1280)
            )
            sent_msg = await bot.send_photo(
                chat_id    = MOVIE_UPDATE_CHANNEL,
                photo      = resized,
                caption    = notify_text,
                reply_markup = reply_markup,
                parse_mode = enums.ParseMode.HTML
            )
            is_photo = True
        else:
            send_params = {
                "chat_id":      MOVIE_UPDATE_CHANNEL,
                "text":         notify_text,
                "reply_markup": reply_markup,
                "parse_mode":   enums.ParseMode.HTML,
            }
            if poster_url and LINK_PREVIEW:
                send_params["invert_media"] = ABOVE_PREVIEW
            else:
                send_params["disable_web_page_preview"] = True
            sent_msg = await bot.send_message(**send_params)
            is_photo = False
    except FloodWait as e:
        await asyncio.sleep(e.value + 2)
        return await status_msg.edit_text(
            "<b>⏳ ꜰʟᴏᴏᴅ ᴡᴀɪᴛ. ᴘʟᴇᴀsᴇ ʀᴇᴛʀʏ ɪɴ ᴀ ᴍᴏᴍᴇɴᴛ.</b>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        logger.exception("Manual /m – failed to send notification: %s", e)
        return await status_msg.edit_text(
            f"<b>❌ ꜰᴀɪʟᴇᴅ ᴛᴏ sᴇɴᴅ ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ: {e}</b>",
            parse_mode=enums.ParseMode.HTML
        )

    # ── Persist the new message_id so future /m calls can delete it ───────────
    update_payload = {
        "message_id": sent_msg.id,
        "is_photo":   is_photo,
        "tag":        primary_tag,
        "ott_platform": ott_str,
        "genres":     genres,
        "rating":     rating,
        "poster_url": poster_url or "",
        "imdb_url":   imdb_url,
    }
    if old_doc:
        await db.movie_updates.update_one(
            {"_id": base_name_used},
            {"$set": update_payload}
        )
    else:
        await db.movie_updates.insert_one({"_id": base_name_used, "files": [], **update_payload})

    label = f"{base_name_used}" + (f" [{season_hint.upper()}]" if season_hint else "")
    await status_msg.edit_text(
        f"<b>✅ ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ sᴇɴᴛ!</b>\n\n"
        f"📽 <b>{label}</b>\n"
        f"📂 ꜰɪʟᴇs ᴜsᴇᴅ: <b>{len(matched_files)}</b>",
        parse_mode=enums.ParseMode.HTML
    )
