import os
from time import time
from mistralai.client import MistralClient
from mistralai.models.chat_completion import ChatMessage
from telegram import Update, constants
from telegram.ext import Application, MessageHandler, CommandHandler, filters, CallbackContext, ApplicationHandlerStop

API_KEY = os.environ.get("MISTRAL_API_KEY")
TOKEN = os.environ.get("TELEGRAMM_BOT_TOKEN")

client = MistralClient(api_key=API_KEY)

model = "mistral-tiny"

MAX_USAGE = 1

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
    messages = [ChatMessage(role="user", content=update.message.text)]
    chat_response = client.chat(
        model=model,
        messages=messages,
        safe_mode=False,
        safe_prompt=False,
    )
    await update.message.reply_text(chat_response.choices[0].message.content)

async def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user.full_name
    await update.message.reply_text(f"Welcome, {user} ! ")

def main() -> None:
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
