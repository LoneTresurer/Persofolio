"""
Fabrique les relevés tableur d'essai, dans persofolio/backend/tests/tableurs/.

    python outils/essai/generer_tableurs.py

Des données **inventées**, jamais un vrai relevé : le dépôt est public. La
structure, elle, reprend fidèlement de vrais exports :

- carte.xlsx / carte.csv / carte.xls : export d'un compte carte à débit différé
  (en-têtes « Date d'opération », « Montant »… ; montants en texte avec virgule ;
  deux lignes « DEBIT MENSUEL CARTE BLEUE » ; deux achats identiques le même jour) ;
- debit_credit.csv : titre et numéro de compte au-dessus des en-têtes, colonnes
  Débit et Crédit séparées, dates en texte jj/mm/aaaa, séparateur « ; » ;
- sans_montant.csv : aucune colonne de montant — doit être refusé en le disant.

Le .xls est produit par LibreOffice (soffice) à partir du .xlsx ; sans lui, il
n'est pas régénéré.
"""

import csv
import os
import shutil
import subprocess
import tempfile
from datetime import datetime

import openpyxl

ICI = os.path.dirname(os.path.abspath(__file__))
SORTIE = os.path.join(ICI, "..", "..", "persofolio", "backend", "tests", "tableurs")
os.makedirs(SORTIE, exist_ok=True)

ENTETES = ["Date d'opération", "Référence de l'opération", "Type de l'opération", "Catégorie",
           "Sous catégorie", "Montant", "Commentaire", "Détail 1", "Détail 2", "Détail 3"]
CARTE = [
    ("2026-11-03", "9100001", "CARTE", "A catégoriser", "A catégoriser", "-12,50"),
    ("2026-10-21", "9100002", "CARTE", "Alimentation", "Restaurant", "-38,40"),
    ("2026-10-18", "9100003", "CARTE", "Alimentation", "Supermarché", "-64,12"),
    ("2026-10-18", "9100004", "CARTE", "Alimentation", "Supermarché", "-64,12"),   # même achat, deux fois
    ("2026-10-15", "9100005", "CARTE", "Santé", "Médecins, Frais médicaux", "-30,00"),
    ("2026-10-12", "09/2026", "DEBIT MENSUEL CARTE BLEUE", "A catégoriser", "A catégoriser", "512,44"),
    ("2026-10-09", "9100006", "CARTE", "Transport", "Voiture", "-55,00"),
    ("2026-10-04", "9100007", "CARTE", "Loisirs", "Week-end, vacances", "-120,00"),
    ("2026-10-02", "9100008", "CARTE", "Autres Dépenses", "Autres", "-19,99"),
    ("2026-09-28", "9100009", "CARTE", "Alimentation", "Supermarché", "-82,30"),
    ("2026-09-24", "9100010", "CARTE", "Alimentation", "Restaurant", "-27,50"),
    ("2026-09-19", "9100011", "CARTE", "Transport", "Voiture", "-48,20"),
    ("2026-09-15", "9100012", "CARTE", "Vie quotidienne", "Education et Famille", "-60,00"),
    ("2026-09-10", "08/2026", "DEBIT MENSUEL CARTE BLEUE", "A catégoriser", "A catégoriser", "301,75"),
    ("2026-09-06", "9100013", "CARTE", "Alimentation", "Supermarché", "-71,45"),
    ("2026-09-02", "9100014", "CARTE", "Loisirs", "Week-end, vacances", "-210,00"),
    ("2026-08-29", "9100015", "CARTE", "Santé", "Médecins, Frais médicaux", "-25,00"),
    ("2026-08-26", "9100016", "CARTE", "", "", "-9,90"),
]


def carte_xlsx(chemin):
    livre = openpyxl.Workbook()
    f = livre.active
    f.title = "Operations"
    f.append(ENTETES)
    for d, ref, typ, cat, scat, m in CARTE:
        f.append([datetime.fromisoformat(d), ref, typ.ljust(20) if typ == "CARTE" else typ, cat, scat, m])
        f.cell(f.max_row, 1).number_format = "m/d/yyyy"
    livre.save(chemin)


def carte_csv(chemin):
    with open(chemin, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(ENTETES)
        for d, ref, typ, cat, scat, m in CARTE:
            a, mo, j = d.split("-")
            w.writerow([f"{j}/{mo}/{a}", ref, typ, cat, scat, m])


def debit_credit_csv(chemin):
    with open(chemin, "w", newline="", encoding="cp1252") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Banque d'essai — export des opérations"])
        w.writerow(["Compte courant n° 00000000000"])
        w.writerow([])
        w.writerow(["Date", "Libellé", "Débit", "Crédit"])
        w.writerow(["01/10/2026", "VIR SALAIRE ENTREPRISE EXEMPLE", "", "2 150,00"])
        w.writerow(["03/10/2026", "PRLV LOYER", "750,00", ""])
        w.writerow(["13/10/2026", "CB BOULANGERIE DU PORT", "4,20", ""])
        w.writerow(["13/10/2026", "CB BOULANGERIE DU PORT", "4,20", ""])
        w.writerow(["20/10/2026", "REMBOURSEMENT MUTUELLE", "", "18,40"])


def sans_montant_csv(chemin):
    with open(chemin, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Date d'opération", "Libellé", "Catégorie"])
        w.writerow(["01/10/2026", "CB EPICERIE", "Alimentation"])


carte_xlsx(os.path.join(SORTIE, "carte.xlsx"))
carte_csv(os.path.join(SORTIE, "carte.csv"))
debit_credit_csv(os.path.join(SORTIE, "debit_credit.csv"))
sans_montant_csv(os.path.join(SORTIE, "sans_montant.csv"))
if shutil.which("soffice"):
    tmp = tempfile.mkdtemp()
    subprocess.run(["soffice", "--headless", "--convert-to", "xls", os.path.join(SORTIE, "carte.xlsx"),
                    "--outdir", tmp], check=True, capture_output=True, timeout=120)
    shutil.move(os.path.join(tmp, "carte.xls"), os.path.join(SORTIE, "carte.xls"))
print("écrits dans", os.path.abspath(SORTIE), ":", sorted(os.listdir(SORTIE)))
