"""
agents/market_data.py
=====================
Accès aux données de marché (Yahoo Finance via yfinance) pour le suivi de portefeuille.

Convention de change : `taux_eur(devise)` renvoie le nombre d'EUR pour 1 unité de devise.
  ex. USD → 0.92  signifie 1 $ = 0,92 €
Cas particulier : les actions de Londres cotent souvent en pence (« GBp ») → 1 GBp = 0,01 GBP.
"""

import logging
import math
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

# Pays (tel que renvoyé par Yahoo) → zone géographique (mêmes zones que Boursorama)
ZONES = {
    "France": "Europe", "Germany": "Europe", "Netherlands": "Europe", "Belgium": "Europe",
    "Italy": "Europe", "Spain": "Europe", "Portugal": "Europe", "Ireland": "Europe",
    "Luxembourg": "Europe", "Austria": "Europe", "Finland": "Europe", "Switzerland": "Europe",
    "United Kingdom": "Europe", "Sweden": "Europe", "Denmark": "Europe", "Norway": "Europe",
    "Poland": "Europe", "Greece": "Europe",
    "United States": "Amérique du Nord", "Canada": "Amérique du Nord", "Mexico": "Amérique du Nord",
    "Japan": "Asie-Océanie", "China": "Asie-Océanie", "Hong Kong": "Asie-Océanie",
    "Taiwan": "Asie-Océanie", "South Korea": "Asie-Océanie", "India": "Asie-Océanie",
    "Singapore": "Asie-Océanie", "Australia": "Asie-Océanie", "New Zealand": "Asie-Océanie",
    "Brazil": "Amérique du Sud", "Chile": "Amérique du Sud", "Argentina": "Amérique du Sud",
    "South Africa": "Afrique", "Tunisia": "Afrique", "Morocco": "Afrique", "Egypt": "Afrique",
    "Israel": "Moyen-Orient", "Saudi Arabia": "Moyen-Orient", "United Arab Emirates": "Moyen-Orient",
}
ZONES_ORDRE = ["Amérique du Nord", "Europe", "Asie-Océanie", "Afrique", "Amérique du Sud",
               "Moyen-Orient", "Autre"]

# Secteurs Yahoo (actions et ETF) → libellés français
SECTEURS_FR = {
    "Technology": "Technologie", "technology": "Technologie",
    "Financial Services": "Sociétés financières", "financial_services": "Sociétés financières",
    "Healthcare": "Santé", "healthcare": "Santé",
    "Consumer Cyclical": "Biens de consommation", "consumer_cyclical": "Biens de consommation",
    "Consumer Defensive": "Consommation de base", "consumer_defensive": "Consommation de base",
    "Industrials": "Industrie", "industrials": "Industrie",
    "Communication Services": "Télécommunications", "communication_services": "Télécommunications",
    "Energy": "Énergie", "energy": "Énergie",
    "Basic Materials": "Matériaux", "basic_materials": "Matériaux",
    "Utilities": "Services publics", "utilities": "Services publics",
    "Real Estate": "Immobilier", "realestate": "Immobilier",
}

# Composition approximative des grands indices répliqués par les ETF, utilisée pour
# « ouvrir » un ETF quand Yahoo ne fournit pas le détail (Yahoo ne donne jamais les zones).
# Ordres de grandeur publics des indices ; à mettre à jour de temps en temps.
_S = lambda **k: k
COMPOSITION_INDICES = [
    (("NASDAQ 100", "NASDAQ-100", "NASDAQ100"),
     {"Amérique du Nord": 97, "Europe": 2, "Autre": 1},
     _S(Technologie=50, Télécommunications=16, **{"Biens de consommation": 14, "Santé": 6,
        "Consommation de base": 5, "Industrie": 5, "Services publics": 1.5, "Énergie": 0.5,
        "Matériaux": 1, "Sociétés financières": 1})),
    (("S&P 500", "S&P500", "SP500", "S&P 500®"),
     {"Amérique du Nord": 100},
     _S(Technologie=33, Télécommunications=10, **{"Sociétés financières": 13, "Biens de consommation": 10.5,
        "Santé": 9, "Industrie": 8.5, "Consommation de base": 5, "Énergie": 3,
        "Services publics": 2.5, "Immobilier": 2, "Matériaux": 2})),
    (("EMERGING", "EMERGENTS", "MSCI EM"),
     {"Asie-Océanie": 78, "Amérique du Sud": 7, "Moyen-Orient": 8, "Afrique": 4, "Europe": 3},
     _S(Technologie=24, Télécommunications=10, **{"Sociétés financières": 23, "Biens de consommation": 13,
        "Industrie": 7, "Matériaux": 6, "Consommation de base": 5, "Énergie": 5, "Santé": 4,
        "Services publics": 2.5, "Immobilier": 1.5})),
    (("MSCI WORLD", "MONDE", "WORLD", "ACWI", "ALL COUNTRY"),
     {"Amérique du Nord": 75, "Europe": 15, "Asie-Océanie": 9, "Moyen-Orient": 0.5, "Autre": 0.5},
     _S(Technologie=27, Télécommunications=8.5, Industrie=11, Santé=9.5, Énergie=3.5, Matériaux=3,
        Immobilier=2, **{"Sociétés financières": 16, "Biens de consommation": 10,
        "Consommation de base": 5.5, "Services publics": 2.7})),
    (("STOXX EUROPE 600", "STOXX 600", "EUROPE 600", "MSCI EUROPE", "STOXX EUROPE"),
     {"Europe": 100},
     _S(Industrie=18, Santé=13, Technologie=7, Matériaux=5.5, Énergie=4.5, Télécommunications=3.5,
        Immobilier=1.5, **{"Sociétés financières": 23, "Biens de consommation": 9,
        "Consommation de base": 8, "Services publics": 4.5})),
    (("EURO STOXX 50", "EUROSTOXX 50", "STOXX 50", "EURO STOXX"),
     {"Europe": 100},
     _S(Industrie=17, Technologie=16, Santé=5, Énergie=5, Matériaux=5, Télécommunications=4,
        **{"Sociétés financières": 21, "Biens de consommation": 16, "Consommation de base": 6,
        "Services publics": 5})),
    (("CAC 40", "CAC40"),
     {"Europe": 100},
     _S(Industrie=20, Santé=9, Technologie=7, Matériaux=7, Énergie=6, Télécommunications=3,
        Immobilier=1, **{"Biens de consommation": 22, "Sociétés financières": 12,
        "Consommation de base": 9, "Services publics": 3})),
    (("JAPAN", "JAPON", "TOPIX", "NIKKEI"), {"Asie-Océanie": 100}, None),
]

# Noms de pays en français (Yahoo les renvoie en anglais)
PAYS_FR = {
    "France": "France", "Germany": "Allemagne", "Netherlands": "Pays-Bas", "Belgium": "Belgique",
    "Italy": "Italie", "Spain": "Espagne", "Switzerland": "Suisse", "United Kingdom": "Royaume-Uni",
    "Sweden": "Suède", "Denmark": "Danemark", "Norway": "Norvège", "Ireland": "Irlande",
    "Finland": "Finlande", "Luxembourg": "Luxembourg", "Portugal": "Portugal", "Austria": "Autriche",
    "Poland": "Pologne", "Greece": "Grèce", "United States": "États-Unis", "Canada": "Canada",
    "Mexico": "Mexique", "Japan": "Japon", "China": "Chine", "Hong Kong": "Hong Kong",
    "Taiwan": "Taïwan", "South Korea": "Corée du Sud", "India": "Inde", "Singapore": "Singapour",
    "Australia": "Australie", "New Zealand": "Nouvelle-Zélande", "Brazil": "Brésil",
    "Chile": "Chili", "Argentina": "Argentine", "South Africa": "Afrique du Sud",
    "Israel": "Israël", "Tunisia": "Tunisie", "Morocco": "Maroc", "Egypt": "Égypte",
    "Saudi Arabia": "Arabie saoudite", "United Arab Emirates": "Émirats arabes unis",
}

# Indices de comparaison.
#
# Un portefeuille encaisse ses dividendes : pour que la comparaison ait un sens, l'indice
# doit lui aussi être mesuré dividendes réinvestis (« total return »). Les indices de prix
# publiés (^GSPC, ^FCHI) les excluent et sous-estiment le marché d'environ 2 % par an ; ils
# ne servent donc que de dernier recours, et de repère pour expliquer l'écart avec la presse.
#
# Deux jeux de sources, selon la devise dans laquelle on veut lire l'indice :
#
#   « euros_dividendes » — ce que vit un épargnant français. Un ETF capitalisant coté en
#       euros, ou converti en euros : la performance inclut donc l'effet de change, qu'il
#       soit favorable ou non. C'est la seule version comparable à un portefeuille en euros.
#
#   « devise_origine » — l'indice dans sa propre monnaie, dividendes réinvestis quand même.
#       Pour le S&P 500 c'est le dollar, et l'indice officiel total return est ^SP500TR.
#       Utile pour recouper une donnée, mais la comparaison avec un portefeuille en euros
#       ignore alors le change.
#
# Chaque liste est une chaîne de repli : si Yahoo ne répond pas pour la première source (ou
# ne couvre pas l'intraday), la suivante est essayée. La source réellement retenue et la
# nature du calcul sont toujours affichées dans l'application. Jamais de données inventées.
#   (ticker, devise de cotation, dividendes réinvestis)
INDICES = {
    "MSCI_WORLD": {
        "libelle": "MSCI World",
        "sources": {
            "euros_dividendes": [("IWDA.AS", "EUR", True), ("SWDA.L", "GBp", True),
                                 ("CW8.PA", "EUR", True), ("URTH", "USD", False)],
            "devise_origine":   [("IWDA.L", "USD", True), ("SWDA.L", "GBp", True),
                                 ("URTH", "USD", False)],
        },
        "prix": ("URTH", "USD"),
    },
    "SP500": {
        "libelle": "S&P 500",
        "sources": {
            "euros_dividendes": [("SXR8.DE", "EUR", True), ("CSPX.AS", "USD", True),
                                 ("ESE.PA", "EUR", True), ("^GSPC", "USD", False)],
            "devise_origine":   [("^SP500TR", "USD", True), ("CSPX.AS", "USD", True),
                                 ("^GSPC", "USD", False)],
        },
        "prix": ("^GSPC", "USD"),
    },
    # Le CAC 40 a été retiré des indices de référence : la seule série accessible
    # publiquement (^FCHI) est un indice de PRIX, dividendes exclus. Comparer un
    # portefeuille qui encaisse ses dividendes à un indice qui les ignore fausse la
    # comparaison d'environ 3 points par an, en votre faveur. Mieux vaut pas d'indice
    # qu'un indice biaisé.
}

_cache_taux: Dict[str, tuple] = {}     # devise -> (taux, horodatage)


def zone_de(pays: Optional[str]) -> str:
    return ZONES.get(pays or "", "Autre")


def secteur_fr(secteur: Optional[str]) -> str:
    return SECTEURS_FR.get(secteur or "", secteur or "Autre")


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
            "secteur": info.get("sector"),
            "pays": info.get("country"),
            "type_actif": info.get("quoteType") or "EQUITY",
            "prix": float(prix),
        }
    except Exception as e:
        logger.warning(f"fiche {ticker}: {e}")
        return None


# ── Cours actuels ────────────────────────────────────────────────────────────

def _valide(x) -> Optional[float]:
    """Un cours exploitable : un nombre fini et strictement positif, ou rien."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def _cours_un(ticker: str) -> Optional[dict]:
    """Dernier cours et clôture de la veille, pour la variation du jour.

    Les deux valeurs viennent de la MÊME source, la cotation publiée par Yahoo :
    `regularMarketPrice` et `regularMarketPreviousClose`. C'est ce couple, et lui
    seul, qui donne le pourcentage affiché sur la page du titre.

    Deux pièges écartés ici, rencontrés l'un après l'autre sur ESE.PA :

    1. `fast_info.previous_close` n'est PAS la clôture officielle de la veille.
       Le 16/10/2026 il valait 34,991 quand la vraie clôture était 34,957, soit
       une variation annoncée de −0,11 % au lieu de −0,02 %.
    2. L'historique journalier ne peut pas servir de référence : sa dernière
       ligne peut porter la clôture de la veille sous la date du jour, et une
       séance peut manquer. Prendre son avant-dernière ligne revient alors à
       comparer le jour en cours à l'avant-veille — c'est le −1,02 % constaté.

    L'historique ne sert donc plus que de secours, et avec une garde : la séance
    précédente est choisie par sa date, et doit être la veille ouvrée.
    """
    try:
        infos = yf.Ticker(ticker).info or {}
        prix = _valide(infos.get("regularMarketPrice"))
        veille = _valide(infos.get("regularMarketPreviousClose")) or _valide(infos.get("previousClose"))
        if prix is not None and veille is not None:
            return {"prix": prix, "cloture_veille": veille,
                    "date_cours": date.today().isoformat()}
    except Exception as e:
        logger.info(f"cours {ticker} : cotation indisponible, repli sur l'historique ({e})")

    try:
        closes = yf.Ticker(ticker).history(period="7d", auto_adjust=False)["Close"].dropna()
        if closes.empty:
            return None
        prix = _valide(closes.iloc[-1])
        if prix is None:
            return None
        jour = pd.Timestamp(closes.index[-1]).tz_localize(None).normalize()
        # Séance précédente réellement cotée, choisie par sa DATE et non par sa
        # position : une ligne manquante ne peut donc plus décaler la référence.
        avant = closes[pd.DatetimeIndex(closes.index).tz_localize(None).normalize() < jour]
        veille = _valide(avant.iloc[-1]) if len(avant) else None
        if veille is not None:
            precedent = pd.Timestamp(avant.index[-1]).tz_localize(None).normalize()
            # Une seule séance d'écart, week-ends exclus : au-delà, c'est un jour férié
            # ou une ligne manquante, et on ne sait pas lequel. Mieux vaut n'annoncer
            # aucune variation du jour qu'une variation fausse.
            if len(pd.bdate_range(precedent, jour)) - 1 != 1:
                logger.info(f"cours {ticker} : séance précédant le "
                            f"{jour.date()} introuvable (dernière : {precedent.date()}), "
                            f"variation du jour non calculée")
                veille = None
        if veille is None:
            veille = prix
        return {"prix": prix, "cloture_veille": veille, "date_cours": jour.strftime("%Y-%m-%d")}
    except Exception as e:
        logger.warning(f"cours {ticker}: {e}")
        return None


_cache_cours: Dict[str, tuple] = {}     # ticker -> (cotation, horodatage)
DUREE_CACHE_COURS = 60                  # secondes


def _cours_cache(ticker: str) -> Optional[dict]:
    """Cotation d'un titre, relue au plus une fois par minute.

    La cotation complète est une requête lourde ; l'écran, lui, se rafraîchit
    souvent. Une minute de cache suffit à ne rien manquer d'un cours de Bourse
    sans interroger Yahoo à chaque clic."""
    garde = _cache_cours.get(ticker)
    if garde and time.time() - garde[1] < DUREE_CACHE_COURS:
        return garde[0]
    valeur = _cours_un(ticker)
    if valeur is not None:
        _cache_cours[ticker] = (valeur, time.time())
    return valeur


def cours_actuels(tickers: List[str]) -> Dict[str, dict]:
    """Dernier cours + clôture de la veille pour chaque ticker (téléchargement parallèle)."""
    if not tickers:
        return {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = dict(zip(tickers, ex.map(_cours_cache, tickers)))
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


def historique_taux(devises: List[str], debut: str, index=None) -> pd.DataFrame:
    """
    EUR pour 1 unité de chaque devise, en journalier depuis `debut`.
    Si `index` est fourni, les taux y sont alignés par report de la dernière valeur connue.
    """
    bases = {d: _devise_base(d) for d in set(devises)}
    paires = sorted({f"EUR{b}=X" for b, _ in bases.values() if b != "EUR"})
    closes = historique_clotures(paires, debut) if paires else pd.DataFrame()
    idx = pd.DatetimeIndex(index) if index is not None and len(index) else (
        closes.index if not closes.empty else pd.date_range(debut, date.today(), freq="B"))
    df = pd.DataFrame(index=idx)
    for dev, (base, facteur) in bases.items():
        col = f"EUR{base}=X"
        if base == "EUR":
            df[dev] = 1.0 * facteur
        elif not closes.empty and col in closes and closes[col].notna().any():
            df[dev] = aligner_sur(1.0 / closes[col].dropna(), idx) * facteur
        else:
            logger.warning(f"Taux {base} indisponible sur Yahoo — valeur de repli utilisée.")
            df[dev] = TAUX_REPLI.get(base, 1.0) * facteur
    return df.sort_index()


# ── Transparence des ETF (répartition secteur / zone) ──────────────────────

_cache_compo: Dict[str, tuple] = {}


def _normaliser(d: dict) -> dict:
    tot = sum(v for v in d.values() if v and v > 0)
    return {k: v / tot for k, v in d.items() if v and v > 0} if tot > 0 else {}


def composition_etf(ticker: str, nom: str) -> dict:
    """
    Ventilation d'un ETF : {"secteurs": {libellé: poids}, "zones": {zone: poids}, "source": ...}
    Secteurs : Yahoo Finance si disponible, sinon composition de l'indice répliqué (d'après le nom).
    Zones : toujours d'après l'indice répliqué (Yahoo ne publie pas la répartition géographique).
    """
    c = _cache_compo.get(ticker)
    if c and time.time() - c[1] < 86400:
        return c[0]
    nom_maj = (nom or "").upper()
    ref = next((r for r in COMPOSITION_INDICES if any(k in nom_maj for k in r[0])), None)
    secteurs, source = {}, "indice"
    try:
        sw = yf.Ticker(ticker).funds_data.sector_weightings or {}
        secteurs = _normaliser({SECTEURS_FR.get(k, k): float(v) for k, v in sw.items()})
        if secteurs:
            source = "yahoo"
    except Exception as e:
        logger.info(f"secteurs ETF {ticker} indisponibles sur Yahoo : {e}")
    if not secteurs and ref and ref[2]:
        secteurs = _normaliser(ref[2])
    zones = _normaliser(ref[1]) if ref else {}
    res = {"secteurs": secteurs or {"Non ventilé (ETF)": 1.0},
           "zones": zones or {"Autre": 1.0},
           "indice": ref[0][0] if ref else None, "source": source if (secteurs or zones) else "aucune"}
    _cache_compo[ticker] = (res, time.time())
    return res


# ── Dividendes ─────────────────────────────────────────────────────────────

def dividendes_par_action(ticker: str, depuis: str) -> List[tuple]:
    """[(date de détachement 'YYYY-MM-DD', montant par action en devise du titre), …]"""
    try:
        s = yf.Ticker(ticker).get_dividends(period="max")
        if s is None or s.empty:
            return []
        s.index = pd.to_datetime(s.index).tz_localize(None)
        s = s[s.index >= pd.Timestamp(depuis)]
        return [(d.strftime("%Y-%m-%d"), float(v)) for d, v in s.items() if v and v > 0]
    except Exception as e:
        logger.warning(f"dividendes {ticker}: {e}")
        return []


def libelle_indice(code: str) -> str:
    return INDICES.get(code, INDICES["MSCI_WORLD"])["libelle"]


def _serie_source(ticker: str, devise: str, debut: str, periode: Optional[str],
                  convertir: bool = True) -> pd.Series:
    """
    Clôtures d'une source d'indice. Converties en EUR par défaut ; laissées dans leur
    devise d'origine si `convertir` est faux — c'est alors le chiffre que publie la
    presse, qui ignore l'effet de change réellement subi par un épargnant en euros.
    """
    if not convertir:
        if periode:
            return historique_titre(ticker, periode)
        cl = historique_clotures([ticker], debut)
        return cl[ticker].dropna() if (not cl.empty and ticker in cl) else pd.Series(dtype=float)
    if periode:                                   # échelles courtes : cotations horaires
        serie = historique_titre(ticker, periode)
    else:
        cl = historique_clotures([ticker], debut)
        serie = cl[ticker].dropna() if (not cl.empty and ticker in cl) else pd.Series(dtype=float)
    if serie.empty:
        return serie
    base, facteur = _devise_base(devise)
    if base != "EUR" or facteur != 1.0:
        debut_fx = serie.index[0].strftime("%Y-%m-%d")
        fx = historique_taux([devise], debut_fx, index=serie.index)
        serie = serie * fx[devise]
    return serie.dropna()


MODES_INDICE = ("euros_dividendes", "devise_origine")


def serie_indice(code: str, debut: str, periode: Optional[str] = None,
                 index=None, mode: str = "euros_dividendes") -> tuple:
    """
    Série de l'indice de comparaison, alignée sur `index` si fourni.

    Deux lectures possibles, et elles ne disent pas la même chose :

    • « euros_dividendes » : l'indice tel que le vit un épargnant français, mesuré par
      un ETF capitalisant coté en euros. C'est la seule version comparable à un
      portefeuille lui aussi libellé en euros et qui encaisse ses dividendes.

    • « devise_origine » : l'indice de prix dans sa monnaie, hors dividendes. C'est le
      chiffre des journaux, directement vérifiable, mais il ignore à la fois les
      dividendes et l'effet de change réellement subi.

    Essaie chaque source déclarée jusqu'à en trouver une exploitable : Yahoo ne publie
    pas toujours l'intraday, et certains tickers restent muets. Retourne
    (série, ticker retenu, dividendes réinvestis) ; série vide si aucune ne répond.
    """
    conf = INDICES.get(code, INDICES["MSCI_WORLD"])
    if mode not in MODES_INDICE:
        mode = "euros_dividendes"
    sources = conf["sources"][mode]
    for ticker, devise, dividendes in sources:
        serie = _serie_source(ticker, devise, debut, periode,
                              convertir=(mode != "devise_origine"))
        if len(serie) >= 2:
            if index is not None and len(index):
                serie = aligner_sur(serie, index).dropna()
            if len(serie) >= 2:
                devise_affichee = "EUR" if mode != "devise_origine" else _devise_base(devise)[0]
                return serie, ticker, dividendes, devise_affichee
        logger.info(f"Indice {code} : source {ticker} inexploitable, essai de la suivante.")
    logger.warning(f"Indice {code} : aucune source disponible sur cette période.")
    return pd.Series(dtype=float), None, None, None


def historique_indice(code: str, debut: str) -> pd.Series:
    """Compatibilité : série journalière de l'indice, en euros."""
    return serie_indice(code, debut)[0]


def splits_par_action(ticker: str, depuis: str) -> List[tuple]:
    """[(date 'YYYY-MM-DD', ratio)] — ratio 4 = 4 nouvelles actions pour 1 ancienne."""
    try:
        s = yf.Ticker(ticker).get_splits(period="max")
        if s is None or s.empty:
            return []
        s.index = pd.to_datetime(s.index).tz_localize(None)
        s = s[s.index >= pd.Timestamp(depuis)]
        return [(d.strftime("%Y-%m-%d"), float(v)) for d, v in s.items() if v and v > 0]
    except Exception as e:
        logger.warning(f"splits {ticker}: {e}")
        return []


# Périodes proposées dans l'appli → (période yfinance, intervalle réel des cotations)
PERIODES = {
    "5j":  ("5d",   "60m"),      # intraday : une cotation par heure
    "1m":  ("1mo",  "60m"),
    "3m":  ("3mo",  "1d"),
    "6m":  ("6mo",  "1d"),
    "1a":  ("1y",   "1d"),
    "5a":  ("5y",   "1wk"),
    "tout": ("max", "1d"),
}


def aligner_sur(donnees, index):
    """
    Aligne une série/DataFrame sur `index` en reprenant la dernière valeur connue
    (méthode « asof »). Indispensable quand les index ne coïncident pas exactement :
    un indice américain et un titre européen n'ont ni les mêmes heures de cotation
    ni les mêmes jours fériés. Un simple reindex().ffill() produirait des NaN partout,
    car reindex supprime les valeurs dont l'horodatage n'existe pas dans la cible.
    """
    if donnees is None or len(donnees) == 0 or len(index) == 0:
        return donnees
    d = donnees.sort_index()
    if getattr(d.index, "tz", None) is not None:
        d.index = d.index.tz_localize(None)
    cible = pd.DatetimeIndex(index)
    if getattr(cible, "tz", None) is not None:
        cible = cible.tz_localize(None)
    aligne = d.reindex(cible, method="ffill")          # dernière valeur connue avant
    return aligne.bfill()                              # puis début de série si besoin


def _nettoyer_index(df):
    idx = pd.to_datetime(df.index)
    try:
        idx = idx.tz_convert("Europe/Paris").tz_localize(None)
    except (TypeError, AttributeError):
        pass
    df.index = idx
    return df.sort_index()


def historique_titre(ticker: str, periode: str = "1a") -> pd.Series:
    """Cours d'un titre sur la période demandée, à l'intervalle réellement publié par Yahoo."""
    per, intervalle = PERIODES.get(periode, PERIODES["1a"])
    try:
        h = yf.Ticker(ticker).history(period=per, interval=intervalle, auto_adjust=False)
        if h.empty:
            return pd.Series(dtype=float)
        return _nettoyer_index(h)["Close"].dropna()
    except Exception as e:
        logger.warning(f"historique {ticker} ({periode}): {e}")
        return pd.Series(dtype=float)


def historique_intraday(tickers: List[str], periode: str = "5j") -> pd.DataFrame:
    """
    Cours horaires (données réelles Yahoo, pas d'interpolation) pour plusieurs titres.
    Yahoo ne publie l'intraday que sur les 2 dernières années environ.
    """
    per, intervalle = PERIODES.get(periode, PERIODES["5j"])
    if not tickers or intervalle == "1d":
        return pd.DataFrame()
    try:
        data = yf.download(tickers, period=per, interval=intervalle, auto_adjust=False,
                           progress=False, group_by="column", threads=True)
        if data is None or data.empty:
            return pd.DataFrame()
        closes = data["Close"]
        if isinstance(closes, pd.Series):
            closes = closes.to_frame(name=tickers[0])
        return _nettoyer_index(closes)
    except Exception as e:
        logger.warning(f"intraday {tickers} ({periode}): {e}")
        return pd.DataFrame()


def taux_intraday(devises: List[str], periode: str = "5j", index=None) -> pd.DataFrame:
    """EUR pour 1 unité de devise, en horaire, aligné sur `index` (asof)."""
    bases = {d: _devise_base(d) for d in set(devises)}
    paires = sorted({f"EUR{b}=X" for b, _ in bases.values() if b != "EUR"})
    closes = historique_intraday(paires, periode) if paires else pd.DataFrame()
    idx = pd.DatetimeIndex(index) if index is not None and len(index) else (
        closes.index if not closes.empty else pd.DatetimeIndex([]))
    df = pd.DataFrame(index=idx)
    for dev, (base, facteur) in bases.items():
        col = f"EUR{base}=X"
        if base == "EUR":
            df[dev] = 1.0 * facteur
        elif not closes.empty and col in closes and closes[col].notna().any():
            df[dev] = aligner_sur(1.0 / closes[col].dropna(), idx) * facteur
        else:
            df[dev] = TAUX_REPLI.get(base, 1.0) * facteur
    return df


# ── Résolution ISIN → ticker Yahoo (import d'un export courtier) ────────────

# Ordre de préférence des places de cotation pour un investisseur en euros :
# on privilégie la cotation européenne, en euros, la plus liquide.
PLACES_PRIORITAIRES = ["PAR", "AMS", "EBS", "MIL", "GER", "FRA", "STO", "CPH",
                       "LSE", "NYQ", "NMS", "NGM", "PCX", "TOR"]
DEVISES_PRIORITAIRES = ["EUR", "GBP", "CHF", "USD"]


def _score_cotation(q: dict) -> tuple:
    """Classe les cotations d'un même titre : Paris et l'euro d'abord."""
    place = (q.get("exchange") or "").upper()
    devise = (q.get("currency") or "").upper()
    i_place = PLACES_PRIORITAIRES.index(place) if place in PLACES_PRIORITAIRES else 99
    i_devise = DEVISES_PRIORITAIRES.index(devise) if devise in DEVISES_PRIORITAIRES else 99
    return (i_place, i_devise, -(q.get("score") or 0))


def rechercher_titre(requete: str, max_resultats: int = 8) -> List[dict]:
    """
    Cherche un titre sur Yahoo Finance par ISIN, ticker ou nom.
    Retourne les cotations trouvées, les plus pertinentes d'abord. Jamais de résultat
    inventé : si Yahoo ne connaît pas la requête, la liste revient vide.
    """
    try:
        res = yf.Search(requete, max_results=max_resultats, news_count=0,
                        lists_count=0, enable_fuzzy_query=False, raise_errors=False)
        quotes = res.quotes or []
    except Exception as e:
        logger.warning(f"Recherche Yahoo « {requete} » : {e}")
        return []
    sorties = []
    for q in quotes:
        symbole = q.get("symbol")
        if not symbole or (q.get("quoteType") or "").upper() in ("FUTURE", "OPTION"):
            continue
        sorties.append({
            "ticker": symbole,
            "nom": q.get("longname") or q.get("shortname") or symbole,
            "place": q.get("exchange"),
            "place_nom": q.get("exchDisp"),
            "devise": q.get("currency"),
            "type": q.get("quoteType"),
            "score": q.get("score"),
        })
    sorties.sort(key=_score_cotation)
    return sorties


def resoudre_isin(isin: str, nom: Optional[str] = None) -> dict:
    """
    Trouve le ticker Yahoo correspondant à un ISIN (recherche par ISIN, puis par nom).
    Retourne {"ticker", "nom", "devise", "place", "alternatives", "source"} ;
    ticker à None si Yahoo ne trouve rien — à l'utilisateur de le saisir.
    """
    for requete, origine in ((isin, "isin"), (nom, "nom")):
        if not requete:
            continue
        trouves = rechercher_titre(requete)
        if trouves:
            retenu = trouves[0]
            return {**retenu, "alternatives": trouves[1:6], "source": origine}
    return {"ticker": None, "nom": nom, "devise": None, "place": None,
            "alternatives": [], "source": None}
