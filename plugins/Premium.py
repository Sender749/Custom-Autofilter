import re, pytz, random, string, hashlib
from datetime import datetime, timedelta
from asyncio import sleep 
import datetime, time
from info import ADMINS, LOG_CHANNEL, QR_CODE, USERNAME
from Script import script 
from utils import get_seconds, get_status, temp
from database.users_chats_db import db 
from pyrogram import Client, filters 
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors.exceptions.bad_request_400 import MessageTooLong
import traceback

@Client.on_message(filters.command("add_premium") & filters.user(ADMINS))
async def add_premium(client, message):
    try:
        _, user_id, time, *custom_message = message.text.split(" ", 3)
        custom_message = "**Tʜᴀɴᴋ ʏᴏᴜ ꜰᴏʀ ᴘᴜʀᴄʜᴀsɪɴɢ ᴛʜᴇ ᴘʀᴇᴍɪᴜᴍ ᴘᴀᴄᴋᴀɢᴇ. Nᴏᴡ, ʟᴇᴠᴇʀᴀɢᴇ ɪᴛs ꜰᴜʟʟ ᴘᴏᴛᴇɴᴛɪᴀʟ**" if not custom_message else " ".join(custom_message)
        time_zone = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
        current_time = time_zone.strftime("%d-%m-%Y : %I:%M:%S %p")
        user = await client.get_users(user_id)
        seconds = await get_seconds(time)
        if seconds > 0:
            expiry_time = datetime.datetime.now() + timedelta(seconds=seconds)
            user_data = {"id": user.id, "expiry_time": expiry_time}
            await db.update_user(user_data)
            data = await db.get_user(user.id)
            expiry = data.get("expiry_time")
            expiry_str_in_ist = expiry.astimezone(pytz.timezone("Asia/Kolkata")).strftime("%d-%m-%Y  :  %I:%M:%S %p")
            await message.reply_text(f"<b><u>Premium Access Added To The User</u>\n\n👤 User: {user.mention}\n\n🪪 User id: <code>{user_id}</code>\n\n⏰ Premium Access: {time}\n\n🎩 Joining : {current_time}\n\n⌛️ Expiry: {expiry_str_in_ist}.\n\n<code>{custom_message}</code></b>", disable_web_page_preview=True)
            await client.send_message(chat_id=user_id, text=f"<b>ʜɪɪ {user.mention},\n\n<u>ᴘʀᴇᴍɪᴜᴍ ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴀᴄᴄᴏᴜɴᴛ</u> 😀\n\nᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss - {time}\n\n⏰ ᴊᴏɪɴɪɴɢ - {current_time}\n\n⌛️ ᴇxᴘɪʀᴇ ɪɴ - {expiry_str_in_ist}\n\n<code>{custom_message}</code></b>", disable_web_page_preview=True)
            await client.send_message(LOG_CHANNEL, text=f"#Added_Premium\n\n👤 User - {user.mention}\n\n🪪 User Id - <code>{user_id}</code>\n\n⏰ Premium Access - {time}\n\n🎩 Joining - {current_time}\n\n⌛️ Expiry - {expiry_str_in_ist}\n\n<code>{custom_message}</code>", disable_web_page_preview=True)
        else:
            await message.reply_text("<b>⚠️ Invalid Format, Use This Format - <code>/add_premium 1030335104 1day</code>\n\n<u>Time Format -</u>\n\n<code>1 day for day\n1 hour for hour\n1 min for minutes\n1 month for month\n1 year for year</code>\n\nChange As Your Wish Like 2, 3, 4, 5 etc....</b>")
    except ValueError:
        await message.reply_text("<b>⚠️ Invalid Format, Use This Format - <code>/add_premium 1030335104 1day</code>\n\n<u>Time Format -</u>\n\n<code>1 day for day\n1 hour for hour\n1 min for minutes\n1 month for month\n1 year for year</code>\n\nChange As Your Wish Like 2, 3, 4, 5 etc....</b>")
    except Exception as e:
        traceback.print_exc()
        await message.reply_text(f"error - {e}")


@Client.on_message(filters.command("remove_premium") & filters.user(ADMINS))
async def remove_premium(client, message):
    if len(message.command) == 2:
        user_id = int(message.command[1])
        user = await client.get_users(user_id)
        if await db.remove_premium_access(user_id):
            await message.reply_text("<b>sᴜᴄᴄᴇssꜰᴜʟʟʏ ʀᴇᴍᴏᴠᴇᴅ 💔</b>")
            await client.send_message(
                chat_id=user_id,
                text=f"<b>ʜᴇʏ {user.mention},\n\nʏᴏᴜʀ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss ʜᴀs ʙᴇᴇɴ ʀᴇᴍᴏᴠᴇᴅ 😕</b>"
            )
        else:
            await message.reply_text("<b>👀 ᴜɴᴀʙʟᴇ ᴛᴏ ʀᴇᴍᴏᴠᴇ, ᴀʀᴇ ʏᴏᴜ sᴜʀᴇ ɪᴛ ᴡᴀs ᴀ ᴘʀᴇᴍɪᴜᴍ ᴜsᴇʀ ɪᴅ??</b>")
    else:
        await message.reply_text("Usage: <code>/remove_premium user_id</code>")

@Client.on_message(filters.command("myplan"))
async def myplan(client, message):
    user = message.from_user.mention 
    user_id = message.from_user.id
    data = await db.get_user(message.from_user.id)
    if data and data.get("expiry_time"):
        expiry = data.get("expiry_time") 
        expiry_ist = expiry.astimezone(pytz.timezone("Asia/Kolkata"))
        expiry_str_in_ist = expiry.astimezone(pytz.timezone("Asia/Kolkata")).strftime("%d-%m-%Y  ⏰: %I:%M:%S %p")            
        current_time = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
        time_left = expiry_ist - current_time
        days = time_left.days
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        time_left_str = f"{days} days, {hours} hours, {minutes} minutes"
        await message.reply_text(f"#Premium_user_data:\n\n👤 User: {user}\n\n🪙 User Id: <code>{user_id}</code>\n\n⏰ Time Left: {time_left_str}\n\n⌛️ Expiry: {expiry_str_in_ist}.")   
    else:
        btn = [
            [InlineKeyboardButton("⚠️ ᴄʟᴏsᴇ / ᴅᴇʟᴇᴛᴇ ⚠️", callback_data="close_data")]
        ]
        reply_markup = InlineKeyboardMarkup(btn)         
        await message.reply_text(f"**ʜᴇʏ {user}.. 💔\n\nʏᴏᴜ ᴅᴏ ɴᴏᴛ ʜᴀᴠᴇ ᴀɴʏ ᴀᴄᴛɪᴠᴇ ᴘʀᴇᴍɪᴜᴍ ᴘʟᴀɴs, ɪꜰ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴛᴀᴋᴇ ᴘʀᴇᴍɪᴜᴍ ᴛʜᴇɴ ᴄʟɪᴄᴋ ᴏɴ /plan ᴛᴏ ᴋɴᴏᴡ ᴀʙᴏᴜᴛ ᴛʜᴇ ᴘʟᴀɴ**",reply_markup=reply_markup)

@Client.on_message(filters.command("check_plan") & filters.user(ADMINS))
async def check_plan(client, message):
    if len(message.text.split()) == 1:
        await message.reply_text("ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ᴡɪᴛʜ ᴜsᴇʀ ɪᴅ... ʟɪᴋᴇ\n\n /check_plan ᴜsᴇʀ_ɪᴅ")
        return
    user_id = int(message.text.split(' ')[1])
    user_data = await db.get_user(user_id)

    if user_data and user_data.get("expiry_time"):
        expiry = user_data.get("expiry_time")
        expiry_ist = expiry.astimezone(pytz.timezone("Asia/Kolkata"))
        expiry_str_in_ist = expiry.astimezone(pytz.timezone("Asia/Kolkata")).strftime("%d-%m-%Y %I:%M:%S %p")
        current_time = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
        time_left = expiry_ist - current_time
        days = time_left.days
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        time_left_str = f"{days} ᴅᴀʏs, {hours} ʜᴏᴜʀs, {minutes} ᴍɪɴᴜᴛᴇs"
        response = (
            f"ᴜsᴇʀ ɪᴅ: {user_id}\n"
            f"ɴᴀᴍᴇ: {(await client.get_users(user_id)).mention}\n"
            f"ᴇxᴘɪʀʏ ᴅᴀᴛᴇ: {expiry_str_in_ist}\n"
            f"ᴇxᴘɪʀʏ ᴛɪᴍᴇ: {time_left_str}"
        )
    else:
        response = "ᴜsᴇʀ ʜᴀᴠᴇ ɴᴏᴛ ᴀ ᴘʀᴇᴍɪᴜᴍ..."
    await message.reply_text(response)

@Client.on_message(filters.command('plan') & filters.incoming)
async def plan(client, message):
    user_id = message.from_user.id
    if message.from_user.username:
        user_info = f"@{message.from_user.username}"
    else:
        user_info = f"{message.from_user.mention}"
    log_message = f"<b><u>🚫 ᴛʜɪs ᴜsᴇʀs ᴛʀʏ ᴛᴏ ᴄʜᴇᴄᴋ /plan</u> {temp.B_LINK}\n\n- ɪᴅ - `{user_id}`\n- ɴᴀᴍᴇ - {user_info}</b>"
    btn = [
        [InlineKeyboardButton("👤 ᴏᴡɴᴇʀ", url=USERNAME)],
        [InlineKeyboardButton("🗑 ᴄʟᴏsᴇ / ᴅᴇʟᴇᴛᴇ 🗑", callback_data="close_data")]
    ]
    await message.reply_photo(
        photo=(QR_CODE),
        caption=script.PREMIUM_TEXT, 
        reply_markup=InlineKeyboardMarkup(btn))
    await client.send_message(LOG_CHANNEL, log_message)

@Client.on_message(filters.command("premium_user") & filters.user(ADMINS))
async def premium_user(client, message):
    aa = await message.reply_text("Fetching ...")  
    users = await db.get_all_users()
    users_list = []
    async for user in users:
        users_list.append(user)    
    user_data = {user['id']: await db.get_user(user['id']) for user in users_list}    
    new_users = []
    for user in users_list:
        user_id = user['id']
        data = user_data.get(user_id)
        expiry = data.get("expiry_time") if data else None        
        if expiry:
            expiry_ist = expiry.astimezone(pytz.timezone("Asia/Kolkata"))
            expiry_str_in_ist = expiry_ist.strftime("%d-%m-%Y %I:%M:%S %p")          
            current_time = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
            time_left = expiry_ist - current_time
            days, remainder = divmod(time_left.total_seconds(), 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, _ = divmod(remainder, 60)            
            time_left_str = f"{int(days)} days, {int(hours)} hours, {int(minutes)} minutes"            
            user_info = await client.get_users(user_id)
            user_str = (
                f"{len(new_users) + 1}. ᴜsᴇʀ ɪᴅ: {user_id}\n"
                f"ɴᴀᴍᴇ: {user_info.mention}\n"
                f"ᴇxᴘɪʀʏ ᴅᴀᴛᴇ: {expiry_str_in_ist}\n"
                f"ᴇxᴘɪʀʏ ᴛɪᴍᴇ: {time_left_str}\n\n"
            )
            new_users.append(user_str)
    new = "ᴘᴀɪᴅ ᴜsᴇʀs - \n\n" + "\n".join(new_users)   
    try:
        await aa.edit_text(new)
    except MessageTooLong:
        with open('usersplan.txt', 'w+') as outfile:
            outfile.write(new)
        await message.reply_document('usersplan.txt', caption="ᴘᴀɪᴅ ᴜsᴇʀs:")

def hash_code(x):
    return hashlib.sha256(x.encode()).hexdigest()

async def generate_code(duration: str):
    token = "".join(random.sample(string.ascii_uppercase + string.digits, 15))
    now = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
    await db.codes.insert_one({
        "code_hash": hash_code(token),
        "duration": duration,
        "used": False,
        "created_at": now,
        "original_code": token
    })
    return token

@Client.on_message(filters.command("add_redeem") & filters.user(ADMINS))
async def cmd_add_redeem(c, m):
    if len(m.command) < 2:
        return await m.reply("⚠ ᴜsᴀɢᴇ: `/add_redeem 1month`")

    duration = " ".join(m.command[1:])
    seconds = await get_seconds(duration)
    if not seconds:
        return await m.reply(
            "❌ ɪɴᴠᴀʟɪᴅ ᴅᴜʀᴀᴛɪᴏɴ. ᴛɪᴍᴇ ꜰᴏʀᴍᴀᴛ-</u>\n\n<code>1 day for day\n1 hour for hour\n1 min for minutes\n1 month for month\n1 year for year</code>"
        )

    token = await generate_code(duration)
    silicon = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 ʀᴇᴅᴇᴇᴍ", url=f"https://t.me/{temp.U_NAME}")]])
    await m.reply(
        f"✅ ᴄᴏᴅᴇ ɢᴇɴᴇʀᴀᴛᴇᴅ\n\n🔑 `{token}`\n⌛ Validity: {duration}\n\nUse: `/redeem {token}`",
        reply_markup=silicon,
    )

@Client.on_message(filters.command("redeem"))
async def cmd_redeem(c, m):
    if len(m.command) < 2:
        return await m.reply("⚠ ᴜsᴀɢᴇ: `/redeem <code>`")

    code, uid = m.command[1], m.from_user.id
    if await db.has_premium_access(uid):
        return await m.reply("❌ ʏᴏᴜ ᴀʟʀᴇᴀᴅʏ ʜᴀᴠᴇ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss.")

    entry = await db.codes.find_one({"code_hash": hash_code(code)})
    if not entry: 
        return await m.reply("🚫 ɪɴᴠᴀʟɪᴅ ᴏʀ ᴇxᴘɪʀᴇᴅ ᴄᴏᴅᴇ.")

    if entry["used"]:
        return await m.reply("🚫 ᴄᴏᴅᴇ ᴀʟʀᴇᴀᴅʏ ʀᴇᴅᴇᴇᴍᴇᴅ.")

    seconds = await get_seconds(entry["duration"])
    if not seconds:
        return await m.reply("🚫 ɪɴᴠᴀʟɪᴅ ᴅᴜʀᴀᴛɪᴏɴ ɪɴ ᴄᴏᴅᴇ.")

    expiry = datetime.datetime.now() + timedelta(seconds=seconds)
    await db.update_user({"id": uid, "expiry_time": expiry})
    await db.codes.update_one({"_id": entry["_id"]}, {"$set": {"used": True, "user_id": uid}})

    ist_expiry = expiry.astimezone(pytz.timezone("Asia/Kolkata"))
    exp_str = ist_expiry.strftime("%d-%m-%Y %I:%M:%S %p")
    await m.reply(f"🎉 ᴄᴏᴅᴇ ʀᴇᴅᴇᴇᴍᴇᴅ!\n\n✨ ᴅᴜʀᴀᴛɪᴏɴ: {entry['duration']}\n⌛ ᴇxᴘɪʀᴇs ᴏɴ: {exp_str}")

@Client.on_message(filters.command("clearcodes") & filters.user(ADMINS))
async def cmd_clear(c, m):
    res = await db.codes.delete_many({})
    await m.reply(f"✅ ᴅᴇʟᴇᴛᴇᴅ {res.deleted_count} ᴄᴏᴅᴇs." if res.deleted_count else "⚠ ɴᴏ ᴄᴏᴅᴇs ꜰᴏᴜɴᴅ.")

@Client.on_message(filters.command("allcodes") & filters.user(ADMINS))
async def cmd_all(c, m):
    codes = await db.codes.find({}).to_list(None)
    if not codes: return await m.reply("⚠ ɴᴏ ᴄᴏᴅᴇs ᴀᴠᴀɪʟᴀʙʟᴇ.")

    text = "📝 **ᴀʟʟ ɢᴇɴᴇʀᴀᴛᴇᴅ ᴄᴏᴅᴇs:**\n\n"
    for x in codes:
        user_text = "ɴᴏᴛ ʀᴇᴅᴇᴇᴍᴇᴅ"
        if x.get("user_id"):
            u = await c.get_users(x["user_id"])
            user_text = f"[{u.first_name}](tg://user?id={u.id})"
        text += (f"🔑 `{x.get('original_code','?')}` | ⌛ {x.get('duration','?')}\n"
                 f"Used: {'✅' if x.get('used') else '⭕'} | By: {user_text}\n"
                 f"🕓 {x['created_at'].astimezone(pytz.timezone('Asia/Kolkata')).strftime('%d-%m-%Y %I:%M%p')}\n\n")
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await m.reply(chunk)
