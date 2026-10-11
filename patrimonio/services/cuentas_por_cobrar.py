"""Reglas de negocio para cuentas por cobrar."""

from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher

from django.db import transaction
from django.db.models import Q

from patrimonio.models import (
    CuentaPorCobrar,
    DetalleCuentaPorCobrar,
)


def primer_dia_mes(fecha: date) -> date:
    return fecha.replace(day=1)


def ultimo_dia_mes(fecha: date) -> date:
    return fecha.replace(day=calendar.monthrange(fecha.year, fecha.month)[1])


def ventana_fuentes_cobro(periodo: date, dias_anteriores: int = 10) -> tuple[date, date]:
    """Devuelve la ventana de transacciones que puede pagar un mes.

    NeatPagos y otras automatizaciones pueden cargar una cuenta entre algunos
    días antes del día 1 y el fin del mes seleccionado. La ventana incluye
    esos cargos anticipados, pero no arrastra todo el historial anterior.
    """
    periodo = primer_dia_mes(periodo)
    return periodo - timedelta(days=dias_anteriores), ultimo_dia_mes(periodo)


def normalizar_referencia(texto: str) -> str:
    """Normaliza glosas para detectar el mismo comercio entre meses."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(char for char in texto if not unicodedata.combining(char))
    texto = re.sub(r"\d+", " ", texto.lower())
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def _pago_neat(evento):
    try:
        return evento.pago_neat
    except Exception:
        return None


def fecha_evento_neat(evento) -> date | None:
    """Obtiene la fecha del evento y recupera reportes antiguos desde metadata."""
    fecha = evento.fecha_cargo or evento.fecha_evento or evento.periodo
    if fecha:
        return fecha
    metadata = evento.metadata or {}
    valor = metadata.get("dia_de_pago") or metadata.get("fecha_cargo") or metadata.get("fecha")
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _descriptor_evento_neat(evento) -> str:
    pago = _pago_neat(evento)
    if not pago:
        return str(evento.external_id or "neat")
    return "|".join(
        [
            pago.destinatario or "",
            pago.cuenta or "",
            pago.tarjeta_ultimos4 or "",
        ]
    ).strip("|")


def clave_fuente(fuente) -> str:
    """Clave estable de una fuente para renovar el cobro en otro mes."""
    from banca.models import EventoFinancieroExterno, MovimientoBancario

    if isinstance(fuente, MovimientoBancario):
        return f"movimiento:{fuente.producto_id}:{normalizar_referencia(fuente.descripcion)}"[:255]
    if isinstance(fuente, EventoFinancieroExterno):
        return f"neat:{normalizar_referencia(_descriptor_evento_neat(fuente))}"[:255]
    return ""


def informacion_fuente(fuente) -> dict:
    """Entrega importe, fecha, moneda y texto de una transacción seleccionable."""
    from banca.models import EventoFinancieroExterno, MovimientoBancario

    if isinstance(fuente, MovimientoBancario):
        monto = fuente.monto if fuente.monto else fuente.monto_clp
        return {
            "monto": abs(monto or Decimal("0")),
            "moneda": fuente.moneda,
            "fecha": fuente.fecha,
            "descripcion": fuente.descripcion,
            "origen": "MOVIMIENTO",
            "clave": clave_fuente(fuente),
        }
    if isinstance(fuente, EventoFinancieroExterno):
        pago = _pago_neat(fuente)
        descripcion = (
            (pago.destinatario or pago.cuenta) if pago else "NeatPagos"
        ) or "NeatPagos"
        return {
            "monto": abs(fuente.monto_neto or fuente.monto_bruto or Decimal("0")),
            "moneda": fuente.moneda,
            "fecha": fecha_evento_neat(fuente),
            "descripcion": descripcion,
            "origen": "NEAT",
            "clave": clave_fuente(fuente),
        }
    raise TypeError("Fuente financiera no soportada")


def validar_fuente_no_solapada(fuente, cuenta, detalle_id=None):
    """Impide cobrar el cargo y su desglose por cualquiera de las interfaces."""
    from banca.models import ConciliacionEventoExterno, MovimientoBancario

    if fuente.user_id != cuenta.user_id:
        raise ValueError("La fuente y la cuenta deben pertenecer al mismo usuario.")
    if isinstance(fuente, MovimientoBancario):
        cargos = [fuente.pk]
        eventos = ConciliacionEventoExterno.objects.filter(
            movimiento=fuente, evento__user=fuente.user, evento__fuente="neat", rol="CARGO",
        ).values("evento_id")
        solapados = DetalleCuentaPorCobrar.objects.filter(cuenta__user=fuente.user, vigente=True, evento_externo_id__in=eventos)
    else:
        cargos = list(ConciliacionEventoExterno.objects.filter(
            evento=fuente, movimiento__user=fuente.user, rol="CARGO",
        ).values_list("movimiento_id", flat=True))
        solapados = DetalleCuentaPorCobrar.objects.filter(cuenta__user=fuente.user, vigente=True, movimiento_id__in=cargos)
    if transaction.get_connection().in_atomic_block:
        list(MovimientoBancario.objects.select_for_update().filter(pk__in=cargos).order_by("pk"))
    if detalle_id:
        solapados = solapados.exclude(pk=detalle_id)
    if solapados.exists():
        raise ValueError("Este pago ya está asignado mediante su cargo bancario o su desglose Neat. Quita esa asignación antes de usar la otra fuente.")


@transaction.atomic
def asignar_fuente_a_cuenta(fuente, cuenta: CuentaPorCobrar | None, detalle_base=None):
    """Vincula una transacción a una sola cuenta por cobrar o la libera.

    La fuente es la clasificación financiera: la categoría temática puede
    seguir describiendo el comercio, pero deja de convertirlo en consumo.
    """
    from banca.models import EventoFinancieroExterno, MovimientoBancario

    if not isinstance(fuente, (MovimientoBancario, EventoFinancieroExterno)):
        raise TypeError("Fuente financiera no soportada")
    if cuenta is not None and fuente.user_id != cuenta.user_id:
        raise ValueError("La fuente y la cuenta deben pertenecer al mismo usuario.")
    fuente = type(fuente).objects.select_for_update().get(pk=fuente.pk)
    if cuenta is not None:
        validar_fuente_no_solapada(fuente, cuenta, detalle_base.pk if detalle_base else None)

    filtro = (
        {"movimiento": fuente}
        if isinstance(fuente, MovimientoBancario)
        else {"evento_externo": fuente}
    )
    detalles = list(
        DetalleCuentaPorCobrar.objects.select_related("cuenta")
        .filter(**filtro)
        .order_by("id")
    )
    if cuenta is None:
        cuentas_afectadas = [detalle.cuenta for detalle in detalles]
        DetalleCuentaPorCobrar.objects.filter(pk__in=[d.pk for d in detalles]).delete()
        for cuenta_afectada in cuentas_afectadas:
            cuenta_afectada.sincronizar_activo()
        return None

    info = informacion_fuente(fuente)
    if info["moneda"] != cuenta.moneda:
        raise ValueError("La moneda de la transacción no coincide con la cuenta.")

    # La categoría explica visualmente por qué el cargo no es consumo. La
    # relación con CuentaPorCobrar sigue siendo la regla contable efectiva.
    from banca.models import CategoriaTematica

    categoria_terceros, _ = CategoriaTematica.objects.get_or_create(
        user=cuenta.user,
        nombre="Pagos por cuenta de terceros",
        defaults={
            "aplica_a": "EGRESO",
            "origen": "SISTEMA",
            "esencialidad_defecto": "FIJO",
        },
    )
    fuente.categoria = categoria_terceros
    fuente.fuente_clasificacion = "MANUAL"
    fuente.categoria_es_manual = True
    campos_fuente = [
        "categoria",
        "fuente_clasificacion",
        "categoria_es_manual",
        "updated_at",
    ]
    if isinstance(fuente, MovimientoBancario):
        from django.utils import timezone

        fuente.clasificado_at = timezone.now()
        campos_fuente.append("clasificado_at")
    fuente.save(update_fields=campos_fuente)

    detalle = detalle_base or next(
        (item for item in detalles if item.cuenta_id == cuenta.pk),
        detalles[0] if detalles else None,
    )
    cuentas_anteriores = [item.cuenta for item in detalles if item.cuenta_id != cuenta.pk]
    if detalle is None:
        detalle = DetalleCuentaPorCobrar(
            movimiento=fuente if isinstance(fuente, MovimientoBancario) else None,
            evento_externo=fuente if isinstance(fuente, EventoFinancieroExterno) else None,
        )
    elif detalle.cuenta_id != cuenta.pk:
        # Los abonos asociados deben viajar con la obligación para mantener
        # consistente el saldo de ambas cuentas.
        detalle.abonos.update(cuenta=cuenta)
    detalle.cuenta = cuenta
    detalle.movimiento = fuente if isinstance(fuente, MovimientoBancario) else None
    detalle.evento_externo = fuente if isinstance(fuente, EventoFinancieroExterno) else None
    detalle.vigente = True
    detalle.descripcion = info["descripcion"]
    detalle.periodo = primer_dia_mes(info["fecha"] or date.today())
    detalle.fecha = info["fecha"] or date.today()
    detalle.monto = info["monto"]
    detalle.origen = info["origen"]
    detalle.monto_manual = False
    detalle.provisional = False
    detalle.clave_similitud = info["clave"]
    detalle.save()

    # Limpia duplicados que pudieran existir antes de imponer la restricción
    # global por fuente.
    for duplicado in (item for item in detalles if item.pk != detalle.pk):
        duplicado.abonos.update(cuenta=cuenta, detalle=detalle)
        duplicado.delete()
    for cuenta_anterior in cuentas_anteriores:
        cuenta_anterior.sincronizar_activo()
    return detalle


def _descriptor_detalle(detalle: DetalleCuentaPorCobrar) -> str:
    if detalle.movimiento_id:
        return normalizar_referencia(detalle.movimiento.descripcion)
    if detalle.evento_externo_id:
        return normalizar_referencia(_descriptor_evento_neat(detalle.evento_externo))
    return ""


def _buscar_fuente_similar(cuenta, detalle, periodo, usados):
    """Busca una fuente del mes y sus cargos anticipados; evita empates."""
    from banca.models import EventoFinancieroExterno, MovimientoBancario

    inicio, fin = ventana_fuentes_cobro(periodo)
    if detalle.movimiento_id:
        queryset = MovimientoBancario.objects.filter(
            user=cuenta.user,
            producto_id=detalle.movimiento.producto_id,
            fecha__range=(inicio, fin),
            monto__gt=0,
            moneda=cuenta.moneda,
        ).exclude(estado="DESCARTADO_CIERRE")
        candidatos = [mov for mov in queryset if ("movimiento", mov.id) not in usados]
    elif detalle.evento_externo_id:
        queryset = EventoFinancieroExterno.objects.filter(
            user=cuenta.user,
            fuente="neat",
            moneda=cuenta.moneda,
            monto_neto__gt=0,
        ).filter(
            Q(fecha_cargo__range=(inicio, fin))
            | Q(fecha_evento__range=(inicio, fin))
            | Q(periodo=periodo)
            | Q(fecha_cargo__isnull=True, fecha_evento__isnull=True, periodo__isnull=True)
        ).exclude(estado__in=["FALLIDO", "REVERSADO"]).select_related("pago_neat")
        candidatos = []
        for evento in queryset:
            if ("neat", evento.id) in usados:
                continue
            fecha = informacion_fuente(evento)["fecha"]
            if fecha and inicio <= fecha <= fin:
                candidatos.append(evento)
    else:
        return None

    if not candidatos:
        return None

    clave_anterior = detalle.clave_similitud
    descriptor_anterior = _descriptor_detalle(detalle)
    puntuados = []
    for candidato in candidatos:
        try:
            validar_fuente_no_solapada(candidato, cuenta)
        except ValueError:
            continue
        clave = clave_fuente(candidato)
        descriptor = (
            normalizar_referencia(candidato.descripcion)
            if hasattr(candidato, "descripcion")
            else normalizar_referencia(_descriptor_evento_neat(candidato))
        )
        puntuacion = 1.0 if clave and clave == clave_anterior else SequenceMatcher(
            None, descriptor_anterior, descriptor
        ).ratio()
        puntuados.append((puntuacion, informacion_fuente(candidato)["fecha"] or inicio, candidato))

    if not puntuados:
        return None
    puntuados.sort(key=lambda item: (item[0], item[1], item[2].id), reverse=True)
    mejor = puntuados[0]
    if mejor[0] < 0.62:
        return None
    if len(puntuados) > 1 and puntuados[1][0] == mejor[0] and mejor[0] < 1.0:
        return None
    return mejor[2]


@transaction.atomic
def proponer_renovacion(cuenta: CuentaPorCobrar, periodo: date) -> dict:
    """Busca líneas del mes indicado sin guardar cambios.

    La propuesta usa las últimas líneas asociadas a una fuente real, aunque la
    cuenta tenga después líneas manuales para el mismo concepto. Esto permite,
    por ejemplo, usar el arriendo NeatPagos de agosto como patrón para buscar
    el arriendo de octubre.
    """
    periodo = primer_dia_mes(periodo)
    detalles = list(
        cuenta.detalles.filter(periodo__lt=periodo, vigente=True)
        .select_related("movimiento", "movimiento__producto", "evento_externo")
        .order_by("-periodo", "-id")
    )
    ultimos_por_clave = {}
    for detalle in detalles:
        if detalle.monto_manual or not detalle.clave_similitud:
            continue
        ultimos_por_clave.setdefault(detalle.clave_similitud, detalle)

    # Un pago del final del mes puede estar en la ventana del siguiente. Su
    # período ya elegido prevalece: no se reutiliza para otro cobro mensual.
    existentes = DetalleCuentaPorCobrar.objects.filter(cuenta__user=cuenta.user, vigente=True)
    usados = {("movimiento", pk) for pk in existentes.filter(movimiento__isnull=False)
              .values_list("movimiento_id", flat=True)}
    usados.update(("neat", pk) for pk in existentes.filter(evento_externo__isnull=False)
                  .values_list("evento_externo_id", flat=True))
    from patrimonio.models import PartidaRevisionCobro
    revisadas = PartidaRevisionCobro.objects.filter(
        cuenta__user_id=cuenta.user_id,
    ).filter(Q(cuenta=cuenta) | Q(resuelta_en__isnull=True) | ~Q(decision="EXCLUIR"))
    usados.update(("movimiento", pk) for pk in revisadas.filter(movimiento__isnull=False)
                  .values_list("movimiento_id", flat=True))
    usados.update(("neat", pk) for pk in revisadas.filter(evento_externo__isnull=False)
                  .values_list("evento_externo_id", flat=True))
    propuestas = []
    no_encontrados = []
    for detalle in ultimos_por_clave.values():
        fuente = _buscar_fuente_similar(cuenta, detalle, periodo, usados)
        if fuente is None:
            no_encontrados.append(detalle.descripcion)
            continue
        info = informacion_fuente(fuente)
        propuestas.append(
            {
                "detalle_id": detalle.id,
                "descripcion": detalle.descripcion,
                "periodo": periodo,
                "fecha": info["fecha"] or periodo,
                "monto": info["monto"],
                "origen": info["origen"],
                "referencia": info["descripcion"],
                "clave": info["clave"],
                "fuente": fuente,
            }
        )
        usados.add(("movimiento" if hasattr(fuente, "producto_id") else "neat", fuente.id))
    return {
        "periodo": periodo,
        "propuestas": propuestas,
        "no_encontrados": no_encontrados,
    }


@transaction.atomic
def renovar_cuenta(cuenta: CuentaPorCobrar, periodo: date) -> dict:
    """Guarda una propuesta previamente calculada para el mes indicado."""
    resultado = proponer_renovacion(cuenta, periodo)
    creados = 0
    for propuesta in resultado["propuestas"]:
        fuente = propuesta["fuente"]
        nuevo = DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion=propuesta["descripcion"],
            periodo=propuesta["periodo"],
            fecha=propuesta["fecha"],
            monto=propuesta["monto"],
            origen=propuesta["origen"],
            monto_manual=False,
            provisional=False,
            movimiento=fuente if propuesta["origen"] == "MOVIMIENTO" else None,
            evento_externo=fuente if propuesta["origen"] == "NEAT" else None,
            clave_similitud=propuesta["clave"],
        )
        creados += 1
    return {
        "creados": creados,
        "no_encontrados": resultado["no_encontrados"],
    }


def formatear_monto(monto: Decimal, moneda: str) -> str:
    monto = monto or Decimal("0")
    if moneda == "USD":
        entero, decimales = f"{monto:,.2f}".split(".")
        return f"US${entero.replace(',', '.')},{decimales}"
    entero = f"{monto:,.0f}"
    return f"${entero.replace(',', '.')}"


def texto_cobro(cuenta: CuentaPorCobrar, periodo: date | None = None) -> str:
    """Construye el texto del mes actual y resume la deuda histórica.

    Las líneas de meses anteriores no se repiten: se agrupan en un único
    ``Saldo pendiente anterior`` para que un concepto recurrente no parezca
    duplicado al copiar el cobro en WhatsApp. Las fechas se conservan en la
    tabla de la aplicación, pero no se incluyen en el texto compartible.
    """
    confirmado = cuenta.calculos.filter(estado="CONFIRMADO")
    if periodo is not None:
        confirmado = confirmado.filter(periodo__lte=periodo)
    calculo = confirmado.first()
    if calculo:
        return calculo.texto
    detalles = [d for d in cuenta.detalles.all() if d.vigente]
    if periodo is None:
        periodo = max((detalle.periodo for detalle in detalles), default=None)
    lineas = [f"Detalle pendiente: {cuenta.nombre}"]
    if cuenta.descripcion:
        lineas.append(cuenta.descripcion)
    pendientes_mes = [
        detalle
        for detalle in detalles
        if detalle.saldo_pendiente > 0
        and (periodo is None or detalle.periodo == periodo)
    ]
    for detalle in pendientes_mes:
        lineas.append(
            f"- {detalle.descripcion}: "
            f"{formatear_monto(detalle.saldo_pendiente, cuenta.moneda)}"
        )
    saldo_anterior = sum(
        (
            detalle.saldo_pendiente
            for detalle in detalles
            if detalle.saldo_pendiente > 0
            and periodo is not None
            and detalle.periodo < periodo
        ),
        Decimal("0"),
    )
    if saldo_anterior:
        lineas.append(
            f"- Saldo pendiente anterior: "
            f"{formatear_monto(saldo_anterior, cuenta.moneda)}"
        )
    abonos_generales = sum(
        (
            abono.monto
            for abono in cuenta.abonos.all()
            if abono.detalle_id is None and abono.vigente
        ),
        Decimal("0"),
    )
    if abonos_generales:
        lineas.append(
            f"Abonos recibidos: -{formatear_monto(abonos_generales, cuenta.moneda)}"
        )
    lineas.append(
        f"Total pendiente: {formatear_monto(cuenta.saldo_pendiente, cuenta.moneda)}"
    )
    return "\n".join(lineas)
