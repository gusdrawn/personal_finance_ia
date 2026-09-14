from django.core.management.base import BaseCommand, CommandError
from altcoins.models import AltcoinProject, EvidenceRecord, ProjectEntityRelationship
from altcoins.services.research import register_relationship_candidate


class Command(BaseCommand):
    help = "Registra una evidencia institucional como relación candidata para revisión staff."

    def add_arguments(self, parser):
        parser.add_argument("--project", required=True, help="Slug del proyecto")
        parser.add_argument("--entity", required=True, help="Nombre canónico de la entidad")
        parser.add_argument("--relation", required=True, choices=[choice[0] for choice in ProjectEntityRelationship.RELATION_CHOICES])
        parser.add_argument("--url", required=True)
        parser.add_argument("--title", default="")
        parser.add_argument("--excerpt", default="")
        parser.add_argument("--tier", choices=[choice[0] for choice in EvidenceRecord.RELIABILITY_CHOICES], default="A_PRIMARY")

    def handle(self, *args, **options):
        project = AltcoinProject.objects.filter(slug=options["project"]).first()
        if not project:
            raise CommandError("Proyecto no encontrado")
        relation = register_relationship_candidate(
            project=project,
            entity_name=options["entity"],
            entity_type="UNKNOWN",
            relationship_type=options["relation"],
            source_url=options["url"],
            title=options["title"],
            excerpt=options["excerpt"],
            reliability_tier=options["tier"],
        )
        self.stdout.write(self.style.SUCCESS(f"Relación candidata registrada: {relation.pk}"))
