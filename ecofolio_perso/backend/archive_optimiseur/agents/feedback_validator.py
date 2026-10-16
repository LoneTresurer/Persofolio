"""
agents/feedback_validator.py
============================
Validation des données de feedback investisseur — 4 couches de protection.

COUCHE 1 — Validation statistique (bornes IC 99%)
COUCHE 2 — Cohérence interne des composantes saisies
COUCHE 3 — Benchmarking vs marché réel (Yahoo Finance)
COUCHE 4 — Extraction PDF si fourni (pdfplumber)

Chaque couche retourne des flags :
  severity = "error"   → rejet du feedback (données impossibles)
  severity = "warning" → alerte, demander confirmation
  severity = "info"    → explication pédagogique
"""

import logging
import re
from typing import Optional, List, Dict
import numpy as np

logger = logging.getLogger("feedback_validator")


# ── Couche 1 : Validation statistique ────────────────────────────────────────

def valider_statistique(
    rendement_declare_pct: float,
    rendement_predit_pct:  float,
    volatilite_pct:        float,
    duree_mois:            int,
    montant_initial:       float,
    montant_final:         float,
    dividendes:            float,
    frais:                 float,
) -> List[dict]:
    """
    Vérifie que le rendement déclaré est statistiquement plausible.
    Utilise les bornes IC 99% du portefeuille (± 3σ annualisés).
    """
    flags = []
    annees = max(duree_mois / 12, 0.08)

    # ── Cohérence du calcul du rendement ─────────────────────────────────────
    if montant_initial > 0:
        valeur_finale_nette = montant_final + dividendes - frais
        rendement_calcule   = (valeur_finale_nette / montant_initial - 1) * 100
        ecart_calcul        = abs(rendement_calcule - rendement_declare_pct)

        if ecart_calcul > 2.0:
            flags.append({
                "code":     "INCOHERENCE_CALCUL",
                "severity": "warning",
                "message": (
                    f"Le rendement déclaré ({rendement_declare_pct:.2f}%) ne correspond pas "
                    f"au calcul depuis les montants ({rendement_calcule:.2f}%). "
                    f"Écart : {ecart_calcul:.2f}%. "
                    f"Vérifiez que dividendes et frais sont bien inclus."
                ),
                "valeur_calculee": round(rendement_calcule, 2),
            })

    # ── Bornes statistiques IC 99% ───────────────────────────────────────────
    sigma_horizon   = volatilite_pct * np.sqrt(annees)
    borne_max_99pct = rendement_predit_pct * annees + 3 * sigma_horizon
    borne_min_99pct = rendement_predit_pct * annees - 3 * sigma_horizon

    if rendement_declare_pct > borne_max_99pct:
        flags.append({
            "code":     "RENDEMENT_TROP_ELEVE",
            "severity": "warning",
            "message": (
                f"Rendement déclaré ({rendement_declare_pct:.1f}%) supérieur "
                f"à la borne IC 99% ({borne_max_99pct:.1f}%). "
                f"Cela est statistiquement très rare — vérifiez le calcul. "
                f"Les dividendes sont-ils comptés en double ?"
            ),
            "borne_max": round(borne_max_99pct, 2),
        })

    if rendement_declare_pct < borne_min_99pct:
        flags.append({
            "code":     "RENDEMENT_TROP_BAS",
            "severity": "info",
            "message": (
                f"Rendement déclaré ({rendement_declare_pct:.1f}%) inférieur "
                f"à la borne basse IC 99% ({borne_min_99pct:.1f}%). "
                f"Des retraits partiels ou des frais élevés peuvent expliquer cela. "
                f"Le portefeuille a-t-il été modifié depuis la recommandation ?"
            ),
            "borne_min": round(borne_min_99pct, 2),
        })

    # ── Absurdités absolues ───────────────────────────────────────────────────
    if rendement_declare_pct > 200:
        flags.append({
            "code":     "VALEUR_IMPOSSIBLE",
            "severity": "error",
            "message":  "Un rendement supérieur à 200% est impossible pour ce type de portefeuille.",
        })

    if montant_final < 0:
        flags.append({
            "code":     "MONTANT_NEGATIF",
            "severity": "error",
            "message":  "La valeur finale du portefeuille ne peut pas être négative.",
        })

    if duree_mois < 1 or duree_mois > 360:
        flags.append({
            "code":     "DUREE_INVALIDE",
            "severity": "error",
            "message":  "La durée doit être comprise entre 1 mois et 30 ans.",
        })

    return flags


# ── Couche 2 : Cohérence interne ─────────────────────────────────────────────

def valider_coherence_interne(
    montant_initial: float,
    montant_final:   float,
    dividendes:      float,
    frais:           float,
    duree_mois:      int,
    rendement_predit_annuel_pct: float,
) -> List[dict]:
    """Vérifie la cohérence des composantes entre elles."""
    flags = []

    # Frais excessifs (> 5% du montant initial)
    if frais > montant_initial * 0.05:
        flags.append({
            "code":     "FRAIS_ELEVES",
            "severity": "warning",
            "message": (
                f"Les frais déclarés ({frais:.2f}€) représentent "
                f"{frais/montant_initial*100:.1f}% du capital initial. "
                f"Confirmez ce montant — cela semble élevé pour un portefeuille d'actions ESG."
            ),
        })

    # Dividendes impossibles (> 15%/an du capital)
    div_yield_implicite = (dividendes / montant_initial) / (duree_mois / 12)
    if div_yield_implicite > 0.15:
        flags.append({
            "code":     "DIVIDENDES_SUSPECTS",
            "severity": "warning",
            "message": (
                f"Les dividendes déclarés impliquent un rendement de "
                f"{div_yield_implicite*100:.1f}%/an, "
                f"ce qui est très élevé pour un portefeuille ESG. "
                f"Vérifiez que vous n'avez pas inclus des plus-values de cession."
            ),
        })

    # Montant final < montant initial mais rendement déclaré positif (incohérence)
    rendement_brut = (montant_final - montant_initial) / montant_initial * 100
    if rendement_brut < 0 and dividendes == 0:
        flags.append({
            "code":     "PERTE_SANS_DIVIDENDES",
            "severity": "info",
            "message": (
                f"La valeur du portefeuille a baissé de {abs(rendement_brut):.1f}%. "
                f"Si vous avez perçu des dividendes, pensez à les inclure "
                f"pour un rendement total correct."
            ),
        })

    return flags


# ── Couche 3 : Benchmarking marché réel ──────────────────────────────────────

async def benchmarker_vs_marche_reel(
    date_debut_str: str,
    duree_mois:     int,
    composition:    list,
    pays:           str = None,
) -> dict:
    """
    Reconstitue la performance théorique du portefeuille recommandé
    sur la période RÉELLE et télécharge la performance du marché.
    Retourne une comparaison structurée.
    """
    import yfinance as yf
    from datetime import datetime, timedelta

    try:
        date_debut = datetime.fromisoformat(date_debut_str)
        date_fin   = date_debut + timedelta(days=int(duree_mois * 30.4))
        start_str  = date_debut.strftime("%Y-%m-%d")
        end_str    = date_fin.strftime("%Y-%m-%d")
        today      = datetime.now().strftime("%Y-%m-%d")

        # Limiter à aujourd'hui si la période dépasse
        if end_str > today:
            end_str = today

        # ── Performance théorique du portefeuille recommandé ─────────────────
        indices_pays = {
            "FR": "^FCHI", "US": "^GSPC", "DE": "^GDAXI",
            "GB": "^FTSE", "JP": "^N225", "CA": "^GSPTSE",
            "CH": "^SSMI", "SE": "^OMX",  "AU": "^AXJO",
        }
        idx_ticker = indices_pays.get(pays, "^GSPC")

        perf_par_titre = []
        perf_theorique_ponderee = 0.0
        poids_total = 0.0

        for stock in composition[:20]:
            ticker = stock.get("ticker", "")
            poids  = stock.get("poids_pct", 0) / 100
            if not ticker or poids <= 0:
                continue
            try:
                hist = yf.Ticker(ticker).history(start=start_str, end=end_str)
                if len(hist) >= 5:
                    perf = float(hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1)
                    perf_par_titre.append({
                        "ticker":     ticker,
                        "poids_pct":  round(poids * 100, 2),
                        "perf_pct":   round(perf * 100, 2),
                        "contribution_pct": round(perf * poids * 100, 3),
                    })
                    perf_theorique_ponderee += perf * poids
                    poids_total += poids
            except Exception as e:
                logger.debug(f"Benchmark {ticker}: {e}")
                continue

        if poids_total > 0:
            perf_theorique_ponderee /= poids_total

        # ── Performance du marché (indice de référence) ───────────────────────
        perf_marche = None
        try:
            hist_m = yf.Ticker(idx_ticker).history(start=start_str, end=end_str)
            if len(hist_m) >= 5:
                perf_marche = float(hist_m["Close"].iloc[-1] / hist_m["Close"].iloc[0] - 1) * 100
        except Exception as e:
            logger.warning(f"Benchmark marché {idx_ticker}: {e}")

        # Top contributeurs et détracteurs
        perf_par_titre.sort(key=lambda x: x["contribution_pct"], reverse=True)

        return {
            "perf_theorique_pct":  round(perf_theorique_ponderee * 100, 2),
            "perf_marche_pct":     round(perf_marche, 2) if perf_marche is not None else None,
            "indice_reference":    idx_ticker,
            "periode":             f"{start_str} → {end_str}",
            "n_titres_verifies":   len(perf_par_titre),
            "top_contributeurs":   perf_par_titre[:5],
            "pires_detracteurs":   sorted(perf_par_titre, key=lambda x: x["contribution_pct"])[:3],
            "note": (
                "Performance théorique si le portefeuille recommandé "
                "avait été suivi exactement (sans frais ni glissement)."
            ),
        }

    except Exception as e:
        logger.error(f"Benchmarking error: {e}")
        return {
            "perf_theorique_pct": None,
            "perf_marche_pct":    None,
            "erreur":             str(e),
            "note":               "Benchmarking non disponible pour cette période.",
        }


# ── Couche 4 : Extraction PDF courtier ───────────────────────────────────────

def extraire_donnees_pdf(pdf_bytes: bytes) -> dict:
    """
    Extrait les données clés d'un relevé de courtage PDF.
    Supporte les formats courants (eToro, DEGIRO, Boursorama, Interactive Brokers).
    Retourne les données extraites et un niveau de fiabilité.
    """
    try:
        import pdfplumber
        import io

        texte_complet = ""
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    texte_complet += t + "\n"

        if len(texte_complet) < 50:
            return {
                "succes": False,
                "erreur": "PDF vide ou scanné sans texte extractible.",
                "fiabilite": "nulle",
            }

        donnees = {}

        # ── Valeur totale du portefeuille ─────────────────────────────────────
        patterns_valeur = [
            r"[Vv]aleur\s+(?:totale|du\s+portefeuille|nette)[^\d]*([\d\s\.,]+)\s*€",
            r"[Pp]ortfolio\s+[Vv]alue[^\d]*([\d\s\.,]+)\s*(?:EUR|€)",
            r"[Tt]otal\s+[Aa]ssets[^\d]*([\d\s\.,]+)",
            r"[Ss]olde\s+[Ee]stimatif[^\d]*([\d\s\.,]+)\s*€",
            r"NET\s+ASSET\s+VALUE[^\d]*([\d\s\.,]+)",
            r"Valorisation\s+globale[^\d]*([\d\s\.,]+)\s*€",
        ]
        for pattern in patterns_valeur:
            m = re.search(pattern, texte_complet, re.IGNORECASE)
            if m:
                valeur_str = m.group(1).replace(" ", "").replace(",", ".")
                try:
                    v = float(valeur_str.replace(".", "", valeur_str.count(".")-1)) if valeur_str.count(".") > 1 else float(valeur_str)
                    if 10 < v < 100_000_000:
                        donnees["valeur_portefeuille"] = round(v, 2)
                        break
                except Exception:
                    continue

        # ── Plus/moins-values latentes ────────────────────────────────────────
        patterns_pnl = [
            r"[Pp]lus.value\s+(?:latente)?[^\d\-]*([\-\d\s\.,]+)\s*€",
            r"[Uu]nrealized\s+[Pp]&[Ll][^\d\-]*([\-\d\s\.,]+)",
            r"[Gg]ain[s]?\s+(?:latents?|non\s+réalisés)[^\d\-]*([\-\d\s\.,]+)\s*€",
        ]
        for pattern in patterns_pnl:
            m = re.search(pattern, texte_complet, re.IGNORECASE)
            if m:
                try:
                    donnees["plus_value_latente"] = float(
                        m.group(1).strip().replace(" ", "").replace(",", ".")
                    )
                    break
                except Exception:
                    continue

        # ── Date du relevé ────────────────────────────────────────────────────
        patterns_date = [
            r"[Aa]u\s+(\d{2}[/\-\.]\d{2}[/\-\.]\d{4})",
            r"[Ss]tatement\s+[Dd]ate[:\s]+(\d{2}[/\-\.]\d{2}[/\-\.]\d{4})",
            r"[Dd]ate\s+(?:du\s+)?[Rr]elevé[:\s]+(\d{2}[/\-\.]\d{2}[/\-\.]\d{4})",
            r"(\d{2}\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|"
            r"septembre|octobre|novembre|décembre)\s+\d{4})",
        ]
        for pattern in patterns_date:
            m = re.search(pattern, texte_complet, re.IGNORECASE)
            if m:
                donnees["date_releve"] = m.group(1).strip()
                break

        # ── Dividendes perçus ─────────────────────────────────────────────────
        patterns_div = [
            r"[Dd]ividendes?\s+(?:perçus?|reçus?|nets?)[^\d]*([\d\s\.,]+)\s*€",
            r"[Dd]ividend\s+[Ii]ncome[^\d]*([\d\s\.,]+)",
        ]
        for pattern in patterns_div:
            m = re.search(pattern, texte_complet, re.IGNORECASE)
            if m:
                try:
                    donnees["dividendes_percus"] = float(
                        m.group(1).strip().replace(" ", "").replace(",", ".")
                    )
                    break
                except Exception:
                    continue

        # ── Courtier détecté ─────────────────────────────────────────────────
        courtiers = {
            "eToro":               r"eToro",
            "DEGIRO":              r"DEGIRO",
            "Boursorama":          r"Boursorama",
            "Interactive Brokers": r"Interactive\s+Brokers|IBKR",
            "Saxo Bank":           r"Saxo\s+Bank",
            "BNP Paribas":         r"BNP\s+Paribas",
            "Crédit Agricole":     r"Crédit\s+Agricole",
            "Fortuneo":            r"Fortuneo",
            "Binck":               r"Binck",
            "Bourse Direct":       r"Bourse\s+Direct",
        }
        courtier_detecte = "Inconnu"
        for nom, pattern in courtiers.items():
            if re.search(pattern, texte_complet, re.IGNORECASE):
                courtier_detecte = nom
                break

        # ── Score de fiabilité ────────────────────────────────────────────────
        n_champs = len(donnees)
        fiabilite = "haute" if n_champs >= 3 else "partielle" if n_champs >= 1 else "faible"

        return {
            "succes":           True,
            "fiabilite":        fiabilite,
            "courtier_detecte": courtier_detecte,
            "n_champs_extraits":n_champs,
            "donnees":          donnees,
            "note": (
                f"{n_champs} champ(s) extrait(s) automatiquement depuis le PDF "
                f"({courtier_detecte}). Vérifiez les valeurs avant validation."
            ),
        }

    except ImportError:
        return {
            "succes":    False,
            "erreur":    "Module pdfplumber non installé. Installez-le avec pip install pdfplumber.",
            "fiabilite": "nulle",
        }
    except Exception as e:
        logger.error(f"Extraction PDF: {e}")
        return {
            "succes":    False,
            "erreur":    str(e),
            "fiabilite": "nulle",
        }


# ── Orchestrateur : toutes les couches ───────────────────────────────────────

async def valider_feedback_complet(
    session:         dict,
    montant_initial: float,
    montant_final:   float,
    dividendes:      float,
    frais:           float,
    duree_mois:      int,
    rendement_reel:  float,
) -> dict:
    """
    Lance toutes les couches de validation et retourne un résultat consolidé.
    """
    annees = max(duree_mois / 12, 0.08)
    rendement_ann = ((1 + rendement_reel / 100) ** (1 / annees) - 1) * 100

    all_flags = []

    # Couche 1
    flags_stat = valider_statistique(
        rendement_reel,
        session.get("rendement_predit", 8.0),
        session.get("volatilite", 15.0),
        duree_mois,
        montant_initial,
        montant_final,
        dividendes,
        frais,
    )
    all_flags.extend(flags_stat)

    # Couche 2
    flags_coh = valider_coherence_interne(
        montant_initial, montant_final,
        dividendes, frais, duree_mois,
        session.get("rendement_predit", 8.0),
    )
    all_flags.extend(flags_coh)

    # Couche 3 — benchmarking asynchrone
    benchmark = await benchmarker_vs_marche_reel(
        session.get("created_at", "")[:10],
        duree_mois,
        session.get("composition", []),
        session.get("pays"),
    )

    # Déterminer si les données sont acceptables
    errors = [f for f in all_flags if f["severity"] == "error"]
    warnings = [f for f in all_flags if f["severity"] == "warning"]
    validation_ok = len(errors) == 0

    return {
        "validation_ok":      validation_ok,
        "flags":              all_flags,
        "n_errors":           len(errors),
        "n_warnings":         len(warnings),
        "rendement_annualise": round(rendement_ann, 2),
        "benchmark":          benchmark,
        "resume_validation": (
            "✅ Données validées" if validation_ok
            else f"⚠️ {len(errors)} erreur(s) et {len(warnings)} avertissement(s) — vérifiez les données"
        ),
    }
