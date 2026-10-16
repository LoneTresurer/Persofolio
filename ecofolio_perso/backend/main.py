"""
EcoFolio API v9.0
=================
POST /optimiser              → Pipeline ESG complet + sauvegarde session
POST /portefeuille/perso     → Analyse d'un portefeuille composé par l'investisseur
POST /suivi                  → Rendement RÉEL reçu (cours Yahoo Finance) vs prédiction + recalibration
POST /feedback               → (hérité) Analyse rétrospective + 4 couches de validation
POST /feedback/pdf           → Upload relevé courtier PDF
POST /portfolio/performance  → Performance historique pondérée (Yahoo Finance)
GET  /prices/{ticker}        → Historique de prix (Yahoo Finance)
GET  /session/{id}           → Session passée
GET  /sessions               → Liste sessions
GET  /feedback/{id}          → Feedbacks d'une session
GET  /esg/{ticker}           → Score ESG
"""

import math, logging, traceback, asyncio
from typing import Optional, List
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents.collector_agent    import CollectorAgent
from agents.optimizer_agent    import OptimizerAgent
from agents.esg_agent          import ESGAgent
from agents.predictor_agent    import PredictorAgent
from agents.news_agent         import NewsAgent
from agents.feedback_validator import valider_feedback_complet, extraire_donnees_pdf
from core.a_calculator         import calculer_A_markowitz, interpreter_A
from core.esg_horizon_model    import get_profil_horizon
from core.session_db           import SessionDB

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger("ecofolio")
db     = SessionDB()

def _san(obj):
    if isinstance(obj, dict):  return {k: _san(v) for k,v in obj.items()}
    if isinstance(obj, list):  return [_san(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)): return None
    return obj

app = FastAPI(title="EcoFolio API", version="10.0.0", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# Suivi de portefeuille (compte, ordres, PnL, risque, actualités) → routes_portefeuille.py
from routes_portefeuille import router as router_portefeuille
app.include_router(router_portefeuille, tags=["Suivi de portefeuille"])

# ─────────────────────── Modèles ─────────────────────────────────────────────

class ProfilInvestisseur(BaseModel):
    att1:int=Field(...,ge=1,le=5); att2:int=Field(...,ge=1,le=5)
    att3:int=Field(...,ge=1,le=5); att4:int=Field(...,ge=1,le=5)
    tolerance:int=Field(...,ge=1,le=10)
    invested:bool; had_loss:bool
    choices:List[str]=Field(...,min_length=5,max_length=5)
    esg_seuil:float=Field(default=59.0,ge=40.0,le=80.0)
    pays:Optional[str]=None
    horizon:int=Field(default=10,ge=1,le=30)
    montant:float=Field(default=10000.0,ge=100.0)

class FeedbackRequest(BaseModel):
    session_id:      str
    montant_initial: float = Field(..., gt=0)
    montant_final:   float = Field(..., gt=0)
    dividendes:      float = Field(default=0.0, ge=0)
    frais:           float = Field(default=0.0, ge=0)
    duree_mois:      int   = Field(..., ge=1, le=360)

class PortfolioPerfRequest(BaseModel):
    tickers: List[str]
    poids:   List[float]
    period:  str = Field(default="1y")

class SuiviRequest(BaseModel):
    """
    Requête de suivi (page « Mon Suivi »).

    L'investisseur ne saisit JAMAIS de rendement ni de montant final : il fournit
    son identifiant de session (→ le programme retrouve le portefeuille réellement
    recommandé et sa date), l'horizon réellement écoulé en jours, ses frais de
    courtage et — si l'horizon dépasse 1 an — les dividendes perçus, chacun avec
    sa devise. Le backend va ensuite chercher les VRAIS prix de marché des actions
    du portefeuille sur la période écoulée pour calculer le rendement réellement reçu.
    """
    session_id:        str
    horizon_jours:     int   = Field(..., ge=1, le=10950)   # durée réellement écoulée
    frais:             float = Field(default=0.0, ge=0)
    frais_devise:      str   = Field(default="EUR")
    dividendes:        float = Field(default=0.0, ge=0)
    dividendes_devise: str   = Field(default="EUR")

class LignePerso(BaseModel):
    ticker: str   = Field(..., min_length=1, max_length=20)
    poids:  float = Field(..., gt=0, le=100)        # en % du portefeuille

class PortefeuillePerso(BaseModel):
    """Portefeuille composé à la main : l'investisseur choisit titres et poids."""
    lignes:  List[LignePerso] = Field(..., min_length=1, max_length=40)
    montant: float = Field(default=10000.0, ge=100.0)
    horizon: int   = Field(default=10, ge=1, le=30)
    indice:  str   = Field(default="^GSPC")         # indice de référence pour les bêtas

# ─────────────────────── Routes ──────────────────────────────────────────────

@app.get("/")
def root():
    return {"status":"ok","version":"7.0.0"}

@app.get("/health")
def health():
    return {"status":"healthy","version":"10.0.0"}

# ── Données Yahoo Finance (graphes prix) ─────────────────────────────────────

@app.get("/prices/{ticker}")
async def prices(ticker: str, period: str = "1y", interval: str = "1d"):
    """
    Renvoie l'historique de prix d'un ticker (Yahoo Finance via yfinance).
    Utilisé par les mini-graphes prix par action (clic sur une ligne du tableau).

    Réponse :
    {
      "ticker": "...",
      "dates":   [...],
      "prices":  [...],          # closes
      "perf_1y": <float>,         # variation totale en %
      "info":    {"nom": "...", "currency": "..."},
      "n":       <int>
    }
    """
    import yfinance as yf
    try:
        # Validation simple
        allowed_periods   = {"1d","5d","1mo","3mo","6mo","1y","2y","5y","10y","ytd","max"}
        allowed_intervals = {"1m","2m","5m","15m","30m","60m","90m","1h","1d","5d","1wk","1mo","3mo"}
        if period not in allowed_periods:
            period = "1y"
        if interval not in allowed_intervals:
            interval = "1d"

        ticker_upper = ticker.upper()
        tk = yf.Ticker(ticker_upper)

        # Téléchargement de l'historique. yfinance peut renvoyer vide pour de
        # l'intraday lorsque le marché est fermé : on tente alors un repli sur
        # un intervalle plus grossier.
        def _hist(p: str, i: str):
            return tk.history(period=p, interval=i, auto_adjust=True)

        hist = await asyncio.to_thread(_hist, period, interval)
        if hist is None or hist.empty:
            # Repli intraday → 15m, puis 1h, puis 1d
            for fallback in ("15m", "1h", "1d"):
                if fallback == interval:
                    continue
                logger.info(f"/prices/{ticker_upper}: repli intervalle {interval} → {fallback}")
                hist = await asyncio.to_thread(_hist, period, fallback)
                if hist is not None and not hist.empty:
                    interval = fallback
                    break
            if hist is None or hist.empty:
                raise HTTPException(status_code=404, detail=f"Aucune donnée Yahoo Finance pour {ticker_upper}")

        closes = hist["Close"].dropna()
        if len(closes) < 2:
            raise HTTPException(status_code=404, detail=f"Historique trop court pour {ticker_upper}")

        # Format dates selon l'intervalle
        if interval in ("1m","2m","5m","15m","30m","60m","90m","1h"):
            dates = [d.strftime("%Y-%m-%dT%H:%M") for d in closes.index]
        else:
            dates = [d.strftime("%Y-%m-%d") for d in closes.index]

        first  = float(closes.iloc[0])
        last   = float(closes.iloc[-1])
        perf   = (last/first - 1.0) * 100 if first > 0 else 0.0

        # Récupération nom + devise (non bloquant — si ça échoue on met des valeurs par défaut)
        nom = ticker_upper
        currency = ""
        try:
            fast = getattr(tk, "fast_info", None)
            if fast:
                nom = getattr(fast, "long_name", None) or getattr(fast, "short_name", None) or ticker_upper
                currency = getattr(fast, "currency", "") or ""
        except Exception:
            pass
        if not currency:
            try:
                info = await asyncio.to_thread(lambda: tk.info)
                if isinstance(info, dict):
                    nom = info.get("longName") or info.get("shortName") or nom
                    currency = info.get("currency") or currency or ""
            except Exception:
                pass

        return _san({
            "ticker":   ticker_upper,
            "dates":    dates,
            "prices":   [float(v) for v in closes.values],   # ← nom attendu par le frontend
            "first":    first,
            "last":     last,
            "perf_1y":  perf,                                 # ← nom attendu par le frontend
            "info":     {"nom": nom, "currency": currency},   # ← attendu par le frontend
            "n":        int(len(closes)),
            "period":   period,
            "interval": interval,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"/prices/{ticker} a échoué: {e}")
        raise HTTPException(status_code=502, detail=f"Yahoo Finance indisponible: {e}")

@app.post("/portfolio/performance")
async def portfolio_performance(req: PortfolioPerfRequest):
    """
    Calcule la performance historique pondérée du portefeuille via Yahoo Finance.
    - tickers : liste des tickers
    - poids   : liste de poids (somme proche de 1.0, ou en %, peu importe — re-normalisé)
    - period  : '1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y'
    """
    import yfinance as yf
    import numpy as np
    import pandas as pd
    from concurrent.futures import ThreadPoolExecutor

    if not req.tickers:
        raise HTTPException(status_code=400, detail="Liste de tickers vide")
    if len(req.tickers) != len(req.poids):
        raise HTTPException(status_code=400, detail="tickers et poids doivent avoir la même taille")

    # Mapping période → (period_yf, interval, facteur_annualisation, fallbacks_intervalle)
    # Lorsque l'intervalle intraday renvoie vide (marché fermé), on tente un repli
    # vers un intervalle plus grossier puis sur l'historique quotidien.
    period_map = {
        "1d":  ("1d",  "5m",   252*78,  ["15m","30m","1h","1d"]),
        "5d":  ("5d",  "30m",  252*13,  ["1h","1d"]),
        "1mo": ("1mo", "1d",   252,     []),
        "3mo": ("3mo", "1d",   252,     []),
        "6mo": ("6mo", "1d",   252,     []),
        "1y":  ("1y",  "1d",   252,     []),
        "2y":  ("2y",  "1d",   252,     []),
        "5y":  ("5y",  "1wk",  52,      ["1mo"]),
    }
    period_yf, interval, ann_factor, fallbacks = period_map.get(
        req.period, ("1y","1d",252,[])
    )

    def _fetch_one(tk: str):
        # Tente l'intervalle principal, puis les replis si vide
        for ivl in [interval] + fallbacks:
            try:
                data = yf.Ticker(tk).history(period=period_yf, interval=ivl, auto_adjust=True)
                if data is None or data.empty:
                    continue
                s = data["Close"].dropna()
                if len(s) >= 2:
                    return tk, s, ivl
            except Exception as e:
                logger.debug(f"fetch perf {tk} @{ivl}: {e}")
        return tk, None, None

    # Téléchargement parallèle
    series: dict   = {}
    used_intervals = set()
    def _do():
        with ThreadPoolExecutor(max_workers=8) as ex:
            for tk, s, ivl in ex.map(_fetch_one, req.tickers):
                if s is not None:
                    series[tk] = s
                    used_intervals.add(ivl)
    await asyncio.to_thread(_do)

    if not series:
        raise HTTPException(status_code=502, detail="Aucune donnée Yahoo Finance disponible pour ces tickers")

    # ── Alignement robuste des séries ─────────────────────────────────────────
    # Naïvement on faisait `pd.DataFrame(series).dropna()` (how='any'), ce qui
    # supprime toute date où au moins une série est manquante. Avec un
    # portefeuille international (bourses Tokyo, Stockholm, Paris, US, …), les
    # jours fériés ne coïncident pas et l'intersection peut être vide.
    #
    # Stratégie :
    # 1) On construit un DataFrame brut.
    # 2) On démarre à partir de la PREMIÈRE date où TOUS les tickers ont une
    #    cotation (ça gère les actions plus récentes).
    # 3) Ensuite on `ffill` les valeurs manquantes (un marché fermé pour
    #    un jour férié garde le dernier prix coté).
    # 4) On supprime les colonnes encore vides après cette procédure
    #    (tickers qui n'ont aucune donnée sur la période).
    df_raw = pd.DataFrame(series).sort_index()
    # 1ère date où aucune valeur n'est NaN
    no_na_mask = df_raw.notna().all(axis=1)
    first_valid = no_na_mask.idxmax() if no_na_mask.any() else None
    if first_valid is None or not no_na_mask.loc[first_valid]:
        # Aucun jour où tous les tickers ont une valeur → fallback : on garde
        # seulement les tickers qui ont au moins 2 observations sur la période
        df_raw = df_raw.dropna(axis=1, thresh=2)
        if df_raw.empty:
            raise HTTPException(status_code=502, detail="Données insuffisantes après alignement")
        df = df_raw.ffill().bfill().dropna()
    else:
        df = df_raw.loc[first_valid:].ffill()
        # Si malgré tout des colonnes commencent par NaN après ffill, on les
        # bfill (cas limites) puis dropna par colonnes
        df = df.bfill().dropna(axis=1, how='any')

    if df.empty or len(df) < 2 or df.shape[1] == 0:
        raise HTTPException(status_code=502, detail="Données insuffisantes après alignement")

    # Re-normalisation des poids sur les tickers réellement disponibles
    valid_tickers = list(df.columns)
    valid_poids = np.array(
        [req.poids[req.tickers.index(t)] for t in valid_tickers], dtype=float
    )
    total = float(valid_poids.sum())
    if total <= 0:
        raise HTTPException(status_code=400, detail="Poids totaux nuls ou négatifs")
    valid_poids = valid_poids / total  # somme = 1.0

    # Valeur normalisée du portefeuille (base 1.0 au début de la période)
    norm_prices       = df / df.iloc[0]
    portfolio_value   = (norm_prices * valid_poids).sum(axis=1)
    cumulative_pct    = ((portfolio_value - 1.0) * 100).tolist()
    perf_total_pct    = float((portfolio_value.iloc[-1] - 1.0) * 100)

    # Volatilité annualisée et Sharpe
    returns = portfolio_value.pct_change().dropna()
    n_obs   = int(len(returns))

    # Ajuste le facteur d'annualisation à l'intervalle réellement utilisé
    # (après éventuel repli intraday → daily)
    if used_intervals and len(used_intervals) == 1:
        actual_ivl = next(iter(used_intervals))
        ann_factor_actual = {
            "5m":  252*78,
            "15m": 252*26,
            "30m": 252*13,
            "1h":  252*7,
            "1d":  252,
            "1wk": 52,
            "1mo": 12,
        }.get(actual_ivl, ann_factor)
    else:
        ann_factor_actual = ann_factor
        actual_ivl = interval

    if n_obs >= 2:
        mean_r = float(returns.mean())
        std_r  = float(returns.std(ddof=1))
        ann_return = mean_r * ann_factor_actual
        ann_vol    = std_r  * math.sqrt(ann_factor_actual)
        sharpe     = (ann_return / ann_vol) if ann_vol > 1e-9 else 0.0
        vol_ann_pct = ann_vol * 100
    else:
        sharpe, vol_ann_pct = 0.0, 0.0

    # Formatage des dates (basé sur l'intervalle réel)
    if actual_ivl in ("1m","2m","5m","15m","30m","60m","90m","1h"):
        dates = [d.strftime("%Y-%m-%dT%H:%M") for d in df.index]
    else:
        dates = [d.strftime("%Y-%m-%d") for d in df.index]

    return _san({
        "tickers_used":       valid_tickers,
        "n_tickers":          len(valid_tickers),
        "dates":              dates,
        "cumulative_pct":     cumulative_pct,
        "perf_total_pct":     perf_total_pct,
        "volatilite_ann_pct": vol_ann_pct,
        "sharpe":             sharpe,
        "n_observations":     n_obs,
        "period":             req.period,
        "interval":           actual_ivl,
    })

# ── Optimisation ─────────────────────────────────────────────────────────────

@app.post("/optimiser")
async def optimiser(profil: ProfilInvestisseur):
    try:
        A           = calculer_A_markowitz(profil.att1,profil.att2,profil.att3,profil.att4,
                          profil.tolerance,profil.invested,profil.had_loss,profil.choices)
        profil_info = interpreter_A(A)
        profil_esg  = get_profil_horizon(profil.horizon)

        collector   = CollectorAgent(chemin_excel="data/Etude_Entreprises_REEL_Data_final.xlsx")
        donnees     = await collector.collecter(pays=profil.pays,esg_seuil=profil.esg_seuil,
                                                horizon=profil.horizon)
        esg_agent   = ESGAgent()
        esg_data    = await esg_agent.fetch_portfolio_esg(donnees.get("tickers",[])[:30])

        optimizer    = OptimizerAgent()
        portfolio_ia = optimizer.optimisation_ia(donnees, A, esg_data)

        actualites  = await NewsAgent().analyser(
            [c["ticker"] for c in portfolio_ia.get("composition",[])[:6]]
        )
        predictor   = PredictorAgent()
        predictions = predictor.predict_multi_horizon(portfolio_ia, profil.horizon, profil_esg)
        stress      = predictor.stress_tests(portfolio_ia, profil.montant)
        dividends   = predictor.analyze_dividends(
            portfolio_ia.get("composition",[]), profil.horizon, profil.montant)

        # Enrichir composition
        composition = portfolio_ia.get("composition",[])
        SIGNAL_MAP  = {"VIGILANCE":"⚠️ Vigilance","RISQUE":"📉 Risque",
                       "OPPORTUNITÉ":"📈 Opportunité","POSITIF":"📈 Positif","NEUTRE":"➡️ Neutre"}
        news_map    = {a.get("ticker",""):a.get("type","NEUTRE")
                       for a in (actualites.get("alertes") or [])}
        for s in composition:
            tk = s.get("ticker","")
            s["montant_eur"] = round((s.get("poids_pct",0)/100)*profil.montant, 2)
            if tk in esg_data:
                e = esg_data[tk]
                s.update({"esg_sustainalytics":e.get("sustainalytics_score"),
                           "esg_score":e.get("our_score",s.get("esg_score",0)),
                           "esg_env":e.get("env_score"),"esg_soc":e.get("soc_score"),
                           "esg_gov":e.get("gov_score"),
                           "esg_tier":e.get("tier",{}).get("label","—"),
                           "esg_data_source":e.get("data_source","live"),
                           "esg_data_age_days":e.get("data_age_days",0),
                           "esg_freshness_warning":e.get("freshness_warning")})
            if tk in news_map:
                s["signal_news"] = SIGNAL_MAP.get(news_map[tk],"➡️ Neutre")

        meta = {
            "source_excel":"Données historiques (Excel EcoFolio)",
            "source_web":"Yahoo Finance + Morningstar Sustainalytics",
            "methode_beta":donnees.get("methode_beta","OLS regression"),
            "methode_covariance":donnees.get("methode_covariance","Ledoit-Wolf"),
            "indice_marche":donnees.get("indice_marche","^GSPC"),
            "n_observations":donnees.get("n_observations",252),
            "n_durables_web":len([c for c in composition if c.get("poids_pct",0)>0.1]),
            "n_titres_univers":len(donnees.get("tickers",[])),
            "pays":profil.pays or "Monde entier","esg_seuil":profil.esg_seuil,
            "pays_demande":   donnees.get("pays_demande", profil.pays),
            "pays_repli":     bool(donnees.get("pays_repli", False)),
            "motif_repli":    donnees.get("motif_repli", ""),
            "label_pays":     donnees.get("label_pays", profil.pays or "Monde entier"),
            "horizon":profil.horizon,"montant":profil.montant,
            "prime_esg_ann_pct":profil_esg.get("prime_rendement",0)*100,
            "confiance_esg":profil_esg.get("confiance_esg","faible"),
            "message_esg":profil_esg.get("message_investisseur",""),
            "n_esg_live":sum(1 for d in esg_data.values()
                             if d.get("data_source")=="sustainalytics_live"),
            "small_sample_warning":portfolio_ia.get("ia_info",{}).get("small_sample_warning",False),
            "drawdown_methode":"Monte Carlo GBM (1 500 trajectoires, percentile 5%)",
            "ic_methode":"Monte Carlo GBM (2 000 trajectoires)",
        }

        pf = {**portfolio_ia,"composition":composition}

        # Sauvegarde session
        session_id = db.save_session(
            portfolio=portfolio_ia,
            meta={"pays":profil.pays,"horizon":profil.horizon,
                  "montant":profil.montant,"esg_seuil":profil.esg_seuil},
            A=A, profil_label=profil_info.get("label","")
        )
        logger.info(f"Session {session_id} sauvegardée | Sharpe={portfolio_ia.get('sharpe',0):.3f}")

        return _san({"session_id":session_id,"A":A,"profil":profil_info,
                     "portefeuille":pf,"portefeuille_web":pf,
                     "esg_portefeuille":esg_data,"actualites":actualites,
                     "predictions":predictions,"dividendes":dividends,
                     "stress_tests":stress,"meta":meta,"meta_donnees":meta})

    except Exception as e:
        logger.error(f"Erreur: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

# ── Portefeuille personnalisé ───────────────────────────────────────────────

@app.post("/portefeuille/perso")
async def portefeuille_perso(req: PortefeuillePerso):
    """
    Même analyse que /optimiser (métriques, ESG, news, projections, stress tests,
    dividendes, session pour le suivi) mais sur un portefeuille dont l'investisseur
    a choisi lui-même les titres et les poids. Aucune optimisation n'est faite.
    La réponse a le même format que /optimiser pour réutiliser la page Résultats.
    """
    try:
        # Regroupe les doublons (ex. AAPL saisi deux fois) et normalise les tickers
        poids_par_ticker: dict = {}
        for l in req.lignes:
            tk = l.ticker.strip().upper()
            poids_par_ticker[tk] = poids_par_ticker.get(tk, 0.0) + l.poids
        total_saisi = sum(poids_par_ticker.values())

        collector = CollectorAgent(chemin_excel="data/Etude_Entreprises_REEL_Data_final.xlsx")
        try:
            donnees = await collector.collecter_liste(list(poids_par_ticker), idx_ticker=req.indice)
        except ValueError as e:
            raise HTTPException(status_code=422, detail={"erreur": str(e)})

        tickers_ok = donnees["tickers"]
        rejetes    = donnees.get("tickers_rejetes", [])
        poids      = [poids_par_ticker[t] for t in tickers_ok]

        avertissements = []
        if abs(total_saisi - 100) > 0.5:
            avertissements.append(
                f"Les poids saisis totalisent {total_saisi:.1f} % : ils ont été ramenés à 100 %."
            )
        if rejetes:
            avertissements.append(
                f"Titres ignorés faute de données Yahoo Finance : {', '.join(rejetes)}. "
                f"Leurs poids ont été répartis proportionnellement sur les autres lignes."
            )

        esg_data  = await ESGAgent().fetch_portfolio_esg(tickers_ok[:30])
        portfolio = OptimizerAgent().evaluer_portefeuille(donnees, poids, esg_data)
        profil_esg = get_profil_horizon(req.horizon)

        composition = portfolio.get("composition", [])
        actualites  = await NewsAgent().analyser([c["ticker"] for c in composition[:6]])
        predictor   = PredictorAgent()
        predictions = predictor.predict_multi_horizon(portfolio, req.horizon, profil_esg)
        stress      = predictor.stress_tests(portfolio, req.montant)
        dividends   = predictor.analyze_dividends(composition, req.horizon, req.montant)

        for s in composition:
            s["montant_eur"] = round((s.get("poids_pct", 0) / 100) * req.montant, 2)

        meta = {
            "source_web": "Yahoo Finance + Morningstar Sustainalytics",
            "methode_beta": donnees.get("methode_beta"),
            "methode_covariance": donnees.get("methode_covariance"),
            "indice_marche": donnees.get("indice_marche"),
            "n_observations": donnees.get("n_observations"),
            "n_durables_web": len(composition),
            "n_titres_univers": len(tickers_ok),
            "pays": "Portefeuille personnalisé", "label_pays": "Portefeuille personnalisé",
            "pays_repli": False, "motif_repli": "",
            "esg_seuil": None, "horizon": req.horizon, "montant": req.montant,
            "prime_esg_ann_pct": profil_esg.get("prime_rendement", 0) * 100,
            "confiance_esg": profil_esg.get("confiance_esg", "faible"),
            "message_esg": profil_esg.get("message_investisseur", ""),
            "n_esg_live": sum(1 for d in esg_data.values()
                              if d.get("data_source") == "sustainalytics_live"),
            "portefeuille_perso": True,
            "tickers_rejetes": rejetes,
            "avertissements": avertissements,
        }

        # Sauvegarde : le portefeuille perso peut ensuite être suivi dans « Mon Suivi »
        session_id = db.save_session(
            portfolio=portfolio,
            meta={"pays": None, "horizon": req.horizon,
                  "montant": req.montant, "esg_seuil": None},
            A=0.0, profil_label="Portefeuille personnalisé",
        )
        logger.info(f"Session perso {session_id} | {len(tickers_ok)} titres | "
                    f"Sharpe={portfolio.get('sharpe', 0):.3f}")

        pf = {**portfolio, "composition": composition}
        return _san({"session_id": session_id, "A": None,
                     "profil": {"label": "Portefeuille personnalisé"},
                     "portefeuille": pf, "portefeuille_web": pf,
                     "esg_portefeuille": esg_data, "actualites": actualites,
                     "predictions": predictions, "dividendes": dividends,
                     "stress_tests": stress, "meta": meta, "meta_donnees": meta})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur portefeuille perso: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

# ── Feedback ─────────────────────────────────────────────────────────────────

@app.post("/feedback")
async def soumettre_feedback(req: FeedbackRequest):
    session = db.get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404,
            detail="Session introuvable. Lancez d'abord une optimisation.")

    # Calcul du rendement depuis les composantes (jamais saisie directe)
    valeur_nette        = req.montant_final + req.dividendes - req.frais
    rendement_reel_pct  = (valeur_nette / req.montant_initial - 1) * 100
    annees              = max(req.duree_mois / 12, 0.08)
    rendement_ann_pct   = ((1 + rendement_reel_pct/100)**(1/annees) - 1) * 100

    # 4 couches de validation
    validation = await valider_feedback_complet(
        session=session, montant_initial=req.montant_initial,
        montant_final=req.montant_final, dividendes=req.dividendes,
        frais=req.frais, duree_mois=req.duree_mois,
        rendement_reel=rendement_reel_pct,
    )

    benchmark    = validation.get("benchmark",{})
    ecart_predit = rendement_reel_pct - (session.get("rendement_predit",0) or 0) * annees

    diagnostic = _generer_diagnostic(
        rendement_reel_pct   = round(rendement_reel_pct,2),
        rendement_ann_pct    = round(rendement_ann_pct,2),
        rendement_predit_ann = session.get("rendement_predit",0),
        ecart_predit         = round(ecart_predit,2),
        perf_theorique       = benchmark.get("perf_theorique_pct"),
        perf_marche          = benchmark.get("perf_marche_pct"),
        duree_mois           = req.duree_mois,
        esg_moyen            = session.get("esg_moyen",65),
        flags                = validation["flags"],
    )

    feedback_id = None
    if validation["validation_ok"]:
        feedback_id = db.save_feedback(
            session_id=req.session_id, montant_initial=req.montant_initial,
            montant_final=req.montant_final, dividendes=req.dividendes,
            frais=req.frais, duree_mois=req.duree_mois,
            rendement_reel=rendement_reel_pct, rendement_ann=rendement_ann_pct,
            source="formulaire", pdf_fourni=False,
            validation_ok=validation["validation_ok"],
            flags=validation["flags"], diagnostic=diagnostic, benchmark=benchmark,
        )

    return _san({
        "feedback_id":             feedback_id,
        "session_id":              req.session_id,
        "validation":              validation,
        "rendement_reel_pct":      round(rendement_reel_pct,2),
        "rendement_annualise_pct": round(rendement_ann_pct,2),
        "ecart_vs_predit_pct":     round(ecart_predit,2),
        "benchmark":               benchmark,
        "diagnostic":              diagnostic,
        "session_resume":{
            "rendement_predit_ann_pct": session.get("rendement_predit"),
            "volatilite_pct":           session.get("volatilite"),
            "sharpe":                   session.get("sharpe"),
            "esg_moyen":               session.get("esg_moyen"),
            "created_at":               session.get("created_at"),
            "horizon":                  session.get("horizon"),
        },
    })

# ── Suivi : rendement RÉEL reçu vs prédiction (page Mon Suivi) ────────────────

from datetime import datetime, timedelta

# Taux de repli : nombre d'EUR pour 1 unité de la devise (valeurs approximatives,
# utilisées seulement si Yahoo Finance n'est pas joignable).
_FX_FALLBACK_EUR = {
    "EUR": 1.0,    "USD": 0.92,   "GBP": 1.17,   "CHF": 1.04,
    "JPY": 0.0061, "CAD": 0.67,   "AUD": 0.60,   "SEK": 0.088,
    "TND": 0.29,   "MAD": 0.092,  "DZD": 0.0068,
}

async def _eur_par_unite(devise: str) -> dict:
    """Taux de conversion vers l'EUR pour 1 unité de `devise`. Yahoo Finance, repli statique."""
    devise = (devise or "EUR").upper()
    if devise == "EUR":
        return {"taux": 1.0, "source": "EUR (devise de référence)"}
    try:
        import yfinance as yf
        def _r():
            h = yf.Ticker(f"EUR{devise}=X").history(period="5d", auto_adjust=True)
            if h is None or h.empty:
                return None
            s = h["Close"].dropna()
            return float(s.iloc[-1]) if len(s) else None
        xxx_par_eur = await asyncio.to_thread(_r)
        if xxx_par_eur and xxx_par_eur > 0:
            return {"taux": 1.0 / xxx_par_eur, "source": "Yahoo Finance (temps réel)"}
    except Exception as e:
        logger.debug(f"FX {devise} indisponible: {e}")
    return {"taux": _FX_FALLBACK_EUR.get(devise, 1.0),
            "source": "taux indicatif (Yahoo Finance indisponible)"}


def _fetch_series_periode(tickers, date_debut, date_fin):
    """
    Récupère les cours de clôture RÉELS (Yahoo Finance) entre deux dates, par ticker.
    auto_adjust=False → prix « as-traded » (hors réinvestissement de dividendes) :
    les dividendes éventuels sont ajoutés séparément côté endpoint.
    Retourne un dict {ticker: pandas.Series}.
    """
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    start_s = date_debut.strftime("%Y-%m-%d")
    end_s   = (date_fin + timedelta(days=2)).strftime("%Y-%m-%d")   # end exclusif côté yfinance

    def _one(tk):
        try:
            h = yf.Ticker(tk).history(start=start_s, end=end_s, auto_adjust=False)
            if h is None or h.empty:
                return tk, None
            col = "Close" if "Close" in h.columns else ("Adj Close" if "Adj Close" in h.columns else None)
            if col is None:
                return tk, None
            s = h[col].dropna()
            return tk, (s if len(s) >= 2 else None)
        except Exception as e:
            logger.debug(f"suivi fetch {tk}: {e}")
            return tk, None

    series = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for tk, s in ex.map(_one, tickers):
            if s is not None:
                series[tk] = s
    return series


def _rendement_reel_pondere(series, tickers, poids):
    """
    Rendement pondéré RÉEL (prix) du portefeuille sur la fenêtre couverte par `series`.
    Aligne les séries (ffill pour gérer les jours fériés décalés entre bourses) puis
    calcule, par titre, (prix_fin / prix_début − 1), et pondère par les poids re-normalisés.
    """
    import pandas as pd
    import numpy as np

    if not series:
        return None

    df = pd.DataFrame(series).sort_index()
    no_na = df.notna().all(axis=1)
    if no_na.any():
        first = no_na.idxmax()
        df = df.loc[first:].ffill().bfill().dropna(axis=1, how="any")
    else:
        df = df.dropna(axis=1, thresh=2).ffill().bfill().dropna()

    if df.empty or len(df) < 2 or df.shape[1] == 0:
        return None

    valid = list(df.columns)
    w = np.array([poids[tickers.index(t)] for t in valid], dtype=float)
    tot = float(w.sum())
    if tot <= 0:
        return None
    w = w / tot

    rdt_titres = (df.iloc[-1] / df.iloc[0] - 1.0)            # rendement prix par titre
    rdt = float(np.dot(rdt_titres.values, w)) * 100

    return {
        "rendement_pct":     rdt,
        "tickers_utilises":  valid,
        "n_obs":             int(len(df)),
        "date_debut":        df.index[0].strftime("%Y-%m-%d"),
        "date_fin":          df.index[-1].strftime("%Y-%m-%d"),
        "jours_effectifs":   int((df.index[-1] - df.index[0]).days),
        "perf_par_titre":    {t: round(float(rdt_titres[t]) * 100, 2) for t in valid},
    }


def _diagnostic_suivi(rdt_reel_pct, rdt_predit_pct, frais_pct, ecart_pct, complet, biais):
    """Comparaison interne réel vs prédit + causes probables + message d'auto-correction."""
    tol = max(1.0, abs(rdt_predit_pct) * 0.30)

    if not complet:
        qualite = "partiel"
        msg = ("Horizon pas encore entièrement écoulé : rendement réel calculé sur la "
               "période de marché déjà disponible.")
    elif ecart_pct > tol:
        qualite = "surperformance"
        msg = f"Votre portefeuille a fait MIEUX que prévu (+{ecart_pct:.2f} pts vs prévision)."
    elif ecart_pct < -tol:
        qualite = "sous-performance"
        msg = f"Votre portefeuille a fait MOINS BIEN que prévu ({ecart_pct:.2f} pts vs prévision)."
    else:
        qualite = "conforme"
        msg = f"Rendement réel conforme à la prévision (écart {ecart_pct:+.2f} pts)."

    causes = []
    if frais_pct >= 0.5 and ecart_pct < 0:
        causes.append(f"Les frais de courtage pèsent {frais_pct:.2f} pts : ils expliquent une "
                      f"partie de l'écart négatif.")
    if qualite == "sous-performance" and frais_pct < 0.5:
        causes.append("L'écart vient surtout du marché ou d'une prédiction trop optimiste, "
                      "pas des frais.")
    if qualite == "surperformance":
        causes.append("Conditions de marché plus favorables que le scénario central prévu.")
    if qualite == "conforme" and not causes:
        causes.append("Aucune anomalie : exécution fidèle à la recommandation.")

    if biais["n_observations"] >= 1:
        b = biais["biais_annuel_pct"]
        if b > 0.05:
            sens, tendance = "à la hausse", "sous-estime"
        elif b < -0.05:
            sens, tendance = "à la baisse", "sur-estime"
        else:
            sens, tendance = "sans changement notable", "est bien calibré"
        autocorrection = (f"Sur {biais['n_observations']} suivi(s), le modèle {tendance} le rendement "
                          f"de {abs(b):.2f} %/an en moyenne. Les futures projections sont corrigées "
                          f"{sens}.")
    else:
        autocorrection = ("Premier suivi enregistré : le modèle commence à se calibrer sur vos "
                          "résultats réels.")

    return {"qualite": qualite, "message": msg, "causes": causes, "autocorrection": autocorrection}


@app.post("/suivi")
async def suivi(req: SuiviRequest):
    """
    Calcule le rendement RÉELLEMENT reçu par l'investisseur depuis l'optimisation,
    à partir des cours de marché réels (Yahoo Finance) des actions du portefeuille,
    puis le compare en interne à la prédiction et met à jour la calibration du modèle.
    """
    session = db.get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session introuvable. Vérifiez votre identifiant.")

    compo   = session.get("composition") or []
    tickers = [c.get("ticker") for c in compo if c.get("ticker")]
    poids   = [float(c.get("poids_pct", 0)) for c in compo if c.get("ticker")]
    if not tickers:
        raise HTTPException(status_code=422,
            detail="La session ne contient pas de composition de portefeuille exploitable.")

    montant   = float(session.get("montant")         or 0) or 10000.0
    mu_ann    = float(session.get("rendement_predit") or 8.0)
    div_yield = float(session.get("div_yield")        or 0.018)

    # Date d'investissement = date de création de la session
    try:
        date_debut = datetime.fromisoformat(session.get("created_at")).replace(tzinfo=None)
    except Exception:
        raise HTTPException(status_code=422, detail="Date de session illisible.")

    horizon_fin = date_debut + timedelta(days=req.horizon_jours)
    maintenant  = datetime.now()
    date_fin    = min(horizon_fin, maintenant)
    complet     = horizon_fin <= maintenant
    if (date_fin - date_debut).days < 1:
        raise HTTPException(status_code=422,
            detail="Horizon trop court ou session trop récente : aucune donnée de marché n'a encore "
                   "couru sur cette période.")

    long_terme = req.horizon_jours > 365

    # ── Récupération des VRAIS prix de marché sur la période écoulée ──────────
    series = await asyncio.to_thread(_fetch_series_periode, tickers, date_debut, date_fin)
    reel   = _rendement_reel_pondere(series, tickers, poids)
    if reel is None:
        raise HTTPException(status_code=502,
            detail="Données Yahoo Finance indisponibles pour calculer le rendement réel sur la période.")

    rdt_marche_pct = reel["rendement_pct"]
    jours_eff      = max(reel["jours_effectifs"], 1)
    annees_eff     = jours_eff / 365.25

    # ── Frais et dividendes (devises → EUR) ──────────────────────────────────
    fx_frais  = await _eur_par_unite(req.frais_devise)
    frais_eur = round(req.frais * fx_frais["taux"], 2)

    if long_terme:
        if req.dividendes and req.dividendes > 0:
            fx_div     = await _eur_par_unite(req.dividendes_devise)
            div_eur    = round(req.dividendes * fx_div["taux"], 2)
            div_source = "saisis par l'investisseur"
            fx_div_src = fx_div["source"]; div_devise = req.dividendes_devise.upper()
        else:
            div_eur    = round(montant * div_yield * annees_eff, 2)
            div_source = "estimation EcoFolio (versements historiques)"
            fx_div_src = "EUR (devise de référence)"; div_devise = "EUR"
    else:
        div_eur, div_source = 0.0, "non applicable (horizon ≤ 1 an)"
        fx_div_src, div_devise = "EUR (devise de référence)", "EUR"

    # ── Valeur réelle et rendement réel net ──────────────────────────────────
    valeur_marche = montant * (1 + rdt_marche_pct / 100)
    valeur_nette  = valeur_marche + div_eur - frais_eur
    rdt_reel_pct  = round((valeur_nette / montant - 1) * 100, 2) if montant > 0 else 0.0
    rdt_reel_ann  = round(((1 + rdt_reel_pct / 100) ** (1 / max(annees_eff, 1/365)) - 1) * 100, 2)

    # ── Comparaison avec la prédiction (sur l'horizon réellement écoulé) ──────
    rdt_predit_horizon = round(((1 + mu_ann / 100) ** annees_eff - 1) * 100, 2)
    ecart_pct          = round(rdt_reel_pct - rdt_predit_horizon, 2)
    frais_pct          = round(frais_eur / montant * 100, 2) if montant > 0 else 0.0

    # ── Enregistrement (auto-correction) + biais de calibration ──────────────
    try:
        db.save_feedback(
            session_id=req.session_id, montant_initial=montant,
            montant_final=round(valeur_marche, 2), dividendes=div_eur, frais=frais_eur,
            duree_mois=max(1, round(jours_eff / 30.4)),
            rendement_reel=rdt_reel_pct, rendement_ann=rdt_reel_ann,
            source="suivi_marche", pdf_fourni=False, validation_ok=True,
            flags=[], diagnostic={}, benchmark={"perf_marche_reelle_pct": round(rdt_marche_pct, 2)},
        )
    except Exception as e:
        logger.warning(f"save_feedback suivi: {e}")

    biais = db.calibration_bias()
    diag  = _diagnostic_suivi(rdt_reel_pct, rdt_predit_horizon, frais_pct, ecart_pct, complet, biais)
    prediction_recalibree = round(mu_ann + biais["biais_annuel_pct"], 2)

    return _san({
        "session_id":            req.session_id,
        "montant_investi_eur":   round(montant, 2),
        "horizon_jours_demande": req.horizon_jours,
        "horizon_complet":       complet,
        "periode_reelle": {"debut": reel["date_debut"], "fin": reel["date_fin"],
                           "jours_ecoules": jours_eff},

        # ── Résultat principal : le rendement RÉELLEMENT reçu ──
        "rendement_reel_pct":           rdt_reel_pct,
        "rendement_reel_annualise_pct": rdt_reel_ann,
        "valeur_reelle_eur":            round(valeur_nette, 2),

        # ── Comparaison interne avec la prédiction ──
        "comparaison": {
            "rendement_predit_horizon_pct": rdt_predit_horizon,
            "rendement_predit_annuel_pct":  round(mu_ann, 2),
            "ecart_pct":                    ecart_pct,
            "verdict":                      diag["qualite"],
        },
        "diagnostic": diag,

        "detail": {
            "performance_marche_reelle_pct": round(rdt_marche_pct, 2),
            "valeur_marche_eur":             round(valeur_marche, 2),
            "dividendes_eur":                div_eur,
            "frais_courtage_eur":            frais_eur,
            "valeur_reelle_nette_eur":       round(valeur_nette, 2),
        },
        "frais": {"montant_saisi": req.frais, "devise": req.frais_devise.upper(),
                  "montant_eur": frais_eur, "taux_change_source": fx_frais["source"]},
        "dividendes": {"montant_eur": div_eur, "source": div_source, "devise": div_devise,
                       "pris_en_compte": long_terme, "taux_change_source": fx_div_src},
        "marche": {
            "tickers_utilises":   reel["tickers_utilises"],
            "n_titres":           len(reel["tickers_utilises"]),
            "perf_par_titre_pct": reel["perf_par_titre"],
            "source":             "Yahoo Finance (cours réels sur la période)",
        },
        "calibration": {
            "biais_annuel_pct":               biais["biais_annuel_pct"],
            "n_observations":                 biais["n_observations"],
            "prediction_recalibree_ann_pct":  prediction_recalibree,
        },
        "note": ("Le rendement est calculé à partir des cours réels Yahoo Finance des actions du "
                 "portefeuille sur la période écoulée depuis l'optimisation, après frais et dividendes "
                 "éventuels. Les performances passées ne préjugent pas des performances futures."),
    })


@app.post("/feedback/pdf")
async def extraire_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés.")
    pdf_bytes = await file.read()
    if len(pdf_bytes) > 10*1024*1024:
        raise HTTPException(status_code=400, detail="Fichier trop volumineux (max 10 MB).")
    return _san(extraire_donnees_pdf(pdf_bytes))

@app.get("/session/{session_id}")
def get_session(session_id: str):
    s = db.get_session(session_id)
    if not s: raise HTTPException(status_code=404, detail="Session introuvable.")
    return _san(s)

@app.get("/sessions")
def list_sessions(limit: int = 20):
    return _san({"sessions": db.list_sessions(limit=limit)})

@app.get("/feedback/{session_id}")
def get_feedbacks(session_id: str):
    fb = db.get_feedbacks_for_session(session_id)
    return _san({"feedbacks": fb, "n": len(fb)})

@app.get("/esg/{ticker}")
async def esg_score(ticker: str):
    r = await ESGAgent().fetch_esg_score(ticker.upper())
    if not r: raise HTTPException(status_code=404, detail=f"ESG indisponible pour {ticker}")
    return _san(r)

# ─────────────────────── Diagnostic ─────────────────────────────────────────

def _generer_diagnostic(rendement_reel_pct, rendement_ann_pct, rendement_predit_ann,
                         ecart_predit, perf_theorique, perf_marche,
                         duree_mois, esg_moyen, flags) -> dict:
    annees = max(duree_mois/12, 0.08)

    if ecart_predit > 2:
        qualite, msg = "surperformance", \
            f"Votre portefeuille a surperformé la prévision de {ecart_predit:.1f}% sur la période."
    elif ecart_predit > -2:
        qualite, msg = "conforme", \
            f"Performance conforme aux prévisions (écart {ecart_predit:+.1f}%)."
    else:
        qualite, msg = "sous-performance", \
            (f"Le portefeuille a sous-performé la prévision de {abs(ecart_predit):.1f}%. "
             f"Marché défavorable, frais ou timing d'exécution peuvent l'expliquer.")

    alpha_vs_marche = None
    alpha_msg = None
    if perf_marche is not None:
        alpha_vs_marche = round(rendement_reel_pct - perf_marche * annees, 2)
        if alpha_vs_marche > 1:
            alpha_msg = f"Alpha positif de {alpha_vs_marche:.1f}% vs marché — la sélection ESG a créé de la valeur."
        elif alpha_vs_marche > -1:
            alpha_msg = f"Performance alignée avec le marché (alpha ≈ {alpha_vs_marche:.1f}%)."
        else:
            alpha_msg = (f"Sous-performance de {abs(alpha_vs_marche):.1f}% vs marché. "
                         f"Normal sur horizon court — l'ESG surperforme à long terme.")

    ecart_vs_theo = None
    theo_msg = None
    if perf_theorique is not None:
        ecart_vs_theo = round(rendement_reel_pct - perf_theorique * annees, 2)
        if abs(ecart_vs_theo) < 2:
            theo_msg = f"Exécution très proche du portefeuille théorique (écart {ecart_vs_theo:+.1f}%)."
        elif ecart_vs_theo < 0:
            theo_msg = (f"Écart de {ecart_vs_theo:.1f}% vs théorique — frais, timing "
                        f"ou composition modifiée probablement en cause.")
        else:
            theo_msg = f"Vous avez dépassé le théorique de {ecart_vs_theo:.1f}% — timing favorable ou dividendes supplémentaires."

    recs = []
    if abs(ecart_predit) > 5: recs.append("Rééquilibrage avec nouvelles données de marché recommandé.")
    if duree_mois >= 12: recs.append(f"Après {duree_mois} mois, relancez l'optimiseur pour adapter l'allocation.")
    if esg_moyen < 62 and qualite == "sous-performance":
        recs.append("Envisagez d'augmenter le seuil ESG pour améliorer la résilience.")
    if not recs: recs.append("Maintenez le portefeuille actuel — résultats conformes aux objectifs.")

    return {
        "qualite_performance":    qualite,
        "message_principal":      msg,
        "alpha_vs_marche_pct":    alpha_vs_marche,
        "alpha_message":          alpha_msg,
        "ecart_vs_theorique_pct": ecart_vs_theo,
        "theorique_message":      theo_msg,
        "recommandations":        recs,
        "points_attention":       [f for f in flags if f.get("severity")=="warning"],
        "resume_chiffres":{
            "rendement_reel_pct":       rendement_reel_pct,
            "rendement_annualise_pct":  rendement_ann_pct,
            "rendement_predit_ann_pct": rendement_predit_ann,
            "ecart_pct":                ecart_predit,
            "perf_theorique_pct":       perf_theorique,
            "perf_marche_pct":          perf_marche,
        },
    }
