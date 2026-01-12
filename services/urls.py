from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='home'),
    path('events/', views.events_list, name='events'),
    path('events/<int:event_id>/', views.event_details, name='event_details'),

    path('payment/', views.PaymentView.as_view(), name='payment'),
    path('my-tickets/', views.get_my_tickets, name='my_tickets'),
    path('tickets/pdf/<int:ticket_id>/', views.ticket_pdf, name='ticket_pdf'),

    # избранное
    path('favorites/', views.favorites_list, name='favorites'),
    path('favorites/toggle/<int:event_id>/', views.toggle_favorite, name='toggle_favorite'),

    # корзина
    path('cart/', views.cart_view, name='cart'),
    path('cart/add/<int:event_id>/', views.add_to_cart, name='add_to_cart'),
    path('cart/remove/<int:item_id>/', views.cart_remove, name='cart_remove'),

    path('analytics/', views.admin_analytics, name='admin-analytics'),
    path('analytics/export/csv/', views.admin_analytics_export_csv, name='admin_analytics_export_csv'),

    # мгновенный возврат
    path('tickets/<int:ticket_id>/refund-now/', views.refund_now, name='refund_now'),

    # верификация QR
    path('tickets/verify/<int:ticket_id>/', views.verify_ticket, name='verify_ticket')

]
