#!/usr/bin/env python3
"""
integrer_portefeuille.py — intègre le travail de l'équipe compte-titres.

Principe : les deux moitiés du projet sont parties de la même version
(commit « base »). On replace leur livraison sur cette base, puis on laisse
git faire la fusion à trois points. Git ne signale alors que les endroits
réellement touchés des deux côtés — en pratique deux fichiers.

Utilisation, depuis la racine du dépôt :

    python outils/integrer_portefeuille.py ../livraison_portefeuille.zip

Le script accepte un .zip ou un dossier déjà décompressé. Il ne touche pas
à la branche perso-finance tant que la fusion n'est pas demandée.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PROJET = "ecofolio_perso"
BRANCHE_EUX = "portefeuille"
BRANCHE_MOI = "perso-finance"


def git(*args, verifier=True) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if verifier and r.returncode:
        sys.exit(f"✗ git {' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout.strip()


def racine_projet(dossier: Path) -> Path:
    """Trouve, dans une livraison, le dossier qui contient backend/ et frontend/."""
    candidats = [d for d in [dossier, *dossier.rglob("*")]
                 if d.is_dir() and (d / "backend").is_dir() and (d / "frontend").is_dir()]
    if not candidats:
        sys.exit("✗ Livraison illisible : aucun dossier ne contient à la fois backend/ et frontend/.")
    return min(candidats, key=lambda d: len(d.parts))


def main():
    ap = argparse.ArgumentParser(description="Intègre la livraison compte-titres dans le dépôt.")
    ap.add_argument("livraison", help="archive .zip ou dossier livré par l'équipe compte-titres")
    ap.add_argument("--fusionner", action="store_true",
                    help="enchaîner la fusion dans " + BRANCHE_MOI + " (sinon la branche est seulement créée)")
    args = ap.parse_args()

    if not Path(".git").is_dir():
        sys.exit("✗ À lancer depuis la racine du dépôt (le dossier qui contient .git).")
    if git("status", "--porcelain"):
        sys.exit("✗ Travail en cours non validé. Faites « git commit » ou « git stash » d'abord.")

    branche_depart = git("rev-parse", "--abbrev-ref", "HEAD")

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(args.livraison)
        if not src.exists():
            sys.exit(f"✗ Introuvable : {src}")
        if src.is_file():
            with zipfile.ZipFile(src) as z:
                z.extractall(tmp)
            src = racine_projet(Path(tmp))
        else:
            src = racine_projet(src)
        print(f"→ Livraison lue depuis {src}")

        existe = git("branch", "--list", BRANCHE_EUX)
        if existe:
            rep = input(f"La branche « {BRANCHE_EUX} » existe déjà. La remplacer ? [o/N] ")
            if rep.strip().lower() not in ("o", "oui", "y"):
                sys.exit("Abandon.")
            git("branch", "-D", BRANCHE_EUX)

        git("checkout", "-q", "-b", BRANCHE_EUX, "base")
        shutil.rmtree(PROJET, ignore_errors=True)
        shutil.copytree(src, PROJET, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.db", ".vscode", ".git", ".DS_Store"))
        git("add", "-A")
        if not git("status", "--porcelain"):
            print("→ Livraison identique à la base : rien à intégrer.")
        else:
            git("commit", "-qm", "Livraison de l'équipe compte-titres")
            print(f"→ Branche « {BRANCHE_EUX} » créée depuis base.")
        print()
        print(git("diff", "--stat", "base", BRANCHE_EUX) or "  (aucun changement)")
        print()

    git("checkout", "-q", BRANCHE_MOI if args.fusionner else branche_depart)
    if not args.fusionner:
        print("Pour voir ce qu'ils ont touché et que nous touchons aussi :")
        print(f"    git diff --stat base {BRANCHE_EUX}")
        print("Puis, quand vous êtes prêt :")
        print(f"    git checkout {BRANCHE_MOI} && git merge {BRANCHE_EUX}")
        return

    r = subprocess.run(["git", "merge", BRANCHE_EUX, "-m",
                        "Fusion du compte-titres et du module Budget et comptes"],
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)
    conflits = git("diff", "--name-only", "--diff-filter=U", verifier=False)
    if conflits:
        print("Fichiers en conflit, à régler à la main :")
        for f in conflits.splitlines():
            print("   ", f)
        print("\nRègles de résolution : voir MERGE.md, section « Qui gagne, fichier par fichier ».")
    else:
        print("✓ Fusion sans conflit.")
    print("\nEnsuite, dans tous les cas :")
    print("    python outils/verifier_fusion.py")


if __name__ == "__main__":
    main()
