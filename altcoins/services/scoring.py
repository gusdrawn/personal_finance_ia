from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from altcoins.constants import FUNDAMENTAL_CRITERIA, POLICY


POINTS = {"PASS": Decimal("12.5"), "PARTIAL": Decimal("6.25"), "FAIL": Decimal("0")}


def calculate_fundamental_score(statuses: dict[str, str]) -> dict:
    """Calcula score y cobertura sin convertir ausencia de datos en un fallo."""
    known = [code for code, _label in FUNDAMENTAL_CRITERIA if statuses.get(code) in POINTS]
    points = sum((POINTS[statuses[code]] for code in known), Decimal("0"))
    possible = Decimal("12.5") * len(known)
    score = (points / possible * 100) if possible else Decimal("0")
    coverage = Decimal(len(known)) / Decimal(len(FUNDAMENTAL_CRITERIA)) * 100
    if not known:
        classification = "SIN_DATOS"
    elif coverage < POLICY.min_coverage_provisional:
        classification = "SIN_DATOS"
    elif score >= 80:
        classification = "TOP" if coverage >= POLICY.min_coverage_provisional else "PROVISIONAL"
    elif score >= 65:
        classification = "SOLID"
    elif score >= 50:
        classification = "SPECULATIVE"
    else:
        classification = "AVOID"
    if coverage < POLICY.min_coverage_complete and classification not in {"SIN_DATOS", "PROVISIONAL"}:
        classification = f"PROVISIONAL_{classification}"
    return {
        "score": score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "coverage_pct": coverage.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "classification": classification,
        "known_criteria": known,
    }


INSTITUTIONAL_AXES = (
    "ACCOUNTABILITY",
    "FUNDING_TRANSPARENCY",
    "CORROBORATION",
    "INSTITUTIONAL_FOOTPRINT",
    "CONTROL_TRANSPARENCY",
)


def calculate_institutional_score(statuses: dict[str, str]) -> dict:
    """Mide documentación y huella institucional; no mide rentabilidad ni riesgo."""
    known = [axis for axis in INSTITUTIONAL_AXES if statuses.get(axis) in POINTS]
    points = sum((Decimal("20") if statuses[axis] == "PASS" else Decimal("10") if statuses[axis] == "PARTIAL" else Decimal("0") for axis in known), Decimal("0"))
    possible = Decimal("20") * len(known)
    score = (points / possible * 100) if possible else Decimal("0")
    coverage = Decimal(len(known)) / Decimal(len(INSTITUTIONAL_AXES)) * 100
    return {
        "score": score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "coverage_pct": coverage.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "known_axes": known,
        "label": "SIN_DATOS" if not known else "DOCUMENTADO" if score >= 80 else "SUSTANCIAL" if score >= 60 else "PARCIAL" if score >= 40 else "LIMITADO",
    }


def trap_signals(assessments: dict[str, str]) -> list[str]:
    """Las trampas son advertencias independientes y nunca reducen el score."""
    signals = []
    if assessments.get("SUPPLY") == "FAIL":
        signals.append("DILUTION_RISK")
    if assessments.get("VALUE_CAPTURE") == "FAIL":
        signals.append("ZERO_VALUE_CAPTURE")
    if assessments.get("NARRATIVE") == "FAIL":
        signals.append("COOLED_HYPE")
    if assessments.get("ATH_DISCOUNT") == "FAIL":
        signals.append("TIMING_TRAP")
    return signals
