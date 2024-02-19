def message_text(language_code: str, message: dict, context: dict = None) -> str:
    if context is not None:
        formatted_message = message[language_code].format(**context)
        return formatted_message
    return message[language_code]
