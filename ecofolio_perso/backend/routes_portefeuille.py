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
"""

import asyncio
import logging
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import List, Optional

import httpx
import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from agents import market_data as md
from agents.news_agent import NewsAgent
from core import portfolio_calc as calc
from core.portfolio_db import PortfolioDB

logger = logging.getLogger("portefeuille")
router = APIRouter()
pdb = PortfolioDB()

TAUX_SANS_RISQUE = 0.02
INDICE_REFERENCE = "IWDA.AS"          # ETF MSCI World coté en EUR
LIBELLE_INDICE = "MSCI World (ETF IWDA, en €)"


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


@router.get("/titres/{ticker}")
async def titre(ticker: str):
    tk = ticker.strip().upper()
    fiche = await _fiche(tk, avec_prix=True)
    if not fiche:
        raise HTTPException(status_code=404,
                            detail=f"Titre « {tk} » introuvable sur Yahoo Finance. "
                                   f"Format attendu : MC.PA, AAPL, SAP.DE, ASML.AS…")
    fiche["taux_eur"] = _r(await asyncio.to_thread(md.taux_eur, fiche["devise"]), 6)
    fiche["zone"] = md.zone_de(fiche.get("pays"))
    return fiche


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


@router.post("/compte/operations")
async def ajouter_operation(op: OperationIn, user: str = Depends(utilisateur_courant)):
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

    existantes = pdb.operations(user)
    try:
        calc.valider_nouvelle_operation(existantes, {**nouvelle, "id": 10**12})
    except calc.OperationInvalide as e:
        raise HTTPException(422, detail=str(e))

    op_id = pdb.ajouter_operation(user, nouvelle)
    return {"id": op_id, "operation": nouvelle}


@router.delete("/compte/operations/{op_id}")
def supprimer_operation(op_id: int, user: str = Depends(utilisateur_courant)):
    existantes = pdb.operations(user)
    if not any(o["id"] == op_id for o in existantes):
        raise HTTPException(404, detail="Opération introuvable.")
    try:
        calc.valider_suppression(existantes, op_id)
    except calc.OperationInvalide as e:
        raise HTTPException(422, detail=f"Suppression impossible, l'historique deviendrait "
                                        f"incohérent : {e}")
    pdb.supprimer_operation(user, op_id)
    return {"ok": True}


@router.get("/compte/operations")
def carnet(user: str = Depends(utilisateur_courant)):
    etat = calc.rejouer(pdb.operations(user))
    noms = {}
    for o in etat.operations:
        if o.get("ticker") and o["ticker"] not in noms:
            f = pdb.fiche_titre(o["ticker"])
            noms[o["ticker"]] = (f or {}).get("nom") or o["ticker"]
        o["nom"] = noms.get(o.get("ticker"))
    return {"operations": list(reversed(etat.operations))}


# ─────────────────────────── Synthèse ───────────────────────────────────────

async def _synthese(user: str) -> dict:
    etat = calc.rejouer(pdb.operations(user))
    ouvertes = etat.positions_ouvertes()
    tickers = [p.ticker for p in ouvertes]

    cours = await asyncio.to_thread(md.cours_actuels, tickers)
    devises = sorted({p.devise for p in ouvertes})
    taux = {d: await asyncio.to_thread(md.taux_eur, d) for d in devises}

    lignes, total_titres = [], 0.0
    for p in ouvertes:
        fiche = await _fiche(p.ticker) or {}
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
            "secteur": fiche.get("secteur") or "Non renseigné",
            "pays": md.pays_fr(fiche.get("pays")),
            "zone": md.zone_de(fiche.get("pays")),
            "cours": _r(prix, 4), "cours_indisponible": c is None,
            "date_cours": c["date_cours"] if c else None,
            "var_jour_pct": _r((prix / veille - 1) * 100 if veille else 0),
            "var_jour_eur": _r(p.quantite * (prix - veille) * t),
            "valeur_devise": _r(valeur_dev), "valeur_eur": _r(valeur_eur),
            "pv_latente_devise": _r((prix - p.pru) * p.quantite),
            "pv_latente_pct": _r((prix / p.pru - 1) * 100 if p.pru else 0),
            "pv_latente_eur": _r(valeur_eur - p.cout_eur),
            "taux_eur": _r(t, 6),
        })
    for l in lignes:
        l["poids_pct"] = _r(l["valeur_eur"] / total_titres * 100 if total_titres else 0)
    lignes.sort(key=lambda l: l["valeur_eur"] or 0, reverse=True)

    def repartition(cle: str) -> List[dict]:
        agg = {}
        for l in lignes:
            agg[l[cle]] = agg.get(l[cle], 0.0) + (l["valeur_eur"] or 0)
        return sorted(({"label": k, "valeur_eur": _r(v),
                        "poids_pct": _r(v / total_titres * 100 if total_titres else 0)}
                       for k, v in agg.items()), key=lambda x: -x["valeur_eur"])

    valorisation = total_titres + etat.cash_eur
    pnl_total = valorisation - etat.depots_nets_eur
    soldees = []
    for p in etat.positions_soldees():
        f = pdb.fiche_titre(p.ticker) or {}
        soldees.append({**p.to_dict(), "nom": f.get("nom") or p.ticker})

    return {
        "username": user,
        "totaux": {
            "valorisation_eur": _r(valorisation),
            "titres_eur": _r(total_titres),
            "cash_eur": _r(etat.cash_eur),
            "depots_nets_eur": _r(etat.depots_nets_eur),
            "pv_latente_eur": _r(sum(l["pv_latente_eur"] or 0 for l in lignes)),
            "pv_realisee_eur": _r(etat.pv_realisee_eur),
            "dividendes_eur": _r(etat.dividendes_eur),
            "frais_eur": _r(etat.frais_eur),
            "pnl_total_eur": _r(pnl_total),
            "perf_pct": _r(pnl_total / etat.depots_nets_eur * 100 if etat.depots_nets_eur > 0 else 0),
            "var_jour_eur": _r(sum(l["var_jour_eur"] or 0 for l in lignes)),
            "n_lignes": len(lignes),
        },
        "positions": lignes,
        "positions_soldees": soldees,
        "repartition": {
            "zone": repartition("zone"), "secteur": repartition("secteur"),
            "pays": repartition("pays"), "devise": repartition("devise"),
            "classe": [
                {"label": "Actions / ETF", "valeur_eur": _r(total_titres),
                 "poids_pct": _r(total_titres / valorisation * 100 if valorisation > 0 else 0)},
                {"label": "Liquidités", "valeur_eur": _r(etat.cash_eur),
                 "poids_pct": _r(etat.cash_eur / valorisation * 100 if valorisation > 0 else 0)},
            ],
        },
    }


@router.get("/compte/synthese")
async def synthese(user: str = Depends(utilisateur_courant)):
    return await _synthese(user)


# ─────────────────────────── Historique de performance ─────────────────────

@router.get("/compte/historique")
async def historique(user: str = Depends(utilisateur_courant)):
    """
    Reconstitue la valorisation du compte chaque jour ouvré depuis la 1re opération.
    PnL = valorisation − dépôts nets. Performance = rendement pondéré dans le temps (TWR),
    qui neutralise l'effet des dépôts et retraits (comme les fonds).
    """
    ops = sorted(pdb.operations(user), key=lambda o: (o["date"], o["id"]))
    if not ops:
        return {"points": []}
    debut = ops[0]["date"]
    tickers = sorted({o["ticker"] for o in ops if o["type"] in ("ACHAT", "VENTE")})
    devises = sorted({o["devise"] for o in ops if o["type"] in ("ACHAT", "VENTE")})

    clotures = await asyncio.to_thread(md.historique_clotures, tickers, debut)
    taux = await asyncio.to_thread(md.historique_taux, devises or ["EUR"], debut)

    jours = pd.bdate_range(debut, date.today())
    clotures = clotures.reindex(jours).ffill() if not clotures.empty else pd.DataFrame(index=jours)
    taux = taux.reindex(jours).ffill().bfill()

    etat, i, v_prec, indice_twr = calc.EtatCompte(), 0, None, 1.0
    dernier_prix = {}
    points = []
    for j in jours:
        jour = j.strftime("%Y-%m-%d")
        flux_externes = 0.0
        while i < len(ops) and ops[i]["date"] <= jour:
            o = ops[i]
            calc._appliquer(etat, o)
            if o["type"] in ("ACHAT", "VENTE"):
                dernier_prix[o["ticker"]] = float(o["prix"])
            if o["type"] == "DEPOT":
                flux_externes += float(o["montant_eur"])
            elif o["type"] == "RETRAIT":
                flux_externes -= float(o["montant_eur"])
            i += 1
        valeur_titres = 0.0
        for p in etat.positions_ouvertes():
            px = clotures[p.ticker].get(j) if p.ticker in clotures else None
            if px is None or pd.isna(px):
                px = dernier_prix.get(p.ticker, p.pru)
            t = taux[p.devise].get(j) if p.devise in taux else 1.0
            valeur_titres += p.quantite * float(px) * float(t if pd.notna(t) else 1.0)
        valeur = valeur_titres + etat.cash_eur
        if v_prec and v_prec > 0:
            indice_twr *= (valeur - flux_externes) / v_prec
        v_prec = valeur
        points.append({
            "date": jour, "valorisation_eur": _r(valeur), "titres_eur": _r(valeur_titres),
            "cash_eur": _r(etat.cash_eur), "depots_nets_eur": _r(etat.depots_nets_eur),
            "pnl_eur": _r(valeur - etat.depots_nets_eur),
            "perf_twr_pct": _r((indice_twr - 1) * 100),
        })
    return {"points": points}


# ─────────────────────────── Indicateurs de risque ──────────────────────────

@router.get("/compte/risque")
async def risque(user: str = Depends(utilisateur_courant)):
    """
    Indicateurs calculés sur 1 an de cours, avec la composition ACTUELLE du portefeuille
    (poids en EUR), rendements convertis en EUR (le risque de change est donc inclus).
    """
    syn = await _synthese(user)
    lignes = syn["positions"]
    if not lignes:
        return {"disponible": False, "message": "Aucune position ouverte."}

    tickers = [l["ticker"] for l in lignes]
    poids = np.array([l["valeur_eur"] or 0 for l in lignes], dtype=float)
    poids = poids / poids.sum()
    debut = (pd.Timestamp.today() - pd.DateOffset(years=1)).strftime("%Y-%m-%d")

    clotures = await asyncio.to_thread(md.historique_clotures, tickers + [INDICE_REFERENCE], debut)
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
    rend = float(r_p.mean() * 252)

    q5 = float(np.percentile(r_p, 5))
    var95 = -q5
    cvar95 = -float(r_p[r_p <= q5].mean())
    trajectoire = np.cumprod(1 + r_p)
    max_dd = float((trajectoire / np.maximum.accumulate(trajectoire) - 1).min())

    beta = correl = None
    r_idx = None
    if INDICE_REFERENCE in clotures and clotures[INDICE_REFERENCE].notna().sum() > 30:
        r_idx = clotures[INDICE_REFERENCE].pct_change().reindex(rdt.index).fillna(0.0).values
        v_idx = float(np.var(r_idx, ddof=1))
        if v_idx > 0:
            beta = float(np.cov(r_p, r_idx, ddof=1)[0, 1] / v_idx)
            correl = float(np.corrcoef(r_p, r_idx)[0, 1])

    contrib = (w * (cov @ w)) / (vol ** 2) if vol > 0 else np.zeros_like(w)
    vols_ind = np.sqrt(np.diag(cov))
    hhi = float((poids ** 2).sum())
    valeur_titres = syn["totaux"]["titres_eur"] or 0
    non_eur = sum(l["poids_pct"] or 0 for l in lignes if l["devise"] != "EUR")

    par_ligne = []
    for k, c in enumerate(cols):
        l = next(x for x in lignes if x["ticker"] == c)
        b = None
        if r_idx is not None and np.var(r_idx) > 0:
            b = float(np.cov(rdt[c].values, r_idx, ddof=1)[0, 1] / np.var(r_idx, ddof=1))
        par_ligne.append({"ticker": c, "nom": l["nom"], "poids_pct": _r(w[k] * 100),
                          "volatilite_pct": _r(vols_ind[k] * 100),
                          "contribution_risque_pct": _r(contrib[k] * 100),
                          "beta": _r(b)})
    par_ligne.sort(key=lambda x: -(x["contribution_risque_pct"] or 0))

    return {
        "disponible": True,
        "periode": f"{rdt.index[0].strftime('%d/%m/%Y')} → {rdt.index[-1].strftime('%d/%m/%Y')}",
        "n_jours": len(rdt),
        "indice_reference": LIBELLE_INDICE,
        "volatilite_pct": _r(vol * 100),
        "rendement_1an_pct": _r(rend * 100),
        "sharpe": _r((rend - TAUX_SANS_RISQUE) / vol if vol > 0 else None),
        "var95_1j_pct": _r(var95 * 100),
        "var95_1j_eur": _r(var95 * valeur_titres),
        "cvar95_1j_pct": _r(cvar95 * 100),
        "max_drawdown_pct": _r(max_dd * 100),
        "beta": _r(beta),
        "correlation_indice": _r(correl),
        "concentration": {
            "premiere_ligne_pct": _r(max(poids) * 100),
            "trois_premieres_pct": _r(sum(sorted(poids, reverse=True)[:3]) * 100),
            "hhi": _r(hhi, 4),
            "nb_lignes_effectif": _r(1 / hhi if hhi > 0 else 0, 1),
        },
        "exposition_devises_hors_eur_pct": _r(non_eur),
        "par_ligne": par_ligne,
    }


# ─────────────────────────── Actualités ─────────────────────────────────────

@router.get("/compte/actualites")
async def actualites(user: str = Depends(utilisateur_courant)):
    etat = calc.rejouer(pdb.operations(user))
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
