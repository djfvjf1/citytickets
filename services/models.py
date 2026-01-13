from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

from accounts.models import User
from services.utils import generate_qr_code
from django.conf import settings 
from django.core import signing



class Location(models.Model):
    name = models.CharField(max_length=120, verbose_name='Площадка')
    address = models.CharField(max_length=255, blank=True, verbose_name='Адрес')
    city = models.CharField(max_length=80, blank=True, verbose_name='Город')
    capacity = models.PositiveIntegerField(null=True, blank=True, verbose_name='Вместимость')

    class Meta:
        verbose_name = 'Площадка'
        verbose_name_plural = 'Площадки'

    def __str__(self):
        return self.name


class Event(models.Model):
    title = models.CharField(max_length=60)
    image = models.ImageField(upload_to='media/img/events', null=True)
    description = models.CharField(max_length=255)
    price = models.IntegerField(
                                validators=[MinValueValidator(0)])
    duration = models.IntegerField(validators=[MinValueValidator(0)])
    datetime_passing = models.DateTimeField()
    organizer = models.CharField(max_length=125)

    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='events',
        verbose_name='Место проведения',
    )

    age_limit = models.IntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        default=6
    )

    CATEGORY_CHOICES = [
        ('concert', 'Концерты'),
        ('theatre', 'Театр & Шоу'),
        ('sport', 'Спорт'),
        ('festival', 'Фестивали'),
        ('other', 'Другое'),
    ]

    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default='other',
        verbose_name='Тип события'
    )

    is_cancelled = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    def cancel(self):
        self.is_cancelled = True
        self.cancelled_at = timezone.now()
        self.save(update_fields=['is_cancelled', 'cancelled_at'])

    def __str__(self):
        return self.title

# здесь находится генерация
class Ticket(models.Model):
    STATUS_CHOICES = [
        ('paid', 'Оплачен'),
        ('refreq', 'Запрошен возврат'),
        ('refunded', 'Возвращён'),
        ('cancelled', 'Отменён'),
        ('used', 'Использован'),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    price = models.IntegerField()
    qr_code = models.ImageField(upload_to='qr_codes', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='paid')
    refunded_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Билет'
        verbose_name_plural = 'Билеты'

    def __str__(self):
        return f'Билет на "{self.event.title}" для {self.user}'


    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)

        if is_new and not self.qr_code:
            base = getattr(settings, "SITE_URL", "").rstrip("/")
            token = signing.dumps({"ticket_id": self.pk})
            verify_url = f"{base}/tickets/verify/{self.pk}/{token}/"

            img = generate_qr_code(verify_url)
            filename = f"qr_ticket_{self.pk}.png"

            self.qr_code.save(filename, img, save=False)
            super(Ticket, self).save(update_fields=['qr_code'])

class Favorite(models.Model):
    user = models.ForeignKey( # бумажка для начальника user
        User,
        on_delete=models.CASCADE,
        related_name='favorites'
    )
    event = models.ForeignKey(    # бумажка для начальника event
        Event,
        on_delete=models.CASCADE,
        related_name='favorite_for'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Избранное'
        verbose_name_plural = 'Избранные события'
        unique_together = ('user', 'event')

    def __str__(self):
        return f'{self.user} → {self.event}'


class CartItem(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='cart_items'
    )
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Товар в корзине'
        verbose_name_plural = 'Корзина'
        unique_together = ('user', 'event')

    def __str__(self):
        return f'{self.user} — {self.event} x{self.quantity}'
