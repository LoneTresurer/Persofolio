"""
Essai de bout en bout du module Budget et comptes, « tout vient des relevés »,
dans un vrai navigateur.

    python outils/essai/essai_releves.py .        # depuis la racine du dépôt

La page banque.html est servie par serveur_essai.py, qui appelle les vraies
fonctions du serveur (lecture des tableurs, passerelle, base). Le dépôt étant
public, il ne contient aucun relevé PDF réel : pour les relevés PDF, la réponse
de /banque/releves est fabriquée ici, au format exact de core/import_releves.py.
Le tableur, lui, est lu par le vrai lecteur (tests/tableurs/debit_credit.csv).

Ce qui est vérifié :
 1. un ancien état (saisi à la main) n'est plus affiché, mais conservé ;
 2. aucune saisie d'opération n'est possible ;
 3. le solde est ancré sur la clôture du dernier relevé ;
 4. un relevé manquant devient une ligne « Relevé manquant », ni crédit ni débit ;
 5. un virement vers le compte-titres est reconnu sans rien écrire là-bas ;
 6. un prélèvement d'emprunt est repéré, l'emprunt configuré, ses échéances
    retrouvées dans les relevés, le capital au centime du contrat ;
 7. un remboursement anticipé se rattache depuis le relevé ;
 8. réimporter les mêmes relevés ne double rien ;
 9. un tableur sans solde demande le solde de sa dernière date.

Demande Playwright et Chromium.
"""
import json, os, sqlite3, subprocess, sys, tempfile, time
from datetime import datetime
from playwright.sync_api import sync_playwright

ICI = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ICI, "..", ".."))
BACK, FRONT = f"{REPO}/persofolio/backend", f"{REPO}/persofolio/frontend"
sys.path.insert(0, BACK)
TMP = tempfile.mkdtemp()
DB = f"{TMP}/portefeuille.db"
os.environ["PERSOFOLIO_DONNEES"] = TMP
from core.banque_db import BanqueDB
from core.portfolio_db import PortfolioDB
pdb, bdb = PortfolioDB(DB), BanqueDB(DB)
U = "pierre"
pdb.creer_utilisateur(U, "motdepasse-test")
PF = pdb.creer_portefeuille(U, "Principal")
# Un versement fait chez le courtier le 29 octobre : le relevé du courant en montre la sortie le 28.
pdb.ajouter_operation(U, PF, {"date": "2026-10-29", "type": "DEPOT", "montant_eur": 150.0, "devise": "EUR", "taux_eur": 1.0, "frais_eur": 0})
# Un état de l'ancienne version, saisi à la main
bdb.ecrire(U, {"version": 1, "creeLe": "2026-09-01", "utilisateur": U, "comptes": {"courant": {"soldeInitial": 2000}, "invest": {"soldeInitial": 0}},
               "operations": [], "programmees": [{"id": "p1", "libelle": "Salaire"}], "dettes": [], "objectifs": [], "budgets": {}}, 0)

PORT = 8766
srv = subprocess.Popen([sys.executable, f"{ICI}/serveur_essai.py", BACK, FRONT, str(PORT), DB])
time.sleep(1.2)
ok, ko = [], []
def verifier(nom, cond, detail=""):
    (ok if cond else ko).append(nom); print(("  OK  " if cond else "  KO  ") + nom + (f"  [{detail}]" if detail and not cond else ""))

# ── Relevés PDF fabriqués : août, septembre, octobre. Octobre commence le 4 :
#    les opérations du 1er au 3 manquent (−64,20 €).
MOIS = [("2026-08", "2026-08-01", "2026-08-31"), ("2026-09", "2026-09-01", "2026-09-30"), ("2026-10", "2026-10-04", "2026-10-31")]
TIERS = {"VIR SALAIRE ATELIER": ("salaire", "ENTREE"), "PRLV LOYER SCI": ("logement", "SORTIE"), "ECH PRET AUTO 0471": ("credit", "SORTIE"),
         "CB CARREFOUR": ("courses", "SORTIE"), "VIR BOURSORAMA TITRES": ("divers", "SORTIE"), "VIR REMBT ANTICIPE": ("divers", "SORTIE"),
         "CB BOULANGERIE": ("courses", "SORTIE")}
def ops_du(m):
    o = [(f"{m}-05", "PRLV LOYER SCI", -700.0), (f"{m}-10", "ECH PRET AUTO 0471", -232.60), (f"{m}-15", "CB CARREFOUR", -300.0), (f"{m}-27", "VIR SALAIRE ATELIER", 2000.0)]
    if m == "2026-10":
        o += [(f"{m}-02", "CB BOULANGERIE", -64.20), (f"{m}-20", "VIR REMBT ANTICIPE", -1000.0), (f"{m}-28", "VIR BOURSORAMA TITRES", -150.0)]
    return sorted(o)
def reponse_pdf():
    fichiers, toutes, solde = [], [], 1000.0
    for m, debut, fin in MOIS:
        ops = ops_du(m)
        avant = sum(v for d, _, v in ops if d < debut)
        ouverture, lues = round(solde + avant, 2), [(d, l, v) for d, l, v in ops if d >= debut]
        cloture = round(solde + sum(v for _, _, v in ops), 2)
        solde = cloture
        fichiers.append({"nom": f"releve-{m}.pdf", "banque": "Banque d'essai", "anomalies": [], "comptes": [{
            "numero": "0001", "libelle": "Compte courant", "tableur": False, "debut": debut, "fin": fin,
            "solde_ouverture": ouverture, "solde_cloture": cloture, "mouvements": round(cloture - ouverture, 2), "nb_operations": len(lues),
            "ecart": 0.0, "equilibre": True, "operations": [{"date": d, "libelle": l, "montant": v} for d, l, v in lues]}]})
        toutes += lues
    tiers = []
    for cle, (cat, sens) in TIERS.items():
        x = [(d, v) for d, l, v in toutes if l == cle]
        if x: tiers.append({"cle": cle, "libelle": cle, "categorie": cat, "confiance": "sure", "sens": sens, "total": round(sum(v for _, v in x), 2),
                            "occurrences": len(x), "mois": sorted({d[:7] for d, _ in x}), "montant_type": abs(x[-1][1]), "jour_type": int(x[-1][0][8:]),
                            "stabilite": 0.0, "ecart_jour": 0.0})
    return {"fichiers": fichiers, "proposition": {"mois_complets": ["2026-08", "2026-09"], "mois_complets_ignores": [], "budgets": {}, "recurrences": [],
            "mesure": {"operations": len(toutes), "tiers": len(tiers), "tiers_inconnus": 0, "part_euros_inconnus": 0.0, "tiers_a_relire_pour_80pct": 0},
            "tiers": tiers, "operations": [{"date": d, "libelle": l, "montant": v, "tiers": l} for d, l, v in toutes]}}
CLOTURE_OCT = round(1000 + sum(v for m, _, _ in MOIS for _, _, v in ops_du(m)), 2)

try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.on("dialog", lambda d: d.accept())
        pg.on("pageerror", lambda e: ko.append("pageerror: " + str(e)) or print("  JS ERROR", e))
        pg.clock.install(time=datetime.fromisoformat("2026-11-23T12:00:00"))
        pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1") else r.abort())
        base = f"http://127.0.0.1:{PORT}/banque.html"
        pg.goto(base)
        pg.evaluate("localStorage.setItem('persofolio_jeton','t');localStorage.setItem('persofolio_user','pierre')")
        pg.goto(base)
        J = lambda expr: pg.evaluate(expr)
        def onglet(nom): pg.click(f"nav.onglets button[data-onglet={nom}]"); pg.wait_for_timeout(300)
        def envoyer_pdf():
            pg.route("**/banque/releves", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(reponse_pdf())))
            pg.set_input_files("#imp-fichiers", [{"name": f"releve-{m}.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4"} for m, _, _ in MOIS])
            pg.wait_for_selector("#imp-valider")
            pg.unroute("**/banque/releves")

        print("1. Un ancien état n'est plus affiché, mais conservé")
        pg.wait_for_selector("#imp-fichiers", state="attached")
        verifier("l'écran d'import s'ouvre, pas l'application", pg.locator("#app").is_hidden() and pg.locator("#import").is_visible())
        verifier("la reprise est expliquée", "version précédente" in pg.inner_text("#imp-corps"))
        verifier("aucun assistant de saisie", pg.locator("#assistant").count() == 0)

        print("2. Import de trois relevés PDF")
        envoyer_pdf()
        pg.click("#imp-valider"); pg.wait_for_selector("#r-net"); pg.wait_for_timeout(600)
        verifier("ancien état conservé dans le nouveau", J("E.archiveV1 && E.archiveV1.programmees.length === 1"))
        verifier("plus d'opérations programmées ni d'objectifs", J("!('programmees' in E) && !('objectifs' in E)"))
        verifier("solde = clôture du dernier relevé", abs(J("soldeCompte('courant')") - CLOTURE_OCT) < 0.005, (J("soldeCompte('courant')"), CLOTURE_OCT))
        verifier("date de référence = fin du dernier relevé", J("DREF") == "2026-10-31", J("DREF"))
        verifier("l'en-tête la donne", "31 octobre 2026" in pg.inner_text("#d-releve"))

        print("3. Le relevé manquant")
        ecarts = J("E.operations.filter(o => o.ecart).map(o => [o.date, o.montant, o.categorie])")
        verifier("une seule ligne d'écart, au 4 octobre, de −64,20 €", ecarts == [["2026-10-04", -64.2, "ecart"]], ecarts)
        verifier("l'écart n'est ni crédit ni débit", abs(J("fluxMois('2026-10').sorties") - (700 + 232.6 + 300 + 1000)) < 0.005, J("fluxMois('2026-10').sorties"))

        print("4. Le virement vers le compte-titres")
        vir = J("E.operations.find(o => o.libelle === 'VIR BOURSORAMA TITRES')")
        verifier("reconnu comme virement entre comptes", vir["categorie"] == "virement" and vir.get("lienTitres"), vir)
        c = sqlite3.connect(DB); n = c.execute("SELECT COUNT(*) FROM operations WHERE type IN ('DEPOT','RETRAIT')").fetchone()[0]; c.close()
        verifier("rien n'est écrit côté compte-titres", n == 1, n)

        print("5. L'emprunt repéré, configuré, retrouvé")
        pg.wait_for_selector("#modale:not(.cache) [data-oui]")
        verifier("la modale propose ECH PRET AUTO", "ECH PRET AUTO 0471" in pg.inner_text("#modale"))
        pg.click("#modale [data-oui]"); pg.wait_for_selector("#d-form")
        for champ, val in [("nom", "Crédit auto"), ("capital", "9000"), ("taux", "4,9"), ("premiere", "2026-02"), ("echeance", "2029-09"), ("mensualite", "229")]:
            pg.fill(f"#d-form [data-d={champ}]", val)
        pg.select_option("#d-form [data-d=jour]", "10")
        pg.select_option("#d-form [data-d=assMode]", "euros"); pg.wait_for_timeout(150)
        pg.fill("#d-form [data-d=assValeur]", "3,6")
        pg.click("#d-form button[type=submit]"); pg.wait_for_timeout(600)
        d = J("E.dettes[0]")
        verifier("emprunt créé depuis le contrat", d and d["capitalDepart"] == 9000 and d["premiere"] == "2026-02", d)
        verifier("trois échéances retrouvées sur trois", J("suiviDette(E.dettes[0])") == {"attendues": 3, "vues": 3}, J("suiviDette(E.dettes[0])"))
        verifier("six échéances d'avant les relevés en historique", len(d.get("historique", [])) == 6, len(d.get("historique", [])))
        cap, attendu = J("etatDette(E.dettes[0]).capital"), J("tableauDette(E.dettes[0]).find(l => l.date === '2026-10-10').restant")
        verifier("capital restant = contrat au 10 octobre, au centime", abs(cap - attendu) < 0.005, (cap, attendu))
        verifier("la catégorie devient « Remboursements d'emprunt »", J("E.operations.filter(o => o.detteId).every(o => o.categorie === 'credit')"))
        onglet("dettes")
        verifier("le tableau dit 3 sur 3", "3 sur 3 échéances" in pg.inner_text("[data-vue=dettes]"))
        verifier("plus de « Rembourser plus vite » ni de coût restant", not any(t in pg.inner_text("[data-vue=dettes]") for t in ("Rembourser plus vite", "Coût restant")))
        verifier("taux d'endettement sur les revenus nets", "Revenus nets moyens" in pg.inner_text("[data-vue=dettes]"))

        print("6. Remboursement anticipé, depuis le relevé")
        onglet("comptes")
        verifier("graphiques dessinés hors ligne (Chart.js de vendor/)", J("window.Chart ? Object.keys(Chart.instances).length : 0") >= 2)
        idop = J("E.operations.find(o => o.libelle === 'VIR REMBT ANTICIPE').id")
        pg.click(f"[data-op='{idop}']"); pg.wait_for_selector("#m-rattacher")
        pg.click("#m-rattacher"); pg.wait_for_timeout(300)
        cap2 = J("etatDette(E.dettes[0]).capital")
        verifier("le capital baisse de 1 000 €", abs((cap - cap2) - 1000) < 0.005, (cap, cap2))

        print("7. Aucune saisie possible")
        verifier("pas de bouton d'ajout d'opération ni de virement", J("!document.querySelector('#btn-operation') && !document.querySelector('[data-ajout-ici], [data-virer-ici], [data-virer]')"))
        verifier("pas d'opération programmée affichée", "programm" not in pg.inner_text("#app").lower())
        verifier("pas de bouton Modifier sur les opérations", pg.locator("[data-vue=comptes] [data-modif]").count() == 0)
        idrecat = J("E.operations.find(o => o.libelle === 'CB CARREFOUR' && o.date === '2026-10-15').id")
        pg.select_option(f"[data-recat='{idrecat}']", "divers"); pg.wait_for_timeout(500)
        verifier("la catégorie, elle, se change", J(f"E.operations.find(o => o.id === '{idrecat}').categorie") == "divers")

        print("8. Réimporter les mêmes relevés ne double rien")
        n_ops, n_rel = J("E.operations.length"), J("E.releves.length")
        pg.click("#btn-import"); pg.wait_for_selector("#imp-fichiers", state="attached")
        envoyer_pdf(); pg.click("#imp-valider"); pg.wait_for_timeout(800)
        verifier("autant d'opérations", J("E.operations.length") == n_ops, (J("E.operations.length"), n_ops))
        verifier("autant de relevés", J("E.releves.length") == n_rel)
        verifier("toujours un seul écart", J("E.operations.filter(o => o.ecart).length") == 1)
        verifier("échéances toujours rattachées, sans doublon", J("E.operations.filter(o => o.detteId && !o.anticipe).length") == 3)
        if pg.locator("#modale:not(.cache)").count(): pg.click("#modale [data-fermer]")

        print("9. Un tableur sans solde : le solde de sa dernière date est demandé")
        pg.click(".menu-utilisateur summary"); pg.click("#btn-reinit"); pg.wait_for_selector("#imp-fichiers", state="attached")
        pg.set_input_files("#imp-fichiers", f"{BACK}/tests/tableurs/debit_credit.csv"); pg.wait_for_selector("#imp-valider")
        verifier("le solde est demandé au 20 octobre", "20 octobre 2026" in pg.inner_text("#imp-corps") and pg.locator("#imp-solde").count() == 1)
        pg.click("#imp-valider"); pg.wait_for_timeout(300)
        verifier("refusé sans solde", pg.locator("#imp-erreur:not(.cache)").count() == 1 and pg.locator("#app").is_hidden())
        pg.fill("#imp-solde", "1 000,00"); pg.click("#imp-valider"); pg.wait_for_selector("#r-net"); pg.wait_for_timeout(500)
        verifier("solde courant = solde indiqué", abs(J("soldeCompte('courant')") - 1000) < 0.005, J("soldeCompte('courant')"))
        verifier("ancre marquée comme indiquée", J("E.ancres.map(a => a.source)") == ["saisi"], J("E.ancres"))
        verifier("la carte le dit", "solde que vous avez indiqué" in pg.inner_text("[data-vue=synthese]"))
        b.close()
finally:
    srv.terminate()

print(f"\n{len(ok)} réussis, {len(ko)} échecs")
sys.exit(1 if ko else 0)
