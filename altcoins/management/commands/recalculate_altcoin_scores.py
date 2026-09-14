from django.core.management.base import BaseCommand

from altcoins.services.score_snapshots import recalculate_all_scores


class Command(BaseCommand):
    help = "Recalcula scores fundamentales e institucionales con la política vigente."

    def handle(self, *args, **options):
        total = recalculate_all_scores()
        self.stdout.write(self.style.SUCCESS(f"Scores recalculados para {total} proyectos."))
