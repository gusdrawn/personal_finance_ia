import os
import django
from decimal import Decimal
from datetime import date, datetime

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'finanzas_project.settings')
django.setup()

from django.contrib.auth.models import User
from configuracion.models import Banco, Producto, TipoCambio
from banca.models import CategoriaTematica
from gastos.models import MovimientoManual
from patrimonio.models import Activo, HistorialActivo, Pasivo, SnapshotPatrimonio
from departamentos.models import Departamento, CreditoHipotecario

def run():
    print("Iniciando carga de datos...")
    
    # Usuario
    user, created = User.objects.get_or_create(username='gusdrawn', defaults={
        'is_superuser': True, 'is_staff': True, 'first_name': 'Gus', 'last_name': 'Drawn'
    })
    if created:
        user.set_password('admin123')
        user.save()
        print(f"Usuario {user.username} creado.")
    else:
        print(f"Usuario {user.username} ya existe.")

    # 1. Bancos
    print("Cargando Bancos...")
    bancos_nombres = ['Santander', 'Scotiabank', 'BCI', 'ITAU', 'Lider BCI', 'Falabella', 'Tenpo', 'Banco de Chile', 'American Express']
    bancos = {}
    for b_nom in bancos_nombres:
        banco, _ = Banco.objects.get_or_create(nombre=b_nom)
        bancos[b_nom] = banco

    # 2. Categorías de Ingreso/Gasto
    print("Cargando Categorías...")
    # Ingresos
    cat_salario, _ = CategoriaTematica.objects.get_or_create(user=user, nombre='Salario', defaults={'aplica_a': 'INGRESO', 'origen': 'SISTEMA', 'orden': 1})
    cat_arriendo, _ = CategoriaTematica.objects.get_or_create(user=user, nombre='Arriendos Deptos', defaults={'aplica_a': 'INGRESO', 'origen': 'SISTEMA', 'orden': 2})
    
    # Gastos
    def gasto(nombre, orden, esencialidad='FIJO'):
        return CategoriaTematica.objects.get_or_create(user=user, nombre=nombre, defaults={'aplica_a': 'EGRESO', 'origen': 'SISTEMA', 'orden': orden, 'esencialidad_defecto': esencialidad})[0]
    cat_utilities = gasto('Suministros (Luz/Agua/Gas)', 1)
    cat_internet = gasto('Internet/Movil', 2)
    cat_vivienda = gasto('Vivienda (Arriendo/G.Comun)', 3)
    cat_tdc = gasto('Tarjetas de Crédito', 4, 'OCIO')
    cat_suscripcion = gasto('Suscripciones', 5, 'OCIO')
    cat_seguro = gasto('Seguros', 6)
    cat_credito = gasto('Dividendos / Créditos', 7)

    # 3. Departamentos (Portafolio)
    print("Cargando Departamentos...")
    deptos_data = [
        {'codigo': 'E107', 'piso': '1', 'valor_compra_uf': 2800, 'valor_actual_uf': 3200},
        {'codigo': 'E205', 'piso': '2', 'valor_compra_uf': 2600, 'valor_actual_uf': 3000},
        {'codigo': 'E507', 'piso': '5', 'valor_compra_uf': 2800, 'valor_actual_uf': 3200},
        {'codigo': 'F107', 'piso': '1', 'valor_compra_uf': 2800, 'valor_actual_uf': 3200},
        {'codigo': 'F205', 'piso': '2', 'valor_compra_uf': 2600, 'valor_actual_uf': 3000},
        {'codigo': 'F405', 'piso': '4', 'valor_compra_uf': 2700, 'valor_actual_uf': 3100},
    ]
    deptos = {}
    for d in deptos_data:
        depto, _ = Departamento.objects.get_or_create(
            user=user, codigo=d['codigo'], 
            defaults={
                'piso': d['piso'], 
                'metros_cuadrados': 45.0, 
                'valor_compra_uf': d['valor_compra_uf'],
                'valor_actual_uf': d['valor_actual_uf'],
                'fecha_inicio': date(2021, 1, 1),
                'plazo_anos': 30
            }
        )
        deptos[d['codigo']] = depto

    # 4. Activos Actuales (Snapshot Image 2)
    print("Cargando Activos...")
    activos_data = [
        # (nombre, tipo, es_liquido, horizonte, monto_clp, monto_usd, *depto)
        # Efectivo
        ('Cash Panama', 'EFECTIVO', True, 'EFECTIVO', 265744, 290),
        ('Efectivo', 'EFECTIVO', True, 'EFECTIVO', 20000, 22),
        ('Cash BoA', 'EFECTIVO', True, 'EFECTIVO', 460929, 503),
        # Inversiones
        ('Crypto General', 'CRIPTO', True, 'MEDIANO_PLAZO', 4394863, 4796),
        ('SoyFocus', 'FONDO_MUTUO', True, 'MEDIANO_PLAZO', 129447, 141),
        ('Fintual', 'FONDO_MUTUO', True, 'MEDIANO_PLAZO', 4338849, 4735),
        ('DVA', 'BROKERAGE', True, 'LARGO_PLAZO', 8637522, 9426),
        ('Buda', 'CRIPTO', True, 'CORTO_PLAZO', 534229, 583),
        # Deptos (Vinculados)
        ('E107 - Activo', 'DEPARTAMENTO', False, 'LARGO_PLAZO', 103057249, 112464, deptos['E107']),
        ('E205 - Activo', 'DEPARTAMENTO', False, 'LARGO_PLAZO', 73220228, 79903, deptos['E205']),
        ('E507 - Activo', 'DEPARTAMENTO', False, 'LARGO_PLAZO', 103057249, 112464, deptos['E507']),
        ('F107 - Activo', 'DEPARTAMENTO', False, 'LARGO_PLAZO', 103057249, 112464, deptos['F107']),
        ('F205 - Activo', 'DEPARTAMENTO', False, 'LARGO_PLAZO', 73220228, 79903, deptos['F205']),
        ('F405 - Activo', 'DEPARTAMENTO', False, 'LARGO_PLAZO', 72511930, 79130, deptos['F405']),
        # Ahorros
        ('AFP Habitat', 'AHORRO', False, 'LARGO_PLAZO', 26090844, 28472),
        ('AFC', 'AHORRO', False, 'LARGO_PLAZO', 8016554, 8748),
    ]
    for act in activos_data:
        m_clp, m_usd = Decimal(act[4]), Decimal(act[5])
        depto = act[6] if len(act) > 6 else None
        Activo.objects.update_or_create(
            user=user, nombre=act[0],
            defaults={
                'tipo': act[1], 'es_liquido': act[2],
                'horizonte_temporal': act[3],
                'monto_clp': m_clp, 'monto_usd': m_usd,
                'departamento': depto
            }
        )

    # 5. Pasivos Actuales (Snapshot Image 2)
    print("Cargando Pasivos...")
    pasivos_data = [
        # Tarjetas
        ('WorldMember Limited CLP', 'TDC', 9234425, 10077),
        ('CMR Falabella', 'TDC', 6981707, 7619),
        ('Itau TDC', 'TDC', 5055600, 5517),
        ('Tenpo TDC', 'TDC', 950000, 1037),
        # Creditos
        ('Credito Scotiabank Consume', 'PRESTAMO', 73793656, 80529),
        # Hipotecas
        ('Hipt. Scotiabank 1', 'CREDITO_HIPOTECARIO', 80430974, 87772),
        ('Hipt. Scotiabank 2', 'CREDITO_HIPOTECARIO', 56612474, 61780),
        ('Hipt. BCI 1', 'CREDITO_HIPOTECARIO', 82731240, 90282),
        ('Hipt. BCI 2', 'CREDITO_HIPOTECARIO', 55318049, 60367),
        ('Hipt. ITAU', 'CREDITO_HIPOTECARIO', 56293561, 61432),
        ('Hipt. Santander', 'CREDITO_HIPOTECARIO', 79085814, 86304),
    ]
    for pas in pasivos_data:
        m_clp, m_usd = Decimal(pas[2]), Decimal(pas[3])
        Pasivo.objects.update_or_create(
            user=user, nombre=pas[0],
            defaults={'tipo': pas[1], 'monto_clp': m_clp, 'monto_usd': m_usd}
        )

    # 6. Historial de Patrimonio (Snapshot History Image 3)
    print("Cargando Historial de Patrimonio...")
    history = [
        # (Fecha, Activos CLP, Pasivos CLP, Patrimonio CLP, Patrimonio USD)
        ('2025-01-01', 77846187, 33514507, 44331680, 48378),
        ('2025-03-01', 78469941, 28945324, 49524617, 54045),
        ('2025-06-01', 529888830, 432443628, 97445202, 106339),
        ('2025-10-01', 534567087, 443265083, 91302003, 99636),
        ('2026-01-01', 542858068, 452795012, 90063056, 98283),
        ('2026-03-09', 618932652, 510601830, 108330822, 118219),
    ]
    for row in history:
        fecha = datetime.strptime(row[0], '%Y-%m-%d').date()
        SnapshotPatrimonio.objects.update_or_create(
            user=user, fecha=fecha,
            defaults={
                'activos_total_clp': Decimal(row[1]), 
                'pasivos_total_clp': Decimal(row[2]),
                'patrimonio_neto_clp': Decimal(row[3]), 
                'patrimonio_neto_usd': Decimal(row[4]),
                'activos_total_usd': Decimal(row[4]) + (Decimal(row[2]) / Decimal('950')), # Estimación real de pasivos USD + neto USD
                'pasivos_total_usd': Decimal(row[2]) / Decimal('950'), # Aproximado
                'activos_liquidos_clp': Decimal(row[1]) * Decimal('0.1')
            }
        )

    # 7. Registros Mensuales (Image 1 - Selected Data)
    print("Cargando Registros Mensuales 2025/2026...")
    
    # Categorías específicas para registros
    cat_netflix = gasto('Netflix', 10, 'OCIO')
    cat_spotify = gasto('Spotify', 11, 'OCIO')
    cat_youtube = gasto('Youtube', 12, 'OCIO')
    cat_icloud = gasto('iCloud', 13, 'OCIO')

    meses_data = [
        # (Año, Mes, Salario, Arriendo E107, TDC World, Suscripciones)
        (2025, 11, 3961740, 356893, 22112159, 35000),
        (2025, 12, 3981950, 356893, 1752942, 35000),
        (2026, 1, 3935178, 356893, 1968862, 35000),
        (2026, 2, 3925646, 356893, 860826, 35000),
    ]

    # Categorías para inversiones
    cat_dva = gasto('DVA (Inversión)', 14)
    cat_fintual = gasto('Fintual (Inversión)', 15)

    for y, m, sal, arr, tdc, sus in meses_data:
        periodo = date(y, m, 1)
        siguiente = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        for cat, monto, direccion, naturaleza in [
            (cat_salario, sal, 'INGRESO', 'OPERACIONAL'), (cat_arriendo, arr, 'INGRESO', 'OPERACIONAL'),
            (cat_tdc, tdc, 'EGRESO', 'OPERACIONAL'), (cat_netflix, 10700, 'EGRESO', 'OPERACIONAL'),
            (cat_youtube, 9200, 'EGRESO', 'OPERACIONAL'), (cat_icloud, 8490, 'EGRESO', 'OPERACIONAL'),
            (cat_dva, 50000, 'EGRESO', 'INVERSION'), (cat_fintual, 50000, 'EGRESO', 'INVERSION'),
        ]:
            MovimientoManual.objects.update_or_create(
                user=user, periodo=periodo, categoria=cat, direccion=direccion, es_resumen_mensual=True,
                defaults={'fecha': siguiente - date.resolution, 'monto': Decimal(monto), 'monto_clp': Decimal(monto), 'moneda': 'CLP', 'naturaleza': naturaleza, 'descripcion': cat.nombre},
            )

    print("Carga de datos finalizada exitosamente.")

if __name__ == '__main__':
    run()
