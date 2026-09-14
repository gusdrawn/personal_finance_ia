from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase

from altcoins.models import AltcoinProject, AltcoinWatchlist, InstitutionalEntity, ProjectEntityRelationship


class AltcoinModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alt_user", password="secret")
        self.other = User.objects.create_user(username="alt_other", password="secret")
        self.project = AltcoinProject.objects.create(name="Proyecto Uno", symbol="ONE", slug="proyecto-uno", in_universe=True)

    def test_watchlist_aislada_y_unica_por_usuario(self):
        AltcoinWatchlist.objects.create(user=self.user, project=self.project)
        AltcoinWatchlist.objects.create(user=self.other, project=self.project)
        self.assertEqual(AltcoinWatchlist.objects.filter(user=self.user).count(), 1)
        self.assertEqual(AltcoinWatchlist.objects.filter(user=self.other).count(), 1)
        with self.assertRaises(IntegrityError):
            AltcoinWatchlist.objects.create(user=self.user, project=self.project)

    def test_relacion_distingue_partner_de_inversor(self):
        entity = InstitutionalEntity.objects.create(canonical_name="Empresa X", normalized_name="empresa x", slug="empresa-x", entity_type="PRIVATE_COMPANY")
        relation = ProjectEntityRelationship.objects.create(project=self.project, entity=entity, relationship_type="COMMERCIAL_PARTNER")
        self.assertEqual(relation.get_relationship_type_display(), "Partner comercial")
        self.assertNotEqual(relation.relationship_type, "EQUITY_INVESTOR")
