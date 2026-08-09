import logging
from pyrogram.errors import InputUserDeactivated, UserNotParticipant, FloodWait, UserIsBlocked, ChatAdminRequired, PeerIdInvalid
from info import LONG_IMDB_DESCRIPTION, IS_VERIFY, START_IMG, LOG_CHANNEL, DELETE_TIME
import asyncio
from pyrogram.types import Message, ReplyKeyboardMarkup, InlineKeyboardButton
from pyrogram import enums
import pytz, re, os 
from shortzy import Shortzy
from datetime import datetime
from typing import Any
from database.users_chats_db import db
from database.extra_db import silicondb


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BANNED = {}

class temp(object):
    BANNED_USERS = []
    BANNED_CHATS = []
    ME = None
    CURRENT=int(os.environ.get("SKIP", 2))
    CANCEL = False
    U_NAME = None
    B_NAME = None
    B_LINK = None
    BOT = None
    FILES_ID = {}
    USERS_CANCEL = False
    GROUPS_CANCEL = False    
    CHAT = {}

def formate_file_name(file_name):
    file_name = ' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@') and not x.startswith('www.'), file_name.split()))
    return file_name

async def is_req_subscribed(bot, user_id, rqfsub_channels):
    btn = []
    for ch_id in rqfsub_channels:
        if await db.has_joined_channel(user_id, ch_id):
            continue
        try:
            member = await bot.get_chat_member(ch_id, user_id)
            if member.status != enums.ChatMemberStatus.BANNED:
                await db.add_join_req(user_id, ch_id)
                continue
        except UserNotParticipant:
            pass
        except Exception as e:
            logger.error(f"Error checking membership in {ch_id}: {e}")
        try:
            chat   = await bot.get_chat(ch_id)
            invite = await bot.create_chat_invite_link(
                ch_id,
                creates_join_request=True
            )
            btn.append([InlineKeyboardButton(f"⛔️ Join {chat.title}", url=invite.invite_link)])
        except ChatAdminRequired:
            logger.warning(f"Bot not admin in {ch_id}")
        except Exception as e:
            logger.warning(f"Invite link error for {ch_id}: {e}")     
    return btn

async def is_subscribed(bot, user_id, fsub_channels):
    btn = []
    for channel_id in fsub_channels:
        try:
            chat = await bot.get_chat(int(channel_id))
            await bot.get_chat_member(channel_id, user_id)
        except UserNotParticipant:
            try:
                invite = await bot.create_chat_invite_link(channel_id, creates_join_request=False)
                btn.append([InlineKeyboardButton(f"📢 Join {chat.title}", url=invite.invite_link)])
            except Exception as e:
                logger.warning(f"Failed to create invite for {channel_id}: {e}")
        except Exception as e:
            logger.exception(f"is_subscribed error for {channel_id}: {e}")
            pass
    return btn

async def process_trending_data(limit, format_type):
    messages = silicondb.get_silicon_messages(limit)
    validator = lambda text: bool(re.match('^[a-zA-Z0-9 ]*$', text))

    unique_searches = []
    seen = set()

    for msg in messages:
        normalized = msg.lower()
        if normalized not in seen and validator(msg):
            seen.add(normalized)
            truncate_len = 32 if format_type == "keyboard" else 32
            processed = msg[:truncate_len] + "..." if len(msg) > 35 else msg
            unique_searches.append(processed)

    return unique_searches

def create_keyboard_layout(searches):
    return [searches[i:i+2] for i in range(0, len(searches), 2)]

def extract_limit_from_command(command_parts, default):
    try:
        return int(command_parts[1]) if len(command_parts) > 1 else default
    except (IndexError, ValueError):
        return -1 if len(command_parts) > 1 else default

def generate_trend_list(searches):
    return "\n".join([f"{idx+1}. <b>{search}</b>" for idx, search in enumerate(searches)])

async def get_poster(query, bulk=False, id=False, file=None):
    """
    Movie/series metadata lookup used for IMDb-style captions.

    This used to go through Cinemagoer (IMDbPY), which screen-scraped
    imdb.com directly. That approach is dead — imdb.com returns HTTP 403 to
    non-browser scrapers — and the `cinemagoer` package's newer releases
    default to a local "s3" dataset access system that expects a multi-GB
    IMDb dataset file on disk. There's no such file here, so `Cinemagoer()`
    now crashes immediately on import with an invalid SQLite URL, which was
    taking down the whole bot on Koyeb before this function even ran.

    This now reuses the TMDB/OMDb metadata fetcher already wired up in
    plugins/helper/Imdbposter.py (the same source channel.py's auto-poster
    feature uses — TMDB first, OMDb as fallback) and reshapes the result
    into the same field set this function has always returned, so callers
    in pm_filter.py don't need to change.
    """
    from plugins.helper.Imdbposter import get_movie_details, get_movie_detailsx
    from info import TMDB_POSTER

    details = {}
    if id:
        # Direct IMDb-ID lookup — only OMDb's `i=` param supports this,
        # TMDB search is title-based only.
        details = await get_movie_details(query, id=True) or {}
    else:
        year_match = re.findall(r'[1-2]\d{3}$', query.strip())
        year = year_match[0] if year_match else None
        if TMDB_POSTER:
            tmdb_result = await get_movie_detailsx(query, year=year)
            if tmdb_result and not tmdb_result.get("error"):
                details = tmdb_result
        if not details:
            details = await get_movie_details(query, file=file) or {}

    if not details:
        return None
    if bulk:
        # Old Cinemagoer bulk mode returned a list of candidate matches for
        # a picker UI; nothing in the codebase actually uses bulk=True, so
        # this just wraps the single best match for signature compatibility.
        return [details]

    imdb_id = details.get('imdb_id') or ""
    return {
        'title': details.get('title') or query,
        'votes': details.get('votes') or "N/A",
        "aka": details.get('title') or "N/A",
        "seasons": details.get('seasons') or "N/A",
        "box_office": "N/A",
        'localized_title': details.get('title') or query,
        'kind': details.get('kind') or "movie",
        "imdb_id": imdb_id or "N/A",
        "cast": details.get('cast') or "N/A",
        "runtime": details.get('runtime') or "N/A",
        "countries": details.get('countries') or "N/A",
        "certificates": "N/A",
        "languages": details.get('languages') or "N/A",
        "director": details.get('director') or "N/A",
        "writer": "N/A",
        "producer": "N/A",
        "composer": "N/A",
        "cinematographer": "N/A",
        "music_team": "N/A",
        "distributors": "N/A",
        'release_date': details.get('year') or "N/A",
        'year': details.get('year') or "N/A",
        'genres': details.get('genres') or "N/A",
        'poster': details.get('poster_url') or START_IMG,
        'plot': details.get('plot') or "N/A",
        'rating': details.get('rating') or "N/A",
        'url': details.get('url') or (f"https://www.imdb.com/title/{imdb_id}" if imdb_id else "")
    }

async def users_broadcast(user_id, message, is_pin):
    try:
        m=await message.copy(chat_id=user_id)
        if is_pin:
            await m.pin(both_sides=True)
        return True, "Success"
    except FloodWait as e:
        await asyncio.sleep(e.x)
        return await users_broadcast(user_id, message)
    except InputUserDeactivated:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id}-Removed from Database, since deleted account.")
        return False, "Deleted"
    except UserIsBlocked:
        logging.info(f"{user_id} -Blocked the bot.")
        await db.delete_user(user_id)
        return False, "Blocked"
    except PeerIdInvalid:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id} - PeerIdInvalid")
        return False, "Error"
    except Exception as e:
        return False, "Error"

async def groups_broadcast(chat_id, message, is_pin):
    try:
        m = await message.copy(chat_id=chat_id)
        if is_pin:
            try:
                await m.pin()
            except:
                pass
        return "Success"
    except FloodWait as e:
        await asyncio.sleep(e.x)
        return await groups_broadcast(chat_id, message)
    except Exception as e:
        await db.delete_chat(chat_id)
        return "Error"

async def junk_group(chat_id, message):
    try:
        kk = await message.copy(chat_id=chat_id)
        await kk.delete(True)
        return True, "Succes", 'mm'
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await junk_group(chat_id, message)
    except Exception as e:
        await db.delete_chat(int(chat_id))       
        logging.info(f"{chat_id} - PeerIdInvalid")
        return False, "deleted", f'{e}\n\n'
    

async def clear_junk(user_id, message):
    try:
        key = await message.copy(chat_id=user_id)
        await key.delete(True)
        return True, "Success"
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await clear_junk(user_id, message)
    except InputUserDeactivated:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id}-Removed from Database, since deleted account.")
        return False, "Deleted"
    except UserIsBlocked:
        logging.info(f"{user_id} -Blocked the bot.")
        return False, "Blocked"
    except PeerIdInvalid:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id} - PeerIdInvalid")
        return False, "Error"
    except Exception as e:
        return False, "Error"

async def get_settings(group_id):
    settings = await db.get_settings(group_id)
    return settings
  
async def save_group_settings(group_id, key, value):
    current = await get_settings(group_id)
    current.update({key: value})

    await db.update_settings(group_id, current)

def get_size(size):
    units = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB"]
    size = float(size)
    i = 0
    while size >= 1024.0 and i < len(units):
        i += 1
        size /= 1024.0
    return "%.2f %s" % (size, units[i])

def get_name(name):
    regex = re.sub(r'@\w+', '', name)
    return regex

def list_to_str(k):    
    if not k:
        return "N/A"
    elif len(k) == 1:
        return str(k[0])
    else:
        return ', '.join(str(item) for item in k)


async def get_shortlink(link, grp_id, is_second_shortener=False, is_third_shortener=False):
    settings = await get_settings(grp_id)
    
    if IS_VERIFY:
        if is_third_shortener:             
            api, site = settings['api_three'], settings['shortner_three']
        else:
            if is_second_shortener:
                api, site = settings['api_two'], settings['shortner_two']
            else:
                api, site = settings['api'], settings['shortner']
        
        shortzy = Shortzy(api, site)
        try:
            link = await shortzy.convert(link)
        except Exception as e:
            link = await shortzy.get_quick_link(link)
    
    return link 

def get_file_id(message: "Message") -> Any:
    media_types = (
        "audio",
        "document",
        "photo",
        "sticker",
        "animation",
        "video",
        "voice",
        "video_note",
    )    
    if message.media:
        for attr in media_types:
            media = getattr(message, attr, None)
            if media:
                setattr(media, "message_type", attr)
                return media

def get_hash(media_msg: Message) -> str:
    media = get_file_id(media_msg)
    return getattr(media, "file_unique_id", "")[:6]

def get_status():
    tz = pytz.timezone('Asia/Colombo')
    hour = datetime.now(tz).time().hour
    if 5 <= hour < 12:
        sts = "ɢᴏᴏᴅ ᴍᴏʀɴɪɴɢ"
    elif 12 <= hour < 18:
        sts = "ɢᴏᴏᴅ ᴀꜰᴛᴇʀɴᴏᴏɴ"
    else:
        sts = "ɢᴏᴏᴅ ᴇᴠᴇɴɪɴɢ"
    return sts

async def is_check_admin(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except:
        return False

async def get_seconds(time_string):
    def extract_value_and_unit(ts):
        value = ""
        unit = ""
        index = 0
        while index < len(ts) and ts[index].isdigit():
            value += ts[index]
            index += 1
        unit = ts[index:].lstrip()
        if value:
            value = int(value)
        return value, unit
    value, unit = extract_value_and_unit(time_string)
    if unit == 's':
        return value
    elif unit == 'min':
        return value * 60
    elif unit == 'hour':
        return value * 3600
    elif unit == 'day':
        return value * 86400
    elif unit == 'month':
        return value * 86400 * 30
    elif unit == 'year':
        return value * 86400 * 365
    else:
        return 0

def get_readable_time(seconds):
    periods = [('days', 86400), ('hour', 3600), ('min', 60), ('sec', 1)]
    result = ''
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            result += f'{int(period_value)}{period_name}'
    return result


async def log_error(client, error_message):
    try:
        await client.send_message(
            chat_id=LOG_CHANNEL, 
            text=f"<b>⚠️ Error Log:</b>\n<code>{error_message}</code>"
        )
    except Exception as e:
        print(f"Failed to log error: {e}")

async def group_setting_buttons(grp_id):
    settings = await get_settings(grp_id)
    buttons = [
                [
                    InlineKeyboardButton('ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ', callback_data=f'setgs#auto_filter#{settings["auto_filter"]}#{grp_id}'),
                    InlineKeyboardButton('ᴏɴ ✓' if settings["auto_filter"] else 'ᴏғғ ✗', callback_data=f'setgs#auto_filter#{settings["auto_filter"]}#{grp_id}')
                ],[
                    InlineKeyboardButton('ɪᴍᴅʙ', callback_data=f'setgs#imdb#{settings["imdb"]}#{grp_id}'),
                    InlineKeyboardButton('ᴏɴ ✓' if settings["imdb"] else 'ᴏғғ ✗', callback_data=f'setgs#imdb#{settings["imdb"]}#{grp_id}')
                ],[
                    InlineKeyboardButton('sᴘᴇʟʟ ᴄʜᴇᴄᴋ', callback_data=f'setgs#spell_check#{settings["spell_check"]}#{grp_id}'),
                    InlineKeyboardButton('ᴏɴ ✓' if settings["spell_check"] else 'ᴏғғ ✗', callback_data=f'setgs#spell_check#{settings["spell_check"]}#{grp_id}')
                ],[
                    InlineKeyboardButton('ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ', callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{grp_id}'),
                    InlineKeyboardButton(f'{get_readable_time(DELETE_TIME)}' if settings["auto_delete"] else 'ᴏғғ ✗', callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{grp_id}')
                ],[
                    InlineKeyboardButton('ʀᴇsᴜʟᴛ ᴍᴏᴅᴇ', callback_data=f'setgs#link#{settings["link"]}#{str(grp_id)}'),
                    InlineKeyboardButton('⛓ ʟɪɴᴋ' if settings["link"] else '🧲 ʙᴜᴛᴛᴏɴ', callback_data=f'setgs#link#{settings["link"]}#{str(grp_id)}')
                ],[
                    InlineKeyboardButton('ᴠᴇʀɪғʏ', callback_data=f'setgs#is_verify#{settings["is_verify"]}#{grp_id}'),
                    InlineKeyboardButton('ᴏɴ ✓' if settings["is_verify"] else 'ᴏғғ ✗', callback_data=f'setgs#is_verify#{settings["is_verify"]}#{grp_id}')
                ],[
                InlineKeyboardButton('❌ ᴄʟᴏsᴇ ❌', callback_data='close_data')
    ]]
    return buttons
