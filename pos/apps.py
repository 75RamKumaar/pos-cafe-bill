from django.apps import AppConfig
from django.contrib.auth.signals import user_logged_in, user_login_failed, user_logged_out


def handle_user_logged_in(sender, request, user, **kwargs):
    from pos.models import AuditLog
    from django.utils import translation

    if hasattr(user, "staff_profile") and user.staff_profile.language:
        lang = user.staff_profile.language
        if request and hasattr(request, "session"):
            request.session["_language"] = lang
        translation.activate(lang)

    AuditLog.objects.create(
        actor=user,
        action="LOGIN",
        model_name="User",
        object_repr=user.username,
        details={"status": [None, "SUCCESS"], "username": [None, user.username]},
    )


def handle_user_login_failed(sender, credentials, request, **kwargs):
    from pos.models import AuditLog

    username = credentials.get("username", "") if credentials else ""
    if not username and request and hasattr(request, "POST"):
        username = request.POST.get("username", "")

    # Never store passwords or PINs!
    AuditLog.objects.create(
        actor=None,
        action="LOGIN_FAILED",
        model_name="User",
        object_repr=username or "Unknown",
        details={"status": [None, "FAILED"], "username": [None, username]},
    )


def handle_user_logged_out(sender, request, user, **kwargs):
    from pos.models import AuditLog

    username = user.username if user else "Anonymous"
    AuditLog.objects.create(
        actor=user if user and user.is_authenticated else None,
        action="LOGOUT",
        model_name="User",
        object_repr=username,
        details={"status": ["ACTIVE", "LOGGED_OUT"], "username": [username, None]},
    )


class PosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pos"

    def ready(self):
        user_logged_in.connect(handle_user_logged_in, dispatch_uid="pos_audit_user_logged_in")
        user_login_failed.connect(handle_user_login_failed, dispatch_uid="pos_audit_user_login_failed")
        user_logged_out.connect(handle_user_logged_out, dispatch_uid="pos_audit_user_logged_out")
