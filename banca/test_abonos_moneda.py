"""Abonos bancarios conservan la moneda original de la cuenta por cobrar."""

from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from banca.models import MovimientoBancario
from configuracion.models import Banco, Producto
from patrimonio.models import AbonoCuentaPorCobrar, CuentaPorCobrar, DetalleCuentaPorCobrar


class AbonosMonedaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("abono_usd")
        self.other = User.objects.create_user("abono_usd_other")
        banco = Banco.objects.create(nombre="Banco abonos")
        self.producto = Producto.objects.create(user=self.user, banco=banco, nombre="Cuenta USD", tipo="CUENTA")
        self.cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Cobro USD", moneda="USD")
        DetalleCuentaPorCobrar.objects.create(cuenta=self.cuenta, periodo=date(2026, 9, 1),
            fecha=date(2026, 9, 10), descripcion="Capital", monto=100)
        self.client.force_login(self.user)

    def movimiento(self, clave, *, clp="-45000", tasa="900", monto="-50"):
        return MovimientoBancario.objects.create(user=self.user, producto=self.producto,
            fecha=date(2026, 9, 10), descripcion="Abono", moneda="USD",
            monto=Decimal(monto), monto_clp=Decimal(clp),
            tipo_cambio_banco=Decimal(tasa) if tasa else None, hash_externo=clave, estado="FACTURADO")

    def asignar(self, movimiento, cuenta=None):
        return self.client.post(reverse("banca_asignar_abono_cuenta_cobrar", args=[movimiento.pk]),
                                {"cuenta_cobro": (cuenta or self.cuenta).pk})

    def test_usd_usa_importe_original_y_reintento_no_duplica(self):
        movimiento = self.movimiento("usd-con-tasa")
        self.assertEqual(self.asignar(movimiento).status_code, 302)
        self.assertEqual(self.asignar(movimiento).status_code, 302)
        abono = AbonoCuentaPorCobrar.objects.get(movimiento=movimiento)
        self.assertEqual(abono.monto, Decimal("50"))
        self.assertEqual(self.cuenta.saldo_pendiente, Decimal("50"))
        self.cuenta.activo_patrimonial.refresh_from_db()
        self.assertEqual(self.cuenta.activo_patrimonial.monto_original, Decimal("50"))

    def test_usd_sin_conversion_clp_puede_abonar_en_usd(self):
        movimiento = self.movimiento("usd-sin-tasa", clp="0", tasa=None)
        self.assertEqual(self.asignar(movimiento).status_code, 302)
        self.assertEqual(AbonoCuentaPorCobrar.objects.get(movimiento=movimiento).monto, Decimal("50"))

    def test_moneda_distinta_o_cuenta_ajena_no_modifican_movimiento(self):
        movimiento = self.movimiento("usd-invalido")
        clp = CuentaPorCobrar.objects.create(user=self.user, nombre="CLP", moneda="CLP")
        ajena = CuentaPorCobrar.objects.create(user=self.other, nombre="Ajena", moneda="USD")
        self.assertEqual(self.asignar(movimiento, clp).status_code, 302)
        self.assertEqual(self.asignar(movimiento, ajena).status_code, 404)
        movimiento.refresh_from_db()
        self.assertFalse(AbonoCuentaPorCobrar.objects.filter(movimiento=movimiento).exists())
        self.assertEqual(movimiento.naturaleza, "OPERACIONAL")

    def test_sin_importe_original_no_inventa_un_abono_usd(self):
        movimiento = self.movimiento("usd-sin-original", monto="0")
        self.assertEqual(self.asignar(movimiento).status_code, 302)
        self.assertFalse(AbonoCuentaPorCobrar.objects.filter(movimiento=movimiento).exists())

    def test_reparacion_historica_tiene_simulacion_aislamiento_e_idempotencia(self):
        movimiento = self.movimiento("usd-historico")
        incorrecto = AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, movimiento=movimiento,
            fecha=movimiento.fecha, monto=45000)
        ajena = CuentaPorCobrar.objects.create(user=self.other, nombre="Ajena histórica", moneda="USD")
        producto_ajeno = Producto.objects.create(user=self.other, banco=self.producto.banco, nombre="Ajena", tipo="CUENTA")
        movimiento_ajeno = MovimientoBancario.objects.create(user=self.other, producto=producto_ajeno,
            fecha=movimiento.fecha, descripcion="Otro abono", moneda="USD", monto=-100,
            monto_clp=-90000, tipo_cambio_banco=900, hash_externo="ajeno-historico")
        abono_ajeno = AbonoCuentaPorCobrar.objects.create(cuenta=ajena, movimiento=movimiento_ajeno,
            fecha=movimiento.fecha, monto=90000)
        parcial_mov = self.movimiento("usd-parcial")
        parcial = AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, movimiento=parcial_mov,
            fecha=parcial_mov.fecha, monto=20)
        call_command("corregir_abonos_usd", user_id=self.user.pk, stdout=StringIO())
        incorrecto.refresh_from_db()
        self.assertEqual(incorrecto.monto, Decimal("45000"))
        for _ in range(2):
            call_command("corregir_abonos_usd", apply=True, user_id=self.user.pk, stdout=StringIO())
        incorrecto.refresh_from_db(); abono_ajeno.refresh_from_db(); parcial.refresh_from_db()
        self.assertEqual(incorrecto.monto, Decimal("50"))
        self.assertEqual(abono_ajeno.monto, Decimal("90000"))
        self.assertEqual(parcial.monto, Decimal("20"))
        self.cuenta.activo_patrimonial.refresh_from_db()
        self.assertEqual(self.cuenta.activo_patrimonial.monto_original, Decimal("30"))
