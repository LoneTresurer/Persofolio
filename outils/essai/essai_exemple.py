"""
Le calcul du patrimoine, contrôlé à date fixe sur le profil d'exemple.

    python outils/essai/essai_exemple.py .        # depuis la racine du dépôt

Le profil d'exemple est construit par rapport à la date du jour : chaque mois
écoulé fait passer des échéances programmées, et son patrimoine change. Le
comparer à un chiffre fixe n'a donc de sens qu'à une date fixe. Ce script fige
l'horloge du navigateur et vérifie les valeurs de référence :

- 28 octobre 2026 : 1 150,86 € — le chiffre qui a traversé la fusion, le
  renommage et la jonction des deux modules ;
- 1er novembre 2026 : 200,85 €, et 10 novembre 2026 : −898,08 €, relevés le
  10 novembre ; ils vérifient que le passage d'un mois est compté de la même
  façon d'une version à l'autre.

Si l'un de ces chiffres change, le calcul a changé : c'est voulu (et les
références se mettent à jour, en le disant dans le commit) ou c'est une
régression.

Comme essai_passerelle.py : vraie page banque.html, vraie passerelle derrière
serveur_essai.py, hors ligne, Playwright et Chromium.
"""

import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

ICI = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ICI, "..", ".."))
BACK, FRONT = f"{REPO}/persofolio/backend", f"{REPO}/persofolio/frontend"
sys.path.insert(0, BACK)

REFERENCES = {
    "2026-10-28T12:00:00": "1 150,86 €",
    "2026-11-01T12:00:00": "200,85 €",
    "2026-11-10T12:00:00": "-898,08 €",
}
PORT = 8767


def normaliser(texte: str) -> str:
    """Espaces fines, insécables, signe moins typographique : on compare le fond."""
    return " ".join(texte.replace(" ", " ").replace("\xa0", " ").replace("−", "-").split())


def patrimoine_a(date_iso: str) -> str:
    tmp = tempfile.mkdtemp()
    db = f"{tmp}/portefeuille.db"
    os.environ["PERSOFOLIO_DONNEES"] = tmp
    from core.banque_db import BanqueDB
    from core.portfolio_db import PortfolioDB
    pdb = PortfolioDB(db)
    BanqueDB(db)
    pdb.creer_utilisateur("pierre", "motdepasse-test")
    pdb.creer_portefeuille("pierre", "Principal")
    srv = subprocess.Popen([sys.executable, f"{ICI}/serveur_essai.py", BACK, FRONT, str(PORT), db])
    time.sleep(1.2)
    try:
        with sync_playwright() as p:
            nav = p.chromium.launch()
            pg = nav.new_page()
            pg.clock.install(time=datetime.fromisoformat(date_iso))
            pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1")
                     else r.abort())
            base = f"http://127.0.0.1:{PORT}/banque.html"
            pg.goto(base)
            pg.evaluate("localStorage.setItem('persofolio_jeton','t');"
                        "localStorage.setItem('persofolio_user','pierre')")
            pg.goto(base)
            pg.wait_for_selector("#ass-exemple")
            pg.click("#ass-exemple")
            # Le profil se charge en deux temps : on attend une valeur stable.
            lu, depuis, debut = None, time.time(), time.time()
            while time.time() - debut < 45:
                v = pg.inner_text("#r-net")
                if v != lu:
                    lu, depuis = v, time.time()
                elif any(c.isdigit() for c in v) and time.time() - depuis > 3:
                    break
                pg.wait_for_timeout(250)
            nav.close()
            return normaliser(lu or "")
    finally:
        srv.terminate()


echecs = 0
for date_iso, attendu in REFERENCES.items():
    obtenu = patrimoine_a(date_iso)
    ok = obtenu == normaliser(attendu)
    echecs += not ok
    print(f"  {'OK' if ok else 'KO'}  {date_iso[:10]} : {obtenu}" + ("" if ok else f"   (attendu {attendu})"))
print(f"\n{len(REFERENCES) - echecs} réussis, {echecs} échecs")
sys.exit(1 if echecs else 0)
