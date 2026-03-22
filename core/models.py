from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class UserProfile(models.Model):
    """Extended user profile for finanzas app"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='finanzas_profile')
    timezone = models.CharField(max_length=50, default='America/Santiago')
    default_currency = models.CharField(
        max_length=3,
        default='CLP',
        choices=[('CLP', 'Chilean Peso'), ('USD', 'US Dollar'), ('UF', 'Unidad de Fomento')]
    )
    bank_defecto_salario = models.ForeignKey(
        'configuracion.Banco',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='usuarios_con_salario_defecto'
    )
    dia_recordatorio = models.IntegerField(default=1, help_text="Día del mes para recordatorio de alimentación")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'

    def __str__(self):
        return f"Profile of {self.user.username}"


class Periodo(models.Model):
    """Tracks the active year/month for data entry"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='periodo_activo')
    year = models.IntegerField()
    month = models.IntegerField(choices=[(i, f'Month {i}') for i in range(1, 13)])
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Active Period'
        verbose_name_plural = 'Active Periods'

    def __str__(self):
        return f"{self.year}-{self.month:02d}"


class AuditoriaChange(models.Model):
    """Audit trail for all data changes"""
    CHANGE_TYPES = [
        ('CREATE', 'Created'),
        ('UPDATE', 'Updated'),
        ('DELETE', 'Deleted'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='cambios_auditados')
    change_type = models.CharField(max_length=10, choices=CHANGE_TYPES)
    content_type = models.CharField(max_length=100, help_text="Model name that was changed")
    object_id = models.IntegerField(help_text="ID of the changed object")
    field_name = models.CharField(max_length=100, blank=True, help_text="Specific field changed")
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.CharField(max_length=45, blank=True)

    class Meta:
        verbose_name = 'Audit Change'
        verbose_name_plural = 'Audit Changes'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', '-timestamp']),
            models.Index(fields=['content_type', 'object_id']),
        ]

    def __str__(self):
        return f"{self.change_type} {self.content_type}#{self.object_id} by {self.user}"
