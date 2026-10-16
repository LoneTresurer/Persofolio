"""
routes_banque.py
================
API du module « Budget et comptes » (compte courant, compte investissement, budget, dettes).
Même authentification que le compte-titres : en-tête « Authorization: Bearer <jeton> ».

  GET    /banque/etat     état du module ({"etat": null} si pas encore configuré)
  PUT    /banque/etat     enregistre l'état ; refusé (409) si la version envoyée est périmée
  DELETE /banque/etat     efface le module pour relancer la configuration
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from core.banque_db import BanqueDB, ConflitVersion
from routes_portefeuille import utilisateur_courant

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
