import json
import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model, authenticate, login, logout
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.contrib import messages

from .models import User, PasswordResetCode, ProfileEditCode
from .utils import normalize_phone

from .forms import ProfileForm
from django.contrib.auth.decorators import login_required


User = get_user_model()


def _json(request):
    try:
        return json.loads(request.body.decode('utf-8'))
    except Exception:
        return {}


# ---------- SIGN UP ----------

@require_POST
def sign_up(request):
    data = _json(request)

    raw_phone = (data.get('phone_number') or '').strip()
    email = (data.get('email') or '').strip()
    password = data.get('password') or ''

    phone = normalize_phone(raw_phone)

    # ВАЛИДАЦИИ
    if not phone:
        return JsonResponse({'status': 'error', 'message': 'Введите корректный номер телефона'}, status=400)

    if not email:
        return JsonResponse({'status': 'error', 'message': 'Укажите email'}, status=400)

    if len(password) < 6:
        return JsonResponse({'status': 'error', 'message': 'Пароль должен быть не короче 6 символов'}, status=400)

    if User.objects.filter(phone_number=phone).exists():
        return JsonResponse({'status': 'error', 'message': 'Этот номер уже зарегистрирован'}, status=400)

    if User.objects.filter(email__iexact=email).exists():
        return JsonResponse({'status': 'error', 'message': 'Этот email уже используется'}, status=400)

    # СОЗДАЁМ ПОЛЬЗОВАТЕЛЯ
    user = User.objects.create_user(
        phone_number=phone,
        email=email,
        password=password
    )

    # 👇 добавляем backend
    user.backend = 'django.contrib.auth.backends.ModelBackend'

    login(request, user)  # тут backend уже проставлен create_user'ом через ModelBackend
    return JsonResponse({'status': 'ok', 'message': 'Регистрация прошла успешно'})


# ---------- SIGN IN ----------

@require_POST
def sign_in(request):
    data = _json(request)

    identifier = (data.get('identifier') or '').strip()  # телефон или email
    password = data.get('password') or ''

    if not identifier or not password:
        return JsonResponse(
            {'status': 'error', 'message': 'Введите логин (телефон или email) и пароль'},
            status=400
        )

    user = None

    # 1) пробуем как телефон
    phone = normalize_phone(identifier)
    if phone:
        user = authenticate(request, phone_number=phone, password=password)

    # 2) пробуем как email
    if user is None:
        try:
            candidate = User.objects.get(email__iexact=identifier)
        except User.DoesNotExist:
            candidate = None

        if candidate:
            # вызываем authenticate с его телефоном,
            # чтобы Django сам выбрал backend и проставил user.backend
            user = authenticate(request, phone_number=candidate.phone_number, password=password)

    if user is None:
        return JsonResponse(
            {'status': 'error', 'message': 'Неверный логин или пароль'},
            status=400
        )

    if not user.is_active:
        return JsonResponse(
            {'status': 'error', 'message': 'Аккаунт отключён'},
            status=403
        )

    # Админам запрещаем вход через обычную форму, только через админ-панель
    if user.is_staff:
        return JsonResponse(
            {
                'status': 'error',
                'message': 'Администраторы заходят только через админ-панель.',
            },
            status=403
        )
    
    user.backend = 'django.contrib.auth.backends.ModelBackend'

    login(request, user)  # backend уже внутри user после authenticate
    return JsonResponse({'status': 'ok', 'message': 'Вы вошли в аккаунт'})


# ---------- LOGOUT ----------

@require_POST
def logout_view(request):
    logout(request)
    return JsonResponse({'status': 'ok'})


# ---------- PASSWORD RESET (STEP 1) ----------

def password_reset_request(request):
    """
    Страница, где юзер вводит email, мы отправляем код.
    """
    context = {}

    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip()
        context['email'] = email

        if not email:
            context['error'] = 'Введите email'
            return render(request, 'accounts/password_reset_request.html', context)

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            context['error'] = 'Пользователь с таким email не найден'
            return render(request, 'accounts/password_reset_request.html', context)

        # генерим код
        code = f'{random.randint(0, 999999):06d}'

        # старые активные коды гасим
        PasswordResetCode.objects.filter(user=user, is_used=False).update(is_used=True)

        PasswordResetCode.objects.create(user=user, code=code)

        from django.core.mail import send_mail

        send_mail(
            'Код для сброса пароля',
            f'Ваш код для сброса пароля: {code}\nОн действует 15 минут.',
            getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@citytickets.local'),
            [user.email],
            fail_silently=False,
        )

        context['sent'] = True
        return render(request, 'accounts/password_reset_request.html', context)

    return render(request, 'accounts/password_reset_request.html', context)


# ---------- PASSWORD RESET (STEP 2) ----------

def password_reset_confirm(request):
    """
    Страница, где юзер вводит email + код + новый пароль.
    """
    context = {}

    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip()
        code = (request.POST.get('code') or '').strip()
        password = request.POST.get('password') or ''
        password2 = request.POST.get('password2') or ''

        context.update({'email': email, 'code': code})

        # === проверки полей ===
        if not email or not code or not password or not password2:
            context['error'] = 'Заполните все поля'
            return render(request, 'accounts/password_reset_confirm.html', context)

        if password != password2:
            context['error'] = 'Пароли не совпадают'
            return render(request, 'accounts/password_reset_confirm.html', context)

        if len(password) < 6:
            context['error'] = 'Пароль должен быть не короче 6 символов'
            return render(request, 'accounts/password_reset_confirm.html', context)

        # === пользователь ===
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            context['error'] = 'Пользователь с таким email не найден'
            return render(request, 'accounts/password_reset_confirm.html', context)

        # === код сброса ===
        try:
            reset = PasswordResetCode.objects.filter(
                user=user,
                code=code,
                is_used=False,
                created_at__gte=timezone.now() - timedelta(minutes=15),
            ).latest('created_at')
        except PasswordResetCode.DoesNotExist:
            context['error'] = 'Неверный или просроченный код'
            return render(request, 'accounts/password_reset_confirm.html', context)

        # === всё ок — меняем пароль ===
        user.set_password(password)
        user.save()

        reset.is_used = True
        reset.save()

        # вместо render -> редирект, чтобы не ловить CSRF 403 при логине
        messages.success(request, 'Пароль успешно изменён. Теперь можете войти.')
        return redirect('home')   # или на любую страницу, откуда удобно логиниться

    # GET-запрос — просто показываем форму
    return render(request, 'accounts/password_reset_confirm.html', context)


@login_required(login_url='home')
def profile_view(request):
    user = request.user

    VERIFIED_TTL_MIN = 15  # сколько минут после кода можно редактировать

    def is_verified():
        ts = request.session.get('profile_edit_verified_at')
        if not ts:
            return False
        try:
            verified_at = timezone.datetime.fromtimestamp(ts, tz=timezone.get_current_timezone())
        except Exception:
            return False
        return timezone.now() <= verified_at + timedelta(minutes=VERIFIED_TTL_MIN)

    edit_verified = is_verified()
    code_sent = request.session.get('profile_edit_code_sent', False)

    # GET: показываем форму (заблокированную, если не подтверждено)
    if request.method == 'GET':
        form = ProfileForm(instance=user)
        if not edit_verified:
            for f in form.fields.values():
                f.disabled = True
        return render(request, 'accounts/profile.html', {
            'form': form,
            'edit_verified': edit_verified,
            'code_sent': code_sent,
        })

    # POST: разбираем действие
    action = (request.POST.get('action') or '').strip()

    # 1) отправить код на email
    if action == 'send_code':
        if not user.email:
            messages.error(request, 'У вас не указан email.')
            return redirect('profile')

        code = f'{random.randint(0, 999999):06d}'

        # гасим старые коды
        ProfileEditCode.objects.filter(user=user, is_used=False).update(is_used=True)
        ProfileEditCode.objects.create(user=user, code=code)

        from django.core.mail import send_mail
        send_mail(
            'Код подтверждения для редактирования профиля',
            f'Ваш код: {code}\nОн действует {VERIFIED_TTL_MIN} минут.',
            getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@citytickets.local'),
            [user.email],
            fail_silently=False,
        )

        request.session['profile_edit_code_sent'] = True
        messages.success(request, 'Код отправлен на вашу почту.')
        return redirect('profile')

    # 2) подтвердить код
    if action == 'verify_code':
        code = (request.POST.get('code') or '').strip()

        if not code or len(code) != 6:
            messages.error(request, 'Введите 6-значный код.')
            return redirect('profile')

        try:
            _ = ProfileEditCode.objects.filter(
                user=user,
                code=code,
                is_used=False,
                created_at__gte=timezone.now() - timedelta(minutes=VERIFIED_TTL_MIN),
            ).latest('created_at')
        except ProfileEditCode.DoesNotExist:
            messages.error(request, 'Неверный или просроченный код.')
            return redirect('profile')

        _.is_used = True
        _.save(update_fields=['is_used'])

        request.session['profile_edit_verified_at'] = int(timezone.now().timestamp())
        messages.success(request, 'Подтверждено. Теперь можно редактировать профиль.')
        return redirect('profile')

    # 3) сохранить изменения профиля
    if action == 'save':
        if not edit_verified:
            messages.error(request, 'Сначала подтвердите редактирование кодом из письма.')
            return redirect('profile')

        form = ProfileForm(request.POST, instance=user)
        if form.is_valid():
            form.save()

            # после сохранения — снова закрываем редактирование
            request.session.pop('profile_edit_verified_at', None)
            request.session.pop('profile_edit_code_sent', None)

            return render(request, 'accounts/profile.html', {
                'form': ProfileForm(instance=user),
                'saved': True,
                'edit_verified': False,
                'code_sent': False,
            })

        return render(request, 'accounts/profile.html', {
            'form': form,
            'edit_verified': edit_verified,
            'code_sent': code_sent,
        })

    # если action неизвестный
    return redirect('profile')
