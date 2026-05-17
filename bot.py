from pyrogram import Client, __version__, filters
from pyrogram.raw.all import layer
from database.users_chats_db import db
from info import API_ID, API_HASH, ADMINS, BOT_TOKEN, LOG_CHANNEL, PORT, SUPPORT_GROUP
from utils import temp
from typing import Union, Optional, AsyncGenerator
from pyrogram import types
from Script import script
from datetime import date, datetime
import datetime
import pytz
from aiohttp import web
from plugins import check_expired_premium, set_silicon_commands, keep_alive, reset_file_limits_daily
from database.ia_filterdb import collection, second_collection 
from web import web_app
import time

class Bot(Client):
    def __init__(self):
        super().__init__(
            name='Silicon',
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            sleep_threshold=5,
            workers=150,
            plugins={"root": "plugins"}
        )
        
    async def start(self):
        st = time.time()
        b_users, b_chats = await db.get_banned()
        temp.BANNED_USERS = b_users
        temp.BANNED_CHATS = b_chats
        await super().start()
        me = await self.get_me()
        temp.ME = me.id
        temp.BOT = self
        temp.U_NAME = me.username
        temp.B_NAME = me.first_name
        temp.B_LINK = me.mention
        self.username = '@' + me.username
        await set_silicon_commands(self)
        self.loop.create_task(check_expired_premium(self))
        self.loop.create_task(keep_alive())
        self.loop.create_task(reset_file_limits_daily()) 
        # Start BM25 + SymSpell background build and periodic refresh
        try:
            from database.search_engine import bm25_refresh_task, alias_manager
            from database.ia_filterdb import collection as _col, second_collection as _scol
            from database.users_chats_db import db as _udb
            # Set alias collection (uses same DATABASE_URI MongoDB)
            from pymongo import MongoClient
            from info import DATABASE_URI, DATABASE_NAME
            _alias_client = MongoClient(DATABASE_URI)
            alias_manager.set_collection(_alias_client[DATABASE_NAME]['search_aliases'])
            self.loop.create_task(bm25_refresh_task(_col, _scol))
            print('BM25 search engine scheduled')
        except Exception as _se:
            print(f'BM25 search engine setup skipped: {_se}')
        print(f"{me.first_name} is started now ❤️")
        tz = pytz.timezone('Asia/Kolkata')
        today = date.today()
        now = datetime.datetime.now(tz)
        timee = now.strftime("%H:%M:%S %p") 
        app = web.AppRunner(web_app)
        await app.setup()
        bind_address = "0.0.0.0"
        await web.TCPSite(app, bind_address, PORT).start()
        await self.send_message(chat_id=LOG_CHANNEL, text=f"<b>{me.mention} ʀᴇsᴛᴀʀᴛᴇᴅ 🤖\n\n📆 ᴅᴀᴛᴇ - <code>{today}</code>\n🕙 ᴛɪᴍᴇ - <code>{timee}</code>\n🌍 ᴛɪᴍᴇ ᴢᴏɴᴇ - <code>Asia/Kolkata</code></b>")
        tt = time.time() - st
        seconds = int(datetime.timedelta(seconds=tt).seconds)
        for admin in ADMINS:
            await self.send_message(chat_id=admin, text=f"<b>✅ ʙᴏᴛ ʀᴇsᴛᴀʀᴛᴇᴅ\n🕥 ᴛɪᴍᴇ ᴛᴀᴋᴇɴ - <code>{seconds} sᴇᴄᴏɴᴅs</code></b>")

    async def stop(self, *args):
        await super().stop()
        print("Bot stopped.")
    
    async def iter_messages(
        self,
        chat_id: Union[int, str],
        limit: int,
        offset: int = 0,
       ) -> Optional[AsyncGenerator["types.Message", None]]:
        current = offset
        while True:
            new_diff = min(200, limit - current)
            if new_diff <= 0:
                return
            messages = await self.get_messages(chat_id, list(range(current, current+new_diff+1)))
            for message in messages:
                yield message
                current += 1

app = Bot()
app.run()
