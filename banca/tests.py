"""
Tests de regresión de banca + bank_sync.

Objetivo: blindar lo que YA funciona (huella estable, registro de connectors, cifrado de
credenciales, ingesta idempotente de movimientos/saldos, señal de pasivos) y lo agregado en
la Fase 0 (fuentes de activos: AssetConnector, ingesta de posiciones, ruteo de run_sync).

Todo es hermético: sin red ni navegador. Los connectors de prueba devuelven datos canónicos.
"""

import os
import tempfile

# El cifrado Fernet y el storage de sesión leen el entorno; fijamos valores de test ANTES
# de importar los servicios que los usan.
from cryptography.fernet import Fernet

os.environ.setdefault("BANCA_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault(
    "BANK_SYNC_SESSION_DIR", tempfile.mkdtemp(prefix="bank_sync_test_")
)

from datetime import date  # noqa: E402
from decimal import Decimal  # noqa: E402

from django.contrib.auth.models import User  # noqa: E402
from django.test import TestCase  # noqa: E402

from bank_sync.connectors.base import AssetConnector, BankConnector  # noqa: E402
from bank_sync.exceptions import ConnectorNotFound  # noqa: E402
from bank_sync.models import (  # noqa: E402
    DeudaResumen,
    MovimientoNormalizado,
    PosicionNormalizada,
    SaldoHipotecario,
    SaldoTarjeta,
)
from bank_sync.registry import (  # noqa: E402
    available_connectors,
    connector_tipo,
    connectors_by_tipo,
    es_fuente_activos,
    get_connector,
    register_connector,
)


# --------------------------------------------------------------------------- connectors dummy
class _DummyBank(BankConnector):
    """Banco de prueba: devuelve datos canónicos sin abrir navegador."""

    nombre = "test_bank"

    def login(self, rut, clave):
        self.logged = (rut, clave)

    def logout(self):
        self.logged = None

    def get_deudas(self):
        return DeudaResumen(
            tarjetas=[
                SaldoTarjeta(
                    tarjeta_id="1234",
                    nombre="TDC Test",
                    deuda_facturada_clp=Decimal("100000"),
                    deuda_no_facturada_clp=Decimal("50000"),
                    cupo_total_clp=Decimal("1000000"),
                    cupo_disponible_clp=Decimal("850000"),
                )
            ],
            creditos=[
                SaldoHipotecario(
                    nombre="Hipotecario Test",
                    moneda="UF",
                    saldo_insoluto_uf=Decimal("1000"),
                    proxima_cuota_uf=Decimal("10"),
                )
            ],
        )

    def get_movimientos_no_facturados(self):
        return []

    def get_estado_cuenta(self, periodo=None):
        return []


class _DummyAsset(AssetConnector):
    """Fuente de activos de prueba: devuelve posiciones canónicas."""

    nombre = "test_asset"

    def authenticate(self, **creds):
        self.creds = creds

    def get_posiciones(self):
        return [
            PosicionNormalizada(
                simbolo="BTC",
                nombre="Bitcoin",
                tipo_activo="CRIPTO",
                fuente="test_asset",
                cantidad=Decimal("0.5"),
                precio_unitario=Decimal("60000"),
                moneda="USD",
            ),
            PosicionNormalizada(
                simbolo="CLP",
                nombre="Caja CLP",
                tipo_activo="EFECTIVO",
                fuente="test_asset",
                valor_clp=Decimal("1000000"),
            ),
        ]


register_connector(
    "test_bank", lambda: _DummyBank, tipo="banco", capabilities=("deudas",)
)
register_connector(
    "test_asset", lambda: _DummyAsset, tipo="cripto", capabilities=("posiciones",)
)


# --------------------------------------------------------------------------- bank_sync core
class HashEstableTest(TestCase):
    def _mov(self, estado, periodo=None):
        return MovimientoNormalizado(
            fecha=date(2026, 6, 15),
            descripcion="APPLE.COM/BILL",
            monto=Decimal("9390"),
            moneda="CLP",
            estado=estado,
            tarjeta_id="6691",
            periodo_factura=periodo,
        )

    def test_hash_no_cambia_al_facturarse(self):
        """La huella debe ser igual entre NO_FACTURADO y FACTURADO -> no se duplica."""
        no_fact = self._mov("NO_FACTURADO")
        facturado = self._mov("FACTURADO", periodo=date(2026, 7, 25))
        self.assertEqual(no_fact.hash_externo(), facturado.hash_externo())

    def test_hash_distingue_monto(self):
        a = self._mov("NO_FACTURADO")
        b = self._mov("NO_FACTURADO")
        b.monto = Decimal("9379")
        self.assertNotEqual(a.hash_externo(), b.hash_externo())

    def test_hash_distingue_tarjeta_y_cuotas(self):
        base = self._mov("NO_FACTURADO")
        otra_tarjeta = self._mov("NO_FACTURADO")
        otra_tarjeta.tarjeta_id = "0000"
        self.assertNotEqual(base.hash_externo(), otra_tarjeta.hash_externo())

        cuota = self._mov("NO_FACTURADO")
        cuota.cuota_actual, cuota.cuota_total = 1, 12
        self.assertNotEqual(base.hash_externo(), cuota.hash_externo())


class RegistryTest(TestCase):
    def test_santander_registrado_como_banco(self):
        self.assertIn("santander", available_connectors())
        self.assertEqual(connector_tipo("santander"), "banco")
        self.assertFalse(es_fuente_activos("santander"))

    def test_tipos_de_fuentes_dummy(self):
        self.assertEqual(connector_tipo("test_bank"), "banco")
        self.assertEqual(connector_tipo("test_asset"), "cripto")
        self.assertTrue(es_fuente_activos("test_asset"))
        self.assertIn("test_asset", connectors_by_tipo("cripto"))
        self.assertNotIn("test_asset", connectors_by_tipo("banco"))

    def test_connector_desconocido_falla(self):
        with self.assertRaises(ConnectorNotFound):
            get_connector("no_existe")
        with self.assertRaises(ConnectorNotFound):
            connector_tipo("no_existe")

    def test_get_connector_instancia_clase_correcta(self):
        self.assertIsInstance(get_connector("test_bank"), _DummyBank)
        self.assertIsInstance(get_connector("test_asset"), _DummyAsset)


class PosicionNormalizadaTest(TestCase):
    def test_clave_estable_por_fuente_y_simbolo(self):
        p = PosicionNormalizada(
            simbolo="BTC ", nombre="Bitcoin", tipo_activo="CRIPTO", fuente="binance"
        )
        self.assertEqual(p.clave(), "binance:btc")


# --------------------------------------------------------------------------- credenciales / cifrado
class CredencialCryptoTest(TestCase):
    def setUp(self):
        from configuracion.models import Banco

        self.user = User.objects.create(username="cred_test")
        self.banco = Banco.objects.create(nombre="Banco Test")

    def _cred(self, **extra):
        from banca.models import CredencialBanco

        return CredencialBanco.objects.create(user=self.user, banco=self.banco, **extra)

    def test_roundtrip_clave_y_api_keys(self):
        cred = self._cred(conector="test_bank")
        cred.set_clave("secreta-123")
        cred.set_api_key("KEY-abc")
        cred.set_api_secret("SECRET-xyz")
        cred.save()

        cred.refresh_from_db()
        self.assertEqual(cred.get_clave(), "secreta-123")
        self.assertEqual(cred.get_api_key(), "KEY-abc")
        self.assertEqual(cred.get_api_secret(), "SECRET-xyz")

    def test_secretos_no_en_texto_plano(self):
        cred = self._cred(conector="test_bank")
        cred.set_clave("no-plana")
        cred.save()
        self.assertNotIn(b"no-plana", bytes(cred.clave_cifrada))

    def test_get_secreto_vacio_no_falla(self):
        """Una fuente por dirección pública no tiene clave/secreto -> devuelve ''."""
        cred = self._cred(conector="test_asset", tipo_fuente="cripto")
        self.assertEqual(cred.get_clave(), "")
        self.assertEqual(cred.get_api_key(), "")


# --------------------------------------------------------------------------- ingesta de bancos
class IngestaMovimientosTest(TestCase):
    def setUp(self):
        from configuracion.models import Banco

        self.user = User.objects.create(username="ing_test")
        self.banco = Banco.objects.create(nombre="Banco Ingesta")

    def _mov(self, estado, periodo=None):
        return MovimientoNormalizado(
            fecha=date(2026, 6, 1),
            descripcion="SUPERMERCADO XYZ",
            monto=Decimal("25000"),
            moneda="CLP",
            estado=estado,
            tarjeta_id="1234",
            periodo_factura=periodo,
        )

    def test_inserta_y_no_duplica(self):
        from banca.models import MovimientoBancario
        from banca.services import ingesta

        r1 = ingesta.ingestar_movimientos(
            self.user, self.banco, [self._mov("NO_FACTURADO")]
        )
        self.assertEqual(r1["nuevos"], 1)
        # segunda ingesta de la MISMA compra no crea otra fila
        r2 = ingesta.ingestar_movimientos(
            self.user, self.banco, [self._mov("NO_FACTURADO")]
        )
        self.assertEqual(r2["nuevos"], 0)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 1)

    def test_transicion_no_facturado_a_facturado(self):
        from banca.models import MovimientoBancario
        from banca.services import ingesta

        ingesta.ingestar_movimientos(self.user, self.banco, [self._mov("NO_FACTURADO")])
        res = ingesta.ingestar_movimientos(
            self.user, self.banco, [self._mov("FACTURADO", periodo=date(2026, 7, 25))]
        )
        self.assertEqual(res["nuevos"], 0)
        self.assertEqual(res["actualizados"], 1)
        mov = MovimientoBancario.objects.get(user=self.user)
        self.assertEqual(mov.estado, "FACTURADO")
        self.assertEqual(mov.periodo_factura, date(2026, 7, 25))


class IngestaSaldosYSignalTest(TestCase):
    def setUp(self):
        from configuracion.models import Banco, TipoCambio

        self.user = User.objects.create(username="saldo_test")
        self.banco = Banco.objects.create(nombre="Banco Saldos")
        TipoCambio.objects.create(
            fecha=date.today(), fuente="test", uf=Decimal("39000"), dolar=Decimal("950")
        )

    def test_snapshot_convierte_uf_y_dispara_pasivo(self):
        from banca.models import SaldoSnapshot
        from banca.services import ingesta
        from patrimonio.models import Pasivo

        deuda = DeudaResumen(
            tarjetas=[
                SaldoTarjeta(
                    tarjeta_id="1234",
                    nombre="TDC",
                    deuda_facturada_clp=Decimal("100000"),
                    deuda_no_facturada_clp=Decimal("50000"),
                )
            ],
            creditos=[
                SaldoHipotecario(
                    nombre="Hipotecario",
                    moneda="UF",
                    saldo_insoluto_uf=Decimal("1000"),
                )
            ],
        )
        n = ingesta.ingestar_saldos(self.user, self.banco, deuda)
        self.assertEqual(n, 2)

        # hipotecario: 1000 UF * 39000 = 39.000.000 CLP
        snap_credito = SaldoSnapshot.objects.get(producto__tipo="CREDITO_HIPOTECARIO")
        self.assertEqual(snap_credito.saldo_insoluto, Decimal("39000000"))

        # la señal debe crear el Pasivo de la TDC con la deuda total (facturada + no facturada)
        pasivo_tdc = Pasivo.objects.get(producto__tipo="TDC")
        self.assertEqual(pasivo_tdc.monto_clp, Decimal("150000"))


# --------------------------------------------------------------------------- ingesta de posiciones (Fase 0)
class IngestaPosicionesTest(TestCase):
    def setUp(self):
        from configuracion.models import TipoCambio

        self.user = User.objects.create(username="pos_test")
        TipoCambio.objects.create(
            fecha=date.today(), fuente="test", uf=Decimal("39000"), dolar=Decimal("950")
        )

    def test_valoriza_ambas_monedas(self):
        from banca.services import ingesta_posiciones
        from patrimonio.models import Activo

        posiciones = [
            PosicionNormalizada(
                simbolo="BTC",
                nombre="Bitcoin",
                tipo_activo="CRIPTO",
                fuente="x",
                cantidad=Decimal("0.5"),
                precio_unitario=Decimal("60000"),
                moneda="USD",
            ),
            PosicionNormalizada(
                simbolo="CLP",
                nombre="Caja CLP",
                tipo_activo="EFECTIVO",
                fuente="x",
                valor_clp=Decimal("1000000"),
            ),
        ]
        res = ingesta_posiciones.ingestar_posiciones(self.user, posiciones)
        self.assertEqual(res["nuevos"], 2)

        btc = Activo.objects.get(user=self.user, nombre="Bitcoin")
        self.assertEqual(btc.monto_usd, Decimal("30000.00"))  # 0.5 * 60000
        self.assertEqual(btc.monto_clp, Decimal("28500000.00"))  # * 950

        caja = Activo.objects.get(user=self.user, nombre="Caja CLP")
        self.assertEqual(caja.monto_usd, Decimal("1052.63"))  # 1.000.000 / 950

    def test_idempotente_no_duplica(self):
        from banca.services import ingesta_posiciones
        from patrimonio.models import Activo, HistorialActivo

        pos = [
            PosicionNormalizada(
                simbolo="ETH",
                nombre="Ethereum",
                tipo_activo="CRIPTO",
                fuente="x",
                valor_usd=Decimal("2000"),
            )
        ]
        ingesta_posiciones.ingestar_posiciones(self.user, pos)
        res2 = ingesta_posiciones.ingestar_posiciones(self.user, pos)
        self.assertEqual(res2["nuevos"], 0)
        self.assertEqual(res2["actualizados"], 1)
        self.assertEqual(Activo.objects.filter(user=self.user).count(), 1)
        # un único snapshot por (activo, fecha)
        self.assertEqual(
            HistorialActivo.objects.filter(activo__user=self.user).count(), 1
        )

    def test_tipo_desconocido_cae_en_otro(self):
        from banca.services import ingesta_posiciones
        from patrimonio.models import Activo

        pos = [
            PosicionNormalizada(
                simbolo="???",
                nombre="Raro",
                tipo_activo="NO_EXISTE",
                fuente="x",
                valor_clp=Decimal("1"),
            )
        ]
        ingesta_posiciones.ingestar_posiciones(self.user, pos)
        self.assertTrue(Activo.objects.filter(user=self.user, tipo="OTRO").exists())


# --------------------------------------------------------------------------- ruteo de run_sync
class RunSyncRuteoTest(TestCase):
    def setUp(self):
        from configuracion.models import Banco, TipoCambio

        self.user = User.objects.create(username="sync_test")
        self.banco = Banco.objects.create(nombre="Banco Sync")
        TipoCambio.objects.create(
            fecha=date.today(), fuente="test", uf=Decimal("39000"), dolar=Decimal("950")
        )

    def test_ruta_banco_genera_snapshot(self):
        from banca.models import CredencialBanco, SaldoSnapshot
        from banca.services.sync import run_sync

        cred = CredencialBanco.objects.create(
            user=self.user,
            banco=self.banco,
            conector="test_bank",
            tipo_fuente="banco",
            rut="1-9",
        )
        cred.set_clave("x")
        cred.save()

        log = run_sync(cred, modo="deudas", notificar=False)
        self.assertTrue(log.exito, log.mensaje)
        self.assertTrue(SaldoSnapshot.objects.filter(user=self.user).exists())

    def test_ruta_activo_genera_activos(self):
        from banca.models import CredencialBanco
        from banca.services.sync import run_sync
        from patrimonio.models import Activo

        cred = CredencialBanco.objects.create(
            user=self.user,
            banco=self.banco,
            conector="test_asset",
            tipo_fuente="cripto",
        )
        cred.set_api_key("k")
        cred.set_api_secret("s")
        cred.save()

        log = run_sync(cred, modo="full")
        self.assertTrue(log.exito, log.mensaje)
        self.assertEqual(log.nuevos, 2)
        self.assertEqual(Activo.objects.filter(user=self.user).count(), 2)


# --------------------------------------------------------------------------- semántica de modelos
class MovimientoSemanticaTest(TestCase):
    def setUp(self):
        from configuracion.models import Banco, Producto

        self.user = User.objects.create(username="sem_test")
        banco = Banco.objects.create(nombre="Banco Sem")
        self.producto = Producto.objects.create(banco=banco, nombre="TDC", tipo="TDC")

    def _mov(self, **kw):
        from banca.models import MovimientoBancario

        defaults = dict(
            user=self.user,
            producto=self.producto,
            fecha=date(2026, 6, 1),
            descripcion="X",
            monto=Decimal("1000"),
            monto_clp=Decimal("1000"),
            hash_externo=kw.pop("hash_externo", "h1"),
        )
        defaults.update(kw)
        return MovimientoBancario.objects.create(**defaults)

    def test_es_cargo_por_signo(self):
        cargo = self._mov(monto_clp=Decimal("1000"), hash_externo="a")
        abono = self._mov(monto_clp=Decimal("-1000"), hash_externo="b")
        self.assertTrue(cargo.es_cargo)
        self.assertFalse(abono.es_cargo)

    def test_contabiliza_solo_operacional_no_por_revisar(self):
        op = self._mov(naturaleza="OPERACIONAL", por_revisar=False, hash_externo="c")
        transfer = self._mov(naturaleza="TRANSFERENCIA", hash_externo="d")
        revisar = self._mov(
            naturaleza="OPERACIONAL", por_revisar=True, hash_externo="e"
        )
        self.assertTrue(op.contabiliza)
        self.assertFalse(transfer.contabiliza)
        self.assertFalse(revisar.contabiliza)

    def test_es_gasto_ocio(self):
        ocio = self._mov(
            naturaleza="OPERACIONAL",
            esencialidad="OCIO",
            monto_clp=Decimal("5000"),
            hash_externo="f",
        )
        fijo = self._mov(
            naturaleza="OPERACIONAL",
            esencialidad="FIJO",
            monto_clp=Decimal("5000"),
            hash_externo="g",
        )
        self.assertTrue(ocio.es_gasto_ocio)
        self.assertFalse(fijo.es_gasto_ocio)


# --------------------------------------------------------------------------- HTTP fake (cripto)
class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class _FakeHTTP:
    """Session HTTP falsa: rutea por (url, params) con un handler; registra las llamadas."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(
            {"url": url, "params": params or {}, "headers": headers or {}}
        )
        payload, status = self._handler(url, params or {})
        return _Resp(payload, status)

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json or {}, "headers": headers or {}})
        payload, status = self._handler(url, json or {})
        return _Resp(payload, status)


class BinanceConnectorTest(TestCase):
    def test_requiere_credenciales(self):
        from bank_sync.connectors.binance import BinanceConnector
        from bank_sync.exceptions import AuthError

        with self.assertRaises(AuthError):
            BinanceConnector().authenticate(api_key="", api_secret="")

    def test_parsea_balances_y_valoriza_en_usd(self):
        from bank_sync.connectors.binance import BinanceConnector

        def handler(url, params):
            if "/api/v3/account" in url:
                return (
                    {
                        "balances": [
                            {"asset": "BTC", "free": "0.5", "locked": "0"},
                            {"asset": "USDT", "free": "100", "locked": "0"},
                            {
                                "asset": "ETH",
                                "free": "0",
                                "locked": "0",
                            },  # se ignora (0)
                        ]
                    },
                    200,
                )
            if "/api/v3/ticker/price" in url:
                return ([{"symbol": "BTCUSDT", "price": "60000"}], 200)
            raise AssertionError(url)

        fake = _FakeHTTP(handler)
        c = BinanceConnector(session=fake)
        c.authenticate(api_key="K", api_secret="S")
        pos = {p.simbolo: p for p in c.get_posiciones()}

        self.assertEqual(set(pos), {"BTC", "USDT"})
        self.assertEqual(pos["BTC"].cantidad, Decimal("0.5"))
        self.assertEqual(pos["BTC"].precio_unitario, Decimal("60000"))
        self.assertEqual(pos["USDT"].precio_unitario, Decimal("1"))  # stablecoin
        # la firma HMAC y la API key deben viajar en la request de cuenta
        cuenta = next(x for x in fake.calls if "/api/v3/account" in x["url"])
        self.assertIn("signature=", cuenta["url"])
        self.assertEqual(cuenta["headers"].get("X-MBX-APIKEY"), "K")

    def test_earn_ld_prefijo_y_filtro_de_polvo(self):
        """LDBTC (Binance Earn) se valoriza como BTC; el polvo (< 1 USD) se descarta."""
        from bank_sync.connectors.binance import BinanceConnector

        def handler(url, params):
            if "/api/v3/account" in url:
                return (
                    {
                        "balances": [
                            {
                                "asset": "LDBTC",
                                "free": "0.001",
                                "locked": "0",
                            },  # Earn -> BTC
                            {
                                "asset": "USDT",
                                "free": "0.005",
                                "locked": "0",
                            },  # polvo < 1 USD
                            {
                                "asset": "SHIB",
                                "free": "10",
                                "locked": "0",
                            },  # sin precio -> fuera
                        ]
                    },
                    200,
                )
            if "/api/v3/ticker/price" in url:
                return ([{"symbol": "BTCUSDT", "price": "60000"}], 200)
            raise AssertionError(url)

        c = BinanceConnector(session=_FakeHTTP(handler))
        c.authenticate(api_key="K", api_secret="S")
        pos = c.get_posiciones()

        # solo BTC Earn sobrevive: 0.001 * 60000 = 60 USD
        self.assertEqual(len(pos), 1)
        self.assertEqual(pos[0].simbolo, "BTC")
        self.assertIn("Earn", pos[0].nombre)
        self.assertEqual(pos[0].precio_unitario, Decimal("60000"))


class BudaConnectorTest(TestCase):
    def test_parsea_balances_y_ticker_clp(self):
        from bank_sync.connectors.buda import BudaConnector

        def handler(url, params):
            if url.endswith("/balances"):
                return (
                    {
                        "balances": [
                            {"id": "BTC", "amount": ["0.1", "BTC"]},
                            {"id": "CLP", "amount": ["50000", "CLP"]},
                        ]
                    },
                    200,
                )
            if "/markets/btc-clp/ticker" in url:
                return ({"ticker": {"last_price": ["60000000", "CLP"]}}, 200)
            raise AssertionError(url)

        fake = _FakeHTTP(handler)
        c = BudaConnector(session=fake)
        c.authenticate(api_key="K", api_secret="S")
        pos = {p.simbolo: p for p in c.get_posiciones()}

        self.assertEqual(pos["BTC"].cantidad, Decimal("0.1"))
        self.assertEqual(pos["BTC"].precio_unitario, Decimal("60000000"))
        self.assertEqual(pos["BTC"].moneda, "CLP")
        self.assertEqual(pos["CLP"].tipo_activo, "EFECTIVO")
        self.assertEqual(pos["CLP"].valor_clp, Decimal("50000"))
        # firma HMAC-SHA384 en headers
        bal = next(x for x in fake.calls if x["url"].endswith("/balances"))
        self.assertEqual(bal["headers"].get("X-SBTC-APIKEY"), "K")
        self.assertIn("X-SBTC-SIGNATURE", bal["headers"])


class CryptoMktConnectorTest(TestCase):
    def test_parsea_balance_y_ticker_clp(self):
        from bank_sync.connectors.cryptomkt import CryptoMktConnector

        def handler(url, params):
            if "/spot/balance" in url:
                return (
                    [
                        {"currency": "ETH", "available": "2", "reserved": "0"},
                        {"currency": "CLP", "available": "1000", "reserved": "0"},
                    ],
                    200,
                )
            if "/public/ticker/ETHCLP" in url:
                return ({"last": "2500000"}, 200)
            raise AssertionError(url)

        fake = _FakeHTTP(handler)
        c = CryptoMktConnector(session=fake)
        c.authenticate(api_key="K", api_secret="S")
        pos = {p.simbolo: p for p in c.get_posiciones()}

        self.assertEqual(pos["ETH"].cantidad, Decimal("2"))
        self.assertEqual(pos["ETH"].precio_unitario, Decimal("2500000"))
        self.assertEqual(pos["CLP"].valor_clp, Decimal("1000"))
        bal = next(x for x in fake.calls if "/spot/balance" in x["url"])
        self.assertTrue(bal["headers"].get("Authorization", "").startswith("Basic "))


class BitcoinWalletConnectorTest(TestCase):
    def test_requiere_direccion(self):
        from bank_sync.connectors.bitcoin_wallet import BitcoinWalletConnector
        from bank_sync.exceptions import AuthError

        with self.assertRaises(AuthError):
            BitcoinWalletConnector().authenticate(identificador_publico="")

    def test_saldo_publico_valorizado_en_usd(self):
        from bank_sync.connectors.bitcoin_wallet import BitcoinWalletConnector

        def handler(url, params):
            if "/balance" in url:
                # 0.2 BTC = 20.000.000 satoshis
                return ({"addr1": {"final_balance": 20000000}}, 200)
            if "/api/v3/ticker/price" in url:
                return ({"price": "60000"}, 200)  # precio_usd con symbol -> dict
            raise AssertionError(url)

        fake = _FakeHTTP(handler)
        c = BitcoinWalletConnector(session=fake)
        c.authenticate(identificador_publico="addr1")
        pos = c.get_posiciones()

        self.assertEqual(len(pos), 1)
        self.assertEqual(pos[0].simbolo, "BTC")
        self.assertEqual(pos[0].cantidad, Decimal("0.2"))
        self.assertEqual(pos[0].precio_unitario, Decimal("60000"))


class FintualConnectorTest(TestCase):
    def test_requiere_email_y_password(self):
        from bank_sync.connectors.fintual import FintualConnector
        from bank_sync.exceptions import AuthError

        with self.assertRaises(AuthError):
            FintualConnector(session=_FakeHTTP(lambda u, p: ({}, 200))).authenticate(
                api_key="", api_secret=""
            )

    def test_token_y_metas_a_posiciones(self):
        from bank_sync.connectors.fintual import FintualConnector

        def handler(url, data):
            if "/access_tokens" in url:
                return ({"data": {"attributes": {"token": "TOK123"}}}, 200)
            if "/goals" in url:
                return (
                    {
                        "data": [
                            {
                                "id": "1",
                                "attributes": {
                                    "name": "Risky Norris",
                                    "nav": "1500000",
                                },
                            },
                            {
                                "id": "2",
                                "attributes": {
                                    "name": "Conservative Clooney",
                                    "nav": "0",
                                },
                            },
                        ]
                    },
                    200,
                )
            raise AssertionError(url)

        fake = _FakeHTTP(handler)
        c = FintualConnector(session=fake)
        c.authenticate(api_key="yo@mail.com", api_secret="secreta")
        self.assertEqual(c.token, "TOK123")

        pos = c.get_posiciones()
        # solo la meta con nav > 0 sobrevive
        self.assertEqual(len(pos), 1)
        self.assertIn("Risky Norris", pos[0].nombre)
        self.assertEqual(pos[0].tipo_activo, "FONDO_MUTUO")
        self.assertEqual(pos[0].valor_clp, Decimal("1500000"))
        # el token viaja en la consulta de metas
        goals = next(x for x in fake.calls if "/goals" in x["url"])
        self.assertEqual(goals["params"].get("user_token"), "TOK123")


# --------------------------------------------------------------------------- form de credenciales
class CredencialFormTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="form_test")

    def _guardar(self, data):
        from banca.forms import CredencialBancoForm

        form = CredencialBancoForm(data=data)
        if not form.is_valid():
            return form, None
        cred = form.save(commit=False)
        cred.user = self.user
        cred.save()
        return form, cred

    def test_alta_exchange_cifra_keys_y_autocrea_institucion(self):
        from configuracion.models import Banco

        form, cred = self._guardar(
            {
                "conector": "binance",
                "api_key": "KEY",
                "api_secret": "SEC",
                "activo": "on",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(cred.tipo_fuente, "cripto")
        self.assertEqual(cred.get_api_key(), "KEY")
        self.assertEqual(cred.get_api_secret(), "SEC")
        # institución auto-creada con nombre legible
        self.assertTrue(Banco.objects.filter(nombre="Binance").exists())
        self.assertEqual(cred.banco.nombre, "Binance")

    def test_alta_wallet_solo_direccion(self):
        form, cred = self._guardar(
            {
                "conector": "bitcoin_wallet",
                "identificador_publico": "xpub123",
                "activo": "on",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(cred.tipo_fuente, "cripto")
        self.assertEqual(cred.identificador_publico, "xpub123")

    def test_exchange_sin_keys_es_invalido(self):
        form, cred = self._guardar({"conector": "binance", "activo": "on"})
        self.assertFalse(form.is_valid())
        self.assertIsNone(cred)

    def test_banco_requiere_rut_y_clave(self):
        form, cred = self._guardar({"conector": "santander", "activo": "on"})
        self.assertFalse(form.is_valid())
        self.assertIn("rut", form.errors)
        self.assertIn("clave", form.errors)

    def test_alta_fintual_como_inversion(self):
        form, cred = self._guardar(
            {
                "conector": "fintual",
                "api_key": "yo@mail.com",  # email
                "api_secret": "clave",  # contraseña
                "activo": "on",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(cred.tipo_fuente, "inversion")
        self.assertEqual(cred.get_api_key(), "yo@mail.com")
        self.assertEqual(cred.banco.nombre, "Fintual")


class ConfigViewRenderTest(TestCase):
    """La página de configuración debe renderizar con el selector de fuentes."""

    def setUp(self):
        self.user = User.objects.create_user(username="view_test", password="x")
        self.client.force_login(self.user)

    def test_config_render_incluye_fuentes(self):
        from django.urls import reverse

        resp = self.client.get(reverse("banca_config"))
        self.assertEqual(resp.status_code, 200)
        # el dropdown de fuentes debe listar bancos y exchanges
        self.assertContains(resp, "Binance")
        self.assertContains(resp, "Santander")
        self.assertContains(resp, 'data-tipo="cripto"')
