"""
core/import_pdf.py
==================
Lecture d'un avis d'opération au format PDF (Bourse Direct et assimilés).

Deux cas se présentent, et le module traite les deux sans rien demander :

1. Le PDF contient une couche de texte — c'est le cas d'un document généré par le
   courtier. On lit le texte directement : exact, instantané, aucune dépendance.

2. Le PDF n'est qu'une image par page — c'est le cas d'un document imprimé puis
   rescanné, ou exporté en image. Il faut alors de la reconnaissance optique, qui
   se trompe parfois : « FR0011871110 » devient « FROO11871110 », « 09 » devient
   « og », un « 1 028,16 » gagne un deux-points parasite.

C'est pourquoi rien n'est cru sur parole. Un avis d'opération porte sa propre preuve :

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
from typing import List, Optional, Tuple

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
# plutôt que par un gabarit figé permet de survivre aux espaces fantaisistes que
# produit la reconnaissance optique.
_LIGNE_DATEE = re.compile(
    r"^[ \t]*(?P<date>[0-3oOlI][\doOlIgSBZ][ ]?/[ ]?[01oOlI][\doOlIgSBZ][ ]?/[ ]?[12][\doOlIgSBZ]{3})"
    r"[ \t]+(?P<reste>\S.*)$", re.MULTILINE)
_CODE12 = re.compile(r"\b(?P<isin>[A-Z][A-Z0-9OISBZlGQ]{11})\b")
_FIN_MONTANT = re.compile(r"(?P<montant>[-+]?\d[\d  .:]*,\d{2})[ .]*$")

# Confusions courantes de la reconnaissance optique, dans les deux sens
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


def _images_du_pdf(contenu: bytes) -> list:
    """
    Images des pages, pour la reconnaissance optique. On récupère d'abord l'image
    déjà intégrée au PDF — un relevé scanné en contient une par page, et l'extraire
    évite tout rendu. À défaut, on rend la page.
    """
    images = []
    # La définition est décisive : sur une page A4, l'image intégrée d'un relevé
    # scanné tourne autour de 1 200 px de large, et à cette taille la reconnaissance
    # confond allègrement « 09/09/2024 » avec « oana2a24 ». On rend donc la page à
    # environ 2 200 px, ce qui corrige l'essentiel des confusions.
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(io.BytesIO(contenu))
        for i in range(len(doc)):
            images.append(doc[i].render(scale=3.7).to_pil())
        if images:
            return images
    except Exception:
        pass
    try:                                    # repli : l'image intégrée, agrandie
        from pypdf import PdfReader
        from PIL import Image
        lecteur = PdfReader(io.BytesIO(contenu))
        for page in lecteur.pages:
            for img in page.images:
                try:
                    im = Image.open(io.BytesIO(img.data))
                    if im.width < 1800:
                        facteur = 1800 / im.width
                        im = im.resize((int(im.width * facteur), int(im.height * facteur)),
                                       Image.LANCZOS)
                    images.append(im)
                except Exception:
                    continue
    except Exception:
        pass
    return images


def _lignes_depuis_blocs(blocs) -> str:
    """
    Recompose des lignes de texte à partir de blocs positionnés.

    Un moteur de reconnaissance rend des morceaux avec leurs coordonnées, pas des
    lignes. On regroupe ceux qui partagent la même hauteur, puis on les ordonne de
    gauche à droite : « 09/09/2024 » + « ACHAT COMPTANT » + « FR0011871110 » + …
    redeviennent la ligne du relevé, que le reste du module sait lire.
    """
    elements = []
    for bloc in blocs or []:
        boite, texte = bloc[0], str(bloc[1])
        ys = [p[1] for p in boite]
        xs = [p[0] for p in boite]
        elements.append((sum(ys) / len(ys), min(xs), texte, max(ys) - min(ys)))
    if not elements:
        return ""
    elements.sort(key=lambda e: (e[0], e[1]))
    hauteur_type = sorted(e[3] for e in elements)[len(elements) // 2] or 20
    lignes, courante, y_ligne = [], [], None
    for y, x, texte, _h in elements:
        if y_ligne is not None and abs(y - y_ligne) > hauteur_type * 0.7:
            lignes.append(" ".join(t for _, t in sorted(courante)))
            courante = []
        if not courante:
            y_ligne = y
        courante.append((x, texte))
    if courante:
        lignes.append(" ".join(t for _, t in sorted(courante)))
    return "\n".join(lignes)


def _ocr_rapide(images) -> Optional[str]:
    """
    Reconnaissance par RapidOCR : s'installe avec pip seul, sans logiciel externe,
    et lit mieux les codes ISIN et les dates que l'alternative. C'est donc le moteur
    essayé en premier.
    """
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None
    try:
        moteur = RapidOCR()
        pages = []
        for img in images:
            resultat, _ = moteur(np.array(img.convert("RGB")))
            pages.append(_lignes_depuis_blocs(resultat))
        return "\n".join(pages)
    except Exception:
        return None


def _ocr_tesseract(images) -> Optional[str]:
    """Repli : Tesseract, s'il se trouve installé sur la machine."""
    try:
        import pytesseract
    except ImportError:
        return None
    try:
        return "\n".join(pytesseract.image_to_string(img, config="--psm 6") for img in images)
    except Exception:
        return None


def _texte_ocr(contenu: bytes) -> Tuple[str, Optional[str]]:
    """
    Texte reconnu optiquement. Renvoie (texte, message d'erreur éventuel).
    L'absence du moteur n'est pas une erreur du fichier : on le dit clairement, avec
    la commande qui règle le problème, plutôt que de prétendre le relevé illisible.
    """
    images = _images_du_pdf(contenu)
    if not images:
        return "", ("Aucune page exploitable n'a pu être extraite de ce PDF. "
                    "Installez les bibliothèques de lecture : "
                    "pip install pypdfium2 pdfplumber pypdf")
    for moteur in (_ocr_rapide, _ocr_tesseract):
        texte = moteur(images)
        if texte and len(texte.strip()) >= 50:
            return texte, None
    return "", ("Ce PDF ne contient aucun texte : c'est une image, et sa lecture demande "
                "un moteur de reconnaissance optique. Installez-le avec la commande "
                "suivante, puis relancez le serveur :\n\n"
                "    pip install rapidocr-onnxruntime\n\n"
                "Aucun logiciel externe n'est nécessaire. "
                "Vous pouvez aussi exporter ce relevé en CSV depuis votre courtier.")


# ── Réparation de ce que la reconnaissance a pu abîmer ──────────────────────

def _corriger_isin(brut: str) -> Optional[str]:
    """
    Remet un ISIN d'aplomb après reconnaissance optique, puis le VALIDE par sa clé
    de contrôle. Sans cette validation, corriger reviendrait à inventer : ici, soit
    le code corrigé est mathématiquement cohérent, soit on renonce.
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
        texte, erreur = _texte_ocr(contenu)
        source = "reconnaissance optique"
        if erreur:
            raise ImportInvalide(erreur)
        avertissements.append(
            "Ce PDF est une image : son contenu a été lu par reconnaissance optique. "
            "Chaque ligne a été recoupée par le calcul (quantité × cours = brut, "
            "brut ± courtage = montant), mais relisez les montants avant de valider.")
    if len(texte.strip()) < 50:
        raise ImportInvalide("Aucun texte exploitable n'a pu être extrait de ce PDF.")

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
    if not entetes:
        raise ImportInvalide(
            "Aucune opération n'a été reconnue dans ce PDF. Le lecteur attend des lignes "
            "du type « 09/09/2024  ACHAT COMPTANT  FR0011871110  LIBELLE  1 019,00 », "
            "suivies des champs QUANTITE, COURS, COURTAGE et BRUT.")

    lignes, ignorees = [], []
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
        "type": "ordres", "avertissements": avertissements,
        "separateur": None, "format_date": "%d/%m/%Y",
    }
