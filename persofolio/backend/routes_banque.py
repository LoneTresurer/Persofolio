"""
routes_banque.py
================
API du module « Budget et comptes » (compte courant, compte investissement, budget, dettes).
Même authentification que le compte-titres : en-tête « Authorization: Bearer <jeton> ».

  GET    /banque/etat     état du module ({"etat": null} si pas encore configuré)
  PUT    /banque/etat     enregistre l'état ; refusé (409) si la version envoyée est périmée
  DELETE /banque/etat     efface le module pour relancer la configuration
  POST   /banque/releves  lit des relevés PDF et renvoie une proposition d'import
  GET    /banque/investissement  espèces et prix de revient du compte-titres
  POST   /banque/virement      budget et compte-titres écrits en une transaction

La lecture des relevés ne modifie rien : elle renvoie ce qu'elle a compris, le
navigateur le fait relire à l'utilisateur, et seul l'enregistrement de l'état
écrit quoi que ce soit. Les PDF ne sont pas conservés.
"""

import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from core import passerelle
from core.banque_db import BanqueDB, ConflitVersion
from core.categorisation import categoriser
from core.habitudes import analyser
from core.releve import extraire
from routes_portefeuille import utilisateur_courant

logger = logging.getLogger("persofolio.banque")

router = APIRouter()
bdb = BanqueDB()

TAILLE_MAX = 2_000_000       # octets : large pour des années d'opérations, borne les abus


class EtatIn(BaseModel):
    etat: Dict[str, Any]
    version: int = Field(..., ge=0)   # version sur laquelle le navigateur a fondé ses modifications

    @field_validator("etat")
    @classmethod
    def _structure(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        manquants = [k for k in ("comptes", "operations", "programmees", "dettes", "objectifs", "budgets") if k not in v]
        if manquants:
            raise ValueError(f"état incomplet, clés manquantes : {', '.join(manquants)}")
        if len(str(v)) > TAILLE_MAX:
            raise ValueError("état trop volumineux")
        return v


@router.get("/banque/etat")
def lire_etat(user: str = Depends(utilisateur_courant)):
    etat, version = bdb.lire(user)
    return {"etat": etat, "version": version}


@router.put("/banque/etat")
def ecrire_etat(corps: EtatIn, user: str = Depends(utilisateur_courant)):
    corps.etat["utilisateur"] = user          # l'état appartient toujours à l'utilisateur du jeton
    try:
        version = bdb.ecrire(user, corps.etat, corps.version)
    except ConflitVersion as e:
        raise HTTPException(status_code=409, detail={
            "message": "Vos données ont été modifiées depuis un autre onglet ou appareil.",
            "version": e.version_actuelle})
    return {"version": version}


@router.delete("/banque/etat")
def effacer_etat(user: str = Depends(utilisateur_courant)):
    bdb.supprimer(user)
    return {"ok": True}


# ──────────────────── Jonction avec le compte-titres ────────────────────────
# Le compte investissement du budget n'est plus un compte à part : ce sont les
# espèces et les titres du compte-titres, vus depuis le budget. Deux routes
# suffisent — une pour lire, une pour virer.

class VirementIn(BaseModel):
    """Un virement entre le compte courant et le compte-titres.

    L'état du budget voyage avec le virement : les deux sont écrits ensemble, ou
    pas du tout. `id` rend l'envoi rejouable sans risque — un double-clic ou une
    reprise après erreur ne crée pas deux versements.
    """
    etat: Dict[str, Any]
    version: int = Field(ge=0)
    id: str = Field(min_length=6, max_length=64)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    montant_eur: float = Field(gt=0, le=10_000_000)
    sens: str = Field(default="DEPOT", pattern="^(DEPOT|RETRAIT)$")
    #: "budget" : l'argent vient du compte courant suivi ici, et compte dans la
    #: part de l'épargne investie. "externe" : héritage, autre banque, salaire
    #: versé ailleurs — l'argent entre dans le portefeuille mais n'a jamais fait
    #: partie de l'épargne observée, et reste donc hors du calcul.
    origine: str = Field(default="budget", pattern="^(budget|externe)$")
    #: Enregistrer malgré un avertissement. L'application décrit une situation,
    #: elle ne la corrige pas à la place de l'utilisateur.
    force: bool = False

    @field_validator("montant_eur")
    @classmethod
    def _arrondi(cls, v: float) -> float:
        return round(v, 2)


@router.get("/banque/investissement")
def lire_investissement(user: str = Depends(utilisateur_courant)):
    """Espèces, prix de revient et rapprochement — sans toucher au marché.

    Aucun de ces chiffres ne demande de cours, donc aucun ne demande internet :
    ils se déduisent des seules opérations enregistrées. C'est ce qui permet au
    budget d'afficher un patrimoine exact hors ligne, la valeur de marché des
    titres étant la seule chose à pouvoir manquer.
    """
    try:
        return passerelle.etat_investissement(user)
    except passerelle.PasserelleIndisponible as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/banque/virement")
def virer(corps: VirementIn, user: str = Depends(utilisateur_courant)):
    """Écrit le budget et le mouvement d'espèces du compte-titres en une transaction."""
    corps.etat["utilisateur"] = user
    avertissement = None
    if corps.sens == "RETRAIT":
        # L'application observe des finances, elle ne les commande pas. Un
        # rapatriement supérieur aux espèces connues n'est pas forcément une
        # erreur de l'utilisateur : il a pu vendre des titres sans l'enregistrer,
        # ou alimenter son compte ailleurs. On le dit, on ne refuse pas.
        dispo = passerelle.etat_investissement(user)["cash_eur"]
        if corps.montant_eur > dispo + 0.005 and not corps.force:
            raise HTTPException(status_code=409, detail={
                "code": "especes_insuffisantes",
                "cash_eur": dispo,
                "message": (
                    f"D'après ce que l'application connaît, votre compte-titres ne "
                    f"dispose que de {dispo:.2f} € en espèces. Si vous avez vendu des "
                    "titres sans encore l'enregistrer, saisissez la vente d'abord — "
                    "sinon vous pouvez enregistrer ce rapatriement tel quel.")})
        if corps.montant_eur > dispo + 0.005:
            avertissement = (
                "Rapatriement supérieur aux espèces connues : le solde du "
                "compte-titres indiquera un manque tant que les ventes "
                "correspondantes ne seront pas enregistrées.")
    try:
        r = passerelle.virer(user, corps.etat, corps.version, corps.id,
                             corps.date, corps.montant_eur, sens=corps.sens,
                             origine=corps.origine)
    except passerelle.ConflitVersion as e:
        raise HTTPException(status_code=409, detail={
            "message": "Vos données ont été modifiées depuis un autre onglet ou appareil.",
            "version": e.version_actuelle})
    except passerelle.PasserelleIndisponible as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if avertissement:
        r["avertissement"] = avertissement
    return r


# ─────────────────────── Lecture de relevés bancaires ───────────────────────

TAILLE_MAX_PDF = 10 * 1024 * 1024      # 10 Mo : un relevé en fait quelques centaines de Ko
NB_MAX_FICHIERS = 24                   # deux ans de relevés mensuels


def _date(d) -> Optional[str]:
    return d.isoformat() if d else None


@router.post("/banque/releves")
async def lire_releves(
    fichiers: List[UploadFile] = File(...),
    regles: Optional[str] = Form(None),
    user: str = Depends(utilisateur_courant),
):
    """Lit des relevés PDF et renvoie ce qui a été compris, sans rien enregistrer.

    La réponse contient, pour chaque fichier, le contrôle d'équilibre compte par
    compte — c'est lui qui dit si l'extraction est digne de confiance — puis une
    proposition d'import : opérations classées, tiers regroupés, récurrences et
    budgets déduits.
    """
    # Les corrections déjà faites par l'utilisateur : un commerçant classé une fois
    # l'est pour toujours, y compris dans les relevés importés plus tard.
    apprises: Dict[str, str] = {}
    if regles:
        try:
            brut = json.loads(regles)
            if isinstance(brut, dict):
                apprises = {str(k): str(v) for k, v in brut.items() if k and v}
        except (ValueError, TypeError):
            logger.warning("règles de catégorisation illisibles, ignorées")

    if len(fichiers) > NB_MAX_FICHIERS:
        raise HTTPException(400, f"{NB_MAX_FICHIERS} relevés au maximum par envoi.")

    lus, operations, periodes = [], [], []
    for f in fichiers:
        contenu = await f.read()
        if len(contenu) > TAILLE_MAX_PDF:
            lus.append({"nom": f.filename, "erreur": "fichier trop volumineux (10 Mo maximum)"})
            continue
        if not contenu[:5].startswith(b"%PDF"):
            lus.append({"nom": f.filename, "erreur": "ce fichier n'est pas un PDF"})
            continue
        chemin = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(contenu)
                chemin = tmp.name
            rel = extraire(chemin)
        except Exception as e:                       # un PDF illisible ne doit pas tuer l'envoi
            logger.warning("relevé illisible (%s) : %s", f.filename, e)
            lus.append({"nom": f.filename, "erreur": "relevé illisible"})
            continue
        finally:
            if chemin and os.path.exists(chemin):
                os.unlink(chemin)                    # le PDF n'est jamais conservé

        comptes = []
        for c in rel.comptes:
            comptes.append({
                "numero": c.numero, "libelle": c.libelle,
                "debut": _date(c.date_ouverture), "fin": _date(c.date_cloture),
                "solde_ouverture": c.solde_ouverture, "solde_cloture": c.solde_cloture,
                "mouvements": c.somme_mouvements, "nb_operations": len(c.operations),
                "ecart": c.ecart, "equilibre": c.equilibre,
                "operations": [
                    {"date": _date(o.date), "libelle": o.libelle, "montant": round(o.montant, 2)}
                    for o in c.operations
                ],
            })
            if c.equilibre and c.operations:
                operations.extend(c.operations)
                periodes.append((c.date_ouverture, c.date_cloture))
        lus.append({"nom": f.filename, "banque": rel.banque,
                    "comptes": comptes, "anomalies": rel.anomalies})

    a = analyser(operations, periodes, apprises) if operations else None
    proposition = None
    if a:
        proposition = {
            "mois_complets": a["mois_complets"],
            "mois_complets_ignores": a["mois_complets_ignores"],
            "budgets": a["budgets"],
            "mesure": a["mesure"],
            "recurrences": [r.proposition() for r in a["recurrences"]],
            "tiers": [{
                "cle": t.cle, "libelle": t.libelle, "categorie": t.categorie,
                "confiance": t.confiance, "sens": t.sens, "total": t.total,
                "occurrences": t.occurrences, "mois": t.mois,
                # De quoi proposer une opération programmée même pour un tiers que la
                # détection automatique n'a pas retenu : l'utilisateur peut le vouloir.
                "montant_type": abs(t.montant_type), "jour_type": t.jour_type,
                "stabilite": t.stabilite_montant, "ecart_jour": t.ecart_jour,
            } for t in a["tiers"]],
            "operations": [{
                "date": _date(o.date), "libelle": o.libelle, "montant": round(o.montant, 2),
                "tiers": categoriser(o.libelle, o.montant, apprises).tiers,
            } for o in sorted(operations, key=lambda o: o.date)],
        }
    return {"fichiers": lus, "proposition": proposition}
