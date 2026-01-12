from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import EmailMultiAlternatives
from django.db import transaction, IntegrityError
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.views import View

from .forms import PaymentForm
from .models import Event, Ticket, Favorite, CartItem
from django.contrib.auth import get_user_model
User = get_user_model()


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

from datetime import timedelta
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count, Avg, Q, F

import csv
from django.contrib import messages

from django.conf import settings

from django.core.mail import send_mail

from django.views.decorators.http import require_http_methods

from .utils import generate_qr_png

logger = logging.getLogger(__name__)

REFUND_LOCK_HOURS = 2  # запрет возврата за N часов до начала


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


# helper для письма
def send_refund_email(ticket):
    user = ticket.user
    if not user.email:
        return

    subject = f'Возврат оформлен — билет №{ticket.id}'
    text = (
        f"Здравствуйте!\n\n"
        f"Мы оформили возврат по билету №{ticket.id}.\n"
        f"Событие: {ticket.event.title}\n"
        f"Сумма: {ticket.price} ₸\n"
        f"Статус: Возвращён\n\n"
        f"CityTickets"
    )

    send_mail(
        subject,
        text,
        getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@citytickets.local'),
        [user.email],
        fail_silently=False
    )




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
    now = timezone.now()
    lock_delta = timedelta(hours=REFUND_LOCK_HOURS)

    tickets = list(
        Ticket.objects
        .filter(user=request.user)
        .select_related('event', 'event__location')
        .order_by('-created_at')
    )

    for t in tickets:
        if t.status != 'paid':
            t.refund_reason = 'Билет не в статусе "Оплачен".'
            t.can_refund = False
        elif t.used_at:
            t.refund_reason = 'Билет уже использован.'
            t.can_refund = False
        elif t.event.datetime_passing <= now:
            t.refund_reason = 'Событие уже прошло.'
            t.can_refund = False
        elif now >= (t.event.datetime_passing - lock_delta):
            t.refund_reason = f'Нельзя вернуть меньше чем за {REFUND_LOCK_HOURS} часа(ов) до начала.'
            t.can_refund = False
        else:
            t.refund_reason = ''
            t.can_refund = True

    return render(request, 'services/my_tickets.html', {
        'tickets': tickets,
        'refund_lock_hours': REFUND_LOCK_HOURS,
        'now': now,  # ✅ важно
    })


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
    # ----------------------------
    # 0) Параметры
    # ----------------------------
    period = request.GET.get('period', '30')  # all | 7 | 30 | 90
    mode = request.GET.get('mode', 'gross')   # gross | net

    base_qs = Ticket.objects.select_related('event', 'user').all()

    # ----------------------------
    # 1) Периоды + сравнение с предыдущим периодом
    # ----------------------------
    if period != 'all':
        days = int(period)
        since = timezone.now() - timedelta(days=days)

        qs = base_qs.filter(created_at__gte=since)

        prev_since = since - timedelta(days=days)
        prev_qs = base_qs.filter(created_at__gte=prev_since, created_at__lt=since)
    else:
        qs = base_qs
        prev_qs = None

    # ✅ Gross / Net:
    # gross: считаем всё (как было)
    # net: выручка и продажи считаются только по paid (refund не входит)
    qs_for_kpi = qs.filter(status='paid') if mode == 'net' else qs
    prev_for_kpi = None
    if prev_qs is not None:
        prev_for_kpi = prev_qs.filter(status='paid') if mode == 'net' else prev_qs

    def kpi(q):
        return {
            'tickets': q.count(),
            'revenue': q.aggregate(s=Sum('price'))['s'] or 0
        }

    cur = kpi(qs_for_kpi)
    prev = kpi(prev_for_kpi) if prev_for_kpi is not None else None

    def pct(cur_v, prev_v):
        if prev_v is None:
            return None
        if prev_v == 0:
            return None if cur_v == 0 else 100.0
        return round((cur_v - prev_v) * 100 / prev_v, 1)

    growth = {
        'tickets_pct': pct(cur['tickets'], prev['tickets'] if prev else None),
        'revenue_pct': pct(cur['revenue'], prev['revenue'] if prev else None),
    }

    total_tickets = cur['tickets']
    total_revenue = cur['revenue']

    # ----------------------------
    # 2) Воронка + продуктовые метрики
    # ----------------------------
    total_users = User.objects.count()

    # buyers и repeat считаем по "paid", иначе refunded будет считаться покупкой
    qs_paid = qs.filter(status='paid')
    buyers = qs_paid.values('user_id').distinct().count()
    repeat_buyers = (
        qs_paid.values('user_id')
              .annotate(c=Count('id'))
              .filter(c__gte=2)
              .count()
    )

    avg_ticket_price = qs_paid.aggregate(a=Avg('price'))['a'] or 0
    arppu = (float(qs_paid.aggregate(s=Sum('price'))['s'] or 0) / buyers) if buyers else 0
    repeat_rate = (repeat_buyers * 100 / buyers) if buyers else 0

    funnel = {
        'total_users': total_users,
        'buyers': buyers,
        'tickets': total_tickets,
        'repeat_buyers': repeat_buyers,
        'repeat_rate': round(repeat_rate, 1),
        'avg_ticket_price': round(float(avg_ticket_price), 1) if avg_ticket_price else 0,
        'arppu': round(float(arppu), 1) if arppu else 0,
    }

    # ----------------------------
    # 3) Качество (refund/used) — считаем по qs (по периоду), независимо от mode
    # ----------------------------
    refunded_count = qs.filter(status='refunded').count()
    used_count = qs.filter(status='used').count()

    denom = qs.count() or 0  # чтобы проценты были адекватны по "факту"
    refund_rate = (refunded_count * 100 / denom) if denom else 0
    used_rate = (used_count * 100 / denom) if denom else 0

    quality = {
        'refunded_count': refunded_count,
        'used_count': used_count,
        'refund_rate': round(refund_rate, 1),
        'used_rate': round(used_rate, 1),
    }

    # ----------------------------
    # 4) Продажи по событиям/категориям/последние покупки
    # ----------------------------
    # Для таблиц продаж логичнее показывать "paid" (иначе refunded будет портить картину)
    sales_qs = qs_paid if mode == 'net' else qs

    sales_by_event = (
        sales_qs.values('event__id', 'event__title')
                .annotate(tickets=Count('id'), revenue=Sum('price'))
                .order_by('-tickets', '-revenue')
    )
    top_events = list(sales_by_event[:5])

    sales_by_category_raw = (
        sales_qs.values('event__category')
                .annotate(tickets=Count('id'), revenue=Sum('price'))
                .order_by('-tickets', '-revenue')
    )

    category_map = dict(Event.CATEGORY_CHOICES)
    sales_by_category = [
        {
            'code': row['event__category'],
            'name': category_map.get(row['event__category'], row['event__category']),
            'tickets': row['tickets'],
            'revenue': row['revenue'],
        }
        for row in sales_by_category_raw
    ]

    recent_purchases = qs.order_by('-created_at')[:50]

    # ----------------------------
    # 5) График (по дням) — тоже по sales_qs, чтобы net реально был net
    # ----------------------------
    series = (
        sales_qs.annotate(d=TruncDate('created_at'))
                .values('d')
                .annotate(revenue=Sum('price'), tickets=Count('id'))
                .order_by('d')
    )
    chart_labels = [str(x['d']) for x in series]
    chart_revenue = [float(x['revenue'] or 0) for x in series]
    chart_tickets = [int(x['tickets'] or 0) for x in series]

    # ----------------------------
    # 6) ABC-анализ (по выручке) — тоже по sales_qs
    # ----------------------------
    sales_list = list(
        sales_qs.values('event__id', 'event__title')
                .annotate(revenue=Sum('price'), tickets=Count('id'))
                .order_by('-revenue', '-tickets')
    )

    total_rev = sum((x['revenue'] or 0) for x in sales_list) or 1
    cum = 0
    abc_rows = []
    for row in sales_list:
        cum += (row['revenue'] or 0)
        share = cum / total_rev

        if share <= 0.8:
            cls = 'A'
        elif share <= 0.95:
            cls = 'B'
        else:
            cls = 'C'

        abc_rows.append({
            **row,
            'abc': cls,
            'cum_share': round(share * 100, 1),
        })

    abc_summary = {
        'A': sum(1 for r in abc_rows if r['abc'] == 'A'),
        'B': sum(1 for r in abc_rows if r['abc'] == 'B'),
        'C': sum(1 for r in abc_rows if r['abc'] == 'C'),
    }

    # ----------------------------
    # 7) Алерты/мониторинг
    # ----------------------------
    upcoming_no_sales = (
        Event.objects.filter(datetime_passing__gte=timezone.now())
             .annotate(sold=Count('ticket'))
             .filter(sold=0)
             .order_by('datetime_passing')[:10]
    )

    avg_price_all = Event.objects.aggregate(a=Avg('price'))['a'] or 0
    price_alerts = []
    if avg_price_all:
        low = avg_price_all * 0.5
        high = avg_price_all * 2.0
        price_alerts = Event.objects.filter(Q(price__lte=low) | Q(price__gte=high)).order_by('-price')[:10]

    refunds_by_event = (
        qs.filter(status='refunded')
          .values('event__id', 'event__title')
          .annotate(refunds=Count('id'))
          .order_by('-refunds')[:10]
    )

    return render(request, 'services/admin_analytics.html', {
        'period': period,
        'mode': mode,

        'total_tickets': total_tickets,
        'total_revenue': total_revenue,
        'growth': growth,

        'funnel': funnel,
        'quality': quality,

        'top_events': top_events,
        'sales_by_event': sales_by_event,
        'sales_by_category': sales_by_category,
        'recent_purchases': recent_purchases,

        'chart_labels': chart_labels,
        'chart_revenue': chart_revenue,
        'chart_tickets': chart_tickets,

        'abc_rows': abc_rows[:50],
        'abc_summary': abc_summary,

        'upcoming_no_sales': upcoming_no_sales,
        'price_alerts': price_alerts,
        'refunds_by_event': refunds_by_event,
    })


@staff_member_required(login_url='home')
def admin_analytics_export_csv(request):
    # экспорт учитывает те же параметры (period/mode)
    period = request.GET.get('period', '30')  # all | 7 | 30 | 90
    mode = request.GET.get('mode', 'gross')  # gross | net

    base_qs = Ticket.objects.select_related('event', 'user').order_by('-created_at')

    if period != 'all':
        days = int(period)
        since = timezone.now() - timedelta(days=days)
        qs = base_qs.filter(created_at__gte=since)
    else:
        qs = base_qs

    if mode == 'net':
        qs = qs.filter(status='paid')

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="tickets_{timezone.now().date()}_{period}_{mode}.csv"'
    response.write('\ufeff')  # Excel UTF-8

    writer = csv.writer(response)
    writer.writerow(['Дата', 'Пользователь email', 'Телефон', 'Событие', 'Цена', 'Статус'])

    for t in qs:
        writer.writerow([
            t.created_at.strftime('%Y-%m-%d %H:%M'),
            getattr(t.user, 'email', ''),
            getattr(t.user, 'phone_number', ''),
            t.event.title,
            t.price,
            getattr(t, 'status', ''),
        ])

    return response



# мгновенный возврат

@login_required(login_url='home')
@require_POST
def refund_now(request, ticket_id):
    ticket = get_object_or_404(
        Ticket.objects.select_related('event'),
        pk=ticket_id,
        user=request.user
    )

    if ticket.status != 'paid':
        messages.error(request, 'Возврат недоступен: билет уже не в статусе "Оплачен".')
        return redirect('my_tickets')

    if ticket.used_at:
        messages.error(request, 'Возврат недоступен: билет уже использован.')
        return redirect('my_tickets')

    now = timezone.now()
    event_dt = ticket.event.datetime_passing

    if event_dt <= now:
        messages.error(request, 'Возврат недоступен: событие уже прошло.')
        return redirect('my_tickets')

    lock_dt = event_dt - timedelta(hours=REFUND_LOCK_HOURS)
    if now >= lock_dt:
        messages.error(request, f'Возврат недоступен: меньше чем за {REFUND_LOCK_HOURS} часа(ов) до начала события.')
        return redirect('my_tickets')

    ticket.status = 'refunded'
    ticket.refunded_at = now
    ticket.save(update_fields=['status', 'refunded_at'])

    try:
        send_refund_email(ticket)
    except Exception:
        logger.exception("Refund email failed")

    messages.success(request, f'Возврат оформлен. Сумма: {ticket.price} ₸')
    return redirect('my_tickets')


@require_http_methods(["GET", "POST"])
def verify_ticket(request, ticket_id):
    ticket = get_object_or_404(
        Ticket.objects.select_related('event', 'user'),
        pk=ticket_id
    )

    # Логика валидности:
    # - билет должен быть paid
    # - не использован
    # - событие не прошло
    # - билет не возвращён
    now = timezone.now()

    valid = (
        ticket.status == 'paid'
        and ticket.used_at is None
        and ticket.event.datetime_passing > now
    )

    # Если staff нажимает "Использовать билет" — помечаем used
    can_mark_used = request.user.is_authenticated and request.user.is_staff

    if request.method == "POST":
        if not can_mark_used:
            return HttpResponse("Forbidden", status=403)

        if valid:
            ticket.status = 'used'
            ticket.used_at = now
            ticket.save(update_fields=['status', 'used_at'])
            valid = False  # после использования уже не валиден как "не использован"

    return render(request, 'services/verify_ticket.html', {
        'ticket': ticket,
        'valid': valid,
        'can_mark_used': can_mark_used,
        'now': now,
    })


def _ticket_verify_url(ticket_id: int) -> str:
    # подпись, чтобы нельзя было просто подобрать /verify/1/ без защиты
    token = signing.dumps({"ticket_id": ticket_id})
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    return f"{base}/tickets/verify/{ticket_id}/{token}/"


@login_required
def ticket_qr_png(request, ticket_id):
    """
    QR-картинка на лету (без /media).
    Внутри QR — ссылка на verify.
    """
    ticket = get_object_or_404(Ticket, pk=ticket_id, user=request.user)

    url = _ticket_verify_url(ticket.id)
    png_bytes = generate_qr_png(url)

    return HttpResponse(png_bytes, content_type="image/png")


def verify_ticket(request, ticket_id, token):
    """
    Публичная страница проверки билета (по QR).
    """
    # 1) проверяем подпись
    try:
        payload = signing.loads(token, max_age=60 * 60 * 24 * 365)  # год
        if int(payload.get("ticket_id")) != int(ticket_id):
            raise signing.BadSignature("ticket id mismatch")
    except Exception:
        return render(request, "services/verify_ticket.html", {
            "ok": False,
            "reason": "QR-код недействителен (подпись неверная).",
            "ticket": None,
        })

    # 2) ищем билет
    ticket = get_object_or_404(Ticket.objects.select_related("event", "user"), pk=ticket_id)

    # 3) статус/валидность
    if ticket.status == "refunded":
        return render(request, "services/verify_ticket.html", {
            "ok": False,
            "reason": "Билет возвращён (недействителен).",
            "ticket": ticket,
        })

    if ticket.status == "cancelled":
        return render(request, "services/verify_ticket.html", {
            "ok": False,
            "reason": "Событие отменено (билет недействителен).",
            "ticket": ticket,
        })

    if ticket.used_at or ticket.status == "used":
        return render(request, "services/verify_ticket.html", {
            "ok": False,
            "reason": "Билет уже использован.",
            "ticket": ticket,
        })

    # если событие уже прошло — по желанию тоже можно считать невалидным
    if ticket.event.datetime_passing <= timezone.now():
        return render(request, "services/verify_ticket.html", {
            "ok": False,
            "reason": "Событие уже прошло.",
            "ticket": ticket,
        })

    # ✅ валиден
    return render(request, "services/verify_ticket.html", {
        "ok": True,
        "reason": "Билет действителен ✅",
        "ticket": ticket,
    })