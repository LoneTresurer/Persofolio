"""
agents/optimizer_agent.py
==========================
Optimiseur de Portefeuille ESG Durable — IA + Markowitz

CORRECTIONS v6 :
  - Drawdown calculé par simulation Monte Carlo (1 000 trajectoires GBM)
    au lieu de l'heuristique -vol * 2.33 * 0.5
  - Alpha IA plus conservateur pour petits univers (< 50 titres) :
    alpha = min(r2 * (n/50), 0.35) afin de ne pas sur-pondérer un R²
    peu fiable sur 30 observations
  - Avertissement small_sample_warning si n < 30
  - Toutes les métriques publiées dans ia_info.gradient_boosting et
    ia_info.random_forest sont cohérentes avec ce que le frontend attend

Méthode :
  GB et RF prédisent μ_futur → blend adaptatif → Markowitz U(w) = w'μ − (A/2)w'Σw
"""

import logging
import warnings
from typing import Dict, List, Optional

import numpy as np
from scipy.optimize import minimize
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
logger = logging.getLogger("optimizer")

RF_RATE = 0.02          # Taux sans risque OAT 10 ans
N_MC    = 1_500         # Trajectoires Monte Carlo pour le drawdown


class OptimizerAgent:
    """Optimiseur de portefeuille ESG — IA (GB/RF) + Markowitz."""

    def __init__(self):
        self._scaler   = StandardScaler()
        self._gb_model = None
        self._rf_model = None

    # ── API publique ──────────────────────────────────────────────────────────

    def optimisation_ia(
        self, donnees: dict, A: float, esg_data: dict = None
    ) -> dict:
        """
        Optimisation principale : IA prédit mu → Markowitz construit le portefeuille.
        GB et RF sont tous les deux évalués ; le meilleur Sharpe est retenu.
        """
        tickers    = donnees["tickers"]
        mu_hist    = np.array(donnees["rendements"])
        cov        = np.array(donnees["covariance"])
        vols       = np.array(donnees.get("vols",       [0.20] * len(tickers)))
        betas      = np.array(donnees.get("betas",      [1.00] * len(tickers)))
        esg_scores = np.array(donnees.get("esg_scores", [65.0] * len(tickers)))
        divs       = np.array(donnees.get("div_yields", [0.015] * len(tickers)))
        max_w      = self._max_poids(A)
        n          = len(tickers)

        # Avertissement petit univers
        small_sample = n < 30
        if small_sample:
            logger.warning(f"Petit univers ({n} titres) : alpha IA réduit automatiquement")

        X = np.column_stack([mu_hist, esg_scores, vols, betas, divs])

        mu_gb, r2_gb = self._predict_gb(X, mu_hist)
        mu_rf, r2_rf = self._predict_rf(X, mu_hist)

        # Alpha adaptatif : plafonne plus tôt si univers petit
        scale = min(n / 50.0, 1.0)
        alpha_gb  = min(max(r2_gb, 0.0) * scale, 0.35)
        alpha_rf  = min(max(r2_rf, 0.0) * scale, 0.35)

        mu_adj_gb = (1 - alpha_gb) * mu_hist + alpha_gb * mu_gb
        mu_adj_rf = (1 - alpha_rf) * mu_hist + alpha_rf * mu_rf

        w_gb = self._optimiser(mu_adj_gb, cov, A, max_w)
        w_rf = self._optimiser(mu_adj_rf, cov, A, max_w)

        m_gb = self._metriques(w_gb, mu_adj_gb, cov, esg_scores, donnees)
        m_rf = self._metriques(w_rf, mu_adj_rf, cov, esg_scores, donnees)

        use_gb = m_gb["sharpe"] >= m_rf["sharpe"]
        best_w = w_gb if use_gb else w_rf
        best_m = m_gb if use_gb else m_rf
        compo  = self._composer(best_w, donnees, esg_data)
        winner = "Gradient Boosting" if use_gb else "Random Forest"

        logger.info(
            f"Modèle IA : {winner} "
            f"(Sharpe GB={m_gb['sharpe']:.3f} | RF={m_rf['sharpe']:.3f})"
        )

        return {
            "methode":     f"IA + Markowitz ({winner})",
            "composition": compo,
            "ia_info": {
                "modele_selectionne": winner,
                "n_titres_univers":   n,
                "small_sample_warning": small_sample,
                "gradient_boosting": {
                    "r2_cv":        round(r2_gb, 3),
                    "alpha_blend":  round(alpha_gb, 3),
                    "n_titres":     int(np.sum(w_gb > 0.001)),
                    "rendement":    m_gb["rendement"],
                    "volatilite":   m_gb["volatilite"],
                    "sharpe":       m_gb["sharpe"],
                    "drawdown":     m_gb["drawdown"],
                    "esg_moyen":    m_gb["esg_moyen"],
                },
                "random_forest": {
                    "r2_cv":        round(r2_rf, 3),
                    "alpha_blend":  round(alpha_rf, 3),
                    "n_titres":     int(np.sum(w_rf > 0.001)),
                    "rendement":    m_rf["rendement"],
                    "volatilite":   m_rf["volatilite"],
                    "sharpe":       m_rf["sharpe"],
                    "drawdown":     m_rf["drawdown"],
                    "esg_moyen":    m_rf["esg_moyen"],
                },
            },
            **best_m,
        }

    def evaluer_portefeuille(
        self, donnees: dict, poids: List[float], esg_data: dict = None
    ) -> dict:
        """
        Portefeuille PERSONNALISÉ : les poids sont imposés par l'utilisateur,
        aucune optimisation. On calcule seulement les mêmes métriques que pour
        le portefeuille optimisé (rendement, vol, Sharpe, drawdown, ESG, bêta…)
        afin que le frontend puisse afficher les deux de la même façon.
        """
        w = np.array(poids, dtype=float)
        if w.sum() <= 0:
            raise ValueError("La somme des poids doit être positive.")
        w = w / w.sum()                               # normalisation à 100 %
        mu    = np.array(donnees["rendements"])
        cov   = np.array(donnees["covariance"])
        esg   = np.array(donnees.get("esg_scores", [65.0] * len(w)))

        metriques = self._metriques(w, mu, cov, esg, donnees)
        return {
            "methode":     "Portefeuille personnalisé (poids choisis par l'investisseur)",
            "composition": self._composer(w, donnees, esg_data),
            "ia_info": {
                "modele_selectionne": "Aucun (poids imposés)",
                "n_titres_univers":   len(w),
                "small_sample_warning": False,
            },
            **metriques,
        }

    # ── Optimisation Markowitz ────────────────────────────────────────────────

    def _optimiser(self, mu: np.ndarray, cov: np.ndarray, A: float, max_w: float) -> np.ndarray:
        """
        Maximise U(w) = w'mu − (A/2) w'Σ w
        Gradient analytique : ∇U = mu − A·Σ·w
        """
        n  = len(mu)
        w0 = np.full(n, 1.0 / n)

        def neg_u(w): return -(w @ mu - (A / 2) * (w @ cov @ w))
        def grad(w):  return -(mu - A * cov @ w)

        res = minimize(
            neg_u, w0, jac=grad, method="SLSQP",
            bounds=[(0.0, max_w)] * n,
            constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
            options={"maxiter": 500, "ftol": 1e-10},
        )
        w = np.clip(res.x, 0, max_w)
        s = w.sum()
        return w / s if s > 1e-10 else w0

    # ── Modèles IA ────────────────────────────────────────────────────────────

    def _predict_gb(self, X: np.ndarray, y: np.ndarray):
        try:
            X_s = self._scaler.fit_transform(X)
            cv  = min(3, max(2, len(y) // 8))
            gb  = GradientBoostingRegressor(
                n_estimators=100, max_depth=3, learning_rate=0.1,
                min_samples_split=3, random_state=42)
            r2  = max(float(
                cross_val_score(gb, X_s, y, cv=cv, scoring="r2").mean()
            ), 0.0)
            gb.fit(X_s, y)
            self._gb_model = gb
            return gb.predict(X_s), r2
        except Exception as e:
            logger.warning(f"GB failed: {e}")
            return y.copy(), 0.0

    def _predict_rf(self, X: np.ndarray, y: np.ndarray):
        try:
            X_s = self._scaler.fit_transform(X)
            cv  = min(3, max(2, len(y) // 8))
            rf  = RandomForestRegressor(
                n_estimators=100, max_depth=4,
                min_samples_split=3, random_state=42)
            r2  = max(float(
                cross_val_score(rf, X_s, y, cv=cv, scoring="r2").mean()
            ), 0.0)
            rf.fit(X_s, y)
            self._rf_model = rf
            return rf.predict(X_s), r2
        except Exception as e:
            logger.warning(f"RF failed: {e}")
            return y.copy(), 0.0

    # ── Métriques ─────────────────────────────────────────────────────────────

    def _metriques(
        self,
        weights:    np.ndarray,
        mu:         np.ndarray,
        cov:        np.ndarray,
        esg_scores: np.ndarray,
        donnees:    dict,
    ) -> dict:
        mu_p   = float(weights @ mu)
        var_p  = float(weights @ cov @ weights)
        vol_p  = float(np.sqrt(max(var_p, 0)))
        sharpe = (mu_p - RF_RATE) / vol_p if vol_p > 1e-6 else 0.0

        # Drawdown Monte Carlo (max drawdown espéré sur 1 an, percentile 5%)
        drawdown = self._mc_max_drawdown(mu_p, vol_p)

        return {
            "rendement":          round(mu_p  * 100, 3),
            "volatilite":         round(vol_p * 100, 3),
            "sharpe":             round(sharpe, 4),
            "drawdown":           round(drawdown, 3),          # % négatif
            "esg_moyen":          round(float(weights @ esg_scores), 2),
            "beta_portefeuille":  round(float(weights @ np.array(
                donnees.get("betas", [1.0] * len(weights))
            )), 3),
            "div_yield":          round(float(weights @ np.array(
                donnees.get("div_yields", [0.015] * len(weights))
            )), 4),
            "n_titres_actifs":    int(np.sum(weights > 0.001)),
        }

    @staticmethod
    def _mc_max_drawdown(
        mu_ann: float, vol_ann: float, horizon_years: float = 1.0
    ) -> float:
        """
        Estime le maximum drawdown par simulation Monte Carlo (GBM).
        Retourne le percentile 5% du max-drawdown sur N_MC trajectoires.
        Valeur négative en pourcentage.
        """
        n_days = max(int(horizon_years * 252), 21)
        mu_d   = mu_ann / 252
        vol_d  = vol_ann / np.sqrt(252) if vol_ann > 0 else 0.01

        rng = np.random.default_rng(seed=42)
        log_r = rng.normal(
            loc=mu_d - 0.5 * vol_d**2,
            scale=vol_d,
            size=(N_MC, n_days)
        )
        prices        = np.exp(np.cumsum(log_r, axis=1))
        prices        = np.hstack([np.ones((N_MC, 1)), prices])
        running_max   = np.maximum.accumulate(prices, axis=1)
        drawdowns     = (prices - running_max) / running_max
        max_drawdowns = drawdowns.min(axis=1)               # toutes négatives

        # percentile 5% : cas défavorable mais pas catastrophique
        mdd = float(np.percentile(max_drawdowns, 5)) * 100
        return round(mdd, 3)

    # ── Composition ──────────────────────────────────────────────────────────

    def _composer(
        self, weights: np.ndarray, donnees: dict, esg_data: Optional[dict]
    ) -> list:
        tickers    = donnees["tickers"]
        noms       = donnees.get("noms",        tickers)
        secteurs   = donnees.get("secteurs",    ["Autre"] * len(tickers))
        esg_scores = donnees.get("esg_scores",  [65.0]  * len(tickers))
        div_yields = donnees.get("div_yields",  [0.015] * len(tickers))
        prix       = donnees.get("prix_actuels",[None]  * len(tickers))

        composition = []
        for i, (tk, w) in enumerate(zip(tickers, weights)):
            if w < 0.001:
                continue
            stock = {
                "ticker":     tk,
                "nom":        noms[i]     if i < len(noms)      else tk,
                "secteur":    secteurs[i] if i < len(secteurs)  else "Autre",
                "poids_pct":  round(w * 100, 3),
                "esg_score":  round(esg_scores[i] if i < len(esg_scores) else 65.0, 1),
                "div_yield":  round(div_yields[i] if i < len(div_yields) else 0.015, 4),
                "prix_actuel":prix[i]     if i < len(prix)      else None,
                "signal_news":"➡️ Neutre",
            }
            if esg_data and tk in esg_data:
                esg = esg_data[tk]
                stock.update({
                    "esg_score":          esg.get("our_score", stock["esg_score"]),
                    "esg_sustainalytics": esg.get("sustainalytics_score"),
                    "esg_tier":           esg.get("tier", {}).get("label", "—"),
                    "esg_performance":    esg.get("performance"),
                    "esg_peer_group":     esg.get("peer_group"),
                    "esg_env":            esg.get("env_score"),
                    "esg_soc":            esg.get("soc_score"),
                    "esg_gov":            esg.get("gov_score"),
                    "esg_data_source":    esg.get("data_source", "live"),
                    "esg_fetched_at":     esg.get("fetched_at"),
                })
            composition.append(stock)

        return sorted(composition, key=lambda x: x["poids_pct"], reverse=True)

    @staticmethod
    def _max_poids(A: float) -> float:
        if A <= 2: return 0.15
        if A <= 4: return 0.12
        return 0.10
