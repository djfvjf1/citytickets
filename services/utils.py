import qrcode
from io import BytesIO
from django.core.files import File
from PIL import Image
from django.core.files.base import ContentFile


def generate_qr_png(data: str) -> bytes:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ✅ ВАЖНО: оставляем старую функцию, чтобы models.py не падал
def generate_qr_code(data: str) -> ContentFile:
    """
    СТАРЫЙ интерфейс: возвращает ContentFile для ImageField.save().
    Нужен, чтобы твой Ticket.save() (который сохраняет qr_code в ImageField)
    не ломался.
    """
    return ContentFile(generate_qr_png(data), name="qr.png")
