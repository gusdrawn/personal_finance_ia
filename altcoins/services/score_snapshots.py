import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from altcoins.models import AltcoinProject, AltcoinScoreSnapshot, InstitutionalAssessment

from .institutional_scoring import assess_project_institutional_footprint
from .scoring import FUNDAMENTAL_CRITERIA, calculate_fundamental_score


@transaction.atomic
def recalculate_project_score(project):
    statuses = {code: "UNKNOWN" for code, _label in FUNDAMENTAL_CRITERIA}
    statuses.update(project.criterion_assessments.values_list("criterion_code", "status"))
    result = calculate_fundamental_score(statuses)
    AltcoinScoreSnapshot.objects.create(
        project=project,
        fundamental_score=result["score"],
        coverage_pct=result["coverage_pct"],
        classification=result["classification"],
        criteria_snapshot=statuses,
    )
    institutional = assess_project_institutional_footprint(project)
    institutional_snapshot = json.loads(json.dumps(institutional, cls=DjangoJSONEncoder))
    InstitutionalAssessment.objects.create(
        project=project,
        institutional_score=institutional["score"],
        coverage_pct=institutional["coverage_pct"],
        breakdown=institutional_snapshot,
    )
    return result


@transaction.atomic
def recalculate_all_scores():
    """Persiste una foto auditable de ambos scores para todo el universo."""
    total = 0
    for project in AltcoinProject.objects.filter(active=True, in_universe=True):
        recalculate_project_score(project)
        total += 1
    return total
