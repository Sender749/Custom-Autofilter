import asyncio
import re
import math
import time
import socket
import aiohttp

from pyrogram.errors.exceptions.bad_request_400 import MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty
from Script import script
import pyrogram
from info import *
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputMediaPhoto, ChatPermissions, ReplyKeyboardMarkup
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, UserIsBlocked, MessageNotModified, PeerIdInvalid, ChatAdminRequired
from utils import (temp, get_settings, is_check_admin, get_status, get_size,
                   save_group_settings, is_subscribed, is_req_subscribed, get_poster,
                   get_readable_time, imdb, formate_file_name, process_trending_data,
                   create_keyboard_layout, log_error, group_setting_buttons)
from database.users_chats_db import db
from database.extra_db import silicondb
from database.ia_filterdb import (
    collection, is_second_db_configured, second_collection,
    get_search_results, get_all_results, delete_files,
    normalize_query, get_title_cache,
    ai_spell_check, parse_query,
    _strip_tech, _extract_year, _extract_season_episode,
)
import logging
import traceback

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

# ── In-memory state ────────────────────────────────────────────────────────────
BUTTONS = {}
FILES_ID = {}
CAP = {}
_META_STORE = {}    # key → {seasons, years, languages} for each active search
SUGGESTION_TRACKER = {}
WRONG_SPELL_WAIT = {}
CUSTOM_REPLY_WAIT = {}
REQUEST_DEDUP = {}

# ══════════════════════════════════════════════════════════════════════════════
# RESULT CACHE — stores the full ranked file list for each query.
# Pagination is pure Python slicing — zero extra DB calls after first search.
# ══════════════════════════════════════════════════════════════════════════════

_RESULT_CACHE: dict = {}        # key → {"files": [...], "meta": {...}, "time": float}
_RESULT_CACHE_TTL: int = 600    # 10 minutes


def _cache_key(search: str) -> str:
    return search.lower().strip()


def _cache_get(search: str) -> dict | None:
    key = _cache_key(search)
    entry = _RESULT_CACHE.get(key)
    if not entry:
        return None
    if time.time() - entry["time"] > _RESULT_CACHE_TTL:
        _RESULT_CACHE.pop(key, None)
        return None
    return entry


def _cache_set(search: str, files: list, meta: dict):
    """Store the full ranked file list and per-query metadata."""
    key = _cache_key(search)
    _RESULT_CACHE[key] = {"files": files, "meta": meta, "time": time.time()}
    # Keep cache size bounded (max 200 unique queries)
    if len(_RESULT_CACHE) > 200:
        oldest = min(_RESULT_CACHE, key=lambda k: _RESULT_CACHE[k]["time"])
        _RESULT_CACHE.pop(oldest, None)


def _get_page(search: str, offset: int, max_btn: int) -> tuple:
    """
    Get a page of files from cache.
    Returns (files, next_offset, total) — all from memory, no DB.
    """
    entry = _cache_get(search)
    if not entry:
        return None, None, None
    all_files = entry["files"]
    total = len(all_files)
    files = all_files[offset:offset + max_btn]
    next_offset = offset + max_btn
    if next_offset >= total:
        next_offset = ''
    return files, next_offset, total


def _extract_meta(files: list) -> dict:
    """
    Scan all results once and extract:
      - available seasons  (sorted descending)
      - available years    (sorted descending)
      - available languages
    All derived from actual filenames in the result set — not hardcoded.
    """
    seasons: set = set()
    years: set = set()
    langs: set = set()

    _LANG_LIST = [
        'hindi', 'english', 'tamil', 'telugu', 'malayalam',
        'kannada', 'punjabi', 'bengali', 'gujarati', 'marathi',
        'dual', 'multi',
    ]

    for f in files:
        text = (f.get("file_name") or "") + " " + (f.get("caption") or "")
        tl = text.lower()

        # Seasons
        for m in re.finditer(r'\bs(\d{1,2})\b', tl):
            n = int(m.group(1))
            if 1 <= n <= 50:
                seasons.add(n)

        # Years
        y = _extract_year(text)
        if y:
            years.add(y)

        # Languages
        for lang in _LANG_LIST:
            if re.search(r'\b' + lang + r'\b', tl):
                langs.add(lang)

    return {
        "seasons":   sorted(seasons, reverse=True),
        "years":     sorted(years, reverse=True),
        "languages": sorted(langs),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Helper utilities
# ══════════════════════════════════════════════════════════════════════════════

def get_display_name(file: dict) -> str:
    caption = file.get("caption")
    if caption and caption.strip():
        return caption.strip()
    return file.get("file_name", "Unknown File")


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3 helpers — TMDB + OMDB suggestion candidates
# (ai_spell_check is imported from database.ia_filterdb)
# ══════════════════════════════════════════════════════════════════════════════

async def _tmdb_title_candidates(query: str) -> list:
    """Fetch title candidates from TMDB (movie + TV). Never raises."""
    from info import TMDB_API_KEY
    if not TMDB_API_KEY:
        return []

    pq = parse_query(query)
    clean = pq.title_only or query.strip()

    candidates = []
    try:
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        timeout = aiohttp.ClientTimeout(total=6, connect=3)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            for endpoint in ("movie", "tv"):
                params = {"api_key": TMDB_API_KEY, "query": clean, "include_adult": "false", "page": 1}
                if pq.year:
                    params["year"] = pq.year
                async with session.get(
                    f"https://api.themoviedb.org/3/search/{endpoint}", params=params
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        key = "name" if endpoint == "tv" else "title"
                        for item in (data.get("results") or [])[:8]:
                            t = item.get(key) or item.get("original_" + key)
                            if t:
                                candidates.append(t)
    except Exception as exc:
        logger.debug("TMDB candidate fetch failed for %r: %s", query, exc)
    return candidates


async def _fetch_omdb_candidates(query: str) -> list:
    """Fetch title candidates from OMDB. Never raises."""
    from info import OMDB_API_KEY
    if not OMDB_API_KEY:
        return []
    pq = parse_query(query)
    clean = pq.title_only or query.strip()
    results = []
    try:
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        timeout = aiohttp.ClientTimeout(total=5, connect=3)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            for mtype in ("movie", "series"):
                params = {"apikey": OMDB_API_KEY, "s": clean, "type": mtype}
                async with session.get("http://www.omdbapi.com/", params=params) as r:
                    if r.status == 200:
                        data = await r.json()
                        for item in (data.get("Search") or [])[:5]:
                            t = item.get("Title")
                            if t:
                                results.append(t)
    except Exception as exc:
        logger.debug("OMDB fetch failed for %r: %s", query, exc)
    return results


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3 — Suggestion buttons (TMDB + OMDB, concurrent)
# ══════════════════════════════════════════════════════════════════════════════

async def show_suggestions(bot, message, query):
    """
    Stage 3: Show suggestion buttons from TMDB + OMDB.

    Each button shows ✅ if the title is likely in DB, or 🔍 if not.
    Clicking a suggestion re-triggers auto_filter (Stage 1 → 2 → request).
    If the user clicks nothing within SUGGESTION_TIMEOUT, auto-request fires.
    """
    q_lower = query.lower()

    # Fetch TMDB + OMDB concurrently
    tmdb_res, omdb_res = await asyncio.gather(
        _tmdb_title_candidates(query),
        _fetch_omdb_candidates(query),
        return_exceptions=True
    )
    tmdb_candidates = tmdb_res if isinstance(tmdb_res, list) else []
    omdb_candidates = omdb_res if isinstance(omdb_res, list) else []

    # Merge + deduplicate + score by similarity
    from database.ia_filterdb import _fuzzy_score, _collapse, _strip_tech
    seen: set = set()
    all_titles = []
    for t in (tmdb_candidates + omdb_candidates):
        k = t.lower()
        if k not in seen:
            seen.add(k)
            score = _fuzzy_score(_strip_tech(q_lower), _strip_tech(k))
            all_titles.append((t, score))

    all_titles.sort(key=lambda x: -x[1])
    final_titles = [t for t, s in all_titles if s >= 30][:MAX_SUGGESTIONS]

    # Build DB presence set (from cache — no extra DB call)
    db_pairs: list = []
    try:
        db_pairs = await get_title_cache()   # list of (stripped_lower, original)
    except Exception:
        pass
    db_stripped_set = {p[0] for p in db_pairs}

    def _in_db(title: str) -> bool:
        tl = _strip_tech(re.sub(r'\s*\(\d{4}\)\s*$', '', title).strip())
        tl_col = _collapse(tl)
        # Collapsed exact match
        if any(_collapse(b) == tl_col for b in db_stripped_set):
            return True
        # Fuzzy check with nearby-length titles only
        return any(
            _fuzzy_score(tl, b) >= 88
            for b in db_stripped_set
            if abs(len(tl) - len(b)) <= 4
        )

    # Build buttons
    buttons = []
    for t in final_titles:
        icon = "✅" if _in_db(t) else "🔍"
        safe_t = t[:50]
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {safe_t}",
            callback_data=f"spelling#{safe_t}"
        )])

    if not buttons:
        google = query.replace(' ', '+')
        buttons = [
            [InlineKeyboardButton("🔍 ꜱᴇᴀʀᴄʜ ᴏɴ ɢᴏᴏɢʟᴇ", url=f"https://www.google.com/search?q={google}")],
            [InlineKeyboardButton("📮 ʀᴇǫᴜᴇsᴛ ᴛᴏ ᴀᴅᴍɪɴ", callback_data=f"req_admin#{query}#{message.from_user.id}")],
        ]
    else:
        buttons.append([InlineKeyboardButton(
            "🔍 ꜱᴇᴀʀᴄʜ ᴏɴ ɢᴏᴏɢʟᴇ",
            url=f"https://www.google.com/search?q={query.replace(' ', '+')}"
        )])
        buttons.append([InlineKeyboardButton(
            "📮 ʀᴇǫᴜᴇsᴛ ᴛᴏ ᴀᴅᴍɪɴ",
            callback_data=f"req_admin#{query}#{message.from_user.id}"
        )])

    text = (
        f"<b>😕 No results found for: <code>{query}</code></b>\n\n"
        f"<b>🔎 Did you mean one of these?</b>\n"
        f"<b>✅</b> = Available in DB  |  <b>🔍</b> = Not in DB"
    )
    sent = await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=True,
    )
    SUGGESTION_TRACKER[sent.id] = {
        "clicked": False,
        "query": query,
        "user": message.from_user,
    }
    asyncio.create_task(suggestion_timeout_handler(bot, sent))


async def suggestion_timeout_handler(bot, msg):
    await asyncio.sleep(SUGGESTION_TIMEOUT)
    data = SUGGESTION_TRACKER.get(msg.id)
    if not data:
        return
    if not data["clicked"]:
        user = data["user"]
        query = data["query"]
        if user.id not in ADMINS:
            mention = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
            text = (
                "<b>#FILE_NOT_FOUND</b>\n\n"
                f"👤 User: {mention}\n"
                f"🆔 ID: <code>{user.id}</code>\n"
                f"🔍 Query: <code>{query}</code>"
            )
            try:
                await bot.send_message(chat_id=NOT_FOUND_FILE_CHANNEL, text=text)
            except Exception:
                pass
    try:
        await msg.delete()
    except Exception:
        pass
    SUGGESTION_TRACKER.pop(msg.id, None)


# ══════════════════════════════════════════════════════════════════════════════
# Request helpers
# ══════════════════════════════════════════════════════════════════════════════

async def send_request_common(bot, *, user, search, origin_message=None):
    dedup_key = search.lower().strip()
    if REQUEST_DEDUP.get(user.id) == dedup_key:
        return
    REQUEST_DEDUP[user.id] = dedup_key

    buttons = []
    if origin_message and origin_message.chat.type in ("group", "supergroup"):
        buttons.append([InlineKeyboardButton("👀 View Request", url=origin_message.link)])
    else:
        buttons.append([InlineKeyboardButton("👀 View Request", callback_data=f"view_req_info#{user.id}")])
    buttons.append([InlineKeyboardButton(
        "⚙ Show Options",
        callback_data=f"show_options#{user.id}#{origin_message.id if origin_message else 0}"
    )])

    sent = await bot.send_message(
        REQUEST_CHANNEL,
        script.REQUEST_TXT.format(user.mention, user.id, search),
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    if user and not user.is_bot:
        await bot.send_message(
            chat_id=user.id,
            text="<b>✅ Your request has been sent to admin!</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✨ View Your Request ✨", url=sent.link)]])
        )


async def trigger_auto_request(bot, message, search):
    user = getattr(message, "_real_user", message.from_user)
    if not user or user.is_bot or user.id in ADMINS:
        return
    await send_request_common(bot, user=user, search=search, origin_message=message)


# ══════════════════════════════════════════════════════════════════════════════
# Main auto_filter — 3-stage search pipeline
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# Main auto_filter — 3-stage search pipeline
# ══════════════════════════════════════════════════════════════════════════════

async def auto_filter(client, msg, spoll=False):
    if not spoll:
        message = msg
        real_user = getattr(msg, "_real_user", None)
        if real_user:
            message.from_user = real_user

        search = normalize_query(message.text)
        chat_id = message.chat.id

        # ── Fire DB search + settings fetch + status update in parallel ───────
        search_msg = await msg.reply_text(f'<b>🕵️ sᴇᴀʀᴄʜɪɴɢ <code>{search}</code></b>')

        # Check cache first (synchronous, zero-cost)
        entry = _cache_get(search)

        if entry:
            all_files = entry["files"]
            meta      = entry["meta"]
            # Fire settings + analytics concurrently (non-blocking)
            settings, _ = await asyncio.gather(
                get_settings(chat_id),
                asyncio.to_thread(silicondb.update_silicon_messages, message.from_user.id, message.text),
                return_exceptions=True
            )
            if isinstance(settings, Exception):
                settings = {}
        else:
            # Cache miss: fetch ALL results + settings concurrently
            (all_files, settings), _ = await asyncio.gather(
                asyncio.gather(
                    get_all_results(search),
                    get_settings(chat_id),
                ),
                asyncio.to_thread(silicondb.update_silicon_messages, message.from_user.id, message.text),
                return_exceptions=True
            )
            if isinstance(all_files, Exception):
                all_files = []
            if isinstance(settings, Exception):
                settings = {}
            if all_files:
                meta = _extract_meta(all_files)
                _cache_set(search, all_files, meta)
            else:
                meta = {}

        await search_msg.delete()

        max_btn = int(MAX_BTN)
        files = all_files[:max_btn]
        total_results = len(all_files)
        offset = max_btn if total_results > max_btn else ''

        if not files:
            if getattr(msg, "from_suggestion", False):
                await trigger_auto_request(client, message, search)
                return

            if settings.get("spell_check", True):
                # ── Stage 2: AI spell check ────────────────────────────────────
                ai_sts = await msg.reply_text('<b>👾 ᴀɪ ɪs ᴄʜᴇᴄᴋɪɴɢ ꜱᴘᴇʟʟɪɴɢ...</b>')
                corrected = await ai_spell_check(search)
                if corrected:
                    await ai_sts.edit(
                        f'<b><i>ᴀɪ ꜱᴜɢɢᴇꜱᴛᴇᴅ 👉 <code>{corrected}</code>\n'
                        f'ꜱᴇᴀʀᴄʜɪɴɢ ꜰᴏʀ 👉 <code>{corrected}</code></i></b>'
                    )
                    await asyncio.sleep(1.2)
                    msg.text = corrected
                    await ai_sts.delete()
                    return await auto_filter(client, msg)

                await ai_sts.delete()
                # ── Stage 3: Suggestion buttons ────────────────────────────────
                await show_suggestions(client, msg, search)
            return

    else:
        settings = await get_settings(msg.message.chat.id)
        message = msg.message.reply_to_message
        search, files, offset, total_results = spoll
        all_files = files
        meta = _extract_meta(all_files)

    # ── Build result buttons / links ───────────────────────────────────────────
    req = message.from_user.id if message.from_user else 0
    key = f"{message.chat.id}-{message.id}"
    temp.FILES_ID[key] = files
    temp.CHAT[message.from_user.id] = message.chat.id
    BUTTONS[key] = search

    # Store meta for dynamic season/year/lang tabs
    _META_STORE[key] = meta

    del_msg = (
        f"\n\n<b>⚠️ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ ᴀꜰᴛᴇʀ "
        f"<code>{get_readable_time(DELETE_TIME)}</code> ᴛᴏ ᴀᴠᴏɪᴅ ᴄᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs</b>"
        if settings.get("auto_delete") else ""
    )

    if settings.get("link"):
        links = "".join([
            f"<b>\n\n{i}. <a href=https://t.me/{temp.U_NAME}?start=file_{message.chat.id}_{f['_id']}>"
            f"[{get_size(f['file_size'])}] {formate_file_name(get_display_name(f))}</a></b>"
            for i, f in enumerate(files, 1)
        ])
        btn = []
    else:
        links = ""
        btn = [[
            InlineKeyboardButton(
                f"🔗 {get_size(f['file_size'])}≽ {formate_file_name(get_display_name(f))}",
                url=f"https://telegram.dog/{temp.U_NAME}?start=file_{message.chat.id}_{f['_id']}"
            )
        ] for f in files]

    batch_link = f"batchfiles#{message.chat.id}#{message.id}#{message.from_user.id}"

    if offset and total_results >= int(MAX_BTN):
        btn.insert(0, [InlineKeyboardButton("• ʟᴀɴɢᴜᴀɢᴇ •", callback_data=f"languages#{key}#{0}#{req}")])
        btn.insert(1, [
            InlineKeyboardButton("• ǫᴜᴀʟɪᴛʏ •", callback_data=f"qualities#{key}#{0}#{req}"),
            InlineKeyboardButton("• sᴇᴀsᴏɴ •", callback_data=f"seasons#{key}#{0}#{req}")
        ])
        btn.insert(2, [InlineKeyboardButton("• sᴇɴᴅ ᴀʟʟ •", callback_data=batch_link)])
        total_pages = math.ceil(total_results / int(MAX_BTN))
        btn.append([
            InlineKeyboardButton(f"1/{total_pages}", callback_data="pages"),
            InlineKeyboardButton("ɴᴇxᴛ ⪼", callback_data=f"next_{req}_{key}_{int(MAX_BTN)}")
        ])
    else:
        btn.insert(0, [InlineKeyboardButton("• sᴇɴᴅ ᴀʟʟ •", callback_data=batch_link)])
        if not offset:
            btn.insert(1, [InlineKeyboardButton("ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇs", user_id=ADMINS[0])])

    if spoll:
        m = await msg.message.edit(f"<b><code>{search}</code> ɪs ꜰᴏᴜɴᴅ ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ꜰᴏʀ ꜰɪʟᴇs 📫</b>")
        await asyncio.sleep(1.2)
        await m.delete()

    imdb_data = await get_poster(search, file=files[0]['file_name']) if settings.get("imdb") else None

    if imdb_data:
        try:
            cap = settings['template'].format(
                query=search,
                title=imdb_data['title'],
                votes=imdb_data['votes'],
                aka=imdb_data["aka"],
                seasons=imdb_data["seasons"],
                box_office=imdb_data['box_office'],
                localized_title=imdb_data['localized_title'],
                kind=imdb_data['kind'],
                imdb_id=imdb_data["imdb_id"],
                cast=imdb_data["cast"],
                runtime=imdb_data["runtime"],
                countries=imdb_data["countries"],
                certificates=imdb_data["certificates"],
                languages=imdb_data["languages"],
                director=imdb_data["director"],
                writer=imdb_data["writer"],
                producer=imdb_data["producer"],
                composer=imdb_data["composer"],
                cinematographer=imdb_data["cinematographer"],
                music_team=imdb_data["music_team"],
                distributors=imdb_data["distributors"],
                release_date=imdb_data['release_date'],
                year=imdb_data['year'],
                genres=imdb_data['genres'],
                poster=imdb_data['poster'],
                plot=imdb_data['plot'],
                rating=imdb_data['rating'],
                url=imdb_data['url'],
                **locals()
            )
        except Exception:
            cap = f"<b>📂 ʜᴇʀᴇ ɪ ꜰᴏᴜɴᴅ ꜰᴏʀ ʏᴏᴜʀ sᴇᴀʀᴄʜ {search}</b>"
    else:
        cap = f"<b>📂 ʜᴇʀᴇ ɪ ꜰᴏᴜɴᴅ ꜰᴏʀ ʏᴏᴜʀ sᴇᴀʀᴄʜ {search}</b>"

    CAP[key] = cap

    async def send_response(photo_url=None):
        try:
            if photo_url:
                return await message.reply_photo(
                    photo=photo_url,
                    caption=cap[:1024] + links + del_msg,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(btn)
                )
            else:
                return await message.reply_text(
                    cap + links + del_msg,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(btn),
                    disable_web_page_preview=True,
                    reply_to_message_id=message.id
                )
        except Exception as e:
            logger.error(f"send_response error: {e}")
            return await message.reply_text(
                cap + links + del_msg,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(btn),
                disable_web_page_preview=True
            )

    async def handle_auto_delete(response_msg):
        await asyncio.sleep(DELETE_TIME)
        try:
            await response_msg.delete()
        except Exception:
            pass

    if imdb_data and imdb_data.get('poster'):
        try:
            k = await send_response(imdb_data['poster'])
        except (MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty):
            poster = imdb_data['poster'].replace('.jpg', "._V1_UX360.jpg")
            try:
                k = await send_response(poster)
            except Exception:
                k = await send_response()
        except Exception:
            k = await send_response()
    else:
        k = await send_response()

    if k and settings.get("auto_delete"):
        asyncio.create_task(handle_auto_delete(k))


# ══════════════════════════════════════════════════════════════════════════════
# Message handlers
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.text & filters.incoming)
async def pm_search(client, message):
    if message.text.startswith("/"):
        return
    sili = silicondb.get_bot_sttgs()
    if not sili.get('PM_SEARCH', False) if sili else False:
        return await message.reply_text(
            '<b><i>ɪ ᴀᴍ ɴᴏᴛ ᴡᴏʀᴋɪɴɢ ʜᴇʀᴇ. ꜱᴇᴀʀᴄʜ ᴍᴏᴠɪᴇꜱ ɪɴ ᴏᴜʀ ᴍᴏᴠɪᴇ ꜱᴇᴀʀᴄʜ ɢʀᴏᴜᴘ.</i></b>',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 ᴍᴏᴠɪᴇ ꜱᴇᴀʀᴄʜ ɢʀᴏᴜᴘ", url="https://t.me/Navex_Movies")]])
        )
    if not sili.get('AUTO_FILTER', True) if sili else True:
        return await message.reply_text('<b><i>ᴄᴜʀʀᴇɴᴛʟʏ, ʙᴏᴛ ᴡᴀs ᴜɴᴅᴇʀ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ. ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ...</i></b>')
    await auto_filter(client, message)


@Client.on_message(filters.group & filters.text & filters.incoming)
async def group_search(client, message):
    user_id = message.from_user.id if message.from_user else None
    chat_id = message.chat.id
    settings = await get_settings(chat_id)
    sili = silicondb.get_bot_sttgs()

    if message.chat.id == SUPPORT_GROUP:
        if message.text.startswith("/"):
            return
        files, n_offset, total = await get_search_results(message.text, offset=0)
        if total != 0:
            link = await db.get_set_grp_links(index=1)
            msg = await message.reply_text(
                script.SUPPORT_GRP_MOVIE_TEXT.format(message.from_user.mention(), total),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ɢᴇᴛ ꜰɪʟᴇs ꜰʀᴏᴍ ʜᴇʀᴇ 😉', url=link)]])
            )
            await asyncio.sleep(300)
            return await msg.delete()
        return

    if not sili.get('AUTO_FILTER', True) if sili else True:
        return await message.reply_text('<b><i>ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ ᴡᴀs ᴅɪsᴀʙʟᴇᴅ!</i></b>')

    if settings["auto_filter"]:
        if not user_id:
            await message.reply("<b>🚨 ɪ'ᴍ ɴᴏᴛ ᴡᴏʀᴋɪɴɢ ꜰᴏʀ ᴀɴᴏɴʏᴍᴏᴜꜱ ᴀᴅᴍɪɴ!</b>")
            return

        if any(lang in message.text.lower() for lang in ['hindi', 'tamil', 'telugu', 'malayalam', 'kannada', 'english', 'gujarati']):
            return await auto_filter(client, message)
        elif message.text.startswith("/"):
            return
        elif re.findall(r'https?://\S+|www\.\S+|t\.me/\S+', message.text):
            if await is_check_admin(client, message.chat.id, message.from_user.id):
                return
            await message.delete()
            return await message.reply("<b>sᴇɴᴅɪɴɢ ʟɪɴᴋ ɪsɴ'ᴛ ᴀʟʟᴏᴡᴇᴅ ʜᴇʀᴇ ❌🤞🏻</b>")
        elif '@admin' in message.text.lower() or '@admins' in message.text.lower():
            if await is_check_admin(client, message.chat.id, message.from_user.id):
                return
            admins = []
            async for member in client.get_chat_members(chat_id=message.chat.id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
                if not member.user.is_bot:
                    admins.append(member.user.id)
                    if member.status == enums.ChatMemberStatus.OWNER:
                        try:
                            if message.reply_to_message:
                                sent_msg = await message.reply_to_message.forward(member.user.id)
                                await sent_msg.reply_text(f"#Attention\n★ User: {message.from_user.mention}\n★ Group: {message.chat.title}\n\n★ <a href={message.reply_to_message.link}>Go to message</a>", disable_web_page_preview=True)
                            else:
                                sent_msg = await message.forward(member.user.id)
                                await sent_msg.reply_text(f"#Attention\n★ User: {message.from_user.mention}\n★ Group: {message.chat.title}\n\n★ <a href={message.link}>Go to message</a>", disable_web_page_preview=True)
                        except Exception:
                            pass
            hidden = (f'[\u2064](tg://user?id={uid})' for uid in admins)
            await message.reply_text('<code>Report sent</code>' + ''.join(hidden))
            return
        else:
            try:
                await auto_filter(client, message)
            except Exception as e:
                traceback.print_exc()
                print('group_search error:', e)
    else:
        k = await message.reply_text('<b>⚠️ ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ ᴍᴏᴅᴇ ɪꜱ ᴏꜰꜰ...</b>')
        await asyncio.sleep(10)
        await k.delete()


# ══════════════════════════════════════════════════════════════════════════════
# Callback handlers
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_callback_query(filters.regex(r"^spelling#"))
async def suggestion_click_handler(client, query: CallbackQuery):
    title = query.data.split("#", 1)[1]
    SUGGESTION_TRACKER.get(query.message.id, {})["clicked"] = True
    try:
        await query.message.delete()
    except Exception:
        pass
    msg = query.message
    msg.text = title
    msg.from_suggestion = True
    msg._real_user = query.from_user
    await query.answer("🔍 Searching...")
    await auto_filter(client, msg)


@Client.on_callback_query(filters.regex(r"^next"))
async def next_page(bot, query):
    try:
        ident, req, key, offset = query.data.split("_")
        if int(req) not in [query.from_user.id, 0]:
            return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
        offset = max(0, int(offset))
        search = BUTTONS.get(key)
        cap = CAP.get(key, "")
        if not search:
            return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)

        max_btn = int(MAX_BTN)

        # ── Instant page from cache (pure list slice — zero DB) ───────────────
        files, n_offset, total = _get_page(search, offset, max_btn)
        if files is None:
            # Cache miss (expired) — re-fetch
            all_files = await get_all_results(search)
            if not all_files:
                return await query.answer("No files found", show_alert=True)
            meta = _extract_meta(all_files)
            _cache_set(search, all_files, meta)
            _META_STORE[key] = meta
            files = all_files[offset:offset + max_btn]
            total = len(all_files)
            n_offset = offset + max_btn
            if n_offset >= total:
                n_offset = ''

        n_offset_int = int(n_offset) if n_offset else 0
        if not files:
            return await query.answer("No files found", show_alert=True)

        temp.FILES_ID[key] = files
        temp.FILES_ID[f"{query.message.chat.id}-{query.id}"] = files
        temp.CHAT[query.from_user.id] = query.message.chat.id

        settings = await get_settings(query.message.chat.id)
        current_page = (offset // max_btn) + 1
        total_pages = math.ceil(total / max_btn)

        del_msg = (
            f"\n\n<b>⚠️ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ ᴀꜰᴛᴇʀ <code>{get_readable_time(DELETE_TIME)}</code> ᴛᴏ ᴀᴠᴏɪᴅ ᴄᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs</b>"
            if settings.get("auto_delete") else ""
        )

        if settings.get("link"):
            links = "".join([
                f"<b>\n\n{i}. <a href=https://t.me/{temp.U_NAME}?start=file_{query.message.chat.id}_{f['_id']}>"
                f"[{get_size(f['file_size'])}] {get_display_name(f)}</a></b>"
                for i, f in enumerate(files, offset + 1)
            ])
            btn = []
        else:
            links = ""
            btn = [[InlineKeyboardButton(
                f"📁 {get_size(f['file_size'])}≽ {formate_file_name(get_display_name(f))}",
                url=f"https://telegram.dog/{temp.U_NAME}?start=file_{query.message.chat.id}_{f['_id']}"
            )] for f in files]

        btn.insert(0, [InlineKeyboardButton("• ʟᴀɴɢᴜᴀɢᴇ •", callback_data=f"languages#{key}#{offset}#{req}")])
        btn.insert(1, [
            InlineKeyboardButton("• ǫᴜᴀʟɪᴛʏ •", callback_data=f"qualities#{key}#{offset}#{req}"),
            InlineKeyboardButton("• sᴇᴀsᴏɴ •", callback_data=f"seasons#{key}#{offset}#{req}")
        ])
        btn.insert(2, [InlineKeyboardButton("• sᴇɴᴅ ᴀʟʟ •", callback_data=f"batchfiles#{query.message.chat.id}#{query.id}#{query.from_user.id}")])

        nav_row = []
        if offset > 0:
            nav_row.append(InlineKeyboardButton("⪻ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{max(0, offset - max_btn)}"))
        nav_row.append(InlineKeyboardButton(f"{current_page} / {total_pages}", callback_data="pages"))
        if n_offset_int > 0:
            nav_row.append(InlineKeyboardButton("ɴᴇxᴛ ⪼", callback_data=f"next_{req}_{key}_{n_offset_int}"))
        btn.append(nav_row)

        if settings.get("link"):
            await query.message.edit_text(cap + links + del_msg, disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(btn))
        else:
            try:
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
            except MessageNotModified:
                pass
            await query.answer()

    except Exception as e:
        logger.error(f"next_page error: {e}")
        await query.answer("Error processing request", show_alert=True)


@Client.on_callback_query(filters.regex(r"^seasons#"))
async def seasons_cb_handler(client: Client, query: CallbackQuery):
    _, key, offset, req = query.data.split("#")
    if int(req) != query.from_user.id:
        return await query.answer(script.ALRT_TXT, show_alert=True)

    # Get seasons from extracted metadata (DB-derived, not hardcoded)
    meta = _META_STORE.get(key, {})
    available_seasons = meta.get("seasons", [])

    if not available_seasons:
        return await query.answer("⚠️ No seasons found for this search.", show_alert=True)

    btn = []
    row = []
    for n in available_seasons:
        label = f"Season {n}"
        cb = f"S{n:02d}"
        row.append(InlineKeyboardButton(label, callback_data=f"season_search#{cb}#{key}#0#{offset}#{req}"))
        if len(row) == 2:
            btn.append(row)
            row = []
    if row:
        btn.append(row)
    btn.append([InlineKeyboardButton("⪻ ʙᴀᴄᴋ ᴛᴏ ᴍᴀɪɴ ᴘᴀɢᴇ", callback_data=f"next_{req}_{key}_0")])
    await query.message.edit_text(
        f"<b>📺 Available Seasons — choose one ↓↓</b>",
        reply_markup=InlineKeyboardMarkup(btn)
    )


@Client.on_callback_query(filters.regex(r"^season_search#"))
async def season_search(client: Client, query: CallbackQuery):
    _, season, key, offset, original_offset, req = query.data.split("#")
    if int(req) != query.from_user.id:
        return await query.answer(script.ALRT_TXT, show_alert=True)
    search = BUTTONS.get(key)
    if not search:
        return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)

    current_offset = int(offset)
    max_btn = int(MAX_BTN)

    # All files from cache — zero DB call
    entry = _cache_get(search)
    if entry:
        all_files = entry["files"]
    else:
        all_files = await get_all_results(search)
        if all_files:
            meta = _extract_meta(all_files)
            _cache_set(search, all_files, meta)
            _META_STORE[key] = meta

    try:
        seas_num = int(re.sub(r'[Ss]', '', season))
        season_patterns = [
            re.compile(rf'\bS{seas_num:02d}\b', re.IGNORECASE),
            re.compile(rf'\bS{seas_num}\b', re.IGNORECASE),
            re.compile(rf'\bSeason\s*{seas_num}\b', re.IGNORECASE),
        ]
    except (ValueError, IndexError):
        return await query.answer("Invalid season format", show_alert=True)

    filtered_files = [
        f for f in all_files
        if any(p.search(f.get('file_name', '') + ' ' + (f.get('caption') or '')) for p in season_patterns)
    ]
    if not filtered_files:
        return await query.answer(f"😔 Season {seas_num} not found for '{search}'", show_alert=True)

    page_files = filtered_files[current_offset:current_offset + max_btn]
    total_filtered = len(filtered_files)
    current_page = (current_offset // max_btn) + 1
    total_pages = math.ceil(total_filtered / max_btn)
    temp.FILES_ID[f"{query.message.chat.id}-{query.id}"] = page_files
    temp.CHAT[query.from_user.id] = query.message.chat.id
    settings = await get_settings(query.message.chat.id)
    cap = CAP.get(key, "")
    del_msg = (
        f"\n\n<b>⚠️ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ ᴀꜰᴛᴇʀ <code>{get_readable_time(DELETE_TIME)}</code> ᴛᴏ ᴀᴠᴏɪᴅ ᴄᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs</b>"
        if settings.get("auto_delete") else ""
    )

    if settings.get("link"):
        links = "".join([
            f"<b>\n\n{i}. <a href=https://t.me/{temp.U_NAME}?start=file_{query.message.chat.id}_{f['_id']}>"
            f"[{get_size(f['file_size'])}] {get_display_name(f)}</a></b>"
            for i, f in enumerate(page_files, current_offset + 1)
        ])
        btn = []
    else:
        links = ""
        btn = [[InlineKeyboardButton(
            f"🔗 {get_size(f['file_size'])}≽ {formate_file_name(f['file_name'])}",
            callback_data=f"files#{query.from_user.id}#{f['_id']}"
        )] for f in page_files]

    btn.insert(0, [
        InlineKeyboardButton("• ǫᴜᴀʟɪᴛʏ •", callback_data=f"qualities#{key}#{current_offset}#{req}"),
        InlineKeyboardButton("• ʟᴀɴɢᴜᴀɢᴇ •", callback_data=f"languages#{key}#{current_offset}#{req}")
    ])
    btn.insert(1, [InlineKeyboardButton("• sᴇɴᴅ ᴀʟʟ •", callback_data=f"batchfiles#{query.message.chat.id}#{query.id}#{query.from_user.id}")])

    nav_row = []
    if current_offset > 0:
        nav_row.append(InlineKeyboardButton("⪻ ʙᴀᴄᴋ", callback_data=f"season_search#{season}#{key}#{max(0, current_offset - max_btn)}#{original_offset}#{req}"))
    nav_row.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="pages"))
    if current_offset + max_btn < total_filtered:
        nav_row.append(InlineKeyboardButton("ɴᴇxᴛ ⪼", callback_data=f"season_search#{season}#{key}#{current_offset + max_btn}#{original_offset}#{req}"))
    btn.append(nav_row if len(nav_row) > 1 else [InlineKeyboardButton("🚸 ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇs 🚸", callback_data="buttons")])
    btn.append([InlineKeyboardButton("⪻ ʙᴀᴄᴋ ᴛᴏ ᴍᴀɪɴ ᴘᴀɢᴇ", callback_data=f"next_{req}_{key}_{original_offset}")])
    await query.message.edit_text(
        cap + links + del_msg,
        disable_web_page_preview=True,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(btn)
    )


# Short quality code → regex pattern mapping (keeps callback_data under 64 bytes)
_QUALITY_CODE_MAP = {
    "4k":     (r'\b(2160p|4k|uhd)\b',          "4K / 2160p"),
    "1080p":  (r'\b1080p\b',                    "1080p"),
    "720p":   (r'\b720p\b',                     "720p"),
    "480p":   (r'\b480p\b',                     "480p"),
    "bluray": (r'\b(bluray|bdrip|remux)\b',     "BluRay / BDRip"),
    "webdl":  (r'\b(web[\-\s]?dl|webdl)\b',  "WEB-DL"),
    "webrip": (r'\bwebrip\b',                   "WEBRip"),
    "hdrip":  (r'\bhdrip\b',                    "HDRip"),
    "dvdrip": (r'\bdvdrip\b',                   "DVDRip"),
    "cam":    (r'\b(hdts|ts|cam|pdvd)\b',       "HDTS / CAM"),
}


@Client.on_callback_query(filters.regex(r"^qualities#"))
@Client.on_callback_query(filters.regex(r"^qualities#"))
async def quality_cb_handler(client: Client, query: CallbackQuery):
    _, key, offset, req = query.data.split("#")
    if int(req) != query.from_user.id:
        return await query.answer(script.ALRT_TXT, show_alert=True)

    search = BUTTONS.get(key)
    if not search:
        return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)

    # Get all files from cache — no DB call needed
    entry = _cache_get(search)
    all_files = entry["files"] if entry else []

    if not all_files:
        return await query.answer("⚠️ No quality info found for this search.", show_alert=True)

    # Find which quality codes actually appear in the result set
    available = []
    for code, (pattern, label) in _QUALITY_CODE_MAP.items():
        regex = re.compile(pattern, re.IGNORECASE)
        if any(
            regex.search((f.get("file_name") or "") + " " + (f.get("caption") or ""))
            for f in all_files
        ):
            available.append((code, label))

    if not available:
        return await query.answer("⚠️ No quality info found for this search.", show_alert=True)

    # Build buttons using short code — callback_data stays well under 64 bytes
    btn = []
    row = []
    for code, label in available:
        cb = f"quality_search#{code}#{key}#0#{offset}#{req}"
        row.append(InlineKeyboardButton(label, callback_data=cb))
        if len(row) == 2:
            btn.append(row)
            row = []
    if row:
        btn.append(row)
    btn.append([InlineKeyboardButton("⪻ ʙᴀᴄᴋ ᴛᴏ ᴍᴀɪɴ ᴘᴀɢᴇ", callback_data=f"next_{req}_{key}_0")])
    await query.message.edit_text(
        "<b>🎬 Available Qualities — choose one ↓↓</b>",
        reply_markup=InlineKeyboardMarkup(btn)
    )


@Client.on_callback_query(filters.regex(r"^quality_search#"))
async def quality_search(client: Client, query: CallbackQuery):
    parts = query.data.split("#")
    # format: quality_search#{code}#{key}#{offset}#{original_offset}#{req}
    _, code, key, offset, original_offset, req = parts
    if int(req) != query.from_user.id:
        return await query.answer(script.ALRT_TXT, show_alert=True)
    search = BUTTONS.get(key)
    if not search:
        return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)

    current_offset = int(offset)
    max_btn = int(MAX_BTN)

    entry = _cache_get(search)
    if entry:
        all_files = entry["files"]
    else:
        all_files = await get_all_results(search)
        if all_files:
            meta = _extract_meta(all_files)
            _cache_set(search, all_files, meta)
            _META_STORE[key] = meta

    # Look up the real regex pattern from the short code
    quality_entry = _QUALITY_CODE_MAP.get(code)
    if quality_entry:
        pattern, _ = quality_entry
        qul_re = re.compile(pattern, re.IGNORECASE)
    else:
        # Fallback: treat code as literal search term
        qul_re = re.compile(re.escape(code), re.IGNORECASE)
    qul = code  # keep for nav callbacks

    filtered_files = [
        f for f in all_files
        if qul_re.search((f.get('file_name') or '') + ' ' + (f.get('caption') or ''))
    ]
    if not filtered_files:
        return await query.answer(f"😔 No files found with that quality.", show_alert=True)

    page_files = filtered_files[current_offset:current_offset + max_btn]
    total_filtered = len(filtered_files)
    current_page = (current_offset // max_btn) + 1
    total_pages = math.ceil(total_filtered / max_btn)
    temp.FILES_ID[f"{query.message.chat.id}-{query.id}"] = page_files
    temp.CHAT[query.from_user.id] = query.message.chat.id
    settings = await get_settings(query.message.chat.id)
    cap = CAP.get(key, "")
    del_msg = (
        f"\n\n<b>⚠️ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ ᴀꜰᴛᴇʀ <code>{get_readable_time(DELETE_TIME)}</code> ᴛᴏ ᴀᴠᴏɪᴅ ᴄᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs</b>"
        if settings.get("auto_delete") else ""
    )

    if settings.get("link"):
        links = "".join([
            f"<b>\n\n{i}. <a href=https://t.me/{temp.U_NAME}?start=file_{query.message.chat.id}_{f['_id']}>"
            f"[{get_size(f['file_size'])}] {get_display_name(f)}</a></b>"
            for i, f in enumerate(page_files, current_offset + 1)
        ])
        btn = []
    else:
        links = ""
        btn = [[InlineKeyboardButton(
            f"🔗 {get_size(f['file_size'])}≽ {formate_file_name(f['file_name'])}",
            callback_data=f"files#{query.from_user.id}#{f['_id']}"
        )] for f in page_files]

    btn.insert(0, [
        InlineKeyboardButton("• ʟᴀɴɢᴜᴀɢᴇ •", callback_data=f"languages#{key}#{current_offset}#{req}"),
        InlineKeyboardButton("• sᴇᴀsᴏɴ •", callback_data=f"seasons#{key}#{current_offset}#{req}")
    ])
    btn.insert(1, [InlineKeyboardButton("• sᴇɴᴅ ᴀʟʟ •", callback_data=f"batchfiles#{query.message.chat.id}#{query.id}#{query.from_user.id}")])

    nav_row = []
    if current_offset > 0:
        nav_row.append(InlineKeyboardButton("⪻ ʙᴀᴄᴋ", callback_data=f"quality_search#{qul}#{key}#{max(0, current_offset - max_btn)}#{original_offset}#{req}"))
    nav_row.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="pages"))
    if current_offset + max_btn < total_filtered:
        nav_row.append(InlineKeyboardButton("ɴᴇxᴛ ⪼", callback_data=f"quality_search#{qul}#{key}#{current_offset + max_btn}#{original_offset}#{req}"))
    btn.append(nav_row if len(nav_row) > 1 else [InlineKeyboardButton("🚸 ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇs 🚸", callback_data="buttons")])
    btn.append([InlineKeyboardButton("⪻ ʙᴀᴄᴋ ᴛᴏ ᴍᴀɪɴ ᴘᴀɢᴇ", callback_data=f"next_{req}_{key}_{original_offset}")])
    await query.message.edit_text(
        cap + links + del_msg,
        disable_web_page_preview=True,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(btn)
    )


@Client.on_callback_query(filters.regex(r"^languages#"))
async def languages_cb_handler(client: Client, query: CallbackQuery):
    try:
        _, key, offset, req = query.data.split("#")
        if int(req) != query.from_user.id:
            return await query.answer(script.ALRT_TXT, show_alert=True)

        # Get languages from extracted metadata (DB-derived)
        meta = _META_STORE.get(key, {})
        available_langs = meta.get("languages", [])

        if not available_langs:
            return await query.answer("⚠️ No language info found for this search.", show_alert=True)

        _LANG_DISPLAY = {
            'hindi': '🇮🇳 Hindi', 'english': '🇬🇧 English',
            'tamil': '🌊 Tamil', 'telugu': '🌟 Telugu',
            'malayalam': '🌴 Malayalam', 'kannada': '🏔️ Kannada',
            'punjabi': '🎵 Punjabi', 'bengali': '🎭 Bengali',
            'gujarati': '🪔 Gujarati', 'marathi': '🎪 Marathi',
            'dual': '🔊 Dual Audio', 'multi': '🌐 Multi Audio',
        }

        btn = []
        row = []
        for lang in available_langs:
            label = _LANG_DISPLAY.get(lang, lang.title())
            row.append(InlineKeyboardButton(label, callback_data=f"lang_search#{lang}#{key}#0#{offset}#{req}"))
            if len(row) == 2:
                btn.append(row)
                row = []
        if row:
            btn.append(row)
        btn.append([InlineKeyboardButton("⪻ ʙᴀᴄᴋ ᴛᴏ ᴍᴀɪɴ ᴘᴀɢᴇ", callback_data=f"next_{req}_{key}_0")])
        await query.message.edit_text(
            "<b>🌐 Available Languages — choose one ↓↓</b>",
            reply_markup=InlineKeyboardMarkup(btn)
        )
    except Exception:
        await query.answer("Error processing request", show_alert=True)


@Client.on_callback_query(filters.regex(r"^lang_search#"))
async def lang_search(client: Client, query: CallbackQuery):
    _, lang, key, offset, original_offset, req = query.data.split("#")
    if int(req) != query.from_user.id:
        return await query.answer(script.ALRT_TXT, show_alert=True)
    search = BUTTONS.get(key)
    if not search:
        return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)

    current_offset = int(offset)
    max_btn = int(MAX_BTN)

    entry = _cache_get(search)
    if entry:
        all_files = entry["files"]
    else:
        all_files = await get_all_results(search)
        if all_files:
            meta = _extract_meta(all_files)
            _cache_set(search, all_files, meta)
            _META_STORE[key] = meta

    lang_re = re.compile(r'\b' + re.escape(lang) + r'\b', re.IGNORECASE)
    filtered_files = [
        f for f in all_files
        if lang_re.search(f.get('file_name', '') + ' ' + (f.get('caption') or ''))
    ]
    if not filtered_files:
        return await query.answer(f"😔 No {lang.title()} files found for '{search}'", show_alert=True)

    page_files = filtered_files[current_offset:current_offset + max_btn]
    total_filtered = len(filtered_files)
    current_page = (current_offset // max_btn) + 1
    total_pages = math.ceil(total_filtered / max_btn)
    temp.FILES_ID[f"{query.message.chat.id}-{query.id}"] = page_files
    temp.CHAT[query.from_user.id] = query.message.chat.id
    settings = await get_settings(query.message.chat.id)
    cap = CAP.get(key, "")
    del_msg = (
        f"\n\n<b>⚠️ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ ᴀꜰᴛᴇʀ <code>{get_readable_time(DELETE_TIME)}</code> ᴛᴏ ᴀᴠᴏɪᴅ ᴄᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs</b>"
        if settings.get("auto_delete") else ""
    )

    if settings.get("link"):
        links = "".join([
            f"<b>\n\n{i}. <a href=https://t.me/{temp.U_NAME}?start=file_{query.message.chat.id}_{f['_id']}>"
            f"[{get_size(f['file_size'])}] {get_display_name(f)}</a></b>"
            for i, f in enumerate(page_files, current_offset + 1)
        ])
        btn = []
    else:
        links = ""
        btn = [[InlineKeyboardButton(
            f"🔗 {get_size(f['file_size'])}≽ {formate_file_name(f['file_name'])}",
            callback_data=f"files#{query.from_user.id}#{f['_id']}"
        )] for f in page_files]

    btn.insert(0, [
        InlineKeyboardButton("• sᴇᴀsᴏɴ •", callback_data=f"seasons#{key}#{current_offset}#{req}"),
        InlineKeyboardButton("• ǫᴜᴀʟɪᴛʏ •", callback_data=f"qualities#{key}#{current_offset}#{req}")
    ])
    btn.insert(1, [InlineKeyboardButton("• sᴇɴᴅ ᴀʟʟ •", callback_data=f"batchfiles#{query.message.chat.id}#{query.id}#{query.from_user.id}")])

    nav_row = []
    if current_offset > 0:
        nav_row.append(InlineKeyboardButton("⪻ ʙᴀᴄᴋ", callback_data=f"lang_search#{lang}#{key}#{max(0, current_offset - max_btn)}#{original_offset}#{req}"))
    nav_row.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="pages"))
    if current_offset + max_btn < total_filtered:
        nav_row.append(InlineKeyboardButton("ɴᴇxᴛ ⪼", callback_data=f"lang_search#{lang}#{key}#{current_offset + max_btn}#{original_offset}#{req}"))
    btn.append(nav_row if len(nav_row) > 1 else [InlineKeyboardButton("🚸 ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇs 🚸", callback_data="buttons")])
    btn.append([InlineKeyboardButton("⪻ ʙᴀᴄᴋ ᴛᴏ ᴍᴀɪɴ ᴘᴀɢᴇ", callback_data=f"next_{req}_{key}_{original_offset}")])
    await query.message.edit_text(
        cap + links + del_msg,
        disable_web_page_preview=True,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(btn)
    )


@Client.on_callback_query(filters.regex(r"^spol"))
async def spoll_checker(bot, query):
    _, id, user = query.data.split('#')
    if int(user) != 0 and query.from_user.id != int(user):
        return await query.answer(script.ALRT_TXT, show_alert=True)
    movie = await get_poster(id, id=True)
    search = movie.get('title')
    await query.answer('ᴄʜᴇᴄᴋɪɴɢ ɪɴ ᴍʏ ᴅᴀᴛᴀʙᴀꜱᴇ 🌚')
    files, offset, total_results = await get_search_results(search)
    if files:
        k = (search, files, offset, total_results)
        await auto_filter(bot, query, k)
    else:
        buttons = [[InlineKeyboardButton("⚠️ ʀᴇᴏ̨ᴜᴇsᴛ ᴛᴏ ᴀᴅᴍɪɴ ⚠️", callback_data=f"req_admin#{search}#{query.from_user.id}")],
                   [InlineKeyboardButton("🚫 ᴄʟᴏsᴇ 🚫", callback_data="close_data")]]
        k = await query.message.edit_text(text=script.NO_RESULT_TXT, reply_markup=InlineKeyboardMarkup(buttons))
        await asyncio.sleep(60)
        await k.delete()


@Client.on_callback_query(filters.regex(r"^req_admin"))
async def request_to_admin(bot, query):
    _, search, user_id = query.data.split("#")
    if int(user_id) != query.from_user.id:
        return await query.answer(script.ALRT_TXT, show_alert=True)
    await send_request_common(bot, user=query.from_user, search=search, origin_message=query.message)
    await query.answer()


@Client.on_callback_query(filters.regex(r"^cancel_custom#"))
async def cancel_custom_reply(client, query: CallbackQuery):
    _, req_msg_id = query.data.split("#")
    req_msg_id = int(req_msg_id)
    prompt_id = next((pid for pid, data in CUSTOM_REPLY_WAIT.items() if data["request_msg"].id == req_msg_id), None)
    if not prompt_id:
        return await query.answer("Nothing to cancel", show_alert=True)
    data = CUSTOM_REPLY_WAIT.pop(prompt_id)
    try:
        await query.message.delete()
    except Exception:
        pass
    user_id = data["user_id"]
    msg_id = data["msg_id"]
    req_msg = data["request_msg"]
    buttons = [
        [InlineKeyboardButton("ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ", callback_data=f"already_available#{user_id}#{msg_id}"),
         InlineKeyboardButton("ɴᴏᴛ ʀᴇʟᴇᴀsᴇᴅ ʏᴇᴛ", callback_data=f"not_released#{user_id}#{msg_id}")],
        [InlineKeyboardButton("ᴛᴇʟʟ ᴍᴇ ʏᴇᴀʀ/ʟᴀɴɢᴜᴀɢᴇ", callback_data=f"year#{user_id}#{msg_id}"),
         InlineKeyboardButton("ᴄʜᴇᴄᴋ ʏᴏᴜʀ sᴘᴇʟʟɪɴɢ", callback_data=f"upload_in#{user_id}#{msg_id}")],
        [InlineKeyboardButton("ᴜᴘʟᴏᴀᴅᴇᴅ", callback_data=f"uploaded#{user_id}#{msg_id}"),
         InlineKeyboardButton("ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ", callback_data=f"not_available#{user_id}#{msg_id}")],
        [InlineKeyboardButton("ᴜᴘʟᴏᴀᴅᴇᴅ, ᴡʀᴏɴɢ sᴘᴇʟʟɪɴɢ", callback_data=f"spl_wrong#{user_id}#{msg_id}")],
        [InlineKeyboardButton("💬 ᴄᴜsᴛᴏᴍ ʀᴇᴘʟʏ", callback_data=f"custom_reply#{user_id}#{msg_id}")]
    ]
    try:
        await req_msg.edit_reply_markup(InlineKeyboardMarkup(buttons))
    except Exception:
        pass
    await query.answer("Cancelled ✖️")


@Client.on_callback_query(filters.regex(r"^cancel_wrong#"))
async def cancel_wrong_spelling(client, query: CallbackQuery):
    _, req_msg_id = query.data.split("#")
    req_msg_id = int(req_msg_id)
    prompt_id = next((pid for pid, data in WRONG_SPELL_WAIT.items() if data["request_msg"].id == req_msg_id), None)
    if not prompt_id:
        return await query.answer("Nothing to cancel", show_alert=True)
    data = WRONG_SPELL_WAIT.pop(prompt_id)
    try:
        await query.message.delete()
    except Exception:
        pass
    user_id = data["user_id"]
    msg_id = data["msg_id"]
    req_msg = data["request_msg"]
    buttons = [
        [InlineKeyboardButton("ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ", callback_data=f"already_available#{user_id}#{msg_id}"),
         InlineKeyboardButton("ɴᴏᴛ ʀᴇʟᴇᴀsᴇᴅ ʏᴇᴛ", callback_data=f"not_released#{user_id}#{msg_id}")],
        [InlineKeyboardButton("ᴛᴇʟʟ ᴍᴇ ʏᴇᴀʀ/ʟᴀɴɢᴜᴀɢᴇ", callback_data=f"year#{user_id}#{msg_id}"),
         InlineKeyboardButton("ᴄʜᴇᴄᴋ ʏᴏᴜʀ sᴘᴇʟʟɪɴɢ", callback_data=f"upload_in#{user_id}#{msg_id}")],
        [InlineKeyboardButton("ᴜᴘʟᴏᴀᴅᴇᴅ", callback_data=f"uploaded#{user_id}#{msg_id}"),
         InlineKeyboardButton("ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ", callback_data=f"not_available#{user_id}#{msg_id}")],
        [InlineKeyboardButton("ᴜᴘʟᴏᴀᴅᴇᴅ, ᴡʀᴏɴɢ sᴘᴇʟʟɪɴɢ", callback_data=f"spl_wrong#{user_id}#{msg_id}")]
    ]
    try:
        await req_msg.edit_reply_markup(InlineKeyboardMarkup(buttons))
    except Exception:
        pass
    await query.answer("Cancelled ✖️")


@Client.on_message(filters.reply & filters.chat(REQUEST_CHANNEL))
async def handle_channel_reply_input(client, message):
    reply = message.reply_to_message
    if not reply:
        return

    # Path 1: Wrong-spelling correction
    spell_data = WRONG_SPELL_WAIT.pop(reply.id, None)
    if spell_data:
        correct_name = (message.text or "").strip()
        request_msg = spell_data["request_msg"]
        user_id = spell_data["user_id"]
        msg_id = spell_data["msg_id"]
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await reply.delete()
        except Exception:
            pass
        old_text = request_msg.text or request_msg.caption or "Request"
        status_btn = [[InlineKeyboardButton("✏️ ᴜᴘʟᴏᴀᴅᴇᴅ (ᴡʀᴏɴɢ sᴘᴇʟʟɪɴɢ)", callback_data=f"ulws_alert#{user_id}")]]
        await request_msg.edit_text(f"<s>{old_text}</s>")
        await request_msg.edit_reply_markup(InlineKeyboardMarkup(status_btn))
        user_buttons = [
            [InlineKeyboardButton("👥 Movie Group", url=MOVIE_GROUP_LINK)],
            [InlineKeyboardButton("👀 View Request", url=request_msg.link)]
        ]
        try:
            await client.send_message(
                chat_id=user_id,
                text=(f"<b>Your requested file is already uploaded.\n\n✅ Correct Spelling – <code>{correct_name}</code>\n\nPlease send correct spelling in group.</b>"),
                reply_markup=InlineKeyboardMarkup(user_buttons)
            )
        except UserIsBlocked:
            await client.send_message(SUPPORT_GROUP, text=(f"<b>Your requested file is already uploaded.\n\n✅ Correct Spelling – <code>{correct_name}</code></b>"), reply_markup=InlineKeyboardMarkup(user_buttons), reply_to_message_id=msg_id)
        return

    # Path 2: Custom reply to user
    custom_data = CUSTOM_REPLY_WAIT.pop(reply.id, None)
    if not custom_data:
        return

    user_id = custom_data["user_id"]
    msg_id = custom_data["msg_id"]
    request_msg = custom_data["request_msg"]
    old_text = request_msg.text or request_msg.caption or "Request"
    status_btn = [[InlineKeyboardButton("💬 ʀᴇsᴘᴏɴᴅᴇᴅ", callback_data=f"responded_alert#{user_id}")]]
    view_btn = [[InlineKeyboardButton("♻️ ᴠɪᴇᴡ sᴛᴀᴛᴜs ♻️", url=request_msg.link)]]

    sent_ok = False
    try:
        await message.copy(chat_id=user_id, reply_markup=InlineKeyboardMarkup(view_btn))
        sent_ok = True
    except (UserIsBlocked, Exception):
        pass

    if not sent_ok:
        try:
            await client.copy_message(chat_id=SUPPORT_GROUP, from_chat_id=message.chat.id, message_id=message.id, reply_to_message_id=msg_id, reply_markup=InlineKeyboardMarkup(view_btn))
        except Exception:
            pass

    try:
        await message.delete()
    except Exception:
        pass
    try:
        await reply.delete()
    except Exception:
        pass
    try:
        await request_msg.edit_text(f"<s>{old_text}</s>")
        await request_msg.edit_reply_markup(InlineKeyboardMarkup(status_btn))
    except Exception:
        pass


@Client.on_callback_query()
async def cb_handler(client: Client, query: CallbackQuery):
    if query.data == "close_data":
        try:
            user = query.message.reply_to_message.from_user.id
        except Exception:
            user = query.from_user.id
        if int(user) != 0 and query.from_user.id != int(user):
            return await query.answer(script.ALRT_TXT, show_alert=True)
        await query.answer("ᴛʜᴀɴᴋs ꜰᴏʀ ᴄʟᴏsᴇ 🙈")
        await query.message.delete()

    elif query.data.startswith("view_req_info"):
        _, user_id = query.data.split("#")
        if query.from_user.id != int(user_id):
            return await query.answer(script.ALRT_TXT, show_alert=True)
        await query.answer("ℹ️ This request was sent from private chat or the original message was deleted.", show_alert=True)

    elif query.data == "premium":
        await query.message.reply_photo(
            photo=QR_CODE,
            caption=script.PREMIUM_TEXT,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('🎁 ʀᴇꜰᴇʀ ᴛᴏ ɢᴇᴛ ᴘʀᴇᴍɪᴜᴍ 🎁', callback_data='reff')],
                [InlineKeyboardButton('👤 ᴏᴡɴᴇʀ', url=USERNAME)],
                [InlineKeyboardButton('ᴄʟᴏsᴇ', callback_data='close_data')]
            ]))

    elif query.data == "reff":
        refer_link = f"https://t.me/{temp.U_NAME}?start=reff_{query.from_user.id}"
        btn = [[
            InlineKeyboardButton('ɪɴᴠɪᴛᴇ ʟɪɴᴋ', url=f'https://telegram.me/share/url?url={refer_link}&text=Hello!%20Experience%20a%20bot%20that%20offers%20a%20vast%20library%20of%20unlimited%20movies%20and%20series.%20%F0%9F%98%83'),
            InlineKeyboardButton(f'⏳ {silicondb.get_silicon_refer_points(query.from_user.id)}', callback_data='ref_point'),
            InlineKeyboardButton('ᴄʟᴏsᴇ', callback_data='close_data')
        ]]
        await query.message.reply_photo(
            photo="https://graph.org/file/1a2e64aee3d4d10edd930.jpg",
            caption=(f'Hey Your refer link:\n\n{refer_link}\n\nShare this link with your friends, Each time they join, you will get 10 refer points and after 100 points you will get 1 month premium subscription.'),
            reply_markup=InlineKeyboardMarkup(btn),
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()

    elif query.data == "ref_point":
        await query.answer(f'You Have: {silicondb.get_silicon_refer_points(query.from_user.id)} Refferal points.', show_alert=True)

    elif query.data == "top_search":
        searches = await process_trending_data(limit=20, format_type="keyboard")
        keyboard = create_keyboard_layout(searches)
        await query.message.reply_text(
            "<b>ᴛᴏᴘ sᴇᴀʀᴄʜᴇs ᴏꜰ ᴛʜᴇ ᴅᴀʏ 👇</b>",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True, placeholder="ᴍᴏsᴛ sᴇᴀʀᴄʜᴇs ᴏꜰ ᴛʜᴇ ᴅᴀʏ")
        )
        await query.answer()

    elif query.data == "delallcancel":
        userid = query.from_user.id
        chat_type = query.message.chat.type
        if chat_type == enums.ChatType.PRIVATE:
            await query.message.delete()
        elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            grp_id = query.message.chat.id
            st = await client.get_chat_member(grp_id, userid)
            if (st.status == enums.ChatMemberStatus.OWNER) or (str(userid) in ADMINS):
                await query.message.delete()
            else:
                await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)

    elif query.data.startswith("checksub"):
        try:
            ident, kk, file_id = query.data.split("#")
            btn = []
            if kk == "miniapp":
                fsub_channels = list(dict.fromkeys(AUTH_CHANNELS))
            else:
                chat = file_id.split("_")[0]
                settings = await get_settings(chat)
                fsub_channels = list(dict.fromkeys((settings.get('fsub', []) if settings else []) + AUTH_CHANNELS))
            btn += await is_subscribed(client, query.from_user.id, fsub_channels)
            btn += await is_req_subscribed(client, query.from_user.id, AUTH_REQ_CHANNELS)
            if btn:
                btn.append([InlineKeyboardButton("♻️ ᴛʀʏ ᴀɢᴀɪɴ ♻️", callback_data=f"checksub#{kk}#{file_id}")])
                try:
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
                except MessageNotModified:
                    pass
                await query.answer(
                    f"👋 ʜᴇʟʟᴏ {query.from_user.first_name},\n\n🛑 ʏᴏᴜ ʜᴀᴠᴇ ɴᴏᴛ ᴊᴏɪɴᴇᴅ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟs.\n👉 ᴘʟᴇᴀsᴇ ᴊᴏɪɴ ᴇᴀᴄʜ ᴏɴᴇ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ.\n",
                    show_alert=True
                )
                return
            if kk == "miniapp":
                from plugins.miniapp_plugin import send_file_with_checks
                await query.message.delete()
                await send_file_with_checks(client, query.from_user.id, file_id)
            else:
                await query.answer(url=f"https://t.me/{temp.U_NAME}?start={kk}_{file_id}")
                await query.message.delete()
        except Exception as e:
            await log_error(client, f"❌ Error in checksub callback:\n\n{repr(e)}")

    elif query.data == "buttons":
        await query.answer("ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇs 😊", show_alert=True)

    elif query.data == "pages":
        await query.answer("ᴛʜɪs ɪs ᴘᴀɢᴇs ʙᴜᴛᴛᴏɴ 😅")

    elif query.data.startswith("lang_art"):
        _, lang = query.data.split("#")
        await query.answer(f"ʏᴏᴜ sᴇʟᴇᴄᴛᴇᴅ {lang.title()} ʟᴀɴɢᴜᴀɢᴇ ⚡️", show_alert=True)

    elif query.data == "start":
        buttons = [
            [InlineKeyboardButton('⇆ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘs ⇆', url=f'http://t.me/{temp.U_NAME}?startgroup=start')],
            [InlineKeyboardButton('• ʀᴇғᴇʀ', callback_data='reff'), InlineKeyboardButton('• ᴜᴘɢʀᴀᴅᴇ', callback_data='premium')]
        ]
        await query.message.edit_text(
            text=script.START_TXT.format(query.from_user.mention, get_status(), query.from_user.id),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data.startswith("stream"):
        file_id = query.data.split('#', 1)[1]
        if IS_PREMIUM_STREAM:
            if not await db.has_premium_access(query.from_user.id):
                await query.answer("⚠️ ᴘʀᴇᴍɪᴜᴍ ᴄᴏɴᴛᴇɴᴛ ❗\n🔓 ᴜɴʟᴏᴄᴋ ɪᴛ ʙʏ ᴜᴘɢʀᴀᴅɪɴɢ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ", show_alert=True)
                await query.message.reply_text("🔒 ᴛʜɪs ꜰᴇᴀᴛᴜʀᴇ ɪs ᴏɴʟʏ ꜰᴏʀ 🏅 ᴘʀᴇᴍɪᴜᴍ ᴜsᴇʀs\n\n✨ ᴜɴʟᴏᴄᴋ ᴇxᴄʟᴜsɪᴠᴇ ᴄᴏɴᴛᴇɴᴛ ᴀɴᴅ ꜰᴇᴀᴛᴜʀᴇs\n💳 ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ ᴛᴏ ɢᴇᴛ sᴛᴀʀᴛᴇᴅ 👉 /plan")
                return
        silicon = await client.send_cached_media(chat_id=BIN_CHANNEL, file_id=file_id)
        watch = f"{URL}watch/{silicon.id}"
        download = f"{URL}download/{silicon.id}"
        btn = [[InlineKeyboardButton("ᴡᴀᴛᴄʜ ᴏɴʟɪɴᴇ", url=watch), InlineKeyboardButton("ꜰᴀsᴛ ᴅᴏᴡɴʟᴏᴀᴅ", url=download)],
               [InlineKeyboardButton('❌ ᴄʟᴏsᴇ ❌', callback_data='close_data')]]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))

    elif query.data == "features":
        buttons = [
            [InlineKeyboardButton('📸 ᴛ-ɢʀᴀᴘʜ', callback_data='telegraph'), InlineKeyboardButton('🆎️ ꜰᴏɴᴛ', callback_data='font')],
            [InlineKeyboardButton('🛢 ɢʀᴏᴜᴘ ᴄᴍᴅ', callback_data='grp_cmd'), InlineKeyboardButton('🧾 ᴀᴅᴍɪɴ ᴄᴍᴅ', callback_data='admincmd')],
            [InlineKeyboardButton('⋞ ʜᴏᴍᴇ', callback_data='start')]
        ]
        await query.message.edit_text(text=script.HELP_TXT, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

    elif query.data == "admincmd":
        if query.from_user.id not in ADMINS:
            return await query.answer('ᴛʜɪs ꜰᴇᴀᴛᴜʀᴇ ɪs ᴏɴʟʏ ꜰᴏʀ ᴀᴅᴍɪɴs !', show_alert=True)
        await query.message.edit_text(text=script.ADMIN_CMD_TXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⋞ ʙᴀᴄᴋ', callback_data='features')]]), parse_mode=enums.ParseMode.HTML)

    elif query.data == "grp_cmd":
        await query.message.edit_text(text=script.GROUP_CMD, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⇆ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘs ⇆', url=f'http://t.me/{temp.U_NAME}?startgroup=start')], [InlineKeyboardButton('⋞ ʙᴀᴄᴋ', callback_data='features')]]), parse_mode=enums.ParseMode.HTML)

    elif query.data == 'about':
        await query.message.edit_text(
            script.ABOUT_TEXT.format(temp.B_LINK),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🎁 sᴏᴜʀᴄᴇ', callback_data='source'), InlineKeyboardButton('📖 ᴅᴍᴄᴀ', callback_data='dmca')], [InlineKeyboardButton('⋞ ʜᴏᴍᴇ', callback_data='start')]]),
            disable_web_page_preview=True
        )

    elif query.data == "source":
        await query.message.edit_text(text=script.SOURCE_TXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ꜱᴏᴜʀᴄᴇ ᴄᴏᴅᴇ 📜', url='https://github.com/Silicon-Developer/Auto-Filter-Bot.git'), InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='about')]]), parse_mode=enums.ParseMode.HTML)

    elif query.data == "dmca":
        await query.message.edit_text(text=script.DISCLAIMER_TXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⇋ ʙᴀᴄᴋ ⇋", callback_data="about")]]), parse_mode=enums.ParseMode.HTML)

    elif query.data == "earn":
        await query.message.edit_text(text=script.EARN_TEXT.format(temp.B_LINK), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⋞ ʜᴏᴍᴇ', callback_data='start'), InlineKeyboardButton('sᴜᴘᴘᴏʀᴛ', user_id=ADMINS[0])]]), parse_mode=enums.ParseMode.HTML)

    elif query.data == "telegraph":
        await query.message.edit_text(text=script.TELE_TXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⋞ ʙᴀᴄᴋ', callback_data='features')]]), parse_mode=enums.ParseMode.HTML)

    elif query.data == "font":
        await query.message.edit_text(text=script.FONT_TXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⋞ ʙᴀᴄᴋ', callback_data='features')]]), parse_mode=enums.ParseMode.HTML)

    elif query.data == "all_files_delete":
        files_primary = collection.count_documents({})
        files_secondary = second_collection.count_documents({}) if is_second_db_configured() else 0
        total_files = files_primary + files_secondary
        try:
            collection.drop()
        except Exception as e:
            logger.error(f"Error dropping main collection: {e}")
        if is_second_db_configured():
            try:
                second_collection.drop()
            except Exception as e:
                logger.error(f"Error dropping second collection: {e}")
        await query.answer('ᴅᴇʟᴇᴛɪɴɢ...')
        await query.message.edit_text(f"sᴜᴄᴄᴇssꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {total_files} ꜰɪʟᴇs")

    elif query.data.startswith("delete"):
        _, query_ = query.data.split("_", 1)
        await query.message.edit('ᴅᴇʟᴇᴛɪɴɢ...')
        deleted = await delete_files(query_)
        await query.message.edit(f'ᴅᴇʟᴇᴛᴇᴅ {deleted} ꜰɪʟᴇs ɪɴ ʏᴏᴜʀ ᴅᴀᴛᴀʙᴀsᴇ ɪɴ ʏᴏᴜʀ ǫᴜᴇʀʏ {query_}')

    elif query.data.startswith("reset_grp_data"):
        grp_id = query.message.chat.id
        btn = [[InlineKeyboardButton('☕️ ᴄʟᴏsᴇ ☕️', callback_data='close_data')]]
        await save_group_settings(grp_id, 'shortner', SHORTENER_WEBSITE)
        await save_group_settings(grp_id, 'api', SHORTENER_API)
        await save_group_settings(grp_id, 'shortner_two', SHORTENER_WEBSITE2)
        await save_group_settings(grp_id, 'api_two', SHORTENER_API2)
        await save_group_settings(grp_id, 'shortner_three', SHORTENER_WEBSITE3)
        await save_group_settings(grp_id, 'api_three', SHORTENER_API3)
        await save_group_settings(grp_id, 'template', IMDB_TEMPLATE)
        await save_group_settings(grp_id, 'tutorial', TUTORIAL)
        await save_group_settings(grp_id, 'tutorial_two', TUTORIAL2)
        await save_group_settings(grp_id, 'tutorial_three', TUTORIAL3)
        await save_group_settings(grp_id, 'caption', FILE_CAPTION)
        await save_group_settings(grp_id, 'log', LOG_VR_CHANNEL)
        await save_group_settings(grp_id, 'fsub', [])
        await save_group_settings(grp_id, 'auto_filter', True)
        await save_group_settings(grp_id, 'spell_check', True)
        await save_group_settings(grp_id, 'imdb', True)
        await save_group_settings(grp_id, 'link', True)
        await save_group_settings(grp_id, 'is_verify', True)
        await save_group_settings(grp_id, 'auto_delete', True)
        await query.answer('ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ʀᴇꜱᴇᴛ...')
        await query.message.edit_text("<b>ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ʀᴇꜱᴇᴛ ɢʀᴏᴜᴘ ꜱᴇᴛᴛɪɴɢꜱ...\n\nɴᴏᴡ ꜱᴇɴᴅ /details ᴀɢᴀɪɴ</b>", reply_markup=InlineKeyboardMarkup(btn))

    elif query.data.startswith("setgs"):
        ident, set_type, status, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        if not await is_check_admin(client, int(grp_id), userid):
            await query.answer(script.ALRT_TXT, show_alert=True)
            return
        if status == "True":
            await save_group_settings(int(grp_id), set_type, False)
            await query.answer("ᴏꜰꜰ ❌")
        else:
            await save_group_settings(int(grp_id), set_type, True)
            await query.answer("ᴏɴ ✅")
        settings = await get_settings(int(grp_id))
        if settings is not None:
            buttons = [
                [InlineKeyboardButton('ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ', callback_data=f'setgs#auto_filter#{settings["auto_filter"]}#{grp_id}'), InlineKeyboardButton('ᴏɴ ✓' if settings["auto_filter"] else 'ᴏꜰꜰ ✗', callback_data=f'setgs#auto_filter#{settings["auto_filter"]}#{grp_id}')],
                [InlineKeyboardButton('ɪᴍᴅʙ', callback_data=f'setgs#imdb#{settings["imdb"]}#{grp_id}'), InlineKeyboardButton('ᴏɴ ✓' if settings["imdb"] else 'ᴏꜰꜰ ✗', callback_data=f'setgs#imdb#{settings["imdb"]}#{grp_id}')],
                [InlineKeyboardButton('sᴘᴇʟʟ ᴄʜᴇᴄᴋ', callback_data=f'setgs#spell_check#{settings["spell_check"]}#{grp_id}'), InlineKeyboardButton('ᴏɴ ✓' if settings["spell_check"] else 'ᴏꜰꜰ ✗', callback_data=f'setgs#spell_check#{settings["spell_check"]}#{grp_id}')],
                [InlineKeyboardButton('ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ', callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{grp_id}'), InlineKeyboardButton(f'{get_readable_time(DELETE_TIME)}' if settings["auto_delete"] else 'ᴏꜰꜰ ✗', callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{grp_id}')],
                [InlineKeyboardButton('ʀᴇsᴜʟᴛ ᴍᴏᴅᴇ', callback_data=f'setgs#link#{settings["link"]}#{str(grp_id)}'), InlineKeyboardButton('⛓ ʟɪɴᴋ' if settings["link"] else '🧲 ʙᴜᴛᴛᴏɴ', callback_data=f'setgs#link#{settings["link"]}#{str(grp_id)}')],
                [InlineKeyboardButton('ᴠᴇʀɪꜰʏ', callback_data=f'setgs#is_verify#{settings["is_verify"]}#{grp_id}'), InlineKeyboardButton('ᴏɴ ✓' if settings["is_verify"] else 'ᴏꜰꜰ ✗', callback_data=f'setgs#is_verify#{settings["is_verify"]}#{grp_id}')],
                [InlineKeyboardButton('❌ ᴄʟᴏsᴇ ❌', callback_data='close_data')]
            ]
            d = await query.message.edit_reply_markup(InlineKeyboardMarkup(buttons))
            await asyncio.sleep(300)
            await d.delete()
        else:
            await query.message.edit_text("<b>ꜱᴏᴍᴇᴛʜɪɴɢ ᴡᴇɴᴛ ᴡʀᴏɴɢ</b>")

    elif query.data.startswith("group_pm"):
        _, grp_id = query.data.split("#")
        btn = await group_setting_buttons(int(grp_id))
        gt = await client.get_chat(int(grp_id))
        await query.message.edit(
            text=f"ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ɢʀᴏᴜᴘ ꜱᴇᴛᴛɪɴɢꜱ ✅\n\nɢʀᴏᴜᴘ ɴᴀᴍᴇ - {gt.title} ⚙ \nɢʀᴏᴜᴘ ɪᴅ - <code>{gt.id}</code>",
            reply_markup=InlineKeyboardMarkup(btn)
        )

    elif query.data.startswith("batchfiles"):
        ident, group_id, message_id, user = query.data.split("#")
        if int(user) != query.from_user.id:
            await query.answer(script.ALRT_TXT, show_alert=True)
            return
        link = f"https://telegram.me/{temp.U_NAME}?start=allfiles_{group_id}-{message_id}"
        await query.answer(url=link)
        return

    elif query.data.startswith("show_options"):
        ident, user_id, msg_id = query.data.split("#")
        chnl_id = query.message.chat.id
        userid = query.from_user.id
        buttons = [
            [InlineKeyboardButton("ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ", callback_data=f"already_available#{user_id}#{msg_id}"), InlineKeyboardButton("ɴᴏᴛ ʀᴇʟᴇᴀsᴇᴅ ʏᴇᴛ", callback_data=f"not_released#{user_id}#{msg_id}")],
            [InlineKeyboardButton("ᴛᴇʟʟ ᴍᴇ ʏᴇᴀʀ/ʟᴀɴɢᴜᴀɢᴇ", callback_data=f"year#{user_id}#{msg_id}"), InlineKeyboardButton("ᴄʜᴇᴄᴋ ʏᴏᴜʀ sᴘᴇʟʟɪɴɢ", callback_data=f"upload_in#{user_id}#{msg_id}")],
            [InlineKeyboardButton("ᴜᴘʟᴏᴀᴅᴇᴅ", callback_data=f"uploaded#{user_id}#{msg_id}"), InlineKeyboardButton("ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ", callback_data=f"not_available#{user_id}#{msg_id}")],
            [InlineKeyboardButton("ᴜᴘʟᴏᴀᴅᴇᴅ, ᴡʀᴏɴɢ sᴘᴇʟʟɪɴɢ", callback_data=f"spl_wrong#{user_id}#{msg_id}")],
            [InlineKeyboardButton("💬 ᴄᴜsᴛᴏᴍ ʀᴇᴘʟʏ", callback_data=f"custom_reply#{user_id}#{msg_id}")]
        ]
        try:
            st = await client.get_chat_member(chnl_id, userid)
            if st.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
                await query.message.edit_reply_markup(InlineKeyboardMarkup(buttons))
            elif st.status == enums.ChatMemberStatus.MEMBER:
                await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)
        except pyrogram.errors.exceptions.bad_request_400.UserNotParticipant:
            await query.answer("⚠️ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀ ᴍᴇᴍʙᴇʀ ᴏꜰ ᴛʜɪꜱ ᴄʜᴀɴɴᴇʟ, ꜰɪʀꜱᴛ ᴊᴏɪɴ", show_alert=True)

    elif query.data.startswith("not_released"):
        ident, user_id, msg_id = query.data.split("#")
        chnl_id = query.message.chat.id
        buttons = [[InlineKeyboardButton("🚫 ɴᴏᴛ ʀᴇʟᴇᴀsᴇᴅ 🚫", callback_data=f"na_alert#{user_id}")]]
        btn = [[InlineKeyboardButton("♻️ ᴠɪᴇᴡ sᴛᴀᴛᴜs ♻️", url=f"{query.message.link}")]]
        st = await client.get_chat_member(chnl_id, query.from_user.id)
        if st.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            user = await client.get_users(user_id)
            request = query.message.text
            await query.answer("Message sent to requester")
            await query.message.edit_text(f"<s>{request}</s>")
            await query.message.edit_reply_markup(InlineKeyboardMarkup(buttons))
            try:
                await client.send_message(chat_id=user_id, text="<b>sᴏʀʀʏ ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ ɪs ɴᴏᴛ ʀᴇʟᴇᴀsᴇᴅ ʏᴇᴛ 😢.\n ᴀᴅᴍɪɴ ᴋᴇᴇᴘ ᴍᴏɴɪᴛᴏʀ ʏᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ, ᴡᴀɪᴛ ғᴏʀ ʀᴇʟᴇᴀsᴇ ᴀɴᴅ ᴛʜᴇɴ sᴇɴᴅ ʀᴇᴏ̨ᴜᴇsᴛᴇᴅ ғɪʟᴇ ɴᴀᴍᴇ ɪɴ ɢʀᴏᴜᴘ.</b>", reply_markup=InlineKeyboardMarkup(btn))
            except UserIsBlocked:
                await client.send_message(SUPPORT_GROUP, text=f"<b>💥 ʜᴇʟʟᴏ {user.mention},\n\nsᴏʀʀʏ ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ ɪs ɴᴏᴛ ʀᴇʟᴇᴀsᴇᴅ ʏᴇᴛ 😢.\n ᴀᴅᴍɪɴ ᴋᴇᴇᴘ ᴍᴏɴɪᴛᴏʀ ʏᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ, ᴡᴀɪᴛ ғᴏʀ ʀᴇʟᴇᴀsᴇ ᴀɴᴅ ᴛʜᴇɴ sᴇɴᴅ ʀᴇᴏ̨ᴜᴇsᴛᴇᴅ ғɪʟᴇ ɴᴀᴍᴇ ɪɴ ɢʀᴏᴜᴘ.</b>", reply_markup=InlineKeyboardMarkup(btn), reply_to_message_id=int(msg_id))
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("not_available"):
        ident, user_id, msg_id = query.data.split("#")
        chnl_id = query.message.chat.id
        buttons = [[InlineKeyboardButton("🚫 ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ 🚫", callback_data=f"hm_alert#{user_id}")]]
        btn = [[InlineKeyboardButton("♻️ ᴠɪᴇᴡ sᴛᴀᴛᴜs ♻️", url=f"{query.message.link}")]]
        st = await client.get_chat_member(chnl_id, query.from_user.id)
        if st.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            user = await client.get_users(user_id)
            request = query.message.text
            await query.answer("Message sent to requester")
            await query.message.edit_text(f"<s>{request}</s>")
            await query.message.edit_reply_markup(InlineKeyboardMarkup(buttons))
            try:
                await client.send_message(chat_id=user_id, text="❌ <b>Your requested movie is not available on the internet.</b>", reply_markup=InlineKeyboardMarkup(btn))
            except UserIsBlocked:
                await client.send_message(SUPPORT_GROUP, text="❌ <b>Your requested movie is not available on the internet.</b>", reply_markup=InlineKeyboardMarkup(btn), reply_to_message_id=int(msg_id))
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("uploaded"):
        ident, user_id, msg_id = query.data.split("#")
        chnl_id = query.message.chat.id
        buttons = [[InlineKeyboardButton("🙂 ᴜᴘʟᴏᴀᴅᴇᴅ 🙂", callback_data=f"ul_alert#{user_id}")]]
        btn = [[InlineKeyboardButton("♻️ ᴠɪᴇᴡ sᴛᴀᴛᴜs ♻️", url=f"{query.message.link}")]]
        st = await client.get_chat_member(chnl_id, query.from_user.id)
        if st.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            user = await client.get_users(user_id)
            request = query.message.text
            await query.answer("Message sent to requester")
            await query.message.edit_text(f"<s>{request}</s>")
            await query.message.edit_reply_markup(InlineKeyboardMarkup(buttons))
            try:
                await client.send_message(chat_id=user_id, text="<b>ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ ɪs ᴜᴘʟᴏᴀᴅᴇᴅ ☺️, ᴊᴜsᴛ ʀᴇ-sᴇɴᴅ ᴍᴏᴠɪᴇ ɴᴀᴍᴇ ɪɴ ɢʀᴏᴜᴘ</b>", reply_markup=InlineKeyboardMarkup(btn))
            except UserIsBlocked:
                await client.send_message(SUPPORT_GROUP, text=f"<b>💥 ʜᴇʟʟᴏ {user.mention},\n\nʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ ɪs ᴜᴘʟᴏᴀᴅᴇᴅ ☺️, ᴊᴜsᴛ ʀᴇ-sᴇɴᴅ ᴍᴏᴠɪᴇ ɴᴀᴍᴇ ɪɴ ɢʀᴏᴜᴘ</b>", reply_markup=InlineKeyboardMarkup(btn), reply_to_message_id=int(msg_id))
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("already_available"):
        ident, user_id, msg_id = query.data.split("#")
        chnl_id = query.message.chat.id
        buttons = [[InlineKeyboardButton("🫤 ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ 🫤", callback_data=f"aa_alert#{user_id}")]]
        btn = [[InlineKeyboardButton("♻️ ᴠɪᴇᴡ sᴛᴀᴛᴜs ♻️", url=f"{query.message.link}")]]
        st = await client.get_chat_member(chnl_id, query.from_user.id)
        if st.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            user = await client.get_users(user_id)
            request = query.message.text
            await query.answer("Message sent to requester")
            await query.message.edit_text(f"<s>{request}</s>")
            await query.message.edit_reply_markup(InlineKeyboardMarkup(buttons))
            try:
                await client.send_message(chat_id=user_id, text="<b>ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ ɪs ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ 😋, ᴊᴜsᴛ ʀᴇ-sᴇɴᴅ ᴍᴏᴠɪᴇ ɴᴀᴍᴇ ɪɴ ɢʀᴏᴜᴘ</b>", reply_markup=InlineKeyboardMarkup(btn))
            except UserIsBlocked:
                await client.send_message(SUPPORT_GROUP, text=f"<b>💥 ʜᴇʟʟᴏ {user.mention},\n\nʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ ɪs ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ 😋, ᴊᴜsᴛ ʀᴇ-sᴇɴᴅ ᴍᴏᴠɪᴇ ɴᴀᴍᴇ ɪɴ ɢʀᴏᴜᴘ</b>", reply_markup=InlineKeyboardMarkup(btn), reply_to_message_id=int(msg_id))
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("upload_in"):
        ident, user_id, msg_id = query.data.split("#")
        chnl_id = query.message.chat.id
        buttons = [[InlineKeyboardButton("⚠️ ᴄʜᴇᴄᴋ ʏᴏᴜʀ sᴘᴇʟʟɪɴɢ ⚠️", callback_data=f"upload_alert#{user_id}")]]
        btn = [[InlineKeyboardButton("♻️ ᴠɪᴇᴡ sᴛᴀᴛᴜs ♻️", url=f"{query.message.link}")]]
        st = await client.get_chat_member(chnl_id, query.from_user.id)
        if st.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            user = await client.get_users(user_id)
            request = query.message.text
            await query.answer("Message sent to requester")
            await query.message.edit_text(f"<s>{request}</s>")
            await query.message.edit_reply_markup(InlineKeyboardMarkup(buttons))
            try:
                await client.send_message(chat_id=user_id, text="<b>ᴀᴅᴍɪɴ ᴄᴀɴ'ᴛ ғɪɴᴅ ᴀɴʏ ᴍᴏᴠɪᴇ ᴀɴᴅ sᴇʀɪᴇs ᴏғ ᴛʜɪs ɴᴀᴍᴇ \nᴍᴀᴋᴇ sᴜʀᴇ, ʏᴏᴜʀ sᴘᴇʟʟɪɴɢ ɪs ᴄᴏʀʀᴇᴄᴛ ⚠️. ᴄʜᴇᴄᴋ sᴘᴇʟʟɪɴɢ ᴏɴ ɢᴏᴏɢʟᴇ ᴀɴᴅ ᴛʜᴇɴ ʀᴇᴏ̨ᴜᴇsᴛ ᴀɢᴀɪɴ ❗</b>", reply_markup=InlineKeyboardMarkup(btn))
            except UserIsBlocked:
                await client.send_message(SUPPORT_GROUP, text=f"<b>💥 ʜᴇʟʟᴏ {user.mention},\n\nᴀᴅᴍɪɴ ᴄᴀɴ'ᴛ ғɪɴᴅ ᴀɴʏ ᴍᴏᴠɪᴇ ᴀɴᴅ sᴇʀɪᴇs ᴏғ ᴛʜɪs ɴᴀᴍᴇ \nᴍᴀᴋᴇ sᴜʀᴇ, ʏᴏᴜʀ sᴘᴇʟʟɪɴɢ ɪs ᴄᴏʀʀᴇᴄᴛ ⚠️. ᴄʜᴇᴄᴋ sᴘᴇʟʟɪɴɢ ᴏɴ ɢᴏᴏɢʟᴇ ᴀɴᴅ ᴛʜᴇɴ ʀᴇᴏ̨ᴜᴇsᴛ ᴀɢᴀɪɴ ❗</b>", reply_markup=InlineKeyboardMarkup(btn), reply_to_message_id=int(msg_id))
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("year"):
        ident, user_id, msg_id = query.data.split("#")
        chnl_id = query.message.chat.id
        buttons = [[InlineKeyboardButton("⚠️ ᴛᴇʟʟ ᴍᴇ ʏᴇᴀʀꜱ & ʟᴀɴɢᴜᴀɢᴇ ⚠️", callback_data=f"yrs_alert#{user_id}")]]
        btn = [[InlineKeyboardButton("♻️ ᴠɪᴇᴡ sᴛᴀᴛᴜs ♻️", url=f"{query.message.link}")]]
        st = await client.get_chat_member(chnl_id, query.from_user.id)
        if st.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            user = await client.get_users(user_id)
            request = query.message.text
            await query.answer("Message sent to requester")
            await query.message.edit_text(f"<s>{request}</s>")
            await query.message.edit_reply_markup(InlineKeyboardMarkup(buttons))
            try:
                await client.send_message(chat_id=user_id, text="<b>ʙʀᴏ ᴘʟᴇᴀꜱᴇ ᴛᴇʟʟ ᴍᴇ ʏᴇᴀʀꜱ, ʟᴀɴɢᴜᴀɢᴇ, ʙᴏʟʟʏᴡᴏᴏᴅ ᴏʀ ʜᴏʟʟʏᴡᴏᴏᴅ ᴇᴛᴄ., ᴛʜᴇɴ ɪ ᴡɪʟʟ ᴜᴘʟᴏᴀᴅ 😬\n ᴊᴜsᴛ ʀᴇ-sᴇɴᴅ ʀᴇᴏ̨ᴜᴇsᴛ ᴡɪᴛʜ ᴍᴏʀᴇ ɪɴғᴏ.</b>", reply_markup=InlineKeyboardMarkup(btn))
            except UserIsBlocked:
                await client.send_message(SUPPORT_GROUP, text=f"<b>💥 ʜᴇʟʟᴏ {user.mention},\n\nʙʀᴏ ᴘʟᴇᴀꜱᴇ ᴛᴇʟʟ ᴍᴇ ʏᴇᴀʀꜱ, ʟᴀɴɢᴜᴀɢᴇ, ʙᴏʟʟʏᴡᴏᴏᴅ ᴏʀ ʜᴏʟʟʏᴡᴏᴏᴅ ᴇᴛᴄ., ᴛʜᴇɴ ɪ ᴡɪʟʟ ᴜᴘʟᴏᴀᴅ 😬\n ᴊᴜsᴛ ʀᴇ-sᴇɴᴅ ʀᴇᴏ̨ᴜᴇsᴛ ᴡɪᴛʜ ᴍᴏʀᴇ ɪɴғᴏ.</b>", reply_markup=InlineKeyboardMarkup(btn), reply_to_message_id=int(msg_id))
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("spl_wrong"):
        ident, user_id, msg_id = query.data.split("#")
        chnl_id = query.message.chat.id
        st = await client.get_chat_member(chnl_id, query.from_user.id)
        if st.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            return await query.answer(script.ALRT_TXT, show_alert=True)
        prompt = await client.send_message(
            chnl_id, "✏️ <b>Send correct spelling</b>",
            reply_to_message_id=query.message.id,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_wrong#{query.message.id}")]])
        )
        WRONG_SPELL_WAIT[prompt.id] = {
            "user_id": int(user_id), "msg_id": int(msg_id),
            "request_msg": query.message, "prompt_id": prompt.id
        }
        await query.answer()

    elif query.data.startswith("rj_alert"):
        ident, user_id = query.data.split("#")
        if str(query.from_user.id) in user_id:
            await query.answer("sᴏʀʀʏ ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ ɪs ʀᴇᴊᴇᴄᴛ", show_alert=True)
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("na_alert"):
        ident, user_id = query.data.split("#")
        if str(query.from_user.id) in user_id:
            await query.answer("sᴏʀʀʏ ʏᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ɪs ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ, ᴍᴀᴋᴇ sᴜʀᴇ ɪᴛ's ʀᴇʟᴇᴀsᴇᴅ. ɪғ ʏᴇs, ᴛʜᴇɴ ɢɪᴠᴇ ᴜs sᴏᴍᴇ ᴛɪᴍᴇ 🤗", show_alert=True)
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("hm_alert"):
        ident, user_id = query.data.split("#")
        if str(query.from_user.id) in user_id:
            await query.answer("❌ Your requested movie is not available on the internet.", show_alert=True)
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("ul_alert"):
        ident, user_id = query.data.split("#")
        if str(query.from_user.id) in user_id:
            await query.answer("ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ ɪs ᴜᴘʟᴏᴀᴅᴇᴅ", show_alert=True)
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("aa_alert"):
        ident, user_id = query.data.split("#")
        if str(query.from_user.id) in user_id:
            await query.answer("ʏᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ɪs ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ, ʏᴏᴜ ɴᴇᴇᴅ ᴛᴏ ᴄʜᴇᴄᴋ ғɪʀsᴛ ᴀɴᴅ ᴛʜᴇɴ ᴍᴀᴋᴇ ᴀ ʀᴇᴏ̨ᴜᴇsᴛ 🤨", show_alert=True)
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("upload_alert"):
        ident, user_id = query.data.split("#")
        if str(query.from_user.id) in user_id:
            await query.answer("ʏᴏᴜ ᴜɴᴇᴅᴜᴄᴀᴛᴇᴅ, ᴄʜᴇᴄᴋ ʏᴏᴜʀ sᴘᴇʟʟɪɴɢ 😑", show_alert=True)
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("yrs_alert"):
        ident, user_id = query.data.split("#")
        if str(query.from_user.id) in user_id:
            await query.answer("ᴅᴜᴅᴇ ʏᴏᴜ ɴᴇᴇᴅ ᴛᴏ ᴘʀᴏᴠɪᴅᴇ ᴍᴏʀᴇ ɪɴғᴏ 😑 (ʟɪᴋᴇ : ʏᴇᴀʀ, ʟᴀɴɢᴜᴀɢᴇ, ʜᴏʟʟʏᴡᴏᴏᴅ ᴏʀ ʙᴏʟʟʏᴡᴏᴏᴅ)", show_alert=True)
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("ulws_alert"):
        ident, user_id = query.data.split("#")
        if str(query.from_user.id) in user_id:
            await query.answer("Correct spelling provided by admin ✏️", show_alert=True)
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("responded_alert"):
        ident, user_id = query.data.split("#")
        if str(query.from_user.id) in user_id:
            await query.answer("ᴀᴅᴍɪɴ ʜᴀs ʀᴇᴘʟɪᴇᴅ ᴛᴏ ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ 💬", show_alert=True)
        else:
            await query.answer(script.ALRT_TXT, show_alert=True)

    elif query.data.startswith("custom_reply"):
        ident, user_id, msg_id = query.data.split("#")
        chnl_id = query.message.chat.id
        try:
            st = await client.get_chat_member(chnl_id, query.from_user.id)
            if st.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
                return await query.answer(script.ALRT_TXT, show_alert=True)
        except pyrogram.errors.exceptions.bad_request_400.UserNotParticipant:
            return await query.answer("⚠️ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀ ᴍᴇᴍʙᴇʀ ᴏꜰ ᴛʜɪꜱ ᴄʜᴀɴɴᴇʟ, ꜰɪʀꜱᴛ ᴊᴏɪɴ", show_alert=True)
        prompt = await client.send_message(
            chnl_id, "💬 <b>Send your custom reply message for the user</b>",
            reply_to_message_id=query.message.id,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_custom#{query.message.id}")]])
        )
        CUSTOM_REPLY_WAIT[prompt.id] = {
            "user_id": int(user_id), "msg_id": int(msg_id),
            "request_msg": query.message, "prompt_id": prompt.id
        }
        await query.answer()
