import asyncio
import logging
from typing import Optional, Tuple
import os 
import ollama
from io import BytesIO
from telegram import (
    Chat, 
    ChatMember, 
    ChatMemberUpdated, 
    Update,
    InlineKeyboardMarkup, 
    constants,
 )

from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackContext,
    CallbackQueryHandler,
)
from utils import message_text, keyboard_layout
from bot_conv import bot_greeting, lang_config, start_page, continue_text
from datetime import datetime

# Enable logging

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SUPPORTED_LANGUAGES = ['en', 'ru', 'fr']
GITHUB_REPO = "https://github.com/RusaUB/BriefifyBot"
ADMIN_ID = os.environ.get("TELEGRAM_ADMIN_ID")

api_key = os.environ["MISTRAL_API_KEY"]
model = "mistral-tiny"

MAX_USAGE = 1

async def handle_error(update, context, error_message):
    """
    Handles errors by sending a generic error message and logging the error.
    """
    await update.message.reply_text("Something went wrong. Please try again later.")
    logger.error(error_message)

def extract_status_change(chat_member_update: ChatMemberUpdated) -> Optional[Tuple[bool, bool]]:
    """Takes a ChatMemberUpdated instance and extracts whether the 'old_chat_member' was a member
    of the chat and whether the 'new_chat_member' is a member of the chat. Returns None, if
    the status didn't change.
    """
    status_change = chat_member_update.difference().get("status")
    old_is_member, new_is_member = chat_member_update.difference().get("is_member", (None, None))

    if status_change is None:
        return None

    old_status, new_status = status_change
    was_member = old_status in [
        ChatMember.MEMBER,
        ChatMember.OWNER,
        ChatMember.ADMINISTRATOR,
    ] or (old_status == ChatMember.RESTRICTED and old_is_member is True)
    is_member = new_status in [
        ChatMember.MEMBER,
        ChatMember.OWNER,
        ChatMember.ADMINISTRATOR,
    ] or (new_status == ChatMember.RESTRICTED and new_is_member is True)

    return was_member, is_member


async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tracks the chats the bot is in."""
    result = extract_status_change(update.my_chat_member)
    if result is None:
        return
    was_member, is_member = result

    # Let's check who is responsible for the change
    cause_name = update.effective_user.full_name

    # Handle chat types differently:
    chat = update.effective_chat
    if chat.type == Chat.PRIVATE:
        if not was_member and is_member:
            # This may not be really needed in practice because most clients will automatically
            # send a /start command after the user unblocks the bot, and start_private_chat()
            # will add the user to "user_ids".
            # We're including this here for the sake of the example.
            logger.info("%s unblocked the bot", cause_name)
            context.bot_data.setdefault("user_ids", set()).add(chat.id)
        elif was_member and not is_member:
            logger.info("%s blocked the bot", cause_name)
            context.bot_data.setdefault("user_ids", set()).discard(chat.id)
    elif chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        if not was_member and is_member:
            logger.info("%s added the bot to the group %s", cause_name, chat.title)
            context.bot_data.setdefault("group_ids", set()).add(chat.id)
        elif was_member and not is_member:
            logger.info("%s removed the bot from the group %s", cause_name, chat.title)
            context.bot_data.setdefault("group_ids", set()).discard(chat.id)
    elif not was_member and is_member:
        logger.info("%s added the bot to the channel %s", cause_name, chat.title)
        context.bot_data.setdefault("channel_ids", set()).add(chat.id)
    elif was_member and not is_member:
        logger.info("%s removed the bot from the channel %s", cause_name, chat.title)
        context.bot_data.setdefault("channel_ids", set()).discard(chat.id)


async def show_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows which chats the bot is in"""
    user_ids = ", ".join(str(uid) for uid in context.bot_data.setdefault("user_ids", set()))
    group_ids = ", ".join(str(gid) for gid in context.bot_data.setdefault("group_ids", set()))
    channel_ids = ", ".join(str(cid) for cid in context.bot_data.setdefault("channel_ids", set()))
    text = (
        f"@{context.bot.username} is currently in a conversation with the user IDs {user_ids}."
        f" Moreover it is a member of the groups with IDs {group_ids} "
        f"and administrator in the channels with IDs {channel_ids}."
    )
    await update.effective_message.reply_text(text)


async def greet_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greets new users in chats and announces when someone leaves"""
    result = extract_status_change(update.chat_member)
    if result is None:
        return

    was_member, is_member = result
    cause_name = update.chat_member.from_user.mention_html()
    member_name = update.chat_member.new_chat_member.user.mention_html()

    if not was_member and is_member:
        await update.effective_chat.send_message(
            f"{member_name} was added by {cause_name}. Welcome!",
            parse_mode=ParseMode.HTML,
        )
    elif was_member and not is_member:
        await update.effective_chat.send_message(
            f"{member_name} is no longer with us. Thanks a lot, {cause_name} ...",
            parse_mode=ParseMode.HTML,
        )

async def start_private_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Start command handler. Handles the /start command.
    """
    user = update.effective_user
    chat = update.effective_chat
    language_code = user.language_code if user.language_code in SUPPORTED_LANGUAGES else "en"
    
    if chat.type != Chat.PRIVATE or chat.id in context.bot_data.get("user_ids", set()):
        await update.message.reply_text(
            message_text(
                language_code=language_code,
                message=start_page,
                context={"user": user, "github_repo": GITHUB_REPO}
            ),
            parse_mode=constants.ParseMode.MARKDOWN
        )
        return
    
    logger.info("%s started a private chat with the bot", user.full_name)
    
    current_datetime = datetime.now()
    start_date = current_datetime.strftime('%Y-%m-%d')
    start_time = current_datetime.strftime('%H:%M')
    
    context.bot_data.setdefault("user_ids", {}).setdefault(chat.id, {}).update({
        "start_date": start_date,
        "start_time": start_time
    })
    
    keyboard = keyboard_layout(language_code, SUPPORTED_LANGUAGES, lang_config, continue_text)
    
    await update.message.reply_text(
        message_text(language_code, bot_greeting, context={"user": user}),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_language_selection(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    lang_choice = query.data
    context.user_data["language"] = lang_choice
    await query.message.edit_text(message_text(language_code=lang_choice,message=start_page, context={"user":update.effective_user.full_name, "github_repo": GITHUB_REPO}), parse_mode=constants.ParseMode.MARKDOWN)

async def handle_message(update: Update, context: CallbackContext) -> None:
    try:
        from mistralai.async_client import MistralAsyncClient
        from mistralai.models.chat_completion import ChatMessage

        client = MistralAsyncClient(api_key=api_key)

        # Initialize the text with an empty string
        edited_text = ""
        # Send the initial text
        text = await update.message.reply_text("🤖💬...")
        messages = [ChatMessage(role="user", content=update.message.text)]
        async_response = client.chat_stream(model=model, messages=messages)
        # Iterate over the parts received from ollama
        async for chunk in async_response:
            content = chunk.choices[0].delta.content  # Remove leading/trailing whitespace
            if content:  # Check if content is not empty
                edited_text += content
                if len(edited_text) % 50 == 0:
                    await text.edit_text(edited_text)
        if edited_text:  # If there's any remaining text
            await text.edit_text(edited_text)
    except Exception as e:
        await update.message.reply_text("Something went wrog try again later")
        logger.error(f"Error handling message: {e}")

async def handle_message_wrapper(update: Update, context: CallbackContext) -> None:
    asyncio.create_task(handle_message(update, context))  # Вызовите вашу функцию handle_message асинхронно

async def handle_photo_messages(update: Update, context: CallbackContext) -> None:
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = BytesIO(await photo_file.download_as_bytearray())
        message = {
            'role': 'user',
            'content': 'Describe this image:',
            'images': [photo_bytes]
        }
        # Send the initial text
        text = await update.message.reply_text("...")
        edited_text = ""
        # Send photo message to ollama for processing
        for part in ollama.chat(model="llava", messages=[message], stream=True):
            edited_text += part['message']['content']
            # Edit the text with the combined content
            if len(edited_text) % 10 == 0:
                await text.edit_text(edited_text)
        if edited_text:  # If there's any remaining text
            await text.edit_text(edited_text)
    except Exception as e:
        await handle_error(update, context, f"Error handling feedback: {e}")


async def get_number_of_users(update: Update, context: CallbackContext):
    try:
        if context.bot_data["user_ids"]:
            await update.message.reply_text(str(context.bot_data["user_ids"]))
    except Exception as e:
        await update.message.reply_text(str(e))

def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TOKEN).build()


    application.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(CommandHandler("show_chats", show_chats))

    application.add_handler(ChatMemberHandler(greet_chat_members, ChatMemberHandler.CHAT_MEMBER))

    application.add_handler(CommandHandler("start", start_private_chat))
    application.add_handler(CallbackQueryHandler(handle_language_selection))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message_wrapper))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))


    application.add_handler(CommandHandler(command="admin",filters=filters.User(int(ADMIN_ID)), callback=get_number_of_users))

    # Run the bot until the user presses Ctrl-C
    # We pass 'allowed_updates' handle *all* updates including `chat_member` updates
    # To reset this, simply pass `allowed_updates=[]`
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()