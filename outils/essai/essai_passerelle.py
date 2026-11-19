"""
Essai de bout en bout de la jonction budget <-> compte-titres, dans un vrai navigateur.

FastAPI ne s'installe pas dans les sessions de travail (pas d'accès à PyPI) : la
page banque.html est servie par serveur_essai.py, qui appelle directement les
vraies fonctions de core/passerelle.py et core/banque_db.py. Ce qui est vérifié
ici est donc la vraie logique d'écriture, pas une imitation — seule la couche
FastAPI (validation pydantic, authentification) n'est pas couverte.

    python outils/essai/essai_passerelle.py .        # depuis la racine du dépôt

Demande Playwright et Chromium. Chart.js est remplacé par un bouchon.
"""
import json, os, re, sqlite3, subprocess, sys, tempfile, time
from datetime import date
from playwright.sync_api import sync_playwright

ICI = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ICI, "..", ".."))
BACK, FRONT = f"{REPO}/persofolio/backend", f"{REPO}/persofolio/frontend"
sys.path.insert(0, BACK)
TMP = tempfile.mkdtemp()
DB = f"{TMP}/portefeuille.db"
os.environ["PERSOFOLIO_DONNEES"] = TMP
from core import passerelle
from core.banque_db import BanqueDB
from core.portfolio_db import PortfolioDB
passerelle.DB_PATH = DB
pdb, bdb = PortfolioDB(DB), BanqueDB(DB)
U, AUJ = "pierre", date.today().isoformat()
pdb.creer_utilisateur(U, "motdepasse-test")
PF = pdb.creer_portefeuille(U, "Principal")

def achat(d, tk, q, p):
    return pdb.ajouter_operation(U, PF, {"date": d, "type": "ACHAT", "ticker": tk, "quantite": q, "prix": p,
                                         "devise": "EUR", "taux_eur": 1.0, "frais_eur": 0, "montant_eur": q * p})

achat("2025-03-10", "CW8", 10, 408.5)                  # 4 085 € avant le début du suivi
E = {"version": 1, "creeLe": "2026-09-01", "utilisateur": U,
     "comptes": {"courant": {"soldeInitial": 2000}, "invest": {"soldeInitial": 0}},
     "operations": [], "programmees": [], "dettes": [], "objectifs": [], "budgets": {},
     "regles": {}, "ignorees": [], "investRepris": {"le": "2026-09-01", "ancienSolde": 0, "objectifs": []}}
bdb.ecrire(U, E, 0)

srv = subprocess.Popen([sys.executable, f"{ICI}/serveur_essai.py", BACK, FRONT, "8765", DB])
time.sleep(1.2)
ok, ko = [], []
def verifier(nom, cond, detail=""):
    (ok if cond else ko).append(nom); print(("  OK  " if cond else "  KO  ") + nom + (f"  [{detail}]" if detail and not cond else ""))

def euros(txt):
    return float(re.sub(r"[^\d,\-−]", "", txt).replace("−", "-").replace(",", "."))

def depots():
    c = sqlite3.connect(DB)
    r = c.execute("SELECT date, montant_eur, origine, commentaire FROM operations WHERE type IN ('DEPOT','RETRAIT') ORDER BY id").fetchall()
    c.close(); return r

try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.on("dialog", lambda d: d.accept())
        pg.on("pageerror", lambda e: ko.append("pageerror: " + str(e)) or print("  JS ERROR", e))
        # Chart.js du CDN : remplacé par un bouchon, le réseau n'est pas garanti ici
        # Hors ligne : tout ce qui ne vient pas du serveur local est coupé. Les
        # graphiques doivent se dessiner quand même, avec les fichiers de vendor/.
        pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1") else r.abort())
        pg.goto("http://127.0.0.1:8765/banque.html")
        pg.evaluate("localStorage.setItem('persofolio_jeton','t');localStorage.setItem('persofolio_user','pierre')")
        def charger():
            pg.goto("http://127.0.0.1:8765/banque.html"); pg.wait_for_function("document.querySelector('#r-net').textContent.trim() !== '—'"); pg.wait_for_timeout(400)
        def net(): return euros(pg.inner_text("#r-net"))
        charger()

        print("1. Le défaut : cliquer plusieurs fois")
        n0 = net()
        verifier("patrimoine de départ = 2 000 + 4 085", abs(n0 - 6085) < 0.01, n0)
        verifier("la question est posée", pg.locator("#apport-externe").count() == 1)
        pg.evaluate("const b=document.querySelector('#apport-externe'); b.click(); b.click(); b.click();")
        pg.wait_for_timeout(1200)
        verifier("patrimoine inchangé après 3 clics", abs(net() - n0) < 0.01, net())
        verifier("un seul versement en base", len(depots()) == 1, depots())
        verifier("daté de la veille de l'achat", depots()[0][0] == "2025-03-09", depots())
        verifier("la question a disparu", pg.locator("#apport-externe").count() == 0)
        charger()
        verifier("toujours inchangé après rechargement", abs(net() - n0) < 0.01, net())

        print("2. Annuler la déclaration")
        pg.click("nav.onglets button[data-onglet=comptes]"); pg.wait_for_timeout(300)
        verifier("panneau des mouvements visible", pg.locator("#mouvements-titres").count() == 1)
        pg.click("[data-annuler-vir]"); pg.wait_for_timeout(1000)
        verifier("plus de versement", depots() == [], depots())
        verifier("patrimoine inchangé", abs(net() - n0) < 0.01, net())

        print("3. « De mon compte courant » pour un achat antérieur au suivi")
        pg.click("nav.onglets button[data-onglet=synthese]"); pg.wait_for_timeout(300)
        pg.dblclick("#apport-courant"); pg.wait_for_timeout(1200)
        verifier("patrimoine inchangé", abs(net() - n0) < 0.01, net())
        verifier("compte courant non débité", abs(euros(pg.inner_text("#r-courant")) - 2000) < 0.01, pg.inner_text("#r-courant"))
        verifier("un versement d'origine budget", [d[2] for d in depots()] == ["budget"], depots())

        print("4. Réparer les dégâts de l'ancien bouton")
        e, v = bdb.lire(U)
        passerelle.annuler_virement(U, e, v, "apport-1")
        for k in range(3):                                # ce que faisait l'ancien code
            e, v = bdb.lire(U)
            passerelle.virer(U, e, v, f"ancien-clic-{k}", AUJ, 4085.0, origine="externe")
        charger()
        verifier("patrimoine gonflé de 3 × 4 085", abs(net() - (n0 + 3 * 4085)) < 0.01, net())
        verifier("signalé en synthèse", pg.locator("text=versements identiques").count() >= 1)
        pg.click("nav.onglets button[data-onglet=comptes]"); pg.wait_for_timeout(300)
        verifier("3 doublons probables", pg.locator("text=doublon probable").count() == 3)
        for _ in range(3):
            pg.click("[data-annuler-vir] >> nth=0"); pg.wait_for_timeout(900)
        verifier("patrimoine revenu à 6 085", abs(net() - n0) < 0.01, net())
        verifier("plus aucun versement", depots() == [], depots())

        print("5. Achat après le début du suivi, sortie déjà importée du relevé")
        achat("2026-10-05", "AAA", 5, 100)
        e, v = bdb.lire(U)
        e["operations"].append({"id": "imp1", "date": "2026-10-03", "compte": "courant", "montant": -500,
                                "libelle": "VIR SEPA COURTIER", "categorie": "inconnu", "importe": True})
        e["comptes"]["courant"]["soldeInitial"] = 2500
        bdb.ecrire(U, e, v)
        charger()
        n1 = net()
        verifier("patrimoine = 2 000 + 4 585", abs(n1 - 6585) < 0.01, n1)
        pg.click("#apport-courant"); pg.wait_for_timeout(1200)
        verifier("patrimoine inchangé", abs(net() - n1) < 0.01, net())
        verifier("courant inchangé (sortie déjà comptée)", abs(euros(pg.inner_text("#r-courant")) - 2000) < 0.01)
        e, v = bdb.lire(U)
        imp = [o for o in e["operations"] if o["id"] == "imp1"][0]
        verifier("la ligne importée devient le virement", imp["categorie"] == "virement" and imp["virementId"].startswith("apport-"), imp)
        pg.click("nav.onglets button[data-onglet=comptes]"); pg.wait_for_timeout(300)
        pg.click("[data-annuler-vir] >> nth=0"); pg.wait_for_timeout(1000)   # le plus récent : apport-2
        e, v = bdb.lire(U)
        imp = [o for o in e["operations"] if o["id"] == "imp1"][0]
        verifier("annulé : la ligne importée retrouve sa catégorie", imp["categorie"] == "inconnu" and "virementId" not in imp, imp)
        verifier("patrimoine toujours inchangé", abs(net() - n1) < 0.01, net())

        print("6. Modifier un virement ne le double pas")
        pg.click("[data-virer-ici]"); pg.fill("#t-montant", "300"); pg.click("#t-form button[type=submit]"); pg.wait_for_timeout(1000)
        pg.click("[data-modif] >> nth=0"); pg.wait_for_timeout(200)
        pg.fill("#t-montant", "500"); pg.click("#t-form button[type=submit]"); pg.wait_for_timeout(1000)
        mv = [d for d in depots() if d[3].startswith("virement budget ") and not "apport" in d[3]]
        verifier("un seul versement, de 500", [d[1] for d in mv] == [500.0], depots())
        verifier("patrimoine inchangé par un virement interne", abs(net() - n1) < 0.01, net())
        pg.click("[data-modif] >> nth=0"); pg.wait_for_timeout(200)
        pg.click("#t-suppr"); pg.wait_for_timeout(1000)
        mv = [d for d in depots() if not "apport" in d[3]]
        verifier("supprimer le virement retire le versement", mv == [], depots())
        verifier("patrimoine inchangé", abs(net() - n1) < 0.01, net())

        print("7. Corriger le solde du compte courant")
        pg.click("#btn-corriger-solde"); pg.fill("#c-reel", "1850,40"); pg.click("#c-form button[type=submit]"); pg.wait_for_timeout(800)
        verifier("courant = solde réel", abs(euros(pg.inner_text("#r-courant")) - 1850.40) < 0.01, pg.inner_text("#r-courant"))
        e, v = bdb.lire(U)
        c = [o for o in e["operations"] if o.get("correction")]
        verifier("une ligne de correction visible", len(c) == 1 and abs(c[0]["montant"] + 149.60) < 0.01, c)
        pg.click(f"[data-modif='{c[0]['id']}']"); pg.wait_for_timeout(200)
        pg.click("#c-suppr"); pg.wait_for_timeout(800)
        verifier("correction supprimée", abs(euros(pg.inner_text("#r-courant")) - 2000) < 0.01, pg.inner_text("#r-courant"))

        print("8. Hors ligne : les bibliothèques embarquées fonctionnent")
        pg.click("nav.onglets button[data-onglet=synthese]"); pg.wait_for_timeout(500)
        verifier("Chart.js 4.4.1 chargé depuis vendor/", pg.evaluate("window.Chart && Chart.version") == "4.4.1")
        verifier("des graphiques sont dessinés", pg.evaluate("Object.keys(Chart.instances).length") > 0)
        n_pays = pg.evaluate("""async () => {
            await new Promise((ok, ko) => { const s = document.createElement('script');
              s.src = 'vendor/chartjs-chart-geo.umd.min.js'; s.onload = ok; s.onerror = ko; document.head.appendChild(s); });
            const topo = await (await fetch('vendor/countries-110m.json')).json();
            return ChartGeo.topojson.feature(topo, topo.objects.countries).features.length; }""")
        verifier("module de carte et fond de carte lisibles", n_pays == 177, n_pays)

        print("9. Versement importé dans le compte-titres : d'où vient-il ? (19 novembre)")
        def reponses():
            c = sqlite3.connect(DB)
            try: return c.execute("SELECT operation_id, origine FROM banque_origines ORDER BY operation_id").fetchall()
            except sqlite3.OperationalError: return []
            finally: c.close()
        def lignes(): return pg.locator(".a-expliquer .ligne-question").count()
        charger(); n_avant = net(); c_avant = euros(pg.inner_text("#r-courant"))
        dep = pdb.ajouter_operation(U, PF, {"date": "2026-10-12", "type": "DEPOT", "montant_eur": 750.0,
                                            "devise": "EUR", "taux_eur": 1.0, "origine": "import_ordres"})
        ret = pdb.ajouter_operation(U, PF, {"date": "2026-10-20", "type": "RETRAIT", "montant_eur": 200.0,
                                            "devise": "EUR", "taux_eur": 1.0, "origine": "manuel"})
        charger()
        verifier("deux lignes posées", lignes() == 2, lignes())
        verifier("le versement est compté tant qu'on ne sait pas d'où il vient", abs(net() - (n_avant + 550)) < 0.01, net())
        pg.evaluate(f"const b=document.querySelector('[data-vers=\"{dep}\"][data-origine=budget]'); b.click(); b.click(); b.click();")
        pg.wait_for_timeout(1500)
        verifier("trois clics, une seule réponse", reponses() == [(dep, "budget")], reponses())
        verifier("la ligne a disparu, l'autre reste", lignes() == 1, lignes())
        verifier("le compte courant porte le versement", abs(euros(pg.inner_text("#r-courant")) - (c_avant - 750)) < 0.01, pg.inner_text("#r-courant"))
        verifier("le patrimoine ne compte plus 750 € en double", abs(net() - (n_avant - 200)) < 0.01, net())
        pg.click(f"[data-vers='{ret}'][data-origine=budget]"); pg.wait_for_timeout(1500)
        verifier("retrait rattaché : le courant le reçoit", abs(euros(pg.inner_text("#r-courant")) - (c_avant - 550)) < 0.01, pg.inner_text("#r-courant"))
        verifier("le patrimoine revient à son niveau", abs(net() - n_avant) < 0.01, net())
        verifier("plus aucun mouvement en question", lignes() == 0, lignes())

        print("10. Annuler une réponse, puis répondre pour tous")
        pg.click("nav.onglets button[data-onglet=comptes]"); pg.wait_for_timeout(600)
        pg.click(f"[data-annuler-rep='{dep}']"); pg.wait_for_timeout(1500)
        verifier("réponse retirée", reponses() == [(ret, "budget")], reponses())
        verifier("la ligne du courant est retirée", abs(euros(pg.inner_text("#r-courant")) - (c_avant + 200)) < 0.01, pg.inner_text("#r-courant"))
        pg.click(f"[data-annuler-rep='{ret}']"); pg.wait_for_timeout(1500)
        pg.click("nav.onglets button[data-onglet=synthese]"); pg.wait_for_timeout(600)
        verifier("les deux questions reviennent", lignes() == 2, lignes())
        pg.click("[data-vers-tous=externe]"); pg.wait_for_timeout(1500)
        verifier("pour tous : ailleurs", reponses() == [(dep, "externe"), (ret, "externe")], reponses())
        verifier("le courant n'a pas bougé", abs(euros(pg.inner_text("#r-courant")) - c_avant) < 0.01, pg.inner_text("#r-courant"))

        print("11. Le centime qui ne partait pas")
        for jour, prix in (("2026-10-07", 10.004), ("2026-10-08", 20.004)):
            pdb.ajouter_operation(U, PF, {"date": jour[:-1] + "6" if jour.endswith("7") else "2026-10-07", "type": "DEPOT",
                                          "montant_eur": round(prix, 2), "devise": "EUR", "taux_eur": 1.0, "origine": "externe"})
            achat(jour, "AAA", 1, prix)
        charger()
        # Reste seul l'achat de la section 5, laissé sans réponse : 500,00 € tout rond.
        verifier("restes de moins d'un centime : seul l'achat de 500 € reste à expliquer",
                 "500,00" in pg.inner_text(".a-expliquer .ligne") and pg.evaluate("INV.a_expliquer_detail.length") == 1,
                 pg.locator(".a-expliquer").all_inner_texts())
        b.close()
finally:
    srv.terminate()
print(f"\n{len(ok)} réussis, {len(ko)} échecs")
sys.exit(1 if ko else 0)
