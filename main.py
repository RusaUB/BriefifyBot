import os
from mistralai.client import MistralClient
from mistralai.models.chat_completion import ChatMessage
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

API_KEY = os.environ.get("MISTRAL_API_KEY")
TOKEN = os.environ.get("TELEGRAMM_BOT_TOKEN")

client = MistralClient(api_key=API_KEY)

model = "mistral-medium"

async def handle_message(update: Update, context: CallbackContext) -> None:
    
    messages = [
        ChatMessage(role="user", content=update.message.text)
    ]
    
    chat_response = client.chat(
        model=model,
        messages=messages,
        safe_mode=False,
        safe_prompt=False,
    )

    await update.message.reply_text(chat_response.choices[0].message.content)


async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text('Hi! I am your Telegram bot. Ask me anything!')



def main() -> None:
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()