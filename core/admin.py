from django.contrib import admin
from .models import UserProfile, Periodo, AuditoriaChange


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'timezone', 'default_currency', 'dia_recordatorio']
    list_filter = ['timezone', 'default_currency', 'created_at']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('User', {'fields': ('user',)}),
        ('Preferences', {'fields': ('timezone', 'default_currency', 'dia_recordatorio')}),
        ('Default Settings', {'fields': ('bank_defecto_salario',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )


@admin.register(Periodo)
class PeriodoAdmin(admin.ModelAdmin):
    list_display = ['user', 'year', 'month', 'updated_at']
    list_filter = ['year', 'month', 'updated_at']
    search_fields = ['user__username']
    readonly_fields = ['updated_at']


@admin.register(AuditoriaChange)
class AuditoriaChangeAdmin(admin.ModelAdmin):
    list_display = ['user', 'change_type', 'content_type', 'object_id', 'timestamp']
    list_filter = ['change_type', 'content_type', 'timestamp']
    search_fields = ['user__username', 'content_type']
    readonly_fields = ['user', 'change_type', 'content_type', 'object_id', 'field_name', 'old_value', 'new_value', 'timestamp', 'ip_address']
    date_hierarchy = 'timestamp'
    
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
