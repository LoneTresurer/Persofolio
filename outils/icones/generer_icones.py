"""
Icône de PersoFolio : dessin, puis fichiers .ico (Windows) et .icns (macOS).

    python outils/icones/generer_icones.py

D'après la maquette du logo (anneau double, rosace guillochée, médaillon, « P »
italique), recolorée dans la palette de l'application — variante « forêt »,
choisie le 28 octobre. Le « P » est tiré de Liberation Serif Italic et converti
en tracé : l'icône ne dépend d'aucune police installée.

Trois dessins :
- grand, avec la marge de la grille Apple (Dock) — pour le .icns ;
- grand, plein cadre — pour Windows à partir de 48 px ;
- simplifié (anneau et « P », sans rosace) — 16 à 48 px, où la rosace ne serait
  plus qu'un gris.

Demande fontTools, Pillow et Playwright (rendu des SVG par Chromium). Écrit dans
persofolio/icones/, que persofolio.spec embarque.
"""
import math
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.boundsPen import BoundsPen

POLICE = "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"

def glyphe_p():
    f = TTFont(POLICE); gs = f.getGlyphSet(); nom = f.getBestCmap()[ord("P")]
    pen = SVGPathPen(gs); gs[nom].draw(pen)
    b = BoundsPen(gs); gs[nom].draw(b)
    return pen.getCommands(), b.bounds   # unités de police, axe y vers le haut

def svg(pal, S=1024, marge=True, simple=False):
    """marge=True : grille macOS (contenu 824/1024). simple : petites tailles."""
    m = 100 if marge else 0
    c = S - 2 * m                     # côté du carré
    rayon = c * 0.225
    cx = cy = S / 2
    u = c / 824                       # unité relative au carré
    o = []
    o.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S}" width="{S}" height="{S}">')
    o.append(f'<defs><linearGradient id="fond" x1="0" y1="0" x2="0" y2="1">'
             f'<stop offset="0" stop-color="{pal["fond1"]}"/><stop offset="1" stop-color="{pal["fond2"]}"/></linearGradient></defs>')
    o.append(f'<rect x="{m}" y="{m}" width="{c}" height="{c}" rx="{rayon}" fill="url(#fond)"/>')
    if not simple:
        o.append(f'<circle cx="{cx}" cy="{cy}" r="{352*u}" fill="none" stroke="{pal["trait"]}" stroke-width="{14*u}"/>')
        o.append(f'<circle cx="{cx}" cy="{cy}" r="{322*u}" fill="none" stroke="{pal["trait"]}" stroke-width="{5*u}" opacity=".9"/>')
        n = 18
        for k in range(n):
            a = 360 * k / n
            o.append(f'<ellipse cx="{cx + 78*u*math.cos(math.radians(a))}" cy="{cy + 78*u*math.sin(math.radians(a))}" '
                     f'rx="{150*u}" ry="{72*u}" transform="rotate({a} {cx + 78*u*math.cos(math.radians(a))} {cy + 78*u*math.sin(math.radians(a))})" '
                     f'fill="none" stroke="{pal["rosace"]}" stroke-width="{2.6*u}" opacity=".8"/>')
        r_med, ep = 112 * u, 12 * u
    else:
        o.append(f'<circle cx="{cx}" cy="{cy}" r="{330*u}" fill="none" stroke="{pal["trait"]}" stroke-width="{34*u}"/>')
        r_med, ep = 0, 0
    if r_med:
        o.append(f'<circle cx="{cx}" cy="{cy}" r="{r_med}" fill="{pal["medaillon"]}" stroke="{pal["trait"]}" stroke-width="{ep}"/>')
    d, (x0, y0, x1, y1) = glyphe_p()
    haut = (162 if not simple else 430) * u          # hauteur voulue du P
    k = haut / (y1 - y0)
    gx, gy = (x0 + x1) / 2, (y0 + y1) / 2
    o.append(f'<path d="{d}" fill="{pal["lettre"]}" transform="translate({cx} {cy}) scale({k} {-k}) translate({-gx} {-gy})"/>')
    o.append('</svg>')
    return "\n".join(o)

# Couleurs de banque.html : --foret #1E6A48, --foret-clair #E3EFE7
FORET = {"fond1": "#24784F", "fond2": "#174F35", "trait": "#E3EFE7", "rosace": "#BFDCCB",
         "medaillon": "#1A5C3E", "lettre": "#FFFFFF"}


def rendre(svg_texte: str, taille: int, chemin: str, page) -> None:
    page.set_viewport_size({"width": taille, "height": taille})
    page.set_content(f'<html><body style="margin:0;background:transparent">'
                     f'<img src="data:image/svg+xml;utf8,{quote(svg_texte)}" width="{taille}" height="{taille}">'
                     '</body></html>')
    page.wait_for_timeout(100)
    page.screenshot(path=chemin, omit_background=True)


if __name__ == "__main__":
    import os
    from urllib.parse import quote
    from PIL import Image
    from playwright.sync_api import sync_playwright

    racine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sortie = os.path.join(racine, "persofolio", "icones")
    os.makedirs(sortie, exist_ok=True)
    dessins = {
        "mac": svg(FORET, marge=True),
        "mac-simple": svg(FORET, marge=True, simple=True),
        "windows": svg(FORET, marge=False),
        "windows-simple": svg(FORET, marge=False, simple=True),
    }
    for nom, texte in dessins.items():
        with open(os.path.join(sortie, f"{nom}.svg"), "w", encoding="utf-8") as f:
            f.write(texte)

    png = {}
    with sync_playwright() as p:
        nav = p.chromium.launch()
        page = nav.new_page()
        for nom, tailles in {"mac": [64, 128, 256, 512, 1024], "mac-simple": [16, 32],
                             "windows": [64, 128, 256], "windows-simple": [16, 20, 24, 32, 48]}.items():
            for t in tailles:
                chemin = os.path.join(sortie, f"tmp-{nom}-{t}.png")
                rendre(dessins[nom], t, chemin, page)
                png[(nom, t)] = Image.open(chemin).convert("RGBA")
        nav.close()

    # Windows : une image par taille, la simplifiée en dessous de 48 px
    ico = [png[("windows-simple", t)] for t in (16, 20, 24, 32, 48)] + [png[("windows", t)] for t in (64, 128, 256)]
    ico[-1].save(os.path.join(sortie, "persofolio.ico"), format="ICO",
                 sizes=[im.size for im in ico], append_images=ico[:-1])
    # macOS : 16 et 32 simplifiées, le reste détaillé
    icns = [png[("mac-simple", 16)], png[("mac-simple", 32)]] + [png[("mac", t)] for t in (64, 128, 256, 512)]
    png[("mac", 1024)].save(os.path.join(sortie, "persofolio.icns"), format="ICNS", append_images=icns)
    png[("mac", 1024)].save(os.path.join(sortie, "persofolio-1024.png"))
    png[("windows", 256)].save(os.path.join(sortie, "persofolio-256.png"))
    for f in os.listdir(sortie):
        if f.startswith("tmp-"):
            os.remove(os.path.join(sortie, f))
    print("icônes écrites dans", sortie)
