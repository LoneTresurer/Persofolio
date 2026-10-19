"""
outils/verifier_bat.py
======================
Controles statiques sur un fichier .bat.

Ecrit apres coup : la premiere version de construire.bat avait ete enregistree
avec des fins de ligne Unix et des caracteres accentues. cmd.exe lit mal un
.bat ainsi enregistre -- il echoue sur les blocs entre parentheses et sur les
boucles for -- et la fenetre se referme avant d'avoir execute la moindre ligne.
Rien ne s'affiche, et il n'y a donc rien a lire pour comprendre.

Ces controles attrapent ce cas, et quelques autres pieges de cmd qui se
manifestent de la meme facon silencieuse.

    python outils/verifier_bat.py construire.bat
"""
import sys, re

chemin = sys.argv[1]
brut = open(chemin, 'rb').read()
ok = pb = 0
def check(nom, cond, detail=''):
    global ok, pb
    if cond: ok += 1; print(f'  ✓ {nom}')
    else: pb += 1; print(f'  ✗ {nom}  {detail}')

check('ASCII pur', all(b < 128 for b in brut),
      f'octets > 127 : {sorted({b for b in brut if b>127})[:8]}')
check('fins de ligne CRLF partout',
      brut.count(b'\n') == brut.count(b'\r\n') and brut.count(b'\n') > 0,
      f"{brut.count(b'\\n')} LF, {brut.count(b'\\r\\n')} CRLF")
check('pas de BOM UTF-8', not brut.startswith(b'\xef\xbb\xbf'))

texte = brut.decode('ascii', 'replace')
lignes = texte.split('\r\n')

# Parentheses equilibrees, en ignorant celles echappees et celles des rem/echo
prof = 0
souci = None
for i, l in enumerate(lignes, 1):
    nu = l.strip()
    if nu.lower().startswith(('rem ', '::')) or nu.lower() == 'rem':
        continue
    if nu.lower().startswith('echo') and prof == 0:
        continue          # hors bloc, une parenthese affichee ne gene personne
    sans_echap = re.sub(r'\^.', '', l)
    sans_chaine = re.sub(r'"[^"]*"', '""', sans_echap)
    prof += sans_chaine.count('(') - sans_chaine.count(')')
    if prof < 0 and souci is None:
        souci = f'parenthese fermante en trop, ligne {i}'
check('parentheses equilibrees', prof == 0 and souci is None, souci or f'profondeur finale {prof}')

# %VAR% lu dans le bloc ou il vient d'etre ecrit : piege classique
dedans, pose, fautes = False, set(), []
for i, l in enumerate(lignes, 1):
    nu = l.strip()
    if nu.lower().startswith('rem'): continue
    if not dedans and re.search(r'\($', re.sub(r'"[^"]*"', '""', nu)):
        dedans, pose = True, set(); continue
    if dedans and nu == ')':
        dedans = False; continue
    if dedans:
        m = re.match(r'set\s+"?(\w+)=', nu, re.I)
        if m: pose.add(m.group(1).upper())
        for v in re.findall(r'%(\w+)%', nu):
            if v.upper() in pose: fautes.append(f'ligne {i} : %{v}%')
check('aucune variable lue dans le bloc qui la definit', not fautes, '; '.join(fautes))

# Chaque sortie d'erreur doit etre atteignable et le script doit toujours faire une pause
check('le script se termine toujours par une pause', 'pause' in texte.lower())
check('exit /b present apres la pause finale', re.search(r'pause\s*>nul\s*\r\n\s*exit /b', texte, re.I) is not None)

# Chemins avec espaces : les appels a python doivent etre entre guillemets
nus = [i for i, l in enumerate(lignes, 1)
       if re.search(r'(?<!")%PYV%', l)
       and not l.strip().lower().startswith(('rem', 'echo'))]
check('%PYV% toujours entre guillemets', not nus, f'lignes {nus}')

# Labels references mais absents
labels = {l.strip()[1:].lower() for l in lignes if l.strip().startswith(':') and not l.strip().startswith('::')}
manquants = [g for g in re.findall(r'goto\s+:?(\w+)', texte, re.I) if g.lower() not in labels]
manquants += [c for c in re.findall(r'call\s+:(\w+)', texte, re.I) if c.lower() not in labels]
check('tous les labels vises existent', not manquants, f'manquants : {manquants}')

print(f'\n{ok} reussis, {pb} problemes')
sys.exit(1 if pb else 0)
