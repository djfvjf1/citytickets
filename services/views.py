from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import EmailMultiAlternatives
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.views import View

from .forms import PaymentForm
from .models import Event, Ticket, Favorite, CartItem

from io import BytesIO
from django.http import HttpResponse
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from django.utils.timezone import localtime
from django.utils import timezone

import logging

from django.views.decorators.http import require_POST
from django.http import HttpResponse, JsonResponse
from django.utils.timezone import localtime

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count
from django.db.models.functions import TruncDate


logger = logging.getLogger(__name__)


def build_ticket_pdf(ticket):
    """
    Генерит PDF по объекту Ticket и возвращает байты.
    """
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 50

    # Заголовок
    c.setFont("Helvetica-Bold", 20)
    c.drawString(50, y, "CityTickets — Электронный билет")
    y -= 40

    c.setFont("Helvetica", 12)
    c.drawString(50, y, f"Билет № {ticket.id}")
    y -= 20

    user_label = ticket.user.email or ticket.user.phone_number or str(ticket.user_id)
    c.drawString(50, y, f"Покупатель: {user_label}")
    y -= 20

    # Дата/время
    dt = localtime(ticket.event.datetime_passing)
    c.drawString(50, y, f"Событие: {ticket.event.title}")
    y -= 20
    c.drawString(50, y, f"Дата: {dt.strftime('%d.%m.%Y')}")
    y -= 20
    c.drawString(50, y, f"Время: {dt.strftime('%H:%M')}")
    y -= 20

    # Локация
    if ticket.event.location:
        loc = ticket.event.location
        loc_parts = [loc.name]
        if loc.city:
            loc_parts.append(loc.city)
        if loc.address:
            loc_parts.append(loc.address)
        loc_str = ", ".join(loc_parts)
        c.drawString(50, y, f"Место: {loc_str}")
        y -= 20

    c.drawString(50, y, f"Цена: {ticket.price} ₸")
    y -= 40

    # QR-код, если есть
    if ticket.qr_code:
        try:
            qr = ImageReader(ticket.qr_code.path)
            qr_size = 200
            c.drawImage(
                qr,
                width - qr_size - 50,
                height - qr_size - 80,
                qr_size,
                qr_size
            )
        except Exception:
            # Если вдруг не прочитается файл — просто пропустим
            pass

    c.showPage()
    c.save()

    pdf = buffer.getvalue()
    buffer.close()
    return pdf


# ===== Главная =====
def index(request):
    return render(request, 'services/home.html')


# ===== Список событий =====
def events_list(request):
    q = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()

    events_qs = Event.objects.all().order_by('datetime_passing')

    if q:
        events_qs = events_qs.filter(
            Q(title__icontains=q) |
            Q(description__icontains=q)
        )

    if category:
        events_qs = events_qs.filter(category=category)

    favorite_ids = set()
    if request.user.is_authenticated:
        favorite_ids = set(
            Favorite.objects.filter(user=request.user).values_list('event_id', flat=True)
        )

    return render(
        request,
        'services/events.html',
        {
            'events': events_qs,
            'search_query': q,
            'selected_category': category,
            'favorite_ids': favorite_ids,
        }
    )


# ===== Детали события =====
def event_details(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    return render(request, 'services/detail.html', {'event': event})


# ===== Оплата =====
class PaymentView(LoginRequiredMixin, View):
    """
    Фейковая оплата:
    - валидируем поля карты,
    - создаём Ticket (не больше одного билета на событие для пользователя),
    - отправляем письмо с билетом и QR (+ PDF),
    - редиректим в "Мои билеты".
    """

    login_url = 'home'  # куда кидать неавторизованных

    def _get_event(self, request):
        event_id = request.GET.get('event')
        if not event_id:
            return None
        return get_object_or_404(Event, pk=event_id)

    def get(self, request):
        event = self._get_event(request)
        if not event:
            return redirect('events')

        form = PaymentForm()
        return render(
            request,
            'services/payment.html',
            {
                'form': form,
                'total_price': event.price,
                'event': event,
            }
        )

    def post(self, request):
        event = self._get_event(request)
        if not event:
            return redirect('events')

        form = PaymentForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                'services/payment.html',
                {
                    'form': form,
                    'total_price': event.price,
                    'event': event,
                    'error': 'Проверьте данные карты',
                }
            )

        user = request.user

        # 🔒 Анти-дубль: защита от многократных кликов по кнопке
        now_ts = timezone.now().timestamp()
        session_key = f"last_payment_event_{event.id}"
        last_ts = request.session.get(session_key)

        # если уже был POST на это событие за последние 5 секунд –
        # считаем, что это повторный клик и просто уводим в "Мои билеты"
        if last_ts and now_ts - last_ts < 5:
            return redirect('my_tickets')

        # запоминаем время оплаты для этого события
        request.session[session_key] = now_ts

        # ✅ создаём билет (один раз на этот POST)
        ticket = Ticket.objects.create(
            event=event,
            user=user,
            price=event.price,
        )

        # ===== письмо с билетом =====
        if user.email:
            subject = f'Ваш билет №{ticket.id} — {event.title}'
            purchase_time = timezone.now()

            html_content = render_to_string(
                'services/ticket-email.html',
                {
                    'tickets': [ticket],
                    'user': user,
                    'purchase_time': purchase_time,
                }
            )
            text_content = strip_tags(html_content)

            email = EmailMultiAlternatives(
                subject,
                text_content,
                'no-reply@citytickets.kz',
                [user.email],
            )
            email.attach_alternative(html_content, "text/html")

            try:
                pdf_bytes = build_ticket_pdf(ticket)
                email.attach(
                    f"ticket_{ticket.id}.pdf",
                    pdf_bytes,
                    "application/pdf"
                )
            except Exception as e:
                logger.exception(e)

            if ticket.qr_code:
                try:
                    email.attach_file(ticket.qr_code.path)
                except Exception as e:
                    logger.exception(e)

            try:
                email.send(fail_silently=False)
                print(f'EMAIL SENT for ticket {ticket.id} to {user.email}')
            except Exception as e:
                print(f'EMAIL ERROR for ticket {ticket.id}: {e}')
                logger.exception(e)

        return redirect('my_tickets')



# ===== Мои билеты =====
@login_required
def get_my_tickets(request):
    tickets = (
        Ticket.objects
        .filter(user=request.user)
        .select_related('event', 'event__location')
    )
    return render(request, 'services/my_tickets.html', {'tickets': tickets})


@login_required
def ticket_pdf(request, ticket_id):
    """
    Пользователь скачивает PDF только для СВОЕГО билета.
    """
    ticket = get_object_or_404(Ticket, pk=ticket_id, user=request.user)

    pdf_bytes = build_ticket_pdf(ticket)

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    filename = f"ticket_{ticket.id}.pdf"
    response['Content-Disposition'] = f'attachment; filename=\"{filename}\"'
    return response


# ===== ИЗБРАННОЕ =====

@login_required(login_url='home')
def favorites_list(request):
    favorites = (
        Favorite.objects
        .filter(user=request.user)
        .select_related('event', 'event__location')
    )
    return render(request, 'services/favorites.html', {'favorites': favorites})


@login_required(login_url='home')
@require_POST
def toggle_favorite(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    fav, created = Favorite.objects.get_or_create(
        user=request.user,
        event=event,
    )
    if not created:
        fav.delete()
        action = 'removed'
    else:
        action = 'added'

    # Если решишь потом делать Ajax – уже готово
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok', 'action': action})

    return redirect(request.META.get('HTTP_REFERER', 'events'))


# ===== КОРЗИНА =====

@login_required(login_url='home')
def cart_view(request):
    items = (
        CartItem.objects
        .filter(user=request.user)
        .select_related('event')
        .order_by('-added_at')
    )

    total_price = sum(item.event.price * item.quantity for item in items)

    return render(
        request,
        'services/cart.html',
        {
            'items': items,
            'total_price': total_price,
        }
    )


@login_required(login_url='home')
@require_POST
def add_to_cart(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    qty = int(request.POST.get('quantity', 1) or 1)

    item, created = CartItem.objects.get_or_create(
        user=request.user,
        event=event,
        defaults={'quantity': qty},
    )
    if not created:
        item.quantity += qty
        item.save()

    return redirect(request.META.get('HTTP_REFERER', 'events'))


@login_required(login_url='home')
@require_POST
def cart_remove(request, item_id):
    item = get_object_or_404(CartItem, pk=item_id, user=request.user)
    item.delete()
    return redirect('cart')


@staff_member_required(login_url='home')
def admin_analytics(request):
    qs = Ticket.objects.select_related('event', 'user').all()

    total_tickets = qs.count()
    total_revenue = qs.aggregate(s=Sum('price'))['s'] or 0

    # ✅ продажи по событиям (лидерборд)
    sales_by_event = (
        qs.values('event__id', 'event__title')
          .annotate(tickets=Count('id'), revenue=Sum('price'))
          .order_by('-tickets', '-revenue')
    )

    top_events = list(sales_by_event[:5])

    # ✅ продажи по категориям (у тебя category = CharField choices)
    sales_by_category_raw = (
        qs.values('event__category')
          .annotate(tickets=Count('id'), revenue=Sum('price'))
          .order_by('-tickets', '-revenue')
    )

    # маппинг ключ -> человекочитаемое название
    category_map = dict(Event.CATEGORY_CHOICES)
    sales_by_category = []
    for row in sales_by_category_raw:
        code = row['event__category']
        sales_by_category.append({
            'code': code,
            'name': category_map.get(code, code),
            'tickets': row['tickets'],
            'revenue': row['revenue'],
        })

    # ✅ последние покупки
    recent_purchases = qs.order_by('-created_at')[:50]

    # ✅ график продаж по дням (выручка + билеты)
    series = (
        qs.annotate(d=TruncDate('created_at'))
          .values('d')
          .annotate(revenue=Sum('price'), tickets=Count('id'))
          .order_by('d')
    )

    chart_labels = [str(x['d']) for x in series]
    chart_revenue = [float(x['revenue'] or 0) for x in series]
    chart_tickets = [int(x['tickets'] or 0) for x in series]

    return render(request, 'services/admin_analytics.html', {
        'total_tickets': total_tickets,
        'total_revenue': total_revenue,
        'top_events': top_events,
        'sales_by_event': sales_by_event,
        'sales_by_category': sales_by_category,
        'recent_purchases': recent_purchases,
        'chart_labels': chart_labels,
        'chart_revenue': chart_revenue,
        'chart_tickets': chart_tickets,
    })
