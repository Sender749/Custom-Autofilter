import os
import requests
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import get_file_id


def upload_image_requests(image_path):
    """
    Upload ANY file to envs.sh (no size limit from our side)
    """
    upload_url = "https://envs.sh"

    try:
        with open(image_path, 'rb') as file:
            files = {'file': file}
            response = requests.post(upload_url, files=files, timeout=300)

            if response.status_code == 200:
                return response.text.strip()

            raise Exception(f"Upload failed: {response.status_code} | {response.text}")

    except Exception as e:
        print(f"[UPLOAD ERROR]: {e}")
        return None


@Client.on_message(filters.command("upload") & filters.private)
async def upload_command(client, message):

    replied = message.reply_to_message
    if not replied:
        return await message.reply_text(
            "⚠️ Reply to any:\n• Photo\n• Video\n• Document\n• Sticker\n• Animation"
        )

    file_info = get_file_id(replied)
    if not file_info:
        return await message.reply_text("❌ Unsupported media type")

    # ❌ REMOVED 5MB LIMIT COMPLETELY

    # Download file
    try:
        silicon_path = await replied.download()
    except Exception as e:
        return await message.reply_text(f"Download error: {e}")

    uploading_message = await message.reply_text(
        "<code>Uploading to envs.sh ...</code>",
        disable_web_page_preview=True
    )

    try:
        silicon_url = upload_image_requests(silicon_path)

        if not silicon_url:
            raise Exception("Upload failed from server")

        await uploading_message.edit_text("<code>Done :)</code>")

    except Exception as error:
        await uploading_message.edit_text(f"Error :- {error}")
        await asyncio.sleep(3)
        return await uploading_message.delete()

    # Remove local file
    try:
        os.remove(silicon_path)
    except Exception as error:
        print(f"Remove error: {error}")

    await uploading_message.delete()

    # Final message
    await message.reply_text(
        text=(
            f"<b>Your Upload Link Ready 👇</b>\n\n"
            f"<code>{silicon_url}</code>\n\n"
            f"<b>Powered By - @Silicon_Bot_Update</b>"
        ),
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✓ Open Link ✓", url=silicon_url),
                InlineKeyboardButton(
                    "📱 Share Link",
                    url=f"https://telegram.me/share/url?url={silicon_url}"
                )
            ],
            [
                InlineKeyboardButton("❌ Close ❌", callback_data="close_data")
            ]
        ])
    )
