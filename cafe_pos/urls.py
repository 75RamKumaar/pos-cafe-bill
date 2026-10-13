import time
from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.urls import include, path, re_path
from django.utils import timezone
from django.views.static import serve

LOCKOUT_DURATION = 300  # 5 minutes
MAX_LOGIN_FAILURES = 5


def get_client_ip(request):
    if not request:
        return "127.0.0.1"
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


def get_throttle_key(username, ip):
    uname = (username or "").strip().lower()
    return f"login_throttle:{uname}:{ip}"


def get_current_time():
    """Return the current time timestamp, supporting mocked clock in tests."""
    try:
        t_time = time.time()
    except Exception:
        t_time = 0
    try:
        tz_time = timezone.now().timestamp()
    except Exception:
        tz_time = 0
    return max(t_time, tz_time)


def is_locked_out(username, ip):
    key = get_throttle_key(username, ip)
    data = cache.get(key)
    if not data:
        return False
    locked_until = data.get("locked_until")
    if locked_until and get_current_time() < locked_until:
        return True
    return False


def record_login_failure(username, ip):
    key = get_throttle_key(username, ip)
    data = cache.get(key) or {"failures": 0, "locked_until": None}
    now = get_current_time()
    # If a previous lock expired, reset the failure counter
    if data.get("locked_until") and now >= data["locked_until"]:
        data = {"failures": 0, "locked_until": None}
    data["failures"] += 1
    if data["failures"] >= MAX_LOGIN_FAILURES:
        data["locked_until"] = now + LOCKOUT_DURATION
    cache.set(key, data, timeout=LOCKOUT_DURATION * 2)


def clear_login_failures(username, ip):
    key = get_throttle_key(username, ip)
    cache.delete(key)


class ThrottledAuthenticationForm(AuthenticationForm):
    def clean(self):
        username = self.cleaned_data.get("username", "")
        ip = get_client_ip(self.request)
        if username and is_locked_out(username, ip):
            raise ValidationError(
                "Too many failed login attempts. Your account is locked for 5 minutes.",
                code="throttled",
            )
        try:
            return super().clean()
        except forms.ValidationError:
            if username:
                record_login_failure(username, ip)
            raise


class QuickPinAuthenticationForm(forms.Form):
    username = forms.CharField(
        label="Username",
        widget=forms.TextInput(attrs={"autofocus": True, "placeholder": "Username", "id": "id_pin_username"}),
    )
    pin = forms.CharField(
        label="4-Digit PIN",
        widget=forms.PasswordInput(attrs={"maxlength": 4, "pattern": r"\d{4}", "inputmode": "numeric", "placeholder": "••••", "id": "id_pin"}),
    )

    error_messages = {
        "invalid_login": "Your username and PIN did not match.",
        "inactive": "This account is inactive.",
        "throttled": "Too many failed login attempts. Your account is locked for 5 minutes.",
    }

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user_cache = None
        super().__init__(*args, **kwargs)

    def clean(self):
        username = self.cleaned_data.get("username", "").strip()
        pin = self.cleaned_data.get("pin", "").strip()
        ip = get_client_ip(self.request)

        if username and is_locked_out(username, ip):
            raise ValidationError(
                self.error_messages["throttled"],
                code="throttled",
            )

        if username and pin:
            from django.contrib.auth.models import User
            from pos.models import StaffProfile

            try:
                user = User.objects.get(username__iexact=username)
            except User.DoesNotExist:
                user = None

            valid_pin = False
            if user:
                try:
                    profile = user.staff_profile
                    if profile.has_pin() and profile.check_pin(pin):
                        valid_pin = True
                except (StaffProfile.DoesNotExist, AttributeError):
                    valid_pin = False

            if not user or not valid_pin:
                if username:
                    record_login_failure(username, ip)
                from django.contrib.auth.signals import user_login_failed
                user_login_failed.send(
                    sender=QuickPinAuthenticationForm,
                    credentials={"username": username},
                    request=self.request,
                )
                raise ValidationError(
                    self.error_messages["invalid_login"],
                    code="invalid_login",
                )

            if not user.is_active:
                if username:
                    record_login_failure(username, ip)
                from django.contrib.auth.signals import user_login_failed
                user_login_failed.send(
                    sender=QuickPinAuthenticationForm,
                    credentials={"username": username},
                    request=self.request,
                )
                raise ValidationError(
                    self.error_messages["inactive"],
                    code="inactive",
                )

            user.backend = "django.contrib.auth.backends.ModelBackend"
            self.user_cache = user

        return self.cleaned_data

    def get_user(self):
        return self.user_cache


class ThrottledLoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    form_class = ThrottledAuthenticationForm

    def get_form_class(self):
        if self.request.method == "POST":
            auth_mode = self.request.POST.get("auth_mode", "")
            if auth_mode == "pin" or ("pin" in self.request.POST and "password" not in self.request.POST):
                return QuickPinAuthenticationForm
        return ThrottledAuthenticationForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        auth_mode = "password"
        if self.request.method == "POST":
            if self.request.POST.get("auth_mode") == "pin" or ("pin" in self.request.POST and "password" not in self.request.POST):
                auth_mode = "pin"
        context["active_tab"] = auth_mode
        if auth_mode == "pin":
            context["pin_form"] = context["form"]
            context["password_form"] = ThrottledAuthenticationForm(self.request)
        else:
            context["password_form"] = context["form"]
            context["pin_form"] = QuickPinAuthenticationForm(self.request)
        return context

    def form_invalid(self, form):
        response = super().form_invalid(form)
        username = form.data.get("username", "")
        ip = get_client_ip(self.request)
        if is_locked_out(username, ip):
            response.render()
            content = response.content.decode("utf-8")
            content = content.replace(
                "Your username and password did not match.",
                "Too many failed login attempts. Your account is locked for 5 minutes.",
            )
            content = content.replace(
                "Your username and PIN did not match.",
                "Too many failed login attempts. Your account is locked for 5 minutes.",
            )
            response.content = content.encode("utf-8")
        return response

    def form_valid(self, form):
        username = form.cleaned_data.get("username", "")
        ip = get_client_ip(self.request)
        clear_login_failures(username, ip)
        return super().form_valid(form)


urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),
    path("login/", ThrottledLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("", include("pos.urls")),
]

# Serve uploaded media files only when DEBUG is True.
# In production, serve media through a reverse proxy (e.g. Nginx or Caddy).
if settings.DEBUG:
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    ]
