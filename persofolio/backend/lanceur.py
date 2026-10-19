"""
lanceur.py
==========
Point d'entrée de l'exécutable Windows : un double-clic, et PersoFolio s'ouvre
dans le navigateur. Aucune ligne de commande, aucune installation de Python.

Ce que fait ce fichier, dans l'ordre :

1. **Il cherche un port libre.** 8000 d'abord, parce que c'est celui du guide et
   des habitudes de l'équipe ; sinon le suivant. Un autre programme occupe
   souvent 8000 sur une machine de développeur, et l'utilisateur ne doit pas
   avoir à le savoir.
2. **Il refuse de se lancer deux fois.** Un second double-clic ne démarre pas un
   second serveur : il rouvre l'onglet du premier. Deux serveurs sur la même
   base, ce sont deux onglets qui s'écrasent l'un l'autre.
3. **Il démarre le serveur en arrière-plan**, attend qu'il réponde vraiment,
   puis ouvre le navigateur. Ouvrir trop tôt donnerait une page d'erreur.
4. **Il affiche une petite fenêtre** avec deux boutons : rouvrir l'onglet, et
   quitter. Sans elle, l'utilisateur ferme son navigateur en croyant avoir
   fermé le programme, et le serveur tourne indéfiniment.

Les comptes sont écrits dans `%LOCALAPPDATA%\\PersoFolio` (voir
`core/emplacements.py`) : le dossier de l'exécutable est temporaire et effacé à
la fermeture.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

PORT_PREFERE = 8000
PORTS_ESSAYES = 12          # 8000 à 8011 : au-delà, quelque chose ne tourne pas rond
ATTENTE_DEMARRAGE = 90      # secondes ; la première ouverture d'un gros exécutable est lente


# ── Sorties standard ────────────────────────────────────────────────────────
def _assurer_sorties() -> None:
    """Sans console, `sys.stdout` vaut None et la moindre écriture lève une erreur.

    PyInstaller construit l'exécutable en mode fenêtré : il n'y a pas de console
    où écrire. Toute bibliothèque qui fait un `print` — uvicorn au démarrage, par
    exemple — ferait tomber le programme. On leur donne un trou noir où écrire ;
    ce qui compte est de toute façon consigné dans le journal.
    """
    for nom in ("stdout", "stderr"):
        if getattr(sys, nom, None) is None:
            setattr(sys, nom, open(os.devnull, "w", encoding="utf-8"))


_assurer_sorties()

from core.emplacements import dossier_donnees   # noqa: E402  (après _assurer_sorties)

FICHIER_VERROU = os.path.join(dossier_donnees(), "instance.json")
JOURNAL = os.path.join(dossier_donnees(), "journal.txt")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.FileHandler(JOURNAL, encoding="utf-8")],
)
log = logging.getLogger("persofolio.lanceur")


# ── Ports ───────────────────────────────────────────────────────────────────
def port_libre(port: int) -> bool:
    """Essaie de réserver le port pour de bon.

    Volontairement sans SO_REUSEADDR : sous Windows, cette option autorise à se
    lier à un port qu'un autre programme écoute déjà, et la vérification
    conclurait « libre » sur un port occupé. On veut ici l'inverse, et
    `SO_EXCLUSIVEADDRUSE` le dit explicitement là où il existe.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        exclusif = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)   # Windows seulement
        if exclusif is not None:
            try:
                s.setsockopt(socket.SOL_SOCKET, exclusif, 1)
            except OSError:
                pass
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def choisir_port() -> int:
    for port in range(PORT_PREFERE, PORT_PREFERE + PORTS_ESSAYES):
        if port_libre(port):
            return port
    # Tout est pris : on laisse le système en attribuer un au hasard.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── Instance déjà ouverte ───────────────────────────────────────────────────
def repond(port: int, delai: float = 1.5) -> bool:
    """Vrai si PersoFolio — et pas un autre programme — répond sur ce port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=delai) as r:
            return json.loads(r.read().decode("utf-8")).get("status") == "healthy"
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return False


def instance_existante() -> int | None:
    """Port d'une instance déjà lancée, ou None. Nettoie un verrou périmé."""
    try:
        with open(FICHIER_VERROU, encoding="utf-8") as f:
            port = int(json.load(f)["port"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if repond(port):
        return port
    # Verrou laissé par un arrêt brutal : il ne vaut plus rien.
    try:
        os.remove(FICHIER_VERROU)
    except OSError:
        pass
    return None


def poser_verrou(port: int) -> None:
    try:
        with open(FICHIER_VERROU, "w", encoding="utf-8") as f:
            json.dump({"port": port, "pid": os.getpid()}, f)
    except OSError as e:
        log.warning("verrou non écrit : %s", e)


def retirer_verrou() -> None:
    try:
        os.remove(FICHIER_VERROU)
    except OSError:
        pass


# ── Serveur ─────────────────────────────────────────────────────────────────
def demarrer_serveur(port: int):
    """Lance uvicorn dans un fil d'arrière-plan et rend l'objet serveur."""
    import uvicorn
    from main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False)
    serveur = uvicorn.Server(config)
    # Les gestionnaires de signaux n'existent que dans le fil principal ;
    # uvicorn échouerait à les installer depuis un fil secondaire.
    serveur.install_signal_handlers = lambda: None
    threading.Thread(target=serveur.run, name="uvicorn", daemon=True).start()
    return serveur


def attendre_serveur(port: int, limite: int = ATTENTE_DEMARRAGE) -> bool:
    fin = time.time() + limite
    while time.time() < fin:
        if repond(port, delai=1.0):
            return True
        time.sleep(0.25)
    return False


# ── Fenêtre ─────────────────────────────────────────────────────────────────
def fenetre(url: str, arreter) -> None:
    """Petite fenêtre de pilotage. Sans elle, rien n'indique comment quitter."""
    import tkinter as tk
    from tkinter import font as tkfont

    racine = tk.Tk()
    racine.title("PersoFolio")
    racine.resizable(False, False)
    racine.configure(bg="#F7F6F1")

    titre = tkfont.Font(family="Segoe UI", size=14, weight="bold")
    corps = tkfont.Font(family="Segoe UI", size=9)

    tk.Label(racine, text="PersoFolio est ouvert", font=titre, bg="#F7F6F1", fg="#123D2A").pack(padx=28, pady=(22, 4))
    tk.Label(racine, text="L'application s'affiche dans votre navigateur.\n"
                          "Gardez cette fenêtre ouverte pendant que vous l'utilisez.",
             font=corps, bg="#F7F6F1", fg="#5B6A61", justify="center").pack(padx=28)
    tk.Label(racine, text=url, font=corps, bg="#F7F6F1", fg="#1E6A48").pack(pady=(10, 0))

    barre = tk.Frame(racine, bg="#F7F6F1")
    barre.pack(padx=28, pady=20)
    tk.Button(barre, text="Rouvrir dans le navigateur", font=corps, relief="flat", cursor="hand2",
              bg="#FFFFFF", fg="#15261D", padx=12, pady=6,
              command=lambda: webbrowser.open(url)).pack(side="left", padx=(0, 10))
    tk.Button(barre, text="Quitter", font=corps, relief="flat", cursor="hand2",
              bg="#123D2A", fg="#FFFFFF", padx=16, pady=6,
              command=racine.destroy).pack(side="left")

    racine.protocol("WM_DELETE_WINDOW", racine.destroy)
    racine.eval("tk::PlaceWindow . center")
    racine.mainloop()
    arreter()


def alerte(titre: str, message: str) -> None:
    """Message d'erreur visible : sans console, une exception ne se voit nulle part."""
    log.error("%s — %s", titre, message)
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(titre, message)
        r.destroy()
    except Exception:
        pass


# ── Déroulé ─────────────────────────────────────────────────────────────────
def main() -> int:
    multiprocessing.freeze_support()   # sans quoi un sous-processus relancerait l'exécutable

    deja = instance_existante()
    if deja:
        log.info("instance déjà ouverte sur le port %s : on rouvre l'onglet", deja)
        webbrowser.open(f"http://localhost:{deja}/app/index.html")
        return 0

    port = choisir_port()
    url = f"http://localhost:{port}/app/index.html"
    log.info("démarrage sur le port %s — données dans %s", port, dossier_donnees())

    try:
        serveur = demarrer_serveur(port)
    except Exception as e:                                  # noqa: BLE001
        log.exception("le serveur n'a pas pu démarrer")
        alerte("PersoFolio n'a pas pu démarrer",
               f"Le serveur interne n'a pas pu être lancé.\n\n{e}\n\n"
               f"Le détail se trouve dans :\n{JOURNAL}")
        return 1

    if not attendre_serveur(port):
        alerte("PersoFolio n'a pas pu démarrer",
               "Le serveur interne ne répond pas après "
               f"{ATTENTE_DEMARRAGE} secondes.\n\nLe détail se trouve dans :\n{JOURNAL}")
        return 1

    poser_verrou(port)
    log.info("serveur prêt, ouverture du navigateur")
    webbrowser.open(url)

    def arreter():
        log.info("fermeture demandée")
        serveur.should_exit = True
        retirer_verrou()

    try:
        fenetre(url, arreter)
    except Exception:                                       # noqa: BLE001
        # Pas d'interface graphique disponible : on reste au moins utilisable.
        log.exception("fenêtre indisponible, on attend l'arrêt du serveur")
        try:
            while not serveur.should_exit:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        arreter()
    return 0


if __name__ == "__main__":
    sys.exit(main())
