"""
core/portfolio_calc.py
======================
Moteur de calcul du compte-titres : à partir de la liste des opérations
(dépôts, retraits, achats, ventes, dividendes), recalcule l'état du compte.

Principe : outil de SUIVI d'investissements, pas plateforme de trading. On peut donc
saisir une position sans avoir enregistré le moindre dépôt : le compte espèces est une
information complémentaire, jamais une contrainte. Il peut devenir négatif, ce qui
signifie simplement « capital investi non déclaré en dépôts ».

  - Le compte espèces est en EUR.
  - Une ligne est suivie dans SA devise (ex. Apple en USD) : quantité et PRU en devise.
  - Chaque achat / vente est converti en EUR au taux de change du jour de l'opération
    (champ `taux_eur` = nombre d'euros pour 1 unité de la devise).
  - PRU = prix moyen pondéré (méthode PMP, celle utilisée en France), hors frais.
  - Coût de revient en EUR = montants payés en EUR, frais inclus. C'est lui qui sert
    au calcul de la plus-value en EUR (qui intègre donc l'effet change).
  - Une vente crédite le cash et fige une plus-value réalisée (en devise et en EUR).

Aucune dépendance réseau : ce module est testable seul.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

EPS = 1e-9
TYPES_OPERATION = ("DEPOT", "RETRAIT", "ACHAT", "VENTE", "DIVIDENDE", "SPLIT")
TYPES_ORDRE     = ("ACHAT", "VENTE")                      # onglet « Ordres »
# Onglet « Mouvements ». Les versements et retraits d'espèces restent traités par le
# calcul — une base existante peut en contenir — mais ne sont plus saisissables ni
# affichés : l'application suit des investissements, pas un compte bancaire.
TYPES_MOUVEMENT = ("DIVIDENDE", "SPLIT")


class OperationInvalide(ValueError):
    """Opération impossible (cash insuffisant, vente de titres non détenus…)."""


@dataclass
class Position:
    ticker: str
    devise: str
    quantite: float = 0.0
    pru: float = 0.0            # prix de revient unitaire, en devise, hors frais
    cout_eur: float = 0.0       # coût de revient total en EUR, frais inclus
    pv_realisee_eur: float = 0.0
    pv_realisee_devise: float = 0.0
    dividendes_eur: float = 0.0
    date_entree: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "devise": self.devise,
            "quantite": round(self.quantite, 6), "pru": round(self.pru, 6),
            "cout_eur": round(self.cout_eur, 2),
            "pv_realisee_eur": round(self.pv_realisee_eur, 2),
            "pv_realisee_devise": round(self.pv_realisee_devise, 2),
            "dividendes_eur": round(self.dividendes_eur, 2),
            "date_entree": self.date_entree,
        }


@dataclass
class EtatCompte:
    cash_eur: float = 0.0
    depots_nets_eur: float = 0.0        # dépôts − retraits réellement saisis
    # Un achat qui dépasse les espèces disponibles suppose que l'argent est entré sur le
    # compte d'une façon ou d'une autre. Faute de dépôt saisi, on inscrit le complément
    # ici : c'est le capital venu de l'extérieur. Il sert de dénominateur à la
    # plus-value, et il évite surtout de compter deux fois l'euro qu'une vente a rendu
    # disponible et qu'un achat ultérieur a réemployé.
    apports_implicites_eur: float = 0.0
    frais_eur: float = 0.0
    dividendes_eur: float = 0.0
    pv_realisee_eur: float = 0.0
    cout_cede_eur: float = 0.0          # coût de revient des titres vendus (pour la perf globale)
    positions: Dict[str, Position] = field(default_factory=dict)   # lignes ouvertes ET soldées
    operations: List[dict] = field(default_factory=list)          # opérations enrichies

    def positions_ouvertes(self) -> List[Position]:
        return [p for p in self.positions.values() if p.quantite > EPS]

    def positions_soldees(self) -> List[Position]:
        return [p for p in self.positions.values()
                if p.quantite <= EPS and abs(p.pv_realisee_eur) > EPS]


def _cle_tri(op: dict):
    return (op.get("date") or "", op.get("id") or 0)


def rejouer(operations: List[dict], jusqu_au: Optional[str] = None) -> EtatCompte:
    """
    Rejoue les opérations dans l'ordre chronologique et renvoie l'état du compte.
    `jusqu_au` (YYYY-MM-DD, inclus) permet de reconstituer l'état à une date passée.
    Lève OperationInvalide si une opération est impossible à ce moment-là.
    """
    etat = EtatCompte()
    for op in sorted(operations, key=_cle_tri):
        if jusqu_au and op["date"] > jusqu_au:
            break
        _appliquer(etat, op)
    return etat


def _montant_obligatoire(op: dict, date: str) -> float:
    """
    Montant en euros d'une opération d'espèces. Une base ancienne, ou un import mal
    classé, peut porter une ligne sans montant : à défaut, on le reconstitue par
    quantité × prix unitaire, et on refuse proprement si même cela manque. Sans ce
    garde-fou, une seule ligne incomplète fait échouer tout le rejeu du compte — donc
    tous les écrans d'un coup, avec une erreur serveur illisible.
    """
    m = op.get("montant_eur")
    if m is None:
        q, p = op.get("quantite"), op.get("prix")
        if q is not None and p is not None:
            m = float(q) * float(p)
    if m is None:
        raise OperationInvalide(
            f"Opération « {op.get('type')} » du {date} sans montant : "
            f"impossible de l'appliquer au compte.")
    return float(m)


def _appliquer(etat: EtatCompte, op: dict) -> None:
    t = op["type"]
    date = op["date"]
    frais = float(op.get("frais_eur") or 0.0)
    enrichie = dict(op)

    if t == "DEPOT":
        m = _montant_obligatoire(op, date)
        etat.cash_eur += m
        etat.depots_nets_eur += m
        enrichie["flux_cash_eur"] = m

    elif t == "RETRAIT":
        m = _montant_obligatoire(op, date)
        etat.cash_eur -= m
        etat.depots_nets_eur -= m
        enrichie["flux_cash_eur"] = -m

    elif t == "ACHAT":
        tk, dev = op["ticker"], op["devise"]
        q, prix, taux = float(op["quantite"]), float(op["prix"]), float(op["taux_eur"])
        montant_eur = q * prix * taux
        cout = montant_eur + frais
        # Aucun contrôle de provision : on suit des investissements, on ne passe pas d'ordres.
        # L'achat se sert d'abord des espèces disponibles — celles d'une vente passée, par
        # exemple. Ce qui manque est forcément venu de l'extérieur : on l'enregistre comme
        # un versement, et le solde espèces ne devient jamais négatif.
        if etat.cash_eur < cout - EPS:
            manque = cout - etat.cash_eur
            etat.apports_implicites_eur += manque
            etat.cash_eur = cout
            enrichie["apport_implicite_eur"] = round(manque, 2)
        pos = etat.positions.get(tk)
        if pos is None or pos.quantite <= EPS:
            ancienne = pos
            pos = Position(ticker=tk, devise=dev, date_entree=date)
            if ancienne is not None:       # ligne déjà soldée puis rachetée : on garde l'historique réalisé
                pos.pv_realisee_eur = ancienne.pv_realisee_eur
                pos.pv_realisee_devise = ancienne.pv_realisee_devise
                pos.dividendes_eur = ancienne.dividendes_eur
            etat.positions[tk] = pos
        elif pos.devise != dev:
            raise OperationInvalide(
                f"{tk} est détenu en {pos.devise}, l'achat du {date} est saisi en {dev}.")
        nouvelle_q = pos.quantite + q
        pos.pru = (pos.pru * pos.quantite + prix * q) / nouvelle_q
        pos.quantite = nouvelle_q
        pos.cout_eur += cout
        etat.cash_eur -= cout
        etat.frais_eur += frais
        enrichie["flux_cash_eur"] = -cout
        enrichie["montant_brut_eur"] = round(montant_eur, 2)

    elif t == "VENTE":
        tk = op["ticker"]
        q, prix, taux = float(op["quantite"]), float(op["prix"]), float(op["taux_eur"])
        pos = etat.positions.get(tk)
        detenu = pos.quantite if pos else 0.0
        if q > detenu + 1e-6:
            raise OperationInvalide(
                f"Vente de {q:g} {tk} le {date} impossible : vous n'en détenez que {detenu:g} "
                f"à cette date.")
        q = min(q, detenu)
        produit_eur = q * prix * taux - frais
        cout_unitaire_eur = pos.cout_eur / pos.quantite
        pv_eur = produit_eur - cout_unitaire_eur * q
        pv_dev = (prix - pos.pru) * q
        pos.quantite -= q
        pos.cout_eur -= cout_unitaire_eur * q
        pos.pv_realisee_eur += pv_eur
        pos.pv_realisee_devise += pv_dev
        if pos.quantite <= EPS:            # ligne soldée
            pos.quantite, pos.cout_eur = 0.0, 0.0
        etat.cash_eur += produit_eur
        etat.frais_eur += frais
        etat.pv_realisee_eur += pv_eur
        etat.cout_cede_eur += cout_unitaire_eur * q
        enrichie["flux_cash_eur"] = produit_eur
        enrichie["pru_au_moment"] = round(pos.pru, 6)
        enrichie["pv_realisee_eur"] = round(pv_eur, 2)
        enrichie["pv_realisee_devise"] = round(pv_dev, 2)

    elif t == "SPLIT":
        # Division / regroupement d'actions : ratio 4 = 4 nouvelles actions pour 1 ancienne.
        # La valeur de la ligne ne change pas : la quantité est multipliée, le PRU divisé.
        tk, ratio = op["ticker"], float(op["quantite"])
        pos = etat.positions.get(tk)
        if ratio <= 0:
            raise OperationInvalide(f"Ratio de division invalide pour {tk} le {date}.")
        if pos and pos.quantite > EPS:
            pos.quantite *= ratio
            pos.pru /= ratio
        enrichie["flux_cash_eur"] = 0.0

    elif t == "DIVIDENDE":
        m = _montant_obligatoire(op, date)
        etat.cash_eur += m
        etat.dividendes_eur += m
        tk = op.get("ticker")
        if tk and tk in etat.positions:
            etat.positions[tk].dividendes_eur += m
        enrichie["flux_cash_eur"] = m

    else:
        raise OperationInvalide(f"Type d'opération inconnu : {t}")

    enrichie["cash_apres_eur"] = round(etat.cash_eur, 2)
    etat.operations.append(enrichie)


def valider_nouvelle_operation(existantes: List[dict], nouvelle: dict) -> EtatCompte:
    """Vérifie que l'historique reste cohérent si on ajoute `nouvelle` (même antidatée)."""
    return rejouer(existantes + [nouvelle])


def valider_suppression(existantes: List[dict], op_id: int) -> EtatCompte:
    """Vérifie que l'historique reste cohérent si on supprime l'opération `op_id`."""
    return rejouer([o for o in existantes if o.get("id") != op_id])
