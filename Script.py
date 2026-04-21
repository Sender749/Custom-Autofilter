class script(object):
    START_TXT = """<b>𝐻𝐸𝑌 {} 👋, \n\n𝑆𝐸𝑁𝐷 𝑀𝐸 𝑀𝑂𝑉𝐼𝐸, 𝑆𝐸𝑅𝐼𝐸𝑆, 𝐴𝑁𝐼𝑀𝐸, 𝑆𝐻𝑂𝑊'𝑆 𝑒𝑡𝑐. 𝑁𝐴𝑀𝐸 𝑊𝐼𝑇𝐻 𝐶𝑂𝑅𝑅𝐸𝐶𝑇 𝑆𝑃𝐸𝐿𝐿𝐼𝑁𝐺 😍\n\n<blockquote>🌿 ᴍᴀɪɴᴛᴀɪɴᴇᴅ ʙʏ : <a href="https://t.me/Navex_69">ɴᴀᴠᴇx</a></blockquote></b>"""
    
    HELP_TXT = """<b>ᴄʟɪᴄᴋ ᴏɴ ᴛʜᴇ ʙᴜᴛᴛᴏɴꜱ ʙᴇʟᴏᴡ ᴛᴏ ɢᴇᴛ ᴅᴏᴄᴜᴍᴇɴᴛᴀᴛɪᴏɴ ᴀʙᴏᴜᴛ ꜱᴘᴇᴄɪꜰɪᴄ ᴍᴏᴅᴜʟᴇꜱ..</b>"""
    
    TELE_TXT = """<b>/upload - sᴇɴᴅ ᴍᴇ ᴘɪᴄᴛᴜʀᴇ ᴏʀ ᴠɪᴅᴇᴏ ᴜɴᴅᴇʀ (𝟻ᴍʙ)

ɴᴏᴛᴇ - ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ᴡᴏʀᴋ ɪɴ ʙᴏᴛʜ ɢʀᴏᴜᴘs ᴀɴᴅ ʙᴏᴛ ᴘᴍ</b>"""

    NOT_FOUND_LOG = """#NOT_FOUND

👤 User: {}
🆔 ID: <code>{}</code>
🔍 Query: <code>{}</code>"""

    ADMIN_CMD_TXT = """<b>📚 ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅꜱ</b>

<b>🔧 ʙᴀꜱɪᴄ</b>
• /start - ᴜꜱᴇ ʙᴏᴛ ꜰᴇᴀᴛᴜʀᴇꜱ
• /stats - ᴛᴏᴛᴀʟ ᴜꜱᴇʀꜱ & ᴄʜᴀᴛꜱ
• /del_msg - ʀᴇᴍᴏᴠᴇ ꜰɪʟᴇ ɴᴀᴍᴇ ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ
• /delete `<query>` - Delete indexed files from the database that match the given query.
• /deleteall - ᴅᴇʟᴇᴛᴇ ᴀʟʟ ꜰɪʟᴇꜱ
• /index - manual index
• /channel - Show the list of all indexed channels.
• /settings - channel settings
• /details - see group details

<b>⚙️ ꜱᴇᴛᴛɪɴɢꜱ</b>
• /movie_update - ᴏɴ/ᴏꜰꜰ ᴜᴘᴅᴀᴛᴇꜱ
• /pm_search - ᴏɴ/ᴏꜰꜰ ᴘᴍ ꜱᴇᴀʀᴄʜ
• /auto_filter - ᴏɴ/ᴏꜰꜰ ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ
• /stats - check bot stats
• /movie_update - Movie update on/off
• /maintenance_mode - Maintenance mode on/off

<b>👥 ᴜꜱᴇʀꜱ & ɢʀᴏᴜᴘꜱ</b>
• /users - ᴜꜱᴇʀ ʟɪꜱᴛ & ɪᴅꜱ
• /groups - ɢʀᴏᴜᴘ ʟɪꜱᴛ & ɪᴅꜱ where bot is admin
• /leave - ʟᴇᴀᴠᴇ ᴄʜᴀᴛ
• /ban_grp - ᴅɪꜱᴀʙʟᴇ ᴄʜᴀᴛ
• /unban_grp - ᴇɴᴀʙʟᴇ ᴄʜᴀᴛ
• /ban_user - ʙᴀɴ ᴜꜱᴇʀ
• /unban_user - ᴜɴʙᴀɴ ᴜꜱᴇʀ
• /reset_grp_data - Reset group settings to default configuration.
• /details - Show current group configuration and settings.
• /setgs - Toggle group settings (auto-filter, imdb, spell check, auto-delete, etc.) Used via inline buttons after `/details`.
• /group_pm - Manage group settings from private chat.
• /group_cmd - Check all group comments 
• /clear_junk - clear un-used group

<b>📢 ᴍᴇꜱꜱᴀɢɪɴɢ</b>
• /broadcast - ᴍꜱɢ ᴛᴏ ᴀʟʟ ᴜꜱᴇʀꜱ
• /grp_broadcast - ᴍꜱɢ ᴛᴏ ᴀʟʟ ɢʀᴏᴜᴘꜱ
• /send - ᴍꜱɢ ᴛᴏ ꜱᴘᴇᴄɪꜰɪᴄ ᴜꜱᴇʀ

<b>💎 ᴘʀᴇᴍɪᴜᴍ</b>
• /add_premium `<user_id>` `<time>` - Grant premium access to a user for a specified duration (e.g. `1day`, `1month`, `2hours`).
• /remove_premium `<user_id>` - Remove premium access from a user.
• /premium_users - ᴘʀᴇᴍɪᴜᴍ ʟɪꜱᴛ
• /check_premium - ᴄʜᴇᴄᴋ ᴘʀᴇᴍɪᴜᴍ ɪɴꜰᴏ
• /add_redeem - ᴀᴅᴅ ʀᴇᴅᴇᴇᴍ ᴄᴏᴅᴇ
• /allcodes - ꜱᴇᴇ ᴀʟʟ ʀᴇᴅᴇᴇᴍ ᴄᴏᴅᴇꜱ
• /clearcodes - ᴄʟᴇᴀʀ ᴀʟʟ ʀᴇᴅᴇᴇᴍ ᴄᴏᴅᴇꜱ

<b>📊 ꜰɪʟᴇ ʟɪᴍɪᴛ</b>
• /resetlimit - ʀᴇꜱᴇᴛ ᴀʟʟ ᴜꜱᴇʀꜱ ʟɪᴍɪᴛ
• /resetuser - ʀᴇꜱᴇᴛ ꜱᴘᴇᴄɪꜰɪᴄ ᴜꜱᴇʀ ʟɪᴍɪᴛ
• /checklimit - ᴄʜᴇᴄᴋ ᴜꜱᴇʀ ꜰɪʟᴇ ʟɪᴍɪᴛ""" 


    ABOUT_TEXT = """<blockquote><b>🤖 ᴍʏ ɴᴀᴍᴇ: {}
⚙️ ʜᴏsᴛᴇᴅ ᴏɴ : <a href="https://www.heroku.com/">ʜᴇʀᴏᴋᴜ</a>
🍿 ᴅᴀᴛᴀʙᴀsᴇ : <a href="https://www.mongodb.com/">ᴍᴏɴɢᴏ ᴅʙ</a>
🐍 ᴄᴏᴅɪɴɢ ᴍᴜsᴄʟᴇs : <a href="https://www.python.org/">ᴘʏᴛʜᴏɴ 𝟹</a>
📚 ʟɪʙʀᴀʀʏ: <a href="https://github.com/Mayuri-Chan/pyrofork">ᴘʏʀᴏꜰᴏʀᴋ</a>
👨‍💻 ᴏᴡɴᴇʀ : <a href="https://telegram.me/Silicon_Official">ѕιℓι¢ση σƒƒ¢ιαℓ</a>
🔧 ʙᴜɪʟᴅ sᴛᴀᴛᴜs : ᴠ𝟷.𝟶.𝟷
💥 ʀᴇᴘᴏ : <a href="https://github.com/Silicon-Developer/Auto-Filter-Bot">ᴄʟɪᴄᴋ ʜᴇʀᴇ</a>
</b></blockquote>"""

    SUPPORT_GRP_MOVIE_TEXT = '''<b>ʜᴇʏ {}
    
ɪ ꜰᴏᴜɴᴅ {} ʀᴇsᴜʟᴛs 🎁,
ʙᴜᴛ ɪ ᴄᴀɴ'ᴛ sᴇɴᴅ ʜᴇʀᴇ ᴘʟᴇᴀsᴇ ᴊᴏɪɴ ᴏᴜʀ ʀᴇǫᴜᴇsᴛ ɢʀᴏᴜᴘ ᴛᴏ ɢᴇᴛ ✨</b>'''

    STATUS_TXT = """<b><u>🗃ᴜsᴇʀs ᴅᴀᴛᴀʙᴀsᴇ 🗃</u>

» ᴛᴏᴛᴀʟ ᴜsᴇʀs - <code>{}</code>
» ᴛᴏᴛᴀʟ ɢʀᴏᴜᴘs - <code>{}</code>
» ᴘʀᴇᴍɪᴜᴍ ᴜsᴇʀs - <code>{}</code>
» ᴜsᴇᴅ sᴛᴏʀᴀɢᴇ - <code>{} / {}</code>

<u>📤 ꜰɪʟᴇs ᴅᴀᴛᴀʙᴀsᴇ 𝟷 📤</u>

» ᴛᴏᴛᴀʟ ꜰɪʟᴇs - <code>{}</code>
» ᴜsᴇᴅ sᴛᴏʀᴀɢᴇ - <code>{} / {}</code>

<u>📥 ꜰɪʟᴇs ᴅᴀᴛᴀʙᴀsᴇ 𝟸 📥</u>

» ᴛᴏᴛᴀʟ ꜰɪʟᴇs - <code>{}</code>
» ᴜsᴇᴅ sᴛᴏʀᴀɢᴇ - <code>{} / {}</code>

<u>🤖 ʙᴏᴛ ᴅᴇᴛᴀɪʟs 🤖</u>

» ᴜᴘᴛɪᴍᴇ - <code>{}</code>
» ʀᴀᴍ - <code>{}%</code>
» ᴄᴘᴜ - <code>{}%</code></b>"""

    NEW_USER_TXT = """<b>#ɴᴇᴡ_ᴜsᴇʀ {}

≈ ɪᴅ:- <code>{}</code>
≈ ɴᴀᴍᴇ:- {}</b>"""

    GROUP_CMD = """    
<u>ꜱᴇᴛᴛɪɴɢꜱ</u> :
- ꜱᴇᴛᴛɪɴɢꜱ ɪꜱ ᴛʜᴇ ᴍᴏꜱᴛ ɪᴍᴘᴏʀᴛᴀɴᴛ ꜰᴇᴀᴛᴜʀᴇ ᴏꜰ ᴛʜɪꜱ ʙᴏᴛ.
- ʏᴏᴜ ᴄᴀɴ ᴇᴀꜱɪʟʏ ᴄᴜꜱᴛᴏᴍɪᴢᴇ ᴛʜɪꜱ ʙᴏᴛ ꜰᴏʀ ʏᴏᴜʀ ɢʀᴏᴜᴘ.

<u>ᴀᴠᴀɪʟᴀʙʟᴇ ᴄᴏᴍᴍᴀɴᴅꜱ</u> :
• /settings - ᴄʜᴀɴɢᴇ ᴛʜᴇ ɢʀᴏᴜᴘ ꜱᴇᴛᴛɪɴɢꜱ ᴀꜱ ʏᴏᴜʀ ᴡɪꜱʜ.
• /set_shortner - ꜱᴇᴛ ʏᴏᴜʀ 𝟷ꜱᴛ ꜱʜᴏʀᴛɴᴇʀ.
• /set_shortner_2 - ꜱᴇᴛ ʏᴏᴜʀ 𝟸ɴᴅ ꜱʜᴏʀᴛɴᴇʀ.
• /set_shortner_3 - ꜱᴇᴛ ʏᴏᴜʀ 𝟹ʀᴅ ꜱʜᴏʀᴛɴᴇʀ.
• /set_tutorial - ꜱᴇᴛ ʏᴏᴜʀ 𝟷ꜱᴛ ᴛᴜᴛᴏʀɪᴀʟ ᴠɪᴅᴇᴏ .
• /set_tutorial_2 - ꜱᴇᴛ ʏᴏᴜʀ 𝟸ɴᴅ ᴛᴜᴛᴏʀɪᴀʟ ᴠɪᴅᴇᴏ .
• /set_tutorial_3 - ꜱᴇᴛ ʏᴏᴜʀ 𝟹ʀᴅ ᴛᴜᴛᴏʀɪᴀʟ ᴠɪᴅᴇᴏ .
• /set_time - ꜱᴇᴛ 𝟷ꜱᴛ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ ɢᴀᴘ.
• /set_time_2 - ꜱᴇᴛ 𝟸ɴᴅ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ ɢᴀᴘ.
• /set_template - sᴇᴛ ɪᴍᴅʙ ᴛᴇᴍᴘʟᴀᴛᴇ.
• /set_caption - sᴇᴛ ꜰɪʟᴇ ᴄᴀᴘᴛɪᴏɴ.
• /set_log_channel - ꜱᴇᴛ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ ʟᴏɢ ᴄʜᴀɴɴᴇʟ.
• /set_fsub - ꜱᴇᴛ ᴄᴜꜱᴛᴏᴍ ꜰᴏʀᴄᴇ ꜱᴜʙ ᴄʜᴀɴɴᴇʟ.
• /remove_fsub - ʀᴇᴍᴏᴠᴇ ᴄᴜꜱᴛᴏᴍ ꜰᴏʀᴄᴇ ꜱᴜʙ ᴄʜᴀɴɴᴇʟ.
• /details - ᴄʜᴇᴄᴋ ʏᴏᴜʀ ꜱᴇᴛᴛɪɴɢꜱ."""

    NEW_GROUP_TXT = """#ɴᴇᴡ_ɢʀᴏᴜᴘ {}

ɢʀᴏᴜᴘ ɴᴀᴍᴇ - {}
ɪᴅ - <code>{}</code>
ɢʀᴏᴜᴘ ᴜsᴇʀɴᴀᴍᴇ - @{}.
ɢʀᴏᴜᴘ ʟɪɴᴋ - {}
ᴛᴏᴛᴀʟ ᴍᴇᴍʙᴇʀs - <code>{}</code>
ᴜsᴇʀ - {}"""

    REQUEST_TXT = """<b>📜 ᴜꜱᴇʀ - {}
📇 ɪᴅ - <code>{}</code>

🎁 ʀᴇǫᴜᴇꜱᴛ ᴍꜱɢ - <code>{}</code></b>"""  
    
    IMDB_TEMPLATE_TXT = """<b>📻 ᴛɪᴛʟᴇ - <a href={url}>{title}</a>
🎭 ɢᴇɴʀᴇs - {genres}
🎖 ʀᴀᴛɪɴɢ - <a href={url}/ratings>{rating}</a> / 10 (ʙᴀsᴇᴅ ᴏɴ {votes} ᴜsᴇʀ ʀᴀᴛɪɴɢ.)
📆 ʏᴇᴀʀ - {release_date}
❗️ ʟᴀɴɢᴜᴀɢᴇ - {languages}</b>
"""

    FILE_CAPTION = """<b><a href="https://t.me/Navex_Movies">💎💎</a>{file_caption}</b>"""

    MOVIE_UPDATE_NOTIFY_TXT = """<b>{tag} ➤ {filename}</b>

<blockquote>🎭 ɢᴇɴʀᴇs     : <b>{genres}</b>
📺 ᴏᴛᴛ          : <b>{ott}</b>
🎞️ ǫᴜᴀʟɪᴛʏ  : <b>{quality}</b>
📐 ʀᴇsᴏʟᴜᴛɪᴏɴ : <b>{resolution}</b>
🎧 ᴀᴜᴅɪᴏ       : <b>{language}</b>
🔥 ʀᴀᴛɪɴɢ      : <b>{rating} ⭐</b>
{episodes}</blockquote>"""

    MANUAL_UPDATE_NOTIFY_TXT = """<b>{tag} ➤ {filename}</b>

<blockquote>🎭 ɢᴇɴʀᴇs     : <b>{genres}</b>
🎞️ ǫᴜᴀʟɪᴛʏ  : <b>{quality}</b>
📐 ʀᴇsᴏʟᴜᴛɪᴏɴ : <b>{resolution}</b>
🎧 ᴀᴜᴅɪᴏ       : <b>{language}</b>
🔥 ʀᴀᴛɪɴɢ      : <b>{rating} ⭐</b>
{episodes}</blockquote>"""

    RESTART_TXT = """<b>
📅 ᴅᴀᴛᴇ : <code>{}</code>
⏰ ᴛɪᴍᴇ : <code>{}</code>
🌐 ᴛɪᴍᴇᴢᴏɴᴇ : <code>Asia/Kolkata</code></b>"""

    ALRT_TXT = """Search Your Own !"""

    OLD_ALRT_TXT = """ʏᴏᴜ ᴀʀᴇ ᴜsɪɴɢ ᴍʏ ᴏʟᴅ ᴍᴇssᴀɢᴇs..sᴇɴᴅ ᴀ ɴᴇᴡ ʀᴇǫᴜᴇsᴛ.."""

    NO_RESULT_TXT = """<b>ᴛʜɪs ᴍᴇssᴀɢᴇ ɪs ɴᴏᴛ ʀᴇʟᴇᴀsᴇᴅ ᴏʀ ᴀᴅᴅᴇᴅ ɪɴ ᴍʏ ᴅᴀᴛᴀʙᴀsᴇ 🙄</b>"""
    
    I_CUDNT = """<b>🤧  ʜᴇʟʟᴏ{}

ɪ ᴄᴏᴜʟᴅɴ'ᴛ ꜰɪɴᴅ ᴀɴʏ ᴍᴏᴠɪᴇ ᴏʀ sᴇʀɪᴇs ᴡɪᴛʜ ᴛʜᴀᴛ ɴᴀᴍᴇ... 😐 </b>"""

    I_CUD_NT = """<b>😑 ʜᴇʟʟᴏ {}

ɪ ᴄᴏᴜʟᴅɴ'ᴛ ꜰɪɴᴅ ᴀɴʏᴛʜɪɴɢ ʀᴇʟᴀᴛᴇᴅ ᴛᴏ ᴛʜɪs 😞... ᴄʜᴇᴄᴋ ʏᴏᴜʀ sᴘᴇʟʟɪɴɢ.</b>"""
    
    CUDNT_FND = """<b>🤧 ʜᴇʟʟᴏ {}

ɪ ᴄᴏᴜʟᴅɴ'ᴛ ꜰɪɴᴅ ᴀɴʏᴛʜɪɴɢ ʀᴇʟᴀᴛᴇᴅ ᴛᴏ ᴛʜɪs ᴅɪᴅ ʏᴏᴜ ᴍᴇᴀɴ ᴀɴʏ ᴏꜰ ᴛʜᴇsᴇ?? 👇</b>"""
    
    FONT_TXT= """<b>ʏᴏᴜ ᴄᴀɴ ᴜsᴇ ᴛʜɪs ᴍᴏᴅᴇ ᴛᴏ ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ꜰᴏɴᴛs sᴛʏʟᴇ, ᴊᴜsᴛ sᴇɴᴅ ᴍᴇ ʟɪᴋᴇ ᴛʜɪs ꜰᴏʀᴍᴀᴛ

<code>/font hi how are you</code></b>"""

    
    PREMIUM_TEXT = """<b><i><blockquote>ᴀᴠᴀɪʟᴀʙʟᴇ ᴘʟᴀɴs  ♻️</blockquote>

• 𝟷 ᴡᴇᴇᴋ  -  ₹15
• 𝟷 ᴍᴏɴᴛʜ  -  ₹50
• 2 ᴍᴏɴᴛʜs  -  ₹80
• 3 ᴍᴏɴᴛʜs  -  ₹100

•─────•─────────•─────•
<blockquote>ᴘʀᴇᴍɪᴜᴍ ꜰᴇᴀᴛᴜʀᴇs  🎁</blockquote>

○ ɴᴏ ɴᴇᴇᴅ ᴛᴏ ᴠᴇʀɪꜰʏ
○ ᴅɪʀᴇᴄᴛ ꜰɪʟᴇs   
○ ᴀᴅ-ꜰʀᴇᴇ ᴇxᴘᴇʀɪᴇɴᴄᴇ 
○ ʜɪɢʜ-sᴘᴇᴇᴅ ᴅᴏᴡɴʟᴏᴀᴅ ʟɪɴᴋ                         
○ ᴍᴜʟᴛɪ-ᴘʟᴀʏᴇʀ sᴛʀᴇᴀᴍɪɴɢ ʟɪɴᴋs                           
○ ᴜɴʟɪᴍɪᴛᴇᴅ ᴍᴏᴠɪᴇꜱ, ꜱᴇʀɪᴇꜱ & ᴀɴɪᴍᴇ                                                                         
○ ꜰᴜʟʟ ᴀᴅᴍɪɴ sᴜᴘᴘᴏʀᴛ                              
○ ʀᴇǫᴜᴇsᴛ ᴡɪʟʟ ʙᴇ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ɪɴ 𝟷ʜ
•─────•─────────•─────•


✨ ᴜᴘɪ ɪᴅ - <code>navex69@axl</code>

ᴄʜᴇᴄᴋ ʏᴏᴜʀ ᴀᴄᴛɪᴠᴇ ᴘʟᴀɴ  /myplan

💢 ᴍᴜsᴛ sᴇɴᴅ sᴄʀᴇᴇɴsʜᴏᴛ ᴀꜰᴛᴇʀ ᴘᴀʏᴍᴇɴᴛ

‼️ ᴀꜰᴛᴇʀ sᴇɴᴅɪɴɢ ᴀ sᴄʀᴇᴇɴsʜᴏᴛ ᴘʟᴇᴀsᴇ ɢɪᴠᴇ ᴍᴇ sᴏᴍᴇ ᴛɪᴍᴇ ᴛᴏ ᴀᴅᴅ ʏᴏᴜ ɪɴ ᴛʜᴇ ᴘʀᴇᴍɪᴜᴍ ᴠᴇʀsɪᴏɴ.</i></b>"""

    BUY_PLAN = """<b>○ <u>ꜰɪʀsᴛ sᴛᴇᴘ</u> : ᴘᴀʏ ᴛʜᴇ ᴀᴍᴏᴜɴᴛ ᴀᴄᴄᴏʀᴅɪɴɢ ᴛᴏ ʏᴏᴜʀ ꜰᴀᴠᴏʀɪᴛᴇ ᴘʟᴀɴ ᴛᴏ ᴛʜɪs <code>navex69@axl</code> ᴜᴘɪ ɪᴅ.
    
○ <u>secoɴᴅ sᴛᴇᴘ</u> : ᴛᴀᴋᴇ ᴀ sᴄʀᴇᴇɴsʜᴏᴛ ᴏꜰ ʏᴏᴜʀ ᴘᴀʏᴍᴇɴᴛ ᴀɴᴅ sʜᴀʀᴇ ɪᴛ ᴅɪʀᴇᴄᴛʟʏ ʜᴇʀᴇ: @Navex_69 

ʏᴏᴜʀ <u>ᴘʀᴇᴍɪᴜᴍ ᴘʟᴀɴ</u> ᴡɪʟʟ ʙᴇ ᴀᴄᴛɪᴠᴀᴛᴇᴅ ᴀꜰᴛᴇʀ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ</b>"""

    PLAN_TEXT = """<b>ᴡᴇ ᴀʀᴇ ᴘʀᴏᴠɪᴅɪɴɢ ᴘʀᴇᴍɪᴜᴍ ᴀᴛ ᴛʜᴇ ʟᴏᴡᴇsᴛ ᴘʀɪᴄᴇs:
10 ʀᴜᴘᴇᴇ ᴘᴇʀ ᴅᴀʏ 
50 ʀᴜᴘᴇᴇs ꜰᴏʀ ᴏɴᴇ ᴍᴏɴᴛʜ 
100 ʀᴜᴘᴇᴇs ꜰᴏʀ ᴛᴡᴏ ᴍᴏɴᴛʜs 

ᴄʟɪᴄᴋ ᴛʜᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ᴄᴏɴᴛɪɴᴜᴇ ʙᴜʏɪɴɢ ↡↡↡
</b>"""

    EARN_TEXT = """<b><i>🤑 ʜᴏᴡ ᴛᴏ ᴇᴀʀɴ ᴍᴏɴᴇʏ ʙʏ ᴛʜɪs ʙᴏᴛ -

1:- ʏᴏᴜ ʜᴀᴠᴇ ᴀᴛʟᴇᴀsᴛ ᴏɴᴇ ᴍᴏᴠɪᴇ ɢʀᴏᴜᴘ.

2:- ᴍᴀᴋᴇ ᴛʜɪs <a href=https://t.me/{}</a> ᴀᴅᴍɪɴ ɪɴ ʏᴏᴜʀ ɢʀᴏᴜᴘ.

3:- ᴄʀᴇᴀᴛᴇ ᴀᴄᴄᴏᴜɴᴛ ᴏɴ ᴀɴʏ sʜᴏʀᴛɴᴇʀ ʟɪᴋᴇ ʏᴏᴜ ᴄᴀɴ ᴀʟsᴏ ᴜsᴇ ᴛʜɪs ʙᴇsᴛ sʜᴏʀᴛɴᴇʀ <a href=https://ez4short.com/ref/movieurl>ez4short</a>.

4:- ᴛʜᴇɴ sᴇᴛ ʏᴏᴜʀ sʜᴏʀᴛɴᴇʀ ᴅᴇᴛᴀɪʟs ʙʏ ᴛʜɪs ꜰᴏʀᴍᴀᴛ 👇

<code>/set_shortner ez4short.com 837b7a64653d1b435f5e20a237840f1251d0c1ce</code>

<code>/set_shortner_2 omnifly.in.net f287e7e9b1a23c34f542f77787d39607cae36a4d</code>

<code>/set_shortner_3 shortslink.in 06b24eb6bbb025713cd522fb3f696b6d5de11354</code>

<code>/set_tutorial https://t.me/bisal_files</code>

5:- ᴀᴅᴅ ʟᴏɢ ᴄʜᴀɴɴᴇʟ ʙʏ ᴛʜɪs ꜰᴏʀᴍᴀᴛ & ᴍᴀᴋᴇ sᴜʀᴇ ʙᴏᴛ ɪs ᴀᴅᴍɪɴ ɪɴ ʏᴏᴜʀ ʟᴏɢ ᴄʜᴀɴɴᴇʟ 👇

<code>/set_log_channel -100*******</code>

ʏᴏᴜ ᴄᴀɴ ᴄʜᴇᴄᴋ ʏᴏᴜʀ ᴀʟʟ ᴅᴇᴛᴀɪʟs ʙʏ /details ᴄᴏᴍᴍᴀɴᴅ

💯 ɴᴏᴛᴇ - <i>ᴛʜɪs ʙᴏᴛ ɪs ꜰʀᴇᴇ ᴛᴏ ᴀʟʟ, ʏᴏᴜ ᴄᴀɴ ᴜsᴇ ᴛʜɪs ʙᴏᴛ ɪɴ ʏᴏᴜʀ ɢʀᴏᴜᴘs ᴀɴᴅ ᴇᴀʀɴ ᴜɴʟɪᴍɪᴛᴇᴅ ᴍᴏɴᴇʏ.</i></b>"""

    VERIFICATION_TEXT = """<b>👋 ʜᴇʏ {} {},

📌 <u>ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴠᴇʀɪꜰɪᴇᴅ ᴛᴏᴅᴀʏ, ᴘʟᴇᴀꜱᴇ ᴄʟɪᴄᴋ ᴏɴ ᴠᴇʀɪꜰʏ & ɢᴇᴛ ᴜɴʟɪᴍɪᴛᴇᴅ ᴀᴄᴄᴇꜱꜱ ꜰᴏʀ ᴛɪʟʟ ɴᴇxᴛ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ</u>

#ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ:- 1/3 ✓

ɪꜰ ʏᴏᴜ ᴡᴀɴᴛ ᴅɪʀᴇᴄᴛ ꜰɪʟᴇꜱ ᴡɪᴛʜᴏᴜᴛ ᴀɴʏ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴꜱ ᴛʜᴇɴ ʙᴜʏ ʙᴏᴛ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ.</b>"""

    VERIFY_COMPLETE_TEXT = """<b>👋 ʜᴇʏ {},

ʏᴏᴜ ʜᴀᴠᴇ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ᴛʜᴇ 1st ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ ✓

ɴᴏᴡ ʏᴏᴜ ʜᴀᴠᴇ ᴜɴʟɪᴍɪᴛᴇᴅ ᴀᴄᴄᴇss ꜰᴏʀ ɴᴇxᴛ <code>{}</code></b>"""

    SECOND_VERIFICATION_TEXT = """<b>👋 ʜᴇʏ {} {},

📌 <u>ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴠᴇʀɪꜰɪᴇᴅ, ᴛᴀᴘ ᴏɴ ᴛʜᴇ ᴠᴇʀɪꜰʏ ʟɪɴᴋ ᴀɴᴅ ɢᴇᴛ ᴜɴʟɪᴍɪᴛᴇᴅ ᴀᴄᴄᴇss ꜰᴏʀ ᴛɪʟʟ ɴᴇxᴛ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ</u>

#ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ:- 2/3

ɪꜰ ʏᴏᴜ ᴡᴀɴᴛ ᴅɪʀᴇᴄᴛ ꜰɪʟᴇꜱ ᴡɪᴛʜᴏᴜᴛ ᴀɴʏ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴꜱ ᴛʜᴇɴ ʙᴜʏ ʙᴏᴛ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ.</b>"""

    SECOND_VERIFY_COMPLETE_TEXT = """<b>👋 ʜᴇʏ {},

ʏᴏᴜ ʜᴀᴠᴇ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ᴛʜᴇ 2nd ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ ✓

ɴᴏᴡ ʏᴏᴜ ʜᴀᴠᴇ ᴜɴʟɪᴍɪᴛᴇᴅ ᴀᴄᴄᴇss ꜰᴏʀ ɴᴇxᴛ <code>{}</code></b>"""

    THIRDT_VERIFICATION_TEXT = """<b>👋 ʜᴇʏ {},
    
📌 <u>ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴠᴇʀɪꜰɪᴇᴅ ᴛᴏᴅᴀʏ, ᴛᴀᴘ ᴏɴ ᴛʜᴇ ᴠᴇʀɪꜰʏ ʟɪɴᴋ & ɢᴇᴛ ᴜɴʟɪᴍɪᴛᴇᴅ ᴀᴄᴄᴇss ꜰᴏʀ ɴᴇxᴛ ꜰᴜʟʟ ᴅᴀʏ.</u>

#ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ:- 3/3

ɪꜰ ʏᴏᴜ ᴡᴀɴᴛ ᴅɪʀᴇᴄᴛ ꜰɪʟᴇs ᴛʜᴇɴ ʏᴏᴜ ᴄᴀɴ ᴛᴀᴋᴇ ᴘʀᴇᴍɪᴜᴍ sᴇʀᴠɪᴄᴇ (ɴᴏ ɴᴇᴇᴅ ᴛᴏ ᴠᴇʀɪꜰʏ)</b>"""

    THIRDT_VERIFY_COMPLETE_TEXT= """<b>👋 ʜᴇʏ {},
    
ʏᴏᴜ ʜᴀᴠᴇ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ᴛʜᴇ 3rd ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ ✓

ɴᴏᴡ ʏᴏᴜ ʜᴀᴠᴇ ᴜɴʟɪᴍɪᴛᴇᴅ ᴀᴄᴄᴇss ꜰᴏʀ ɴᴇxᴛ ꜰᴜʟʟ ᴅᴀʏ </b>"""

    VERIFIED_LOG_TEXT = """<b><u>☄ ᴜsᴇʀ ᴠᴇʀɪꜰɪᴇᴅ sᴜᴄᴄᴇssꜰᴜʟʟʏ ☄</u>

⚡️ ɴᴀᴍᴇ:- {} [ <code>{}</code> ] 
📆 ᴅᴀᴛᴇ:- <code>{} </code></b>

#verified_{}_completed"""

    DISCLAIMER_TXT = """
<b>ᴛʜɪꜱ ɪꜱ ᴀɴ ᴏᴘᴇɴ ꜱᴏᴜʀᴄᴇ ᴘʀᴏᴊᴇᴄᴛ.

ᴀʟʟ ᴛʜᴇ ꜰɪʟᴇꜱ ɪɴ ᴛʜɪꜱ ʙᴏᴛ ᴀʀᴇ ꜰʀᴇᴇʟʏ ᴀᴠᴀɪʟᴀʙʟᴇ ᴏɴ ᴛʜᴇ ɪɴᴛᴇʀɴᴇᴛ ᴏʀ ᴘᴏꜱᴛᴇᴅ ʙʏ ꜱᴏᴍᴇʙᴏᴅʏ ᴇʟꜱᴇ. ᴊᴜꜱᴛ ꜰᴏʀ ᴇᴀꜱʏ ꜱᴇᴀʀᴄʜɪɴɢ ᴛʜɪꜱ ʙᴏᴛ ɪꜱ ɪɴᴅᴇxɪɴɢ ꜰɪʟᴇꜱ ᴡʜɪᴄʜ ᴀʀᴇ ᴀʟʀᴇᴀᴅʏ ᴜᴘʟᴏᴀᴅᴇᴅ ᴏɴ ᴛᴇʟᴇɢʀᴀᴍ. ᴡᴇ ʀᴇꜱᴘᴇᴄᴛ ᴀʟʟ ᴛʜᴇ ᴄᴏᴘʏʀɪɢʜᴛ ʟᴀᴡꜱ ᴀɴᴅ ᴡᴏʀᴋꜱ ɪɴ ᴄᴏᴍᴘʟɪᴀɴᴄᴇ ᴡɪᴛʜ ᴅᴍᴄᴀ ᴀɴᴅ ᴇᴜᴄᴅ. ɪꜰ ᴀɴʏᴛʜɪɴɢ ɪꜱ ᴀɢᴀɪɴꜱᴛ ʟᴀᴡ ᴘʟᴇᴀꜱᴇ ᴄᴏɴᴛᴀᴄᴛ ᴍᴇ ꜱᴏ ᴛʜᴀᴛ ɪᴛ ᴄᴀɴ ʙᴇ ʀᴇᴍᴏᴠᴇᴅ ᴀꜱᴀᴘ. ɪᴛ ɪꜱ ꜰᴏʀʙɪʙʙᴇɴ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ, ꜱᴛʀᴇᴀᴍ, ʀᴇᴘʀᴏᴅᴜᴄᴇ, ꜱʜᴀʀᴇ ᴏʀ ᴄᴏɴꜱᴜᴍᴇ ᴄᴏɴᴛᴇɴᴛ ᴡɪᴛʜᴏᴜᴛ ᴇxᴘʟɪᴄɪᴛ ᴘᴇʀᴍɪꜱꜱɪᴏɴ ꜰʀᴏᴍ ᴛʜᴇ ᴄᴏɴᴛᴇɴᴛ ᴄʀᴇᴀᴛᴏʀ ᴏʀ ʟᴇɢᴀʟ ᴄᴏᴘʏʀɪɢʜᴛ ʜᴏʟᴅᴇʀ. ɪꜰ ʏᴏᴜ ʙᴇʟɪᴇᴠᴇ ᴛʜɪꜱ ʙᴏᴛ ɪꜱ ᴠɪᴏʟᴀᴛɪɴɢ ʏᴏᴜʀ ɪɴᴛᴇʟʟᴇᴄᴛᴜᴀʟ ᴘʀᴏᴘᴇʀᴛʏ, ᴄᴏɴᴛᴀᴄᴛ ᴛʜᴇ ʀᴇꜱᴘᴇᴄᴛɪᴠᴇ ᴄʜᴀɴɴᴇʟꜱ ꜰᴏʀ ʀᴇᴍᴏᴠᴀʟ. ᴛʜᴇ ʙᴏᴛ ᴅᴏᴇꜱ ɴᴏᴛ ᴏᴡɴ ᴀɴʏ ᴏꜰ ᴛʜᴇꜱᴇ ᴄᴏɴᴛᴇɴᴛꜱ, ɪᴛ ᴏɴʟʏ ɪɴᴅᴇx ᴛʜᴇ ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴛᴇʟᴇɢʀᴀᴍ. 
</b>"""
