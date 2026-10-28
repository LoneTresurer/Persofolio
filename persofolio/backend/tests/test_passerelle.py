"""
Contrôles de la passerelle budget <-> compte-titres, sur une vraie base SQLite.

Ne demande ni FastAPI ni réseau : on appelle `core.passerelle` directement.

    cd persofolio/backend && python -m unittest tests.test_passerelle -v

Le premier test reproduit le défaut d'octobre 2026 : répondre à « d'où venait cet
argent ? » enregistrait un versement daté du jour, qui ne couvrait aucun achat
passé et s'ajoutait aux espèces — à chaque clic.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import passerelle                       # noqa: E402
from core.banque_db import BanqueDB               # noqa: E402
from core.portfolio_db import PortfolioDB         # noqa: E402

U = "pierre"
ETAT = {"comptes": {"courant": {"soldeInitial": 1000}, "invest": {"soldeInitial": 0}},
        "operations": [], "programmees": [], "dettes": [], "objectifs": [], "budgets": {}}


def achat(date, ticker, q, prix, frais=0.0):
    return {"date": date, "type": "ACHAT", "ticker": ticker, "quantite": q, "prix": prix,
            "devise": "EUR", "taux_eur": 1.0, "frais_eur": frais, "montant_eur": q * prix}


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.chemin = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._ancien = passerelle.DB_PATH
        passerelle.DB_PATH = self.chemin
        self.pdb = PortfolioDB(self.chemin)
        BanqueDB(self.chemin)
        self.pdb.creer_utilisateur(U, "motdepasse-test")
        self.pf = self.pdb.creer_portefeuille(U, "Principal")
        self.version = 0

    def tearDown(self):
        passerelle.DB_PATH = self._ancien
        os.remove(self.chemin)

    def inv(self):
        return passerelle.etat_investissement(U)

    def patrimoine_titres(self):
        i = self.inv()
        return round(i["cash_eur"] + i["titres_cout_eur"], 2)

    def declarer(self, origine="externe", attendus=None):
        if attendus is None:
            attendus = self.inv()["a_expliquer_detail"]
        r = passerelle.declarer_apports(U, ETAT, self.version, origine, attendus)
        self.version = r["version"]
        return r


class DefautOrigine(Base):
    def test_ancien_comportement_gonflait_les_especes(self):
        """Ce que faisait le bouton : un versement daté d'aujourd'hui, à chaque clic."""
        self.pdb.ajouter_operation(U, self.pf, achat("2025-03-10", "CW8", 10, 408.5))
        avant = self.patrimoine_titres()
        for k in range(3):
            r = passerelle.virer(U, ETAT, self.version, f"clic-{k}-xxxx", "2026-10-28", 4085.0,
                                 origine="externe")
            self.version = r["version"]
        i = self.inv()
        self.assertEqual(i["a_expliquer_eur"], 4085.0)           # la question restait posée
        self.assertEqual(self.patrimoine_titres(), avant + 3 * 4085.0)


class Declaration(Base):
    def setUp(self):
        super().setUp()
        self.pdb.ajouter_operation(U, self.pf, achat("2025-03-10", "CW8", 10, 408.5))

    def test_ne_change_pas_le_patrimoine(self):
        avant = self.patrimoine_titres()
        r = self.declarer()
        self.assertEqual(len(r["apports"]), 1)
        self.assertEqual(r["apports"][0]["date"], "2025-03-09")
        i = self.inv()
        self.assertEqual(i["a_expliquer_eur"], 0.0)
        self.assertEqual(i["cash_eur"], 0.0)
        self.assertEqual(self.patrimoine_titres(), avant)
        self.assertEqual(i["verses_exterieurs_eur"], 4085.0)

    def test_deuxieme_clic_sans_effet(self):
        attendus = self.inv()["a_expliquer_detail"]
        self.declarer(attendus=attendus)
        avant = self.patrimoine_titres()
        with self.assertRaises(passerelle.ExplicationPerimee) as ctx:
            self.declarer(attendus=attendus)      # le même écran, cliqué une seconde fois
        self.assertEqual(ctx.exception.restant, [])
        self.assertEqual(self.patrimoine_titres(), avant)
        self.assertEqual(len(self.inv()["mouvements"]), 1)

    def test_montant_perime_refuse(self):
        with self.assertRaises(passerelle.ExplicationPerimee):
            self.declarer(attendus=[{"achat_id": 1, "montant_eur": 4000.0}])
        self.assertEqual(self.inv()["mouvements"], [])

    def test_version_perimee_refusee(self):
        attendus = self.inv()["a_expliquer_detail"]
        with self.assertRaises(passerelle.ConflitVersion):
            passerelle.declarer_apports(U, ETAT, 7, "budget", attendus)
        self.assertEqual(self.inv()["mouvements"], [])

    def test_annuler_fait_reapparaitre_la_question(self):
        r = self.declarer()
        vid = r["apports"][0]["id"]
        a = passerelle.annuler_virement(U, ETAT, self.version, vid)
        self.version = a["version"]
        self.assertEqual(a["supprimes"], 1)
        self.assertEqual(self.inv()["a_expliquer_eur"], 4085.0)
        self.assertEqual(self.inv()["cash_eur"], 0.0)


class CasComposes(Base):
    def test_plusieurs_achats_et_especes_partielles(self):
        # 500 versés, achat de 800 (300 à découvert), vente, rachat à découvert le même jour que
        # un autre achat.
        passerelle.virer(U, ETAT, 0, "dep-initial", "2025-01-02", 500.0)
        self.version = 1
        self.pdb.ajouter_operation(U, self.pf, achat("2025-01-05", "AAA", 8, 100))
        self.pdb.ajouter_operation(U, self.pf, {"date": "2025-02-01", "type": "VENTE", "ticker": "AAA",
                                                "quantite": 2, "prix": 110, "devise": "EUR",
                                                "taux_eur": 1.0, "frais_eur": 0, "montant_eur": 220})
        self.pdb.ajouter_operation(U, self.pf, achat("2025-03-01", "BBB", 3, 100))
        self.pdb.ajouter_operation(U, self.pf, achat("2025-03-01", "CCC", 1, 50, frais=1))
        i = self.inv()
        self.assertEqual(i["a_expliquer_eur"], round(300 + 80 + 51, 2))
        avant = self.patrimoine_titres()
        self.declarer("budget")
        i = self.inv()
        self.assertEqual(i["a_expliquer_eur"], 0.0)
        self.assertEqual(self.patrimoine_titres(), avant)
        self.assertEqual(i["verses_depuis_budget_eur"], round(500 + 431, 2))


class Remplacement(Base):
    def test_modifier_un_virement_ne_le_double_pas(self):
        r = passerelle.virer(U, ETAT, 0, "vir-aaaaaa", "2026-10-01", 300.0)
        r = passerelle.virer(U, ETAT, r["version"], "vir-bbbbbb", "2026-10-01", 500.0,
                             remplace="vir-aaaaaa")
        mv = self.inv()["mouvements"]
        self.assertEqual([m["montant_eur"] for m in mv], [500.0])
        self.assertEqual(self.inv()["cash_eur"], 500.0)

    def test_annuler_un_retrait(self):
        r = passerelle.virer(U, ETAT, 0, "vir-aaaaaa", "2026-10-01", 300.0)
        r = passerelle.virer(U, ETAT, r["version"], "ret-aaaaaa", "2026-10-02", 100.0, sens="RETRAIT")
        passerelle.annuler_virement(U, ETAT, r["version"], "ret-aaaaaa")
        self.assertEqual(self.inv()["cash_eur"], 300.0)


if __name__ == "__main__":
    unittest.main()
