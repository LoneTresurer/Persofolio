"""
routes_portefeuille.py
======================
API du suivi de portefeuille (façon compte-titres de courtier).

Connexion
  POST   /auth/inscription          créer un compte          → jeton
  POST   /auth/connexion            se connecter             → jeton
  POST   /auth/deconnexion
Titres
  GET    /titres/{ticker}           fiche + dernier cours (aperçu avant un ordre)
Compte (en-tête « Authorization: Bearer <jeton> » obligatoire)
  GET    /compte/synthese           cash, positions valorisées, PnL, répartitions
  GET    /compte/operations         carnet d'ordres et mouvements
  POST   /compte/operations         passer un ordre / enregistrer un mouvement
  DELETE /compte/operations/{id}    annuler une opération saisie par erreur
  GET    /compte/historique         valorisation et PnL jour par jour
  GET    /compte/risque             indicateurs de risque du portefeuille actuel
  GET    /compte/actualites         actualités des titres détenus
  POST   /compte/dividendes/synchroniser   détecte et crédite les dividendes (Yahoo Finance)
  GET    /compte/notifications      notifications (dividendes versés…)
  GET|PUT /compte/parametres        fiscalité des dividendes, indice de comparaison
  GET    /compte/performances       performances cumulées et annualisées par période
  POST   /compte/import/apercu      lit un export de courtier (CSV) et propose les tickers
  POST   /compte/import/valider     enregistre les positions validées par l'utilisateur
"""

import asyncio
import logging
import time
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import List, Optional

import httpx
import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from agents import market_data as md
from agents.news_agent import NewsAgent
from core import portfolio_calc as calc
from core.import_csv import ImportInvalide, lire_positions
from core.import_pdf import lire_releve_pdf
from core.portfolio_db import PortfolioDB

logger = logging.getLogger("portefeuille")
router = APIRouter()
pdb = PortfolioDB()

# Valeur de repli seulement : le taux sans risque est un réglage de l'utilisateur,
# car le ratio de Sharpe en dépend directement et on doit pouvoir en citer la source.
TAUX_SANS_RISQUE_DEFAUT = 2.0


def _fr(x: float, n: int = 2) -> str:
    """Nombre au format français : 1 234,56"""
    return f"{x:,.{n}f}".replace(",", "\u202f").replace(".", ",")


def _frg(x: float) -> str:
    """Nombre court au format français (0,26 ; 10 ; 2,5)"""
    return f"{x:g}".replace(".", ",")


def _r(x, n=2):
    """Arrondi tolérant (None / NaN → None) pour des réponses JSON propres."""
    try:
        if x is None or not np.isfinite(float(x)):
            return None
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


# ─────────────────────────── Connexion ──────────────────────────────────────

class Identifiants(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    mdp: str = Field(..., min_length=6, max_length=100)

    @field_validator("username")
    @classmethod
    def _nettoyer(cls, v: str) -> str:
        return v.strip()


def utilisateur_courant(authorization: Optional[str] = Header(default=None)) -> str:
    jeton = (authorization or "").removeprefix("Bearer ").strip()
    user = pdb.utilisateur_du_jeton(jeton) if jeton else None
    if not user:
        raise HTTPException(status_code=401, detail="Session expirée : reconnectez-vous.")
    return user


def portefeuille_courant(user: str = Depends(utilisateur_courant),
                         x_portefeuille: Optional[int] = Header(default=None)) -> int:
    """
    Portefeuille sur lequel porte la requête.

    Le client indique lequel par l'en-tête X-Portefeuille. Sans précision, ou si
    l'identifiant ne lui appartient pas, on retombe sur son premier portefeuille —
    jamais sur celui d'un autre utilisateur.
    """
    if x_portefeuille is not None and pdb.portefeuille_valide(user, x_portefeuille):
        return x_portefeuille
    return pdb.portefeuille_par_defaut(user)


# ─────────────────────────── Portefeuilles ──────────────────────────────────

class PortefeuilleIn(BaseModel):
    nom: str = Field(..., min_length=1, max_length=60)

    @field_validator("nom")
    @classmethod
    def _nom(cls, v: str) -> str:
        v = " ".join((v or "").split())
        if not v:
            raise ValueError("le nom ne peut pas être vide")
        return v


@router.get("/portefeuilles")
def lister_portefeuilles(user: str = Depends(utilisateur_courant)):
    """Portefeuilles de l'utilisateur. Le premier est créé automatiquement au besoin."""
    liste = pdb.portefeuilles(user)
    if not liste:
        pdb.portefeuille_par_defaut(user)
        liste = pdb.portefeuilles(user)
    return {"portefeuilles": liste, "defaut": liste[0]["id"] if liste else None}


@router.post("/portefeuilles")
def creer_portefeuille(p: PortefeuilleIn, user: str = Depends(utilisateur_courant)):
    pid = pdb.creer_portefeuille(user, p.nom)
    if pid is None:
        raise HTTPException(409, detail=f"Vous avez déjà un portefeuille nommé « {p.nom} ».")
    return {"id": pid, "nom": p.nom}


@router.put("/portefeuilles/{pid}")
def renommer_portefeuille(pid: int, p: PortefeuilleIn, user: str = Depends(utilisateur_courant)):
    if not pdb.portefeuille_valide(user, pid):
        raise HTTPException(404, detail="Portefeuille introuvable.")
    if not pdb.renommer_portefeuille(user, pid, p.nom):
        raise HTTPException(409, detail=f"Vous avez déjà un portefeuille nommé « {p.nom} ».")
    _cache_serie.pop((user, pid), None)
    return {"id": pid, "nom": p.nom}


@router.delete("/portefeuilles/{pid}")
def supprimer_portefeuille(pid: int, user: str = Depends(utilisateur_courant)):
    """Supprime un portefeuille et toutes ses opérations. Le dernier ne peut pas l'être."""
    if not pdb.portefeuille_valide(user, pid):
        raise HTTPException(404, detail="Portefeuille introuvable.")
    if len(pdb.portefeuilles(user)) <= 1:
        raise HTTPException(409, detail="Impossible de supprimer votre seul portefeuille.")
    pdb.supprimer_portefeuille(user, pid)
    _cache_serie.pop((user, pid), None)
    return {"supprime": pid}


@router.post("/auth/local")
def ouverture_locale():
    """Ouvre la session sans identifiant : l'application est locale et mono-utilisateur.

    Le compte existant est repris tel quel — les positions, opérations et portefeuilles
    déjà saisis restent les vôtres. Sur une installation neuve, un compte est créé.
    """
    nom = pdb.utilisateur_local()
    return {"username": nom, "jeton": pdb.creer_jeton(nom)}


@router.post("/auth/inscription")
def inscription(ids: Identifiants):
    if not pdb.creer_utilisateur(ids.username, ids.mdp):
        raise HTTPException(status_code=409, detail="Ce nom d'utilisateur existe déjà.")
    return {"username": ids.username, "jeton": pdb.creer_jeton(ids.username)}


@router.post("/auth/connexion")
def connexion(ids: Identifiants):
    if not pdb.verifier_mdp(ids.username, ids.mdp):
        raise HTTPException(status_code=401, detail="Identifiant ou mot de passe incorrect.")
    return {"username": ids.username, "jeton": pdb.creer_jeton(ids.username)}


@router.post("/auth/deconnexion")
def deconnexion(authorization: Optional[str] = Header(default=None)):
    pdb.supprimer_jeton((authorization or "").removeprefix("Bearer ").strip())
    return {"ok": True}


# ─────────────────────────── Titres ─────────────────────────────────────────

async def _fiche(ticker: str, avec_prix: bool = False) -> Optional[dict]:
    """Fiche du titre depuis le cache local, sinon Yahoo Finance (puis mise en cache)."""
    fiche = pdb.fiche_titre(ticker)
    if fiche and not avec_prix:
        return fiche
    yahoo = await asyncio.to_thread(md.fiche_yahoo, ticker)
    if yahoo:
        pdb.enregistrer_fiche(yahoo)
        return yahoo
    return fiche


# Trois familles, celles d'un relevé de courtier français. Yahoo ne connaît que ses
# propres libellés (quoteType) : on les ramène ici. Un ETF est un OPC indiciel coté,
# il rejoint donc les fonds plutôt que les actions. Défaut « Action » : c'est le cas
# de loin le plus fréquent, et un titre mal classé reste visible, jamais perdu.
CLASSES_ACTIF = {
    "EQUITY": "Action", "EQUITYINDEX": "Action",
    "ETF": "OPC", "MUTUALFUND": "OPC", "FUND": "OPC", "MONEYMARKET": "OPC",
    "BOND": "Obligation", "CORPORATEBOND": "Obligation", "GOVERNMENTBOND": "Obligation",
}
ORDRE_CLASSES = ("Action", "OPC", "Obligation")


def _classe_actif(type_yahoo: str | None) -> str:
    return CLASSES_ACTIF.get((type_yahoo or "").upper(), "Action")


@router.get("/titres/{ticker}")
async def titre(ticker: str):
    tk = ticker.strip().upper()
    fiche = await _fiche(tk, avec_prix=True)
    if not fiche:
        raise HTTPException(status_code=404,
                            detail=f"Titre « {tk} » introuvable sur Yahoo Finance. "
                                   f"Format attendu : MC.PA, AAPL, SAP.DE, ASML.AS…")
    fiche["taux_eur"] = _r(await asyncio.to_thread(md.taux_eur, fiche["devise"]), 6)
    est_etf = (fiche.get("type_actif") or "").upper() in ("ETF", "MUTUALFUND")
    fiche["zone"] = "Diversifié (ETF)" if est_etf else md.zone_de(fiche.get("pays"))
    fiche["secteur"] = "ETF" if est_etf else md.secteur_fr(fiche.get("secteur"))
    return fiche


@router.get("/titres/{ticker}/historique")
async def historique_titre(ticker: str, periode: str = "1a"):
    """Cours d'un titre, à l'intervalle réellement publié par Yahoo (horaire sur 5 j et 1 mois)."""
    tk = ticker.strip().upper()
    serie = await asyncio.to_thread(md.historique_titre, tk, periode)
    if serie.empty:
        raise HTTPException(404, detail=f"Pas d'historique disponible pour {tk} sur cette période.")
    horaire = md.PERIODES.get(periode, ("", "1d"))[1].endswith("m")
    fmt = "%Y-%m-%d %H:%M" if horaire else "%Y-%m-%d"
    fiche = pdb.fiche_titre(tk) or {}
    debut, fin = float(serie.iloc[0]), float(serie.iloc[-1])
    return {"ticker": tk, "nom": fiche.get("nom") or tk, "devise": fiche.get("devise") or "EUR",
            "periode": periode, "granularite": "heure" if horaire else "jour",
            "points": [{"t": d.strftime(fmt), "prix": _r(float(v), 4)} for d, v in serie.items()],
            "perf_pct": _r((fin / debut - 1) * 100 if debut else None)}


# ─────────────────────────── Opérations ─────────────────────────────────────

class OperationIn(BaseModel):
    type: str
    date: str = Field(default_factory=lambda: date.today().isoformat())
    ticker: Optional[str] = None
    quantite: Optional[float] = Field(default=None, gt=0)
    prix: Optional[float] = Field(default=None, gt=0)
    frais_eur: float = Field(default=0.0, ge=0)
    montant_eur: Optional[float] = Field(default=None, gt=0)
    taux_eur: Optional[float] = Field(default=None, gt=0)   # facultatif : forcé par l'utilisateur
    commentaire: Optional[str] = Field(default=None, max_length=200)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in calc.TYPES_OPERATION:
            raise ValueError(f"type doit être parmi {', '.join(calc.TYPES_OPERATION)}")
        return v

    @field_validator("date")
    @classmethod
    def _date(cls, v: str) -> str:
        d = datetime.strptime(v, "%Y-%m-%d").date()
        if d > date.today():
            raise ValueError("la date ne peut pas être dans le futur")
        return d.isoformat()


async def _preparer_operation(op: "OperationIn", user: str) -> dict:
    """Complète une saisie : ticker normalisé, devise et taux de change du jour de l'opération."""
    nouvelle = op.model_dump()

    if op.type in ("ACHAT", "VENTE"):
        if not (op.ticker and op.quantite and op.prix):
            raise HTTPException(422, detail="Ticker, quantité et prix sont obligatoires.")
        tk = op.ticker.strip().upper()
        fiche = await _fiche(tk)
        if not fiche:
            raise HTTPException(404, detail=f"Titre « {tk} » introuvable sur Yahoo Finance.")
        nouvelle["ticker"] = tk
        nouvelle["devise"] = fiche.get("devise") or "EUR"
        if not op.taux_eur:
            nouvelle["taux_eur"] = await asyncio.to_thread(md.taux_eur, nouvelle["devise"], op.date)
        nouvelle["montant_eur"] = None
    else:
        if not op.montant_eur:
            raise HTTPException(422, detail="Le montant est obligatoire.")
        nouvelle.update(quantite=None, prix=None, devise="EUR", taux_eur=1.0)
        nouvelle["ticker"] = op.ticker.strip().upper() if (op.type == "DIVIDENDE" and op.ticker) else None
    return nouvelle


@router.post("/compte/operations")
async def ajouter_operation(op: OperationIn, user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    nouvelle = await _preparer_operation(op, user)
    try:
        calc.valider_nouvelle_operation(pdb.operations(user, pf), {**nouvelle, "id": 10**12})
    except calc.OperationInvalide as e:
        raise HTTPException(422, detail=str(e))
    op_id = pdb.ajouter_operation(user, pf, nouvelle)
    return {"id": op_id, "operation": nouvelle}


@router.put("/compte/operations/{op_id}")
async def modifier_operation(op_id: int, op: OperationIn, user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    """Corrige une saisie : l'opération est remplacée, puis tout l'historique est revérifié."""
    existantes = pdb.operations(user, pf)
    if not any(o["id"] == op_id for o in existantes):
        raise HTTPException(404, detail="Opération introuvable.")
    nouvelle = await _preparer_operation(op, user)
    autres = [o for o in existantes if o["id"] != op_id]
    try:
        calc.valider_nouvelle_operation(autres, {**nouvelle, "id": op_id})
    except calc.OperationInvalide as e:
        raise HTTPException(422, detail=str(e))
    pdb.remplacer_operation(user, pf, op_id, nouvelle)
    return {"id": op_id, "operation": nouvelle}


@router.delete("/compte/operations/{op_id}")
def supprimer_operation(op_id: int, user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    existantes = pdb.operations(user, pf)
    if not any(o["id"] == op_id for o in existantes):
        raise HTTPException(404, detail="Opération introuvable.")
    try:
        calc.valider_suppression(existantes, op_id)
    except calc.OperationInvalide as e:
        raise HTTPException(422, detail=f"Suppression impossible, l'historique deviendrait "
                                        f"incohérent : {e}")
    pdb.supprimer_operation(user, pf, op_id)
    return {"ok": True}


@router.get("/compte/operations")
def carnet(user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    etat = calc.rejouer(pdb.operations(user, pf))
    noms = {}
    for o in etat.operations:
        if o.get("ticker") and o["ticker"] not in noms:
            f = pdb.fiche_titre(o["ticker"])
            noms[o["ticker"]] = (f or {}).get("nom") or o["ticker"]
        o["nom"] = noms.get(o.get("ticker"))
        if o["type"] in calc.TYPES_ORDRE:
            o["sens"] = "Achat" if o["type"] == "ACHAT" else "Vente"
            o["montant_position_eur"] = _r(abs(o.get("flux_cash_eur") or 0))
    ops = list(reversed(etat.operations))
    return {"operations": ops,
            "ordres": [o for o in ops if o["type"] in calc.TYPES_ORDRE],
            "mouvements": [o for o in ops if o["type"] in calc.TYPES_MOUVEMENT]}


# ─────────────────────────── Synthèse ───────────────────────────────────────

async def _synthese(user: str, pf: int) -> dict:
    etat = calc.rejouer(pdb.operations(user, pf))
    ouvertes = etat.positions_ouvertes()
    tickers = [p.ticker for p in ouvertes]

    cours = await asyncio.to_thread(md.cours_actuels, tickers)
    devises = sorted({p.devise for p in ouvertes})
    taux = {d: await asyncio.to_thread(md.taux_eur, d) for d in devises}

    lignes, total_titres = [], 0.0
    ventilations = {}
    for p in ouvertes:
        fiche = await _fiche(p.ticker) or {}
        est_etf = (fiche.get("type_actif") or "").upper() in ("ETF", "MUTUALFUND")
        if est_etf:
            ventilations[p.ticker] = await asyncio.to_thread(
                md.composition_etf, p.ticker, fiche.get("nom") or p.ticker)
        c = cours.get(p.ticker)
        prix = c["prix"] if c else p.pru
        veille = c["cloture_veille"] if c else prix
        t = taux.get(p.devise, 1.0)
        valeur_dev = p.quantite * prix
        valeur_eur = valeur_dev * t
        total_titres += valeur_eur
        lignes.append({
            **p.to_dict(),
            "nom": fiche.get("nom") or p.ticker,
            "type_actif": _classe_actif(fiche.get("type_actif")),
            "secteur": "ETF" if est_etf else md.secteur_fr(fiche.get("secteur")),
            "pays": "Diversifié (ETF)" if est_etf else md.pays_fr(fiche.get("pays")),
            "zone": "Diversifié (ETF)" if est_etf else md.zone_de(fiche.get("pays")),
            "cours": _r(prix, 4), "cours_indisponible": c is None,
            "date_cours": c["date_cours"] if c else None,
            "var_jour_pct": _r((prix / veille - 1) * 100 if veille else 0),
            "var_jour_eur": _r(p.quantite * (prix - veille) * t),
            "valeur_devise": _r(valeur_dev), "valeur_eur": _r(valeur_eur),
            "pv_latente_devise": _r((prix - p.pru) * p.quantite),
            "pv_latente_pct": _r((prix / p.pru - 1) * 100 if p.pru else 0),
            "pv_latente_eur": _r(valeur_eur - p.cout_eur),
            "pv_latente_eur_pct": _r((valeur_eur / p.cout_eur - 1) * 100 if p.cout_eur else None),
            "taux_eur": _r(t, 6),
        })
    for l in lignes:
        l["poids_pct"] = _r(l["valeur_eur"] / total_titres * 100 if total_titres else 0)
    lignes.sort(key=lambda l: l["valeur_eur"] or 0, reverse=True)

    def agreger(contributions) -> List[dict]:
        """contributions : itérable de (libellé, valeur_eur) → liste triée avec poids."""
        agg = {}
        for k, v in contributions:
            agg[k] = agg.get(k, 0.0) + (v or 0)
        return sorted(({"label": k, "valeur_eur": _r(v),
                        "poids_pct": _r(v / total_titres * 100 if total_titres else 0)}
                       for k, v in agg.items() if v > 0.005), key=lambda x: -x["valeur_eur"])

    def ventile(cle: str):
        """Secteur / zone avec transparence : un ETF est réparti selon son contenu."""
        for l in lignes:
            v = ventilations.get(l["ticker"])
            if v:
                for lib, w in v["secteurs" if cle == "secteur" else "zones"].items():
                    yield lib, (l["valeur_eur"] or 0) * w
            else:
                yield l[cle], l["valeur_eur"]

    zones = {z["label"]: z for z in agreger(ventile("zone"))}
    repartition_zones = [zones.get(z, {"label": z, "valeur_eur": 0.0, "poids_pct": 0.0})
                         for z in md.ZONES_ORDRE]
    etf_ventiles = [{"ticker": t, "indice": v.get("indice"), "source": v.get("source")}
                    for t, v in ventilations.items()]

    # Capital venu de l'extérieur : les dépôts saisis, plus ce qu'il a bien fallu verser
    # pour payer les achats que les espèces ne couvraient pas. Une vente crédite les
    # espèces ; l'achat suivant s'en sert avant de réclamer de l'argent neuf. C'est ce
    # qui empêche de compter deux fois l'euro réemployé — et c'est la raison pour
    # laquelle le capital investi peut être inférieur au cumul des achats.
    capital_apporte = etat.depots_nets_eur + etat.apports_implicites_eur
    valorisation = total_titres + etat.cash_eur
    pnl_total = valorisation - capital_apporte
    depots_declares = etat.depots_nets_eur > 0.005
    pv_latente = sum(l["pv_latente_eur"] or 0 for l in lignes)
    pv_totale = pv_latente + etat.pv_realisee_eur + etat.dividendes_eur
    # Prix de revient, frais compris : celui des lignes encore détenues, celui des lignes
    # soldées, et leur somme. Ce sont les trois dénominateurs de l'en-tête — chacun porte
    # sur les mêmes lignes que son numérateur, sans quoi le pourcentage ne veut rien dire.
    cout_ouvertes = sum(p.cout_eur for p in ouvertes)
    investi = cout_ouvertes + etat.cout_cede_eur                        # cumul des achats
    var_jour = sum(l["var_jour_eur"] or 0 for l in lignes)
    soldees = []
    for p in etat.positions_soldees():
        f = pdb.fiche_titre(p.ticker) or {}
        soldees.append({**p.to_dict(), "nom": f.get("nom") or p.ticker})

    return {
        "username": user,
        "date_valorisation": date.today().isoformat(),
        # Les espèces sont désormais toujours suivies : une vente les crédite, un achat
        # les débite, et elles ne peuvent pas devenir négatives. Le drapeau ne sert plus
        # qu'à signaler que les versements ont été déduits des achats faute de dépôt saisi.
        "suivi_sans_especes": False,
        "apports_implicites": not depots_declares and etat.apports_implicites_eur > 0.005,
        "totaux": {
            "valorisation_eur": _r(valorisation),
            "titres_eur": _r(total_titres),
            "cash_eur": _r(etat.cash_eur),
            "depots_nets_eur": _r(etat.depots_nets_eur),
            "pv_latente_eur": _r(pv_latente),
            "pv_realisee_eur": _r(etat.pv_realisee_eur),
            "pv_totale_eur": _r(pv_totale),
            "apports_implicites_eur": _r(etat.apports_implicites_eur),
            # ── Les trois mesures de l'en-tête ────────────────────────────────────
            # Latente : ce que valent vos lignes face à ce qu'elles ont coûté.
            # Réalisée : ce que les lignes vendues ont rapporté, face à leur coût.
            # Totale : la somme des deux, face au coût de tout ce qui a été acheté.
            "cout_titres_eur": _r(cout_ouvertes),
            "cout_cede_eur": _r(etat.cout_cede_eur),
            "pv_latente_pct": _r(pv_latente / cout_ouvertes * 100) if cout_ouvertes > 0.005 else None,
            "pv_realisee_pct": _r(etat.pv_realisee_eur / etat.cout_cede_eur * 100)
                               if etat.cout_cede_eur > 0.005 else None,
            "pv_totale_pct": _r(pv_totale / investi * 100) if investi > 0.005 else None,
            "pv_totale_pct_investi": _r(pv_totale / investi * 100 if investi > 0 else 0),
            "investi_eur": _r(investi),
            "dividendes_eur": _r(etat.dividendes_eur),
            "frais_eur": _r(etat.frais_eur),
            "pnl_total_eur": _r(pnl_total),
            # ── Les deux seuls chiffres de l'en-tête ──────────────────────────────
            # La plus-value est la différence entre ce que vaut le compte et ce qui y
            # a été versé : elle englobe donc déjà les dividendes encaissés, les frais
            # payés et les ventes réalisées. Rapportée au capital investi, c'est le
            # pourcentage que montre un relevé de courtier, et rien d'autre.
            "capital_investi_eur": _r(capital_apporte),
            "achats_cumules_eur": _r(investi),
            "plus_value_eur": _r(pnl_total),
            "plus_value_pct": _r(pnl_total / capital_apporte * 100 if capital_apporte > 0 else 0),
            "perf_pct": _r(pnl_total / capital_apporte * 100 if capital_apporte > 0 else 0),
            "var_jour_eur": _r(var_jour),
            "var_jour_pct": _r(var_jour / (total_titres - var_jour) * 100
                               if (total_titres - var_jour) > 0.005 else 0),
            "n_lignes": len(lignes),
        },
        "positions": lignes,
        "positions_soldees": soldees,
        "repartition": {
            "titre": agreger((l["nom"], l["valeur_eur"]) for l in lignes),
            "secteur": agreger(ventile("secteur")),
            "zone": repartition_zones,
            "actif": agreger((l["type_actif"], l["valeur_eur"]) for l in lignes),
            "devise": agreger((l["devise"], l["valeur_eur"]) for l in lignes),
            "etf_ventiles": etf_ventiles,
            # Les espèces n'apparaissent que si elles existent vraiment — produit d'une
            # vente pas encore réinvesti. Sans versement saisi, elles sont le plus
            # souvent nulles, et une ligne à 0 € n'apprend rien.
            "classe": [
                {"label": "Titres", "valeur_eur": _r(total_titres),
                 "poids_pct": _r(total_titres / valorisation * 100 if valorisation > 0 else 0)},
            ] + ([
                {"label": "Liquidités", "valeur_eur": _r(etat.cash_eur),
                 "poids_pct": _r(etat.cash_eur / valorisation * 100 if valorisation > 0 else 0)},
            ] if etat.cash_eur > 0.005 else []),
        },
    }


@router.get("/compte/synthese")
async def synthese(user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    return await _synthese(user, pf)


# ─────────────────────────── Historique de performance ─────────────────────

# ── Reconstitution de l'historique ──────────────────────────────────────────

_cache_serie: dict = {}          # user -> (horodatage, série journalière)


def _valeur_titres(etat, j, clotures, taux, dernier_prix, entrees_du_jour=None) -> float:
    """
    Valeur de marché des positions à la date j.

    Les titres achetés ce jour-là sont comptés à leur prix d'achat et non au cours de
    clôture : la position entre dans le portefeuille à ce qu'elle a coûté. L'écart avec
    le marché apparaît dès le lendemain, et compte donc dans la performance. C'est ce
    qui permet de retrouver exactement la performance d'un courtier, cours actuel
    rapporté au prix de revient.
    """
    entrees_du_jour = entrees_du_jour or {}
    total = 0.0
    for p in etat.positions_ouvertes():
        t = taux[p.devise].get(j) if p.devise in taux.columns else 1.0
        t = float(t) if pd.notna(t) else 1.0
        achetee, prix_achat = entrees_du_jour.get(p.ticker, (0.0, 0.0))
        achetee = min(achetee, p.quantite)
        reste = p.quantite - achetee
        if reste > 0:
            px = clotures[p.ticker].get(j) if p.ticker in clotures.columns else None
            if px is None or pd.isna(px):
                px = dernier_prix.get(p.ticker, p.pru)   # dernier prix connu, jamais inventé
            total += reste * float(px) * t
        if achetee > 0:
            total += achetee * prix_achat * t
    return total


def _parcourir(ops, jours, clotures, taux) -> List[dict]:
    """
    Rejoue les opérations jour par jour et produit, pour chaque date, deux mesures
    distinctes qu'il ne faut surtout pas confondre :

    1. La PLUS-VALUE : valorisation moins capital investi. Rapportée au capital
       investi, c'est le pourcentage qu'affiche un relevé de courtier. C'est la
       réponse à « combien ai-je gagné par rapport à ce que j'ai mis ? ».

    2. La PERFORMANCE (champ `base`, indice interne partant de 100) : le rendement
       enchaîné jour après jour, dont les versements sont neutralisés.

           rendement du jour = valeur du jour ÷ (valeur de la veille + versements du jour)

       C'est la réponse à « qu'a rapporté un euro placé chez moi ? ». C'est la SEULE
       mesure comparable à un indice, puisqu'un indice ne reçoit aucun versement.

    Les deux diffèrent dès qu'on verse à des dates différentes, et c'est normal :
    verser tard sur un portefeuille qui monte laisse la plus-value rapportée au
    capital bien en deçà du rendement réellement obtenu.

    Une position entre à son prix de revient le jour de son acquisition : la plus-value
    accumulée depuis l'achat est intégralement comptée, y compris pour des positions
    importées depuis un relevé de courtier. Un achat non couvert par un versement saisi
    est traité comme un apport de capital, pour que la mesure reste valable en simple
    suivi de positions.
    """
    etat, i, dernier_prix, points = calc.EtatCompte(), 0, {}, []
    base, valeur_prec, engage_prec, jour_precedent = 100.0, None, 0.0, None
    for j in jours:
        jour = j.strftime("%Y-%m-%d")

        # Opérations du jour ; on retient ce qui est acheté pour le valoriser au coût
        entrees = {}
        while i < len(ops) and ops[i]["date"] <= jour:
            o = ops[i]
            calc._appliquer(etat, o)
            if o["type"] in ("ACHAT", "VENTE"):
                dernier_prix[o["ticker"]] = float(o["prix"])
            # Un achat entre à son prix d'acquisition le jour où il est pris en compte,
            # c'est-à-dire s'il est survenu depuis le point précédent — ce qui couvre les
            # opérations datées d'un jour non coté (week-end, férié), rattrapées à la
            # cotation suivante.
            #
            # Au TOUT PREMIER point, tout dépend de la fiabilité de la date.
            #
            # Si l'achat est réellement daté de ce jour-là — saisie manuelle, relevé
            # d'opérations, carnet d'ordres — alors il a bien eu lieu à ce prix ce jour-là :
            # on le valorise à son coût, et le rendement court dès cette séance. C'est la
            # convention habituelle, et elle restitue le gain d'un ordre exécuté sous la
            # clôture.
            #
            # Si la date vient d'un export de POSITIONS, elle n'est qu'une approximation —
            # la date du dernier mouvement — et le prix de revient peut remonter à des
            # années. Le valoriser au coût ferait surgir, dès la cotation suivante, toute
            # la plus-value accumulée depuis l'achat réel en une seule journée. On
            # valorise alors au marché : la courbe mesure le marché, elle ne rattrape pas
            # le passé d'un coup. La plus-value, elle, reste intégralement comptée.
            if jour_precedent is not None:
                nouvelle = o["date"] > jour_precedent
            else:
                nouvelle = (o["date"] == jour
                            and (o.get("origine") or "manuel") != "import_positions")
            if o["type"] == "ACHAT" and nouvelle:
                q, prix = entrees.get(o["ticker"], (0.0, 0.0))
                q_new = q + float(o["quantite"])
                entrees[o["ticker"]] = (q_new,
                                        (prix * q + float(o["prix"]) * float(o["quantite"])) / q_new)
            i += 1

        # Portefeuille après opérations. Les espèces sont tenues par le calcul lui-même :
        # une vente les crédite, un achat les débite, et un achat non couvert inscrit le
        # complément en apport. Le même état sert ici et dans l'en-tête, pour que les deux
        # écrans ne puissent pas raconter deux histoires.
        cash = max(etat.cash_eur, 0.0)
        valeur_titres = _valeur_titres(etat, j, clotures, taux, dernier_prix, entrees)
        valeur = valeur_titres + cash
        engage = etat.depots_nets_eur + etat.apports_implicites_eur

        # Rendement de la journée, versements neutralisés. Un versement du jour est
        # retranché de la valeur du jour plutôt qu'ajouté à celle de la veille : c'est
        # la convention qui correspond à notre valorisation, puisqu'un titre acheté
        # aujourd'hui est compté à son prix d'achat et n'a donc encore rien rapporté.
        #
        # Garde-fou : si la veille le compte était quasi vide, le rendement du jour
        # n'a aucun sens (on diviserait par presque rien et le moindre centime de
        # variation donnerait des centaines de pour cent). La journée est alors
        # neutralisée plutôt que mesurée.
        flux = engage - engage_prec
        if valeur_prec is not None and valeur_prec > max(1.0, 0.05 * abs(flux)):
            base *= (valeur - flux) / valeur_prec
        valeur_prec, engage_prec, jour_precedent = valeur, engage, jour

        # Plus-value latente des titres rapportée à leur prix de revient : c'est le
        # pourcentage d'un relevé de courtier, agrégé sur toutes les lignes. Les
        # espèces n'entrent pas au dénominateur — seul l'argent réellement placé compte.
        cout_titres = sum(p.cout_eur for p in etat.positions_ouvertes())
        points.append({
            "date": jour, "horodatage": j, "valorisation_eur": _r(valeur),
            "titres_eur": _r(valeur_titres), "cash_eur": _r(cash),
            "capital_engage_eur": _r(engage), "pnl_eur": _r(valeur - engage),
            "flux_eur": _r(flux),
            "cout_titres_eur": _r(cout_titres),
            "pv_titres_pct": _r((valeur_titres - cout_titres) / cout_titres * 100)
                             if cout_titres > 0.005 else 0.0,
            # Plus-value rapportée au capital investi : le pourcentage du relevé de courtier
            "pv_pct": _r((valeur - engage) / engage * 100) if engage > 0.005 else 0.0,
            # Indice de performance interne, base 100 au premier point de l'historique
            "base": base,
        })
    return points


async def _serie_journaliere(user: str, pf: int) -> List[dict]:
    """Historique journalier du portefeuille, mis en cache une minute."""
    cache = _cache_serie.get((user, pf))
    if cache and time.time() - cache[0] < 60:
        return cache[1]
    ops = sorted(pdb.operations(user, pf), key=lambda o: (o["date"], o["id"]))
    if not ops:
        return []
    debut = ops[0]["date"]
    tickers = sorted({o["ticker"] for o in ops if o["type"] in ("ACHAT", "VENTE")})
    devises = sorted({o["devise"] for o in ops if o["type"] in ("ACHAT", "VENTE")})
    jours = pd.bdate_range(debut, date.today())
    clotures = await asyncio.to_thread(md.historique_clotures, tickers, debut)
    clotures = md.aligner_sur(clotures, jours) if not clotures.empty else pd.DataFrame(index=jours)
    taux = await asyncio.to_thread(md.historique_taux, devises or ["EUR"], debut, jours)
    points = _parcourir(ops, jours, clotures, taux)
    _cache_serie[(user, pf)] = (time.time(), points)
    return points


@router.get("/compte/historique")
async def historique(indice: Optional[str] = None, periode: Optional[str] = None,
                     mode: Optional[str] = None,
                     user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    """
    Valorisation et performance du portefeuille, comparées à un indice.
    Sur 5 jours et 1 mois, les cotations horaires réellement publiées par Yahoo sont
    utilisées ; au-delà, les clôtures journalières. Aucune donnée n'est interpolée.
    """
    ops = sorted(pdb.operations(user, pf), key=lambda o: (o["date"], o["id"]))
    if not ops:
        return {"points": [], "indice": None}
    reglages = pdb.parametres(user)
    code = indice if indice in md.INDICES else reglages["indice"]
    mode = mode if mode in md.MODES_INDICE else reglages.get("mode_indice") or "euros_dividendes"
    debut_compte = ops[0]["date"]
    tickers = sorted({o["ticker"] for o in ops if o["type"] in ("ACHAT", "VENTE")})
    devises = sorted({o["devise"] for o in ops if o["type"] in ("ACHAT", "VENTE")})

    intraday = periode in ("5j", "1m")
    if intraday:
        cl_h = await asyncio.to_thread(md.historique_intraday, tickers, periode)
        cl_h = cl_h[cl_h.index >= pd.Timestamp(debut_compte)] if not cl_h.empty else cl_h
        # Yahoo renvoie « environ » cinq jours ou un mois de cotations horaires. On
        # recadre sur la fenêtre calendaire exacte, celle qu'annonce le bouton et celle
        # qu'emploie le tableau : sans cela, la courbe « 1 M » et la ligne « 1 mois »
        # ne portent pas sur la même période et ne peuvent pas tomber sur le même chiffre.
        if not cl_h.empty:
            # Ancré sur la dernière cotation reçue, comme le tableau : un samedi,
            # partir d'aujourd'hui recalerait la fenêtre d'un week-end entier.
            fin_reelle = cl_h.index[-1].normalize()
            borne = fin_reelle - (
                pd.DateOffset(months=1) if periode == "1m" else pd.Timedelta(days=5))
            recadre = cl_h[cl_h.index >= borne]
            if len(recadre) >= 2:
                cl_h = recadre
        # La fenêtre horaire de Yahoo commence en pleine séance : la courbe partirait
        # d'un cours de milieu de journée, alors que le tableau part de la clôture de
        # ce jour-là. Deux bases différentes, donc deux pourcentages qui ne se
        # rejoignent jamais tout à fait. On jette les barres du premier jour sauf la
        # dernière : la courbe démarre alors sur la même clôture que le tableau.
        if not cl_h.empty:
            premier_jour = cl_h.index[0].normalize()
            du_jour = cl_h.index[cl_h.index.normalize() == premier_jour]
            if len(du_jour) > 1:
                cl_h = cl_h[cl_h.index >= du_jour[-1]]
        if len(cl_h) < 2:
            intraday = False                 # pas d'intraday disponible : repli journalier
        else:
            jours = cl_h.index
            taux = await asyncio.to_thread(md.taux_intraday, devises or ["EUR"], periode, jours)
            # ffill puis bfill : un titre qui n'a pas encore coté au premier horodatage
            # est valorisé à sa première cotation connue, et non à son prix d'achat,
            # ce qui créerait un saut artificiel en début de courbe.
            points = _parcourir(ops, jours, cl_h.ffill().bfill(), taux)

    if not intraday:
        tous = await _serie_journaliere(user, pf)
        # Décalages CALENDAIRES, et non des nombres de jours : « 1 an » doit tomber sur
        # le même quantième que dans le tableau, pas 365 jours en arrière. Et la fenêtre
        # est ancrée sur la DERNIÈRE COTATION, comme le tableau — un samedi, partir
        # d'aujourd'hui décalerait la courbe du week-end et les deux écrans
        # n'afficheraient pas le même pourcentage pour la même période.
        recul = {"3m": ("mois", 3), "6m": ("mois", 6), "1a": ("ans", 1), "5a": ("ans", 5)}
        points = tous
        if periode in recul and tous:
            unite, n = recul[periode]
            limite = _recul(tous[-1]["horodatage"], unite, n)
            fenetre = [p for p in tous if p["horodatage"] >= limite]
            points = fenetre if len(fenetre) >= 2 else tous
        jours = pd.DatetimeIndex([p["horodatage"] for p in points])

    # Indice de comparaison, aligné sur les mêmes horodatages que le portefeuille
    serie, source, dividendes, devise_indice = await asyncio.to_thread(
        md.serie_indice, code, jours[0].strftime("%Y-%m-%d"),
        periode if intraday else None, jours, mode)

    base_indice = float(serie.iloc[0]) if len(serie) >= 2 else None
    depart = points[0] if points else None

    def perf_depuis_depart(p) -> Optional[float]:
        """
        Performance du point p depuis le début de la fenêtre affichée : rapport de
        deux valeurs de l'indice de performance interne. Le dénominateur est fixe sur
        toute la fenêtre, donc la courbe ne bouge que si le marché bouge — un versement
        en cours de route ne crée ni marche ni décrochage.
        """
        if not depart or depart["base"] <= 0:
            return None
        return (p["base"] / depart["base"] - 1) * 100

    sortie = []
    for p in points:
        j = p["horodatage"]
        valeur_i = float(serie.get(j)) if (base_indice and j in serie.index
                                           and pd.notna(serie.get(j))) else None
        sortie.append({
            "date": j.strftime("%Y-%m-%d %H:%M") if intraday else p["date"],
            "valorisation_eur": p["valorisation_eur"], "titres_eur": p["titres_eur"],
            "cash_eur": p["cash_eur"], "capital_engage_eur": p["capital_engage_eur"],
            "pnl_eur": p["pnl_eur"],
            # Performances rebasées à 0 au début de la fenêtre affichée
            "perf_pct": _r(perf_depuis_depart(p)),
            "indice_pct": _r((valeur_i / base_indice - 1) * 100) if valeur_i else None,
            # Plus-value latente des titres sur leur prix de revient. C'est un NIVEAU,
            # pas une variation : il ne part pas de zéro au début de la fenêtre, et il
            # se dilue mécaniquement à chaque renforcement, puisque le prix de revient
            # augmente en même temps que la valeur.
            "pv_titres_pct": p["pv_titres_pct"],
            "cout_titres_eur": p["cout_titres_eur"],
            # Plus-value accumulée depuis le début de la fenêtre, en euros
            "gain_fenetre_eur": _r(p["pnl_eur"] - depart["pnl_eur"]) if depart else None,
        })
    manquants = sum(1 for p in sortie if p["indice_pct"] is None)
    return {
        "points": sortie, "indice": code, "libelle_indice": md.libelle_indice(code),
        "source_indice": source, "indice_dividendes": dividendes,
        "mode_indice": mode, "devise_indice": devise_indice,
        "granularite": "heure" if intraday else "jour",
        "indice_disponible": base_indice is not None and manquants < len(sortie) / 2,
        "message_indice": None if base_indice else
            f"{md.libelle_indice(code)} : aucune cotation disponible sur cette période.",
    }


# ── Tableau de performances ─────────────────────────────────────────────────

# Annualiser, c'est répondre à « à ce rythme, combien par an ? ». En deçà de trois
# mois, la question n'a pas de sens : porter quatre semaines à la puissance treize
# transforme la moindre variation en chiffre absurde. À partir de trois mois le calcul
# est affiché, mais tant qu'on n'a pas un an de recul il reste une EXTRAPOLATION, et
# l'interface le signale. Au-delà d'un an, c'est une mesure.
# Le même seuil vaut pour l'indice, sans quoi les deux colonnes ne seraient pas
# comparables.
SEUIL_ANNUALISATION = 91
SEUIL_EXTRAPOLATION = 365

# Les horizons se comptent en mois et en années de calendrier, pas en nombre de jours.
# « 5 ans » doit partir du même quantième cinq ans plus tôt, comme le fait n'importe
# quel site de cotation : compter 1 826 jours décale le départ d'une séance, et si
# l'indice a bougé ce jour-là l'écart se retrouve en entier dans la performance
# affichée. C'est exactement ce qui faisait diverger le CAC 40 de 1,5 point.
PERIODES_PERF = [("1 mois", "mois", 1), ("3 mois", "mois", 3),
                 ("1 an", "ans", 1), ("3 ans", "ans", 3), ("5 ans", "ans", 5),
                 ("10 ans", "ans", 10)]


def _recul(fin: pd.Timestamp, unite: str, n: int) -> pd.Timestamp:
    """Même quantième, n mois ou n années plus tôt."""
    return fin - (pd.DateOffset(years=n) if unite == "ans" else pd.DateOffset(months=n))


def _annualiser(cumul: float, jours: int) -> Optional[float]:
    """Taux annuel équivalent. None en deçà de trois mois, où il ne voudrait rien dire."""
    if jours < SEUIL_ANNUALISATION or cumul <= -1:
        return None
    return _r(((1 + cumul) ** (365.25 / jours) - 1) * 100)


def _perf_entre(points: List[dict], depuis: pd.Timestamp) -> Optional[dict]:
    """
    Performance de la période : rapport des deux valeurs de l'indice de performance
    interne, soit le rendement enchaîné jour après jour, versements neutralisés.
    C'est la mesure comparable à un indice.

    La plus-value de la période est renvoyée en parallèle, en euros et rapportée au
    capital investi : c'est l'autre lecture, celle du relevé de courtier.
    """
    fenetre = [p for p in points if p["horodatage"] >= depuis]
    if len(fenetre) < 2:
        return None
    d, f = fenetre[0], fenetre[-1]
    jours = (f["horodatage"] - d["horodatage"]).days
    if jours < 1 or d["base"] <= 0:
        return None
    cumul = f["base"] / d["base"] - 1
    versements = f["capital_engage_eur"] - d["capital_engage_eur"]
    gain = f["pnl_eur"] - d["pnl_eur"]
    return {"cumul_pct": _r(cumul * 100), "jours": jours,
            # Plus-value sur PRU : son niveau en fin de période, et le chemin parcouru
            # depuis le début, exprimé en points de pourcentage.
            "pv_pru_pct": f["pv_titres_pct"],
            "pv_pru_debut_pct": d["pv_titres_pct"],
            "pv_pru_variation_pts": _r(f["pv_titres_pct"] - d["pv_titres_pct"]),
            "cout_titres_eur": f["cout_titres_eur"],
            "annualise_pct": _annualiser(cumul, jours),
            "debut": d["date"], "fin": f["date"],
            "valeur_debut": d["valorisation_eur"], "valeur_fin": f["valorisation_eur"],
            "versements_eur": _r(versements), "gain_eur": _r(gain),
            "capital_debut": d["capital_engage_eur"], "capital_fin": f["capital_engage_eur"]}


def _perf_indice(serie, depuis: pd.Timestamp) -> Optional[dict]:
    fenetre = serie[serie.index >= depuis].dropna()
    if len(fenetre) < 2:
        return None
    jours = (fenetre.index[-1] - fenetre.index[0]).days
    if jours < 1:
        return None
    cumul = float(fenetre.iloc[-1]) / float(fenetre.iloc[0]) - 1
    # `jours` est la durée RÉELLEMENT mesurée entre la première et la dernière cotation
    # disponibles, pas l'horizon demandé : c'est elle qui sert à annualiser, et c'est
    # donc elle qu'il faut publier pour que le chiffre soit refaisable à la main.
    return {"cumul_pct": _r(cumul * 100), "jours": jours,
            "annualise_pct": _annualiser(cumul, jours),
            "niveau_debut": _r(float(fenetre.iloc[0]), 4),
            "niveau_fin": _r(float(fenetre.iloc[-1]), 4),
            "debut": fenetre.index[0].strftime("%Y-%m-%d"),
            "fin": fenetre.index[-1].strftime("%Y-%m-%d")}


# Horizons sur lesquels l'indice est donné seul, indépendamment du portefeuille.
# L'historique d'un indice remonte à des décennies : il n'y a aucune raison de s'en
# priver sous prétexte que le compte est récent. Ces lignes ne sont PAS une
# comparaison — le portefeuille n'existait pas — mais un repère.
HORIZONS_INDICE = [("1 an", 1), ("3 ans", 3), ("5 ans", 5), ("10 ans", 10)]


async def _reference_indice(code: str, mode: str = "euros_dividendes") -> dict:
    """
    Performance de l'indice seul sur ses propres horizons longs, cumulée et annualisée.
    Chaque ligne porte les dates et les niveaux effectivement utilisés, pour que le
    chiffre soit refaisable à la main.
    """
    debut = (pd.Timestamp.today() - pd.DateOffset(years=HORIZONS_INDICE[-1][1])
             - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    serie, source, dividendes, devise = await asyncio.to_thread(
        md.serie_indice, code, debut, None, None, mode)
    if serie.empty or len(serie) < 2:
        return {"lignes": [], "source": None, "dividendes": None, "devise": None}
    serie = serie.dropna()
    fin = serie.index[-1]
    lignes = []
    for libelle, annees in HORIZONS_INDICE:
        depuis = fin - pd.DateOffset(years=annees)
        if depuis < serie.index[0]:
            continue              # l'historique disponible ne couvre pas cet horizon
        idx = _perf_indice(serie, depuis)
        if idx:
            lignes.append({"libelle": libelle, "horizon_annees": annees, **idx,
                           "extrapolation": False})
    return {"lignes": lignes, "source": source, "dividendes": dividendes, "devise": devise,
            "debut_disponible": serie.index[0].strftime("%Y-%m-%d"),
            "fin": fin.strftime("%Y-%m-%d")}


async def _decomposition_indice(code: str, debut: str, fin: str) -> Optional[dict]:
    """
    D'où vient l'écart entre l'indice qu'on lit dans la presse et celui du tableau ?

    La presse cite le S&P 500 en dollars, hors dividendes. Le tableau mesure ce qu'a
    réellement gagné un épargnant en euros : dividendes réinvestis, et exposé au change.
    Trois composantes donc, et il vaut mieux les montrer que laisser croire à une erreur :

        indice local (sa devise, hors dividendes)  ×  effet de change  ×  dividendes
    """
    conf = md.INDICES.get(code)
    if not conf or not conf.get("prix"):
        return None
    ticker, devise = conf["prix"]
    serie = await asyncio.to_thread(md.historique_clotures, [ticker], debut)
    if serie.empty or ticker not in serie:
        return None
    serie = serie[ticker].dropna()
    serie = serie[(serie.index >= pd.Timestamp(debut)) & (serie.index <= pd.Timestamp(fin))]
    if len(serie) < 2:
        return None
    local = float(serie.iloc[-1]) / float(serie.iloc[0]) - 1

    change = None
    base, _f = md._devise_base(devise)
    if base != "EUR":
        taux = await asyncio.to_thread(md.historique_taux, [devise], debut, serie.index)
        if devise in taux:
            t = taux[devise].dropna()
            if len(t) >= 2:
                change = float(t.iloc[-1]) / float(t.iloc[0]) - 1
    return {"ticker": ticker, "devise": base,
            "local_pct": _r(local * 100),
            "change_pct": _r(change * 100) if change is not None else None,
            "hors_dividendes_eur_pct": _r(((1 + local) * (1 + (change or 0)) - 1) * 100),
            "debut": serie.index[0].strftime("%Y-%m-%d"),
            "fin": serie.index[-1].strftime("%Y-%m-%d")}


@router.get("/compte/performances")
async def performances(indice: Optional[str] = None, mode: Optional[str] = None,
                       user: str = Depends(utilisateur_courant),
                       pf: int = Depends(portefeuille_courant)):
    """
    Performances cumulées et annualisées par période, portefeuille contre indice,
    comme le ferait la fiche mensuelle d'un fonds. Une période plus courte que
    l'historique disponible n'est pas affichée plutôt que d'être extrapolée.
    """
    points = await _serie_journaliere(user, pf)
    if len(points) < 2:
        return {"lignes": [], "depuis_ouverture": None}
    reglages = pdb.parametres(user)
    code = indice if indice in md.INDICES else reglages["indice"]
    mode = mode if mode in md.MODES_INDICE else reglages.get("mode_indice") or "euros_dividendes"
    debut = points[0]["date"]
    serie, source, dividendes, devise_indice = await asyncio.to_thread(
        md.serie_indice, code, debut, None, [p["horodatage"] for p in points], mode)
    aujourdhui = points[-1]["horodatage"]

    # L'indice est aussi chargé sur un historique long : une période que votre compte
    # ne couvre pas encore reste renseignée côté marché, et la ligne est affichée avec
    # une case vide de votre côté plutôt que d'être escamotée.
    serie_longue, source_longue, div_longue, devise_longue = await asyncio.to_thread(
        md.serie_indice, code,
        (pd.Timestamp.today() - pd.DateOffset(years=PERIODES_PERF[-1][2])
         - pd.Timedelta(days=10)).strftime("%Y-%m-%d"), None, None, mode)

    lignes = []
    for libelle, unite, n in PERIODES_PERF:
        depuis = _recul(aujourdhui, unite, n)
        couvert = depuis >= points[0]["horodatage"]
        pf = _perf_entre(points, depuis) if couvert else None
        # L'indice se mesure sur la même fenêtre, sur son propre historique
        idx = _perf_indice(serie_longue, depuis) if len(serie_longue) else None
        if not pf and not idx:
            continue                       # ni le compte ni le marché ne couvrent
        if not pf:
            lignes.append({
                "libelle": libelle, "jours": (idx or {}).get("jours"),
                "extrapolation": False, "hors_historique": True,
                "debut": (idx or {}).get("debut"), "fin": (idx or {}).get("fin"),
                "portefeuille_cumul_pct": None, "portefeuille_annualise_pct": None,
                "indice_cumul_pct": (idx or {}).get("cumul_pct"),
                "indice_annualise_pct": (idx or {}).get("annualise_pct"),
                "indice_niveau_debut": (idx or {}).get("niveau_debut"),
                "indice_niveau_fin": (idx or {}).get("niveau_fin"),
                "ecart_pct": None})
            continue
        lignes.append({
            "libelle": libelle, "jours": pf["jours"],
            "extrapolation": pf["jours"] < SEUIL_EXTRAPOLATION,
            "debut": pf["debut"], "fin": pf["fin"],
            "valeur_debut": pf["valeur_debut"], "valeur_fin": pf["valeur_fin"],
            "versements_eur": pf["versements_eur"], "gain_eur": pf["gain_eur"],
            "capital_debut": pf["capital_debut"], "capital_fin": pf["capital_fin"],
            "pv_pru_pct": pf["pv_pru_pct"], "pv_pru_debut_pct": pf["pv_pru_debut_pct"],
            "pv_pru_variation_pts": pf["pv_pru_variation_pts"],
            "cout_titres_eur": pf["cout_titres_eur"],
            "portefeuille_cumul_pct": pf["cumul_pct"],
            "portefeuille_annualise_pct": pf["annualise_pct"],
            "indice_cumul_pct": (idx or {}).get("cumul_pct"),
            "indice_annualise_pct": (idx or {}).get("annualise_pct"),
            "indice_niveau_debut": (idx or {}).get("niveau_debut"),
            "indice_niveau_fin": (idx or {}).get("niveau_fin"),
            "ecart_pct": _r(pf["cumul_pct"] - idx["cumul_pct"]) if idx else None,
        })

    ouverture = _perf_entre(points, points[0]["horodatage"])
    idx_ouv = _perf_indice(serie, points[0]["horodatage"]) if len(serie) else None
    if ouverture:
        lignes.append({
            "libelle": "Depuis l'ouverture", "jours": ouverture["jours"],
            "extrapolation": ouverture["jours"] < SEUIL_EXTRAPOLATION,
            "debut": ouverture["debut"], "fin": ouverture["fin"],
            "valeur_debut": ouverture["valeur_debut"], "valeur_fin": ouverture["valeur_fin"],
            "versements_eur": ouverture["versements_eur"], "gain_eur": ouverture["gain_eur"],
            "capital_debut": ouverture["capital_debut"], "capital_fin": ouverture["capital_fin"],
            "pv_pru_pct": ouverture["pv_pru_pct"], "pv_pru_debut_pct": ouverture["pv_pru_debut_pct"],
            "pv_pru_variation_pts": ouverture["pv_pru_variation_pts"],
            "cout_titres_eur": ouverture["cout_titres_eur"],
            "portefeuille_cumul_pct": ouverture["cumul_pct"],
            "portefeuille_annualise_pct": ouverture["annualise_pct"],
            "indice_cumul_pct": (idx_ouv or {}).get("cumul_pct"),
            "indice_annualise_pct": (idx_ouv or {}).get("annualise_pct"),
            "indice_niveau_debut": (idx_ouv or {}).get("niveau_debut"),
            "indice_niveau_fin": (idx_ouv or {}).get("niveau_fin"),
            "ecart_pct": _r(ouverture["cumul_pct"] - idx_ouv["cumul_pct"]) if idx_ouv else None,
            "principal": True,
        })

    return {"lignes": lignes, "indice": code, "libelle_indice": md.libelle_indice(code),
            "source_indice": source, "indice_dividendes": dividendes,
            "debut_historique": debut,
            "depuis_ouverture": ouverture["cumul_pct"] if ouverture else None,
            "annualise_ouverture": ouverture["annualise_pct"] if ouverture else None,
            "seuil_annualisation_jours": SEUIL_ANNUALISATION,
            "seuil_extrapolation_jours": SEUIL_EXTRAPOLATION,
            "mode_indice": mode, "devise_indice": devise_indice,
            "source_indice_long": source_longue, "indice_dividendes_long": div_longue,
            # L'autre lecture, celle du relevé de courtier, pour que les deux chiffres
            # soient donnés côte à côte plutôt que de sembler se contredire
            "plus_value_eur": points[-1]["pnl_eur"],
            "plus_value_pct": points[-1]["pv_pct"],
            "capital_investi_eur": points[-1]["capital_engage_eur"]}


# ─────────────────────────── Indicateurs de risque ──────────────────────────

# Chaque indicateur est accompagné de sa définition exacte : un chiffre de risque
# qu'on ne sait pas refaire à la main ne vaut rien, et c'est le genre de chose qu'on
# vous demandera d'expliquer. Les rendements sont quotidiens, convertis en euros
# (le risque de change est donc compris), sur un an glissant, et le portefeuille est
# figé dans sa composition d'aujourd'hui.
_METHODE_RISQUE = {
    "base": "Rendements quotidiens des titres détenus, convertis en euros, sur un an "
            "glissant. La composition retenue est celle d'aujourd'hui : on mesure le "
            "risque du portefeuille tel qu'il est, pas celui qu'il a eu.",
    "volatilite": {
        "titre": "Volatilité annualisée",
        "formule": "σ = √(wᵀ Σ w) × √252",
        "detail": "Écart-type des rendements quotidiens du portefeuille, ramené à l'année "
                  "en multipliant par la racine de 252 jours de bourse. Σ est la matrice "
                  "de covariance des titres, w le vecteur des poids en euros. Les "
                  "corrélations entre titres sont donc prises en compte : la volatilité du "
                  "portefeuille est inférieure à la moyenne de celles des lignes.",
    },
    "var": {
        "titre": "VaR 95 % à 1 jour",
        "formule": "VaR = −quantile 5 % des rendements quotidiens",
        "detail": "Perte quotidienne dépassée un jour sur vingt, lue directement dans les "
                  "rendements observés sur un an (méthode historique, aucune hypothèse de "
                  "loi normale). La CVaR est la perte moyenne de ces jours-là, donc "
                  "toujours supérieure à la VaR : elle dit ce qui se passe quand ça dépasse.",
    },
    "drawdown": {
        "titre": "Perte maximale",
        "formule": "min( V(t) / max V(s), s ≤ t ) − 1",
        "detail": "Plus forte baisse entre un sommet et le creux qui le suit, sur l'année "
                  "écoulée, à composition constante.",
    },
    "beta": {
        "titre": "Bêta",
        "formule": "β = Cov(r_portefeuille, r_indice) / Var(r_indice)",
        "detail": "Sensibilité du portefeuille à l'indice, estimée par régression des "
                  "rendements quotidiens en euros sur un an. β = 1,2 signifie qu'en "
                  "moyenne, une hausse de 1 % de l'indice s'accompagne d'une hausse de "
                  "1,2 % du portefeuille. C'est une moyenne historique, pas une garantie : "
                  "la corrélation indiquée dit à quel point cette relation est étroite. "
                  "Un bêta élevé avec une corrélation faible n'est pas exploitable.",
    },
    "sharpe": {
        "titre": "Ratio de Sharpe",
        "formule": "(rendement annuel − taux sans risque) / volatilité annualisée",
        "detail": "Rendement obtenu par unité de risque pris. Calculé sur un an de "
                  "rendements passés : c'est une mesure rétrospective, elle ne prédit rien.",
    },
    "contribution": {
        "titre": "Part du risque",
        "formule": "part du risque de la ligne i  =  poids de i  ×  βᵢ/portefeuille\n"
                   "       où  βᵢ/portefeuille = Cov(rᵢ, r_portefeuille) / Var(r_portefeuille)\n"
                   "       (c'est wᵢ (Σw)ᵢ / σ² ; la somme de la colonne fait exactement 100 %)",
        "detail": "Part de la variance du portefeuille imputable à une ligne. Attention : le "
                  "bêta qui intervient ici est celui de la ligne face à VOTRE PORTEFEUILLE, "
                  "pas face à l'indice — ce sont deux choses différentes, et les deux colonnes "
                  "sont affichées. Une ligne peut peser 17 % et porter 20 % du risque : son "
                  "bêta portefeuille vaut alors 20/17 ≈ 1,2, elle amplifie donc légèrement les "
                  "mouvements du portefeuille. Une ligne décorrélée du reste porte au contraire "
                  "moins de risque que son poids ne le suggère. C'est la décomposition d'Euler "
                  "de la volatilité : elle répond à « si je coupe cette ligne, de combien baisse "
                  "le risque total ? ».",
    },
}


@router.get("/compte/risque")
async def risque(user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    """
    Indicateurs calculés sur 1 an de cours, avec la composition ACTUELLE du portefeuille
    (poids en EUR), rendements convertis en EUR (le risque de change est donc inclus).
    """
    taux_sans_risque = (pdb.parametres(user).get("taux_sans_risque")
                        if pdb.parametres(user).get("taux_sans_risque") is not None
                        else TAUX_SANS_RISQUE_DEFAUT) / 100.0
    syn = await _synthese(user, pf)
    lignes = syn["positions"]
    if not lignes:
        return {"disponible": False, "message": "Aucune position ouverte."}

    tickers = [l["ticker"] for l in lignes]
    poids = np.array([l["valeur_eur"] or 0 for l in lignes], dtype=float)
    poids = poids / poids.sum()
    debut = (pd.Timestamp.today() - pd.DateOffset(years=1)).strftime("%Y-%m-%d")

    code = "MSCI_WORLD"          # référence fixe pour le bêta : indice actions mondial, en euros
    clotures = await asyncio.to_thread(md.historique_clotures, tickers, debut)
    serie_indice, _src, _div, _dev = await asyncio.to_thread(md.serie_indice, code, debut, None, clotures.index)
    taux = await asyncio.to_thread(md.historique_taux, [l["devise"] for l in lignes], debut)
    if clotures.empty:
        return {"disponible": False, "message": "Historique de cours indisponible."}

    clotures = clotures.ffill()
    taux = taux.reindex(clotures.index).ffill().bfill()
    prix_eur = pd.DataFrame({l["ticker"]: clotures[l["ticker"]] * taux[l["devise"]]
                             for l in lignes if l["ticker"] in clotures})
    rdt = prix_eur.pct_change().dropna(how="all").fillna(0.0)
    if len(rdt) < 30:
        return {"disponible": False, "message": "Moins de 30 jours d'historique : indicateurs non fiables."}

    cols = list(rdt.columns)
    w = np.array([poids[tickers.index(c)] for c in cols])
    w = w / w.sum()
    r_p = rdt.values @ w
    cov = np.cov(rdt.values, rowvar=False) * 252
    cov = np.atleast_2d(cov)
    vol = float(np.sqrt(w @ cov @ w))

    q5 = float(np.percentile(r_p, 5))
    var95 = -q5
    cvar95 = -float(r_p[r_p <= q5].mean())
    trajectoire = np.cumprod(1 + r_p)
    max_dd = float((trajectoire / np.maximum.accumulate(trajectoire) - 1).min())

    # Rendement annualisé, formule géométrique — la même que celle de l'onglet
    # Performance : (1 + performance cumulée) ^ (1 / nombre d'années) − 1.
    #
    # L'annualisation arithmétique (moyenne quotidienne × 252) est l'autre convention
    # admise pour le numérateur du Sharpe, et elle a sa justification : la moyenne et
    # l'écart-type sont alors mis à l'échelle de la même manière. Mais elle donne
    # toujours un chiffre supérieur — l'écart étant d'environ σ²/2 — et surtout elle
    # ferait coexister dans la même application deux « rendement annualisé » calculés
    # différemment. Une seule définition, vérifiable à la main, vaut mieux.
    cumul = float(trajectoire[-1]) - 1
    annees = len(r_p) / 252
    rend = ((1 + cumul) ** (1 / annees) - 1) if (cumul > -1 and annees > 0) else None

    beta = correl = None
    r_idx = None
    if not serie_indice.empty and serie_indice.notna().sum() > 30:
        r_idx = serie_indice.pct_change().reindex(rdt.index).fillna(0.0).values
        v_idx = float(np.var(r_idx, ddof=1))
        if v_idx > 0:
            beta = float(np.cov(r_p, r_idx, ddof=1)[0, 1] / v_idx)
            correl = float(np.corrcoef(r_p, r_idx)[0, 1])

    contrib = (w * (cov @ w)) / (vol ** 2) if vol > 0 else np.zeros_like(w)
    vols_ind = np.sqrt(np.diag(cov))
    valeur_titres = syn["totaux"]["titres_eur"] or 0

    var_p = float(np.var(r_p, ddof=1))
    par_ligne = []
    for k, c in enumerate(cols):
        l = next(x for x in lignes if x["ticker"] == c)
        b = None
        if r_idx is not None and np.var(r_idx) > 0:
            b = float(np.cov(rdt[c].values, r_idx, ddof=1)[0, 1] / np.var(r_idx, ddof=1))
        # Bêta de la ligne face au PORTEFEUILLE (et non face à l'indice) : c'est lui qui
        # commande la part du risque, puisque part = poids × ce bêta. L'afficher rend le
        # calcul vérifiable à la main, et évite de confondre les deux bêtas.
        b_pf = (float(np.cov(rdt[c].values, r_p, ddof=1)[0, 1] / var_p) if var_p > 0 else None)
        par_ligne.append({"ticker": c, "nom": l["nom"], "poids_pct": _r(w[k] * 100),
                          "volatilite_pct": _r(vols_ind[k] * 100),
                          "contribution_risque_pct": _r(contrib[k] * 100),
                          "beta": _r(b), "beta_portefeuille": _r(b_pf)})
    par_ligne.sort(key=lambda x: -(x["contribution_risque_pct"] or 0))

    return {
        "disponible": True,
        "periode": f"{rdt.index[0].strftime('%d/%m/%Y')} → {rdt.index[-1].strftime('%d/%m/%Y')}",
        "n_jours": len(rdt),
        "indice": code,
        "indice_reference": md.libelle_indice(code),
        "methode": _METHODE_RISQUE,
        "volatilite_pct": _r(vol * 100),
        # Une perte totale rend l'annualisation géométrique indéfinie : on affiche alors
        # un tiret plutôt qu'un nombre inventé, et le Sharpe suit.
        "rendement_1an_pct": _r(rend * 100) if rend is not None else None,
        "sharpe": _r((rend - taux_sans_risque) / vol) if (rend is not None and vol > 0) else None,
        "taux_sans_risque_pct": _r(taux_sans_risque * 100),
        "var95_1j_pct": _r(var95 * 100),
        "var95_1j_eur": _r(var95 * valeur_titres),
        "cvar95_1j_pct": _r(cvar95 * 100),
        "max_drawdown_pct": _r(max_dd * 100),
        "beta": _r(beta),
        "correlation_indice": _r(correl),
        "nb_lignes": len(lignes),
        "par_ligne": par_ligne,
    }


# ─────────────────────────── Actualités ─────────────────────────────────────

@router.get("/compte/actualites")
async def actualites(user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    etat = calc.rejouer(pdb.operations(user, pf))
    tickers = [p.ticker for p in etat.positions_ouvertes()][:12]
    if not tickers:
        return {"articles": []}
    agent = NewsAgent()
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        resultats = await asyncio.gather(*(agent._fetch_ticker_news(client, t) for t in tickers),
                                         return_exceptions=True)
    articles = []
    for tk, res in zip(tickers, resultats):
        if not isinstance(res, list):
            continue
        nom = (pdb.fiche_titre(tk) or {}).get("nom") or tk
        for a in res:
            a = agent._analyse_article({**a, "ticker": tk})
            try:
                d = parsedate_to_datetime(a.get("date", "")).isoformat()
            except Exception:
                d = None
            articles.append({"ticker": tk, "nom": nom, "titre": a.get("titre"),
                             "lien": a.get("lien"), "date": d, "resume": a.get("desc"),
                             "sentiment": a.get("type"), "score": a.get("score")})
    articles.sort(key=lambda a: a["date"] or "", reverse=True)
    return {"articles": articles}


# ─────────────────────────── Paramètres ─────────────────────────────────────

class ParametresIn(BaseModel):
    """Tous les champs sont facultatifs : l'écran n'envoie que ce qu'il modifie.

    Le choix de l'indice se fait désormais dans l'onglet Performance et le taux sans
    risque dans l'onglet Risque ; chacun n'a aucune raison d'écraser l'autre."""
    prelevement_div_pct: Optional[float] = Field(default=None, ge=0, le=100)
    indice: Optional[str] = None
    mode_indice: Optional[str] = None
    taux_sans_risque: Optional[float] = Field(default=None, ge=-5, le=25)
    # Cible d'investissement mensuel, en euros. Propre à chacun, et appelée à bouger :
    # elle se modifie depuis l'onglet Risque, au-dessus du score de régularité.
    cible_mensuelle_eur: Optional[float] = Field(default=None, ge=0, le=1_000_000)

    @field_validator("mode_indice")
    @classmethod
    def _mode(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in md.MODES_INDICE:
            raise ValueError(f"mode_indice doit être parmi {', '.join(md.MODES_INDICE)}")
        return v

    @field_validator("indice")
    @classmethod
    def _indice(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in md.INDICES:
            raise ValueError(f"indice doit être parmi {', '.join(md.INDICES)}")
        return v


@router.get("/compte/parametres")
def lire_parametres(user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    p = dict(pdb.parametres(user))
    # Un indice retiré de la liste (le CAC 40) peut rester enregistré d'une version
    # précédente : on le ramène sur l'indice mondial plutôt que d'afficher un choix vide.
    if p.get("indice") not in md.INDICES:
        p["indice"] = "MSCI_WORLD"
    return {**p,
            "indices_disponibles": [{"code": k, "libelle": v["libelle"]} for k, v in md.INDICES.items()],
            "modes_indice": [
                {"code": "euros_dividendes", "libelle": "En euros, dividendes réinvestis",
                 "aide": "L'indice tel que le vit un épargnant français. Seule version "
                         "comparable à votre portefeuille, lui aussi en euros."},
                {"code": "devise_origine", "libelle": "Dans la devise de l'indice, dividendes réinvestis",
                 "aide": "Le S&P 500 en dollars (indice officiel total return). "
                         "Pratique pour recouper une donnée publiée, mais l'effet de change que "
                         "vous avez réellement subi n'y figure pas."}]}


@router.put("/compte/parametres")
def ecrire_parametres(p: ParametresIn, user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    actuels = pdb.parametres(user)
    choisi = p.indice if p.indice is not None else actuels.get("indice")
    if choisi not in md.INDICES:
        choisi = "MSCI_WORLD"
    pdb.enregistrer_parametres(
        user,
        actuels.get("prelevement_div_pct", 0.0) if p.prelevement_div_pct is None else p.prelevement_div_pct,
        choisi,
        actuels.get("mode_indice") or "euros_dividendes" if p.mode_indice is None else p.mode_indice,
        actuels.get("taux_sans_risque", 2.0) if p.taux_sans_risque is None else p.taux_sans_risque,
        actuels.get("cible_mensuelle_eur", 0.0) if p.cible_mensuelle_eur is None
        else p.cible_mensuelle_eur)
    return pdb.parametres(user)


# ─────────────────────────── Dividendes automatiques ────────────────────────

@router.post("/compte/dividendes/synchroniser")
async def synchroniser_dividendes(user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    """
    Pour chaque titre détenu (ou déjà détenu), récupère sur Yahoo Finance les dividendes
    détachés depuis le premier achat. Si vous déteniez des titres la veille du détachement,
    le dividende est crédité en espèces (brut − prélèvement choisi dans les paramètres)
    et une notification est créée. Un dividende déjà traité n'est jamais recrédité,
    même si vous supprimez l'opération.
    """
    ops = pdb.operations(user, pf)
    achats = [o for o in ops if o["type"] == "ACHAT"]
    if not achats:
        return {"nouveaux": []}
    prelevement = pdb.parametres(user)["prelevement_div_pct"] / 100
    premier_achat = {}
    for o in achats:
        premier_achat[o["ticker"]] = min(premier_achat.get(o["ticker"], o["date"]), o["date"])
    aujourd_hui = date.today().isoformat()
    nouveaux = []
    manuels = [o for o in ops if o["type"] == "DIVIDENDE" and (o.get("origine") or "manuel") == "manuel"]

    # Divisions d'actions (OST) : quantité multipliée, PRU divisé, valeur inchangée
    splits = await asyncio.gather(*(asyncio.to_thread(md.splits_par_action, t, d)
                                    for t, d in premier_achat.items()))
    for (tk, _), liste in zip(premier_achat.items(), splits):
        for date_split, ratio in liste:
            if date_split > aujourd_hui or pdb.dividende_deja_traite(user, pf, f"SPLIT:{tk}", date_split):
                continue
            veille = (pd.Timestamp(date_split) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            pos = calc.rejouer(pdb.operations(user, pf), jusqu_au=veille).positions.get(tk)
            pdb.marquer_dividende(user, pf, f"SPLIT:{tk}", date_split)
            if not pos or pos.quantite <= calc.EPS:
                continue
            nom = (pdb.fiche_titre(tk) or {}).get("nom") or tk
            pdb.ajouter_operation(user, pf, {
                "date": date_split, "type": "SPLIT", "ticker": tk, "quantite": ratio,
                "devise": pos.devise, "taux_eur": 1.0, "origine": "auto",
                "commentaire": f"Division par {_frg(ratio)} : {_frg(pos.quantite)} → "
                               f"{_frg(pos.quantite * ratio)} titres, PAM divisé par {_frg(ratio)}"})
            pdb.notifier(user, f"Division d'actions : {nom}",
                         f"{_frg(ratio)} nouvelles actions pour 1 ancienne le "
                         f"{pd.Timestamp(date_split).strftime('%d/%m/%Y')} : votre position passe de "
                         f"{_frg(pos.quantite)} à {_frg(pos.quantite * ratio)} titres, "
                         f"pour une valeur identique.")
            nouveaux.append({"ticker": tk, "nom": nom, "date": date_split, "type": "SPLIT", "ratio": ratio})

    historiques = await asyncio.gather(*(asyncio.to_thread(md.dividendes_par_action, t, d)
                                         for t, d in premier_achat.items()))
    for (tk, _), divs in zip(premier_achat.items(), historiques):
        for date_detach, montant_action in divs:
            if date_detach > aujourd_hui or pdb.dividende_deja_traite(user, pf, tk, date_detach):
                continue
            veille = (pd.Timestamp(date_detach) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            etat = calc.rejouer(pdb.operations(user, pf), jusqu_au=veille)
            pos = etat.positions.get(tk)
            if not pos or pos.quantite <= calc.EPS:
                pdb.marquer_dividende(user, pf, tk, date_detach)
                continue
            # Dividende déjà saisi à la main autour de cette date → on ne double pas
            d0 = pd.Timestamp(date_detach)
            if any(m["ticker"] == tk and -5 <= (pd.Timestamp(m["date"]) - d0).days <= 45 for m in manuels):
                pdb.marquer_dividende(user, pf, tk, date_detach)
                continue
            taux = await asyncio.to_thread(md.taux_eur, pos.devise, date_detach)
            brut_eur = pos.quantite * montant_action * taux
            net_eur = round(brut_eur * (1 - prelevement), 2)
            if net_eur <= 0:
                pdb.marquer_dividende(user, pf, tk, date_detach)
                continue
            nom = (pdb.fiche_titre(tk) or {}).get("nom") or tk
            op = {"date": date_detach, "type": "DIVIDENDE", "ticker": tk, "montant_eur": net_eur,
                  "quantite": pos.quantite, "prix": montant_action,     # affichés dans le carnet
                  "devise": pos.devise, "taux_eur": taux, "origine": "auto",
                  "commentaire": (f"Automatique : {_frg(pos.quantite)} × {_frg(montant_action)} {pos.devise}, "
                                  f"brut {_fr(brut_eur)} €"
                                  + (f", prélèvement {_frg(prelevement * 100)} %" if prelevement else ""))}
            pdb.ajouter_operation(user, pf, op)
            pdb.marquer_dividende(user, pf, tk, date_detach)
            pdb.notifier(user, f"Dividende versé : {nom}",
                         f"{_fr(net_eur)} € crédités en espèces ({_frg(pos.quantite)} titres × "
                         f"{_frg(montant_action)} {pos.devise}, détaché le "
                         f"{pd.Timestamp(date_detach).strftime('%d/%m/%Y')}).")
            nouveaux.append({"ticker": tk, "nom": nom, "date": date_detach, "net_eur": net_eur,
                             "type": "DIVIDENDE"})
    return {"nouveaux": nouveaux}


# ─────────────────────────── Notifications ──────────────────────────────────

@router.get("/compte/notifications")
def notifications(user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    notifs = pdb.notifications(user)
    return {"notifications": notifs, "non_lues": sum(1 for n in notifs if not n["lue"])}


@router.delete("/compte/notifications")
def effacer_notifications(user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    pdb.effacer_notifications(user)
    return {"ok": True}


@router.post("/compte/notifications/lues")
def notifications_lues(user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    pdb.marquer_notifications_lues(user)
    return {"ok": True}


# ─────────────────────────── Import d'un export courtier ───────────────────

class LigneImport(BaseModel):
    """
    Une ligne validée dans l'écran d'aperçu : soit une opération sur titre
    (ticker, quantité, prix), soit un mouvement d'espèces (montant seul).
    """
    sens:        str = Field(default="ACHAT")
    date:        str
    ticker:      Optional[str] = Field(default=None, max_length=20)
    quantite:    Optional[float] = Field(default=None, gt=0)
    pru:         Optional[float] = Field(default=None, gt=0)
    montant_eur: Optional[float] = Field(default=None, gt=0)
    frais:       float = Field(default=0.0, ge=0)
    nom:         Optional[str] = None
    isin:        Optional[str] = None

    @field_validator("date")
    @classmethod
    def _date(cls, v: str) -> str:
        d = datetime.strptime(v, "%Y-%m-%d").date()
        if d > date.today():
            raise ValueError("la date ne peut pas être dans le futur")
        return d.isoformat()

    @field_validator("sens")
    @classmethod
    def _sens(cls, v: str) -> str:
        v = (v or "ACHAT").strip().upper()
        if v not in calc.TYPES_OPERATION:
            raise ValueError(f"sens doit être parmi {', '.join(calc.TYPES_OPERATION)}")
        return v

    def verifier(self) -> Optional[str]:
        """Message d'erreur si la ligne est incomplète pour sa nature, None sinon."""
        if self.sens in ("ACHAT", "VENTE"):
            if not (self.ticker and self.quantite and self.pru):
                return "ticker, quantité et prix sont obligatoires"
        elif not self.montant_eur:
            return "montant obligatoire"
        return None


class ImportValidation(BaseModel):
    lignes: List[LigneImport] = Field(..., min_length=1, max_length=500)
    remplacer: bool = False          # true : les positions existantes sont d'abord soldées
    # Libellés que l'utilisateur vient de classer lui-même : retenus pour les prochains
    # imports, de sorte que la question ne soit posée qu'une fois par banque.
    mappages: dict = Field(default_factory=dict)
    # Nature du fichier d'origine. Elle décide si les dates sont de VRAIES dates
    # d'exécution (relevé d'opérations, carnet d'ordres) ou une date déduite par
    # l'export de positions — ce qui change la façon de valoriser la première journée.
    type_fichier: str = Field(default="positions")


@router.post("/compte/import/apercu")
async def import_apercu(fichier: UploadFile = File(...), user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    """
    Lit un export de positions (Boursorama, Fortuneo, Degiro…) et prépare l'import.

    Rien n'est enregistré à ce stade : pour chaque ligne du fichier, le ticker Yahoo
    est recherché à partir de l'ISIN (puis du nom), et la ligne est renvoyée avec les
    cotations trouvées pour que l'utilisateur confirme ou corrige avant d'importer.
    """
    contenu = await fichier.read()
    if len(contenu) > 2_000_000:
        raise HTTPException(413, detail="Fichier trop volumineux (2 Mo maximum).")
    try:
        # Les libellés déjà classés par l'utilisateur lors d'un import précédent sont
        # réappliqués : l'application ne repose jamais deux fois la même question.
        # Le format est reconnu au contenu, pas à l'extension : un PDF commence
        # toujours par « %PDF », quel que soit le nom du fichier.
        mappages = pdb.mappages_import(user)
        if contenu[:5] == b"%PDF-" or (fichier.filename or "").lower().endswith(".pdf"):
            lu = lire_releve_pdf(contenu, mappages)
        else:
            lu = lire_positions(contenu, mappages)
    except ImportInvalide as e:
        raise HTTPException(422, detail=str(e))

    espece = lambda l: (l.get("nature") or l.get("sens")) in ("DEPOT", "RETRAIT", "DIVIDENDE")
    resolutions = await asyncio.gather(*(
        asyncio.to_thread(md.resoudre_isin, l["isin"] or l["ticker_fichier"] or "", l["nom"])
        if not espece(l) else asyncio.sleep(0, result={})
        for l in lu["lignes"]))

    etat_actuel = calc.rejouer(pdb.operations(user, pf))
    deja_detenu = {p.ticker for p in etat_actuel.positions_ouvertes()}
    # Quantités détenues aujourd'hui, pour vérifier que les ventes du fichier sont
    # couvertes. Un relevé partiel vend souvent des titres achetés AVANT la période
    # couverte : mieux vaut le dire dans l'aperçu que planter à la validation.
    detenu = {p.ticker: p.quantite for p in etat_actuel.positions_ouvertes()}
    # Empreinte des opérations déjà enregistrées, pour repérer un relevé qui recouvre
    # partiellement un import précédent plutôt que de créer des doublons silencieux.
    empreintes = {(o["date"], o["type"], o.get("ticker"),
                   round(float(o.get("quantite") or 0), 4),
                   round(float(o.get("prix") or o.get("montant_eur") or 0), 2))
                  for o in pdb.operations(user, pf)}
    lignes = []
    for ligne, res in zip(lu["lignes"], resolutions):
        ticker = res.get("ticker")
        # Simulation au fil de l'eau : les lignes sont déjà en ordre chronologique
        decouvert = None
        if ticker and ligne.get("nature") in ("ACHAT", "VENTE") and ligne.get("quantite"):
            q = float(ligne["quantite"])
            if ligne["nature"] == "ACHAT":
                detenu[ticker] = detenu.get(ticker, 0.0) + q
            else:
                dispo = detenu.get(ticker, 0.0)
                if q > dispo + 1e-9:
                    decouvert = (
                        f"Ce relevé vend {_frg(q)} titres alors que {_frg(dispo)} seulement sont "
                        f"connus à cette date : l'achat correspondant est antérieur au fichier. "
                        f"Importez d'abord le relevé de la période précédente, ou saisissez la "
                        f"position d'origine, puis reprenez cet import.")
                detenu[ticker] = max(dispo - q, 0.0)
        lignes.append({
            **ligne,
            "sens": ligne.get("sens") or "ACHAT",
            "frais": ligne.get("frais") or 0.0,
            "ticker": ticker,
            "nom_yahoo": res.get("nom"),
            "devise": res.get("devise"),
            "place": res.get("place_nom") or res.get("place"),
            "trouve_par": res.get("source"),
            "alternatives": res.get("alternatives", []),
            "deja_en_portefeuille": ticker in deja_detenu if ticker else False,
            "doublon": (ligne["date"], ligne.get("nature") or "ACHAT", ticker,
                        round(float(ligne.get("quantite") or 0), 4),
                        round(float(ligne.get("pru") or ligne.get("montant_eur") or 0), 2))
                       in empreintes,
            "date": ligne["date"] or date.today().isoformat(),
            "espece": espece(ligne),
            "importable": (bool(ligne.get("montant_eur")) if espece(ligne)
                           else bool(ticker and ligne["quantite"] and ligne["pru"]
                                     and not decouvert)),
            "avertissement": (
                None if espece(ligne) else
                "Titre introuvable sur Yahoo Finance : saisissez le ticker à la main."
                if not ticker else
                "Prix absent du fichier : saisissez-le à la main."
                if not ligne["pru"] else
                decouvert if decouvert else
                "Date absente du fichier : la date du jour est proposée."
                if not ligne["date"] else None),
        })
    return {"lignes": lignes, "colonnes_reconnues": lu["colonnes_reconnues"],
            "decisions": lu.get("decisions", {}),
            "ignorees": lu["ignorees"], "a_classer": lu.get("a_classer", []),
            "avertissements": lu.get("avertissements", []),
            "format_date": lu.get("format_date"), "fichier": fichier.filename,
            "type": lu.get("type", "positions")}


class MappagesIn(BaseModel):
    mappages: dict = Field(default_factory=dict)
    # Nature du fichier d'origine. Elle décide si les dates sont de VRAIES dates
    # d'exécution (relevé d'opérations, carnet d'ordres) ou une date déduite par
    # l'export de positions — ce qui change la façon de valoriser la première journée.
    type_fichier: str = Field(default="positions")


@router.post("/compte/import/mappages")
def enregistrer_mappages(req: MappagesIn, user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    """
    Retient le classement d'un libellé d'opération inconnu (« DROITS DE GARDE » →
    retrait, par exemple). Au prochain import, l'application le reconnaîtra seule :
    la question n'est posée qu'une fois par banque.
    """
    valides = {k.strip(): v for k, v in req.mappages.items()
               if k and v in calc.TYPES_OPERATION}
    inconnus = set(req.mappages) - set(valides)
    if inconnus:
        raise HTTPException(422, detail="Nature inconnue pour : " + ", ".join(sorted(inconnus)))
    pdb.enregistrer_mappages(user, valides)
    return {"ok": True, "enregistres": len(valides)}


@router.post("/compte/import/valider")
async def import_valider(req: ImportValidation, user: str = Depends(utilisateur_courant), pf: int = Depends(portefeuille_courant)):
    """
    Enregistre les lignes validées : chacune devient un ordre daté, visible dans
    l'onglet Ordres. Un relevé d'opérations crée les achats et les ventes dans l'ordre
    chronologique, ce qui reconstitue l'historique réel du portefeuille ; un export de
    positions crée un achat par ligne, à son prix de revient.
    """
    existantes = pdb.operations(user, pf)
    etat = calc.rejouer(existantes)
    cree, soldees, erreurs = [], [], []
    # « import_ordres » : la date vient du relevé, c'est celle de l'exécution réelle.
    # « import_positions » : la date est celle du dernier mouvement connu, donc une
    # approximation — on ne peut pas s'y fier pour valoriser au prix d'achat.
    origine = "import_ordres" if req.type_fichier in ("operations", "ordres") else "import_positions"
    if req.mappages:
        pdb.enregistrer_mappages(user, {k: v for k, v in req.mappages.items()
                                        if v in calc.TYPES_OPERATION})

    for l in req.lignes:
        probleme = l.verifier()
        if probleme:
            erreurs.append({"ticker": l.ticker or l.nom or l.sens, "motif": probleme})
            continue

        # Mouvement d'espèces : versement, retrait ou dividende
        if l.sens in ("DEPOT", "RETRAIT", "DIVIDENDE"):
            op = {"date": l.date, "type": l.sens, "ticker": None, "quantite": None,
                  "prix": None, "devise": "EUR", "taux_eur": 1.0, "frais_eur": 0.0,
                  "montant_eur": l.montant_eur, "origine": origine,
                  "commentaire": (l.nom or "")[:120] or "Importé depuis un export courtier"}
            try:
                calc.valider_nouvelle_operation(pdb.operations(user, pf), {**op, "id": 10**12})
            except calc.OperationInvalide as e:
                erreurs.append({"ticker": l.nom or l.sens, "motif": str(e)})
                continue
            pdb.ajouter_operation(user, pf, op)
            cree.append({"ticker": None, "nom": l.nom or l.sens, "montant_eur": l.montant_eur,
                         "date": l.date, "sens": l.sens})
            etat = calc.rejouer(pdb.operations(user, pf))
            continue

        tk = l.ticker.strip().upper()
        fiche = await _fiche(tk)
        if not fiche:
            erreurs.append({"ticker": tk, "motif": "introuvable sur Yahoo Finance"})
            continue
        devise = fiche.get("devise") or "EUR"

        # Remplacement : on solde d'abord la position existante au prix de revient,
        # ce qui la neutralise sans créer de plus-value artificielle.
        pos = etat.positions.get(tk)
        if req.remplacer and pos and pos.quantite > calc.EPS:
            pdb.ajouter_operation(user, pf, {
                "date": l.date, "type": "VENTE", "ticker": tk, "quantite": pos.quantite,
                "prix": pos.pru, "devise": pos.devise,
                "taux_eur": await asyncio.to_thread(md.taux_eur, pos.devise, l.date),
                "frais_eur": 0.0, "origine": origine,
                "commentaire": "Position remplacée par l'import"})
            soldees.append(tk)

        taux = await asyncio.to_thread(md.taux_eur, devise, l.date)
        op = {"date": l.date, "type": l.sens, "ticker": tk, "quantite": l.quantite,
              "prix": l.pru, "devise": devise, "taux_eur": taux, "frais_eur": l.frais,
              "origine": origine,
              "commentaire": "Importé depuis un export courtier"
                             + (f" · ISIN {l.isin}" if l.isin else "")}
        try:                        # une vente sans titres détenus serait incohérente
            calc.valider_nouvelle_operation(pdb.operations(user, pf), {**op, "id": 10**12})
        except calc.OperationInvalide as e:
            erreurs.append({"ticker": tk, "motif": str(e)})
            continue
        pdb.ajouter_operation(user, pf, op)
        cree.append({"ticker": tk, "nom": fiche.get("nom"), "quantite": l.quantite,
                     "pru": l.pru, "devise": devise, "date": l.date, "sens": l.sens})
        etat = calc.rejouer(pdb.operations(user, pf))

    _cache_serie.pop(user, None)
    if cree:
        pdb.notifier(user, "Import réalisé",
                     f"{len(cree)} ligne(s) ajoutée(s) depuis un export courtier"
                     + (f", {len(soldees)} position(s) remplacée(s)" if soldees else "") + ".")
    return {"importees": cree, "remplacees": soldees, "erreurs": erreurs}


@router.get("/titres/recherche/{requete}")
async def recherche_titre(requete: str):
    """Recherche par ISIN, ticker ou nom — utilisée pour corriger une ligne d'import."""
    return {"resultats": await asyncio.to_thread(md.rechercher_titre, requete.strip())}
