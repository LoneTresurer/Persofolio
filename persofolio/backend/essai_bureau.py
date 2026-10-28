"""
essai_bureau.py
===============
Contrôle de l'application de bureau, depuis l'intérieur : lancé par lanceur.py
quand `PERSOFOLIO_ESSAI` désigne un fichier de rapport.

Ce qu'il vérifie, dans la vraie fenêtre et contre le vrai serveur FastAPI :

- **Étape 1** (installation neuve) : la session locale s'ouvre, la page Budget
  se charge avec Chart.js embarqué, et le profil d'exemple affiche le patrimoine
  de référence, 1 150,86 €.
- **Étape 2** (second lancement, mêmes données) : la session a survécu à la
  fermeture — c'est ce qui prouve que le stockage de la fenêtre est persistant —
  et le patrimoine est toujours là.

Il mesure aussi les temps de démarrage : fenêtre affichée, serveur prêt, page
prête. Le rapport est un JSON ; outils/essai/lancer_essai_bureau.py le met en
forme dans le résumé de la construction GitHub.

Rien de ceci ne s'exécute en usage normal.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.request

log = logging.getLogger("persofolio.essai")

PATRIMOINE_ATTENDU = "115086"        # 1 150,86 €, chiffres seuls (les espaces varient)
DELAI_MAX = 150                       # secondes, au-delà l'essai est déclaré en échec


class Essai:
    def __init__(self, fenetre, port: int, chemin: str, mesures: dict, t0: float, etat: dict):
        self.f, self.port, self.chemin, self.t0, self.etat = fenetre, port, chemin, t0, etat
        self.etape = os.environ.get("PERSOFOLIO_ESSAI_ETAPE", "1")
        self.rapport = {"etape": self.etape, "plateforme": sys.platform, "ok": False,
                        "erreurs": [], "controles": {}, **mesures}
        self.fini = False

    # ── outils ──
    def _api(self, chemin: str, methode: str = "GET", jeton: str | None = None, corps=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{chemin}", method=methode,
            data=json.dumps(corps).encode() if corps is not None else None,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {jeton}"} if jeton else {})})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def _js(self, code: str):
        return self.f.evaluate_js(code)

    def _attendre(self, condition_js: str, limite: float = 40) -> bool:
        fin = time.time() + limite
        while time.time() < fin:
            try:
                if self._js(f"!!({condition_js})"):
                    return True
            except Exception:                               # noqa: BLE001  (page en cours de chargement)
                pass
            time.sleep(0.2)
        return False

    def _charger(self, url: str, condition_js: str, limite: float = 40) -> bool:
        self.f.load_url(url)
        return self._attendre(f"location.href.indexOf({json.dumps(url)}) === 0 && "
                              f"document.readyState === 'complete' && ({condition_js})", limite)

    def _controle(self, nom: str, ok: bool, detail=None):
        self.rapport["controles"][nom] = {"ok": bool(ok), **({"detail": detail} if detail is not None else {})}
        if not ok:
            self.rapport["erreurs"].append(f"{nom} : {detail}")

    # ── déroulé ──
    def lancer(self):
        minuteur = threading.Timer(DELAI_MAX, self._abandon)
        minuteur.daemon = True
        minuteur.start()
        try:
            self._derouler()
        except Exception as e:                              # noqa: BLE001
            log.exception("essai interrompu")
            self.rapport["erreurs"].append(f"exception : {e!r}")
        self._terminer()

    def _derouler(self):
        base = f"http://localhost:{self.port}"
        session = self._api("/auth/local", "POST")
        jeton, user = session["jeton"], session["username"]
        if not self._api("/portefeuilles", jeton=jeton):
            self._api("/portefeuilles", "POST", jeton, {"nom": "Principal"})

        # Une page de la même origine, pour lire et écrire son stockage.
        self._controle("page d'accueil servie",
                       self._charger(f"{base}/app/index.html", "document.body !== null"))
        if self.etape == "2":
            precedent = os.environ.get("PERSOFOLIO_ESSAI_PRECEDENT")
            ancien = None
            if precedent and os.path.exists(precedent):
                with open(precedent, encoding="utf-8") as f:
                    ancien = json.load(f).get("jeton")
            conserve = self._js("localStorage.getItem('persofolio_jeton')")
            self._controle("session conservée après fermeture", bool(ancien) and conserve == ancien,
                           "le stockage de la fenêtre n'est pas persistant" if conserve != ancien else None)
        # Ce qu'index.html fait après la connexion locale
        self._js(f"localStorage.setItem('persofolio_jeton', {json.dumps(jeton)});"
                 f"localStorage.setItem('persofolio_user', {json.dumps(user)});")
        self.rapport["jeton"] = jeton

        pret = self._charger(f"{base}/app/banque.html",
                             "document.querySelector('#ass-exemple') || "
                             "(document.querySelector('#r-net') && document.querySelector('#r-net').textContent.trim() !== '—')")
        self._controle("page Budget chargée", pret)
        if not pret:
            return
        self._controle("Chart.js embarqué", self._js("window.Chart ? Chart.version : null") == "4.4.1",
                       self._js("window.Chart ? Chart.version : null"))

        if self.etape == "1":
            a_assistant = self._js("!!document.querySelector('#ass-exemple')")
            self._controle("assistant de configuration proposé", a_assistant)
            if a_assistant:
                self._js("document.querySelector('#ass-exemple').click()")
        rempli = self._attendre("document.querySelector('#r-net') && "
                                "/\\d/.test(document.querySelector('#r-net').textContent)", 40)
        texte = self._js("document.querySelector('#r-net') ? document.querySelector('#r-net').textContent : ''") or ""
        chiffres = "".join(c for c in texte if c.isdigit())
        self._controle("patrimoine de l'exemple = 1 150,86 €", rempli and chiffres == PATRIMOINE_ATTENDU, texte.strip())
        time.sleep(1.0)                                     # laisser les graphiques se dessiner
        n = self._js("window.Chart ? Object.keys(Chart.instances).length : 0") or 0
        self._controle("graphiques dessinés", n > 0, n)
        self.rapport["page_s"] = round(time.perf_counter() - self.t0, 2)
        self.rapport["ok"] = not self.rapport["erreurs"]

    def _ecrire(self):
        with open(self.chemin, "w", encoding="utf-8") as f:
            json.dump(self.rapport, f, ensure_ascii=False, indent=2)

    def _terminer(self):
        if self.fini:
            return
        self.fini = True
        self._ecrire()
        # Fichier témoin : l'outil d'essai peut photographier la fenêtre pendant la pause.
        pause = float(os.environ.get("PERSOFOLIO_ESSAI_PAUSE") or 0)
        if pause > 0:
            open(self.chemin + ".pret", "w").close()
            time.sleep(pause)
        self.etat["code"] = 0 if self.rapport["ok"] else 1
        try:
            self.f.destroy()
        except Exception:                                   # noqa: BLE001
            os._exit(self.etat["code"])

    def _abandon(self):
        if self.fini:
            return
        self.rapport["erreurs"].append(f"délai dépassé ({DELAI_MAX} s)")
        self._terminer()
