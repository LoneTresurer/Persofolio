"""Structure de l'état du module Budget et comptes, telle que PUT /banque/etat l'accepte.

    python -m unittest tests.test_banque_db        # depuis persofolio/backend
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.banque_db import verifier_structure          # noqa: E402

NOUVEAU = {"format": "releves-1", "comptes": {}, "operations": [], "releves": [], "ancres": [], "dettes": []}
ANCIEN = {"comptes": {}, "operations": [], "programmees": [], "dettes": [], "objectifs": [], "budgets": {}}


class Structure(unittest.TestCase):
    def test_etat_releves_accepte(self):
        verifier_structure(dict(NOUVEAU))

    def test_etat_releves_sans_programmees_ni_objectifs(self):
        # Le refus de ces clés absentes a bloqué l'enregistrement à la refonte (23 novembre).
        self.assertNotIn("programmees", NOUVEAU)
        verifier_structure(dict(NOUVEAU))

    def test_etat_ancien_accepte(self):
        verifier_structure(dict(ANCIEN))

    def test_cle_manquante_refusee(self):
        e = dict(NOUVEAU); del e["ancres"]
        with self.assertRaisesRegex(ValueError, "ancres"):
            verifier_structure(e)

    def test_format_inconnu_refuse(self):
        with self.assertRaisesRegex(ValueError, "format"):
            verifier_structure({**NOUVEAU, "format": "autre"})

    def test_trop_volumineux_refuse(self):
        with self.assertRaisesRegex(ValueError, "volumineux"):
            verifier_structure({**NOUVEAU, "operations": ["x" * 2_100_000]})


if __name__ == "__main__":
    unittest.main()
