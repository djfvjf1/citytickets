import re


def normalize_phone(phone: str) -> str:
    """
    Приводим телефон к формату +7XXXXXXXXXX (последние 10 цифр).
    Любые скобки/пробелы/дефисы выкидываем.
    """
    if not phone:
        return ''

    digits = re.sub(r'\D+', '', phone)   # оставляем только цифры
    digits = digits[-10:]                # берём последние 10

    if len(digits) != 10:
        return ''

    return f'+7{digits}'
