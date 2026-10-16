"""
routes_banque.py
================
API du module « Budget et comptes » (compte courant, compte investissement, budget, dettes).
Même authentification que le compte-titres : en-tête « Authorization: Bearer <jeton> ».

  GET    /banque/etat     état du module ({"etat": null} si pas encore configuré)
  PUT    /banque/etat     enregistre l'état ; refusé (409) si la version envoyée est périmée
  DELETE /banque/etat     efface le module pour relancer la configuration
  POST   /banque/releves  lit des relevés PDF et renvoie une proposition d'import

La lecture des relevés ne modifie rien : elle renvoie ce qu'elle a compris, le
navigateur le fait relire à l'utilisateur, et seul l'enregistrement de l'état
écrit quoi que ce soit. Les PDF ne sont pas conservés.
"""

import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from core.banque_db import BanqueDB, ConflitVersion
from core.categorisation import categoriser
from core.habitudes import analyser
from core.releve import extraire
from routes_portefeuille import utilisateur_courant

logger = logging.getLogger("ecofolio.banque")

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


# ─────────────────────── Lecture de relevés bancaires ───────────────────────

TAILLE_MAX_PDF = 10 * 1024 * 1024      # 10 Mo : un relevé en fait quelques centaines de Ko
NB_MAX_FICHIERS = 24                   # deux ans de relevés mensuels


def _date(d) -> Optional[str]:
    return d.isoformat() if d else None


@router.post("/banque/releves")
async def lire_releves(
    fichiers: List[UploadFile] = File(...),
    user: str = Depends(utilisateur_courant),
):
    """Lit des relevés PDF et renvoie ce qui a été compris, sans rien enregistrer.

    La réponse contient, pour chaque fichier, le contrôle d'équilibre compte par
    compte — c'est lui qui dit si l'extraction est digne de confiance — puis une
    proposition d'import : opérations classées, tiers regroupés, récurrences et
    budgets déduits.
    """
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

    a = analyser(operations, periodes) if operations else None
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
            } for t in a["tiers"]],
            "operations": [{
                "date": _date(o.date), "libelle": o.libelle, "montant": round(o.montant, 2),
                "tiers": categoriser(o.libelle, o.montant).tiers,
            } for o in sorted(operations, key=lambda o: o.date)],
        }
    return {"fichiers": lus, "proposition": proposition}
