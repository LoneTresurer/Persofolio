"""
agents/esg_agent.py
===================
Agent ESG — Scores calculés automatiquement depuis Yahoo Finance.

METHODE DE CALCUL :
  Le score ESG est calculé à partir de vraies données financières
  téléchargées en temps réel depuis Yahoo Finance (yfinance).
  
  3 composantes calculées séparément puis combinées :

  E - Environnement (30%) :
      Basé sur le secteur d'activité et le pays du siège.
      Source académique : les secteurs et pays sont les meilleurs
      proxies ESG gratuits disponibles (Refinitiv, 2023).

  S - Social (30%) :
      Basé sur la taille de l'entreprise (nb employés) et la
      stabilité des dividendes. Une entreprise qui paye des
      dividendes réguliers traite mieux ses parties prenantes.

  G - Gouvernance (40%) :
      Basé sur la volatilité du titre, le niveau d'endettement,
      et la capitalisation boursière. Les entreprises bien
      gouvernées sont moins volatiles et moins endettées.

  Score final = 0.30*E + 0.30*S + 0.40*G  (échelle 0-100)

  Ces critères sont utilisés comme proxies ESG dans la littérature
  académique en finance (Berg et al., 2022 ; Refinitiv Methodology).

  Cache 24h pour éviter de surcharger Yahoo Finance.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional

import yfinance as yf

logger = logging.getLogger("esg_agent")

EXCLUSION_THRESHOLD = 30.0
CACHE_TTL_SECONDS   = 86_400   # 24h

_executor = ThreadPoolExecutor(max_workers=6)

# ── Scores environnementaux par secteur ───────────────────────────────────────
# Source : classification MSCI / Refinitiv par intensité carbone sectorielle
SECTOR_ENV_SCORE = {
    "Technology":             82,
    "Healthcare":             75,
    "Financial Services":     68,
    "Industrials":            62,
    "Consumer Defensive":     65,
    "Consumer Cyclical":      60,
    "Communication Services": 70,
    "Real Estate":            58,
    "Basic Materials":        45,
    "Utilities":              72,   # beaucoup investissent dans le vert
    "Energy":                 35,   # pétrole/gaz = score bas
}

# ── Bonus environnemental par pays ────────────────────────────────────────────
# Source : Environmental Performance Index (EPI) Yale University
COUNTRY_ENV_BONUS = {
    "Sweden":        15, "Denmark":    14, "Finland":  13,
    "Switzerland":   12, "Norway":     12, "Austria":  11,
    "Germany":       10, "France":     10, "Netherlands": 9,
    "United Kingdom": 8, "Canada":      7, "Australia":  6,
    "United States":  4, "Japan":       5, "South Korea": 3,
    "China":          0, "India":       0,
}


def _compute_esg(ticker: str) -> Optional[dict]:
    """
    Calcule le score ESG depuis les données Yahoo Finance.
    Exécuté dans un thread séparé (yfinance est synchrone).
    """
    try:
        tk   = yf.Ticker(ticker)
        info = tk.info or {}

        if not info or info.get("quoteType") is None:
            return None

        # ── COMPOSANTE E — Environnement (30%) ───────────────────────────────

        sector  = info.get("sector", "")
        country = info.get("country", "")

        env_base  = SECTOR_ENV_SCORE.get(sector, 55)
        env_bonus = COUNTRY_ENV_BONUS.get(country, 3)
        env_score = min(100, env_base + env_bonus)

        # ── COMPOSANTE S — Social (30%) ───────────────────────────────────────

        employees    = info.get("fullTimeEmployees") or 0
        dividend_yld = info.get("dividendYield") or 0
        payout_ratio = info.get("payoutRatio") or 0

        # Taille : plus l'entreprise est grande, plus elle a d'obligations sociales
        if employees >= 200_000:
            size_score = 80
        elif employees >= 50_000:
            size_score = 70
        elif employees >= 10_000:
            size_score = 60
        elif employees >= 1_000:
            size_score = 50
        else:
            size_score = 40

        # Dividende stable = bonne relation avec les parties prenantes
        if dividend_yld > 0.01 and 0.1 < payout_ratio < 0.8:
            dividend_score = 75   # dividende raisonnable et régulier
        elif dividend_yld > 0:
            dividend_score = 60   # dividende existe mais mal calibré
        else:
            dividend_score = 50   # pas de dividende (souvent tech en croissance)

        soc_score = 0.5 * size_score + 0.5 * dividend_score

        # ── COMPOSANTE G — Gouvernance (40%) ──────────────────────────────────

        beta             = info.get("beta") or 1.0
        debt_to_equity   = info.get("debtToEquity") or 0
        market_cap       = info.get("marketCap") or 0
        profit_margins   = info.get("profitMargins") or 0

        # Volatilité (bêta) : plus proche de 1 = mieux géré
        if 0.5 <= beta <= 1.2:
            beta_score = 80
        elif 0.3 <= beta <= 1.5:
            beta_score = 65
        elif beta < 0.3:
            beta_score = 55   # très défensif, pas forcément bien géré
        else:
            beta_score = 40   # très volatil = risque de gouvernance

        # Dette : une dette excessive = mauvaise gouvernance
        if debt_to_equity <= 30:
            debt_score = 85
        elif debt_to_equity <= 80:
            debt_score = 70
        elif debt_to_equity <= 150:
            debt_score = 55
        else:
            debt_score = 35

        # Taille de marché : les grandes capitalisations sont plus surveillées
        if market_cap >= 100_000_000_000:    # > 100 Mrd$
            cap_score = 80
        elif market_cap >= 10_000_000_000:   # > 10 Mrd$
            cap_score = 68
        elif market_cap >= 1_000_000_000:    # > 1 Mrd$
            cap_score = 55
        else:
            cap_score = 40

        # Marge bénéficiaire : entreprise rentable = bien gérée
        if profit_margins >= 0.20:
            margin_score = 80
        elif profit_margins >= 0.10:
            margin_score = 68
        elif profit_margins >= 0:
            margin_score = 55
        else:
            margin_score = 35   # pertes = gouvernance à surveiller

        gov_score = (
            0.30 * beta_score   +
            0.25 * debt_score   +
            0.25 * cap_score    +
            0.20 * margin_score
        )

        # ── SCORE FINAL ───────────────────────────────────────────────────────
        our_score = round(0.30 * env_score + 0.30 * soc_score + 0.40 * gov_score)
        our_score = max(20, min(95, our_score))   # bornes réalistes

        # Conversion indicative vers Sustainalytics (0-40+, inférieur = meilleur)
        sustainalytics = round((100 - our_score) / 1.5, 1)

        logger.info(
            f"ESG calc {ticker}: {our_score}/100 "
            f"[E={env_score:.0f} S={soc_score:.0f} G={gov_score:.0f}] "
            f"| secteur={sector} pays={country}"
        )

        return {
            "ticker":               ticker,
            "sustainalytics_score": sustainalytics,
            "our_score":            our_score,
            "env_score":            round(env_score, 1),
            "soc_score":            round(soc_score, 1),
            "gov_score":            round(gov_score, 1),
            "rating_year":          str(datetime.now().year),
            "rating_month":         f"{datetime.now().month:02d}",
            "performance":          None,
            "peer_group":           sector or None,
            "peer_avg":             None,
            "peer_min":             None,
            "peer_max":             None,
            "percentile":           None,
            "controversy_level":    0,
            "controversies":        [],
            "tier":                 _tier(our_score),
            "is_excluded":          our_score < EXCLUSION_THRESHOLD,
            "source": (
                f"Score calculé depuis données financières réelles Yahoo Finance "
                f"(secteur: {sector}, pays: {country})"
            ),
            "fetched_at":        datetime.now().isoformat(),
            "data_source":       "calculated_from_financials",
            "data_age_days":     0,
            "freshness_warning": (
                "Score ESG estimé à partir de données financières réelles "
                "(secteur, gouvernance, taille). Mis à jour toutes les 24h."
            ),
        }

    except Exception as e:
        logger.error(f"ESG calcul erreur {ticker}: {e}")
        return None


def _tier(score: float) -> dict:
    if score >= 70:
        return {"label": "Excellent", "color": "#16a34a", "bg": "rgba(22,163,74,.15)"}
    if score >= 55:
        return {"label": "Bon",       "color": "#22c55e", "bg": "rgba(34,197,94,.12)"}
    if score >= 40:
        return {"label": "Moyen",     "color": "#facc15", "bg": "rgba(250,204,21,.12)"}
    if score >= 30:
        return {"label": "Faible",    "color": "#f97316", "bg": "rgba(249,115,22,.12)"}
    return     {"label": "Critique",  "color": "#ef4444", "bg": "rgba(239,68,68,.12)"}


class ESGAgent:

    def __init__(self):
        self._cache:    Dict[str, dict]  = {}
        self._cache_ts: Dict[str, float] = {}

    async def fetch_esg_score(self, ticker: str) -> Optional[dict]:
        """
        Calcule le score ESG depuis Yahoo Finance.
        Cache 24h pour éviter de refaire le calcul à chaque requête.
        """
        ticker = ticker.upper()

        # Vérifier le cache
        if ticker in self._cache:
            age = datetime.now().timestamp() - self._cache_ts.get(ticker, 0)
            if age < CACHE_TTL_SECONDS:
                return self._cache[ticker]

        # Calcul dans un thread (yfinance est synchrone)
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, _compute_esg, ticker)

        if result:
            self._cache[ticker]    = result
            self._cache_ts[ticker] = datetime.now().timestamp()

        return result

    async def fetch_portfolio_esg(self, tickers: List[str]) -> Dict[str, dict]:
        """Calcule les scores ESG pour une liste de tickers en parallèle."""

        # Calcul parallèle — tous les tickers en même temps
        tasks   = [self.fetch_esg_score(t) for t in tickers]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        results: Dict[str, dict] = {}
        for ticker, res in zip(tickers, results_list):
            if isinstance(res, dict):
                results[ticker] = res

        n_ok = len(results)
        logger.info(
            f"ESG portfolio: {n_ok}/{len(tickers)} scores calculés "
            f"| source=yahoo_finance_calculated"
        )
        return results
