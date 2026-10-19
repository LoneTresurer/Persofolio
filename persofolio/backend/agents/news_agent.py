"""
agents/news_agent.py
=====================
Agent Actualités — Analyse de sentiment RSS en temps réel

Sources RSS (gratuites, sans clé API) :
  - Yahoo Finance RSS par ticker
  - Google News RSS (recherche ticker)
  - Reuters RSS business

Analyse :
  1. Récupération des flux RSS pour les tickers du portefeuille
  2. Classification des articles par sentiment (Positif / Neutre / Négatif)
  3. Calcul d'un score de sentiment global pour le portefeuille
  4. Ajustement du rendement prédit en fonction du sentiment
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger("news")

# ── Mots-clés de sentiment ────────────────────────────────────────────────────
POSITIVE_KEYWORDS = [
    "hausse","croissance","bénéfice","profit","record","acquisition","dividende",
    "augmentation","révision","surperformance","guidance","beat","rallye",
    "growth","rise","record","dividend","buy","upgrade","outperform","rally",
    "strong","positive","gains","momentum","expansion","breakthrough","award",
]
NEGATIVE_KEYWORDS = [
    "baisse","perte","chute","recul","avertissement","amende","procès","rappel",
    "crise","risque","dégradation","downgrade","vente","sell","loss","decline",
    "fall","warning","lawsuit","fine","recall","risk","cut","miss","negative",
    "fraud","investigation","layoff","restructuring","bankruptcy",
]
CRITICAL_NEGATIVE = ["fraude","scandale","faillite","enquête","sanction","plainte",
                     "fraud","scandal","bankruptcy","investigation","sanction","complaint"]


class NewsAgent:
    """Agent d'analyse des actualités financières par flux RSS."""

    RSS_URLS = {
        "yahoo": "https://finance.yahoo.com/rss/headline?s={ticker}",
        "google": "https://news.google.com/rss/search?q={query}+stock&hl=fr&gl=FR&ceid=FR:fr",
    }

    async def analyser(self, tickers: List[str]) -> dict:
        """
        Analyse les actualités pour une liste de tickers.

        Returns:
            dict avec alertes, sentiment_marché, impact_rendement, résumé
        """
        if not tickers:
            return self._empty_result()

        all_articles: List[dict] = []

        async with httpx.AsyncClient(timeout=8.0) as client:
            tasks = [self._fetch_ticker_news(client, tk) for tk in tickers[:6]]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for ticker, result in zip(tickers, results):
            if isinstance(result, list):
                for art in result:
                    art["ticker"] = ticker
                all_articles.extend(result)

        if not all_articles:
            return self._empty_result()

        # Analyser le sentiment de chaque article
        analysed = [self._analyse_article(art) for art in all_articles]

        # Agréger par ticker
        alertes = self._construire_alertes(analysed, tickers)

        # Sentiment global du marché
        scores = [a["score"] for a in analysed]
        sentiment_global = sum(scores) / len(scores) if scores else 0.0

        # Impact estimé sur le rendement
        ajustement = self._calculer_ajustement(alertes, sentiment_global)

        return {
            "alertes":           alertes[:8],  # top 8 alertes
            "sentiment_marche":  round(sentiment_global, 3),
            "n_articles":        len(analysed),
            "impact_rendement":  ajustement,
            "resume_investisseur": self._resume(alertes, sentiment_global),
        }

    async def _fetch_ticker_news(self, client: httpx.AsyncClient, ticker: str) -> List[dict]:
        """Récupère les actualités RSS pour un ticker."""
        articles = []

        # Yahoo Finance RSS
        try:
            url  = self.RSS_URLS["yahoo"].format(ticker=ticker)
            resp = await client.get(url, headers={"User-Agent": "PersoFolio/4.0"})
            if resp.status_code == 200:
                articles.extend(self._parse_rss(resp.text, ticker))
        except Exception:
            pass

        # Google News RSS (si Yahoo ne retourne rien)
        if not articles:
            try:
                q    = quote(ticker.replace(".PA", "").replace(".DE", "").replace(".L", ""))
                url  = self.RSS_URLS["google"].format(query=q)
                resp = await client.get(url, headers={"User-Agent": "PersoFolio/4.0"})
                if resp.status_code == 200:
                    articles.extend(self._parse_rss(resp.text, ticker))
            except Exception:
                pass

        return articles[:5]  # max 5 articles par ticker

    @staticmethod
    def _parse_rss(xml_text: str, ticker: str) -> List[dict]:
        """Parse un flux RSS XML de manière simple (sans dépendance externe)."""
        articles = []
        # Extraire les items
        items = re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL)
        for item in items[:5]:
            titre = re.search(r"<title>(.*?)</title>", item)
            lien  = re.search(r"<link>(.*?)</link>", item)
            date  = re.search(r"<pubDate>(.*?)</pubDate>", item)
            desc  = re.search(r"<description>(.*?)</description>", item)

            if titre:
                t_text = re.sub(r"<[^>]+>", "", titre.group(1)).strip()
                d_text = re.sub(r"<[^>]+>", "", desc.group(1) if desc else "").strip() if desc else ""
                articles.append({
                    "titre":   t_text,
                    "lien":    lien.group(1).strip() if lien else "",
                    "date":    date.group(1).strip() if date else "",
                    "desc":    d_text[:200],
                    "ticker":  ticker,
                })
        return articles

    def _analyse_article(self, article: dict) -> dict:
        """Analyse le sentiment d'un article."""
        texte = (article.get("titre", "") + " " + article.get("desc", "")).lower()

        pos_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in texte)
        neg_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in texte)
        crit_neg  = any(kw in texte for kw in CRITICAL_NEGATIVE)

        if crit_neg:
            score = -0.9
        elif neg_count > pos_count + 1:
            score = -(neg_count / max(pos_count + neg_count, 1)) * 0.8
        elif pos_count > neg_count + 1:
            score = (pos_count / max(pos_count + neg_count, 1)) * 0.7
        else:
            score = 0.0

        # Déterminer le type d'alerte
        if crit_neg or score < -0.5:
            alerte_type = "VIGILANCE"
        elif score < -0.2:
            alerte_type = "RISQUE"
        elif score > 0.4:
            alerte_type = "OPPORTUNITÉ"
        elif score > 0.2:
            alerte_type = "POSITIF"
        else:
            alerte_type = "NEUTRE"

        return {
            **article,
            "score":       round(score, 3),
            "type":        alerte_type,
            "titre_source":article.get("titre", ""),
            "source":      "Yahoo Finance RSS",
        }

    def _construire_alertes(self, articles: List[dict], tickers: List[str]) -> List[dict]:
        """Filtre et trie les alertes importantes par ordre de priorité."""
        importantes = [
            a for a in articles
            if a["type"] in ("VIGILANCE", "RISQUE", "OPPORTUNITÉ")
        ]
        return sorted(importantes, key=lambda x: abs(x["score"]), reverse=True)

    @staticmethod
    def _calculer_ajustement(alertes: List[dict], sentiment_global: float) -> dict:
        """
        Calcule l'ajustement du rendement basé sur le sentiment des actualités.
        Calibré pour rester dans des bornes réalistes (±3%/an).
        """
        if abs(sentiment_global) < 0.1:
            return {
                "ajustement_pct": 0,
                "direction": "neutre",
                "n_tickers_signal": 0,
                "confiance": "faible",
                "message": "Actualités neutres — pas d'ajustement du rendement.",
            }

        adj = round(sentiment_global * 2.0, 2)  # max ±2%/an d'ajustement
        n_sig = len(set(a["ticker"] for a in alertes))

        return {
            "ajustement_pct":  adj,
            "direction":       "positif" if adj > 0 else "négatif",
            "n_tickers_signal":n_sig,
            "confiance":       "modérée" if n_sig >= 3 else "faible",
            "message": (
                f"Sentiment {'positif' if adj > 0 else 'négatif'} détecté sur "
                f"{n_sig} titre(s) — ajustement rendement : {'+' if adj >= 0 else ''}{adj}%/an"
            ),
        }

    @staticmethod
    def _resume(alertes: List[dict], sentiment: float) -> str:
        """Génère un résumé textuel pour l'investisseur."""
        if not alertes:
            return "Aucune actualité significative détectée sur les titres du portefeuille."
        n_opp = sum(1 for a in alertes if a["type"] == "OPPORTUNITÉ")
        n_vig = sum(1 for a in alertes if a["type"] in ("VIGILANCE", "RISQUE"))
        if n_vig > n_opp:
            return (f"{n_vig} alerte(s) de vigilance détectée(s) sur le portefeuille. "
                    "Surveillance renforcée recommandée.")
        if n_opp > 0:
            return (f"{n_opp} opportunité(s) identifiée(s) sur le portefeuille. "
                    "Sentiment de marché globalement favorable.")
        return "Actualités mitigées — position neutre recommandée."

    @staticmethod
    def _empty_result() -> dict:
        return {
            "alertes": [],
            "sentiment_marche": 0.0,
            "n_articles": 0,
            "impact_rendement": {
                "ajustement_pct": 0, "direction": "neutre",
                "n_tickers_signal": 0, "confiance": "faible",
                "message": "Aucune actualité analysée.",
            },
            "resume_investisseur": "Analyse des actualités non disponible.",
        }
