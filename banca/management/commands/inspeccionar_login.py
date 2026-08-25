"""
Inspecciona la página de login del banco SIN ingresar credenciales.

Abre el navegador, navega al login y vuelca screenshot + HTML + lista de inputs (incluyendo
iframes) en ~/.bank_sync/debug, para afinar los selectores del connector cuando el sitio
cambia. Útil porque el login real solo se puede ver desde tu máquina.

    python manage.py inspeccionar_login --no-headless
    python manage.py inspeccionar_login --url https://www.santander.cl/personas
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Abre el login del banco y vuelca inputs/screenshot/HTML para depurar selectores."

    def add_arguments(self, parser):
        parser.add_argument("--conector", type=str, default="santander")
        parser.add_argument("--url", type=str, default=None, help="URL de login a inspeccionar (opcional)")
        parser.add_argument("--no-headless", action="store_true", help="Mostrar el navegador")

    def handle(self, *args, **opts):
        from bank_sync.registry import get_connector

        connector = get_connector(opts["conector"], headless=not opts["no_headless"])
        try:
            connector._ensure_page()  # type: ignore[attr-defined]
            from bank_sync.connectors import santander as s

            url = opts["url"] or s.LOGIN_URL
            self.stdout.write(f"→ Navegando a {url} ...")
            connector._goto(url)  # type: ignore[attr-defined]
            connector._page.wait_for_timeout(2500)  # type: ignore[attr-defined]
            # intentar abrir el panel de ingreso si está colapsado
            try:
                import re

                connector._page.get_by_role(  # type: ignore[attr-defined]
                    "link", name=re.compile("Ingresar", re.I)
                ).first.click(timeout=4000)
                connector._page.wait_for_timeout(2000)  # type: ignore[attr-defined]
            except Exception:
                pass
            base = connector._dump_debug("inspeccion")  # type: ignore[attr-defined]

            # imprimir los inputs encontrados
            campos = []
            for fr in connector._page.frames:  # type: ignore[attr-defined]
                try:
                    fi = fr.eval_on_selector_all(
                        "input",
                        "els=>els.map(e=>({name:e.name,id:e.id,type:e.type,placeholder:e.placeholder}))",
                    )
                except Exception:
                    fi = []
                for f in fi:
                    f["frame"] = fr.url[:70]
                    campos.append(f)
            self.stdout.write(self.style.SUCCESS(f"Inputs detectados: {len(campos)}"))
            for f in campos:
                self.stdout.write("  " + str({k: v for k, v in f.items() if v}))
            if base:
                self.stdout.write(self.style.SUCCESS(f"Artefactos: {base}.png / .html / .inputs.json"))
        finally:
            connector.logout()
