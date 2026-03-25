from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import date, timedelta
from decimal import Decimal

from core.models import UserProfile, Periodo, AuditoriaChange
from configuracion.models import Banco, Producto, TipoCambio, ConfiguracionGeneral
from gastos.models import CategoriaIngreso, RegistroMensual, GastoProgramado
from patrimonio.models import Activo, Pasivo, SnapshotPatrimonio, MiniSesion, LineaMiniSesion
from departamentos.models import Departamento, Arrendatario, CreditoHipotecario, Estacionamiento
from inversiones.models import Inversion, HistorialInversion


class Command(BaseCommand):
    help = 'Populate database with sample financial data'

    def handle(self, *args, **options):
        self.stdout.write('Creating sample data...')
        
        # Create test user
        user, created = User.objects.get_or_create(
            username='demo_user',
            defaults={
                'email': 'demo@finanzas.cl',
                'first_name': 'Demo',
                'last_name': 'User'
            }
        )
        if created:
            user.set_password('demo1234')
            user.save()
            self.stdout.write(self.style.SUCCESS(f'✓ Created user: {user.username}'))

        # Create user profile
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={'timezone': 'America/Santiago', 'default_currency': 'CLP'}
        )
        self.stdout.write(self.style.SUCCESS(f'✓ Created user profile'))

        # Create banks
        banks_data = [
            {'nombre': 'Santander', 'orden': 1},
            {'nombre': 'BCI', 'orden': 2},
            {'nombre': 'ScotiaBank', 'orden': 3},
            {'nombre': 'Itaú', 'orden': 4},
            {'nombre': 'Tenpo', 'orden': 5},
        ]
        
        banks = {}
        for bank_data in banks_data:
            banco, _ = Banco.objects.get_or_create(
                nombre=bank_data['nombre'],
                defaults={'orden': bank_data['orden'], 'activo': True}
            )
            banks[banco.nombre] = banco

        self.stdout.write(self.style.SUCCESS(f'✓ Created {len(banks)} banks'))

        # Create bank products
        products_data = [
            ('Santander', 'American Express', 'TDC'),
            ('Santander', 'Visa Platinum', 'TDC'),
            ('Santander', 'WorldMember', 'TDC'),
            ('Santander', 'Línea de Crédito', 'LINEA_CREDITO'),
            ('BCI', 'BCI Visa', 'TDC'),
            ('BCI', 'BCI Mastercard', 'TDC'),
            ('BCI', 'Línea de Crédito BCI', 'LINEA_CREDITO'),
            ('ScotiaBank', 'ScotiaBank Visa', 'TDC'),
            ('ScotiaBank', 'Línea de Crédito Scotia', 'LINEA_CREDITO'),
            ('Itaú', 'Itaú Visa', 'TDC'),
            ('Itaú', 'Línea de Crédito Itaú', 'LINEA_CREDITO'),
        ]
        
        for banco_name, producto_name, tipo in products_data:
            Producto.objects.get_or_create(
                banco=banks[banco_name],
                nombre=producto_name,
                defaults={'tipo': tipo, 'orden': 0, 'activo': True}
            )

        self.stdout.write(self.style.SUCCESS(f'✓ Created bank products'))

        # Create type change rates
        today = date.today()
        for i in range(30):  # Last 30 days
            fecha = today - timedelta(days=i)
            TipoCambio.objects.get_or_create(
                fecha=fecha,
                fuente='mindicador.cl',
                defaults={
                    'uf': Decimal('37.5') + (i * Decimal('0.01')),
                    'dolar': Decimal('850.0') + (i * Decimal('0.5'))
                }
            )

        self.stdout.write(self.style.SUCCESS(f'✓ Created exchange rates (last 30 days)'))

        # Create configuration
        config, _ = ConfiguracionGeneral.objects.get_or_create(
            user=user,
            defaults={
                'banco_defecto_salario': banks['Santander'],
                'dia_recordatorio_alimentacion': 1,
                'actualizar_tc_automatico': True
            }
        )

        self.stdout.write(self.style.SUCCESS(f'✓ Created general configuration'))

        # Create expense categories
        categories_data = [
            ('Salario', 'INGRESO'),
            ('Arriendos Depto', 'INGRESO'),
            ('Otros Ingresos', 'INGRESO'),
            ('Móvil', 'GASTO_FIJO'),
            ('Gas', 'GASTO_FIJO'),
            ('Agua', 'GASTO_FIJO'),
            ('Internet', 'GASTO_FIJO'),
            ('Luz', 'GASTO_FIJO'),
            ('Tag', 'GASTO_FIJO'),
            ('Gasolina', 'GASTO_FIJO'),
            ('Netflix', 'SUSCRIPCION'),
            ('Microsoft Pass', 'SUSCRIPCION'),
            ('Seguros Vehículos', 'SEGURO'),
            ('Amex', 'TDC'),
            ('Visa Platinum', 'TDC'),
            ('WorldMember', 'TDC'),
        ]
        
        categories = {}
        for cat_name, cat_type in categories_data:
            banco_defecto = banks['Santander'] if cat_name == 'Salario' else None
            cat, _ = CategoriaIngreso.objects.get_or_create(
                user=user,
                nombre=cat_name,
                tipo=cat_type,
                defaults={'orden': 0, 'activo': True, 'banco_defecto': banco_defecto}
            )
            categories[cat_name] = cat

        self.stdout.write(self.style.SUCCESS(f'✓ Created {len(categories)} expense categories'))

        # Create 6 departments
        departments_data = [
            {'codigo': 'E107', 'piso': 'E1', 'metros_cuadrados': Decimal('65.5'), 
             'valor_compra_uf': Decimal('2800'), 'valor_actual_uf': Decimal('3200')},
            {'codigo': 'E205', 'piso': 'E2', 'metros_cuadrados': Decimal('72.3'),
             'valor_compra_uf': Decimal('2900'), 'valor_actual_uf': Decimal('3350')},
            {'codigo': 'E507', 'piso': 'E5', 'metros_cuadrados': Decimal('68.0'),
             'valor_compra_uf': Decimal('2750'), 'valor_actual_uf': Decimal('3150')},
            {'codigo': 'F107', 'piso': 'F1', 'metros_cuadrados': Decimal('75.0'),
             'valor_compra_uf': Decimal('3000'), 'valor_actual_uf': Decimal('3450')},
            {'codigo': 'F205', 'piso': 'F2', 'metros_cuadrados': Decimal('70.0'),
             'valor_compra_uf': Decimal('2850'), 'valor_actual_uf': Decimal('3300')},
            {'codigo': 'F405', 'piso': 'F4', 'metros_cuadrados': Decimal('78.5'),
             'valor_compra_uf': Decimal('3100'), 'valor_actual_uf': Decimal('3600')},
        ]
        
        departments = {}
        tenants_data = [
            ('Juan García', '12345678-9', Decimal('800000')),
            ('María López', '13456789-0', Decimal('850000')),
            ('Carlos Rodríguez', '14567890-1', Decimal('820000')),
            ('Ana Martínez', '15678901-2', Decimal('880000')),
            ('Pedro Sánchez', '16789012-3', Decimal('795000')),
            ('Isabel Gómez', '17890123-4', Decimal('910000')),
        ]
        
        for i, dept_data in enumerate(departments_data):
            depto, _ = Departamento.objects.get_or_create(
                user=user,
                codigo=dept_data['codigo'],
                defaults={
                    'piso': dept_data['piso'],
                    'metros_cuadrados': dept_data['metros_cuadrados'],
                    'valor_compra_uf': dept_data['valor_compra_uf'],
                    'valor_actual_uf': dept_data['valor_actual_uf'],
                    'activo': True
                }
            )
            departments[depto.codigo] = depto
            
            # Create tenant
            tenant_name, rut, arriendo = tenants_data[i]
            Arrendatario.objects.get_or_create(
                departamento=depto,
                defaults={
                    'nombre': tenant_name,
                    'rut': rut,
                    'fecha_inicio_contrato': date.today() - timedelta(days=365),
                    'monto_arriendo_clp': arriendo,
                    'activo': True
                }
            )
            
            # Create mortgage
            CreditoHipotecario.objects.get_or_create(
                departamento=depto,
                defaults={
                    'banco': banks['ScotiaBank'],
                    'monto_original_uf': dept_data['valor_compra_uf'] * Decimal('0.7'),
                    'tasa_anual': Decimal('4.5'),
                    'plazo_anos': 20,
                    'tipo_tasa': 'FIJA',
                    'dfl2': False,
                    'cuota_uf': Decimal('14.5'),
                    'saldo_actual_uf': dept_data['valor_compra_uf'] * Decimal('0.65'),
                    'cuotas_totales': 240,
                    'cuotas_pagadas': 60,
                    'activo': True
                }
            )

        self.stdout.write(self.style.SUCCESS(f'✓ Created 6 departments with tenants and mortgages'))

        # Create assets
        assets_data = [
            ('Efectivo Panama', 'EFECTIVO', 'LIQUIDO', Decimal('50000'), Decimal('100')),
            ('Crypto General', 'INVERSION', 'LIQUIDO', Decimal('500000'), Decimal('750')),
            ('SoyFocus', 'INVERSION', 'LIQUIDO', Decimal('200000'), Decimal('0')),
            ('Fintual', 'INVERSION', 'LIQUIDO', Decimal('150000'), Decimal('250')),
            ('Buda', 'INVERSION', 'LIQUIDO', Decimal('80000'), Decimal('150')),
            ('Préstamo Wilder', 'PRESTAMO_DADO', 'NO_LIQUIDO', Decimal('100000'), Decimal('0')),
            ('Préstamo Leonel', 'PRESTAMO_DADO', 'NO_LIQUIDO', Decimal('80000'), Decimal('0')),
        ]
        
        for nombre, tipo, liquidez, monto_clp, monto_usd in assets_data:
            Activo.objects.get_or_create(
                user=user,
                nombre=nombre,
                defaults={
                    'tipo': tipo,
                    'tipo_liquidez': liquidez,
                    'monto_clp': monto_clp,
                    'monto_usd': monto_usd
                }
            )

        self.stdout.write(self.style.SUCCESS(f'✓ Created assets'))

        # Create liabilities
        liabilities_data = [
            ('American Express', 'TDC', Decimal('150000'), Decimal('200')),
            ('Visa Platinum', 'TDC', Decimal('200000'), Decimal('350')),
            ('WorldMember', 'TDC', Decimal('100000'), Decimal('100')),
            ('Línea Crédito Santander', 'CREDITO_HIPOTECARIO', Decimal('300000'), Decimal('500')),
        ]
        
        for nombre, tipo, monto_clp, monto_usd in liabilities_data:
            Pasivo.objects.get_or_create(
                user=user,
                nombre=nombre,
                tipo=tipo,
                defaults={
                    'monto_clp': monto_clp,
                    'monto_usd': monto_usd
                }
            )

        self.stdout.write(self.style.SUCCESS(f'✓ Created liabilities'))

        # Create monthly records for this month
        current_year = today.year
        current_month = today.month
        
        monthly_records = [
            ('Salario', 'INGRESO', Decimal('5000000'), 'CLP'),
            ('Arriendos Depto', 'INGRESO', Decimal('5145000'), 'CLP'),
            ('Móvil', 'GASTO_FIJO', Decimal('35000'), 'CLP'),
            ('Gas', 'GASTO_FIJO', Decimal('45000'), 'CLP'),
            ('Agua', 'GASTO_FIJO', Decimal('28000'), 'CLP'),
            ('Internet', 'GASTO_FIJO', Decimal('55000'), 'CLP'),
            ('Luz', 'GASTO_FIJO', Decimal('120000'), 'CLP'),
            ('Gasolina', 'GASTO_FIJO', Decimal('200000'), 'CLP'),
            ('Netflix', 'SUSCRIPCION', Decimal('19000'), 'CLP'),
            ('Microsoft Pass', 'SUSCRIPCION', Decimal('12000'), 'CLP'),
            ('Amex', 'TDC', Decimal('450000'), 'CLP'),
            ('Visa Platinum', 'TDC', Decimal('380000'), 'CLP'),
        ]
        
        for cat_name, tipo, monto, moneda in monthly_records:
            if cat_name in categories:
                RegistroMensual.objects.get_or_create(
                    user=user,
                    year=current_year,
                    mes=current_month,
                    categoria=categories[cat_name],
                    defaults={
                        'monto': monto,
                        'moneda': moneda,
                        'tipo': tipo
                    }
                )

        self.stdout.write(self.style.SUCCESS(f'✓ Created monthly records'))

        # Create annual expenses as programmed
        annual_expenses = [
            ('Póliza Seguros Vehículo', Decimal('800000'), 'ANUAL'),
            ('Patente Auto', Decimal('250000'), 'ANUAL'),
            ('Revisión Técnica', Decimal('100000'), 'ANUAL'),
            ('Mantenimiento Edificio', Decimal('2000000'), 'TRIMESTRAL'),
            ('Revisión Computadores', Decimal('500000'), 'TRIMESTRAL'),
        ]
        
        for nombre, monto, frecuencia in annual_expenses:
            GastoProgramado.objects.get_or_create(
                user=user,
                nombre=nombre,
                defaults={
                    'monto': monto,
                    'fecha_inicio': today,
                    'frecuencia': frecuencia,
                    'activo': True
                }
            )

        self.stdout.write(self.style.SUCCESS(f'✓ Created programmed expenses'))

        # Create patrimony snapshot
        total_assets_clp = Decimal('1160000')
        total_liabilities_clp = Decimal('1150000')
        
        SnapshotPatrimonio.objects.get_or_create(
            user=user,
            fecha=today,
            defaults={
                'activos_total_clp': total_assets_clp,
                'activos_total_usd': Decimal('1400'),
                'pasivos_total_clp': total_liabilities_clp,
                'pasivos_total_usd': Decimal('1350'),
                'patrimonio_neto_clp': total_assets_clp - total_liabilities_clp,
                'patrimonio_neto_usd': Decimal('50'),
                'activos_liquidos_clp': Decimal('1010000')
            }
        )

        self.stdout.write(self.style.SUCCESS(f'✓ Created patrimony snapshot'))

        # Create investments
        investments_data = [
            ('Crypto General', 'CRIPTO', Decimal('500000'), Decimal('750')),
            ('SoyFocus', 'FONDO_MUTUO', Decimal('200000'), Decimal('0')),
            ('Fintual', 'FONDO_MUTUO', Decimal('150000'), Decimal('250')),
        ]
        
        for nombre, tipo, monto_clp, monto_usd in investments_data:
            inv, _ = Inversion.objects.get_or_create(
                user=user,
                nombre=nombre,
                defaults={
                    'tipo': tipo,
                    'monto_clp': monto_clp,
                    'monto_usd': monto_usd,
                    'porcentaje_cartera': Decimal('0'),
                    'activo': True
                }
            )
            
            # Add historical records for last 5 days
            for days_ago in range(5):
                HistorialInversion.objects.get_or_create(
                    inversion=inv,
                    fecha=today - timedelta(days=days_ago),
                    defaults={
                        'monto_clp': monto_clp + (Decimal(days_ago) * Decimal('10000')),
                        'monto_usd': monto_usd + (Decimal(days_ago) * Decimal('15')),
                    }
                )

        self.stdout.write(self.style.SUCCESS(f'✓ Created investments with history'))

        self.stdout.write(self.style.SUCCESS('✅ Sample data created successfully!'))
        self.stdout.write(f'\n📌 Test Credentials:')
        self.stdout.write(f'   Username: demo_user')
        self.stdout.write(f'   Password: demo1234')
