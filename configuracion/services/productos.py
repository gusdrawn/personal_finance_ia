"""Consolidación explícita de cuentas financieras importadas y configuradas."""

from collections import defaultdict

from django.db import transaction

from bank_sync.models import _norm

from banca.models import ImportacionEstadoCuenta, MovimientoBancario, SaldoSnapshot
from configuracion.models import Producto
from departamentos.models import CreditoHipotecario
from patrimonio.models import Pasivo
from prestamos.models import Prestamo


def producto_canonico(producto):
    """Resuelve los alias importados, comprobando aislamiento y ciclos."""
    visitados = set()
    while producto.reemplazado_por_id:
        if producto.pk in visitados:
            raise ValueError("La vinculación de productos contiene un ciclo.")
        visitados.add(producto.pk)
        destino = producto.reemplazado_por
        if (destino.user_id, destino.banco_id, destino.tipo) != (producto.user_id, producto.banco_id, producto.tipo):
            raise ValueError("El producto reemplazante debe conservar usuario, banco y tipo.")
        producto = destino
    return producto


def _clave_movimiento(movimiento):
    return ("contenido", movimiento.fecha, movimiento.monto, movimiento.moneda,
            _norm(movimiento.descripcion), movimiento.cuota_actual or 0, movimiento.cuota_total or 0)


def _fusionar_movimiento(anterior, canonico):
    """Conserva enlaces y decisiones; un conflicto requiere revisión explícita."""
    if anterior.categoria_es_manual and canonico.categoria_es_manual and anterior.categoria_id != canonico.categoria_id:
        raise ValueError("Hay movimientos coincidentes con categorías manuales distintas. Revisa sus categorías antes de vincular.")
    if anterior.naturaleza_es_manual and canonico.naturaleza_es_manual and (anterior.naturaleza, anterior.prestamo_id) != (canonico.naturaleza, canonico.prestamo_id):
        raise ValueError("Hay movimientos coincidentes con decisiones financieras distintas.")
    if anterior.prestamo_id and canonico.prestamo_id and anterior.prestamo_id != canonico.prestamo_id:
        raise ValueError("Los movimientos coincidentes corresponden a préstamos distintos.")

    # Los enlaces contables no se suman ni se descartan por una similitud de
    # glosa. Si ambas filas tienen enlaces del mismo tipo, la fusión se revierte.
    relaciones = []
    for relacion in MovimientoBancario._meta.get_fields():
        if not relacion.auto_created or not (relacion.one_to_many or relacion.one_to_one):
            continue
        campo = relacion.field.name
        manager = relacion.related_model._base_manager
        origen = manager.filter(**{campo: anterior})
        if origen.exists() and manager.filter(**{campo: canonico}).exists():
            raise ValueError("Ambos movimientos tienen enlaces contables. Revisa las asignaciones antes de vincular productos.")
        relaciones.append((origen, campo))

    campos = []
    if anterior.referencia_externa and not canonico.referencia_externa:
        canonico.referencia_externa = anterior.referencia_externa
        campos.append("referencia_externa")
    if anterior.categoria_es_manual or (not canonico.categoria_id and anterior.categoria_id):
        for campo in ("categoria_id", "categoria_es_manual", "fuente_clasificacion", "esencialidad", "clasificado_at", "confianza_ia"):
            setattr(canonico, campo, getattr(anterior, campo))
            campos.append(campo.removesuffix("_id"))
    if anterior.naturaleza_es_manual:
        for campo in ("naturaleza", "naturaleza_es_manual", "por_revisar"):
            setattr(canonico, campo, getattr(anterior, campo))
            campos.append(campo)
    if not canonico.prestamo_id and anterior.prestamo_id:
        canonico.prestamo_id = anterior.prestamo_id
        campos.append("prestamo")
    if not canonico.tipo_cambio_banco and anterior.tipo_cambio_banco:
        canonico.tipo_cambio_banco = anterior.tipo_cambio_banco
        canonico.monto_clp = anterior.monto_clp
        campos.extend(["tipo_cambio_banco", "monto_clp"])
    prioridad = {"DESCARTADO_CIERRE": 0, "PROVISIONAL": 1, "NO_FACTURADO": 2, "FACTURADO": 3, "PAGADO": 4}
    if prioridad.get(anterior.estado, 0) > prioridad.get(canonico.estado, 0):
        canonico.estado = anterior.estado
        canonico.periodo_factura = anterior.periodo_factura
        campos.extend(["estado", "periodo_factura"])
    if anterior.origen == "PDF":
        canonico.origen = "PDF"
        campos.append("origen")
    if anterior.notificado_telegram:
        canonico.notificado_telegram = True
        campos.append("notificado_telegram")
    if campos:
        canonico.save(update_fields=list(set(campos)) + ["updated_at"])
    canonico.importaciones.add(*anterior.importaciones.all())
    for origen, campo in relaciones:
        origen.update(**{campo: canonico})
    anterior.delete()


@transaction.atomic
def vincular_producto(user, anterior: Producto, canonico: Producto) -> Producto:
    if anterior.user_id != user.id or canonico.user_id != user.id:
        raise ValueError("Ambos productos deben pertenecer al usuario.")
    if anterior.pk == canonico.pk:
        raise ValueError("El producto anterior y el canónico deben ser distintos.")
    if anterior.banco_id != canonico.banco_id or anterior.tipo != canonico.tipo:
        raise ValueError("Banco y tipo deben coincidir para consolidar productos.")
    list(Producto.objects.select_for_update().filter(user=user, pk__in=[anterior.pk, canonico.pk]).order_by("pk"))
    anterior.refresh_from_db()
    canonico.refresh_from_db()
    canonico = producto_canonico(canonico)
    if anterior.reemplazado_por_id or canonico.pk == anterior.pk:
        raise ValueError("Selecciona productos sin reemplazar; la vinculación no puede crear ciclos.")

    destinos, destinos_por_referencia = defaultdict(list), defaultdict(list)
    for movimiento in MovimientoBancario.objects.filter(user=user, producto=canonico).order_by("pk"):
        destinos[_clave_movimiento(movimiento)].append(movimiento)
        if movimiento.referencia_externa:
            destinos_por_referencia[movimiento.referencia_externa].append(movimiento)
    usados = set()
    for movimiento in MovimientoBancario.objects.filter(user=user, producto=anterior).order_by("pk"):
        candidatos = destinos_por_referencia.get(movimiento.referencia_externa, []) or destinos[_clave_movimiento(movimiento)]
        destino = next((item for item in candidatos if item.pk not in usados and not (
            item.referencia_externa and movimiento.referencia_externa and item.referencia_externa != movimiento.referencia_externa
        )), None)
        if destino:
            _fusionar_movimiento(movimiento, destino)
            usados.add(destino.pk)

    MovimientoBancario.objects.filter(user=user, producto=anterior).update(producto=canonico)
    ImportacionEstadoCuenta.objects.filter(user=user, producto=anterior).update(producto=canonico)
    vigentes = defaultdict(list)
    for documento in ImportacionEstadoCuenta.objects.filter(user=user, producto=canonico, tipo_documento="ESTADO_CUENTA", estado="PROCESADO", vigente=True).order_by("-created_at", "-pk"):
        referencia = documento.fecha_estado if canonico.tipo == "CUENTA" else documento.fecha_vencimiento
        if referencia:
            vigentes[(documento.moneda, referencia.replace(day=1))].append(documento)
    for grupo in vigentes.values():
        if len(grupo) > 1:
            ImportacionEstadoCuenta.objects.filter(pk__in=[item.pk for item in grupo[1:]]).update(vigente=False)
            if not grupo[0].reemplaza_a_id:
                grupo[0].reemplaza_a = grupo[1]
                grupo[0].save(update_fields=["reemplaza_a"])
    for pasivo in Pasivo.objects.filter(user=user, producto=anterior):
        existente = Pasivo.objects.filter(user=user, producto=canonico, sincronizado_banca=True, moneda_original=pasivo.moneda_original).first() if pasivo.sincronizado_banca else None
        if existente:
            # Una deuda es una foto de saldo, no un movimiento sumable. Conserva
            # el registro anterior como historial inactivo, sin infringir unicidad.
            if pasivo.updated_at > existente.updated_at:
                for campo in ("monto_original", "moneda_original", "monto_clp", "monto_usd", "activo", "requiere_revision_moneda"):
                    setattr(existente, campo, getattr(pasivo, campo))
                existente.save()
            pasivo.producto = None
            pasivo.activo = False
            pasivo.notas = f"{pasivo.notas}\nConsolidado con pasivo #{existente.pk} del producto #{canonico.pk}.".strip()
            pasivo.save(update_fields=["producto", "activo", "notas", "updated_at"])
        else:
            pasivo.producto = canonico
            pasivo.save(update_fields=["producto", "updated_at"])
    Prestamo.objects.filter(user=user, tarjeta_asociada=anterior).update(tarjeta_asociada=canonico)
    CreditoHipotecario.objects.filter(
        departamento__user=user, producto=anterior
    ).update(producto=canonico)

    for snapshot in SaldoSnapshot.objects.filter(user=user, producto=anterior):
        existente = SaldoSnapshot.objects.filter(
            user=user, producto=canonico, fecha=snapshot.fecha
        ).first()
        if not existente:
            snapshot.producto = canonico
            snapshot.save(update_fields=["producto"])
            continue
        campos = []
        for field in (
            "fecha_vencimiento", "deuda_facturada_clp", "deuda_no_facturada_clp",
            "cupo_total_clp", "cupo_disponible_clp", "cupo_utilizado_clp",
            "cupo_avance_disponible_clp", "cupo_usd", "cupo_usd_disponible",
            "deuda_usd", "tipo_cambio_banco", "saldo_insoluto", "saldo_insoluto_uf",
            "proxima_cuota", "proxima_cuota_uf", "fecha_proximo_pago",
            "saldo_disponible_clp",
        ):
            if getattr(existente, field) is None and getattr(snapshot, field) is not None:
                setattr(existente, field, getattr(snapshot, field))
                campos.append(field)
        if campos:
            existente.save(update_fields=campos)
        snapshot.delete()

    # El último snapshot es la evidencia preferente frente al saldo cacheado.
    ultimo = SaldoSnapshot.objects.filter(user=user, producto=canonico).order_by("-fecha", "-id").first()
    if ultimo:
        from banca.signals import sync_pasivo_desde_snapshot

        sync_pasivo_desde_snapshot(SaldoSnapshot, ultimo)

    anterior.activo = False
    anterior.requiere_revision = False
    anterior.reemplazado_por = canonico
    anterior.save(update_fields=["activo", "requiere_revision", "reemplazado_por"])
    canonico.activo = True
    canonico.requiere_revision = False
    canonico.save(update_fields=["activo", "requiere_revision"])
    return canonico
