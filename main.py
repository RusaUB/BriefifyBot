import asyncio
import logging
from typing import Optional, Tuple
import os 
from time import time
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
    ApplicationHandlerStop
)
from utils import message_text, keyboard_layout
from bot_conv import *
from datetime import datetime
import json

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Load environment variables
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
SUPPORTED_LANGUAGES = ['en', 'ru', 'fr']
GITHUB_REPO = "https://github.com/RusaUB/BriefifyBot"
ADMIN_ID = os.environ.get("TELEGRAM_ADMIN_ID")

# Initialize Mistral client and model
api_key = os.environ["MISTRAL_API_KEY"]
model = "mistral-tiny"

# Restriction settings
MAX_USAGE = 30 # 30 messages
RATE_INTERVAL = 60 # 60 minutes

async def handle_error(update, context, error_message, reply = True):
    """
    Handles errors by sending a generic error message and logging the error.
    """
    if reply:
        await update.message.reply_text("Something went wrong. Please try again later.")
    logger.error(error_message)

def extract_status_change(chat_member_update: ChatMemberUpdated) -> Optional[Tuple[bool, bool]]:
    """
    Takes a ChatMemberUpdated instance and extracts whether the 'old_chat_member' was a member
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
    
    # Extract status change from update
    result = extract_status_change(update.my_chat_member)
    
    # Return if no status change
    if result is None:
        return
    
    # Get previous and current membership status
    was_member, is_member = result

    # Get the name of the user responsible for the change
    cause_name = update.effective_user.full_name

    # Handle chat types differently
    chat = update.effective_chat
    if chat.type == Chat.PRIVATE:
        if not was_member and is_member:
            # Log when user unblocks the bot
            logger.info("%s unblocked the bot", cause_name)
            context.bot_data.setdefault("user_ids", set()).add(chat.id)
        elif was_member and not is_member:
            # Log when user blocks the bot
            logger.info("%s blocked the bot", cause_name)
            context.bot_data.setdefault("user_ids", set()).discard(chat.id)
    elif chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        if not was_member and is_member:
            # Log when bot is added to group
            logger.info("%s added the bot to the group %s", cause_name, chat.title)
            context.bot_data.setdefault("group_ids", set()).add(chat.id)
        elif was_member and not is_member:
            # Log when bot is removed from group
            logger.info("%s removed the bot from the group %s", cause_name, chat.title)
            context.bot_data.setdefault("group_ids", set()).discard(chat.id)
    elif not was_member and is_member:
        # Log when bot is added to channel
        logger.info("%s added the bot to the channel %s", cause_name, chat.title)
        context.bot_data.setdefault("channel_ids", set()).add(chat.id)
    elif was_member and not is_member:
        # Log when bot is removed from channel
        logger.info("%s removed the bot from the channel %s", cause_name, chat.title)
        context.bot_data.setdefault("channel_ids", set()).discard(chat.id)


async def show_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
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
    except Exception as e:
        await handle_error(update, context, f"Error showing chats: {e}")


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
    Extracts user, chat, and language info from the update.
    Checks if it's a private chat and user's status.
    Records chat start time, generates language keyboard, sends greeting.
    """
    try:
        # Extract user, chat, and language info from the update
        user = update.effective_user
        chat = update.effective_chat
        language_code = user.language_code if user.language_code in SUPPORTED_LANGUAGES else "en"

        if context.user_data.get("language"):
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
        # Record chat start time
        current_datetime = datetime.now()
        start_date = current_datetime.strftime('%Y-%m-%d')
        start_time = current_datetime.strftime('%H:%M')
        # Record chat start time in context bot data
        context.bot_data.setdefault("user_ids", {}).setdefault(chat.id, {}).update({
            "start_date": start_date,
            "start_time": start_time
        })
        
        keyboard = keyboard_layout(language_code, SUPPORTED_LANGUAGES, lang_config, continue_text)
        
        await update.message.reply_text(
            message_text(language_code, bot_greeting, context={"user": user}),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        await handle_error(update, context, f"Error starting private chat: {e}")

async def handle_language_selection(update: Update, context: CallbackContext) -> None:
    """
    Handles the user's language selection from the language selection keyboard.
    """
    try:
        # Extract the callback query and acknowledge it
        query = update.callback_query
        await query.answer()
        
        # Extract the selected language choice from the callback data
        lang_choice = query.data
        
        # Store the selected language choice in user data
        context.user_data["language"] = lang_choice
        
        # Update the message with the selected language and provide a confirmation message
        await query.message.edit_text(
            message_text(
                language_code=lang_choice,
                message=start_page,
                context={"user": update.effective_user.full_name, "github_repo": GITHUB_REPO}
            ),
            parse_mode=constants.ParseMode.MARKDOWN
        )
    except Exception as e:
        # Handle any errors that may occur
        await handle_error(update, context, f"Error handling language selection: {e}")

async def handle_message(update: Update, context: CallbackContext) -> None:
    """
    Handles incoming messages from users.
    Processes user messages using MistralAI.
    Updates messages with processed text.
    Records user message counts.
    """
    try:
        if not context.user_data.get("language"):
            await update.message.reply_text(message_text(
                language_code=context.user_data.get("language", update.effective_user.language_code if update.effective_user.language_code in SUPPORTED_LANGUAGES else "en"),
                message=skipped_start_command
            ))
            return
        # Import necessary modules for message processing
        count = context.user_data.get("usageCount", 0) 
        restrict_since = context.user_data.get("restrictSince", 0)
        if restrict_since:
            time_left = (restrict_since + 60 * 5) - time()  # Calculate time left for restriction to expire
            if time_left <= 0:  # If time left is negative, remove restriction
                del context.user_data["restrictSince"]
                del context.user_data["usageCount"]
                await update.message.reply_text(message_text(
                    language_code=context.user_data["language"],
                    message=restriction_end_message
                )) 
            else:
                await update.message.reply_text(message_text(
                    language_code=context.user_data["language"],
                    message=restriction_message
                )) 
                raise ApplicationHandlerStop
        else:
            if count == MAX_USAGE:
                context.user_data["restrictSince"] = time()
                await update.message.reply_text(message_text(
                    language_code=context.user_data["language"],
                    message=restriction_message
                )) 
                raise ApplicationHandlerStop
            else:
                context.user_data["usageCount"] = count + 1
                # Import necessary modules for message processing
        from mistralai.async_client import MistralAsyncClient
        from mistralai.models.chat_completion import ChatMessage

        # Initialize MistralAsyncClient with the provided API key
        client = MistralAsyncClient(api_key=api_key)

        # Initialize an empty string to hold the edited text
        edited_text = ""

        # Send initial response indicating processing is underway
        text = await update.message.reply_text("🤖💬...")

        # Prepare the user's message for processing by Mistral
        messages = [ChatMessage(role="user", content=update.message.text)]

        # Start the async chat stream with Mistral
        async_response = client.chat_stream(model=model, messages=messages)

        # Iterate over the parts received from Mistral
        async for chunk in async_response:
            # Extract content from the chunk
            content = chunk.choices[0].delta.content 

            # Check if content is not empty
            if content:
                edited_text += content

                # Update the message with the edited text in chunks of 50 characters
                if len(edited_text) % 50 == 0:
                    await text.edit_text(edited_text)
        
        # Record the date of the user's message and increment the user's message count
        current_datetime = datetime.now()
        start_date = current_datetime.strftime('%Y-%m-%d')
        user_id = update.effective_user.id
        
        user_data = context.bot_data.setdefault("user_message_counts", {}).setdefault(user_id, {})
        user_data.setdefault(start_date, 0)
        user_data[start_date] += 1
        
        # If there's any remaining edited text, update the message
        if edited_text:
            await text.edit_text(edited_text)
    except Exception as e:
        await handle_error(update, context, f"Error handling message: {e}", reply=False)


async def handle_message_wrapper(update: Update, context: CallbackContext) -> None:
    """
    Wraps the handle_message function in an asynchronous task for execution.
    """
    asyncio.create_task(handle_message(update, context))


async def handle_photo_messages(update: Update, context: CallbackContext) -> None:
    try:
        if not context.user_data.get("language"):
            await update.message.reply_text(message_text(
                language_code=context.user_data.get("language", update.effective_user.language_code if update.effective_user.language_code in SUPPORTED_LANGUAGES else "en"),
                message=skipped_start_command
            ))
            return
        
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
        # Get the current date
        current_date = datetime.now().strftime('%Y-%m-%d')
        
        # Get the number of total users
        total_users = len(context.bot_data.get("user_ids", {}).keys())
        
        # Get the total number of messages handled today
        total_messages_today = sum(user_count.get(current_date, 0) for user_count in context.bot_data.get("user_message_counts", {}).values())

        # Count the number of active users today
        active_users_today = sum(1 for user_count in context.bot_data.get("user_message_counts", {}).values() if current_date in user_count)
        
        # Send information about today's active users and handled messages
        await update.message.reply_text(f"Number of total users: {total_users}\nNumber of active users today: {active_users_today}\nTotal messages handled today: {total_messages_today}")
    except Exception as e:
        await update.message.reply_text(str(e))

async def collect_feedback(update: Update, context: CallbackContext) -> None:
    """
    Command handler to collect feedback from users.
    """
    try:
        # Extract the feedback message from the command
        feedback_message = " ".join(context.args)
        # Store the anonymized feedback in bot data
        
        #check if feedback is not empty
        if feedback_message != "":
            context.bot_data.setdefault("feedbacks", []).append(feedback_message)
            await update.message.reply_text("Thank you for your feedback!")
        else :
            await update.message.reply_text("Please provide a feedback message.")
            return
    except Exception as e:
        await handle_error(update, context, f"Error collecting feedback: {e}")

async def export_data(update: Update, context: CallbackContext) -> None:
    try:
        # Prepare data for export
        bot_data = context.bot_data
        user_data = context.user_data
        
        # Serialize bot data and user data to JSON
        bot_data_json = json.dumps(bot_data, indent=4)
        user_data_json = json.dumps(user_data, indent=4)
        
        # Write JSON data to BytesIO buffer
        with BytesIO() as buffer:
            buffer.write("Bot Data:\n".encode())
            buffer.write(bot_data_json.encode())
            buffer.write("\n\nUser Data:\n".encode())
            buffer.write(user_data_json.encode())

            # Include feedback data if available
            if "feedbacks" in context.bot_data:
                feedbacks_json = json.dumps(context.bot_data["feedbacks"], indent=4)
                buffer.write("\n\nFeedback Data:\n".encode())
                buffer.write(feedbacks_json.encode())

            buffer.seek(0)
            
            # Send the JSON file as a document to the user
            await update.message.reply_document(buffer, filename="data_export.json")
    except Exception as e:
        await update.message.reply_text("Error exporting data: {}".format(e))

def main() -> None:
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TOKEN).build()

    # Add a handler for the /start command to greet new users when they first start using the bot
    # Ask the user to select a language
    application.add_handler(CommandHandler(["start","help"], start_private_chat))

    # Add a handler for the /feedback command
    application.add_handler(CommandHandler("feedback", collect_feedback))

    # Add a handler for the language selection
    application.add_handler(CallbackQueryHandler(handle_language_selection))

    # Add a handler for messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message_wrapper))
    application.add_handler(MessageHandler(filters.ALL & (~filters.PHOTO) & (~filters.TEXT | filters.COMMAND), start_private_chat))

    # Add a handler for photo messages
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))

    # Add a handler for chat member updates
    application.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(ChatMemberHandler(greet_chat_members, ChatMemberHandler.CHAT_MEMBER))

    # Commands that are executed only if the user is an administrator:
    # 1. /admin - Get the number of users in the chat with the bot and the total number of messages handled today
    # 2. /admin_export_data - Export the data of the bot to a JSON file
    # 3. /show_chats - Show which chats the bot is in and how many users are in each
    application.add_handler(CommandHandler(command="admin",filters=filters.User(int(ADMIN_ID)), callback=get_number_of_users))
    application.add_handler(CommandHandler(command="admin_export_data",filters=filters.User(int(ADMIN_ID)), callback=export_data))
    application.add_handler(CommandHandler("show_chats", show_chats,filters=filters.User(int(ADMIN_ID))))

    # Run the bot until the user presses Ctrl-C
    # We pass 'allowed_updates' handle *all* updates including `chat_member` updates
    # To reset this, simply pass `allowed_updates=[]`
    application.run_polling(allowed_updates=Update.ALL_TYPES)


# Run the bot in the main function
if __name__ == "__main__":
    main()