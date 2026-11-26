# Reprendre PersoFolio dans une nouvelle conversation

Ce fichier existe pour qu'une session qui ne sait rien du projet puisse s'y
mettre sans qu'on ait à lui raconter trois semaines de travail. Il dit où en est
l'application, ce qui a déjà été décidé, et ce qui fait tomber ceux qui
l'ignorent.

**À lire en entier avant de toucher au code.**

> **23 novembre 2026 : refonte du module Budget et comptes.** Il ne fait plus
> que lire des relevés bancaires : plus d'assistant, d'opérations saisies ou
> programmées, d'objectifs, de virements vers le compte-titres. Le §13 décrit
> ce qui est en place et fait foi. Les §4, §11 et §12 décrivent en partie un
> fonctionnement retiré ; ils restent pour l'histoire du code serveur, qui, lui,
> n'a pas changé.

Dépôt public : <https://github.com/LoneTresurer/persofolio>. Branche par défaut
`perso-finance` ; `portefeuille` et `base` servent à la méthode de fusion (§5).
Les commits portent l'adresse « noreply » de GitHub, pas une adresse personnelle.
Une session Claude peut pousser des branches, mais **pas d'étiquettes (tags)** :
le proxy les refuse. Elles se créent depuis l'interface GitHub.

---

## 1. Ce qu'est l'application

Deux modules, un seul compte, un seul serveur.

- **Budget et comptes** (`frontend/banque.html`, `backend/routes_banque.py`) —
  lecture des relevés bancaires (PDF, Excel, CSV) : compte courant, crédits et
  débits, moyennes, emprunts. Écrit par Pierre.
- **Compte-titres** (`frontend/portefeuille.html`,
  `backend/routes_portefeuille.py`, `core/portfolio_*.py`, `agents/`) — ordres,
  positions, performance, risque. Écrit par le reste de l'équipe, livré par
  paquets successifs.
- **Accueil et connexion** (`frontend/index.html`) — un compte unique ouvre les
  deux.

C'est une application de **visualisation**. Elle observe des finances, elle ne
les commande pas. Elle n'interdit rien, elle explique et laisse décider. Cette
phrase a tranché plusieurs arbitrages ; elle en tranchera d'autres.

---

## 2. Ce qu'il faut savoir avant d'écrire une ligne

**Budget et comptes ne contient que ce que disent les relevés.** Aucune
opération ne se saisit, ne se modifie ni ne se programme. Seuls se décrivent à
la main les paramètres d'un emprunt (absents des relevés) et, pour un tableur,
le solde à sa dernière date. Changer la catégorie d'une opération reste permis :
c'est du classement, pas une donnée. Toute nouvelle fonction se juge à cette
règle (§13).

**Le code de l'équipe compte-titres ne se modifie pas** sans raison de fusion.
Leurs livraisons arrivent régulièrement et sont rejouées par-dessus (§5). Toute
retouche dans leurs fichiers est une dette à repayer à chaque livraison. Quand
c'est inévitable, un bloc unique et clairement commenté se refusionne en une
fois ; dix lignes éparpillées, non.

**L'optimiseur ESG est archivé**, dans `backend/archive_optimiseur/`. Leurs
livraisons le ramènent à chaque fois ; il retourne à l'archive à chaque fois.
Décision de Pierre, prise en septembre, toujours valable.

**Rien ne s'invente sur les chiffres.** Toute valeur affichée vient d'une
mesure, jamais d'un plan. Depuis la refonte, il n'y a plus de plan du tout :
seulement ce que les relevés montrent, et l'échéancier contractuel des emprunts
pour l'avenir.

**Le ton.** Conseil, jamais leçon. Quand l'utilisateur fait un choix discutable,
l'application le signale une fois, discrètement, et n'y revient pas.

**Les textes sont courts** (26 novembre). Sous-titre d'une carte : une phrase,
une dizaine de mots, qui dit ce que la carte montre. Infobulle de formule : le
calcul et ses chiffres, une précision seulement si elle est indispensable.
Alerte : ce qui se passe et quoi faire, sans le pourquoi. Pas de discours de
vente : l'utilisateur est déjà dans l'application. `portefeuille.html` n'est pas
concerné (code de l'équipe).

---

## 3. Les pièges qui ont déjà coûté du temps

Chacun a été découvert en production ou en test, pas par raisonnement.

| Piège | Ce qui se passe |
|---|---|
| **`.bat` en fins de ligne Unix** | cmd.exe échoue sur les blocs `( )` et les boucles `for`. La fenêtre se ferme avant d'exécuter une ligne, sans rien afficher. `outils/verifier_bat.py` l'attrape. |
| **Accents dans un `.bat`** | Même effet. Le fichier doit rester en ASCII pur. |
| **Chart.js venait d'un CDN** | Hors ligne, `donut()` affichait « Aucune donnée » — le même message que pour un portefeuille vide. **Corrigé le 28 octobre** : Chart.js, le module de carte et le fond de carte sont dans `frontend/vendor/`. La retouche dans `portefeuille.html` est documentée en MERGE.md §4 ; `verifier_fusion.py` échoue si une livraison remet un lien CDN. Seules les polices Google viennent encore d'internet (cosmétique). |
| **Pas de FastAPI dans une session Claude** | Pas d'accès à PyPI ni aux CDN depuis la session. Localement, `outils/essai/serveur_essai.py` fait tourner la vraie passerelle sans FastAPI. **Le vrai serveur tourne dans GitHub Actions** (§9) : c'est là qu'on le vérifie. |
| **Les journaux GitHub Actions ne sont pas lisibles depuis la session** | Ni les journaux ni les fichiers déposés (stockage Azure bloqué). Les **annotations** le sont : `lancer_essai_bureau.py` y recopie temps, contrôles en échec et fin du journal. `gh api repos/LoneTresurer/persofolio/check-runs/<job>/annotations`. |
| **cryptography compilé sur place (Mac Intel)** | Compilé contre l'OpenSSL du système, il heurtait celui que PyInstaller embarque depuis Python : `Symbol not found: _SSL_get0_group_name`, serveur mort. `PIP_ONLY_BINARY=cryptography` dans le workflow. |
| **Le port fait partie de l'origine** | Session et préférences vivent dans le localStorage de la fenêtre, rangé par origine, port compris. Passer de 8000 à 8001 d'un lancement à l'autre = déconnexion. Le lanceur reprend 8000 même en fin de fermeture (SO_REUSEADDR hors Windows). |
| **PyInstaller ne suit que les `import`** | Les fichiers de données — les pages web — doivent être déclarés dans le `.spec`, sinon l'exécutable démarre et sert une page blanche. |
| **Un versement daté d'aujourd'hui ne couvre pas un achat passé** | Le rejeu du compte-titres trie par date puis par ordre de saisie. Un versement enregistré aujourd'hui pour « expliquer » un achat de 2025 passe *après* lui : l'apport implicite reste, et le versement s'ajoute aux espèces. C'était le bouton « d'où venait cet argent ? » : +4 085 € de patrimoine à chaque clic, et la question restait posée. Ces versements sont maintenant datés de la veille de l'achat, côté serveur. |
| **Supprimer un virement côté budget seulement** | La ligne du compte courant disparaissait, le versement restait dans le compte-titres : le patrimoine montait d'autant. Même chose en *modifiant* un virement. Tout passe maintenant par `/banque/virement/annuler` ou `remplace`. |
| **Fichiers marqués « téléchargé depuis internet » (Windows)** | Extraits d'un zip téléchargé, ils portent la marque Zone.Identifier ; le .NET Framework refuse alors pythonnet : « Failed to resolve Python.Runtime.Loader.Initialize », pas de fenêtre. Le lanceur retire la marque de ses bibliothèques .NET ; le workflow pose la marque sur tout avant l'essai pour le vérifier. |
| **Un vrai relevé dans le dépôt** | Le dépôt est public. Le .gitignore exclut *.pdf, *.csv, *.xls, *.xlsx ; seule exception, `tests/tableurs/`, qui ne contient que des relevés inventés par `outils/essai/generer_tableurs.py`. Le relevé reçu le 17 novembre (compte carte de l'enseignant) n'est **jamais** versé. |
| **xlrd absent des sessions Claude** | Le test `.xls` est sauté localement (pas de PyPI) et tourne dans GitHub Actions. LibreOffice (`soffice`) est installé : il convertit un .xls en .xlsx ou .csv pour l'examiner. |
| **Grille et téléphone** | `grid-template-columns: 1fr` laisse une colonne s'élargir à la taille d'un tableau : la page déborde au lieu de faire défiler le tableau. `minmax(0, 1fr)`. De même, une infobulle invisible (opacity 0) occupe de la place : elle est en `display: none` hors survol. |
| **Une échéance passée dans `E.operations`** | `soldeCompte` additionne *toutes* les opérations, sans filtre de date. Une échéance d'avant le début du suivi (`E.creeLe`) y serait retranchée une seconde fois : le solde de départ la contient déjà. Elle va dans `dette.historique`, que seul `etatDette` rejoue (`evenementsDette`). |
| **La colonne `origine` du compte-titres n'est pas à nous** | Le compte-titres y note *comment* une opération a été saisie (`manuel`, `auto`, `import_ordres`, `import_positions`). La passerelle n'y écrit `budget`/`externe` que sur les versements qu'elle crée elle-même. La réponse à « d'où vient ce versement ? » pour un versement saisi là-bas va dans `banque_origines`, jamais dans cette colonne. |
| **Arrondir un montant qu'un autre calcul doit couvrir** | Un versement déclaré au centime laissait chaque achat couvert à un demi-centime près : « 0,01 € à expliquer » sans aucune ligne, question impossible à faire disparaître. Le montant écrit est exact ; ce qui s'affiche est la somme des lignes listées (19 novembre). |
| **Deux écritures concurrentes** | Tout ce qui envoie l'état du budget porte un numéro de version. Deux envois simultanés et le second est refusé (409). Les virements s'enchaînent pour cette raison. |

---

## 4. La jonction entre les deux modules

C'est la partie la plus récente et la plus délicate. `core/passerelle.py` la
documente en détail ; l'essentiel :

- Le **compte investissement du budget n'existe plus en propre**. Ce qu'il
  affiche, ce sont les espèces et les titres du compte-titres.
- Les espèces et le prix de revient se déduisent des **seules opérations
  enregistrées** : exacts, et connus **sans réseau**. Seule la valeur de marché
  demande des cours, et hors ligne c'est le prix de revient qui s'affiche, dit
  comme tel.
- Un virement écrit **deux lignes dans une seule transaction SQLite** —
  `banque_etats` et `operations` sont deux tables du même fichier. Il ne peut
  pas passer à moitié. Son identifiant le rend rejouable sans doublon.
- L'argent entré dans le portefeuille **sans passer par le budget** est une
  question posée à l'utilisateur, pas une anomalie : « de mon compte courant »
  (compte dans le taux d'épargne investie) ou « d'ailleurs » (n'y compte pas).
- **Nommer l'origine d'un achat passé ne change jamais le patrimoine.**
  L'argent est déjà compté dans les titres. `declarer_apports()` recalcule
  sous verrou les achats à découvert, refuse d'écrire si l'écran n'était plus
  à jour (un second clic ne trouve plus rien), et vérifie avant de valider que
  plus rien n'est à découvert. « De mon compte courant » ne débite le courant
  que pour un achat postérieur au début du suivi (`E.creeLe`) : avant, le
  solde de départ en tient déjà compte. Si le relevé importé porte déjà la
  sortie, c'est elle qui devient le virement (`reclasse` garde sa catégorie
  pour une éventuelle annulation).
- **Tout se répare depuis l'écran Comptes** : la liste « Espèces échangées
  avec le compte-titres » annule n'importe quel versement écrit par le budget,
  des deux côtés, et signale les doublons du même jour. « Corriger le solde »
  inscrit l'écart avec le solde réel comme une ligne visible, catégorie
  `correction` — ni revenu ni dépense (`estFlux` exclut tout ce qui est
  `INTERNE`).
- La **part de l'épargne investie** = virements partis du courant ÷ épargne
  libre, sur les mois civils complets. Jamais la valeur du portefeuille : elle
  monte avec le marché et afficherait un taux supérieur à 100 % sans qu'un euro
  de plus ait été épargné.

---

## 5. Intégrer une nouvelle livraison de l'équipe

**Depuis le 10 novembre, l'équipe part de la version publiée sur GitHub** : la
méthode par la branche `portefeuille` ci-dessous ne s'applique plus telle
quelle. Voir MERGE.md §6 quater — comparer l'archive au dépôt, ne reprendre que
les fichiers modifiés sur une branche `integration-<date>`, tout vérifier (y
compris la construction GitHub) avant d'avancer `perso-finance`.

La méthode est dans `MERGE.md`, §2 et §6 ter. En deux lignes :

```bash
git checkout portefeuille      # leur branche, partie du commit base
# remplacer backend/ et frontend/ par le contenu de leur livraison
git commit -m "Livraison de l'équipe compte-titres (<date>)"
git checkout perso-finance
git merge portefeuille
```

Poser leur livraison **sur leur propre branche** est ce qui rend la fusion
triviale : git ne compare que ce qu'ils ont changé depuis la fois précédente. La
dernière fusion n'a demandé qu'un seul fichier résolu à la main.

---

## 6. Vérifier avant de livrer

```bash
python outils/verifier_fusion.py     # 53 contrôles statiques
python outils/dependances.py         # requirements.txt contre les imports réels
python outils/verifier_bat.py construire.bat
python outils/essai/essai_releves.py .      # 36 contrôles, vrai navigateur hors ligne (§13)
python outils/essai/essai_exemple.py .      # patrimoine de l'exemple à date fixe
python outils/essai/essai_recette.py .      # 28 contrôles : cas limites, survol, téléphone, sombre (§14)
cd persofolio/backend && python -m py_compile main.py routes_*.py core/*.py agents/*.py
python -m unittest tests.test_passerelle tests.test_calc_frais   # depuis persofolio/backend
```

`outils/essai/` remplace le faux serveur Node : `serveur_essai.py` sert
`banque.html` et appelle **les vraies fonctions** de `core/passerelle.py` et
`core/banque_db.py`, sans FastAPI. Seule la couche FastAPI (pydantic,
authentification) reste non testée ici.

Et le contrôle qui vaut tous les autres : **le patrimoine du profil d'exemple,
à date fixe** — `python outils/essai/essai_exemple.py .` fige l'horloge du
navigateur et attend **3 403,95 €** au 23 novembre 2026, au 5 décembre 2026 et
au 15 janvier 2027. Depuis la refonte, l'exemple est fait de six mois de relevés
fictifs relatifs au mois en cours : son patrimoine ne dépend plus de la date, et
les trois dates vérifient que le passage d'un mois ou d'une année ne change
rien. Si ce chiffre bouge sans raison explicite, quelque chose s'est perdu en
route. (Avant la refonte : 1 150,86 € au 28 octobre ; ne s'applique plus.)

---

## 7. Lancer l'application

```bash
cd persofolio/backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000
```

Puis <http://localhost:8000>. Python 3.10 ou plus récent.

Si `No module named uvicorn` : les dépendances ne sont pas installées dans le
Python qui lance la commande. Avec un environnement virtuel, c'est
`.venv\Scripts\python.exe -m pip install -r requirements.txt` qu'il faut, pas
`pip` tout court.

---

## 8. Ce qui reste ouvert

- **Une installation neuve déjà connectée** (17 novembre, point laissé pour
  plus tard) : rien dans le code ni dans l'exécutable n'emporte de données ; le
  profil venait du dossier de données du poste. `journal.txt` le confirmera.

- **Le vrai serveur tourne**, dans l'application construite (GitHub Actions,
  Windows et Mac Intel). Depuis la refonte, l'essai de bureau charge le profil
  d'exemple par l'écran d'import (`#imp-exemple`).
- **Routes de la passerelle sans appelant** depuis la refonte :
  `/banque/virement`, `/virement/annuler`, `/apports`, `/versements`,
  `/versements/annuler`. Gardées, testées par `tests.test_passerelle` ; à
  retirer si la refonte est confirmée à l'usage (§13).
- **Mac Apple Silicon** : construit et validé le 29 octobre (fenêtre 0,9 s,
  serveur 4,8 s au premier lancement, 2,6 s au second). Le 28, GitHub n'avait
  aucune machine libre de ce type (5 tâches abandonnées, « capacity
  constraints ») : en cas de nouvel abandon, relancer plus tard, ce n'est pas
  un défaut du projet. La version Intel passe aussi tous les contrôles.
- **Installateur Windows, .dmg, publication (Releases), guides d'installation** :
  étapes (b) et (c), pas encore faites. Icône : en attente du logo.
- **Les versements et retraits d'espèces** ont été retirés de la saisie du
  compte-titres par l'équipe (« un suivi d'investissements, pas un compte
  bancaire »). Le moteur les accepte toujours, ce qui laisse passer les
  virements du budget. À retrancher avec eux s'ils vont plus loin.
- **`core/passerelle.py` écrit dans `operations`**, table qui appartient au
  compte-titres. C'est le seul endroit. `_verifier_schema()` échoue bruyamment
  si leur structure change.

---

## 9. L'application de bureau

`persofolio/backend/lanceur.py` ouvre une **fenêtre native** (pywebview :
WebView2 sous Windows, WKWebView sous macOS) sur un écran de chargement, démarre
le serveur, puis affiche l'application. Fermer la fenêtre arrête tout. Repli sur
le navigateur si WebView2 manque.

- **Construction** : GitHub Actions, `.github/workflows/construire.yml`, bouton
  « Run workflow » (une session Claude peut aussi le lancer :
  `gh workflow run construire.yml --ref perso-finance -R LoneTresurer/persofolio`,
  `-f intel=true` pour le Mac Intel). Windows : dossier `PersoFolio/` ;
  macOS : `PersoFolio.app` (arm64 par défaut, `PERSOFOLIO_ARCH=x86_64`).
- **Essai de l'application construite** : `PERSOFOLIO_ESSAI=<rapport.json>` fait
  tourner `essai_bureau.py` *dans* l'application — session locale, profil
  d'exemple (patrimoine affiché), Chart.js, puis au second lancement la session doit
  avoir survécu. Temps mesurés (28 octobre, machines GitHub) : fenêtre ~1 s,
  serveur prêt 2,5 à 6,5 s selon la machine et le lancement.
- **Dépendances** : `requirements-bureau.txt` (pywebview, PyInstaller) s'ajoute
  à `requirements.txt` ; le serveur seul n'en a pas besoin.
- **Version** : un seul fichier, `persofolio/VERSION`.
- **Données** : `%LOCALAPPDATA%\PersoFolio` (Windows),
  `~/Library/Application Support/PersoFolio` (macOS). Le stockage de la
  fenêtre (localStorage) est dans le sous-dossier `fenetre/`.
- **Éditeur** affiché : « IAE Caen - Master G2C 2026 ».

## 10. Relevés en tableur (17 novembre)

`core/tableur.py` lit .xls, .xlsx et .csv ; `core/import_releves.py` porte le
corps de POST /banque/releves (PDF et tableurs mêlés), appelable sans FastAPI.
Pas de soldes dans un tableur : pas de contrôle d'équilibre, le compte est
marqué `tableur` et le solde du jour saisi fait foi. Les lignes « DEBIT MENSUEL
CARTE » d'un compte carte sont écartées (ce n'est pas un revenu). Les
catégories de la banque passent avant le classement par commerçant, jamais
avant une correction de l'utilisateur.

## 11. Crédits, moyennes, dépenser ou investir (18 novembre)

**Crédits.** La saisie demande « Vous connaissez : le capital restant dû
aujourd'hui | le capital total emprunté » (`b.mode` : `restant` | `contrat`).
- *Restant* : capital du jour et **prochaine échéance** (le prélèvement ne
  démarre plus le lendemain). Rien n'est rejoué.
- *Contrat* : capital emprunté et première échéance, même lointaine.
  `tableauContrat()` déroule le contrat ; chaque échéance passée est supposée
  payée. Avant `E.creeLe` → `dette.historique` (hors compte courant, voir §3).
  Depuis → rattachée à l'opération du relevé (montant à 5 %, date à 5 jours,
  pas un virement), sinon ajoutée (`rapprocherEcheances`, `creerCredit`).
  L'assistant passe « demain » comme début de suivi : tout le passé va dans
  l'historique.
- **Différé** (`dette.premiere`, `dette.differe = {type, mois}`) : partiel =
  intérêts seuls ; total = rien (sauf assurance), intérêts capitalisés aux dates
  d'échéance (`evenementsDette`). Suivi par le postage, l'échéancier,
  `projDette`. `dette.mensualite` = mensualité *après* le différé.
- Les centimes laissés par une mensualité arrondie vont sur la dernière
  échéance (`dernierReste`, seuil 5 % d'une mensualité), dans toutes les
  projections et le postage.
- Si le prélèvement réel diffère du contrat, le rejeu suit le réel : le capital
  restant bouge de l'écart. Voulu (rien ne s'invente).
- Le profil d'exemple crée ses crédits par `ajouterDette` comme avant : son
  patrimoine daté (1 150,86 / 200,85 / −898,08 €) ne doit pas bouger.

**Budget et comptes.** « Six derniers mois » est dans Comptes (compte courant),
sous l'évolution du solde : entrées, sorties, solde en barres, mois en cours
pâle. Dans Budget, « Moyennes mensuelles » (3 mois, 6 mois, 12 mois, 5 ans) sur
les mois *complets* (`premierMoisComplet`) ; taux d'épargne = total épargné ÷
total des entrées. Sur 3 mois, le solde moyen égale la capacité constatée de la
Synthèse. Dépenses historiques : « Dépenser ou investir » (versements nets vers
le compte-titres). Onglet « Simulateur d'investissement », sans « Le coût
d'attendre ».

## 12. v57 de l'équipe, origine des versements, doublons (19 novembre)

**v57 fusionnée sans la coupure** de la passerelle annoncée dans leur
`A_LIRE.txt` : malentendu, confirmé par l'équipe (MERGE.md §6 quinquies).

**« D'où venait cet argent ? »** porte maintenant sur deux choses, dans le
même bloc de la Synthèse (`blocAExpliquer`, affiché même avant le premier mois
complet) :
- les achats à découvert, comme avant (une réponse pour tous) ;
- les versements et retraits **saisis ou importés dans le compte-titres**
  (`versements_a_expliquer`), une réponse par ligne et « pour tous ». « Compte
  courant » : virement côté budget (`versement-<id>`), ligne du relevé
  rattachée si elle existe (débit 0 à 10 jours avant le versement, crédit 0 à
  10 jours après un retrait), sinon ajoutée ; rien avant `E.creeLe`. Le
  compte-titres n'est pas touché. Réponses dans `banque_origines` ;
  « Annuler la réponse » dans Comptes ; `lien_budget` protège un versement
  rattaché au courant.

**Doublons** : à l'import d'un avis, un mouvement déjà écrit par un virement du
budget (même sens, même montant, ±5 jours) est décoché (retouche 7). Dans
l'autre sens, le formulaire de virement propose « Le rattacher à ce virement »
quand le versement est déjà dans le compte-titres sans origine.

**Boutons** : tout le bloc reste désactivé jusqu'à ce que les chiffres soient
relus, quoi qu'il arrive. `essai_passerelle.py` : 55 contrôles (triple clic,
annulation, pour tous, centimes, rattachement depuis le formulaire).

## 13. Refonte « tout vient des relevés » (23 novembre)

Décidée par Pierre, maquettée puis validée avant d'être codée. Tout est dans
`frontend/banque.html` (environ 2 850 lignes au lieu de 4 650) ; **le serveur
n'a pas changé**, hormis un message du lecteur de tableurs.

**Le modèle** (`E.format = 'releves-1'`) : `operations` (toutes importées),
`releves` (dates, soldes d'ouverture et de clôture de chaque relevé),
`ancres` (soldes connus : clôture d'un PDF, ou solde indiqué à la fin d'un
tableur), `dettes`, `regles`, `nonEmprunts`. Plus de `programmees`,
`objectifs`, `budgets`. Un état d'avant la refonte ouvre l'écran d'import ; il
est gardé tel quel dans `archiveV1` au premier import, jamais relu.

**Dates.** `DREF` est la fin du dernier relevé : soldes, capital restant dû et
« sur 30 jours » valent à cette date, et l'en-tête la donne. Le compte-titres
reste au cours du jour.

**Solde.** `recalerSolde()` part de l'ancre la plus récente. Un tableur n'a pas
de solde : celui de sa dernière date est demandé à l'import, quand il prolonge
l'historique au-delà du dernier solde connu.

**Relevé manquant.** `regenererEcarts()` compare la clôture d'un relevé PDF à
l'ouverture du suivant ; une différence devient une ligne « Relevé manquant »,
catégorie `ecart` (interne : ni crédit ni débit). Recalculée à chaque import.

**Virements vers le compte-titres.** `lierVirementsTitres()` : une sortie du
courant du même montant qu'un versement du compte-titres (`INV.mouvements`), à
5 jours près, devient « Virement entre comptes ». Rien n'est écrit côté
compte-titres. La question « D'où venait cet argent ? » a disparu du budget.

**Emprunts.** Décrits par leur contrat (ou par le capital restant à la date de
saisie, `capitalLe`). `rapprocherDettes()` cherche chaque échéance attendue dans
les relevés (5 % sur le montant, 5 jours sur la date) ; **le capital ne baisse
que des échéances retrouvées**. Avant le premier relevé, les échéances sont
supposées payées (`historique`). Le tableau Dettes dit « n sur m échéances ».
Un prélèvement classé « Remboursements d'emprunt » sans emprunt configuré
ouvre, après l'import, la configuration de l'emprunt (`modaleEmprunts`), et un
bandeau le rappelle dans Dettes. Un débit peut être rattaché comme
remboursement anticipé (menu ⋯ dans Comptes). Taux d'endettement : mensualités
÷ revenus nets moyens des 3 derniers mois complets (crédit hors virements
internes et hors remboursements reçus).

**Écrans.** Synthèse (patrimoine net, deux cartes, répartition, épargne
constatée) ; Comptes (période 3 mois → Tout pour les deux graphiques,
opérations en lecture seule, catégorie modifiable) ; Budget mensuel (crédit,
débit, flux, débits face à la moyenne des 3 mois complets précédents) ; Budget
historique (période × Tout / Crédit / Débit) ; Dettes (sans « Rembourser plus
vite » ni coût restant) ; Simulateur (pré-rempli par l'épargne constatée).
Vocabulaire : crédit / débit pour les sens, « emprunt » pour les dettes.

**Profil d'exemple.** Six mois de relevés fictifs passés par `integrer()`,
comme un vrai import ; le quatrième commence le 4 (écart visible), trois
emprunts retrouvés dans les relevés, solde de départ 12 000 €. Il n'écrit plus
dans le compte-titres.

**Essais.** `essai_releves.py` remplace `essai_passerelle.py`, dont les
parcours n'existent plus ; la passerelle côté serveur reste couverte par
`tests.test_passerelle`. Le dépôt étant public, aucun PDF réel : la réponse PDF
de `/banque/releves` y est fabriquée au format du serveur ; le tableur passe par
le vrai lecteur. `serveur_essai.py` sait désormais répondre à DELETE
`/banque/etat` (Réinitialiser).


## 14. Lire un graphique, recette de stabilité (26 novembre)

**Survol des graphiques.** Deux présentations, branchées dans `graphique()` :
- *Lecture* (`config.lecture`, `brancherLecture`) pour les graphiques à
  plusieurs séries par mois : Débit, crédit et solde historique ; Solde,
  Débits ou Crédits mois par mois ; Composition de chaque mois ; Capital
  restant dû. **Rien ne s'affiche sans survol.** Au survol : le mois et son
  total à droite du titre de la carte, la valeur de chaque série dans la
  légende, les autres mois estompés ; sur une barre empilée, le segment
  survolé et sa part. Un clic sur la légende masque une série.
- *Bulle* (`brancherBulle`, par défaut) pour les autres : une petite carte au
  style de l'application, construite à partir des infobulles du graphique.

Piège rencontré : Chart.js traite les mouvements de souris à l'image suivante.
Un mouvement capté juste avant la sortie arrivait après elle et réaffichait le
mois. `onHover` ne retient un mois que si le canevas est encore `:hover`.

**Recette** (`essai_recette.py`, aussi dans GitHub Actions) : compte vide, un
seul relevé incomplet (ordinateur clair et téléphone sombre), tableur seul,
emprunts soldé / qui ne se rembourse jamais / non configuré, cinq ans et
2 760 opérations (chaque onglet sous la seconde), noms très longs sur
téléphone, compte-titres en panne (500), conflit entre deux onglets (409).
Chaque onglet, chaque réglage, chaque graphique survolé puis quitté ; toute
erreur JavaScript ou réponse HTTP en erreur fait échouer. `/compte/*` n'existe
qu'avec FastAPI : son 404 dans le serveur d'essai est attendu.
