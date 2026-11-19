"""
core/import_pdf.py
==================
Lecture d'un avis d'opération au format PDF (Bourse Direct et assimilés).

Le module lit la couche de texte du PDF, celle que tout avis téléchargé chez un
courtier contient. C'est exact, instantané, et cela ne demande qu'une bibliothèque
(pdfplumber). Un PDF qui n'est qu'une image — relevé scanné ou photographié — est
refusé avec un message qui dit quoi faire : aucun moteur de reconnaissance optique
n'est embarqué, ces bibliothèques étant lourdes à installer pour un cas marginal.

Rien n'est pour autant cru sur parole : une couche de texte peut être mal découpée,
et un chiffre mal placé passerait sans bruit dans le portefeuille. Un avis
d'opération porte sa propre preuve :

        QUANTITÉ × COURS = BRUT          et          BRUT ± COURTAGE = MONTANT

Chaque bloc lu est confronté à ces deux égalités. Une ligne qui ne retombe pas juste
n'est pas importée : elle est signalée avec l'écart constaté. Une erreur de lecture
devient ainsi visible au lieu de passer dans le portefeuille.

Ce module ne lit QUE les blocs d'opération. L'en-tête du relevé — nom, adresse,
numéro de compte — n'est jamais extrait ni conservé.
"""

import io
import re
import unicodedata
from datetime import date, datetime
from typing import Optional

from .import_csv import ImportInvalide, _cle, _isin_valide, _nombre, _nature

# Un montant dans un avis d'opération : « +1 016,10 », « 19 637,03 », « 2.037,84 »
_MONTANT = r"[-+]?\d[\d  .]*(?:,\d+)?"

# Étiquettes des champs, telles qu'elles apparaissent sur l'avis. La reconnaissance
# se fait sur l'étiquette et non sur la position, pour tolérer un décalage de mise en page.
_CHAMPS = {
    "quantite": r"QUANTITE\s*[:;]?\s*(" + _MONTANT + r")",
    "cours":    r"COURS\s*[:;]?\s*(" + _MONTANT + r")",
    "brut":     r"BRUT\s*[:;]?\s*(" + _MONTANT + r")",
    "courtage": r"COURTAGE\s*[:;]?\s*(" + _MONTANT + r")",
    "tva":      r"TVA\s*[:;]?\s*(" + _MONTANT + r")",
}

# Début d'un bloc : une ligne qui commence par une date et contient un code à douze
# caractères. L'ISIN sert de pivot — ce qui le précède est la nature de l'opération,
# ce qui le suit est le libellé du titre, terminé par le montant. Repérer par pivot
# plutôt que par un gabarit figé permet de survivre au découpage approximatif que
# donne parfois l'extraction de texte : espaces en trop, colonnes recollées.
_LIGNE_DATEE = re.compile(
    r"^[ \t]*(?P<date>[0-3oOlI][\doOlIgSBZ][ ]?/[ ]?[01oOlI][\doOlIgSBZ][ ]?/[ ]?[12][\doOlIgSBZ]{3})"
    r"[ \t]+(?P<reste>\S.*)$", re.MULTILINE)
_CODE12 = re.compile(r"\b(?P<isin>[A-Z][A-Z0-9OISBZlGQ]{11})\b")
_FIN_MONTANT = re.compile(r"(?P<montant>[-+]?\d[\d  .:]*,\d{2})[ .]*$")

# Glyphes qu'un PDF mal généré peut faire ressortir à la place du bon caractère
# (un « O » pour un zéro, un « l » pour un 1), dans les deux sens. La correction
# n'est jamais appliquée seule : elle n'est retenue que si la clé de contrôle
# tombe juste.
_VERS_CHIFFRE = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "I": "1", "l": "1",
                               "L": "1", "i": "1", "|": "1", "S": "5", "s": "5", "B": "8",
                               "Z": "2", "z": "2", "G": "6", "T": "7", "A": "4"})
_VERS_LETTRE = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z"})


def _sans_accent(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t or "")
                   if not unicodedata.combining(c))


# ── Extraction du texte ─────────────────────────────────────────────────────

def _texte_couche(contenu: bytes) -> str:
    """Texte réellement présent dans le PDF. Vide si le document n'est qu'une image."""
    try:
        import pdfplumber
    except ImportError:
        return ""
    try:
        with pdfplumber.open(io.BytesIO(contenu)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return ""


# ── Mouvements d'espèces : la colonne décide du sens ────────────────────────
#
# Un avis d'opération porte, sous l'en-tête « Date | Désignation | Débit (€) |
# Crédit (€) », des lignes qui ne concernent aucun titre : virement reçu,
# régularisation, remboursement de frais de gestion, prélèvement social.
#
# Le texte brut ne dit pas de quelle colonne vient le montant : il le place en fin
# de ligne, débit et crédit confondus. Lire le sens dans le libellé serait deviner —
# « REGULARISATION PEA VIRT 85611543 » ne dit pas si l'argent entre ou sort. On lit
# donc la POSITION du montant sur la page et on la compare à celle des deux en-têtes
# de colonne. C'est le courtier lui-même qui déclare le sens, par la mise en page.
#
# Deux précautions. Le repère est pris sur la ligne d'en-tête du tableau, repérée par
# le mot « Désignation » : les mots « Débit » et « Crédit » réapparaissent dans la
# mention légale du pied de page, et s'y fier donnerait des colonnes fantaisistes.
# Et un nombre n'est retenu que s'il commence à droite de la colonne Débit : sans
# cela, le numéro de compte du virement et la ligne « BRUT : » d'un ordre, tous deux
# dans la zone Désignation, seraient pris pour des montants.

_MARGE_COLONNE = 15.0          # tolérance pour un nombre aligné à droite
_TOLERANCE_LIGNE = 3.0         # écart vertical en deçà duquel deux mots sont sur la même ligne
_NOMBRE_SEUL = re.compile(r"^\d{1,3}(?:,\d{1,4})?$")

# Un montant au débit peut être de deux natures. Un RETRAIT est votre argent qui
# repart chez vous ; un PRÉLÈVEMENT est une charge subie — contribution sociale,
# taxe, droits de garde — qui quitte le compte sans vous revenir. La distinction
# n'est pas cosmétique : un retrait diminue vos versements nets, un prélèvement
# pèse sur votre résultat. Les confondre gonflerait le gain affiché.
_PRELEVEMENTS = ("CSG", "CRDS", "NPS", "PRELEV", "TAXE", "TVA", "IMPOT", "IMPÔT",
                 "DROITS DE GARDE", "CONTRIBUTION", "COTISATION", "COMMISSION",
                 "FRAIS")


def _nature_debit(libelle: str) -> str:
    """Retrait ou prélèvement, d'après le libellé du courtier."""
    t = _sans_accent(libelle or "").upper()
    return "PRELEVEMENT" if any(mot in t for mot in _PRELEVEMENTS) else "RETRAIT"


def _especes_des_pages(contenu: bytes) -> tuple:
    """
    Mouvements d'espèces lus page par page, avec le sens donné par la colonne.

    Renvoie (mouvements, motifs) : les mouvements reconnus, et les raisons pour
    lesquelles certaines lignes datées ont été laissées de côté.
    """
    try:
        import pdfplumber
    except ImportError:
        return [], []
    mouvements, motifs = [], []
    try:
        with pdfplumber.open(io.BytesIO(contenu)) as pdf:
            for page in pdf.pages:
                m, r = _especes_une_page(page)
                mouvements.extend(m)
                motifs.extend(r)
    except Exception:
        return [], []
    return mouvements, motifs


def _especes_une_page(page) -> tuple:
    mots = page.extract_words()
    if not mots:
        return [], []

    # Regroupement des mots en lignes, par ordonnée
    lignes = []
    for mot in sorted(mots, key=lambda m: (m["top"], m["x0"])):
        if lignes and abs(mot["top"] - lignes[-1][0]["top"]) <= _TOLERANCE_LIGNE:
            lignes[-1].append(mot)
        else:
            lignes.append([mot])

    # L'en-tête du tableau : la ligne qui porte « Désignation »
    entete = next((lg for lg in lignes
                   if any(_sans_accent(m["text"]).lower() == "designation" for m in lg)), None)
    if entete is None:
        return [], []
    def repere(nom):
        for m in entete:
            if _sans_accent(m["text"]).lower() == nom:
                return m
        return None
    debit, credit = repere("debit"), repere("credit")
    if debit is None or credit is None:
        return [], []
    bord = debit["x0"] - _MARGE_COLONNE
    c_debit = (debit["x0"] + debit["x1"]) / 2
    c_credit = (credit["x0"] + credit["x1"]) / 2

    mouvements, motifs = [], []
    for num, lg in enumerate(lignes, start=1):
        texte = " ".join(m["text"] for m in lg)
        depart = _LIGNE_DATEE.match(texte)
        if not depart or _CODE12.search(texte):
            continue                      # pas une ligne datée, ou une opération sur titre

        # Le montant : les derniers mots numériques situés dans la zone des colonnes.
        # On remonte de la droite tant que les mots s'enchaînent — « 5 » puis « 555,00 »
        # forment un seul nombre, le séparateur de milliers étant une espace.
        pris = []
        for mot in reversed(lg):
            if mot["x0"] < bord or not _NOMBRE_SEUL.match(mot["text"]):
                break
            pris.insert(0, mot)
        if not pris:
            motifs.append({"ligne": num, "nom": " ".join(texte.split()[1:])[:60],
                           "motif": "aucun montant lisible dans les colonnes Débit ou Crédit"})
            continue

        montant = _montant("".join(m["text"] for m in pris))
        if montant is None or montant == 0:
            motifs.append({"ligne": num, "nom": " ".join(texte.split()[1:])[:60],
                           "motif": "montant illisible"})
            continue

        centre = (pris[0]["x0"] + pris[-1]["x1"]) / 2
        au_credit = abs(centre - c_credit) < abs(centre - c_debit)
        # Le libellé se reconstruit à partir des MOTS restants, et non par découpe du
        # texte : « VRT DE 85611543 5 555,00 » contient des « 5 » qu'une recherche
        # textuelle du montant viendrait rogner dans le numéro de compte.
        corps = " ".join(m["text"] for m in lg[:len(lg) - len(pris)])
        sans_date = _LIGNE_DATEE.match(corps)
        libelle = " ".join((sans_date.group("reste") if sans_date else corps).split())
        propre = libelle.strip(" .-") or ("Crédit" if au_credit else "Débit")
        mouvements.append({
            "ligne": num,
            "date": _corriger_date(depart.group("date")),
            "libelle": propre,
            "montant": abs(montant),
            "nature": "DEPOT" if au_credit else _nature_debit(propre),
        })
    return mouvements, motifs


# ── Contrôle des codes et des dates lus ──────────────────────

def _corriger_isin(brut: str) -> Optional[str]:
    """
    Valide un ISIN par sa clé de contrôle, en tentant au besoin les quelques
    substitutions de glyphes ci-dessus. Sans cette validation, corriger reviendrait
    à inventer : ici, soit le code est mathématiquement cohérent, soit on renonce.
    """
    lu = re.sub(r"[^A-Za-z0-9]", "", brut or "")
    t = lu.upper()
    if len(t) != 12:
        return None
    # Un avis d'opération est imprimé en majuscules. Des minuscules dans le corps du
    # code trahissent donc une lecture douteuse, et il ne faut surtout pas s'y fier :
    # « Lutes1043599 » a une clé de contrôle juste par accident, et l'accepter
    # importerait silencieusement le mauvais titre.
    suspect = any(c.islower() for c in lu[2:])
    if not suspect and _isin_valide(t):
        return t
    # Les deux premiers caractères sont un pays (lettres), les dix suivants
    # sont, chez les émetteurs européens, presque toujours des chiffres.
    candidat = t[:2].translate(_VERS_LETTRE) + t[2:].translate(_VERS_CHIFFRE)
    if _isin_valide(candidat):
        return candidat
    # Dernier essai : certains ISIN contiennent de vraies lettres au milieu
    for i in range(2, 12):
        essai = candidat[:i] + t[i] + candidat[i + 1:]
        if _isin_valide(essai):
            return essai
    return None


# Dans une date, tout caractère non « / » est forcément un chiffre : on peut donc
# convertir plus largement qu'ailleurs sans risque d'inventer. Le contrôle final reste
# le même — la date doit se parser, et rester plausible.
_VERS_CHIFFRE_DATE = str.maketrans({
    "O": "0", "o": "0", "Q": "0", "D": "0", "I": "1", "l": "1", "L": "1", "i": "1",
    "|": "1", "S": "5", "s": "5", "B": "8", "b": "6", "Z": "2", "z": "2", "G": "6",
    "g": "9", "T": "7", "A": "4", "J": "1", "q": "9", "e": "6",
})


def _corriger_date(brut: str) -> Optional[str]:
    """Date d'un avis, réparée puis bornée : jamais dans le futur, jamais antédiluvienne."""
    t = re.sub(r"\s+", "", brut or "")
    t = t.translate(_VERS_CHIFFRE_DATE)
    t = re.sub(r"[^\d/]", "", t)
    try:
        d = datetime.strptime(t, "%d/%m/%Y").date()
    except ValueError:
        return None
    if d.year < 1990:
        return None                       # lecture manifestement fausse
    return min(d, date.today()).isoformat()


def _montant(brut: str) -> Optional[float]:
    """
    Montant d'un avis. La reconnaissance glisse parfois un deux-points ou transforme
    l'espace des milliers en point : « 1: 028,16 », « 2.037,84 ». On nettoie d'abord,
    puis on applique les conventions françaises de l'import CSV.
    """
    if brut is None:
        return None
    t = _sans_accent(str(brut))
    t = re.sub(r"[^\d,.\-+]", "", t.replace(" ", "").replace("\xa0", ""))
    # Un point ou une virgule suivi d'exactement trois chiffres puis d'une autre
    # séparation est un séparateur de milliers, pas une décimale.
    t = re.sub(r"[.](?=\d{3}(?:[.,]|$))", "", t)
    return _nombre(t)


# ── Lecture des blocs ───────────────────────────────────────────────────────

def _champ(bloc: str, nom: str) -> Optional[float]:
    m = re.search(_CHAMPS[nom], bloc, re.IGNORECASE)
    return _montant(m.group(1)) if m else None


def lire_releve_pdf(contenu: bytes, mappages: Optional[dict] = None) -> dict:
    """
    Lit un avis d'opération PDF et renvoie la même structure que le lecteur CSV,
    de sorte que l'écran d'aperçu et la validation soient rigoureusement les mêmes.
    """
    texte = _texte_couche(contenu)
    source, avertissements = "texte", []
    if len(texte.strip()) < 50:
        raise ImportInvalide(
            "Ce PDF ne contient pas de texte : c'est une image (document scanné ou "
            "photographié). Téléchargez l'avis d'opération directement depuis l'espace "
            "de votre courtier plutôt qu'une version scannée, ou exportez vos "
            "opérations en CSV.")

    entetes = []
    for m in _LIGNE_DATEE.finditer(texte):
        reste = m.group("reste")
        pivot = _CODE12.search(reste)
        if not pivot:
            continue                      # ligne datée sans code titre : pas une opération
        apres = reste[pivot.end():].strip()
        fin = _FIN_MONTANT.search(apres)
        entetes.append({
            "debut": m.start(),
            "date": m.group("date"),
            "nature": reste[:pivot.start()].strip(),
            "isin": pivot.group("isin"),
            "libelle": (apres[:fin.start()] if fin else apres).strip(),
            "montant": fin.group("montant") if fin else None,
        })
    # Les mouvements d'espèces se lisent à part : ils n'ont ni titre ni quantité, et
    # leur sens vient de la colonne où le courtier a posé le montant.
    especes, motifs_especes = _especes_des_pages(contenu)

    if not entetes and not especes:
        raise ImportInvalide(
            "Aucune opération n'a été reconnue dans ce PDF. Le lecteur attend soit des "
            "lignes du type « 09/09/2024  ACHAT COMPTANT  FR0011871110  LIBELLE  1 019,00 », "
            "suivies des champs QUANTITE, COURS, COURTAGE et BRUT, soit des mouvements "
            "d'espèces portés aux colonnes Débit ou Crédit.")

    lignes, ignorees = [], list(motifs_especes)
    for k, e in enumerate(entetes):
        # Le bloc court jusqu'au début de l'opération suivante
        fin = entetes[k + 1]["debut"] if k + 1 < len(entetes) else len(texte)
        bloc = texte[e["debut"]:fin]
        num = texte[:e["debut"]].count("\n") + 1

        libelle_nature = " ".join(e["nature"].split())
        nature = _nature(libelle_nature, None, mappages)
        isin = _corriger_isin(e["isin"])
        jour = _corriger_date(e["date"])
        nom = " ".join(e["libelle"].split()).strip(" .-")

        quantite = _champ(bloc, "quantite")
        cours = _champ(bloc, "cours")
        brut = _champ(bloc, "brut")
        courtage = _champ(bloc, "courtage") or 0.0
        tva = _champ(bloc, "tva") or 0.0
        montant = _montant(e["montant"])

        identite = nom or isin or libelle_nature
        if nature not in ("ACHAT", "VENTE"):
            ignorees.append({"ligne": num, "nom": identite,
                             "motif": f"opération non reconnue : « {libelle_nature} »"})
            continue
        if quantite is None or cours is None:
            ignorees.append({"ligne": num, "nom": identite,
                             "motif": "quantité ou cours illisible"})
            continue
        # Le signe de la quantité confirme le sens lu dans le libellé
        if quantite < 0:
            nature = "VENTE"
        quantite = abs(quantite)
        if not quantite or not cours:
            ignorees.append({"ligne": num, "nom": identite, "motif": "quantité ou cours nul"})
            continue

        # ── Les deux recoupements ───────────────────────────────────────────
        controles, ecarts = [], []
        if brut is not None:
            ecart = abs(quantite * cours - abs(brut))
            controles.append(ecart <= max(0.02, abs(brut) * 0.001))
            if not controles[-1]:
                ecarts.append(f"quantité × cours = {quantite * cours:.2f} € "
                              f"mais le brut indiqué est {abs(brut):.2f} €")
        if brut is not None and montant is not None:
            attendu = abs(brut) + courtage + tva if nature == "ACHAT" else abs(brut) - courtage - tva
            ecart = abs(attendu - abs(montant))
            controles.append(ecart <= max(0.02, abs(montant) * 0.001))
            if not controles[-1]:
                ecarts.append(f"brut {'+' if nature == 'ACHAT' else '−'} frais = {attendu:.2f} € "
                              f"mais le montant indiqué est {abs(montant):.2f} €")
        if ecarts:
            ignorees.append({"ligne": num, "nom": identite,
                             "motif": "le relevé ne se recoupe pas : " + " ; ".join(ecarts)})
            continue

        if isin is None:
            avertissements.append(
                f"Ligne {num} ({identite}) : le code ISIN « {e['isin']} » n'a pas pu être "
                "validé. Le titre sera recherché par son libellé — vérifiez-le dans l'aperçu.")

        lignes.append({
            "ligne": num, "isin": isin, "nom": nom or None, "ticker_fichier": None,
            "quantite": quantite, "pru": cours, "sens": nature, "nature": nature,
            "montant_eur": None, "frais": round(courtage + tva, 2),
            "cours_fichier": cours, "montant_fichier": montant,
            "devise_fichier": None, "date": jour,
        })

    for mv in especes:
        if not mv["date"]:
            ignorees.append({"ligne": mv["ligne"], "nom": mv["libelle"], "motif": "date illisible"})
            continue
        lignes.append({
            "ligne": mv["ligne"], "isin": None, "nom": mv["libelle"],
            "ticker_fichier": None, "quantite": None, "pru": None,
            "sens": mv["nature"], "nature": mv["nature"],
            "montant_eur": mv["montant"], "frais": 0.0,
            "cours_fichier": None, "montant_fichier": mv["montant"],
            "devise_fichier": None, "date": mv["date"],
        })

    if not lignes:
        raise ImportInvalide(
            "Aucune opération exploitable. " + (
                f"{len(ignorees)} ligne(s) écartée(s) — première raison : {ignorees[0]['motif']}"
                if ignorees else ""))

    rang = {"DEPOT": 0, "RETRAIT": 1, "DIVIDENDE": 1, "ACHAT": 2, "VENTE": 2}
    lignes.sort(key=lambda l: (l["date"] or "", rang.get(l.get("nature"), 3), l["ligne"]))
    return {
        "lignes": lignes, "a_classer": [], "ignorees": ignorees,
        "colonnes_reconnues": {"source": f"PDF lu par {source}"},
        "decisions": {"source": "avis d'opération, champs reconnus par étiquette "
                                "et recoupés par le calcul"},
        # « ordres » signale à la validation que les dates sont de vraies dates
        # d'exécution, par opposition à un export de positions. Un avis qui ne porte
        # que des mouvements d'espèces relève de la même catégorie : ses dates sont
        # celles des mouvements.
        "type": "ordres", "avertissements": avertissements,
        "separateur": None, "format_date": "%d/%m/%Y",
    }
