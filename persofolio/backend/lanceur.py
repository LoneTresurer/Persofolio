"""
lanceur.py
==========
Point d'entrée de l'application de bureau (Windows et macOS) : un double-clic,
et PersoFolio s'ouvre dans sa propre fenêtre. Aucune ligne de commande, aucune
installation de Python.

Ce que fait ce fichier, dans l'ordre :

1. **Il ouvre la fenêtre tout de suite**, sur un écran « Chargement… ». Le
   démarrage du serveur prend quelques secondes (pandas, numpy, FastAPI) : mieux
   vaut une fenêtre qui attend qu'un double-clic qui semble ne rien faire.
2. **Il cherche un port libre.** 8000 d'abord ; sinon le suivant. Les pages
   s'adressent au serveur qui les a servies, n'importe quel port convient.
3. **Il refuse de se lancer deux fois.** Un second double-clic ramène la fenêtre
   existante au premier plan au lieu de démarrer un second serveur sur la même
   base. (Sur macOS, le système s'en charge déjà.)
4. **Il démarre le serveur en arrière-plan**, attend qu'il réponde vraiment, puis
   affiche l'application dans la fenêtre. Fermer la fenêtre arrête tout.

La fenêtre est celle du système — WebView2 (le moteur d'Edge) sous Windows,
WKWebView (celui de Safari) sous macOS — par l'intermédiaire de pywebview. Aucun
navigateur n'est embarqué : la page s'affiche à la même vitesse qu'un onglet.

**Repli.** Si WebView2 manque (vieux Windows 10 jamais mis à jour) ou si pywebview
ne se charge pas, l'application s'ouvre dans le navigateur, avec une petite
fenêtre pour la quitter — comme avant l'arrivée de la fenêtre native.

**Mode d'essai.** `PERSOFOLIO_ESSAI=<fichier.json>` : l'application démarre,
ouvre une session locale, charge le profil d'exemple, mesure ses temps de
démarrage, vérifie la page, écrit un rapport et se ferme. C'est ce que lance la
construction automatique (outils/essai/essai_bureau.py) sur les machines Windows
et macOS de GitHub.

Les comptes sont écrits dans le dossier de données de l'utilisateur (voir
`core/emplacements.py`), jamais dans celui du programme.
"""

from __future__ import annotations

import time

T0 = time.perf_counter()          # le plus tôt possible : sert à mesurer le démarrage

import json                        # noqa: E402
import logging                     # noqa: E402
import multiprocessing             # noqa: E402
import os                          # noqa: E402
import socket                      # noqa: E402
import sys                         # noqa: E402
import threading                   # noqa: E402
import urllib.error                # noqa: E402
import urllib.request              # noqa: E402
import webbrowser                  # noqa: E402

PORT_PREFERE = 8000
PORTS_ESSAYES = 12          # 8000 à 8011 : au-delà, quelque chose ne tourne pas rond
ATTENTE_DEMARRAGE = 90      # secondes ; la première ouverture après installation est lente
WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"


# ── Sorties standard ────────────────────────────────────────────────────────
def _assurer_sorties() -> None:
    """Sans console, `sys.stdout` vaut None et la moindre écriture lève une erreur.

    L'application est construite sans console : il n'y a nulle part où écrire.
    Toute bibliothèque qui fait un `print` — uvicorn au démarrage, par exemple —
    ferait tomber le programme. On leur donne un trou noir où écrire ; ce qui
    compte est de toute façon consigné dans le journal.
    """
    for nom in ("stdout", "stderr"):
        if getattr(sys, nom, None) is None:
            setattr(sys, nom, open(os.devnull, "w", encoding="utf-8"))


_assurer_sorties()

from core.emplacements import RACINE, dossier_donnees   # noqa: E402  (après _assurer_sorties)

FICHIER_VERROU = os.path.join(dossier_donnees(), "instance.json")
JOURNAL = os.path.join(dossier_donnees(), "journal.txt")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.FileHandler(JOURNAL, encoding="utf-8")],
)
log = logging.getLogger("persofolio.lanceur")


def lire_version() -> str:
    """La version vient du fichier VERSION, embarqué à côté des pages."""
    for chemin in (os.path.join(RACINE, "VERSION"),                         # application construite
                   os.path.join(os.path.dirname(RACINE), "VERSION")):       # depuis les sources
        try:
            with open(chemin, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            continue
    return "?"


VERSION = lire_version()


# ── Ports ───────────────────────────────────────────────────────────────────
def port_libre(port: int) -> bool:
    """Essaie de réserver le port pour de bon.

    Le port compte plus qu'il n'y paraît : la session et les préférences des pages
    vivent dans le stockage de la fenêtre, rangé par origine — port compris.
    Changer de port d'un lancement à l'autre, c'est se retrouver déconnecté.

    Sous Windows, pas de SO_REUSEADDR : là-bas, cette option autorise à se lier
    à un port qu'un autre programme écoute déjà. `SO_EXCLUSIVEADDRUSE` dit
    explicitement l'inverse.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        exclusif = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)   # Windows seulement
        if exclusif is not None:
            try:
                s.setsockopt(socket.SOL_SOCKET, exclusif, 1)
            except OSError:
                pass
        else:
            # macOS et Linux : SO_REUSEADDR y a l'effet inverse de Windows. Il
            # n'autorise pas à prendre un port qu'un programme écoute, seulement un
            # port qui finit de se fermer (TIME_WAIT) — exactement ce que fait
            # uvicorn. Sans lui, un lancement juste après une fermeture trouvait 8000
            # « occupé » et prenait 8001 : nouvelle origine pour la fenêtre, donc
            # session et préférences (localStorage) perdues.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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


def ramener_au_premier_plan(port: int) -> bool:
    """Demande à l'instance déjà ouverte de montrer sa fenêtre."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/bureau/premier-plan", method="POST")
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


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
def demarrer_serveur(port: int, au_premier_plan=None):
    """Lance uvicorn dans un fil d'arrière-plan et rend l'objet serveur.

    `au_premier_plan` : fonction appelée quand un second lancement demande la
    fenêtre. La route est ajoutée ici, à l'application déjà construite, pour ne
    pas toucher à main.py : elle n'a de sens que dans l'application de bureau.
    """
    import uvicorn
    from main import app

    if au_premier_plan is not None:
        def premier_plan():
            au_premier_plan()
            return {"ok": True}
        app.add_api_route("/bureau/premier-plan", premier_plan, methods=["POST"],
                          include_in_schema=False)

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
        time.sleep(0.1)
    return False


# ── Fenêtre native ──────────────────────────────────────────────────────────
def webview2_present() -> bool:
    """Vrai si le moteur WebView2 est installé (toujours vrai hors Windows).

    C'est la méthode documentée par Microsoft : la clé de registre du runtime,
    pour la machine ou pour l'utilisateur. Windows 11 l'a d'office, Windows 10
    l'a reçu avec Edge ; seul un poste jamais mis à jour en est dépourvu.
    """
    if not WINDOWS:
        return True
    import winreg
    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    emplacements = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
    ]
    for racine, cle in emplacements:
        try:
            with winreg.OpenKey(racine, cle) as k:
                valeur, _ = winreg.QueryValueEx(k, "pv")
                if valeur and valeur != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


PAGE_CHARGEMENT = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<style>
  html, body { height: 100%; margin: 0; }
  body { display: flex; align-items: center; justify-content: center; background: #F2F4F1;
         font-family: -apple-system, "Segoe UI", system-ui, sans-serif; color: #15261D; }
  .bloc { text-align: center; max-width: 520px; padding: 24px; }
  h1 { font-size: 26px; font-weight: 600; margin: 0 0 10px; color: #1E6A48; letter-spacing: -.01em; }
  p { margin: 0; color: #5B6A61; font-size: 14px; line-height: 1.5; }
  .barre { width: 180px; height: 3px; margin: 22px auto 0; background: #E3EFE7; border-radius: 2px; overflow: hidden; }
  .barre i { display: block; width: 40%; height: 100%; background: #1E6A48; border-radius: 2px;
             animation: aller 1.1s ease-in-out infinite alternate; }
  @keyframes aller { from { transform: translateX(-10%); } to { transform: translateX(160%); } }
  .erreur h1 { color: #B23A2E; }
  .erreur .barre { display: none; }
  code { display: block; margin-top: 14px; font-size: 12px; color: #15261D; word-break: break-all; }
  @media (prefers-color-scheme: dark) {
    body { background: #121A16; color: #E6ECE8; } p { color: #9DAAA2; } code { color: #E6ECE8; }
    h1 { color: #6FBF95; } .barre { background: #1F2D26; } .barre i { background: #6FBF95; }
  }
</style></head><body>
<div class="bloc" id="bloc"><h1>PersoFolio</h1><p id="message">Chargement…</p><div class="barre"><i></i></div></div>
<script>
  function afficherErreur(titre, detail, journal) {
    document.getElementById('bloc').className = 'bloc erreur';
    document.querySelector('h1').textContent = titre;
    document.getElementById('message').textContent = detail;
    var c = document.createElement('code'); c.textContent = 'Détail : ' + journal;
    document.getElementById('bloc').appendChild(c);
  }
</script></body></html>"""


def ouvrir_fenetre_native(port: int, essai: str | None) -> int:
    """Fenêtre de l'application. Rend le code de sortie du programme."""
    import webview

    url = f"http://localhost:{port}/app/index.html"
    etat = {"serveur": None, "code": 0}
    try:
        # Les liens « target=_blank » (actualités des titres) s'ouvrent dans le
        # navigateur, pas dans la fenêtre de l'application.
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview.settings["ALLOW_DOWNLOADS"] = True
    except (AttributeError, TypeError):
        pass

    fenetre = webview.create_window(
        "PersoFolio", html=PAGE_CHARGEMENT, width=1360, height=880,
        min_size=(980, 640), background_color="#F2F4F1", text_select=True)

    def au_premier_plan():
        try:
            fenetre.restore()
            fenetre.show()
            fenetre.on_top = True
            fenetre.on_top = False
        except Exception:                                   # noqa: BLE001
            log.exception("premier plan impossible")

    def erreur(titre: str, detail: str):
        log.error("%s — %s", titre, detail)
        try:
            fenetre.evaluate_js(f"afficherErreur({json.dumps(titre)}, {json.dumps(detail)}, "
                                f"{json.dumps(JOURNAL)})")
        except Exception:                                   # noqa: BLE001
            pass

    def demarrage():
        """Exécuté dans un fil à part, une fois la fenêtre affichée."""
        mesures = {"fenetre_s": round(time.perf_counter() - T0, 2)}
        log.info("fenêtre affichée en %.2f s ; démarrage du serveur sur le port %s",
                 mesures["fenetre_s"], port)
        try:
            etat["serveur"] = demarrer_serveur(port, au_premier_plan)
        except Exception as e:                              # noqa: BLE001
            log.exception("le serveur n'a pas pu démarrer")
            erreur("PersoFolio n'a pas pu démarrer", f"Le serveur interne n'a pas pu être lancé : {e}")
            etat["code"] = 1
            return
        if not attendre_serveur(port):
            erreur("PersoFolio n'a pas pu démarrer",
                   f"Le serveur interne ne répond pas après {ATTENTE_DEMARRAGE} secondes.")
            etat["code"] = 1
            return
        mesures["serveur_s"] = round(time.perf_counter() - T0, 2)
        log.info("serveur prêt en %.2f s", mesures["serveur_s"])
        if not essai:
            poser_verrou(port)
        if essai:
            from essai_bureau import Essai
            Essai(fenetre, port, essai, mesures, T0, etat).lancer()
        else:
            fenetre.load_url(url)

    def fermeture():
        if etat.get("ferme"):
            return
        etat["ferme"] = True
        log.info("fenêtre fermée")
        srv = etat.get("serveur")
        if srv is not None:
            srv.should_exit = True
        if not essai:
            retirer_verrou()

    fenetre.events.closed += fermeture
    webview.start(
        demarrage,
        gui="edgechromium" if WINDOWS else None,
        # La session et les préférences des pages vivent dans localStorage : sans
        # stockage persistant, il faudrait se reconnecter à chaque lancement.
        private_mode=False,
        storage_path=os.path.join(dossier_donnees(), "fenetre"),
        debug=bool(os.environ.get("PERSOFOLIO_DEBUG")),
    )
    fermeture()
    return etat["code"]


# ── Repli : navigateur + petite fenêtre ─────────────────────────────────────
def ouvrir_dans_le_navigateur(port: int) -> int:
    """L'ancien fonctionnement, gardé pour les postes sans WebView2."""
    url = f"http://localhost:{port}/app/index.html"
    try:
        serveur = demarrer_serveur(port)
    except Exception as e:                                  # noqa: BLE001
        log.exception("le serveur n'a pas pu démarrer")
        alerte("PersoFolio n'a pas pu démarrer",
               f"Le serveur interne n'a pas pu être lancé.\n\n{e}\n\nLe détail se trouve dans :\n{JOURNAL}")
        return 1
    if not attendre_serveur(port):
        alerte("PersoFolio n'a pas pu démarrer",
               f"Le serveur interne ne répond pas après {ATTENTE_DEMARRAGE} secondes.\n\n"
               f"Le détail se trouve dans :\n{JOURNAL}")
        return 1
    poser_verrou(port)
    webbrowser.open(url)

    def arreter():
        serveur.should_exit = True
        retirer_verrou()

    try:
        fenetre_tk(url, arreter)
    except Exception:                                       # noqa: BLE001
        log.exception("fenêtre indisponible, on attend l'arrêt du serveur")
        try:
            while not serveur.should_exit:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        arreter()
    return 0


def fenetre_tk(url: str, arreter) -> None:
    """Petite fenêtre de pilotage du mode navigateur. Sans elle, rien n'indique comment quitter."""
    import tkinter as tk
    from tkinter import font as tkfont

    racine = tk.Tk()
    racine.title("PersoFolio")
    racine.resizable(False, False)
    racine.configure(bg="#F2F4F1")
    titre = tkfont.Font(family="Segoe UI", size=14, weight="bold")
    corps = tkfont.Font(family="Segoe UI", size=9)
    tk.Label(racine, text="PersoFolio est ouvert", font=titre, bg="#F2F4F1", fg="#1E6A48").pack(padx=28, pady=(22, 4))
    tk.Label(racine, text="L'application s'affiche dans votre navigateur.\n"
                          "Gardez cette fenêtre ouverte pendant que vous l'utilisez.",
             font=corps, bg="#F2F4F1", fg="#5B6A61", justify="center").pack(padx=28)
    tk.Label(racine, text=url, font=corps, bg="#F2F4F1", fg="#1E6A48").pack(pady=(10, 0))
    barre = tk.Frame(racine, bg="#F2F4F1")
    barre.pack(padx=28, pady=20)
    tk.Button(barre, text="Rouvrir dans le navigateur", font=corps, relief="flat", cursor="hand2",
              bg="#FFFFFF", fg="#15261D", padx=12, pady=6,
              command=lambda: webbrowser.open(url)).pack(side="left", padx=(0, 10))
    tk.Button(barre, text="Quitter", font=corps, relief="flat", cursor="hand2",
              bg="#1E6A48", fg="#FFFFFF", padx=16, pady=6,
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
    except Exception:                                       # noqa: BLE001
        pass


# ── Déroulé ─────────────────────────────────────────────────────────────────
def main() -> int:
    multiprocessing.freeze_support()   # sans quoi un sous-processus relancerait l'exécutable
    essai = os.environ.get("PERSOFOLIO_ESSAI") or None

    if not essai:
        deja = instance_existante()
        if deja:
            log.info("instance déjà ouverte sur le port %s : on la ramène au premier plan", deja)
            if not ramener_au_premier_plan(deja):
                webbrowser.open(f"http://localhost:{deja}/app/index.html")
            return 0

    port = choisir_port()
    log.info("PersoFolio %s — port %s — données dans %s", VERSION, port, dossier_donnees())

    natif = webview2_present()
    if natif:
        try:
            import webview  # noqa: F401
        except Exception:                                   # noqa: BLE001
            log.exception("pywebview indisponible : repli sur le navigateur")
            natif = False
    else:
        log.warning("WebView2 absent : repli sur le navigateur")

    if essai and not natif:
        # L'essai doit dire que la fenêtre native manque, pas réussir en mode dégradé.
        with open(essai, "w", encoding="utf-8") as f:
            json.dump({"ok": False, "erreurs": ["fenêtre native indisponible (WebView2 ou pywebview)"],
                       "version": VERSION, "plateforme": sys.platform}, f, ensure_ascii=False)
        return 1

    if natif:
        return ouvrir_fenetre_native(port, essai)
    return ouvrir_dans_le_navigateur(port)


if __name__ == "__main__":
    sys.exit(main())
