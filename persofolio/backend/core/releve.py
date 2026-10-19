"""
core/releve.py
==============
Lecture d'un relevé de compte bancaire au format PDF, sans modèle par banque.

Le principe est géométrique, pas textuel : dans tous les relevés français que
nous avons vus, le montant d'une opération est posé dans l'une des deux colonnes
« Débit » / « Crédit », et c'est sa position horizontale — pas son signe, absent
du papier — qui dit le sens de l'opération. On repère donc les deux colonnes à
partir de la ligne d'en-tête, puis on range chaque montant dans la plus proche.

Déroulé :
  1. caractères → lignes (regroupées par ordonnée), avec reconstitution des
     espaces : certaines banques (Société Générale) n'écrivent aucun caractère
     d'espace dans le PDF, les mots y sont simplement séparés par un écart.
  2. détection de l'en-tête « Débit … Crédit » → abscisse des deux colonnes.
  3. une ligne qui commence par une date ouvre une opération ; une ligne sans
     date la prolonge (c'est souvent là que se trouve le sens : « MOTIF:Loyer »).
  4. soldes d'ouverture et de clôture repérés par le mot SOLDE + une date.
  5. contrôle : ouverture + somme des mouvements = clôture. Sans cette égalité,
     l'extraction est fausse et le relevé doit être refusé, pas importé.

Aucune dépendance au-delà de pdfplumber.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Tuple

import pdfplumber

# ── Expressions régulières communes ─────────────────────────────────────────
RE_DATE = re.compile(r"^(\d{2})[/.](\d{2})[/.](\d{2}(?:\d{2})?)$")
# 1 234,56 / 1.234,56 / 1234,56 / 12,34- (certaines banques suffixent le signe)
RE_MONTANT = re.compile(r"^[+-]?\d{1,3}(?:[ . ]\d{3})*,\d{2}[+-]?$|^[+-]?\d+,\d{2}[+-]?$")
RE_SOLDE = re.compile(r"\bSOLDE\b", re.I)
RE_TOTAUX = re.compile(r"\bTOTAU?X?\s+DES\s+MOUVEMENTS|\bTOTAL\s+DES\s+(?:MOUVEMENTS|OPERATIONS)", re.I)
RE_DEBITEUR = re.compile(r"D[ÉE]BITEUR", re.I)

# Lignes de pied de page / mentions légales : jamais des opérations.
RE_BRUIT = re.compile(
    r"garantiedesdepots|m[ée]diateur|\bRCS\b|\bTVA\b|capital\s+de|www\.|"
    r"suite\s*(au\s*verso|>>)|^page\b|protection\s+des\s+comptes|\bIBAN\b|\bBIC\b",
    re.I,
)

# En-tête de compte : « C/C CAP TRANSAT CIC N° 00010723607 en euros », « LIVRET JEUNE N° … ».
# Un même fichier contient souvent plusieurs comptes : compte courant, livret, prêt.
RE_COMPTE = re.compile(r"(?:N[°ºo]|COMPTE)\s*:?\s*([0-9][0-9 .]{6,})", re.I)


@dataclass
class Operation:
    date: date
    date_valeur: Optional[date]
    libelle: str
    montant: float            # négatif = débit, positif = crédit
    page: int
    ligne: int                # ordonnée, pour renvoyer l'utilisateur au bon endroit

    def cle(self) -> Tuple:
        """Clé de dédoublonnage entre relevés qui se chevauchent."""
        return (self.date, round(self.montant, 2), normaliser(self.libelle)[:60])


@dataclass
class CompteReleve:
    """Les opérations d'un seul compte. Un PDF en contient parfois plusieurs."""
    numero: str = ""
    libelle: str = ""
    operations: List[Operation] = field(default_factory=list)
    soldes: List[Tuple[date, float]] = field(default_factory=list)

    @property
    def solde_ouverture(self) -> Optional[float]:
        return self.soldes[0][1] if self.soldes else None

    @property
    def date_ouverture(self) -> Optional[date]:
        return self.soldes[0][0] if self.soldes else None

    @property
    def solde_cloture(self) -> Optional[float]:
        return self.soldes[-1][1] if len(self.soldes) > 1 else None

    @property
    def date_cloture(self) -> Optional[date]:
        return self.soldes[-1][0] if len(self.soldes) > 1 else None

    @property
    def somme_mouvements(self) -> float:
        return round(sum(o.montant for o in self.operations), 2)

    @property
    def ecart(self) -> Optional[float]:
        if self.solde_ouverture is None or self.solde_cloture is None:
            return None
        return round(self.solde_cloture - (self.solde_ouverture + self.somme_mouvements), 2)

    @property
    def equilibre(self) -> bool:
        return self.ecart is not None and abs(self.ecart) < 0.005

    def resume(self) -> dict:
        debits = [o for o in self.operations if o.montant < 0]
        credits = [o for o in self.operations if o.montant > 0]
        return {
            "numero": self.numero, "libelle": self.libelle,
            "periode": [self.date_ouverture, self.date_cloture],
            "solde_ouverture": self.solde_ouverture, "solde_cloture": self.solde_cloture,
            "operations": len(self.operations),
            "debits": round(sum(o.montant for o in debits), 2), "nb_debits": len(debits),
            "credits": round(sum(o.montant for o in credits), 2), "nb_credits": len(credits),
            "ecart": self.ecart, "equilibre": self.equilibre,
        }


@dataclass
class Releve:
    fichier: str
    comptes: List[CompteReleve] = field(default_factory=list)
    banque: Optional[str] = None
    anomalies: List[str] = field(default_factory=list)

    @property
    def principal(self) -> Optional[CompteReleve]:
        """Le compte le plus fourni : dans un relevé multi-comptes, c'est le compte courant."""
        return max(self.comptes, key=lambda c: len(c.operations), default=None)

    # Raccourcis vers le compte principal, pour les usages simples.
    @property
    def operations(self) -> List[Operation]:
        return self.principal.operations if self.principal else []

    @property
    def equilibre(self) -> bool:
        """Vrai si chaque compte du fichier se recalcule exactement."""
        return bool(self.comptes) and all(c.equilibre for c in self.comptes)

    def resume(self) -> dict:
        return {
            "fichier": self.fichier, "banque": self.banque,
            "comptes": [c.resume() for c in self.comptes],
            "equilibre": self.equilibre, "anomalies": self.anomalies,
        }


# ── Utilitaires ─────────────────────────────────────────────────────────────
def normaliser(texte: str) -> str:
    """Majuscules sans accents ni ponctuation : base des comparaisons de libellés."""
    t = unicodedata.normalize("NFKD", texte or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]", " ", t)).strip().upper()


def lire_montant(texte: str) -> Optional[float]:
    """« 1 234,56 », « 1.234,56 », « 12,34- » → float. Le signe du papier est rare mais existe."""
    t = texte.strip().replace(" ", " ")
    negatif = t.endswith("-") or t.startswith("-")
    t = t.strip("+-")
    t = t.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if negatif else v


def lire_date(texte: str) -> Optional[date]:
    m = RE_DATE.match(texte.strip())
    if not m:
        return None
    j, mo, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if a < 100:
        a += 2000
    try:
        return date(a, mo, j)
    except ValueError:
        return None


# ── Étape 1 : caractères → lignes ───────────────────────────────────────────
@dataclass
class Jeton:
    texte: str
    x0: float
    x1: float

    @property
    def centre(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Ligne:
    y: float
    jetons: List[Jeton]

    @property
    def texte(self) -> str:
        return " ".join(j.texte for j in self.jetons)


def _lignes_de_page(page) -> List[Ligne]:
    """Regroupe les caractères en lignes puis en jetons, en recréant les espaces manquants."""
    chars = [c for c in page.chars if c.get("text")]
    if not chars:
        return []
    tol = max(1.5, (sum(c["size"] for c in chars) / len(chars)) * 0.3)
    groupes: dict = {}
    for c in chars:
        cle = round(c["top"] / tol)
        groupes.setdefault(cle, []).append(c)

    lignes = []
    for cle in sorted(groupes):
        cs = sorted(groupes[cle], key=lambda c: c["x0"])
        jetons: List[Jeton] = []
        courant, x0, x1 = "", None, None
        for c in cs:
            # Espace explicite, ou écart suffisant pour en tenir lieu (cas Société Générale)
            separe = c["text"].isspace() or (x1 is not None and c["x0"] - x1 > 0.15 * c["size"])
            if separe and courant.strip():
                jetons.append(Jeton(courant.strip(), x0, x1))
                courant, x0 = "", None
            if c["text"].isspace():
                x1 = c["x1"]
                continue
            if x0 is None:
                x0 = c["x0"]
            courant += c["text"]
            x1 = c["x1"]
        if courant.strip():
            jetons.append(Jeton(courant.strip(), x0, x1))
        if jetons:
            lignes.append(Ligne(y=min(c["top"] for c in cs), jetons=jetons))
    return lignes


# ── Étape 2 : repérage des colonnes ─────────────────────────────────────────
@dataclass
class Colonnes:
    debit: float          # bord droit de la cellule d'en-tête « Débit »
    credit: float         # bord droit de la cellule d'en-tête « Crédit »
    gauche: float         # bord gauche du tableau : à gauche, c'est du bruit de marge
    date: float           # abscisse de la colonne Date

    @property
    def seuil(self) -> float:
        """Demi-distance entre les deux colonnes : au-delà, un montant n'appartient à aucune."""
        return max(30.0, abs(self.credit - self.debit) * 0.75)


# Mots qui prolongent l'intitulé d'une colonne de montant : « Débit EUROS », « Crédit EUR ».
RE_SUITE_ENTETE = re.compile(r"^(EUROS?|EUR|€|\(?EUROS?\)?)$", re.I)


def _cellule(lg: Ligne, i: int) -> float:
    """Bord droit de la cellule d'en-tête commençant au jeton i, suites comprises.

    Les montants sont alignés à droite sous le bord droit de leur intitulé ; si l'on
    s'arrêtait au mot « Débit » de « Débit EUROS », la référence serait décalée de
    toute la largeur de « EUROS » et les deux colonnes deviendraient indiscernables.
    """
    x1 = lg.jetons[i].x1
    for j in lg.jetons[i + 1:]:
        if j.x0 - x1 < 12 and RE_SUITE_ENTETE.match(normaliser(j.texte)):
            x1 = j.x1
        else:
            break
    return x1


def _detecter_colonnes(lignes: List[Ligne]) -> Optional[Colonnes]:
    """Cherche la ligne d'en-tête « … Débit … Crédit » et en déduit la géométrie du tableau."""
    for lg in lignes:
        deb = cre = None
        dat = None
        for i, j in enumerate(lg.jetons):
            t = normaliser(j.texte)
            if t.startswith("DEBIT") and deb is None:
                deb = _cellule(lg, i)
            elif t.startswith("CREDIT") and cre is None:
                cre = _cellule(lg, i)
            elif t == "DATE" and dat is None:
                dat = j
        if deb is not None and cre is not None and cre > deb:
            gauche = (dat.x0 if dat else lg.jetons[0].x0) - 8
            return Colonnes(debit=deb, credit=cre,
                            gauche=gauche, date=(dat.centre if dat else gauche + 20))
    return None


def _colonne_du_montant(j: Jeton, col: Colonnes) -> Optional[str]:
    """Range un montant dans la colonne dont il est le plus proche, s'il en est assez près.

    On compare les bords droits : les montants sont alignés à droite, comme les
    intitulés de colonne. Comparer les centres mélangerait « 4,80 » et « 1 234,56 ».
    """
    d_deb = abs(j.x1 - col.debit)
    d_cre = abs(j.x1 - col.credit)
    if min(d_deb, d_cre) > col.seuil:
        return None
    return "debit" if d_deb < d_cre else "credit"


# ── Étape 3 : lecture du relevé ─────────────────────────────────────────────
BANQUES = [
    ("Société Générale", r"SOCIETE GENERALE|\bSG\b|particuliers\.sg\.fr"),
    ("CIC", r"CREDIT INDUSTRIEL ET COMMERCIAL|\bCIC\b"),
    ("BoursoBank", r"BOURSOBANK|BOURSORAMA"),
    ("Crédit Agricole", r"CREDIT AGRICOLE|\bCA\b\s*BANQUE"),
    ("BNP Paribas", r"BNP PARIBAS"),
    ("Crédit Mutuel", r"CREDIT MUTUEL"),
    ("La Banque Postale", r"LA BANQUE POSTALE"),
    ("LCL", r"\bLCL\b|LE CREDIT LYONNAIS"),
    ("Caisse d'Épargne", r"CAISSE D EPARGNE"),
    ("Hello bank!", r"HELLO BANK"),
    ("Fortuneo", r"FORTUNEO"),
    ("Revolut", r"REVOLUT"),
    ("N26", r"\bN26\b"),
]


def _detecter_banque(texte: str) -> Optional[str]:
    n = normaliser(texte)
    for nom, motif in BANQUES:
        if re.search(motif, n, re.I):
            return nom
    return None


def extraire(chemin: str) -> Releve:
    """Lit un relevé PDF : opérations, soldes et contrôle d'équilibre, compte par compte."""
    rel = Releve(fichier=chemin.split("/")[-1])
    comptes: dict = {}
    courante: Optional[Operation] = None
    col: Optional[Colonnes] = None
    entete: List[str] = []
    actif: Optional[CompteReleve] = None

    def compte(numero: str = "", libelle: str = "") -> CompteReleve:
        """Section de compte en cours ; créée à la première rencontre de son numéro."""
        cle = re.sub(r"\D", "", numero)
        if cle not in comptes:
            comptes[cle] = CompteReleve(numero=numero.strip(), libelle=libelle.strip())
            rel.comptes.append(comptes[cle])
        return comptes[cle]

    with pdfplumber.open(chemin) as pdf:
        for n_page, page in enumerate(pdf.pages, start=1):
            lignes = _lignes_de_page(page)
            if not lignes:
                rel.anomalies.append(f"page {n_page} sans texte : relevé scanné ou protégé")
                continue
            if n_page <= 2:
                entete.append(" ".join(lg.texte for lg in lignes[:40]))
            trouvee = _detecter_colonnes(lignes)
            if trouvee:
                col = trouvee
            if col is None:
                continue                      # en-tête pas encore rencontré : page de garde

            for lg in lignes:
                # Bornes du tableau : à gauche et à droite se trouvent les codes
                # d'impression verticaux (Société Générale), qui pollueraient les libellés.
                jetons = [j for j in lg.jetons if j.x1 > col.gauche and j.x0 < col.credit + 25]
                if not jetons:
                    continue
                texte = " ".join(j.texte for j in jetons)

                montants = []
                for j in jetons:
                    if RE_MONTANT.match(j.texte):
                        c = _colonne_du_montant(j, col)
                        if c:
                            v = lire_montant(j.texte)
                            if v is not None:
                                montants.append((c, v))

                # ── Début d'opération : une date dans la colonne de gauche ─
                # Ce test passe avant le filtre de bruit : une ligne datée et chiffrée
                # est une opération, même si son libellé contient un mot « suspect »
                # (« LES ARCS » renferme RCS, « PHARMACIE » renferme ACI…).
                jd = next((j for j in jetons if lire_date(j.texte) and j.x0 < col.date + 40), None)
                if jd and montants and not RE_SOLDE.search(texte) and not RE_TOTAUX.search(texte):
                    dates = [d for d in (lire_date(j.texte) for j in jetons
                                         if j.x0 < col.date + 80) if d]
                    libelle = " ".join(
                        j.texte for j in jetons
                        if not RE_MONTANT.match(j.texte) and not lire_date(j.texte)
                    )
                    c, v = montants[-1]
                    actif = actif or compte()
                    courante = Operation(
                        date=dates[0], date_valeur=dates[1] if len(dates) > 1 else None,
                        libelle=libelle.strip(), montant=-abs(v) if c == "debit" else abs(v),
                        page=n_page, ligne=int(lg.y),
                    )
                    actif.operations.append(courante)
                    continue

                # ── Ligne de solde ────────────────────────────────────────
                if RE_SOLDE.search(texte) and montants:
                    d = next((lire_date(j.texte) for j in jetons if lire_date(j.texte)), None)
                    if d is None:   # certaines banques collent la date au mot : SOLDEAU09/03/2024
                        m = re.search(r"(\d{2}[/.]\d{2}[/.]\d{2,4})", texte)
                        d = lire_date(m.group(1)) if m else None
                    c, v = montants[-1]
                    v = -abs(v) if (c == "debit" or RE_DEBITEUR.search(texte)) else abs(v)
                    if d:
                        actif = actif or compte()
                        actif.soldes.append((d, v))
                    courante = None
                    continue

                # ── Changement de compte dans le même fichier ─────────────
                m = RE_COMPTE.search(texte)
                if m and not montants and not jd:
                    libelle = texte[:m.start()].strip(" :-")
                    actif = compte(m.group(1), libelle)
                    courante = None
                    continue

                if RE_TOTAUX.search(texte) or RE_BRUIT.search(texte):
                    courante = None
                    continue

                # ── Suite du libellé ──────────────────────────────────────
                if courante is not None and not montants and not jd:
                    suite = " ".join(j.texte for j in jetons)
                    if suite and len(courante.libelle) < 300:
                        courante.libelle = (courante.libelle + " " + suite).strip()

    rel.banque = _detecter_banque(" ".join(entete))
    for c in rel.comptes:
        if len(c.soldes) < 2:
            rel.anomalies.append(
                f"compte {c.numero or '?'} : soldes d'ouverture et de clôture introuvables")
    if not rel.comptes or not any(c.operations for c in rel.comptes):
        rel.anomalies.append("aucune opération détectée")
    return rel
