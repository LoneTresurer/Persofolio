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


class PlusieursPortefeuilles(Base):
    """Chaque portefeuille rejoué à part, comme l'en-tête du compte-titres."""

    def test_especes_de_chacun_additionnees(self):
        from core.portfolio_calc import rejouer
        pf2 = self.pdb.creer_portefeuille(U, "PEA")
        self.pdb.ajouter_operation(U, self.pf, {"date": "2026-01-02", "type": "DEPOT", "montant_eur": 1000.0,
                                                "devise": "EUR", "taux_eur": 1.0})
        self.pdb.ajouter_operation(U, pf2, achat("2026-01-05", "CW8", 1, 500))   # sans versement
        i = self.inv()
        # Rejouées ensemble, les espèces de l'un payaient l'achat de l'autre : 500 € et rien à expliquer.
        self.assertEqual(i["cash_eur"], 1000.0)
        self.assertEqual(i["a_expliquer_eur"], 500.0)
        attendu = sum(rejouer(self.pdb.operations(U, p)).cash_eur for p in (self.pf, pf2))
        self.assertEqual(i["cash_eur"], round(attendu, 2))
        self.assertEqual(sorted(i["portefeuilles"]), sorted([self.pf, pf2]))
        self.assertIsNone(i["portefeuille"])

    def test_un_seul_portefeuille_inchange(self):
        self.pdb.ajouter_operation(U, self.pf, {"date": "2026-01-02", "type": "DEPOT", "montant_eur": 300.0,
                                                "devise": "EUR", "taux_eur": 1.0})
        i = self.inv()
        self.assertEqual((i["portefeuille"], i["portefeuilles"], i["cash_eur"]), (self.pf, [self.pf], 300.0))


class LienBudget(Base):
    """Un mouvement écrit par le budget ne se corrige pas depuis le compte-titres."""

    def test_versement_du_budget_refuse(self):
        passerelle.virer(U, ETAT, 0, "vir-aaaaaa", "2026-10-01", 300.0)
        ops = self.pdb.operations(U, self.pf)
        msg = passerelle.lien_budget(ops, ops[0]["id"])
        self.assertIsNotNone(msg)
        self.assertIn("Budget et comptes", msg)

    def test_apport_declare_refuse(self):
        self.pdb.ajouter_operation(U, self.pf, achat("2025-03-10", "CW8", 10, 408.5))
        self.declarer()
        depot = [o for o in self.pdb.operations(U, self.pf) if o["type"] == "DEPOT"][0]
        self.assertIsNotNone(passerelle.lien_budget(self.pdb.operations(U, self.pf), depot["id"]))

    def test_versement_saisi_dans_le_compte_titres_libre(self):
        self.pdb.ajouter_operation(U, self.pf, {"date": "2026-10-01", "type": "DEPOT", "montant_eur": 50.0,
                                                "devise": "EUR", "taux_eur": 1.0})
        ops = self.pdb.operations(U, self.pf)
        self.assertIsNone(passerelle.lien_budget(ops, ops[0]["id"]))

    def test_achat_et_operation_absente(self):
        aid = self.pdb.ajouter_operation(U, self.pf, achat("2025-03-10", "CW8", 1, 10))
        ops = self.pdb.operations(U, self.pf)
        self.assertIsNone(passerelle.lien_budget(ops, aid))
        self.assertIsNone(passerelle.lien_budget(ops, 999999))



class Centimes(Base):
    """Défaut du 19 novembre : après réponse, « 0,01 € à expliquer » sans aucun achat
    derrière — question impossible à faire disparaître — ou des centimes d'espèces
    apparus. Achats en dollars, montants qui ne tombent jamais au centime."""

    def test_trente_achats_aleatoires(self):
        import random
        for graine in range(8):
            with self.subTest(graine=graine):
                self.setUp()
                try:
                    alea = random.Random(graine)
                    for i in range(30):
                        q, prix = alea.randint(1, 9), round(alea.uniform(10, 600), 2)
                        taux, frais = round(alea.uniform(0.88, 0.95), 5), round(alea.uniform(0, 2), 3)
                        self.pdb.ajouter_operation(U, self.pf, {
                            "date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "type": "ACHAT",
                            "ticker": f"T{i}", "quantite": q, "prix": prix, "devise": "USD",
                            "taux_eur": taux, "frais_eur": frais, "montant_eur": q * prix * taux})
                    avant = self.inv()
                    self.assertEqual(avant["a_expliquer_eur"],
                                     round(sum(t["montant_eur"] for t in avant["a_expliquer_detail"]), 2))
                    self.declarer()
                    apres = self.inv()
                    self.assertEqual((apres["a_expliquer_eur"], apres["a_expliquer_detail"]), (0.0, []))
                    self.assertEqual(apres["cash_eur"], 0.0)                 # pas un centime apparu
                    self.assertEqual(apres["titres_cout_eur"], avant["titres_cout_eur"])
                finally:
                    self.tearDown()
        self.setUp()                                                       # pour le tearDown final

    def test_total_egal_somme_des_lignes(self):
        """Une base déjà abîmée (versements arrondis) : les restes ne s'affichent plus."""
        # Deux achats couverts chacun par un versement arrondi, à 0,004 € près :
        # 0,008 € en tout, que l'ancien total affichait « 0,01 € » sans aucune ligne.
        for jour, prix in (("2026-01-05", 10.004), ("2026-02-05", 20.004)):
            self.pdb.ajouter_operation(U, self.pf, {"date": jour[:-1] + "4", "type": "DEPOT", "montant_eur": round(prix, 2),
                                                    "devise": "EUR", "taux_eur": 1.0})
            self.pdb.ajouter_operation(U, self.pf, achat(jour, "AAA", 1, prix))
        i = self.inv()
        self.assertGreater(round(passerelle.etat_investissement(U)["entre_en_tout_eur"] - 30.0, 3), 0.005)
        self.assertEqual(i["a_expliquer_detail"], [])
        self.assertEqual(i["a_expliquer_eur"], 0.0)


class VersementsSaisis(Base):
    """Un versement saisi ou importé dans le compte-titres pose la question de son origine."""

    def setUp(self):
        super().setUp()
        self.dep = self.pdb.ajouter_operation(U, self.pf, {
            "date": "2026-11-05", "type": "DEPOT", "montant_eur": 500.0, "devise": "EUR",
            "taux_eur": 1.0, "origine": "import_ordres"})

    def repondre(self, origine="budget", montant=500.0, op=None):
        r = passerelle.declarer_versements(U, ETAT, self.version, [
            {"operation_id": op or self.dep, "montant_eur": montant, "origine": origine}])
        self.version = r["version"]
        return r

    def test_question_posee(self):
        v = self.inv()["versements_a_expliquer"]
        self.assertEqual([(x["operation_id"], x["type"], x["montant_eur"], x["saisie"]) for x in v],
                         [(self.dep, "DEPOT", 500.0, "import_ordres")])
        self.assertEqual(self.inv()["verses_depuis_budget_eur"], 0.0)

    def test_reponse_courant(self):
        self.repondre("budget")
        i = self.inv()
        self.assertEqual(i["versements_a_expliquer"], [])
        self.assertEqual(i["verses_depuis_budget_eur"], 500.0)
        self.assertEqual(i["cash_eur"], 500.0)                       # le compte-titres n'a pas bougé
        mv = [m for m in i["mouvements"] if m["operation_id"] == self.dep][0]
        self.assertEqual((mv["origine"], mv["repondu"]), ("budget", True))
        # la colonne du compte-titres garde sa propre information
        self.assertEqual(self.pdb.operations(U, self.pf)[0]["origine"], "import_ordres")

    def test_reponse_externe(self):
        self.repondre("externe")
        i = self.inv()
        self.assertEqual((i["verses_exterieurs_eur"], i["verses_depuis_budget_eur"]), (500.0, 0.0))

    def test_second_clic_sans_effet(self):
        self.repondre()
        with self.assertRaises(passerelle.ExplicationPerimee):
            passerelle.declarer_versements(U, ETAT, self.version, [
                {"operation_id": self.dep, "montant_eur": 500.0, "origine": "budget"}])
        self.assertEqual(self.inv()["verses_depuis_budget_eur"], 500.0)

    def test_montant_modifie_refuse(self):
        with self.assertRaises(passerelle.ExplicationPerimee):
            self.repondre(montant=450.0)
        self.assertEqual(len(self.inv()["versements_a_expliquer"]), 1)

    def test_annuler_la_reponse(self):
        self.repondre()
        r = passerelle.annuler_reponse(U, ETAT, self.version, self.dep)
        self.version = r["version"]
        self.assertEqual(r["supprimes"], 1)
        self.assertEqual(len(self.inv()["versements_a_expliquer"]), 1)

    def test_rattache_au_courant_protege(self):
        ops = self.pdb.operations(U, self.pf)
        self.assertIsNone(passerelle.lien_budget(ops, self.dep))           # sans réponse : libre
        self.repondre("budget")
        self.assertIn("Annuler la réponse", passerelle.lien_budget(ops, self.dep))

    def test_externe_reste_libre(self):
        self.repondre("externe")
        self.assertIsNone(passerelle.lien_budget(self.pdb.operations(U, self.pf), self.dep))

    def test_ecrits_par_le_budget_et_prelevements_exclus(self):
        passerelle.virer(U, ETAT, self.version, "vir-aaaaaa", "2026-11-06", 300.0)
        self.pdb.ajouter_operation(U, self.pf, {"date": "2026-11-07", "type": "PRELEVEMENT",
                                                "montant_eur": 12.0, "devise": "EUR", "taux_eur": 1.0})
        self.assertEqual([x["operation_id"] for x in self.inv()["versements_a_expliquer"]], [self.dep])

    def test_retrait(self):
        ret = self.pdb.ajouter_operation(U, self.pf, {"date": "2026-11-20", "type": "RETRAIT",
                                                      "montant_eur": 200.0, "devise": "EUR", "taux_eur": 1.0})
        self.repondre("budget")
        self.repondre("budget", 200.0, ret)
        self.assertEqual(self.inv()["verses_depuis_budget_eur"], 300.0)   # 500 versés − 200 rapatriés


if __name__ == "__main__":
    unittest.main()
