"""
core/habitudes.py
=================
Ce que trois relevés disent des habitudes : les opérations qui reviennent tous
les mois, et ce que coûtent les autres.

Deux idées portent ce module.

**Regrouper par tiers, pas par ligne.** Trois cent soixante-dix opérations, c'est
illisible ; les mêmes réduites à leurs tiers, c'est une petite liste où les vingt
premiers pèsent l'essentiel des euros. L'utilisateur classe un tiers une fois, et
toutes ses lignes suivent — passées comme futures.

**Un mois incomplet ne compte pas dans une moyenne.** Les relevés suivent le cycle
de facturation de la banque, pas le calendrier : trois relevés Société Générale
du 10 au 10 couvrent quatre mois civils, dont aucun entièrement. Les moyennes ne
portent donc que sur les mois intégralement couverts, et le module dit lesquels.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from core.categorisation import INCONNU, categoriser


# ── Couverture du calendrier ────────────────────────────────────────────────
def fusionner_periodes(periodes: Sequence[Tuple[date, date]]) -> List[Tuple[date, date]]:
    """Réunit les périodes des relevés en intervalles continus (ils se chevauchent souvent)."""
    propres = sorted((a, b) for a, b in periodes if a and b and a <= b)
    fusion: List[Tuple[date, date]] = []
    for debut, fin in propres:
        if fusion and debut <= fusion[-1][1] + timedelta(days=1):
            fusion[-1] = (fusion[-1][0], max(fusion[-1][1], fin))
        else:
            fusion.append((debut, fin))
    return fusion


def _fin_de_mois(a: int, m: int) -> date:
    return date(a + (m == 12), 1 if m == 12 else m + 1, 1) - timedelta(days=1)


def derniere_serie(mois: List[str]) -> List[str]:
    """Ne garde que la dernière suite de mois consécutifs.

    Des relevés peuvent couvrir deux époques sans rapport — un compte suivi au
    printemps, repris deux ans plus tard. Faire une moyenne par-dessus le trou
    donnerait un « mois moyen » qui n'a jamais existé.
    """
    serie: List[str] = []
    for m in sorted(mois):
        if serie:
            a, mo = map(int, serie[-1].split("-"))
            suivant = f"{a + (mo == 12)}-{1 if mo == 12 else mo + 1:02d}"
            if m != suivant:
                serie = []
        serie.append(m)
    return serie


def mois_complets(periodes: Sequence[Tuple[date, date]]) -> List[str]:
    """Mois civils entièrement couverts, au format AAAA-MM. Seuls ceux-ci font une moyenne."""
    fusion = fusionner_periodes(periodes)
    complets = []
    for debut, fin in fusion:
        a, m = debut.year, debut.month
        while date(a, m, 1) <= fin:
            if date(a, m, 1) >= debut and _fin_de_mois(a, m) <= fin:
                complets.append(f"{a}-{m:02d}")
            a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return sorted(set(complets))


# ── Regroupement par tiers ──────────────────────────────────────────────────
@dataclass
class Tiers:
    """Toutes les opérations d'un même bénéficiaire ou commerçant."""
    cle: str
    libelle: str                       # le libellé d'origine le plus long, pour que l'humain reconnaisse
    categorie: str
    confiance: str
    sens: str                          # "SORTIE" ou "ENTREE"
    montants: List[float] = field(default_factory=list)
    dates: List[date] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(self.montants), 2)

    @property
    def occurrences(self) -> int:
        return len(self.montants)

    @property
    def mois(self) -> List[str]:
        return sorted({f"{d.year}-{d.month:02d}" for d in self.dates})

    @property
    def montant_type(self) -> float:
        return round(statistics.median(self.montants), 2)

    @property
    def jour_type(self) -> int:
        return int(statistics.median([d.day for d in self.dates]))

    @property
    def stabilite_montant(self) -> float:
        """Écart relatif des montants : 0 = toujours le même, 1 = très variable."""
        v = [abs(m) for m in self.montants]
        moy = statistics.fmean(v)
        if len(v) < 2 or moy == 0:
            return 0.0
        return round(statistics.pstdev(v) / moy, 3)

    @property
    def ecart_jour(self) -> float:
        j = [d.day for d in self.dates]
        return round(statistics.pstdev(j), 1) if len(j) > 1 else 0.0


@dataclass
class Recurrence:
    """Un tiers qui revient chaque mois : candidat à devenir une opération programmée."""
    tiers: Tiers
    reguliere: bool                    # montant stable → charge fixe ; sinon facture variable
    mois_couverts: int

    def proposition(self) -> dict:
        t = self.tiers
        return {
            "cle": t.cle, "libelle": t.libelle, "categorie": t.categorie,
            "sens": t.sens, "montant": abs(t.montant_type), "jour": t.jour_type,
            "occurrences": t.occurrences, "mois": t.mois,
            "reguliere": self.reguliere, "stabilite": t.stabilite_montant,
            "ecart_jour": t.ecart_jour,
        }


def regrouper(operations, regles_utilisateur: Optional[dict] = None) -> List[Tiers]:
    """Un Tiers par bénéficiaire, trié par poids décroissant : c'est l'ordre de relecture."""
    groupes: Dict[Tuple[str, str], Tiers] = {}
    for o in operations:
        c = categoriser(o.libelle, o.montant, regles_utilisateur)
        sens = "ENTREE" if o.montant > 0 else "SORTIE"
        cle = (c.tiers or "(sans libellé)", sens)   # un même tiers peut débiter et créditer
        t = groupes.get(cle)
        if t is None:
            t = groupes[cle] = Tiers(cle=c.tiers, libelle=o.libelle, categorie=c.categorie,
                                     confiance=c.confiance, sens=sens)
        if len(o.libelle) > len(t.libelle):
            t.libelle = o.libelle
        t.montants.append(o.montant)
        t.dates.append(o.date)
    return sorted(groupes.values(), key=lambda t: -abs(t.total))


# ── Récurrences ─────────────────────────────────────────────────────────────
def detecter_recurrences(tiers: List[Tiers], mois: List[str]) -> List[Recurrence]:
    """Un tiers présent dans au moins deux mois, à raison d'une fois par mois environ.

    On ne demande pas un montant constant : EDF ou une facture de téléphone varient
    d'un mois à l'autre tout en étant des charges fixes. C'est la régularité du
    rendez-vous qui compte ; la stabilité du montant ne sert qu'à distinguer une
    charge fixe (loyer) d'une facture variable, et à prévenir l'utilisateur.
    """
    n_mois = max(len(mois), 1)
    trouvees = []
    for t in tiers:
        mois_vus = [m for m in t.mois if m in mois] or t.mois
        if len(mois_vus) < 2:
            continue
        if t.occurrences / len(mois_vus) > 1.6:  # plusieurs passages par mois : une habitude, pas une échéance
            continue
        reguliere = t.stabilite_montant <= 0.12 and t.ecart_jour <= 4
        # Deux mois ne suffisent que si le rendez-vous est net (même montant, même jour).
        # Sinon un commerçant fréquenté pendant un voyage de deux mois passerait pour un abonnement.
        if len(mois_vus) < 3 and not (len(mois_vus) >= 2 and reguliere):
            continue
        trouvees.append(Recurrence(tiers=t, reguliere=reguliere, mois_couverts=len(mois_vus)))
    return sorted(trouvees, key=lambda r: -abs(r.tiers.montant_type))


# ── Budgets des dépenses courantes ──────────────────────────────────────────
def budgets(tiers: List[Tiers], recurrences: List[Recurrence], mois: List[str]) -> Dict[str, float]:
    """Moyenne mensuelle par catégorie, hors récurrences et sur les mois complets seulement.

    Les récurrences en sont exclues parce qu'elles deviendront des opérations
    programmées : les compter ici les ferait payer deux fois dans le plan mensuel.
    """
    if not mois:
        return {}
    exclus = {r.tiers.cle for r in recurrences}
    cumul: Dict[str, float] = defaultdict(float)
    for t in tiers:
        if t.cle in exclus or t.sens != "SORTIE" or t.categorie == INCONNU:
            continue
        for montant, d in zip(t.montants, t.dates):
            if f"{d.year}-{d.month:02d}" in mois:
                cumul[t.categorie] += abs(montant)
    return {c: round(v / len(mois), 2) for c, v in sorted(cumul.items()) if v > 0}


# ── Synthèse pour l'écran de relecture ──────────────────────────────────────
def analyser(operations, periodes, regles_utilisateur: Optional[dict] = None) -> dict:
    tiers = regrouper(operations, regles_utilisateur)
    tous_les_mois = mois_complets(periodes)
    mois = derniere_serie(tous_les_mois)
    rec = detecter_recurrences(tiers, mois)
    inconnus = [t for t in tiers if t.categorie == INCONNU]
    euros_inconnus = sum(abs(t.total) for t in inconnus)
    euros_total = sum(abs(t.total) for t in tiers) or 1.0

    # Combien de tiers faut-il relire pour expliquer 80 % des euros non reconnus ?
    cumul, a_relire = 0.0, 0
    for t in sorted(inconnus, key=lambda t: -abs(t.total)):
        if cumul >= 0.8 * euros_inconnus:
            break
        cumul += abs(t.total)
        a_relire += 1

    return {
        "mois_complets": mois,
        "mois_complets_ignores": [m for m in tous_les_mois if m not in mois],
        "tiers": tiers,
        "recurrences": rec,
        "budgets": budgets(tiers, rec, mois),
        "mesure": {
            "operations": sum(t.occurrences for t in tiers),
            "tiers": len(tiers),
            "tiers_inconnus": len(inconnus),
            "part_euros_inconnus": round(euros_inconnus / euros_total * 100, 1),
            "tiers_a_relire_pour_80pct": a_relire,
        },
    }
