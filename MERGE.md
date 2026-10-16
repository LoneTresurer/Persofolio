# Fusionner les deux moitiés du projet

Le projet est développé en deux moitiés parties de la même version :

- **Budget et comptes** — cette branche, `perso-finance`.
- **Compte-titres** — l'autre équipe, livraison attendue.

Ce dépôt contient la version commune de départ (commit **`base`**). C'est ce qui
rend la fusion simple : git compare les deux travaux à ce point commun et ne
signale que les endroits réellement touchés des deux côtés.

---

## 1. À envoyer à l'équipe compte-titres maintenant

Trois demandes, qui coûtent peu de leur côté et évitent l'essentiel des conflits :

1. **Tout le travail compte-titres dans `routes_portefeuille.py`, `core/` et
   `agents/`.** Ne rien ajouter dans `main.py` : il ne fait plus que monter les
   modules et servir les pages. Une route ajoutée là devra être déplacée.
2. **Ne pas toucher à `index.html`.** La page d'accueil et la connexion sont
   refaites de ce côté-ci ; leur version sera remplacée en bloc, donc tout
   travail fait dessus serait perdu.
3. **Livrer le dossier complet** (`ecofolio_perso/` avec `backend/` et
   `frontend/`), en `.zip`, sans `__pycache__` ni `.db`.

S'ils acceptent de passer à git, le mieux est qu'ils travaillent sur une branche
partie du commit `base` de ce dépôt : la fusion devient une opération normale et
cette page n'est plus qu'un pense-bête.

---

## 2. Intégrer la livraison

Depuis la racine du dépôt, travail en cours validé (`git status` propre) :

```bash
python outils/integrer_portefeuille.py ../livraison_portefeuille.zip
```

Le script replace leur livraison sur le commit `base`, dans une branche
`portefeuille`, et affiche ce qu'ils ont changé. Rien n'est encore fusionné :
c'est le moment de regarder leur diff.

Puis :

```bash
git merge portefeuille
```

ou, en une fois : `python outils/integrer_portefeuille.py ../livraison.zip --fusionner`.

---

## 3. Qui gagne, fichier par fichier

Ce tableau est la règle de résolution. Il vient d'un relevé exact des fichiers
touchés de ce côté depuis `base` (`git diff --stat base perso-finance`).

| Fichier | À qui il appartient | En cas de conflit |
|---|---|---|
| `frontend/banque.html` | nous seuls | impossible : ils ne l'ont pas |
| `backend/routes_banque.py`, `core/banque_db.py` | nous seuls | impossible |
| `frontend/index.html` | **nous** | garder notre version entière : `git checkout --ours ecofolio_perso/frontend/index.html` |
| `backend/main.py` | **nous** | garder notre version, puis y remonter à la main les routes qu'ils auraient ajoutées |
| `backend/routes_portefeuille.py` | **eux** | garder leur version, puis y remettre nos 7 lignes (`GET /auth/moi`, voir §4) |
| `frontend/portefeuille.html` | **eux** | garder leur version, puis y remettre nos 4 retouches (§4) |
| `core/portfolio_calc.py`, `core/portfolio_db.py`, `agents/*` | **eux** | garder leur version : nous n'y avons rien touché |
| `backend/archive_optimiseur/*` | nous | garder le dossier archivé, ignorer leur version active de ces fichiers |
| `requirements.txt`, `GUIDE_DEMARRAGE.md` | nous | garder notre version |

Le point important : `core/portfolio_db.py`, `core/portfolio_calc.py`,
`agents/market_data.py` et `agents/news_agent.py` sont **identiques à `base`**
de notre côté. Tout ce qu'ils y ont fait passe sans discussion.

---

## 4. Nos retouches dans leurs fichiers

Ce sont les seuls endroits où nous avons écrit dans des fichiers qui leur
appartiennent. Si git n'arrive pas à les replacer, les voici.

### `backend/routes_portefeuille.py` — une route

Juste avant `@router.post("/auth/deconnexion")` :

```python
@router.get("/auth/moi")
def moi(user: str = Depends(utilisateur_courant)):
    """Vérifie un jeton conservé par le navigateur (reprise de session au rechargement)."""
    return {"username": user}
```

Sans elle, `index.html` ne peut pas vérifier au chargement qu'une session encore
en mémoire est toujours valable.

### `frontend/portefeuille.html` — quatre retouches

1. L'écran de connexion de la page est masqué (la connexion est sur `index.html`) :
   ```html
   <section id="ecran-connexion" class="cache">
   ```
2. Deux liens dans l'en-tête, avant le bouton « Passer un ordre » :
   ```html
   <a class="mini-btn" href="index.html" style="text-decoration:none">Accueil</a>
   <a class="mini-btn" href="banque.html" style="text-decoration:none">Budget et comptes</a>
   ```
3. L'adresse de l'API suit celle de la page :
   ```js
   const API = /:8000$/.test(location.host) ? location.origin : 'http://localhost:8000';
   ```
4. Sans jeton, on renvoie vers la connexion unique — dans `deconnecter()` et à la
   toute fin du script :
   ```js
   location.replace('index.html');       // dans deconnecter(), à la place de l'affichage de #ecran-connexion
   if (jeton) demarrer(); else location.replace('index.html');
   ```

### À quoi ressemble un conflit réel

Essai fait avec une livraison fictive (nouvelle route, nouveau bouton dans
l'en-tête, nouvelle constante) : **un seul fichier en conflit**,
`portefeuille.html`, sur deux blocs de trois lignes, tous deux du type « garder
les deux côtés » :

```
<<<<<<< HEAD
      <a class="mini-btn" href="index.html">Accueil</a>
      <a class="mini-btn" href="banque.html">Budget et comptes</a>
=======
      <button class="mini-btn" id="btn-export">Exporter</button>
>>>>>>> portefeuille
```

On garde les trois lignes et on supprime les marqueurs. `main.py` et
`routes_portefeuille.py` s'étaient fusionnés seuls.

---

## 5. Après la fusion

```bash
python outils/verifier_fusion.py
```

43 contrôles statiques, sans dépendance ni serveur : les deux modules sont bien
montés, la connexion unique n'a pas été réécrasée par la connexion locale, les
pages suivent l'adresse du serveur, aucun fichier n'importe un module parti dans
`archive_optimiseur/`, aucun marqueur de conflit n'est resté. Le script affiche
le remède à côté de chaque échec.

Puis le test de bout en bout, serveur lancé (`python -m uvicorn main:app --reload`),
sur <http://localhost:8000> :

1. Créer un compte → on arrive sur l'accueil.
2. Ouvrir **Budget et comptes**, charger l'exemple, ajouter une dépense.
3. Revenir à l'accueil, ouvrir le **Compte-titres** : on y entre sans se
   reconnecter.
4. Recharger la page : toujours connecté, les données sont là.
5. Se déconnecter, puis ouvrir `banque.html` directement : renvoi vers la
   connexion.

Si les cinq passent, la fusion est bonne.

```bash
git commit                       # valider la fusion
git tag v1-fusion                # repère utile avant la suite
```

---

## 6. Ce qu'il restera à faire ensuite

- **Sortir l'authentification de `routes_portefeuille.py`** vers un
  `routes_auth.py` : `routes_banque.py` y importe aujourd'hui
  `utilisateur_courant`, ce qui lie les deux modules par un fichier qui
  appartient à l'autre équipe. **À faire après la fusion, pas avant** : déplacer
  ce code maintenant créerait exactement le conflit que l'on cherche à éviter.
- **Relier les deux modules** : le compte investissement du budget doit alimenter
  les espèces du compte-titres. Décision à prendre d'abord : quel côté détient ce
  solde. Le garder côté compte-titres évite de stocker le même montant deux fois.
- **Découper l'état du module budget en tables** (`routes_banque.py` stocke
  aujourd'hui un document JSON par utilisateur). C'est ce qui permettra à deux
  onglets ouverts de fusionner leurs modifications au lieu de se refuser l'un
  l'autre.
