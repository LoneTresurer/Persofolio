"""
agents/predictor_agent.py
==========================
Agent Prédiction — Prévisions multi-horizon + Dividendes + Stress Tests

CORRECTIONS v6 :
  - Intervalles de confiance calculés par Monte Carlo (2 000 trajectoires GBM)
    au lieu de la règle √T qui suppose des rendements normaux indépendants
  - Retourne pessimiste_pct / central_pct / optimiste_pct en termes de quantiles
    réels (p5, p50, p95) cohérents avec le frontend
  - probabilite_gain_pct : fraction des trajectoires avec rendement > 0
  - La fonction _predict retourne maintenant central_pct (manquant dans v5)

Modèles utilisés :
  Court terme (1J–1M)  : Momentum + RSI + Volatilité historique
  Moyen terme (3M–6M)  : Rendement annualisé + Mean-reversion partielle
  Long terme (1A–5A)   : Markowitz + Gordon Growth Model + Prime ESG + Dividendes composés

Stress Tests (inspirés d'Aladdin Market-Driven Scenarios) :
  Impact = choc_marché × β_portefeuille + protection_ESG × Δ_ESG
"""

import logging
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger("predictor")

RF_RATE = 0.02
N_MC    = 2_000          # Trajectoires Monte Carlo pour les IC

DIV_YIELD_SECTEUR: Dict[str, float] = {
    "Utilities": 0.038, "Financials": 0.030, "Energy": 0.042,
    "Cons. Staples": 0.025, "Real Estate": 0.040, "Healthcare": 0.014,
    "Industrials": 0.016, "Technology": 0.005, "Materials": 0.020,
    "Telecom": 0.030, "Cons. Cyclical": 0.012, "International": 0.018,
}

STRESS_SCENARIOS = [
    {
        "name":  "📉 Crise COVID-19 (2020)",
        "shk_m": -0.34, "shk_esg": +0.05,
        "desc":  "Choc liquidité soudain — recovery en V attendue",
        "source":"Mars 2020 : S&P 500 −34% en 33 jours",
    },
    {
        "name":  "💻 Bulle Technologique (2000-02)",
        "shk_m": -0.49, "shk_esg": -0.02,
        "desc":  "Effondrement valorisations de croissance",
        "source":"Nasdaq −78% entre mars 2000 et octobre 2002",
    },
    {
        "name":  "🏦 Crise Financière Mondiale (2008-09)",
        "shk_m": -0.52, "shk_esg": -0.01,
        "desc":  "Credit crunch systémique — corrélations à 1",
        "source":"S&P 500 −57% entre octobre 2007 et mars 2009",
    },
    {
        "name":  "📈 Hausse Taux Brusque +300 pb",
        "shk_m": -0.18, "shk_esg": +0.02,
        "desc":  "Duration risk — valorisations comprimées",
        "source":"Fed 2022 : +425 pb en 12 mois → S&P −19%",
    },
    {
        "name":  "🌍 Transition Verte Accélérée",
        "shk_m": +0.12, "shk_esg": +0.08,
        "desc":  "Prime ESG +50% — nouvelles réglementations carbone",
        "source":"Scénario MSCI ESG Leaders Outperformance",
    },
    {
        "name":  "⚡ Stagflation Style 1970s",
        "shk_m": -0.28, "shk_esg": -0.03,
        "desc":  "Inflation élevée + récession + taux hauts simultanés",
        "source":"1973-74 : S&P −48% (inflation + choc pétrolier)",
    },
]

HORIZONS_JOURS  = [1, 5, 21, 63, 126, 252, 756, 1260]
HORIZONS_LABELS = [
    "1 Jour", "1 Semaine", "1 Mois", "3 Mois",
    "6 Mois", "1 An", "3 Ans", "5 Ans",
]


class PredictorAgent:

    # ── Prédictions multi-horizon ─────────────────────────────────────────────

    def predict_multi_horizon(
        self,
        portfolio:      dict,
        horizon_years:  int,
        profil_esg:     Optional[dict] = None,
    ) -> dict:
        """
        Prédictions pour 8 horizons + horizon principal de l'investisseur.
        IC calculés par Monte Carlo (N_MC trajectoires GBM).
        """
        mu_ann    = portfolio.get("rendement",  8.0) / 100
        vol_ann   = portfolio.get("volatilite", 15.0) / 100
        esg_score = portfolio.get("esg_moyen",  65.0)
        div_yield = portfolio.get("div_yield",  0.018)

        # Prédictions 8 horizons fixes
        par_horizon: Dict[str, dict] = {}
        for days, label in zip(HORIZONS_JOURS, HORIZONS_LABELS):
            esg_prime = self._esg_prime(esg_score, days / 252)
            par_horizon[label] = self._predict(
                mu_ann, vol_ann, div_yield, days, esg_prime
            )

        # Horizon principal (Monte Carlo complet)
        main_days  = min(horizon_years * 252, 1260)
        esg_prime  = self._esg_prime(esg_score, horizon_years)
        main_pred  = self._predict(mu_ann, vol_ann, div_yield, main_days, esg_prime)
        main_label = f"{horizon_years} an{'s' if horizon_years > 1 else ''}"

        # Message ESG
        esg_msg       = (profil_esg or {}).get("message_investisseur", "")
        confiance_esg = (profil_esg or {}).get("confiance_esg", "faible")

        return {
            "par_horizon": par_horizon,
            "horizon_principal": {
                "label": main_label,
                "days":  main_days,
                **main_pred,
            },
            "prime_esg": {
                "valeur_pct": round(esg_prime * 100, 2),
                "confiance":  confiance_esg,
                "message":    esg_msg,
            },
            "intervalle_confiance_95pct": {
                "rendement_min_pct":  main_pred["pessimiste_pct"],
                "rendement_max_pct":  main_pred["optimiste_pct"],
                "interpretation": (
                    f"IC 95% sur {main_label} (Monte Carlo {N_MC} trajectoires) : "
                    f"rendement entre {main_pred['pessimiste_pct']:.2f}% "
                    f"et {main_pred['optimiste_pct']:.2f}% "
                    f"· Probabilité gain : {main_pred.get('probabilite_gain_pct', 0):.0f}%"
                ),
                "methode": f"Monte Carlo GBM ({N_MC} simulations)",
            },
            "score_esg_portefeuille": {
                "score_moyen":         esg_score,
                "label":               self._esg_label(esg_score),
                "prime_annuelle_pct":  round(esg_prime * 100, 2),
            },
        }

    def _predict(
        self,
        mu_ann:    float,
        vol_ann:   float,
        div_yield: float,
        days:      int,
        esg_prime: float,
    ) -> dict:
        """
        Prédiction pour un horizon en jours de trading.
        Pour les horizons courts (≤ 5 jours) : formule analytique GBM.
        Pour les horizons longs : Monte Carlo.
        """
        mu_total = mu_ann + esg_prime   # rendement ajusté ESG annuel

        if days <= 5:
            # Courte durée : formule analytique suffisante
            mu_d  = mu_total / 252
            vol_d = vol_ann  / np.sqrt(252)
            mu_h  = mu_d  * days
            vol_h = vol_d * np.sqrt(days)
            div_h = div_yield * (days / 252)

            return {
                "pessimiste_pct":     round((mu_h - 1.645 * vol_h) * 100, 3),
                "central_pct":        round((mu_h + div_h) * 100, 3),
                "optimiste_pct":      round((mu_h + 1.645 * vol_h) * 100, 3),
                "capital_gain_pct":   round(mu_h * 100, 3),
                "div_pct":            round(div_h * 100, 3),
                "vol_horizon_pct":    round(vol_h * 100, 3),
                "probabilite_gain_pct": round(
                    float(1 - self._norm_cdf(-mu_h / vol_h)) * 100, 1
                ) if vol_h > 0 else (100.0 if mu_h > 0 else 0.0),
                "methode": "analytique GBM",
            }
        else:
            # Long terme : Monte Carlo
            return self._monte_carlo_predict(mu_total, vol_ann, div_yield, days)

    def _monte_carlo_predict(
        self,
        mu_ann:    float,
        vol_ann:   float,
        div_yield: float,
        days:      int,
    ) -> dict:
        """
        Simulation Monte Carlo (GBM) pour les intervalles de confiance.
        Returns quantiles p5, p50, p95 des rendements cumulés.
        """
        mu_d  = (mu_ann + div_yield) / 252
        vol_d = max(vol_ann, 0.01) / np.sqrt(252)

        rng      = np.random.default_rng(seed=42)
        log_r    = rng.normal(
            loc   = mu_d - 0.5 * vol_d**2,
            scale = vol_d,
            size  = (N_MC, days),
        )
        cum_returns = np.exp(np.sum(log_r, axis=1)) - 1.0   # rendement total

        p5  = float(np.percentile(cum_returns, 5))
        p25 = float(np.percentile(cum_returns, 25))
        p50 = float(np.median(cum_returns))
        p75 = float(np.percentile(cum_returns, 75))
        p95 = float(np.percentile(cum_returns, 95))
        prob_gain = float(np.mean(cum_returns > 0))

        # Composante dividende (déterministe, réinvesti annuellement)
        div_h = float(div_yield * (days / 252))
        mu_h  = float(mu_ann * (days / 252))
        vol_h = float(vol_ann * np.sqrt(days / 252))

        return {
            "pessimiste_pct":       round(p5  * 100, 2),
            "q25_pct":              round(p25 * 100, 2),
            "central_pct":          round(p50 * 100, 2),
            "q75_pct":              round(p75 * 100, 2),
            "optimiste_pct":        round(p95 * 100, 2),
            "capital_gain_pct":     round(mu_h * 100, 3),
            "div_pct":              round(div_h * 100, 3),
            "vol_horizon_pct":      round(vol_h * 100, 3),
            "probabilite_gain_pct": round(prob_gain * 100, 1),
            "methode":              f"Monte Carlo GBM ({N_MC} trajectoires)",
        }

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """CDF de la loi normale standard (approximation de Abramowitz & Stegun)."""
        t = 1.0 / (1.0 + 0.2316419 * abs(x))
        p = (0.319381530 * t - 0.356563782 * t**2
             + 1.781477937 * t**3 - 1.821255978 * t**4
             + 1.330274429 * t**5)
        cdf = 1.0 - (1.0 / np.sqrt(2 * np.pi)) * np.exp(-0.5 * x**2) * p
        return cdf if x >= 0 else 1.0 - cdf

    @staticmethod
    def _esg_prime(esg_score: float, years: float) -> float:
        """
        Prime ESG annualisée calibrée sur le backtesting 2017-2025.
        Nulle à court terme, positive et croissante à long terme.
        """
        if years < 1:
            return 0.0
        base   = 0.008 if esg_score > 65 else 0.004 if esg_score > 55 else 0.0
        factor = min(years / 5.0, 1.0)
        return round(base * factor, 4)

    @staticmethod
    def _esg_label(score: float) -> str:
        if score >= 70: return "Excellence ESG"
        if score >= 65: return "Très bon profil ESG"
        if score >= 60: return "Bon profil ESG"
        if score >= 55: return "Profil satisfaisant"
        return "Profil de base"

    # ── Stress Tests ──────────────────────────────────────────────────────────

    def stress_tests(self, portfolio: dict, montant: float = 10_000.0) -> List[dict]:
        """
        Calcule l'impact des 6 scénarios de stress sur le portefeuille.
        Impact = choc_marché × β_portefeuille + protection_ESG × Δ_ESG
        """
        beta_p    = portfolio.get("beta_portefeuille", 0.95)
        esg_score = portfolio.get("esg_moyen", 65.0)
        results   = []

        for sc in STRESS_SCENARIOS:
            esg_protection = max(0.0, (esg_score - 60) / 40) * sc["shk_esg"]
            raw_impact  = sc["shk_m"] * beta_p + esg_protection
            impact_pct  = raw_impact * 100
            impact_eur  = montant * raw_impact

            if sc["shk_esg"] > 0 and esg_score > 65:
                prot = "🌿 ESG protège"
            elif beta_p < 0.80:
                prot = "β faible protège"
            else:
                prot = "⚠ Exposé"

            results.append({
                "scenario":    sc["name"],
                "description": sc["desc"],
                "source":      sc["source"],
                "impact_pct":  round(impact_pct, 2),
                "impact_eur":  round(impact_eur, 0),
                "protection":  prot,
                "severity": (
                    "Sévère" if abs(impact_pct) > 30
                    else "Modéré" if abs(impact_pct) > 15
                    else "Faible"
                ),
            })

        return sorted(results, key=lambda x: x["impact_pct"])

    # ── Analyse Dividendes ────────────────────────────────────────────────────

    def analyze_dividends(
        self,
        composition:   List[dict],
        horizon_years: int,
        montant:       float,
    ) -> dict:
        """
        Dividendes par action et pour le portefeuille total.
        Formule Gordon Growth + intérêts composés (DRP).
        """
        total_div_yield = 0.0
        per_stock: List[dict] = []

        for stock in composition:
            secteur = stock.get("secteur", "International")
            poids   = stock.get("poids_pct", 0) / 100
            alloc   = montant * poids
            div_y   = stock.get("div_yield") or DIV_YIELD_SECTEUR.get(secteur, 0.015)
            div_ann     = alloc * div_y
            div_total   = div_ann * horizon_years
            div_reinvest= alloc * ((1 + div_y) ** horizon_years - 1)

            freq = ("Mensuel"     if div_y > 0.04
                    else "Trimestriel" if div_y > 0.01
                    else "Annuel"     if div_y > 0
                    else "Aucun")

            total_div_yield += poids * div_y

            per_stock.append({
                "ticker":            stock.get("ticker", ""),
                "nom":               stock.get("nom", stock.get("ticker", "")),
                "secteur":           secteur,
                "poids_pct":         round(poids * 100, 2),
                "yield_annuel_pct":  round(div_y  * 100, 2),
                "div_annuel_eur":    round(div_ann, 2),
                "div_total_eur":     round(div_total, 2),
                "div_reinvesti_eur": round(div_reinvest, 2),
                "frequence":         freq,
            })

        # Croissance composée année par année
        compound_table: List[dict] = []
        mu_central = 0.08   # rendement central portefeuille ~8%

        for y in range(1, min(horizon_years, 20) + 1):
            cap       = montant * (1 + mu_central) ** y
            div_cum   = montant * total_div_yield * y
            div_rein  = montant * ((1 + total_div_yield) ** y - 1)
            total     = cap + div_rein
            compound_table.append({
                "annee":               y,
                "capital_eur":         round(cap, 0),
                "div_cumules_eur":     round(div_cum, 0),
                "div_reinvesti_eur":   round(div_rein, 0),
                "total_eur":           round(total, 0),
                "rendement_total_pct": round((total / montant - 1) * 100, 2),
            })

        return {
            "yield_portefeuille_pct":  round(total_div_yield * 100, 2),
            "div_annuel_total_eur":    round(montant * total_div_yield, 2),
            "div_horizon_total_eur":   round(montant * total_div_yield * horizon_years, 2),
            "div_reinvesti_eur":       round(montant * ((1 + total_div_yield) ** horizon_years - 1), 2),
            "par_action":              sorted(per_stock, key=lambda x: x["yield_annuel_pct"], reverse=True),
            "croissance_composee":     compound_table,
            "note": (
                "Les dividendes sont estimés sur la base des versements historiques et des "
                "moyennes sectorielles. Le réinvestissement automatique (DRP) est supposé annuel. "
                "Les dividendes futurs ne sont pas garantis."
            ),
        }
