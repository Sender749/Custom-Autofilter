import os
import asyncio
import subprocess
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import get_file_id


async def upload_envs(file_path: str):
    """
    PURE CURL STYLE UPLOAD - MOST STABLE FOR envs.sh
    """
    try:
        # Ensure filename has extension
        if "." not in file_path:
            new_path = file_path + ".bin"
            os.rename(file_path, new_path)
            file_path = new_path

        cmd = [
            "curl",
            "-#",
            "-F", f"file=@{file_path}",
            "https://envs.sh"
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        out, err = process.communicate()

        if process.returncode != 0:
            raise Exception(err.decode())

        link = out.decode().strip()

        if not link.startswith("http"):
            raise Exception("Server rejected file")

        return link

    except Exception as e:
        print(f"[UPLOAD ERROR] {e}")
        return None



@Client.on_message(filters.command("upload") & filters.private)
async def upload_command(client, message):

    replied = message.reply_to_message
    if not replied:
        return await message.reply_text(
            "⚠️ Reply to any media:\n• Photo\n• Video\n• Document\n• Sticker\n• Animation"
        )

    file_info = get_file_id(replied)
    if not file_info:
        return await message.reply_text("❌ Unsupported type")

    # DOWNLOAD
    try:
        path = await replied.download()
    except Exception as e:
        return await message.reply_text(f"Download failed: {e}")

    status = await message.reply_text("<code>Uploading...</code>")

    # UPLOAD
    link = await upload_envs(path)

    # REMOVE FILE
    try:
        os.remove(path)
    except:
        pass

    if not link:
        return await status.edit("❌ Upload error from server")

    await status.delete()

    await message.reply_text(
        text=(
            f"<b>Upload Successful 👇</b>\n\n"
            f"<code>{link}</code>\n\n"
            f"<b>Powered By - @Silicon_Bot_Update</b>"
        ),
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✓ Open Link ✓", url=link),
                InlineKeyboardButton(
                    "📱 Share Link",
                    url=f"https://telegram.me/share/url?url={link}"
                )
            ],
            [
                InlineKeyboardButton("❌ Close ❌", callback_data="close_data")
            ]
        ])
    )
