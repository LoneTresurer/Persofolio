"""
Contrôle de l'extracteur de relevés, sur des fichiers réels.

Le test ne vérifie pas le détail des libellés — il vérifie la seule chose qui
prouve que l'extraction est juste : pour chaque compte de chaque fichier,
solde d'ouverture + somme des mouvements = solde de clôture, au centime.

Placez vos relevés PDF dans tests/releves/ (non versionnés : données réelles).
    python tests/test_releve.py [dossier]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.releve import extraire          # noqa: E402


def controler(dossier: Path) -> int:
    fichiers = sorted(dossier.glob("*.pdf"))
    if not fichiers:
        print(f"Aucun PDF dans {dossier}.")
        return 1
    echecs = 0
    for f in fichiers:
        rel = extraire(str(f))
        for c in rel.comptes:
            etat = "OK " if c.equilibre else "ECHEC"
            if not c.equilibre:
                echecs += 1
            print(f"{etat} {rel.banque or '?':<18} {c.libelle[:20] or c.numero[:20]:<20} "
                  f"{c.date_ouverture} → {c.date_cloture}  "
                  f"{c.solde_ouverture:>9} {c.somme_mouvements:>+10} = {c.solde_cloture:>9}  "
                  f"({len(c.operations)} opérations, écart {c.ecart})")
        for a in rel.anomalies:
            print(f"     ! {a}")
    print(f"\n{len(fichiers)} fichiers, {echecs} compte(s) en échec.")
    return 1 if echecs else 0


if __name__ == "__main__":
    dossier = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "releves"
    sys.exit(controler(dossier))
