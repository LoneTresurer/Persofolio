"""
tableur.py
==========
Relevés bancaires exportés en tableur : Excel (.xls, .xlsx) ou CSV.

Une autre porte d'entrée que le PDF, pour les mêmes opérations. Le résultat a la
forme d'un compte de relevé PDF (`CompteReleve`) : la suite — classement,
regroupement par tiers, récurrences, écran de relecture — ne voit pas la
différence.

**Ce qu'un tableur ne dit pas.** Un relevé PDF porte un solde d'ouverture et un
solde de clôture : s'ils ne se recalculent pas au centime avec les opérations, la
lecture est fausse et le relevé est écarté. Un export tableur n'a, le plus
souvent, aucun solde. Ce contrôle n'existe donc pas : les opérations sont
reprises telles que la banque les a exportées, et c'est le solde du jour, saisi
par l'utilisateur, qui fixe le point d'arrivée.

**Des en-têtes qui varient d'une banque à l'autre.** « Date d'opération »,
« Date », « DATE OPERATION » ; « Montant », « Montant (EUR) », ou bien deux
colonnes « Débit » et « Crédit ». Les en-têtes sont comparés sans casse, sans
accents ni ponctuation, à une liste de variantes connues, et la ligne d'en-tête
est cherchée dans les premières lignes : beaucoup d'exports commencent par un
titre ou un numéro de compte. Deux informations sont indispensables — la date et
le montant (ou débit et crédit) ; si l'une manque, l'erreur la nomme et liste
les colonnes trouvées.

**Le règlement mensuel d'une carte à débit différé.** L'export d'un compte carte
contient chaque achat, puis une ligne positive « DEBIT MENSUEL CARTE » qui solde
le mois : c'est le compte courant qui rembourse la carte. Ce n'est pas un revenu ;
la compter comme tel gonflerait les entrées du montant de toutes les dépenses
carte. Ces lignes sont écartées, et le relevé le dit.

**Les catégories de la banque.** Quand l'export en fournit (« Alimentation /
Supermarché »), elles sont traduites dans les catégories de l'application et
servent de proposition — avant le classement par nom de commerçant, qui ne sait
rien faire d'un libellé sans commerçant, mais jamais avant une correction faite
par l'utilisateur.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from core.releve import CompteReleve, Operation

LIGNES_EXPLOREES = 30            # où chercher la ligne d'en-tête
TAILLE_MAX = 10 * 1024 * 1024


class TableurIllisible(ValueError):
    """Le fichier n'a pas pu être lu comme un tableur."""


class ColonnesManquantes(ValueError):
    """Une colonne indispensable manque : le message la nomme."""

    def __init__(self, manquantes: List[str], trouvees: List[str]):
        self.manquantes, self.trouvees = manquantes, trouvees
        liste = ", ".join(t for t in trouvees if t) or "aucune"
        super().__init__(
            f"Colonne introuvable : {' ; '.join(manquantes)}. "
            f"Colonnes trouvées dans le fichier : {liste}. "
            "Renommez la colonne concernée dans votre fichier, ou exportez-le depuis "
            "l'espace client de votre banque avec ces informations.")


# ── En-têtes ────────────────────────────────────────────────────────────────
def normaliser(texte) -> str:
    """« Date d'opération » → « date d operation » : sans accents, casse ni ponctuation."""
    t = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t.lower()).split())


# Champ → variantes connues (déjà normalisées). L'ordre des champs compte : une
# colonne n'est attribuée qu'une fois, au premier champ qui la reconnaît.
VARIANTES: Dict[str, Tuple[str, ...]] = {
    "date": ("date", "date operation", "date d operation", "date de l operation", "date comptable",
             "date de comptabilisation", "date op", "date d execution", "date transaction",
             "transaction date", "booking date", "jour"),
    "debit": ("debit", "debits", "debit eur", "debit euros", "montant debit", "sortie", "sorties"),
    "credit": ("credit", "credits", "credit eur", "credit euros", "montant credit", "entree", "entrees"),
    "montant": ("montant", "montant eur", "montant euros", "montant en euros", "montant en eur",
                "montant operation", "montant de l operation", "somme", "amount", "valeur eur"),
    "libelle": ("libelle", "libelles", "libelle operation", "libelle de l operation", "libelle simplifie",
                "description", "intitule", "label", "beneficiaire", "tiers", "nom", "operation"),
    "type": ("type", "type operation", "type de l operation", "nature", "nature de l operation"),
    "sous_categorie": ("sous categorie", "sous categories", "subcategory", "sub category"),
    "categorie": ("categorie", "categories", "category", "rubrique"),
    "reference": ("reference", "reference de l operation", "ref", "numero", "numero d operation"),
    "detail": ("commentaire", "commentaires", "detail", "details", "detail 1", "detail 2", "detail 3",
               "complement", "informations complementaires", "memo"),
    "date_valeur": ("date de valeur", "date valeur", "value date"),
}
NOMS = {"date": "Date (ou « Date d'opération »)", "montant": "Montant (ou bien deux colonnes Débit et Crédit)"}


def _reconnaitre(entetes: Sequence) -> Dict[str, List[int]]:
    """Colonnes reconnues : {champ: [indices]}. « detail » peut en avoir plusieurs."""
    norm = [normaliser(h) for h in entetes]
    pris, res = set(), {}

    def attribuer(champ, i):
        pris.add(i)
        res.setdefault(champ, []).append(i)

    for passe in ("exact", "prefixe"):
        for champ, variantes in VARIANTES.items():
            if champ in res and champ != "detail":
                continue
            for i, h in enumerate(norm):
                if i in pris or not h:
                    continue
                ok = h in variantes if passe == "exact" else any(h.startswith(v + " ") for v in variantes)
                if ok:
                    attribuer(champ, i)
                    if champ != "detail":
                        break
    # « Date de valeur » ne sert de date qu'à défaut d'une date d'opération.
    if "date" not in res and "date_valeur" in res:
        res["date"] = res.pop("date_valeur")
    return res


def _indispensables(cols: Dict[str, List[int]]) -> List[str]:
    manque = []
    if "date" not in cols:
        manque.append(NOMS["date"])
    if "montant" not in cols and not ("debit" in cols and "credit" in cols):
        if "debit" in cols or "credit" in cols:
            manque.append("Crédit" if "debit" in cols else "Débit")
        else:
            manque.append(NOMS["montant"])
    return manque


# ── Valeurs ─────────────────────────────────────────────────────────────────
def lire_montant(v) -> Optional[float]:
    """Nombre, ou texte : « -27,00 », « 1 234,56 € », « 1,234.56 », « (27,00) », « 27,00- »."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace(" ", "").replace("\xa0", "").replace(" ", "").replace("€", "").replace("EUR", "")
    if not t:
        return None
    negatif = False
    if t.startswith("(") and t.endswith(")"):
        negatif, t = True, t[1:-1]
    if t.endswith("-"):
        negatif, t = True, t[:-1]
    t = t.replace("+", "")
    if t.startswith("-"):
        negatif, t = not negatif, t[1:]
    if "," in t and "." in t:                      # le dernier séparateur est le décimal
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        n = float(t)
    except ValueError:
        return None
    return -n if negatif else n


RE_DATE = re.compile(r"^\s*(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})")


def _morceaux_date(v) -> Optional[Tuple[int, int, int]]:
    m = RE_DATE.match(str(v))
    return tuple(int(x) for x in m.groups()) if m else None


def _ordre_dates(valeurs: Sequence) -> str:
    """« jma » (français) ou « mja » (américain), déduit de toute la colonne.

    Un « 13/02 » ne peut être que jour/mois, un « 02/13 » que mois/jour. Sans
    indice dans un sens ou dans l'autre, la convention française l'emporte.
    """
    jour_d_abord = mois_d_abord = False
    for v in valeurs:
        if isinstance(v, (date, datetime)):
            continue
        p = _morceaux_date(v)
        if not p or p[0] > 31:                     # forme ISO aaaa-mm-jj : pas d'ambiguïté
            continue
        if p[0] > 12:
            jour_d_abord = True
        if p[1] > 12:
            mois_d_abord = True
    return "mja" if mois_d_abord and not jour_d_abord else "jma"


def lire_date(v, ordre: str = "jma") -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < v < 80000:     # numéro de série Excel
        return date(1899, 12, 30) + timedelta(days=int(v))
    p = _morceaux_date(v)
    if not p:
        return None
    a, b, c = p
    try:
        if a > 31:                                  # aaaa-mm-jj
            return date(a, b, c)
        annee = c + 2000 if c < 100 else c
        return date(annee, b, a) if ordre == "jma" else date(annee, a, b)
    except ValueError:
        return None


def _texte(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


# ── Catégories de la banque ─────────────────────────────────────────────────
# Mots de la catégorie ou de la sous-catégorie de la banque → catégorie de
# l'application. La sous-catégorie est essayée d'abord : « Alimentation /
# Restaurant » est un restaurant, pas des courses. Ce qui n'est pas reconnu
# (« Autres », « A catégoriser », « Hors budget ») reste à classer.
REGLES_SORTIE = [
    (r"restaurant|restauration|bar |bars|cafe|fast food|sortie", "sorties"),
    (r"supermarche|hypermarche|alimentation|courses|epicerie|boulangerie|primeur", "courses"),
    (r"medecin|sante|pharmac|frais medicaux|mutuelle|optique|dentaire|dentiste|hopital|kine", "sante"),
    (r"carburant|essence|gasoil|peage|parking|voiture|vehicule|auto moto|transport|train|sncf|metro|taxi|vtc|avion|velo", "transport"),
    (r"vacances|week end|voyage|hotel|loisir|sport|culture|spectacle|cinema|musee|jeux", "loisirs"),
    (r"loyer|logement|habitation|immobilier|travaux|bricolage|jardin|charges de copropriete", "logement"),
    (r"electricite|gaz|energie|eau|telephone|internet|mobile|telecom|box", "factures"),
    (r"abonnement|streaming|presse", "abonnements"),
    (r"impot|taxe|amende|tresor public", "impots"),
    (r"frais bancaire|commission|agios|cotisation", "frais"),
    (r"retrait|especes|distributeur", "especes"),
    (r"vetement|habillement|chaussure|shopping|high tech|multimedia|electromenager|cadeau|achats", "shopping"),
    (r"pret|credit|emprunt|echeance", "credit"),
]
REGLES_ENTREE = [
    (r"salaire|paie|traitement|remuneration", "salaire"),
    (r"allocation|caf|aide|pole emploi|france travail|bourse|pension", "aides"),
    (r"remboursement|rembourse|avoir|retour", "rembours"),
    (r"prime|interet|dividende|revenu|loyer percu|vente", "complement"),
]


def categorie_banque(categorie: str, sous_categorie: str, montant: float) -> Optional[str]:
    regles = REGLES_ENTREE if montant > 0 else REGLES_SORTIE
    for texte in (sous_categorie, categorie):
        t = " " + normaliser(texte) + " "
        if not t.strip():
            continue
        for motif, cat in regles:
            if re.search(motif, t):
                return cat
    return None


# Règlement mensuel d'une carte à débit différé : un virement du compte courant
# vers la carte, pas un revenu.
RE_REGLEMENT_CARTE = re.compile(
    r"\b(debit|releve|facture|prelevement|reglement|solde) (mensuel |differe )?(de |de la |du )?(carte|cb)\b")


def euros(v: float) -> str:
    """1214.89 → « 1 214,89 € », comme dans le reste de l'application."""
    return f"{v:,.2f}".replace(",", "\u202f").replace(".", ",") + " €"


@dataclass
class LectureTableur:
    compte: CompteReleve
    exclues: List[Tuple[date, float, str]] = field(default_factory=list)
    illisibles: int = 0
    colonnes: Dict[str, str] = field(default_factory=dict)       # champ → en-tête d'origine
    format: str = ""

    @property
    def periode(self) -> Optional[Tuple[date, date]]:
        d = [o.date for o in self.compte.operations]
        return (min(d), max(d)) if d else None

    def anomalies(self) -> List[str]:
        a = []
        if self.exclues:
            total = sum(m for _, m, _ in self.exclues)
            a.append(f"{len(self.exclues)} ligne{'s' if len(self.exclues) > 1 else ''} « règlement mensuel de la "
                     f"carte » écartée{'s' if len(self.exclues) > 1 else ''} ({euros(total)}) : c'est le compte "
                     "courant qui rembourse la carte, pas un revenu. Les achats carte, eux, sont bien repris.")
        if self.illisibles:
            a.append(f"{self.illisibles} ligne{'s' if self.illisibles > 1 else ''} sans date ou sans montant "
                     "lisible, ignorée" + ("s" if self.illisibles > 1 else "") + ".")
        a.append("Tableur : pas de soldes à contrôler. Vérifiez le solde du jour proposé plus bas.")
        return a


# ── Lecture des formats ─────────────────────────────────────────────────────
def _lignes_xls(contenu: bytes) -> List[list]:
    try:
        import xlrd
    except ImportError as e:                                   # pragma: no cover
        raise TableurIllisible("lecture des fichiers .xls indisponible (module xlrd absent)") from e
    try:
        livre = xlrd.open_workbook(file_contents=contenu)
    except Exception as e:                                     # noqa: BLE001
        raise TableurIllisible("fichier Excel (.xls) illisible") from e
    feuille = max(livre.sheets(), key=lambda f: f.nrows)
    lignes = []
    for r in range(feuille.nrows):
        ligne = []
        for c in range(feuille.ncols):
            cel = feuille.cell(r, c)
            if cel.ctype == xlrd.XL_CELL_DATE:
                try:
                    ligne.append(xlrd.xldate_as_datetime(cel.value, livre.datemode))
                except Exception:                              # noqa: BLE001
                    ligne.append(cel.value)
            elif cel.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                ligne.append(None)
            else:
                ligne.append(cel.value)
        lignes.append(ligne)
    return lignes


def _lignes_xlsx(contenu: bytes) -> List[list]:
    try:
        import openpyxl
    except ImportError as e:                                   # pragma: no cover
        raise TableurIllisible("lecture des fichiers .xlsx indisponible (module openpyxl absent)") from e
    try:
        livre = openpyxl.load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    except Exception as e:                                     # noqa: BLE001
        raise TableurIllisible("fichier Excel (.xlsx) illisible") from e
    feuilles = [[list(r) for r in f.iter_rows(values_only=True)] for f in livre.worksheets]
    livre.close()
    return max(feuilles, key=len) if feuilles else []


def _lignes_csv(contenu: bytes) -> List[list]:
    for codage in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texte = contenu.decode(codage)
            break
        except UnicodeDecodeError:
            continue
    else:                                                      # pragma: no cover
        raise TableurIllisible("fichier texte illisible")
    echantillon = texte[:20000]
    try:
        dialecte = csv.Sniffer().sniff(echantillon, delimiters=";,\t|")
        sep = dialecte.delimiter
    except csv.Error:
        sep = ";" if echantillon.count(";") >= echantillon.count(",") else ","
    return [ligne for ligne in csv.reader(io.StringIO(texte), delimiter=sep)]


def format_de(contenu: bytes, nom: str = "") -> Optional[str]:
    """« xls », « xlsx », « csv », ou None si ce n'est pas un tableur."""
    n = (nom or "").lower()
    if contenu[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "xls"
    if contenu[:4] == b"PK\x03\x04" and (n.endswith((".xlsx", ".xlsm")) or b"xl/" in contenu[:2000]):
        return "xlsx"
    if n.endswith((".csv", ".txt", ".tsv")):
        return "csv"
    # Certaines banques livrent un « .xls » qui n'est qu'un texte séparé par des tabulations.
    if n.endswith(".xls") and b"\x00" not in contenu[:2000]:
        return "csv"
    return None


# ── Extraction ──────────────────────────────────────────────────────────────
def lire(contenu: bytes, nom: str = "") -> LectureTableur:
    fmt = format_de(contenu, nom)
    if fmt is None:
        raise TableurIllisible("format non reconnu (formats acceptés : PDF, .xls, .xlsx, .csv)")
    lignes = {"xls": _lignes_xls, "xlsx": _lignes_xlsx, "csv": _lignes_csv}[fmt](contenu)
    lignes = [l for l in lignes if any(_texte(v) for v in l)]
    if not lignes:
        raise TableurIllisible("le fichier est vide")

    # La ligne d'en-tête : la première qui contient les colonnes indispensables.
    # À défaut, la plus prometteuse sert à nommer ce qui manque.
    meilleure, meilleur_score = 0, -1
    for i, ligne in enumerate(lignes[:LIGNES_EXPLOREES]):
        cols = _reconnaitre(ligne)
        if not _indispensables(cols):
            meilleure, meilleur_score = i, 999
            break
        score = len(cols)
        if score > meilleur_score:
            meilleure, meilleur_score = i, score
    entetes = lignes[meilleure]
    cols = _reconnaitre(entetes)
    manque = _indispensables(cols)
    if manque:
        raise ColonnesManquantes(manque, [_texte(h) for h in entetes])

    un = lambda champ: cols[champ][0] if champ in cols else None
    i_date, i_mont, i_deb, i_cred = un("date"), un("montant"), un("debit"), un("credit")
    i_lib, i_type, i_cat, i_scat, i_ref = un("libelle"), un("type"), un("categorie"), un("sous_categorie"), un("reference")
    i_det = cols.get("detail", [])
    corps = lignes[meilleure + 1:]
    val = lambda l, i: l[i] if i is not None and i < len(l) else None
    ordre = _ordre_dates([val(l, i_date) for l in corps])

    lecture = LectureTableur(compte=CompteReleve(numero=f"tableur:{nom}"[:80], libelle="Relevé tableur"),
                             format=fmt,
                             colonnes={c: _texte(entetes[ix[0]]) for c, ix in cols.items()})
    for n, l in enumerate(corps, start=meilleure + 2):
        d = lire_date(val(l, i_date), ordre)
        if i_mont is not None:
            m = lire_montant(val(l, i_mont))
        else:
            deb, cred = lire_montant(val(l, i_deb)), lire_montant(val(l, i_cred))
            m = None if deb is None and cred is None else (cred or 0.0) - abs(deb or 0.0)
        if d is None or m is None:
            if any(_texte(v) for v in l):
                lecture.illisibles += 1
            continue
        if abs(m) < 0.005:
            continue
        typ, cat, scat = _texte(val(l, i_type)), _texte(val(l, i_cat)), _texte(val(l, i_scat))
        details = [t for t in (_texte(val(l, i)) for i in i_det) if t]
        lib = _texte(val(l, i_lib))
        if not lib:
            morceaux = [typ.capitalize() if typ.isupper() else typ]
            morceaux += [x for x in (cat, scat) if x and normaliser(x) not in ("a categoriser", "autres", "autre")]
            lib = " · ".join(dict.fromkeys(x for x in morceaux if x))
        if details:
            lib = f"{lib} — {' '.join(details)}" if lib else " ".join(details)
        lib = lib or "Opération"
        if m > 0 and RE_REGLEMENT_CARTE.search(normaliser(f"{typ} {lib}")):
            lecture.exclues.append((d, round(m, 2), lib))
            continue
        op = Operation(date=d, date_valeur=None, libelle=lib, montant=round(m, 2), page=0, ligne=n)
        op.categorie_banque = categorie_banque(cat, scat, m)
        op.reference = _texte(val(l, i_ref)) or None
        lecture.compte.operations.append(op)

    lecture.compte.operations.sort(key=lambda o: (o.date, o.ligne))
    if not lecture.compte.operations:
        raise TableurIllisible("aucune opération lisible sous la ligne d'en-tête")
    return lecture
