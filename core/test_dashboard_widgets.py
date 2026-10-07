import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import PreferenciaDashboard


class DashboardWidgetsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="widgets_a")
        self.other = User.objects.create_user(username="widgets_b")
        self.client.force_login(self.user)

    def test_guarda_y_recupera_orden_por_usuario(self):
        orden = ["activos", "patrimonio", "flujo"]
        response = self.client.post(
            reverse("guardar_dashboard_widgets"), json.dumps({"orden": orden}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PreferenciaDashboard.objects.get(user=self.user).orden_widgets, orden)
        self.assertFalse(PreferenciaDashboard.objects.filter(user=self.other).exists())
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard.context["widget_order"], orden)
        self.assertContains(dashboard, 'data-widget="activos"')

    def test_orden_invalido_no_se_guarda(self):
        for orden in (["activos", "activos"], ["ajeno"], [{"x": 1}], []):
            response = self.client.post(
                reverse("guardar_dashboard_widgets"), json.dumps({"orden": orden}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400)
        self.assertFalse(PreferenciaDashboard.objects.filter(user=self.user).exists())
