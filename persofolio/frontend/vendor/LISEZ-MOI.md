# Bibliothèques embarquées

Copiées telles quelles, sans modification, pour que l'application fonctionne
hors ligne et ne dépende d'aucun serveur extérieur pour s'afficher. Avant, elles
étaient chargées depuis un CDN à chaque ouverture de page : hors ligne, les
graphiques restaient vides et les camemberts affichaient « Aucune donnée », comme
pour un portefeuille vide.

| Fichier | Source | Version | Licence |
|---|---|---|---|
| `chart.umd.min.js` | cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js | Chart.js 4.4.1 | MIT |
| `chartjs-chart-geo.umd.min.js` | cdn.jsdelivr.net/npm/chartjs-chart-geo@4.3.1/build/index.umd.min.js | chartjs-chart-geo 4.3.1 | MIT |
| `countries-110m.json` | cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json | world-atlas 2.0.2 (Natural Earth) | ISC |

Empreintes SHA-256, pour vérifier qu'un fichier n'a pas été remplacé :

```
81ffafe13c37e1b25793b020d446f4d9739b949dadb7f9f79d709a0cad781c2f  chart.umd.min.js
459f4ce3cacc60e456b370b2c5a4d10d4a1e785c47552d2053311e01e59a576b  chartjs-chart-geo.umd.min.js
2516c915867c7baf18ddec727aec46c315541a07cfb3d79a6559b05d5e94eee8  countries-110m.json
```

Pour changer de version : remplacer le fichier, mettre ce tableau à jour, et
relancer `python outils/essai/essai_passerelle.py .` — il vérifie que les
graphiques se dessinent.
