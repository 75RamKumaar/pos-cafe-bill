from django.urls import include, path, re_path
from django.conf import settings
from django.views.static import serve
from django.contrib import admin
from django.contrib.auth import views as auth_views

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("", include("pos.urls")),
    # Serve uploaded media files for single-shop LAN installs.
    # Note: Dedicated web servers like nginx or Caddy are better at scale.
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]
