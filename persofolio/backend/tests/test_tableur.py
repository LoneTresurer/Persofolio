"""
Relevés tableur (.xls, .xlsx, .csv) : lecture, en-têtes, erreurs, import complet.

    cd persofolio/backend && python -m unittest tests.test_tableur -v

Fichiers d'essai inventés : tests/tableurs/, fabriqués par
outils/essai/generer_tableurs.py. Le .xls demande le module xlrd (installé par
requirements.txt) ; s'il manque, ces essais-là sont sautés, pas réussis.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import tableur                                  # noqa: E402
from core.import_releves import lire_fichiers             # noqa: E402

ICI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tableurs")
lire_octets = lambda nom: open(os.path.join(ICI, nom), "rb").read()
try:
    import xlrd  # noqa: F401
    XLRD = True
except ImportError:
    XLRD = False


class CompteCarte(unittest.TestCase):
    """Export d'un compte carte à débit différé : la structure du relevé reçu le 17 novembre."""

    def verifier(self, nom):
        r = tableur.lire(lire_octets(nom), nom)
        ops = r.compte.operations
        self.assertEqual(len(ops), 16)                     # 18 lignes − 2 règlements mensuels
        self.assertEqual([round(m, 2) for _, m, _ in r.exclues], [512.44, 301.75])
        self.assertAlmostEqual(sum(o.montant for o in ops), -938.48, places=2)
        self.assertEqual(r.periode[0].isoformat(), "2026-08-26")
        self.assertEqual(r.periode[1].isoformat(), "2026-11-03")
        self.assertTrue(all(o.montant < 0 for o in ops))   # aucun « revenu » fantôme
        par = {o.reference: o for o in ops}
        self.assertEqual(par["9100002"].categorie_banque, "sorties")      # Alimentation / Restaurant
        self.assertEqual(par["9100003"].categorie_banque, "courses")      # Alimentation / Supermarché
        self.assertEqual(par["9100005"].categorie_banque, "sante")
        self.assertEqual(par["9100006"].categorie_banque, "transport")
        self.assertEqual(par["9100007"].categorie_banque, "loisirs")
        self.assertIsNone(par["9100001"].categorie_banque)                 # « A catégoriser »
        self.assertEqual(par["9100002"].libelle, "Carte · Alimentation · Restaurant")
        self.assertEqual(sum(1 for o in ops if o.montant == -64.12), 2)  # deux achats identiques, gardés
        self.assertEqual(r.colonnes["montant"], "Montant")
        return r

    def test_xlsx(self):
        self.assertEqual(self.verifier("carte.xlsx").format, "xlsx")

    def test_csv(self):
        self.assertEqual(self.verifier("carte.csv").format, "csv")

    @unittest.skipUnless(XLRD, "module xlrd absent")
    def test_xls(self):
        self.assertEqual(self.verifier("carte.xls").format, "xls")


class AutresBanques(unittest.TestCase):
    def test_titre_au_dessus_et_debit_credit(self):
        r = tableur.lire(lire_octets("debit_credit.csv"), "debit_credit.csv")
        ops = r.compte.operations
        self.assertEqual([(o.date.isoformat(), o.montant) for o in ops], [
            ("2026-10-01", 2150.0), ("2026-10-03", -750.0), ("2026-10-13", -4.2),
            ("2026-10-13", -4.2), ("2026-10-20", 18.4)])
        self.assertEqual(ops[0].libelle, "VIR SALAIRE ENTREPRISE EXEMPLE")
        self.assertEqual(r.exclues, [])

    def test_colonne_manquante_nommee(self):
        with self.assertRaises(tableur.ColonnesManquantes) as ctx:
            tableur.lire(lire_octets("sans_montant.csv"), "sans_montant.csv")
        msg = str(ctx.exception)
        self.assertIn("Montant (ou bien deux colonnes Débit et Crédit)", msg)
        self.assertIn("Date d'opération, Libellé, Catégorie", msg)

    def test_debit_sans_credit(self):
        csv = "Date;Libellé;Débit\n01/10/2026;CB X;12,00\n".encode()
        with self.assertRaises(tableur.ColonnesManquantes) as ctx:
            tableur.lire(csv, "x.csv")
        self.assertIn("Crédit", str(ctx.exception))

    def test_en_tetes_variantes(self):
        csv = "DATE OPERATION,LIBELLE DE L'OPERATION,MONTANT (EUR)\n2026-10-05,CB Y,\"-1 234,50\"\n".encode()
        ops = tableur.lire(csv, "y.csv").compte.operations
        self.assertEqual((ops[0].date.isoformat(), ops[0].montant, ops[0].libelle), ("2026-10-05", -1234.5, "CB Y"))

    def test_format_inconnu(self):
        self.assertIsNone(tableur.format_de(b"%PDF-1.7", "releve.pdf"))


class Montants(unittest.TestCase):
    def test_formes(self):
        for texte, attendu in [("-27,00", -27.0), ("1 234,56 €", 1234.56), ("1,234.56", 1234.56),
                               ("(27,00)", -27.0), ("27,00-", -27.0), ("+5", 5.0), (12, 12.0), ("", None), ("abc", None)]:
            self.assertEqual(tableur.lire_montant(texte), attendu, texte)


class ImportComplet(unittest.TestCase):
    """Le même chemin que la route POST /banque/releves."""

    def test_tableur_seul(self):
        r = lire_fichiers([("carte.xlsx", lire_octets("carte.xlsx"))], {})
        f = r["fichiers"][0]
        c = f["comptes"][0]
        self.assertTrue(c["tableur"])
        self.assertIsNone(c["solde_cloture"])
        self.assertEqual(c["nb_operations"], 16)
        self.assertTrue(any("règlement mensuel" in a for a in f["anomalies"]))
        p = r["proposition"]
        self.assertEqual(p["mois_complets"], ["2026-09", "2026-10"])
        tiers = {t["libelle"]: t["categorie"] for t in p["tiers"]}
        self.assertEqual(tiers["Carte · Alimentation · Restaurant"], "sorties")

    def test_correction_utilisateur_prioritaire(self):
        r = lire_fichiers([("carte.xlsx", lire_octets("carte.xlsx"))],
                          {"CARTE ALIMENTATION RESTAURANT": "divers"})
        tiers = {t["libelle"]: t["categorie"] for t in r["proposition"]["tiers"]}
        self.assertEqual(tiers["Carte · Alimentation · Restaurant"], "divers")

    def test_erreur_par_fichier_sans_bloquer_les_autres(self):
        r = lire_fichiers([("sans_montant.csv", lire_octets("sans_montant.csv")),
                           ("debit_credit.csv", lire_octets("debit_credit.csv"))], {})
        self.assertIn("Colonne introuvable", r["fichiers"][0]["erreur"])
        self.assertEqual(r["fichiers"][1]["comptes"][0]["nb_operations"], 5)
        self.assertIsNotNone(r["proposition"])

    def test_fichier_inconnu(self):
        r = lire_fichiers([("photo.png", b"\x89PNG....")], {})
        self.assertIn("format non reconnu", r["fichiers"][0]["erreur"])


if __name__ == "__main__":
    unittest.main()
