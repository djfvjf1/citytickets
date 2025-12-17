from django.contrib import admin
from .models import Event, Ticket, Location

admin.site.register(Location)
admin.site.register(Event)
admin.site.register(Ticket)
