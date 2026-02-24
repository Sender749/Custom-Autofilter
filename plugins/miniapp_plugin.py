import asyncio
import json
import logging
import random
import string
import os

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated

from database.ia_filterdb import (
    collection, second_collection,
    get_file_details, is_second_db_configured,
)
from database.users_chats_db import db
from database.extra_db import silicondb
from utils import (
    get_size, formate_file_name, temp, get_settings,
    is_subscribed, is_req_subscribed, get_shortlink, get_status,
)
from info import (
    ADMINS, AUTH_CHANNELS, AUTH_REQ_CHANNELS,
    FILES_DATABASE_URL, DATABASE_NAME, COLLECTION_NAME,
    TMDB_API_KEY, LOG_CHANNEL, BIN_CHANNEL,
    IS_FILE_LIMIT, FILES_LIMIT, IS_VERIFY,
    TWO_VERIFY_GAP, THREE_VERIFY_GAP,
    FILE_AUTO_DEL_TIMER, FSUB_PICS,
    TUTORIAL, TUTORIAL2, TUTORIAL3,
)

logger = logging.getLogger(__name__)

# ─── config ───────────────────────────────────────────────────────────────────
try:
    from info import MINI_APP_URL as _INFO_URL
    MINI_APP_URL = os.environ.get('MINI_APP_URL', _INFO_URL)
except ImportError:
    MINI_APP_URL = os.environ.get('MINI_APP_URL', 'https://your-domain.com/miniapp')


# ─── /miniapp command ─────────────────────────────────────────────────────────

@Client.on_message(filters.command('miniapp') & filters.incoming)
async def open_miniapp(client, message):
    """Send the Mini App launch button."""
    btn = [[
        InlineKeyboardButton(
            '🎬 Open Movie Browser',
            web_app=WebAppInfo(url=MINI_APP_URL)
        )
    ]]
    await message.reply_photo(
        photo='https://graph.org/file/56b5deb73f3b132e2bb73.jpg',
        caption=(
            '<b>🎬 Movie Browser Mini App</b>\n\n'
            'Browse recently added movies & series, search titles, '
            'view details and get files — all inside Telegram!\n\n'
            'Tap the button below to open 👇'
        ),
        reply_markup=InlineKeyboardMarkup(btn),
        parse_mode=enums.ParseMode.HTML,
    )


# ─── web app data handler (legacy/fallback — not primary path) ─────────────────

async def _is_web_app_data(_, __, message):
    return bool(getattr(message, 'web_app_data', None))

web_app_data_filter = filters.create(_is_web_app_data)


@Client.on_message(filters.private & web_app_data_filter)
async def handle_web_app_data(client, message):
    """Handle tg.sendData() calls from miniapp (fallback only)."""
    try:
        payload = json.loads(message.web_app_data.data)
    except Exception as exc:
        logger.error(f'Bad web_app_data: {exc}')
        return

    action  = payload.get('action')
    user_id = message.from_user.id

    if action == 'get_file':
        file_id = payload.get('file_id', '')
        if not file_id:
            await client.send_message(user_id, '❌ ɴᴏ ꜰɪʟᴇ ɪᴅ ʀᴇᴄᴇɪᴠᴇᴅ.')
            return
        await send_file_with_checks(client, user_id, file_id)


# ══════════════════════════════════════════════════════════════════════════════
#  THE ONE UNIVERSAL FUNCTION — used by EVERY entry point:
#    • /start miniapp_FILEID   (commands.py)
#    • checksub#miniapp#FILEID (pm_filter.py)
#    • handle_web_app_data     (above)
#
#  Sends directly to user_id via client — NO fake message objects.
#  Works identically regardless of how the miniapp was opened.
# ══════════════════════════════════════════════════════════════════════════════

async def send_file_with_checks(client, user_id: int, file_id: str):
    """
    Universal miniapp file-delivery. Runs all checks then sends to user_id.
    Call this from any entry point — it always behaves the same way.
    """
    logger.info(f'[MiniApp] send_file_with_checks: user={user_id} file_id={file_id}')

    async def _get_mention():
        try:
            u = await client.get_users(user_id)
            return u.mention
        except Exception:
            return f'<a href="tg://user?id={user_id}">User</a>'

    # ── 1. Force-subscribe check ──────────────────────────────────────────────
    if not await db.has_premium_access(user_id):
        try:
            btn = []
            if AUTH_CHANNELS:
                btn += await is_subscribed(client, user_id, AUTH_CHANNELS)
            if AUTH_REQ_CHANNELS:
                btn += await is_req_subscribed(client, user_id, AUTH_REQ_CHANNELS)

            if btn:
                logger.info(f'[MiniApp] user={user_id} — force-sub required')
                btn.append([
                    InlineKeyboardButton(
                        '♻️ ᴛʀʏ ᴀɢᴀɪɴ ♻️',
                        callback_data=f'checksub#miniapp#{file_id}',
                    )
                ])
                photo   = random.choice(FSUB_PICS) if FSUB_PICS else \
                          'https://graph.org/file/7478ff3eac37f4329c3d8.jpg'
                mention = await _get_mention()
                caption = (
                    f'👋 ʜᴇʟʟᴏ {mention}\n\n'
                    '🛑 ʏᴏᴜ ᴍᴜsᴛ ᴊᴏɪɴ ᴛʜᴇ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟs ᴛᴏ ᴄᴏɴᴛɪɴᴜᴇ.\n'
                    '👉 ᴊᴏɪɴ ᴀʟʟ ᴛʜᴇ ʙᴇʟᴏᴡ ᴄʜᴀɴɴᴇʟs ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ.'
                )
                await client.send_photo(
                    chat_id=user_id,
                    photo=photo,
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(btn),
                    parse_mode=enums.ParseMode.HTML,
                )
                return
        except Exception as e:
            logger.error(f'[MiniApp] Force-sub check error: {e}')

    # ── 2. Fetch file from DB ─────────────────────────────────────────────────
    file_doc = await get_file_details(file_id)
    if not file_doc:
        logger.warning(f'[MiniApp] user={user_id} file_id={file_id} — NOT FOUND in DB')
        await client.send_message(
            user_id,
            '<b>⚠️ ꜰɪʟᴇ ɴᴏᴛ ꜰᴏᴜɴᴅ ⚠️</b>',
            parse_mode=enums.ParseMode.HTML,
        )
        return
    logger.info(f'[MiniApp] user={user_id} — found: {file_doc.get("file_name", "")}')

    fname    = file_doc.get('file_name', '')
    fcaption = file_doc.get('caption', '')

    # grp_id=0 → global/default settings (miniapp has no group context)
    grp_id   = 0
    settings = await get_settings(grp_id)

    # ── 3. Premium / file-limit / verification checks ─────────────────────────
    if not await db.has_premium_access(user_id):
        user_verified       = await db.is_user_verified(user_id)
        is_second_shortener = await db.use_second_shortener(
            user_id, settings.get('verify_time', TWO_VERIFY_GAP)
        )
        is_third_shortener  = await db.use_third_shortener(
            user_id, settings.get('third_verify_time', THREE_VERIFY_GAP)
        )

        if IS_FILE_LIMIT and FILES_LIMIT > 0:
            current_file_count = silicondb.silicon_file_limit(user_id)

            if current_file_count >= FILES_LIMIT:
                await client.send_message(
                    user_id,
                    f'<b>⚠️ ʏᴏᴜ ʜᴀᴠᴇ ʀᴇᴀᴄʜᴇᴅ ʏᴏᴜʀ ꜰʀᴇᴇ ꜰɪʟᴇ ʟɪᴍɪᴛ '
                    f'({current_file_count}/{FILES_LIMIT}).\n\n'
                    'ᴘʟᴇᴀsᴇ ᴠᴇʀɪꜰʏ ᴛᴏ ʀᴇsᴇᴛ ʏᴏᴜʀ ʟɪᴍɪᴛ ᴏʀ ᴜᴘɢʀᴀᴅᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ.</b>',
                    parse_mode=enums.ParseMode.HTML,
                )
                # Fall through to verification check so user can verify to reset
            else:
                silicondb.increment_silicon_limit(user_id)
                current_file_count += 1

                if settings.get('is_verify', IS_VERIFY) and \
                        (not user_verified or is_second_shortener or is_third_shortener):
                    await _send_verify_prompt(
                        client, user_id, file_id, grp_id,
                        settings, is_second_shortener, is_third_shortener,
                    )
                    return

                await _do_send_file(client, user_id, file_id, file_doc, settings, current_file_count)
                return

        # Verification check when no file-limit or limit exceeded
        if settings.get('is_verify', IS_VERIFY) and \
                (not user_verified or is_second_shortener or is_third_shortener):
            await _send_verify_prompt(
                client, user_id, file_id, grp_id,
                settings, is_second_shortener, is_third_shortener,
            )
            return

    # ── 4. All checks passed (or premium user) ────────────────────────────────
    await _do_send_file(client, user_id, file_id, file_doc, settings)


async def _send_verify_prompt(client, user_id: int, file_id: str, grp_id: int,
                               settings: dict, is_second_shortener: bool,
                               is_third_shortener: bool):
    """Send verification prompt directly to user_id."""
    verify_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))
    await db.create_verify_id(user_id, verify_id)
    temp.CHAT[user_id] = grp_id

    verify_url = await get_shortlink(
        f'https://telegram.me/{temp.U_NAME}?start=notcopy_{user_id}_{verify_id}_{file_id}',
        grp_id, is_second_shortener, is_third_shortener,
    )
    tutorial = (
        settings.get('tutorial_three', TUTORIAL3) if is_third_shortener
        else (settings.get('tutorial_two', TUTORIAL2) if is_second_shortener
              else settings.get('tutorial', TUTORIAL))
    )
    buttons = [
        [InlineKeyboardButton(text='♻️ ᴠᴇʀɪғʏ ♻️', url=verify_url)],
        [InlineKeyboardButton(text='❗️ ʜᴏᴡ ᴛᴏ ᴠᴇʀɪғʏ ❓', url=tutorial)],
    ]
    from Script import script
    is_verified_third = await db.user_verified(user_id)
    msg = (
        script.THIRDT_VERIFICATION_TEXT if is_verified_third
        else (script.SECOND_VERIFICATION_TEXT if is_second_shortener
              else script.VERIFICATION_TEXT)
    )
    try:
        u       = await client.get_users(user_id)
        mention = u.mention
    except Exception:
        mention = f'<a href="tg://user?id={user_id}">User</a>'

    d = await client.send_message(
        chat_id=user_id,
        text=msg.format(mention, get_status()),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML,
    )
    await asyncio.sleep(300)
    try:
        await d.delete()
    except Exception:
        pass


async def _do_send_file(client, user_id: int, file_id: str,
                        file_doc: dict, settings: dict,
                        current_file_count: int = None):
    """Send the file directly to user_id. No message object. No side-effects."""
    fname    = file_doc.get('file_name', '')
    fcaption = file_doc.get('caption', '')

    file_limit_info = ''
    if current_file_count is not None and FILES_LIMIT > 0:
        file_limit_info = (
            f'\n\n📊 ʏᴏᴜ ʜᴀᴠᴇ ʀᴇᴄᴇɪᴠᴇᴅ {current_file_count}/{FILES_LIMIT} ꜰʀᴇᴇ ꜰɪʟᴇs'
        )

    try:
        f_caption = settings['caption'].format(
            file_name=formate_file_name(fname),
            file_size=get_size(file_doc.get('file_size', 0)),
            file_caption=fcaption,
        ) + file_limit_info
    except Exception:
        f_caption = (
            f'<b>📁 {formate_file_name(fname or fcaption)}</b>\n'
            f'<b>💾 Size:</b> <code>{get_size(file_doc.get("file_size", 0))}</code>'
            + file_limit_info
        )

    btn = [[InlineKeyboardButton('✛ ᴡᴀᴛᴄʜ & ᴅᴏᴡɴʟᴏᴀᴅ ✛', callback_data=f'stream#{file_id}')]]

    try:
        logger.info(f'[MiniApp] send_cached_media → user={user_id}')
        toDel = await client.send_cached_media(
            chat_id=user_id,
            file_id=file_id,
            caption=f_caption,
            reply_markup=InlineKeyboardMarkup(btn),
        )
        logger.info(f'[MiniApp] send_cached_media SUCCESS: user={user_id}')
    except FloodWait as fw:
        logger.warning(f'[MiniApp] FloodWait {fw.value}s for user={user_id}')
        await asyncio.sleep(fw.value)
        return await _do_send_file(client, user_id, file_id, file_doc, settings, current_file_count)
    except (UserIsBlocked, InputUserDeactivated) as e:
        logger.warning(f'[MiniApp] User unreachable user={user_id}: {e}')
        return
    except Exception as exc:
        logger.error(f'[MiniApp] send_cached_media FAILED user={user_id}: {exc}')
        try:
            await client.send_message(
                user_id,
                '❌ ᴄᴏᴜʟᴅ ɴᴏᴛ sᴇɴᴅ ᴛʜᴇ ꜰɪʟᴇ. ᴘʟᴇᴀsᴇ sᴇᴀʀᴄʜ ɪɴ ᴛʜᴇ ʙᴏᴛ ᴅɪʀᴇᴄᴛʟʏ.',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        '🔍 Search in Bot',
                        switch_inline_query_current_chat=(fname or fcaption)[:40],
                    )
                ]]),
            )
        except Exception:
            pass
        return

    time_text = (
        f'{FILE_AUTO_DEL_TIMER / 60:.0f} ᴍɪɴᴜᴛᴇs'
        if FILE_AUTO_DEL_TIMER >= 60
        else f'{FILE_AUTO_DEL_TIMER} sᴇᴄᴏɴᴅs'
    )
    delCap      = (f'<b>ʏᴏᴜʀ ғɪʟᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ᴀғᴛᴇʀ {time_text} '
                   f'ᴛᴏ ᴀᴠᴏɪᴅ ᴄᴏᴘʏʀɪɢʜᴛ ᴠɪᴏʟᴀᴛɪᴏɴs!</b>')
    afterDelCap = (f'<b>ʏᴏᴜʀ ғɪʟᴇ ɪs ᴅᴇʟᴇᴛᴇᴅ ᴀғᴛᴇʀ {time_text} '
                   f'ᴛᴏ ᴀᴠᴏɪᴅ ᴄᴏᴘʏʀɪɢʜᴛ ᴠɪᴏʟᴀᴛɪᴏɴs!</b>')

    try:
        replyed = await client.send_message(
            chat_id=user_id,
            text=delCap,
            reply_to_message_id=toDel.id,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        replyed = None

    await asyncio.sleep(FILE_AUTO_DEL_TIMER)
    try:
        await toDel.delete()
    except Exception:
        pass
    if replyed:
        try:
            await replyed.edit(afterDelCap)
        except Exception:
            pass


logger.info('✅ MiniApp plugin loaded — universal send_file_with_checks')
