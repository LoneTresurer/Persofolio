"""
core/a_calculator.py
Calcul du coefficient A d'aversion au risque (Arrow-Pratt 1964 / Holt & Laury 2002).
Echelle de sortie : A in [1.0, 10.0]
  A = 1  → Très Agressif
  A = 10 → Très Conservateur

CALIBRATION DES BORNES :
  A_BRUT_MIN = 1.95  → profil le plus agressif observable (tout A, toutes loteries en A)
  A_BRUT_MAX = 30.53 → profil le plus conservateur observable (tout loteries en B, had_loss)
  Ces bornes sont calibrées empiriquement sur les profils extrêmes théoriques.
  Pour recalibrer, utiliser recalibrer_bornes() avec un jeu de réponses réelles.
"""
import math
from typing import List

# Scénarios de loteries Arrow-Pratt (gain_certain_pct, gain_risqué_pct)
SCENARIOS = [(3, 8), (5, 14), (8, 22), (12, 32), (18, 50)]

# Bornes de rescaling — à recalibrer sur données réelles utilisateurs
A_BRUT_MIN: float = 1.95
A_BRUT_MAX: float = 30.53


def calculer_A_markowitz(
    att1: int, att2: int, att3: int, att4: int,
    tolerance: int, invested: bool, had_loss: bool,
    choices: List[str]
) -> float:
    """
    Transforme les réponses du questionnaire en coefficient A in [1.0, 10.0].
    4 étapes :
      1. Score psychologique (attitudes Likert)
      2. Tolérance déclarée + ajustements expérience
      3. A implicite des loteries Arrow-Pratt (Holt & Laury 2002)
      4. Pondération par cohérence + rescaling [1, 10]
    """
    # Étape 1 — Attitudes Likert
    score_att = (6 - att1) + att2 + att3 + att4      # [4, 20]
    A_att = 1 + ((score_att - 4) / 16) * 99           # [1, 100]

    # Étape 2 — Tolérance déclarée
    A_tol = 1 + ((10 - tolerance) / 9) * 99           # [1, 100]
    A_psy = (A_att + A_tol) / 2
    if invested:
        A_psy *= 0.80   # expérience réduit l'aversion perçue
    if had_loss:
        A_psy *= 1.20   # perte passée augmente l'aversion
    A_psy = max(1.0, A_psy)

    # Étape 3 — Loteries Arrow-Pratt
    choices_up = [c.upper() for c in choices]
    ind = next((i for i, c in enumerate(choices_up) if c == "B"), len(SCENARIOS) // 2)
    C_p, G_p = SCENARIOS[ind]
    C, G = C_p / 100, G_p / 100
    E   = 0.5 * G
    var = 0.5 * (G - E) ** 2 + 0.5 * (0 - E) ** 2
    sig = math.sqrt(var)
    A_sc = (2 * (E - C) / var) if (sig > 1e-9 and abs(E - C) > 1e-5) else A_psy * 2

    # Étape 4 — Cohérence & pondération
    sw = sum(
        1 for i in range(1, len(choices_up))
        if (choices_up[i] == "B") != (choices_up[i - 1] == "B")
    )
    n    = len(choices_up)
    cons = max(0.0, 1 - (sw - 1) / (n - 2)) if n > 2 else 1.0
    ws   = 0.20 + 0.60 * cons
    A_brut = max(1.0, ws * A_sc + (1 - ws) * A_psy)

    # Rescaling calibré sur les profils extrêmes observés
    A_final = 1.0 + (A_brut - A_BRUT_MIN) / (A_BRUT_MAX - A_BRUT_MIN) * 9.0
    return round(max(1.0, min(10.0, A_final)), 2)


def recalibrer_bornes(responses_brutes: List[float]) -> dict:
    """
    Recalibre A_BRUT_MIN et A_BRUT_MAX à partir d'un jeu de réponses réelles.
    À appeler périodiquement avec les données utilisateurs accumulées.

    Args:
        responses_brutes: liste de valeurs A_brut calculées avant rescaling

    Returns:
        dict avec les nouvelles bornes recommandées et statistiques
    """
    import numpy as np
    if len(responses_brutes) < 10:
        return {"erreur": "Minimum 10 réponses requises pour recalibration"}
    arr = np.array(responses_brutes)
    return {
        "n":               len(arr),
        "A_BRUT_MIN_rec":  round(float(np.percentile(arr, 2)), 2),
        "A_BRUT_MAX_rec":  round(float(np.percentile(arr, 98)), 2),
        "moyenne":         round(float(arr.mean()), 2),
        "mediane":         round(float(np.median(arr)), 2),
        "note": "Mettre à jour A_BRUT_MIN et A_BRUT_MAX avec ces valeurs si n >= 100",
    }


def interpreter_A(A: float) -> dict:
    """
    Retourne le profil de l'investisseur selon le coefficient A.
    A faible → portefeuille concentré sur les meilleures opportunités ESG.
    A élevé  → portefeuille très diversifié, volatilité minimale.
    Contrainte UCITS : max 10%-15% par titre dans tous les cas.
    """
    if A <= 2:
        return {
            "A": A,
            "label": "Très Agressif",
            "description": (
                "Portefeuille concentré sur les meilleures actions ESG à fort rendement. "
                "Forte tolérance aux fluctuations de marché."
            ),
            "note_optimisation": "A faible → 5-10 titres à fort rendement, poids max 15%",
        }
    if A <= 4:
        return {
            "A": A,
            "label": "Agressif-Modéré",
            "description": (
                "Bonne tolérance au risque. "
                "Portefeuille d'actions ESG avec diversification sectorielle modérée."
            ),
            "note_optimisation": "A modéré-bas → 10-20 titres, poids max 12%",
        }
    if A <= 6:
        return {
            "A": A,
            "label": "Modéré-Prudent",
            "description": (
                "Équilibre rendement/risque. "
                "Portefeuille d'actions ESG bien diversifié, proche du profil de marché."
            ),
            "note_optimisation": "A médian → 20-30 titres, diversification sectorielle équilibrée",
        }
    if A <= 8:
        return {
            "A": A,
            "label": "Prudent",
            "description": (
                "Priorité à la stabilité. "
                "L'optimiseur pénalise fortement la variance du portefeuille ESG."
            ),
            "note_optimisation": "A élevé → 25-35 titres, poids plus uniformes, faible volatilité",
        }
    return {
        "A": A,
        "label": "Très Conservateur",
        "description": (
            "Minimisation du risque prioritaire. "
            "Portefeuille ESG très diversifié et défensif."
        ),
        "note_optimisation": "A très élevé → portefeuille quasi-équipondéré, volatilité minimale",
    }
