"""
Lance l'application construite en mode d'essai, deux fois, et résume le résultat.

    python outils/essai/lancer_essai_bureau.py <exécutable> <dossier de sortie> [<dossier construit>]

- Étape 1 : installation neuve (dossier de données vide). L'application ouvre sa
  fenêtre, démarre son serveur, charge le profil d'exemple et vérifie le
  patrimoine de référence.
- Étape 2 : second lancement sur les mêmes données. La session doit avoir
  survécu à la fermeture, et le patrimoine être toujours là.

Pendant chaque étape, la fenêtre est photographiée (macOS : screencapture ;
Windows : capture d'écran PowerShell, au mieux). Le résumé — temps de démarrage,
contrôles, taille — est écrit dans $GITHUB_STEP_SUMMARY quand il existe.

Le code de sortie est non nul si un contrôle échoue.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

EXE, SORTIE = sys.argv[1], sys.argv[2]
CONSTRUIT = sys.argv[3] if len(sys.argv) > 3 else None
os.makedirs(SORTIE, exist_ok=True)
DONNEES = tempfile.mkdtemp(prefix="persofolio-essai-")
PAUSE = 4


def capture(chemin: str) -> bool:
    try:
        if sys.platform == "darwin":
            return subprocess.run(["screencapture", "-x", chemin], timeout=30).returncode == 0
        if sys.platform == "win32":
            ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                  "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
                  "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
                  "$g=[System.Drawing.Graphics]::FromImage($i);"
                  "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
                  f"$i.Save('{chemin}')")
            return subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=60).returncode == 0
    except Exception as e:                                  # noqa: BLE001
        print(f"capture impossible : {e}")
    return False


def lancer(etape: str, precedent: str | None = None) -> dict:
    rapport = os.path.join(SORTIE, f"rapport-etape{etape}.json")
    env = {**os.environ, "PERSOFOLIO_DONNEES": DONNEES, "PERSOFOLIO_ESSAI": rapport,
           "PERSOFOLIO_ESSAI_ETAPE": etape, "PERSOFOLIO_ESSAI_PAUSE": str(PAUSE)}
    if precedent:
        env["PERSOFOLIO_ESSAI_PRECEDENT"] = precedent
    print(f"\n── Étape {etape} ──")
    t0 = time.perf_counter()
    proc = subprocess.Popen([EXE], env=env)
    temoin, photo = rapport + ".pret", False
    while proc.poll() is None and time.perf_counter() - t0 < 240:
        if not photo and os.path.exists(temoin):
            photo = capture(os.path.join(SORTIE, f"fenetre-etape{etape}.png"))
        time.sleep(0.3)
    if proc.poll() is None:
        proc.kill()
        print("l'application ne s'est pas fermée : arrêtée de force")
    duree = round(time.perf_counter() - t0, 2)
    try:
        with open(rapport, encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError):
        r = {"ok": False, "erreurs": ["aucun rapport écrit — voir journal.txt"], "controles": {}}
    r["processus_s"] = duree
    r["code_sortie"] = proc.returncode
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return r


def taille_mo(chemin: str) -> float:
    total = 0
    for d, _, fichiers in os.walk(chemin):
        for f in fichiers:
            p = os.path.join(d, f)
            if not os.path.islink(p):
                total += os.path.getsize(p)
    return round(total / 1_048_576, 1)


r1 = lancer("1")
r2 = lancer("2", os.path.join(SORTIE, "rapport-etape1.json"))

journal = os.path.join(DONNEES, "journal.txt")
if os.path.exists(journal):
    with open(journal, encoding="utf-8", errors="replace") as f:
        contenu = f.read()
    with open(os.path.join(SORTIE, "journal.txt"), "w", encoding="utf-8") as f:
        f.write(contenu)

nom = {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, sys.platform)
s = lambda r, k: f"{r[k]:.1f} s" if isinstance(r.get(k), (int, float)) else "—"
lignes = [
    f"## {nom} — essai de l'application construite",
    "",
    "| | 1er lancement (installation neuve) | 2e lancement |",
    "|---|---|---|",
    f"| Fenêtre affichée | {s(r1, 'fenetre_s')} | {s(r2, 'fenetre_s')} |",
    f"| Serveur prêt | {s(r1, 'serveur_s')} | {s(r2, 'serveur_s')} |",
    f"| Page Budget prête | {s(r1, 'page_s')} | {s(r2, 'page_s')} |",
    f"| Durée totale du processus (essai compris) | {s(r1, 'processus_s')} | {s(r2, 'processus_s')} |",
    "",
    "Temps comptés depuis le démarrage de Python dans l'application ; la durée totale "
    f"inclut les vérifications et une pause de {PAUSE} s pour la capture d'écran.",
    "",
]
if CONSTRUIT and os.path.exists(CONSTRUIT):
    lignes += [f"Taille de l'application : **{taille_mo(CONSTRUIT)} Mo**", ""]
for etape, r in (("1", r1), ("2", r2)):
    lignes.append(f"**Étape {etape}** : {'✅ réussie' if r.get('ok') else '❌ échec'}")
    for nom_c, c in (r.get("controles") or {}).items():
        detail = f" — `{c['detail']}`" if "detail" in c else ""
        lignes.append(f"- {'✅' if c['ok'] else '❌'} {nom_c}{detail}")
    for e in r.get("erreurs") or []:
        if not any(e.startswith(n) for n in (r.get("controles") or {})):
            lignes.append(f"- ❌ {e}")
    lignes.append("")
resume = "\n".join(lignes)
print("\n" + resume)
with open(os.path.join(SORTIE, "resume.md"), "w", encoding="utf-8") as f:
    f.write(resume)
if os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
        f.write(resume + "\n")

sys.exit(0 if r1.get("ok") and r2.get("ok") else 1)
