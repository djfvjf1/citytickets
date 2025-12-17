from django.core.management.base import BaseCommand
from ...models import Event
from django.utils import timezone


class Command(BaseCommand):
    help = 'Delete event that have already passed'

    def handle(self, *args, **kwargs):
        events = Event.objects.all()
        deleted_count = 0
        for event in events:
            if event.datetime_passing < timezone.now():
                event.delete_related_data()
                deleted_count += 1
        self.stdout.write(self.style.SUCCESS(f'Successfully deleted {deleted_count} old events.'))
