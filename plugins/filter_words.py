from pyrogram import filters, Client
from info import ADMINS
from database.ia_filterdb import get_filter_words, set_filter_words
from Jisshu.bot import JisshuBot
import logging

logger = logging.getLogger(__name__)

@Client.on_message(filters.command("filterword") & filters.user(ADMINS))
async def show_filter_words(client, message):
    try:
        words = await get_filter_words()
        formatted_words = ", ".join(sorted(words)) 
        count = len(words)
        await message.reply_text(
            f"📝 Current filter words ({count}):\n\n"
            f"{formatted_words}\n\n"
            "To add more words : /set_filterword send, movie, series ha kya"
        )
    except Exception as e:
        logger.error(f"Error in show_filter_words: {e}")
        await message.reply_text("❌ Error showing filter words")

@Client.on_message(filters.command("set_filterword") & filters.user(ADMINS))
async def update_filter_words(client, message):
    try:
        if len(message.command) < 2:
            current_words = await get_filter_words()
            example = "/set_filterword send, kr do, movie, series hai bhai, pls"
            await message.reply_text(
                "❌ Please provide words/phrases separated by commas.\n"
                f"Example: {example}\n\n"
                f"Current count: {len(current_words)} words"
            )
            return
        
        # Split by commas and clean the words
        words = [word.strip().lower() 
                for word in message.text.split(maxsplit=1)[1].split(",") 
                if word.strip()]
        
        if not words:
            await message.reply_text("❌ No valid words provided!")
            return
        
        # Merge with existing words
        existing = await get_filter_words()
        updated = existing.union(words)
        await set_filter_words(updated)
        
        # Show results
        added = len(updated) - len(existing)
        await message.reply_text(
            f"✅ Updated filter words:\n"
            f"- Total words: {len(updated)}\n"
            f"- Newly added: {added}\n"
            f"- Use /filterword to view all"
        )
    except Exception as e:
        logger.error(f"Error in update_filter_words: {e}")
        await message.reply_text("❌ Error updating filter words")
