"""
Recette de stabilité du module Budget et comptes : des comptes aux cas limites,
chaque onglet, chaque réglage, chaque graphique survolé, en clair et en sombre,
sur ordinateur et sur téléphone. Le moindre message d'erreur dans la console
fait échouer l'essai.

    python outils/essai/essai_recette.py .        # depuis la racine du dépôt

Les comptes sont construits par integrer(), le chemin d'un vrai import, avec
des relevés fabriqués ici : aucune donnée réelle (le dépôt est public).

Cas couverts :
  vide          aucun relevé : seul l'écran d'import
  court         un seul relevé, moins d'un mois complet
  tableur       un tableur seul, solde indiqué, sans relevé PDF
  emprunts      un emprunt soldé, un emprunt dont la mensualité ne couvre pas
                les intérêts, un prélèvement d'emprunt non configuré
  long          cinq ans de relevés (≈ 3 000 opérations) : temps d'affichage
  noms          libellés et noms d'emprunt très longs
  sans-titres   le compte-titres ne répond pas (erreur 500)
  conflit       deux onglets ouverts : le second enregistrement est refusé (409)

Demande Playwright et Chromium.
"""
import os, subprocess, sys, tempfile, time
from datetime import datetime
from playwright.sync_api import sync_playwright

ICI = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ICI, "..", ".."))
BACK, FRONT = f"{REPO}/persofolio/backend", f"{REPO}/persofolio/frontend"
sys.path.insert(0, BACK)
TMP = tempfile.mkdtemp(); DB = f"{TMP}/portefeuille.db"; os.environ["PERSOFOLIO_DONNEES"] = TMP
from core.banque_db import BanqueDB
from core.portfolio_db import PortfolioDB
pdb, bdb = PortfolioDB(DB), BanqueDB(DB)
U = "pierre"; pdb.creer_utilisateur(U, "motdepasse-test"); pdb.creer_portefeuille(U, "Principal")
PORT = 8767
srv = subprocess.Popen([sys.executable, f"{ICI}/serveur_essai.py", BACK, FRONT, str(PORT), DB]); time.sleep(1.2)
ok, ko = [], []
def verifier(nom, cond, detail=""):
    (ok if cond else ko).append(nom); print(("  OK  " if cond else "  KO  ") + nom + (f"  [{detail}]" if detail and not cond else ""))

# Relevés mensuels fabriqués dans la page : n mois finissant au 31 octobre 2026.
# Chaque mois : salaire, loyer, courses, une échéance d'emprunt ; un relevé PDF par mois.
FABRIQUE = """
(({ n, debutJour = 1, libelleLong = false, sansPdf = false }) => {
  const ops = [], releves = []; let solde = 3000;
  const lib = l => libelleLong ? l + ' — PAIEMENT PAR CARTE BANCAIRE CHEZ UN COMMERCANT AU NOM PARTICULIEREMENT LONG 0042' : l;
  for (let k = n - 1; k >= 0; k--) {
    const m = decalerMois('2026-10', -k), [a, mm] = m.split('-').map(Number), fin = dateDansMois(m, joursDansMois(a, mm));
    const duMois = [[2, -42.5, 'CB BOULANGERIE', 'courses'], [5, -700, 'PRLV LOYER SCI', 'logement'], [10, -232.6, 'ECH PRET AUTO 0471', 'credit'],
      [15, -310.4, 'CB CARREFOUR', 'courses'], [18, -60, 'PRLV COFIDIS 88412', 'credit'], [27, 2400, 'VIR SALAIRE ATELIER', 'salaire']]
      .map(([j, v, l, c]) => ({ date: dateDansMois(m, j), montant: v, libelle: lib(l), categorie: c, tiers: l }));
    const debut = k === n - 1 ? dateDansMois(m, debutJour) : m + '-01';
    const lus = duMois.filter(o => o.date >= debut);
    const ouverture = Math.round(solde * 100) / 100;
    solde += lus.reduce((s, o) => s + o.montant, 0);
    ops.push(...lus);
    releves.push({ source: sansPdf ? 'tableur' : 'pdf', debut, fin, ouverture: sansPdf ? null : ouverture, cloture: sansPdf ? null : Math.round(solde * 100) / 100, fichier: 'r-' + m, banque: 'Essai', nb: lus.length });
  }
  const ancres = sansPdf ? [{ date: '2026-10-31', solde: 1234.56, source: 'saisi' }] : releves.map(r => ({ date: r.fin, solde: r.cloture, source: 'releve' }));
  E = null; integrer({ ops, releves, ancres }); sauver(); demarrer();
  return E.operations.length;
})
"""

try:
    with sync_playwright() as p:
        nav = p.chromium.launch()
        def page(largeur=1280, theme='clair'):
            pg = nav.new_page(viewport={'width': largeur, 'height': 900})
            pg.erreurs = []
            pg.on('pageerror', lambda e: pg.erreurs.append('JS : ' + str(e)))
            # Les messages « Failed to load resource » ne disent pas quelle adresse : on suit
            # les réponses elles-mêmes. /compte/* n'existe qu'avec FastAPI (le serveur d'essai
            # répond 404) ; l'application s'en passe, c'est voulu.
            pg.on('console', lambda m: m.type == 'error' and 'Failed to load resource' not in m.text and pg.erreurs.append('console : ' + m.text))
            pg.on('response', lambda r: r.status >= 400 and '/compte/' not in r.url and pg.erreurs.append(f'HTTP {r.status} : {r.url}'))
            pg.on('dialog', lambda d: d.accept())
            pg.clock.install(time=datetime.fromisoformat('2026-11-23T12:00:00'))
            pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1") else r.abort())
            pg.goto(f"http://127.0.0.1:{PORT}/banque.html")
            pg.evaluate(f"localStorage.setItem('persofolio_jeton','t');localStorage.setItem('persofolio_user','pierre');localStorage.setItem('persofolio_theme','{theme}')")
            return pg
        def ouvrir(pg):
            pg.goto(f"http://127.0.0.1:{PORT}/banque.html"); pg.wait_for_selector('#app:not(.cache), #import:not(.cache)'); pg.wait_for_timeout(300)
        def remettre_a_zero():
            bdb.supprimer(U)
        def parcourir(pg, nom):
            """Chaque onglet, chaque réglage ; chaque graphique survolé puis quitté."""
            t0 = time.time()
            for onglet in ('synthese', 'comptes', 'budget', 'dettes', 'simulateur'):
                pg.click(f'nav.onglets [data-onglet={onglet}]'); pg.wait_for_timeout(150)
                if onglet == 'comptes':
                    for v in ('3', '12', '0'): pg.click(f'[data-pc="{v}"]'); pg.wait_for_timeout(80)
                if onglet == 'budget':
                    pg.click('[data-bvue=mensuel]')
                    if pg.locator('#m-prec:not([disabled])').count(): pg.click('#m-prec'); pg.wait_for_timeout(80)
                    pg.click('[data-bvue=historique]')
                    for sens in ('tout', 'credit', 'debit'):
                        pg.click(f'[data-hs={sens}]'); pg.wait_for_timeout(80)
                        for per in ('3', '0'): pg.click(f'[data-hp="{per}"]'); pg.wait_for_timeout(80)
                        survoler_tout(pg)
                    continue
                survoler_tout(pg)
            # Bascule de thème pendant l'affichage : tous les graphiques se redessinent
            pg.click('#btn-theme'); pg.wait_for_timeout(200); pg.click('#btn-theme'); pg.wait_for_timeout(150)
            return time.time() - t0
        def survoler_tout(pg):
            for cid in pg.evaluate("[...document.querySelectorAll('[data-vue]:not(.cache) canvas')].map(c => c.id)"):
                el = pg.locator(f'#{cid}')
                if not el.is_visible(): continue
                el.scroll_into_view_if_needed(); bb = el.bounding_box()
                if not bb: continue
                for fx in (0.15, 0.5, 0.85): pg.mouse.move(bb['x'] + bb['width'] * fx, bb['y'] + bb['height'] * 0.6); pg.wait_for_timeout(40)
                pg.mouse.move(2, 2); pg.wait_for_timeout(80)
                reste = pg.evaluate(f"(() => {{ const c = document.getElementById('{cid}').closest('.carte, .panneau'); const l = c && c.querySelector('.lecture'); return l ? l.innerText.trim() : ''; }})()")
                if reste: pg.erreurs.append(f'{cid} : la lecture reste affichée hors survol')
        def bilan(pg, nom, extra=''):
            verifier(f"{nom} : aucune erreur{extra}", not pg.erreurs, '; '.join(pg.erreurs[:3]))

        print("vide")
        remettre_a_zero(); pg = page(); ouvrir(pg)
        verifier("vide : l'écran d'import, rien d'autre", pg.locator('#import').is_visible() and pg.locator('#app').is_hidden())
        bilan(pg, 'vide'); pg.close()

        for largeur, theme in ((1280, 'clair'), (390, 'sombre')):
            print(f"court ({largeur} px, {theme})")
            remettre_a_zero(); pg = page(largeur, theme); ouvrir(pg)
            pg.evaluate(FABRIQUE + "({ n: 1, debutJour: 4 })"); pg.wait_for_timeout(300)
            verifier("court : aucun mois complet, épargne « — »", pg.evaluate("moisComplets().length") == 0 and '—' in pg.inner_text('[data-vue=synthese]'))
            parcourir(pg, 'court')
            pg.click('nav.onglets [data-onglet=budget]'); pg.click('[data-bvue=historique]'); pg.wait_for_timeout(150)
            verifier("court : le budget historique le dit", 'au moins un mois complet' in pg.inner_text('[data-vue=budget]'))
            larg = pg.evaluate("document.documentElement.scrollWidth")
            verifier(f"court : rien ne déborde ({largeur} px)", larg <= largeur, larg)
            bilan(pg, 'court', f' ({largeur} px, {theme})'); pg.close()

        print("tableur")
        remettre_a_zero(); pg = page(); ouvrir(pg)
        pg.evaluate(FABRIQUE + "({ n: 4, sansPdf: true })"); pg.wait_for_timeout(300)
        verifier("tableur : solde = solde indiqué", abs(pg.evaluate("soldeCompte('courant')") - 1234.56) < 0.005)
        verifier("tableur : pas d'écart inventé sans PDF", pg.evaluate("E.operations.filter(o => o.ecart).length") == 0)
        parcourir(pg, 'tableur'); bilan(pg, 'tableur'); pg.close()

        print("emprunts")
        remettre_a_zero(); pg = page(); ouvrir(pg)
        pg.evaluate(FABRIQUE + "({ n: 8 })"); pg.wait_for_timeout(300)
        pg.evaluate("""(() => {
          ajouterDette({ nom: 'Crédit auto', type: 'auto', capital: 9000, taux: 4.9, mensualite: 229, jour: 10, echeance: '2029-09', assurance: { mode: 'euros', valeur: 3.6 }, premiere: '2026-02', depuisOrigine: true,
            historique: tableauContrat({ capital: 9000, taux: 4.9, premiere: '2026-02', jour: 10, mensualite: 229, ass: 3.6 }).lignes.filter(l => l.date < E.creeLe).map(l => ({ date: l.date, credit: l.credit, assurance: l.assurance })) });
          ajouterDette({ nom: 'Prêt soldé', type: 'autre', capital: 1000, taux: 2, mensualite: 100, jour: 1, echeance: '2026-01', premiere: '2025-02', depuisOrigine: true,
            historique: tableauContrat({ capital: 1000, taux: 2, premiere: '2025-02', jour: 1, mensualite: 100 }).lignes.map(l => ({ date: l.date, credit: l.credit, assurance: 0 })) });
          ajouterDette({ nom: 'Revolving à perte', type: 'renouvelable', capital: 5000, taux: 22, mensualite: 50, jour: 20, echeance: '2040-01', premiere: '2026-12', capitalLe: AUJ });
          rapprocherDettes(); sauver(); rafraichir();
        })()""")
        pg.click('nav.onglets [data-onglet=dettes]'); pg.wait_for_timeout(300)
        txt = pg.inner_text('[data-vue=dettes]')
        verifier("emprunts : l'emprunt soldé est marqué « Soldé »", 'Soldé' in txt)
        verifier("emprunts : la mensualité trop faible est signalée", 'ne se rembourse jamais' in txt)
        verifier("emprunts : le prélèvement non configuré est signalé", 'ressemble à un emprunt' in txt)
        verifier("emprunts : les échéances du crédit auto sont retrouvées", pg.evaluate("JSON.stringify(suiviDette(E.dettes[0]))") == '{"attendues":8,"vues":8}', pg.evaluate("JSON.stringify(suiviDette(E.dettes[0]))"))
        pg.click('#b-configurer'); pg.wait_for_timeout(200); pg.keyboard.press('Escape'); pg.wait_for_timeout(150)
        parcourir(pg, 'emprunts'); bilan(pg, 'emprunts'); pg.close()

        print("long (cinq ans)")
        remettre_a_zero(); pg = page(); ouvrir(pg)
        n = pg.evaluate(FABRIQUE + "({ n: 60 })"); pg.wait_for_timeout(300)
        # Densifie : 40 dépenses de plus par mois, pour approcher un vrai relevé
        pg.evaluate("""(() => { const ajout = [];
          for (let k = 0; k < 60; k++) { const m = decalerMois('2026-10', -k); for (let i = 0; i < 40; i++) ajout.push({ id: uid(), compte: 'courant', importe: true, date: dateDansMois(m, 1 + (i % 28)), montant: -(5 + (i * 7) % 60), libelle: 'CB COMMERCE ' + (i % 25), categorie: ['courses', 'sorties', 'shopping', 'transport', 'loisirs'][i % 5], tiers: 'CB COMMERCE ' + (i % 25) }); }
          E.operations.push(...ajout); recalerSolde(); sauver(); rafraichir(); })()""")
        nops = pg.evaluate("E.operations.length")
        t = {}
        for onglet in ('synthese', 'comptes', 'budget', 'dettes'):
            t0 = time.time(); pg.click(f'nav.onglets [data-onglet={onglet}]'); pg.wait_for_timeout(20); t[onglet] = time.time() - t0
        pg.click('nav.onglets [data-onglet=budget]'); pg.click('[data-bvue=historique]')
        t0 = time.time(); pg.click('[data-hp="0"]'); pg.click('[data-hs=debit]'); t['historique, tout, débit'] = time.time() - t0
        lent = max(t.values())
        verifier(f"long : {nops} opérations, chaque onglet s'affiche en moins d'une seconde", lent < 1.0, {k: round(v, 2) for k, v in t.items()})
        parcourir(pg, 'long'); bilan(pg, 'long'); pg.close()

        print("noms longs")
        remettre_a_zero(); pg = page(390); ouvrir(pg)
        pg.evaluate(FABRIQUE + "({ n: 3, libelleLong: true })"); pg.wait_for_timeout(300)
        pg.evaluate("ajouterDette({ nom: 'Prêt immobilier de la résidence principale à taux fixe sur vingt-cinq ans', type: 'immo', capital: 180000, taux: 3.4, mensualite: 890, jour: 5, echeance: '2049-01', premiere: '2024-02', depuisOrigine: true }); sauver(); rafraichir();")
        for onglet in ('comptes', 'dettes'):
            pg.click(f'nav.onglets [data-onglet={onglet}]'); pg.wait_for_timeout(200)
            larg = pg.evaluate("document.documentElement.scrollWidth")
            verifier(f"noms longs : {onglet} ne déborde pas sur téléphone", larg <= 390, larg)
        bilan(pg, 'noms longs'); pg.close()

        print("compte-titres injoignable")
        remettre_a_zero(); pg = page()
        pg.route("**/banque/investissement", lambda r: r.fulfill(status=500, content_type="application/json", body='{"detail":"panne"}'))
        pg.erreurs_attendues = True
        ouvrir(pg); pg.evaluate(FABRIQUE + "({ n: 3 })"); pg.wait_for_timeout(300)
        verifier("sans compte-titres : la Synthèse s'affiche", pg.locator('#r-net').is_visible() and 'pas encore ouvert' in pg.inner_text('[data-vue=synthese]'))
        pg.erreurs = [e for e in pg.erreurs if 'HTTP 500' not in e]     # la panne simulée elle-même est attendue
        parcourir(pg, 'sans compte-titres'); bilan(pg, 'sans compte-titres'); pg.close()

        print("conflit entre deux onglets")
        remettre_a_zero(); a = page(); ouvrir(a); a.evaluate(FABRIQUE + "({ n: 3 })"); a.wait_for_timeout(800)
        b = page(); ouvrir(b); b.wait_for_selector('#r-net')
        a.evaluate("E.regles['X'] = 'divers'; sauver();"); a.wait_for_timeout(900)
        b.erreurs = []
        b.evaluate("E.regles['Y'] = 'divers'; sauver();"); b.wait_for_timeout(1500)
        b.erreurs = [e for e in b.erreurs if 'HTTP 409' not in e]       # le refus lui-même est attendu
        verifier("conflit : le second onglet recharge l'état au lieu d'écraser", b.evaluate("'X' in E.regles"), b.inner_text('#sauvegarde'))
        verifier("conflit : l'application reste utilisable", b.locator('#r-net').is_visible())
        bilan(b, 'conflit'); a.close(); b.close()
        nav.close()
finally:
    srv.terminate()

print(f"\n{len(ok)} réussis, {len(ko)} échecs")
sys.exit(1 if ko else 0)
