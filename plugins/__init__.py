import aiohttp, pytz, asyncio, logging
from database.extra_db import silicondb
from asyncio import sleep 
from datetime import datetime, timedelta, time
from database.users_chats_db import db
from info import LOG_CHANNEL, URL
from pyrogram.types import BotCommand

COMMANDS = {
    "start": "ꜱᴛᴀʀᴛ ᴛʜᴇ ʙᴏᴛ.",
    "trendlist": "ɢᴇᴛ ᴛʀᴇɴᴅɪɴɢ ꜱᴇᴀʀᴄʜ ʟɪꜱᴛ.",
    "myplan" : "sᴇᴇ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ ᴘʟᴀɴ.",
    "plan" :"ᴄʜᴇᴄᴋ ᴘʀᴇᴍɪᴜᴍ ᴘʀɪᴄᴇ.",
    "miniapp": "ᴏᴘᴇɴ ᴍᴏᴠɪᴇ ᴡᴇʙsɪᴛᴇ ɪɴ ᴛᴇʟᴇɢʀᴀᴍ.",
    "admin_cmd": "ᴏɴʟʏ ғᴏʀ ᴀᴅᴍɪɴ."
}


async def check_expired_premium(client):
    while 1:
        data = await db.get_expired(datetime.now())
        for user in data:
            user_id = user["id"]
            await db.remove_premium_access(user_id)
            try:
                user = await client.get_users(user_id)
                await client.send_message(
                    chat_id=user_id,
                    text=f"<b>ʜᴇʏ {user.mention},\n\nʏᴏᴜʀ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss ʜᴀs ᴇxᴘɪʀᴇᴅ, ᴛʜᴀɴᴋ ʏᴏᴜ ꜰᴏʀ ᴜsɪɴɢ ᴏᴜʀ sᴇʀᴠɪᴄᴇ 😊\n\nɪꜰ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴛᴀᴋᴇ ᴛʜᴇ ᴘʀᴇᴍɪᴜᴍ ᴀɢᴀɪɴ, ᴛʜᴇɴ ᴄʟɪᴄᴋ ᴏɴ ᴛʜᴇ /plan ꜰᴏʀ ᴛʜᴇ ᴅᴇᴛᴀɪʟs ᴏꜰ ᴛʜᴇ ᴘʟᴀɴs...</b>"
                )
                await client.send_message(LOG_CHANNEL, text=f"<b>#Premium_Expire\n\nUser name: {user.mention}\nUser id: <code>{user_id}</code>")
            except Exception as e:
                print(e)
            await sleep(0.5)
        await sleep(1)

async def set_silicon_commands(client):
    try:
        commands = [BotCommand(cmd, desc) for cmd, desc in COMMANDS.items()]
        await client.set_bot_commands(commands)
        print("✅ Bot commands updated successfully!")
    except Exception as e:
        print(f"❌ Error setting bot commands: {e}")

async def reset_file_limits_daily():
    tz = pytz.timezone('Asia/Kolkata')
    while True:
        now = datetime.now(tz)
        target_time = time(23, 59)
        target_datetime = tz.localize(datetime.combine(now.date(), target_time))
        if now > target_datetime:
            target_datetime += timedelta(days=1)
        time_diff = (target_datetime - now).total_seconds()
        await asyncio.sleep(time_diff)
        silicondb.reset_all_file_limits()
        print("Files count reset successfully")

async def keep_alive():
    async with aiohttp.ClientSession() as session:
        while True:
            await sleep(298)
            try:
                async with session.get(URL) as resp:
                    if resp.status != 200:
                        print(f"⚠️ Ping Error! Status: {resp.status}")
            except Exception as e:
                print(f"❌ Ping Failed: {e}")   
