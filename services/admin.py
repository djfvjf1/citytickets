from django.contrib import admin, messages
from django.utils import timezone

from .models import Event, Ticket, Location
from .views import send_refund_email  # твой хелпер

@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    pass

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('id', 'event', 'user', 'price', 'status', 'created_at')
    list_filter = ('status', 'created_at')

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'datetime_passing', 'price', 'category')
    list_filter = ('category',)

    # 🔥 1) удаление одного события из карточки
    def delete_model(self, request, obj):
        now = timezone.now()

        # Берём ВСЕ билеты события, которые ещё не refunded
        tickets = Ticket.objects.filter(event=obj).exclude(status='refunded')

        refunded = 0
        for t in tickets:
            t.status = 'refunded'
            t.refunded_at = now
            t.save(update_fields=['status', 'refunded_at'])
            refunded += 1
            try:
                send_refund_email(t)
            except Exception:
                pass

        super().delete_model(request, obj)
        messages.success(request, f'Событие удалено. Возвратов выполнено: {refunded}')

    # 🔥 2) массовое удаление из списка (actions delete selected)
    def delete_queryset(self, request, queryset):
        now = timezone.now()

        events_ids = list(queryset.values_list('id', flat=True))
        tickets = Ticket.objects.filter(event_id__in=events_ids).exclude(status='refunded')

        refunded = 0
        for t in tickets.select_related('event', 'user'):
            t.status = 'refunded'
            t.refunded_at = now
            t.save(update_fields=['status', 'refunded_at'])
            refunded += 1
            try:
                send_refund_email(t)
            except Exception:
                pass

        super().delete_queryset(request, queryset)
        messages.success(request, f'События удалены. Возвратов выполнено: {refunded}')
