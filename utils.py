from telegram import InlineKeyboardButton

def get_key_from_value(dictionary, target_value):
    for key, value in dictionary.items():
        if value == target_value:
            return key
    return None  # Return None if the value is not found in the dictionary

def message_text(language_code: str, message: dict, context: dict = None) -> str:
    if context is not None:
        formatted_message = message[language_code].format(**context)
        return formatted_message
    return message[language_code]

def keyboard_layout(default_lang: str, supported_lang: list, lang_config: dict, continue_text: dict) -> list:
    keyboard = [[InlineKeyboardButton(text=continue_text[default_lang], callback_data=default_lang)]]
    for lang in supported_lang:
        if lang != default_lang:
            keyboard.append([InlineKeyboardButton(text=lang_config[lang], callback_data=lang)])
    return keyboard