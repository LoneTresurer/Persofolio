"""
scripts/update_esg_scores.py
============================
Script de mise à jour mensuelle des scores ESG.

Ce script va chercher automatiquement les vrais scores ESG de chaque
entreprise sur le site public de Sustainalytics, puis les sauvegarde
dans data/esg_scores.json (lu par esg_agent.py).

COMMENT LANCER CE SCRIPT :
  1. Ouvrir PowerShell
  2. Naviguer vers le dossier backend :
       cd C:/Users/21655/Desktop/StartUp/ecofolio_v7_final/ecofolio_v7/backend
  3. Lancer le script :
       python scripts/update_esg_scores.py
  4. Attendre la fin (environ 2-3 minutes)
  5. Relancer le serveur normalement

FRÉQUENCE RECOMMANDÉE : une fois par mois
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("esg_updater")

# ── Liste des entreprises à mettre à jour ─────────────────────────────────────
TICKER_TO_NAME = {
    "MSFT":        "Microsoft",
    "GOOGL":       "Alphabet",
    "AAPL":        "Apple",
    "AMZN":        "Amazon",
    "META":        "Meta Platforms",
    "ADBE":        "Adobe",
    "INTU":        "Intuit",
    "CDNS":        "Cadence Design Systems",
    "NEE":         "NextEra Energy",
    "JNJ":         "Johnson Johnson",
    "AZN.L":       "AstraZeneca",
    "GSK.L":       "GSK",
    "ROG.SW":      "Roche",
    "NESN.SW":     "Nestle",
    "NOVN.SW":     "Novartis",
    "SAN.PA":      "Sanofi",
    "ABT":         "Abbott Laboratories",
    "DHR":         "Danaher",
    "UNH":         "UnitedHealth",
    "ZTS":         "Zoetis",
    "ISRG":        "Intuitive Surgical",
    "EW":          "Edwards Lifesciences",
    "SIE.DE":      "Siemens",
    "AIR.PA":      "Airbus",
    "SAF.PA":      "Safran",
    "HON":         "Honeywell",
    "ITW":         "Illinois Tool Works",
    "ETN":         "Eaton",
    "SSE.L":       "SSE",
    "NG.L":        "National Grid",
    "ENGI.PA":     "Engie",
    "DTE.DE":      "Deutsche Telekom",
    "ENB":         "Enbridge",
    "AMT":         "American Tower",
    "OR.PA":       "L Oreal",
    "PG":          "Procter Gamble",
    "ULVR.L":      "Unilever",
    "HEN3.DE":     "Henkel",
    "BNP.PA":      "BNP Paribas",
    "UBSG.SW":     "UBS Group",
    "BMO":         "Bank of Montreal",
    "TD":          "Toronto-Dominion Bank",
    "RY":          "Royal Bank of Canada",
    "BLK":         "BlackRock",
    "MCO":         "Moody's",
    "SPGI":        "S&P Global",
    "MA":          "Mastercard",
    "V":           "Visa",
    "TTE.PA":      "TotalEnergies",
    "SHEL.L":      "Shell",
    "BP.L":        "BP",
    "LIN":         "Linde",
    "BHP.AX":      "BHP Group",
    "LONN.SW":     "Lonza",
    "CNR":         "Canadian National Railway",
    "ABBN.SW":     "ABB",
    "ZURN.SW":     "Zurich Insurance",
    "SAP.DE":      "SAP",
    "DSY.PA":      "Dassault Systemes",
    "VOLV-B.ST":   "Volvo",
    "ESSITY-B.ST": "Essity",
    "SAND.ST":     "Sandvik",
    "ERIC-B.ST":   "Ericsson",
    "SEB-A.ST":    "SEB",
    "CBA.AX":      "Commonwealth Bank Australia",
    "CSL.AX":      "CSL",
    "MQG.AX":      "Macquarie Group",
    "ADP":         "Automatic Data Processing",
    "RWE.DE":      "RWE",
    "ETN":         "Eaton",
}

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "esg_scores.json")

SUSTAIN_URL = "https://www.sustainalytics.com/sustapi/companyratings/getcompanyratings"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://www.sustainalytics.com/esg-ratings",
    "Origin":     "https://www.sustainalytics.com",
}


async def fetch_one(client: httpx.AsyncClient, ticker: str, name: str) -> Optional[dict]:
    """Cherche une entreprise sur Sustainalytics et retourne son score."""
    try:
        resp = await client.get(
            SUSTAIN_URL,
            params={"q": name, "page": 1, "pageSize": 5, "resourcePackage": "Sustainalytics"},
            headers=HEADERS,
            timeout=15.0,
        )

        if resp.status_code == 200:
            data      = resp.json()
            companies = data.get("results") or data.get("companies") or []

            if companies:
                company   = companies[0]
                risk_score = (
                    company.get("esgRiskScore") or
                    company.get("riskScore") or
                    company.get("totalEsg")
                )
                if risk_score is not None:
                    risk  = float(risk_score)
                    score = max(0, min(100, round(100 - risk * 1.5)))
                    return {
                        "our_score":            score,
                        "sustainalytics_score": risk,
                        "company_name":         company.get("name", name),
                        "industry":             company.get("industry"),
                        "source":               "Sustainalytics (site public)",
                        "fetched_at":           datetime.now().isoformat(),
                    }

        logger.warning(f"  [{ticker}] HTTP {resp.status_code} ou données manquantes")
        return None

    except httpx.TimeoutException:
        logger.warning(f"  [{ticker}] Timeout")
        return None
    except Exception as e:
        logger.error(f"  [{ticker}] Erreur: {e}")
        return None


async def main():
    print("\n" + "="*55)
    print("  MISE À JOUR DES SCORES ESG — PersoFolio")
    print("="*55)

    # Charger scores existants
    existing = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            existing = json.load(f)
        print(f"\n→ {len(existing)} scores existants chargés")

    results = dict(existing)
    success, kept, failed = 0, 0, []

    async with httpx.AsyncClient() as client:
        total = len(TICKER_TO_NAME)
        for i, (ticker, name) in enumerate(TICKER_TO_NAME.items(), 1):
            print(f"[{i:02d}/{total}] {ticker:<15} ({name})...", end=" ", flush=True)

            data = await fetch_one(client, ticker, name)

            if data:
                results[ticker] = data
                print(f"✓ Score: {data['our_score']}/100  (risque ESG: {data['sustainalytics_score']})")
                success += 1
            elif ticker in existing:
                print(f"→ Ancien score conservé: {existing[ticker]['our_score']}/100")
                kept += 1
            else:
                print("✗ Indisponible")
                failed.append(ticker)

            await asyncio.sleep(1.2)

    # Sauvegarder
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\n" + "="*55)
    print(f"  Nouveaux scores récupérés : {success}")
    print(f"  Anciens scores conservés  : {kept}")
    print(f"  Introuvables              : {len(failed)}")
    if failed:
        print(f"  Tickers échoués : {', '.join(failed)}")
    print(f"\n  Fichier sauvegardé : {os.path.abspath(OUTPUT_FILE)}")
    print("  → Relancez le serveur pour appliquer les nouveaux scores")
    print("="*55 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
