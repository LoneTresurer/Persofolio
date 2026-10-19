"""
core/import_csv.py
==================
Lecture d'un fichier d'opérations ou de positions exporté par une banque.

Le problème : chaque établissement a son format. Les intitulés de colonnes changent,
les dates s'écrivent dans les deux sens, les nombres mélangent virgules et points, et
le vocabulaire des opérations n'a rien de standard — « ACHAT COMPTANT » chez l'un,
« SOUSCRIPTION » chez l'autre, « VIR SEPA RECU » pour un simple versement.

Le parti pris : ne rien supposer, et surtout ne rien deviner.

1. Le séparateur, l'encodage et la ligne d'en-tête sont détectés, pas présumés.
2. Les colonnes sont reconnues d'abord par leur CONTENU, ensuite seulement par leur
   intitulé. Une colonne dont les valeurs sont des ISIN est la colonne ISIN, qu'elle
   s'appelle « Code Valeur », « Code ISIN » ou « Instrument ».
3. Le format des dates et des nombres est tranché sur la colonne entière, pas valeur
   par valeur : si une seule ligne porte un quantième supérieur à 12, l'ambiguïté
   jour/mois est levée pour tout le fichier.
4. Les ordres non exécutés (annulés, expirés, en attente) sont écartés et signalés.
   C'est le piège d'un carnet d'ordres : un ordre annulé n'est pas une opération.
5. Un libellé d'opération inconnu n'est jamais interprété au hasard : la ligne est
   présentée « à classer », l'utilisateur tranche une fois, et le choix est retenu.

Rien n'entre dans le portefeuille sans passer par l'écran de validation.
"""

import csv
import io
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

# ── Intitulés rencontrés chez les courtiers, par rôle ────────────────────────
# Servent d'indice secondaire : le contenu prime quand il est décisif.
COLONNES = {
    "isin":       ["isin", "code isin", "codeisin", "code valeur", "code"],
    "nom":        ["libelle valeur", "libelle", "name", "nom", "valeur", "designation",
                   "produit", "instrument", "titre", "product", "support"],
    "ticker":     ["ticker", "symbole", "symbol", "mnemo", "mnemonique"],
    "quantite":   ["quantite", "quantity", "qte", "nombre", "nb titres", "shares",
                   "position", "quantite detenue", "quantite montant", "nombre de parts"],
    "prix":       ["lim cours execute", "cours execute", "prix execute", "cours",
                   "prix unitaire", "prix de revient", "pru", "buyingprice",
                   "prix d achat", "prix moyen", "average price", "cost price",
                   "lastprice", "dernier cours", "last", "cours de la part"],
    "montant":    ["montant brut", "montant net", "montant de l operation", "montant",
                   "amount", "valorisation", "valeur de marche", "market value", "brut", "net"],
    "devise":     ["devise cours", "devise", "currency", "monnaie"],
    "sens":       ["sens", "operation", "type d operation", "nature", "nature de l operation",
                   "libelle operation", "type de mouvement", "mouvement", "side",
                   "direction", "type"],
    "statut":     ["statut", "etat", "status", "etat de l ordre"],
    "frais":      ["frais de courtage", "frais", "commission", "courtage", "fees"],
}

# Colonnes de date, par ordre de préférence. Une date d'exécution vaut mieux qu'une
# date de saisie, qui vaut mieux qu'une date générique ; une date de validité est une
# échéance d'ordre et ne doit jamais servir de date d'opération.
DATES_PRIORITE = [
    (["date de statut", "date d execution", "date execution", "date d exec"], 100),
    (["date de negociation", "date de transaction", "date operation",
      "date d operation", "operationdate", "trade date"], 90),
    (["date de saisie", "date de l ordre", "date ordre"], 80),
    (["date comptable", "date de comptabilisation"], 70),
    (["date de valeur", "date valeur"], 60),
    (["date d achat", "date achat", "date de position", "lastmovementdate",
      "date du dernier mouvement", "date mouvement"], 50),
    (["date"], 40),
]
DATES_EXCLUES = ["date de validite", "date validite", "echeance", "date d echeance"]

# ── Vocabulaire des opérations ──────────────────────────────────────────────
NATURES = [
    ("ACHAT",     ["achat", "souscription", "acquisition", "buy", "versement programme"]),
    ("VENTE",     ["vente", "cession", "sell", "rachat", "remboursement titres"]),
    ("DIVIDENDE", ["dividende", "coupon", "revenu", "distribution"]),
    ("DEPOT",     ["virement recu", "vir recu", "virement", "vir ", "versement",
                   "alimentation", "apport", "depot", "approvisionnement"]),
    ("RETRAIT",   ["retrait", "prelevement", "taxe", "droits de garde", "impot",
                   "frais", "commission", "cotisation"]),
]

# ── Statuts d'ordre ─────────────────────────────────────────────────────────
# Un carnet d'ordres contient aussi ce qui n'a jamais eu lieu : ne jamais l'importer.
STATUTS_EXECUTES = ["execute", "executee", "realise", "realisee", "comptabilise",
                    "denoue", "termine", "ok", "valide", "traite", "paye", "regle"]
STATUTS_REJETES = {
    "annule": "ordre annulé", "annulee": "ordre annulé", "cancelled": "ordre annulé",
    "rejete": "ordre rejeté", "rejetee": "ordre rejeté", "refuse": "ordre refusé",
    "expire": "ordre expiré", "expiree": "ordre expiré", "echu": "ordre échu",
    "supprime": "ordre supprimé", "non execute": "ordre non exécuté",
    "en cours": "ordre encore en cours", "en attente": "ordre en attente",
    "attente": "ordre en attente", "transmis": "ordre transmis, pas encore exécuté",
}
STATUTS_PARTIELS = ["partiel", "partielle", "partiellement"]

# Devises réellement cotées : un code à trois lettres ne suffit pas à faire une devise
# (« LIM », « CPT », « SRD » sont des types d'ordre, pas des monnaies).
DEVISES = {"EUR", "USD", "GBP", "GBX", "GBP", "CHF", "JPY", "CAD", "AUD", "NZD", "SEK",
           "NOK", "DKK", "PLN", "CZK", "HUF", "HKD", "SGD", "CNY", "CNH", "KRW", "INR",
           "BRL", "MXN", "ZAR", "TRY", "ILS", "RUB", "THB", "TWD", "AED", "SAR"}

SEPARATEURS = [";", "\t", ",", "|"]
FORMATS_DATE = ["%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y",
                "%d/%m/%y", "%Y%m%d", "%d %m %Y"]


class ImportInvalide(ValueError):
    """Le fichier ne ressemble à aucun relevé exploitable."""


# ── Outils de normalisation ─────────────────────────────────────────────────

def _sans_accent(texte: str) -> str:
    sans = unicodedata.normalize("NFKD", texte or "")
    return "".join(c for c in sans if not unicodedata.combining(c))


def _cle(texte: str) -> str:
    """Normalise un texte pour la comparaison : sans accent, sans ponctuation, minuscule."""
    t = _sans_accent(texte or "").lower().replace("﻿", "")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _nombre(valeur, defaut=None) -> Optional[float]:
    """
    Convertit un montant de banque en nombre.
    Gère « 15 290,00 », « 1 332,00 € », « 2'954.74 », « -31,49 », « 1,234.56 », « (45,20) ».
    """
    if valeur is None:
        return defaut
    if isinstance(valeur, (int, float)):
        return float(valeur)
    t = _sans_accent(str(valeur)).strip()
    negatif = t.startswith("(") and t.endswith(")")      # comptabilité anglo-saxonne
    t = re.sub(r"[^\d,.\-+]", "", t.replace(" ", "").replace("\xa0", "").replace("'", ""))
    if not t or t in "-+":
        return defaut
    if "," in t and "." in t:
        # Les deux sont présents : le séparateur décimal est le dernier des deux
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif t.count(",") > 1:
        t = t.replace(",", "")                  # 1,234,567 : séparateurs de milliers
    elif "," in t:
        # Une seule virgule : décimale du format français, quel que soit le nombre de
        # chiffres qui suit (« 26,5770 € » vaut 26,577 et non 265 770).
        t = t.replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")                  # 1.234.567 : séparateurs de milliers
    try:
        v = float(t)
        return -v if negatif else v
    except ValueError:
        return defaut


def _est_nombre(valeur) -> bool:
    return _nombre(valeur) is not None


def _isin_valide(code: str) -> bool:
    """Vérifie la clé de contrôle d'un ISIN (Luhn sur l'expansion alphanumérique)."""
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", code or ""):
        return False
    chiffres = "".join(str(int(c, 36)) for c in code)
    total, double = 0, True
    for c in reversed(chiffres[:-1]):
        n = int(c) * (2 if double else 1)
        total += n - 9 if n > 9 else n
        double = not double
    return (10 - total % 10) % 10 == int(chiffres[-1])


def _ressemble_isin(valeur) -> bool:
    return bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", (str(valeur or "").strip().upper())))


# ── Dates : le format se tranche sur la colonne entière ─────────────────────

def _format_date(valeurs: List[str]) -> Optional[str]:
    """
    Détermine le format de date d'une colonne en l'essayant en entier.

    L'ambiguïté jour/mois (01/02/2026) ne se lève pas valeur par valeur : il faut
    regarder toute la colonne. Dès qu'une seule ligne porte un quantième supérieur
    à 12, l'ordre est établi pour l'ensemble du fichier.
    """
    echantillon = [str(v).strip()[:19] for v in valeurs if str(v or "").strip()]
    if not echantillon:
        return None
    meilleurs = []
    for fmt in FORMATS_DATE:
        reussis = 0
        for v in echantillon:
            try:
                datetime.strptime(v, fmt)
                reussis += 1
            except ValueError:
                pass
        if reussis >= max(1, int(0.8 * len(echantillon))):
            meilleurs.append((reussis, fmt))
    if not meilleurs:
        return None
    # Entre jour/mois et mois/jour, le premier format qui reste valable l'emporte ;
    # l'ordre de FORMATS_DATE place le format français d'abord, et un quantième > 12
    # a de toute façon déjà éliminé le format américain.
    meilleurs.sort(key=lambda x: (-x[0], FORMATS_DATE.index(x[1])))
    return meilleurs[0][1]


def _ambigu(valeurs: List[str], fmt: str) -> bool:
    """Vrai si jour/mois et mois/jour conviennent tous deux : aucune valeur ne tranche."""
    if fmt not in ("%d/%m/%Y", "%m/%d/%Y"):
        return False
    autre = "%m/%d/%Y" if fmt == "%d/%m/%Y" else "%d/%m/%Y"
    for v in valeurs:
        v = str(v or "").strip()[:19]
        try:
            datetime.strptime(v, fmt)
        except ValueError:
            continue
        try:
            datetime.strptime(v, autre)
        except ValueError:
            return False          # cette valeur tranche : pas d'ambiguïté
    return True


def _date(valeur, fmt: Optional[str] = None) -> Optional[str]:
    """Convertit une date en AAAA-MM-JJ. None si illisible. Une date future est refusée."""
    if not valeur:
        return None
    t = str(valeur).strip()[:19]
    formats = ([fmt] if fmt else []) + FORMATS_DATE + [
        "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"]
    for f in formats:
        try:
            d = datetime.strptime(t, f).date()
            return min(d, date.today()).isoformat()
        except (ValueError, TypeError):
            continue
    return None


# ── Structure du fichier ────────────────────────────────────────────────────

def _decoder(contenu: bytes) -> str:
    """
    Choisit l'encodage qui restitue le plus d'accents français plausibles.
    Un fichier Windows mal décodé donne « Libell� » : on le détecte et on change.
    """
    candidats = []
    for rang, encodage in enumerate(("utf-8-sig", "utf-8", "cp1252", "latin-1")):
        try:
            texte = contenu.decode(encodage)
        except UnicodeDecodeError:
            continue
        # À qualité égale, le premier de la liste l'emporte : utf-8-sig avant utf-8.
        candidats.append((texte.count("�"), -len(re.findall(r"[éèêàçùôîû]", texte)),
                          rang, texte))
    if not candidats:
        texte = contenu.decode("utf-8", errors="replace")
    else:
        candidats.sort(key=lambda c: c[:3])
        texte = candidats[0][3]
    # Marque d'ordre des octets en tête de fichier, sous ses deux formes : décodée,
    # ou relue octet par octet par un encodage Windows (« ï»¿ »). Si on la laisse, elle
    # se colle au premier intitulé de colonne et en fausse la reconnaissance.
    return texte.lstrip("﻿").removeprefix("ï»¿")


def _separateur(lignes: List[str]) -> str:
    """
    Sépare sur le caractère qui découpe le plus régulièrement les premières lignes.
    Le compte seul ne suffit pas : une ligne de texte libre contient des virgules.
    """
    echantillon = [l for l in lignes[:20] if l.strip()][:10]
    if not echantillon:
        return ";"
    meilleur, score_max = ";", -1
    for sep in SEPARATEURS:
        comptes = [l.count(sep) for l in echantillon]
        if max(comptes) == 0:
            continue
        frequent = Counter(comptes).most_common(1)[0]
        regularite = frequent[1] / len(comptes)        # part des lignes au même découpage
        score = regularite * 10 + min(frequent[0], 20)
        if frequent[0] > 0 and score > score_max:
            meilleur, score_max = sep, score
    return meilleur


def _ligne_entete(lignes: List[str], sep: str) -> int:
    """
    Trouve la vraie ligne d'en-tête. Les banques placent souvent au-dessus le nom de
    l'établissement, le numéro de compte et la date d'édition : ces lignes n'ont ni le
    bon nombre de colonnes ni des intitulés reconnaissables.
    """
    tous = {a for alias in COLONNES.values() for a in alias}
    tous |= {a for alias, _ in DATES_PRIORITE for a in alias}
    meilleur, score_max = 0, -1
    for i, ligne in enumerate(lignes[:25]):
        cellules = [_cle(c) for c in next(csv.reader([ligne], delimiter=sep), [])]
        if len(cellules) < 2:
            continue
        # Un en-tête est fait de libellés, pas de nombres ni de dates
        reconnues = sum(1 for c in cellules if c and any(
            c == a or (len(a) > 3 and a in c) for a in tous))
        chiffres = sum(1 for c in cellules if _est_nombre(c))
        score = reconnues * 3 + len(cellules) - chiffres * 2
        if reconnues >= 2 and score > score_max:
            meilleur, score_max = i, score
    return meilleur


# ── Reconnaissance des colonnes ─────────────────────────────────────────────

def _score_intitule(cle: str, alias: str) -> int:
    """0 si l'intitulé ne correspond pas, sinon d'autant plus que la correspondance est nette."""
    if not cle:
        return 0
    if cle == alias:
        return 100
    if re.search(rf"(^| ){re.escape(alias)}( |$)", cle):
        return 70                       # l'alias est un mot entier de l'intitulé
    if len(alias) > 3 and alias in cle:
        return 40                       # l'alias est contenu dans l'intitulé
    return 0


def _profil(valeurs: List[str]) -> dict:
    """Ce que contient réellement une colonne, indépendamment de son intitulé."""
    v = [str(x).strip() for x in valeurs if str(x or "").strip()]
    if not v:
        return {"n": 0}
    n = len(v)
    cles = [_cle(x) for x in v]
    return {
        "n": n,
        "isin": sum(1 for x in v if _ressemble_isin(x)) / n,
        "isin_valide": sum(1 for x in v if _isin_valide(x.upper())) / n,
        "date": sum(1 for x in v if _date(x)) / n,
        "nombre": sum(1 for x in v if _est_nombre(x)) / n,
        "statut": sum(1 for c in cles if any(s in c for s in STATUTS_EXECUTES)
                      or any(s in c for s in STATUTS_REJETES)) / n,
        "sens": sum(1 for c in cles if any(m in c for nat, motifs in NATURES for m in motifs)) / n,
        "devise": sum(1 for x in v if x.upper() in DEVISES) / n,
        "valeurs": v,
    }


def _associer(entetes: List[str], profils: Dict[str, dict]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Associe chaque rôle à une colonne. Renvoie (rôles, origine de la décision).

    Le contenu prime pour ce qu'il identifie sans ambiguïté — un ISIN, un statut, une
    date — parce qu'un intitulé peut mentir : chez certaines banques, la colonne qui
    contient les ISIN s'appelle « Code Valeur », ce qui ressemble à un ticker.
    """
    roles, origine, prises = {}, {}, set()

    def attribuer(role, colonne, pourquoi):
        if role not in roles and colonne not in prises:
            roles[role], origine[role] = colonne, pourquoi
            prises.add(colonne)

    # 1. Ce que le contenu désigne sans équivoque
    for role, cle_profil, seuil, pourquoi in (
            ("isin", "isin", 0.6, "colonne contenant des codes ISIN"),
            ("statut", "statut", 0.6, "colonne contenant des statuts d'ordre"),
            ("devise", "devise", 0.8, "colonne contenant des codes devise"),
    ):
        candidats = [(profils[c].get(cle_profil, 0), c) for c in entetes
                     if profils[c].get("n") and profils[c].get(cle_profil, 0) >= seuil]
        if candidats:
            attribuer(role, max(candidats)[1], pourquoi)

    # 2. Les dates : toutes les colonnes qui en contiennent, départagées par l'intitulé
    candidates_date = [c for c in entetes
                       if profils[c].get("date", 0) >= 0.7 and c not in prises
                       and not any(x in _cle(c) for x in DATES_EXCLUES)]
    if candidates_date:
        def rang(colonne):
            cle = _cle(colonne)
            for alias, poids in DATES_PRIORITE:
                if any(_score_intitule(cle, a) for a in alias):
                    return poids
            return 0
        attribuer("date", max(candidates_date, key=rang), "colonne de dates")

    # 3. Le reste par l'intitulé, en traitant d'abord les correspondances les plus nettes
    paires = []
    for colonne in entetes:
        if colonne in prises:
            continue
        cle = _cle(colonne)
        for role, alias in COLONNES.items():
            if role in roles:
                continue
            score = max((_score_intitule(cle, a) for a in alias), default=0)
            if score:
                paires.append((score, role, colonne))
    for score, role, colonne in sorted(paires, key=lambda x: -x[0]):
        attribuer(role, colonne, f"intitulé « {colonne.strip()} »")

    # 4. Repêchage par le contenu pour ce qui manque encore
    if "sens" not in roles:
        candidats = [(profils[c].get("sens", 0), c) for c in entetes
                     if c not in prises and profils[c].get("sens", 0) >= 0.7]
        if candidats:
            attribuer("sens", max(candidats)[1], "colonne contenant des libellés d'opération")
    if "isin" not in roles and "ticker" in roles:
        # Un « ticker » qui contient en fait des ISIN : on rectifie
        c = roles["ticker"]
        if profils[c].get("isin", 0) >= 0.6:
            roles["isin"] = roles.pop("ticker")
            origine["isin"] = "colonne contenant des codes ISIN"
    return roles, origine


# ── Statut et nature ────────────────────────────────────────────────────────

def _statut(valeur) -> Tuple[str, Optional[str]]:
    """('execute' | 'rejete' | 'partiel' | 'inconnu', motif lisible)."""
    c = _cle(valeur)
    if not c:
        return "inconnu", None
    for motif, libelle in STATUTS_REJETES.items():
        if motif in c:
            return "rejete", libelle
    if any(s in c for s in STATUTS_PARTIELS):
        return "partiel", "ordre partiellement exécuté : vérifiez la quantité"
    if any(s in c for s in STATUTS_EXECUTES):
        return "execute", None
    return "inconnu", f"statut non reconnu : « {str(valeur).strip()} »"


def _nature(libelle, montant=None, mappages: Optional[dict] = None) -> Optional[str]:
    """
    Nature d'une opération d'après son libellé. None si aucune règle ne s'applique :
    la ligne est alors présentée à classer plutôt que devinée.
    """
    t = _cle(str(libelle or ""))
    if not t:
        return None
    if mappages and t in mappages:
        return mappages[t]                      # classement déjà choisi par l'utilisateur
    for nature, motifs in NATURES:
        if any(m.strip() in t for m in motifs):
            if nature in ("DEPOT", "RETRAIT") and montant is not None:
                return "DEPOT" if montant >= 0 else "RETRAIT"
            return nature
    return None


# ── Lecture ─────────────────────────────────────────────────────────────────

def lire_releve(contenu: bytes, mappages: Optional[dict] = None) -> dict:
    """
    Lit un relevé d'opérations, un carnet d'ordres ou un export de positions.

    Renvoie :
        lignes             opérations exploitables, en ordre chronologique
        a_classer          lignes complètes dont le libellé d'opération est inconnu
        ignorees           lignes écartées, avec le motif
        colonnes_reconnues rôle → intitulé de colonne retenu
        decisions          comment chaque colonne a été reconnue
        type               'operations' | 'ordres' | 'positions'
        avertissements     ce qui mérite un coup d'œil avant de valider
    """
    texte = _decoder(contenu)
    if not texte.strip():
        raise ImportInvalide("Le fichier est vide.")

    brut = texte.splitlines()
    sep = _separateur(brut)
    debut = _ligne_entete(brut, sep)
    lecteur = csv.DictReader(io.StringIO("\n".join(brut[debut:])), delimiter=sep)
    entetes = [c for c in (lecteur.fieldnames or []) if c is not None]
    if not entetes:
        raise ImportInvalide("Aucune ligne d'en-tête n'a pu être identifiée.")
    donnees = [r for r in lecteur if any((v or "").strip() for v in r.values())]
    if not donnees:
        raise ImportInvalide("Le fichier ne contient aucune ligne de données.")

    profils = {c: _profil([r.get(c) for r in donnees]) for c in entetes}
    roles, decisions = _associer(entetes, profils)

    avertissements = []
    if debut:
        avertissements.append(f"{debut} ligne(s) d'en-tête ignorée(s) avant le tableau.")

    # Format de date, tranché sur la colonne entière
    fmt_date = None
    if "date" in roles:
        valeurs = [r.get(roles["date"]) for r in donnees]
        fmt_date = _format_date(valeurs)
        if fmt_date and _ambigu(valeurs, fmt_date):
            avertissements.append(
                "Toutes les dates du fichier ont un quantième inférieur à 13 : impossible "
                "de distinguer jour/mois de mois/jour. Le format français a été retenu — "
                "vérifiez les dates ci-dessous avant de valider.")

    # Nature du fichier
    if "statut" in roles:
        type_fichier = "ordres"
    elif "sens" in roles and "date" in roles:
        type_fichier = "operations"
    else:
        type_fichier = "positions"

    if type_fichier == "positions" and (
            "quantite" not in roles or not ({"isin", "ticker", "nom"} & set(roles))):
        raise ImportInvalide(
            "Colonnes introuvables : il faut au minimum une quantité et un identifiant de "
            "titre (ISIN, ticker ou nom). Intitulés lus : " + ", ".join(entetes[:12]))

    lignes, a_classer, ignorees = [], [], []
    for num, ligne in enumerate(donnees, start=debut + 2):
        val = lambda role: ligne.get(roles[role]) if role in roles else None
        libelle_op = (val("sens") or "").strip()
        nom = (val("nom") or "").strip().strip('"')
        isin = (val("isin") or "").strip().upper()
        ticker = (val("ticker") or "").strip().upper()
        identite = nom or isin or ticker or libelle_op or "—"

        # 1. Un ordre non exécuté n'est pas une opération
        if "statut" in roles:
            etat, motif = _statut(val("statut"))
            if etat in ("rejete", "inconnu"):
                ignorees.append({"ligne": num, "nom": identite,
                                 "motif": motif or "ordre non exécuté"})
                continue
            if etat == "partiel":
                avertissements.append(f"Ligne {num} ({identite}) : {motif}")

        # 2. Nature de l'opération
        montant = _nombre(val("montant"))
        if type_fichier == "positions":
            nature = "ACHAT"
        else:
            nature = _nature(libelle_op, montant, mappages)

        quantite = _nombre(val("quantite"))
        prix = _nombre(val("prix"))
        frais = _nombre(val("frais"), None)
        jour = _date(val("date"), fmt_date)

        if nature is None:
            # Libellé inconnu : on ne devine pas, on fait classer
            a_classer.append({
                "ligne": num, "libelle": libelle_op, "nom": nom or None,
                "isin": isin or None, "ticker_fichier": ticker or None,
                "quantite": quantite, "pru": prix, "montant_fichier": montant,
                "date": jour, "devise_fichier": (val("devise") or "").strip().upper() or None,
            })
            continue

        # 3. Mouvement d'espèces : ni titre ni quantité, seulement un montant
        if nature in ("DEPOT", "RETRAIT", "DIVIDENDE") and not (quantite and prix):
            if montant is None:
                ignorees.append({"ligne": num, "nom": identite, "motif": "montant absent"})
                continue
            lignes.append({
                "ligne": num, "isin": None, "nom": libelle_op or nature,
                "ticker_fichier": None, "nature": nature, "sens": nature,
                "quantite": None, "pru": None, "frais": 0.0,
                "montant_eur": abs(montant), "cours_fichier": None,
                "montant_fichier": montant, "devise_fichier": None, "date": jour,
            })
            continue

        # 4. Opération sur titre
        if not quantite or quantite <= 0:
            ignorees.append({"ligne": num, "nom": identite, "motif": "quantité absente ou nulle"})
            continue
        if not _ressemble_isin(isin):
            isin = ""
        if prix is None and montant and quantite:
            prix = abs(montant) / quantite
        if prix is None:
            ignorees.append({"ligne": num, "nom": identite, "motif": "prix introuvable"})
            continue
        # Frais de courtage : écart entre le montant débité et prix × quantité, quand le
        # fichier donne les deux. Jamais inventés : à défaut, zéro.
        if frais is None and montant:
            ecart = abs(montant) - prix * quantite
            if nature == "VENTE":
                ecart = -ecart            # à la vente, les frais réduisent le produit perçu
            frais = round(ecart, 2) if 0 < ecart < abs(montant) * 0.05 else 0.0
        lignes.append({
            "ligne": num, "isin": isin or None, "nom": nom or None,
            "ticker_fichier": ticker or None, "quantite": quantite, "pru": prix,
            "sens": nature, "nature": nature, "montant_eur": None, "frais": frais or 0.0,
            "cours_fichier": prix, "montant_fichier": montant,
            "devise_fichier": (val("devise") or "").strip().upper() or None,
            "date": jour,
        })

    if not lignes and not a_classer:
        raise ImportInvalide(
            "Aucune ligne exploitable n'a été trouvée. "
            + (f"{len(ignorees)} ligne(s) ont été écartées — "
               f"motif le plus fréquent : {Counter(i['motif'] for i in ignorees).most_common(1)[0][0]}."
               if ignorees else "Vérifiez que le fichier contient bien des opérations."))

    sans_date = sum(1 for l in lignes if not l["date"])
    if sans_date:
        avertissements.append(
            f"{sans_date} ligne(s) sans date exploitable : la date devra être saisie à la main.")

    rang = {"DEPOT": 0, "RETRAIT": 1, "DIVIDENDE": 1, "ACHAT": 2, "VENTE": 2}
    lignes.sort(key=lambda l: (l["date"] or "", rang.get(l.get("nature"), 3), l["ligne"]))
    return {"lignes": lignes, "a_classer": a_classer, "ignorees": ignorees,
            "colonnes_reconnues": roles, "decisions": decisions,
            "type": type_fichier, "avertissements": avertissements,
            "separateur": sep, "format_date": fmt_date}


# Nom historique, conservé pour ne pas casser l'appel existant
lire_positions = lire_releve
