"""Serveur d'essai : les vraies fonctions de core.passerelle et core.banque_db derrière
http.server, pour tester banque.html là où FastAPI ne s'installe pas. Aucune authentification.
Usage : serveur_essai.py <backend> <frontend> <port> <db>"""
import json, os, sys
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial

backend, frontend, port, db = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
os.environ["PERSOFOLIO_DONNEES"] = os.path.dirname(db)
sys.path.insert(0, backend)
from core import passerelle
from core.banque_db import BanqueDB, ConflitVersion as CV1
from core.portfolio_db import PortfolioDB
passerelle.DB_PATH = db
pdb, bdb = PortfolioDB(db), BanqueDB(db)
U = "pierre"


class H(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _corps(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/banque/etat":
            e, v = bdb.lire(U); return self._json(200, {"etat": e, "version": v})
        if self.path == "/banque/investissement":
            return self._json(200, passerelle.etat_investissement(U))
        if self.path.startswith("/compte/"):
            return self._json(404, {"detail": "absent de l'essai"})
        return super().do_GET()

    def do_PUT(self):
        c = self._corps()
        try: return self._json(200, {"version": bdb.ecrire(U, c["etat"], c["version"])})
        except CV1 as e: return self._json(409, {"detail": {"message": "version", "version": e.version_actuelle}})

    def do_POST(self):
        c = self._corps()
        try:
            if self.path == "/banque/virement":
                r = passerelle.virer(U, c["etat"], c["version"], c["id"], c["date"], c["montant_eur"],
                                     sens=c.get("sens", "DEPOT"), origine=c.get("origine", "budget"),
                                     remplace=c.get("remplace"))
            elif self.path == "/banque/virement/annuler":
                r = passerelle.annuler_virement(U, c["etat"], c["version"], c["id"])
            elif self.path == "/banque/apports":
                r = passerelle.declarer_apports(U, c["etat"], c["version"], c["origine"], c["achats"])
            elif self.path == "/auth/deconnexion":
                r = {}
            else:
                return self._json(404, {"detail": "inconnu"})
            return self._json(200, r)
        except passerelle.ExplicationPerimee as e:
            return self._json(409, {"detail": {"code": "deja_explique" if not e.restant else "explication_perimee",
                                               "message": "Cet argent a déjà été expliqué." if not e.restant else "périmé",
                                               "restant": e.restant}})
        except passerelle.ConflitVersion as e:
            return self._json(409, {"detail": {"message": "version", "version": e.version_actuelle}})


ThreadingHTTPServer(("127.0.0.1", port), partial(H, directory=frontend)).serve_forever()
