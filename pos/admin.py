from django.contrib import admin
from .models import AuditLog, Wastage


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "actor", "action", "model_name", "object_repr")
    list_filter = ("action", "model_name", "timestamp")
    search_fields = ("actor__username", "model_name", "object_repr", "action")
    readonly_fields = ("actor", "action", "model_name", "object_repr", "timestamp", "details")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Wastage)
class WastageAdmin(admin.ModelAdmin):
    list_display = ("id", "item_name", "item_type", "quantity", "cost", "reason", "recorded_by", "created_at")
    list_filter = ("reason", "created_at")
    search_fields = ("product__name", "stock_item__name", "note")
    readonly_fields = ("cost", "created_at")

    def has_delete_permission(self, request, obj=None):
        return False

