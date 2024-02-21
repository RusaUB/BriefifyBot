import os
import logging
from telegram import Update, constants, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, filters, CallbackContext, CallbackQueryHandler, ContextTypes, AIORateLimiter, ApplicationHandlerStop
from utils import message_text
from bot_conv import bot_greeting, lang_config, start_page, continue_text
from io import BytesIO
from utils import keyboard_layout
from datetime import datetime
import ollama
import pandas as pd
import time
import asyncio
from mistralai.async_client import MistralAsyncClient
from mistralai.models.chat_completion import ChatMessage


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


LANGUAGE_CODE = '' 
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SUPPORTED_LANGUAGES = ['en', 'ru', 'fr']
GITHUB_REPO = "https://github.com/RusaUB/BriefifyBot"
ADMIN_ID = os.environ.get("TELEGRAM_ADMIN_ID")

api_key = os.environ["MISTRAL_API_KEY"]
model = "mistral-tiny"

MAX_USAGE = 1

client = MistralAsyncClient(api_key=api_key)

async def handle_error(update, context, error_message):
    """
    Handles errors by sending a generic error message and logging the error.
    """
    await update.message.reply_text("Something went wrong. Please try again later.")
    logger.error(error_message)

async def start(update: Update, context: CallbackContext) -> None:
    """
    Start command handler. Handles the /start command.
    """
    user = update.effective_user.full_name
    await update.message.reply_text(update.effective_user.id)
    global LANGUAGE_CODE  # Ensure we modify the global variable
    LANGUAGE_CODE = update.effective_user.language_code
    # Check if the user has already selected a language
    if "language" in context.user_data:
        # If the user has already selected a language, do not show the language selection menu again
        await update.message.reply_text(message_text(language_code=context.user_data["language"],message=start_page, context={"user": user, "github_repo": GITHUB_REPO}), parse_mode= constants.ParseMode.MARKDOWN)
        return
    # Set default initial language based on user language_code
    if LANGUAGE_CODE not in SUPPORTED_LANGUAGES:
        LANGUAGE_CODE = "en"
    keyboard = keyboard_layout(LANGUAGE_CODE, SUPPORTED_LANGUAGES, lang_config, continue_text)
    await update.message.reply_text(message_text(LANGUAGE_CODE, bot_greeting, context={"user": user}), reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_language_selection(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    lang_choice = query.data
    context.user_data["language"] = lang_choice
    await query.message.edit_text(message_text(language_code=lang_choice,message=start_page, context={"user":update.effective_user.full_name, "github_repo": GITHUB_REPO}), parse_mode=constants.ParseMode.MARKDOWN)


async def handle_message(update: Update, context: CallbackContext) -> None:
    try:
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

async def feedback(update: Update, context: CallbackContext):
    try:
        # Retrieve the feedback number from bot_data or initialize it to 1
        feedback_number = context.bot_data.get('feedback_counter', 1)
        
        # Extract the content of the feedback
        content = ' '.join(context.args)
        
        # Get the current time
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Construct the feedback message including the current time
        feedback_message = f"{content} (Feedback made at {current_time})"
        
        # Save the feedback message to bot_data
        feedbacks = context.bot_data.get('feedbacks', [])
        feedbacks.append(feedback_message)
        context.bot_data['feedbacks'] = feedbacks
        
        # Update the feedback counter for the next feedback
        context.bot_data['feedback_counter'] = feedback_number + 1
        
        # Reply to the user indicating that the feedback has been received
        await update.message.reply_text(f"Feedback received.")
        
    except Exception as e:
        await handle_error(update, context, f"Error handling feedback: {e}")

async def get_feedbacks(update: Update, context: CallbackContext):
    try:
        # Retrieve feedbacks from bot_data
        feedbacks = context.bot_data.get('feedbacks', [])
        
        if feedbacks:
            # Split each feedback entry into content and time
            feedback_data = [(feedback.split(' (Feedback made at ')[0], 
                              feedback.split(' (Feedback made at ')[1].rstrip(')')) 
                             for feedback in feedbacks]
            
            # Convert feedbacks to DataFrame
            df = pd.DataFrame(feedback_data, columns=['Feedback', 'Time'])
            
            # Export DataFrame to Excel in memory
            excel_data = BytesIO()
            df.to_excel(excel_data, index=False)
            excel_data.seek(0)
            
            # Send the Excel file
            await context.bot.send_document(chat_id=update.message.chat_id, document=excel_data, filename='feedbacks.xlsx')
        else:
            await update.message.reply_text("No feedbacks available.")
            
    except Exception as e:
        await handle_error(update, context, f"Error getting feedbacks: {e}")

async def get_number_of_users(update: Update, context: CallbackContext):
    try:
        count = await update.message._bot.get_chat_member_count(chat_id=update.effective_chat.id)
        await update.message.reply_text(f"Total user => {count}")
    except Exception as e:
        await handle_error(update, context, f"Error tracking chat members: {e}")

def main() -> None:
    application = Application.builder().rate_limiter(AIORateLimiter(overall_max_rate=0, overall_time_period=0)).token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("feedback", feedback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message_wrapper))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))
    application.add_handler(CallbackQueryHandler(handle_language_selection))

    #admin commands
    application.add_handler(CommandHandler(command="admin",filters=filters.User(int(ADMIN_ID)), callback=get_number_of_users))
    application.add_handler(CommandHandler(command="admin_feedbacks",filters=filters.User(int(ADMIN_ID)), callback=get_feedbacks))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()