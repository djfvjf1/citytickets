from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models

from accounts.models import User
from services.utils import generate_qr_code


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

    def __str__(self):
        return self.title

# здесь находится генерация
class Ticket(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    price = models.IntegerField()
    qr_code = models.ImageField(upload_to='qr_codes', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Билет'
        verbose_name_plural = 'Билеты'

    def __str__(self):
        return f'Билет на "{self.event.title}" для {self.user}'

    def save(self, *args, **kwargs):
        """
        1) Если билет новый – сначала обычный save (INSERT), чтобы получить id.
        2) Потом генерим QR и сохраняем ТОЛЬКО qr_code вторым save'ом (UPDATE).
        3) Если билет уже существующий – просто обычный save.
        """
        is_new = self.pk is None

        # первый save — создаём запись
        super().save(*args, **kwargs)

        # только для новых билетов и только если ещё нет qr_code
        if is_new and not self.qr_code:
            img = generate_qr_code(
                f'Ticket for {self.event.title} | User: {self.user_id}'
            )
            filename = f'qr_ticket_{self.pk}.png'

            # прикрепляем файл к полю, но без рекурсивного save
            self.qr_code.save(filename, img, save=False)

            # второй save — уже UPDATE, без force_insert
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
