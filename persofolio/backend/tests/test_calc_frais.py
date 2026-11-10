"""
Contrôles du prix de revient frais inclus (livraison du 10 novembre).

    cd persofolio/backend && python -m unittest tests.test_calc_frais -v

Code de l'équipe compte-titres, vérifié ici sans y toucher : un titre en dollars
acheté avec des frais en euros, puis vendu en partie.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.portfolio_calc import rejouer   # noqa: E402


def op(i, date, type_, q, prix, frais, taux=0.9, ticker="AAPL"):
    return {"id": i, "date": date, "type": type_, "ticker": ticker, "quantite": q, "prix": prix,
            "devise": "USD", "taux_eur": taux, "frais_eur": frais, "montant_eur": q * prix * taux}


class PrixDeRevient(unittest.TestCase):
    def setUp(self):
        # 10 titres à 100 $ (taux 0,9 €/$), 9 € de frais ; puis une vente de 4 à 120 $, 3 € de frais.
        self.ops = [{"id": 1, "date": "2026-01-02", "type": "DEPOT", "montant_eur": 2000.0},
                    op(2, "2026-01-05", "ACHAT", 10, 100.0, 9.0),
                    op(3, "2026-03-05", "VENTE", 4, 120.0, 3.0)]

    def test_frais_inclus_apres_achat(self):
        p = rejouer(self.ops[:2]).positions["AAPL"]
        self.assertAlmostEqual(p.pru, 100.0)                      # hors frais
        self.assertAlmostEqual(p.pru_frais_inclus, 101.0)         # 1000 $ + 9 € / 0,9 = 1010 $ pour 10
        self.assertAlmostEqual(p.cout_eur, 909.0)

    def test_vente_partielle_garde_le_prix_de_revient(self):
        e = rejouer(self.ops)
        p = e.positions["AAPL"]
        self.assertAlmostEqual(p.quantite, 6)
        self.assertAlmostEqual(p.pru_frais_inclus, 101.0)         # inchangé par une vente partielle
        self.assertAlmostEqual(p.cout_devise, 606.0)

    def test_frais_ventiles(self):
        e = rejouer(self.ops)
        self.assertAlmostEqual(e.frais_achat_eur, 9.0)
        self.assertAlmostEqual(e.frais_vente_eur, 3.0)
        self.assertAlmostEqual(e.frais_eur, 12.0)
        self.assertEqual((e.nb_ordres_achat, e.nb_ordres_vente), (1, 1))


if __name__ == "__main__":
    unittest.main()
