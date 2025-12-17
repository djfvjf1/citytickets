from django.core.management.base import BaseCommand
from services.models import Hall, ColumnSeats, Seat


class Command(BaseCommand):
    help = 'Create 3 halls, each with 6 columns and 10 seats per column'

    def handle(self, *args, **kwargs):
        for i in range(1, 20):
            hall = Hall.objects.create(number_hall=i)

            for j in range(1, 7):
                column = ColumnSeats.objects.create(number_column=j, hall=hall)

                for k in range(1, 11):
                    Seat.objects.create(column=column, row=f'Row {k}', number=k)

        self.stdout.write(
            self.style.SUCCESS('Successfully created 3 halls, each with 6 columns and 10 seats per column'))
