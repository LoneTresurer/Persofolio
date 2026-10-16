"""
Test rapide du backend EcoFolio v8.
À lancer pendant que `uvicorn main:app --reload --port 8000` tourne.

Utilisation :
    python diagnostic.py
"""
import sys

try:
    import httpx
except ImportError:
    print("❌ Le module 'httpx' n'est pas installé.")
    print("   Lancez : pip install httpx")
    sys.exit(1)

BASE = "http://localhost:8000"

print("─" * 70)
print(" Diagnostic backend EcoFolio v8")
print("─" * 70)

# 1. Le backend répond-il ?
try:
    r = httpx.get(f"{BASE}/health", timeout=5.0)
    if r.status_code == 200:
        info = r.json()
        print(f"✓ Backend joignable. Version : {info.get('version', '?')}")
        if info.get('version', '').startswith('7.'):
            print()
            print("⚠️  ATTENTION : vous tournez encore la v7 du backend !")
            print("   Arrêtez uvicorn (Ctrl+C) et relancez-le depuis le dossier")
            print("   backend/ du zip v8 que vous venez de télécharger.")
            sys.exit(1)
    else:
        print(f"❌ Backend joignable mais /health renvoie {r.status_code}")
        sys.exit(1)
except httpx.ConnectError:
    print(f"❌ Backend introuvable sur {BASE}")
    print("   Lancez d'abord le backend :")
    print("     cd backend")
    print("     uvicorn main:app --reload --port 8000")
    sys.exit(1)
except Exception as e:
    print(f"❌ Erreur de connexion : {e}")
    sys.exit(1)

# 2. /prices/{ticker} existe-t-il ?
print()
print("Test 1/2 — endpoint /prices/AAPL …")
try:
    r = httpx.get(f"{BASE}/prices/AAPL?period=1y&interval=1d", timeout=30.0)
    if r.status_code == 404 and "Not Found" in r.text:
        print("❌ Le endpoint /prices/{ticker} N'EXISTE PAS dans le backend qui tourne.")
        print("   → Vous n'avez pas relancé uvicorn avec les nouveaux fichiers.")
        print("   Faites Ctrl+C et relancez depuis le bon dossier backend/.")
        sys.exit(1)
    elif r.status_code == 502:
        print(f"⚠️  Endpoint présent mais Yahoo Finance indisponible : {r.json().get('detail')}")
        print("   Vérifiez votre accès Internet vers query1.finance.yahoo.com")
    elif r.status_code == 200:
        d = r.json()
        keys = set(d.keys())
        expected = {"prices", "dates", "perf_1y", "info", "n"}
        if not expected.issubset(keys):
            missing = expected - keys
            print(f"❌ Champs manquants dans la réponse : {missing}")
            print(f"   Champs reçus : {sorted(keys)}")
            sys.exit(1)
        print(f"✓ /prices/AAPL OK — {d['n']} prix, perf_1y={d['perf_1y']:.2f}%, devise={d['info']['currency']}")
    else:
        print(f"❌ Réponse inattendue : {r.status_code} {r.text[:200]}")
except Exception as e:
    print(f"❌ Erreur : {e}")
    sys.exit(1)

# 3. /portfolio/performance existe-t-il ?
print()
print("Test 2/2 — endpoint /portfolio/performance …")
try:
    body = {"tickers": ["AAPL", "MSFT", "GOOGL"], "poids": [0.4, 0.4, 0.2], "period": "1y"}
    r = httpx.post(f"{BASE}/portfolio/performance", json=body, timeout=60.0)
    if r.status_code == 404:
        print("❌ Le endpoint /portfolio/performance N'EXISTE PAS dans le backend qui tourne.")
        print("   Mêmes instructions que ci-dessus : relancez uvicorn.")
        sys.exit(1)
    elif r.status_code == 502:
        print(f"⚠️  Endpoint présent mais Yahoo Finance indisponible : {r.json().get('detail')}")
    elif r.status_code == 200:
        d = r.json()
        print(f"✓ /portfolio/performance OK — {d['n_tickers']} actions, perf={d['perf_total_pct']:.2f}%, sharpe={d['sharpe']:.2f}")
    else:
        print(f"❌ Réponse inattendue : {r.status_code} {r.text[:200]}")
except Exception as e:
    print(f"❌ Erreur : {e}")
    sys.exit(1)

print()
print("─" * 70)
print("✓ Tout fonctionne. Si le frontend affiche encore « Données indisponibles »,")
print("  videz le cache du navigateur (Ctrl+Shift+R) et rechargez la page.")
print("─" * 70)
