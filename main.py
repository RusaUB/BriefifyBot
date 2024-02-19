import os
import logging
from telegram import Update, constants, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, filters, CallbackContext, CallbackQueryHandler, ContextTypes
from utils import message_text
from bot_conv import bot_greeting, lang_config, start_page, continue_text
from io import BytesIO
from utils import keyboard_layout

import ollama

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


TOKEN = os.environ.get("TELEGRAMM_BOT_TOKEN")

MAX_USAGE = 1

LANGUAGE_CODE = '' 
SUPPORTED_LANGUAGES = ['en', 'ru', 'fr']
GITHUB_REPO = "https://github.com/RusaUB/BriefifyBot"

async def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user.full_name
    LANGUAGE_CODE = update.effective_user.language_code
    # Check if the user has already selected a language
    if "language" in context.user_data:
        # If the user has already selected a language, do not show the language selection menu again
        await update.message.reply_text(message_text(language_code=context.user_data["language"],message=start_page, context={"user": user, "github_repo": GITHUB_REPO}), parse_mode=constants.ParseMode.MARKDOWN)
        return
    # Set default initial language based on user language_code
    if LANGUAGE_CODE not in SUPPORTED_LANGUAGES:
        LANGUAGE_CODE = "en"
    keyboard = keyboard_layout(LANGUAGE_CODE, SUPPORTED_LANGUAGES, lang_config,continue_text)
    await update.message.reply_text(message_text(LANGUAGE_CODE, bot_greeting, context={"user": user}), reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_language_selection(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    lang_choice = query.data
    context.user_data["language"] = lang_choice
    await query.message.edit_text(message_text(language_code=lang_choice,message=start_page, context={"user":update.effective_user.full_name, "github_repo": GITHUB_REPO}), parse_mode=constants.ParseMode.MARKDOWN)


async def handle_message(update: Update, context: CallbackContext) -> None:
    await update.message.chat.send_action(action=constants.ChatAction.TYPING)
    response = ollama.chat(model='openhermes', messages=[
    {
        'role': 'user',
        'content': update.message.text,
    },
    ])
    res = response['message']['content']
    await update.message.reply_text(res)

async def handle_photo_messages(update: Update, context: CallbackContext) -> None:
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = BytesIO(await photo_file.download_as_bytearray())
    message = {
        'role': 'user',
        'content': 'Describe this image:',
        'images': [photo_bytes]
    }
    response = ollama.chat(
        model="llava",
        messages=[message]
    )
    await update.message.reply_text(response["message"]["content"])

def main() -> None:
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))
    application.add_handler(CallbackQueryHandler(handle_language_selection))
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()