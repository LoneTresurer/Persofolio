#!/usr/bin/env python3
"""
verifier_fusion.py — contrôles statiques après la fusion des deux moitiés.

Vérifie ce qu'une fusion mal résolue casse en silence : un module qui n'est
plus monté, la connexion unique redevenue locale, une page qui pointe vers
un serveur figé, des marqueurs de conflit oubliés dans le code.

Aucune dépendance : à lancer depuis la racine du dépôt, serveur éteint.

    python outils/verifier_fusion.py
"""

import re
import sys
from pathlib import Path

P = Path("persofolio")
BACK, FRONT = P / "backend", P / "frontend"

ok, echecs, avertissements = [], [], []


def lire(chemin: Path) -> str:
    return chemin.read_text(encoding="utf-8", errors="replace") if chemin.is_file() else ""


def verifier(nom, condition, remede=""):
    (ok if condition else echecs).append((nom, remede))


def avertir(nom, condition, remede=""):
    if not condition:
        avertissements.append((nom, remede))


# ── Fichiers attendus ───────────────────────────────────────────────────────
attendus = [
    BACK / "main.py", BACK / "routes_portefeuille.py", BACK / "routes_banque.py",
    BACK / "core" / "portfolio_db.py", BACK / "core" / "banque_db.py",
    BACK / "core" / "portfolio_calc.py", BACK / "agents" / "market_data.py",
    FRONT / "index.html", FRONT / "banque.html", FRONT / "portefeuille.html",
]
for f in attendus:
    verifier(f"présent : {f}", f.is_file(), "fichier perdu pendant la fusion, le récupérer sur sa branche")

main, rp, rb = lire(BACK / "main.py"), lire(BACK / "routes_portefeuille.py"), lire(BACK / "routes_banque.py")
index, banque, pf = lire(FRONT / "index.html"), lire(FRONT / "banque.html"), lire(FRONT / "portefeuille.html")

# ── Marqueurs de conflit ────────────────────────────────────────────────────
for f in [*attendus, BACK / "requirements.txt"]:
    t = lire(f)
    verifier(f"sans marqueur de conflit : {f.name}",
             not re.search(r"^(<{7}|>{7})[ \t]", t, re.M),
             "supprimer les blocs <<<<<<< ======= >>>>>>> laissés par git")

# ── Backend : les deux modules sont montés, le frontend est servi ───────────
verifier("main.py monte le compte-titres", "router_portefeuille" in main,
         "ajouter : app.include_router(router_portefeuille)")
verifier("main.py monte Budget et comptes", "router_banque" in main,
         "ajouter : from routes_banque import router as router_banque + include_router")
verifier("main.py sert le frontend sur /app", 'mount("/app"' in main,
         "sans ce montage, les pages ne partagent plus la même origine ni la session")
def _apres_les_routes(texte: str) -> bool:
    """Le montage de /app doit venir après toutes les routes, sinon il les masque."""
    if 'mount("/app"' not in texte:
        return False
    dernier = max([texte.rindex(m) for m in ("include_router", "@app.get", "@app.post") if m in texte])
    return texte.index('mount("/app"') > dernier


verifier("/app monté après les routes de l'API", _apres_les_routes(main),
         'déplacer app.mount("/app", ...) en toute fin de main.py')
verifier("GET /auth/moi présent", "/auth/moi" in rp,
         "reprise de session au rechargement ; réappliquer le bloc moi() de routes_portefeuille.py")
verifier("routes_banque réutilise l'authentification du compte-titres",
         "utilisateur_courant" in rb and "def utilisateur_courant" in rp,
         "routes_banque.py importe utilisateur_courant depuis routes_portefeuille.py")
verifier("une seule base de données", "from core.portfolio_db import DB_PATH" in lire(BACK / "core" / "banque_db.py"),
         "banque_db doit écrire dans data/portefeuille.db, pas dans une base à part")

# ── Frontend : connexion unique, même origine ───────────────────────────────
for nom, t in (("index.html", index), ("banque.html", banque), ("portefeuille.html", pf)):
    # Le port n'est pas toujours 8000 : l'exécutable prend le premier port libre.
    verifier(f"{nom} suit l'origine du serveur, quel que soit le port",
             "location.origin" in t and "/:8000$/" not in t,
             "remplacer l'adresse figée par : location.protocol.startsWith('http') ? location.origin : 'http://localhost:8000'")

verifier("index.html : connexion serveur", "/auth/connexion" in index and "/auth/inscription" in index,
         "la connexion locale par navigateur a été réintroduite : reprendre index.html de la branche perso-finance")
verifier("index.html : vérification du jeton au chargement", "/auth/moi" in index,
         "sans cet appel, la session n'est plus vérifiée au rechargement")
verifier("index.html : accès aux deux modules",
         "banque.html" in index and "portefeuille.html" in index,
         "la page d'accueil doit mener aux deux modules")
verifier("banque.html : données sur le serveur", "/banque/etat" in banque,
         "le module ne doit plus écrire dans le localStorage du navigateur")
verifier("portefeuille.html : renvoi vers la connexion unique",
         "location.replace('index.html')" in pf,
         "sans jeton, la page doit renvoyer vers index.html au lieu d'afficher son propre écran de connexion")
# L'écran d'accueil de portefeuille.html a changé de nom au fil des versions
# (ecran-connexion, puis ecran-attente). Ce qui compte n'est pas son identifiant
# mais le fait qu'il ne serve pas de second écran de connexion.
verifier("portefeuille.html : pas de second écran de connexion",
         not re.search(r'id="(auth-mdp|auth-user)"', pf),
         "la connexion se fait sur index.html ; retirer le formulaire de portefeuille.html")
avertir("portefeuille.html : liens vers les autres pages",
        "banque.html" in pf, "ajouter les liens Accueil et Budget et comptes dans l'en-tête (confort, non bloquant)")

# ── Imports : rien ne doit pointer vers un module archivé ───────────────────
def _imports_casses() -> list:
    """Tout « from agents.X » / « from core.X » doit correspondre à un fichier présent."""
    manquants = []
    for py in BACK.rglob("*.py"):
        if "archive_optimiseur" in py.parts:
            continue
        for paquet, module in re.findall(r"^\s*from\s+(agents|core)\.(\w+)\s+import", lire(py), re.M):
            if not (BACK / paquet / f"{module}.py").is_file():
                manquants.append(f"{py.relative_to(P)} → {paquet}.{module}")
    return manquants


_casses = _imports_casses()
verifier("aucun import vers un module archivé ou absent", not _casses,
         "remonter le module depuis backend/archive_optimiseur/, ou retirer le code qui en dépend : "
         + ", ".join(_casses) if _casses else "")

# ── Dépendances ─────────────────────────────────────────────────────────────
req = lire(BACK / "requirements.txt")
for paquet in ("fastapi", "uvicorn", "pandas", "yfinance", "httpx"):
    verifier(f"requirements : {paquet}", re.search(rf"^{paquet}", req, re.M) is not None,
             "dépendance nécessaire aux deux modules")
# pdfplumber sert désormais à la lecture des relevés bancaires : il n'est plus
# propre à l'optimiseur. Seuls scipy, scikit-learn et openpyxl le restent.
avertir("requirements : dépendances de l'optimiseur retirées",
        not re.search(r"^(scipy|scikit-learn|openpyxl)", req, re.M),
        "l'optimiseur est archivé ; ces paquets ne servent plus (les commenter)")

# ── Rapport ─────────────────────────────────────────────────────────────────
print(f"\n{len(ok)} contrôles passés.\n")
if avertissements:
    print("Avertissements :")
    for nom, remede in avertissements:
        print(f"  ~ {nom}\n      {remede}")
    print()
if echecs:
    print("Échecs :")
    for nom, remede in echecs:
        print(f"  ✗ {nom}\n      {remede}")
    print(f"\n{len(echecs)} problème(s) à régler avant de lancer le serveur.")
    sys.exit(1)
print("✓ Rien à signaler. Lancez le serveur et faites le test de bout en bout de MERGE.md.")
