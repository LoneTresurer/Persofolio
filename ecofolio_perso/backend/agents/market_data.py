"""
agents/market_data.py
=====================
Accès aux données de marché (Yahoo Finance via yfinance) pour le suivi de portefeuille.

Convention de change : `taux_eur(devise)` renvoie le nombre d'EUR pour 1 unité de devise.
  ex. USD → 0.92  signifie 1 $ = 0,92 €
Cas particulier : les actions de Londres cotent souvent en pence (« GBp ») → 1 GBp = 0,01 GBP.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger("market_data")

# Taux de repli (EUR pour 1 unité) si Yahoo Finance ne répond pas
TAUX_REPLI = {"EUR": 1.0, "USD": 0.92, "GBP": 1.17, "CHF": 1.05, "JPY": 0.0062,
              "CAD": 0.68, "AUD": 0.61, "SEK": 0.088, "DKK": 0.134, "NOK": 0.086,
              "HKD": 0.118}

# Pays (tel que renvoyé par Yahoo) → zone géographique
ZONES = {
    "France": "Europe", "Germany": "Europe", "Netherlands": "Europe", "Belgium": "Europe",
    "Italy": "Europe", "Spain": "Europe", "Portugal": "Europe", "Ireland": "Europe",
    "Luxembourg": "Europe", "Austria": "Europe", "Finland": "Europe", "Switzerland": "Europe",
    "United Kingdom": "Europe", "Sweden": "Europe", "Denmark": "Europe", "Norway": "Europe",
    "Poland": "Europe", "Greece": "Europe",
    "United States": "Amérique du Nord", "Canada": "Amérique du Nord",
    "Japan": "Asie-Pacifique", "China": "Asie-Pacifique", "Hong Kong": "Asie-Pacifique",
    "Taiwan": "Asie-Pacifique", "South Korea": "Asie-Pacifique", "India": "Asie-Pacifique",
    "Singapore": "Asie-Pacifique", "Australia": "Asie-Pacifique",
    "Brazil": "Amérique latine", "Mexico": "Amérique latine", "Chile": "Amérique latine",
    "South Africa": "Afrique & Moyen-Orient", "Israel": "Afrique & Moyen-Orient",
    "Tunisia": "Afrique & Moyen-Orient", "Morocco": "Afrique & Moyen-Orient",
}

PAYS_FR = {
    "France": "France", "Germany": "Allemagne", "Netherlands": "Pays-Bas", "Belgium": "Belgique",
    "Italy": "Italie", "Spain": "Espagne", "Switzerland": "Suisse", "United Kingdom": "Royaume-Uni",
    "Sweden": "Suède", "Denmark": "Danemark", "Norway": "Norvège", "Ireland": "Irlande",
    "United States": "États-Unis", "Canada": "Canada", "Japan": "Japon", "China": "Chine",
    "Taiwan": "Taïwan", "South Korea": "Corée du Sud", "India": "Inde", "Australia": "Australie",
    "Brazil": "Brésil", "Finland": "Finlande", "Luxembourg": "Luxembourg", "Portugal": "Portugal",
}

_cache_taux: Dict[str, tuple] = {}     # devise -> (taux, horodatage)


def zone_de(pays: Optional[str]) -> str:
    return ZONES.get(pays or "", "Autre / non renseigné")


def pays_fr(pays: Optional[str]) -> str:
    return PAYS_FR.get(pays or "", pays or "Non renseigné")


def _devise_base(devise: str) -> tuple:
    """GBp (pence) → (GBP, 0.01). Toute autre devise → (devise, 1)."""
    if devise in ("GBp", "GBX"):
        return "GBP", 0.01
    if devise == "ILA":
        return "ILS", 0.01
    return (devise or "EUR").upper(), 1.0


# ── Fiche d'un titre ─────────────────────────────────────────────────────────

def fiche_yahoo(ticker: str) -> Optional[dict]:
    """Nom, devise, secteur, pays et dernier cours d'un titre. None si inconnu."""
    try:
        tk = yf.Ticker(ticker)
        fi = tk.fast_info
        try:
            prix, devise = fi.last_price, fi.currency
        except Exception:
            prix, devise = None, None
        if not prix:
            hist = tk.history(period="5d")
            if hist.empty:
                return None
            prix = float(hist["Close"].dropna().iloc[-1])
        info = {}
        try:
            info = tk.info or {}
        except Exception:
            pass
        return {
            "ticker": ticker,
            "nom": info.get("longName") or info.get("shortName") or ticker,
            "devise": devise or info.get("currency") or "EUR",
            "secteur": info.get("sector") or ("ETF / Fonds" if info.get("quoteType") == "ETF" else None),
            "pays": info.get("country"),
            "type_actif": info.get("quoteType") or "EQUITY",
            "prix": float(prix),
        }
    except Exception as e:
        logger.warning(f"fiche {ticker}: {e}")
        return None


# ── Cours actuels ────────────────────────────────────────────────────────────

def _cours_un(ticker: str) -> Optional[dict]:
    try:
        hist = yf.Ticker(ticker).history(period="7d", auto_adjust=False)
        closes = hist["Close"].dropna()
        if closes.empty:
            return None
        return {"prix": float(closes.iloc[-1]),
                "cloture_veille": float(closes.iloc[-2]) if len(closes) > 1 else float(closes.iloc[-1]),
                "date_cours": closes.index[-1].strftime("%Y-%m-%d")}
    except Exception as e:
        logger.warning(f"cours {ticker}: {e}")
        return None


def cours_actuels(tickers: List[str]) -> Dict[str, dict]:
    """Dernier cours + clôture de la veille pour chaque ticker (téléchargement parallèle)."""
    if not tickers:
        return {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = dict(zip(tickers, ex.map(_cours_un, tickers)))
    return {k: v for k, v in res.items() if v}


# ── Taux de change ───────────────────────────────────────────────────────────

def taux_eur(devise: str, le: Optional[str] = None) -> float:
    """EUR pour 1 unité de `devise`, au jour `le` (YYYY-MM-DD) ou au plus récent."""
    base, facteur = _devise_base(devise)
    if base == "EUR":
        return 1.0 * facteur
    if le is None:
        c = _cache_taux.get(base)
        if c and time.time() - c[1] < 900:
            return c[0] * facteur
    try:
        paire = yf.Ticker(f"EUR{base}=X")        # cours = nombre de <devise> pour 1 EUR
        if le:
            d = datetime.strptime(le, "%Y-%m-%d").date()
            hist = paire.history(start=d - timedelta(days=7), end=d + timedelta(days=1))
        else:
            hist = paire.history(period="5d")
        closes = hist["Close"].dropna()
        if closes.empty:
            raise ValueError("pas de cotation")
        taux = 1.0 / float(closes.iloc[-1])
        if le is None:
            _cache_taux[base] = (taux, time.time())
        return taux * facteur
    except Exception as e:
        logger.warning(f"taux {base} ({le or 'actuel'}) indisponible, repli : {e}")
        return TAUX_REPLI.get(base, 1.0) * facteur


# ── Historiques (graphique de performance, indicateurs de risque) ───────────

def historique_clotures(tickers: List[str], debut: str) -> pd.DataFrame:
    """Clôtures journalières depuis `debut`, une colonne par ticker (index = dates)."""
    if not tickers:
        return pd.DataFrame()
    data = yf.download(tickers, start=debut, auto_adjust=False, progress=False,
                       group_by="column", threads=True)
    if data is None or data.empty:
        return pd.DataFrame()
    closes = data["Close"]
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(name=tickers[0])
    closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
    return closes.sort_index()


def historique_taux(devises: List[str], debut: str) -> pd.DataFrame:
    """EUR pour 1 unité de chaque devise, en journalier depuis `debut`."""
    out = {}
    bases = {d: _devise_base(d) for d in set(devises)}
    paires = sorted({f"EUR{b}=X" for b, _ in bases.values() if b != "EUR"})
    closes = historique_clotures(paires, debut) if paires else pd.DataFrame()
    for dev, (base, facteur) in bases.items():
        col = f"EUR{base}=X"
        if base == "EUR":
            out[dev] = None
        elif not closes.empty and col in closes and closes[col].notna().any():
            out[dev] = (1.0 / closes[col]) * facteur
        else:
            out[dev] = TAUX_REPLI.get(base, 1.0) * facteur
    idx = closes.index if not closes.empty else pd.date_range(debut, date.today(), freq="B")
    df = pd.DataFrame(index=idx)
    for dev, serie in out.items():
        df[dev] = 1.0 if serie is None else serie
    return df.sort_index()
