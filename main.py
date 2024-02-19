import os
import logging
from time import time
from mistralai.client import MistralClient
from mistralai.models.chat_completion import ChatMessage
from telegram import Update, constants, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, filters, CallbackContext, ApplicationHandlerStop, CallbackQueryHandler
from utils import message_text
from bot_conv import bot_greeting, lang_config, start_page, model_system_role, continue_text

from utils import keyboard_layout

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

API_KEY = os.environ.get("MISTRAL_API_KEY")
TOKEN = os.environ.get("TELEGRAMM_BOT_TOKEN")

client = MistralClient(api_key=API_KEY)

model = "mistral-medium"

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
    count = context.user_data.get("usageCount", 0) 
    restrict_since = context.user_data.get("restrictSince", 0)
    if restrict_since:
        time_left = (restrict_since + 60 * 5) - time()  # Calculate time left for restriction to expire
        if time_left <= 0:  # If time left is negative, remove restriction
            del context.user_data["restrictSince"]
            del context.user_data["usageCount"]
            await update.message.reply_text("I have unrestricted you. Please behave well.") 
        else:
            minutes_left = int(time_left / 60)  # Convert remaining seconds to minutes
            await update.message.reply_text(f"Back off! Wait for your restriction to expire... Remaining time: {minutes_left} minutes")
            raise ApplicationHandlerStop
    else:
        if count == MAX_USAGE:
            context.user_data["restrictSince"] = time()
            await update.message.reply_text("⚠️ You've used up all your free requests. Please wait for 5 minutes before trying again.") #print the remaining time
            raise ApplicationHandlerStop
        else:
            context.user_data["usageCount"] = count + 1
    messages = [
        ChatMessage(role="system", content = message_text(language_code=context.user_data["language"], message=model_system_role)),
        ChatMessage(role="user", content=update.message.text)
        ]
    chat_response = client.chat(
        model=model,
        messages=messages,
        safe_mode=False,
        safe_prompt=False,
    )
    await update.message.reply_text(chat_response.choices[0].message.content)


def main() -> None:
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_language_selection))
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
