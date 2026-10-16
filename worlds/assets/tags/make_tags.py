#!/usr/bin/env python3
"""Génère des textures d'étiquettes d'oreille AEAC 2027 (Figure 2 du CONOPS).

Proportions mesurées sur la Figure 2 (exprimées en hauteur de capitale h du code) :
  largeur papier 2,17 h ; hauteur papier 3,79 h ; trou Ø 0,22 h centré à 0,38 h du haut ;
  centre vertical du code à 54 % de la hauteur ; code ≈ 75 % de la largeur.
Police : Times New Roman Regular sur la Figure ; on utilise TeX Gyre Termes (clone libre,
licence GUST/LPPL) qui a le même « 3 » à sommet arrondi. Liberation Serif en repli.
Usage : nice -n 19 python3 make_tags.py [--px 512] [--codes 3V K7 ...] [--n 12 --seed 1]
"""
import argparse, os, random, string, math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = [os.path.join(HERE, '..', 'fonts', 'texgyretermes-regular.otf'),
         os.path.join(HERE, '..', 'fonts', 'liberation-fonts-ttf-2.1.5', 'LiberationSerif-Regular.ttf'),
         '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf']
W_H, H_H, HOLE_D_H, HOLE_Y_H, TEXT_CY, TEXT_W = 2.17, 3.79, 0.22, 0.38, 0.54, 0.75
PAPER = (242, 241, 238, 255)   # papier blanc vu sous plastification (albedo)
INK = (30, 30, 32, 255)        # noir laser
LAM = (235, 240, 242, 70)      # bord de plastique transparent, légèrement bleuté
# Jeu de caractères : 0-9 A-Z. Option --safe retire O, I, Q (ambiguïtés avec 0, 1).
def font_path():
    for f in FONTS:
        if os.path.exists(f):
            return f
    raise SystemExit('aucune police serif trouvée')

def render(code, px=512, margin_mm=1.5, cap_mm=19.9, back=False):
    fp = font_path()
    h = px / W_H                          # hauteur de capitale en px
    W, H = px, int(round(px * H_H / W_H))
    lam = int(round(margin_mm / cap_mm * h))  # bord plastique en px
    im = Image.new('RGBA', (W + 2 * lam, H + 2 * lam), LAM)
    d = ImageDraw.Draw(im)
    d.rectangle([lam, lam, lam + W - 1, lam + H - 1], fill=PAPER)
    # taille de police telle que la capitale fasse h px
    f = ImageFont.truetype(fp, 400); bb = f.getbbox('H'); ch = bb[3] - bb[1]
    size = 400 * h / ch
    f = ImageFont.truetype(fp, int(round(size)))
    # largeur cible 75 % : on réduit seulement si le code déborde (ex. « WM »)
    tw = f.getlength(code)
    if tw > 0.9 * W:
        f = ImageFont.truetype(fp, int(round(size * 0.9 * W / tw)))
    bb = f.getbbox(code, anchor='ls')
    cx = lam + W / 2; cy = lam + TEXT_CY * H
    x = cx - (bb[0] + bb[2]) / 2; y = cy - (bb[1] + bb[3]) / 2
    d.text((x, y), code, font=f, fill=INK, anchor='ls')
    # trou : transparent (alpha 0), traverse papier et plastique
    r = HOLE_D_H * h / 2; hx = lam + W / 2; hy = lam + HOLE_Y_H * h
    d.ellipse([hx - r, hy - r, hx + r, hy + r], fill=(0, 0, 0, 0))
    im = im.filter(ImageFilter.GaussianBlur(0.4))  # léger flou d'impression laser
    return im

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--px', type=int, default=512)
    ap.add_argument('--codes', nargs='*', default=['3V', 'K7'])
    ap.add_argument('--n', type=int, default=10, help='codes aléatoires uniques en plus')
    ap.add_argument('--seed', type=int, default=2027)
    ap.add_argument('--safe', action='store_true')
    ap.add_argument('--out', default=HERE)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    chars = string.digits + string.ascii_uppercase
    if a.safe:
        chars = ''.join(c for c in chars if c not in 'OIQ')
    codes = list(dict.fromkeys(a.codes))
    while len(codes) < len(a.codes) + a.n:
        c = rng.choice(chars) + rng.choice(chars)
        if c not in codes:
            codes.append(c)
    os.makedirs(a.out, exist_ok=True)
    tiles = []
    for c in codes:
        im = render(c, a.px)
        im.save(os.path.join(a.out, f'tag_{c}_recto.png'))
        # verso : même impression (lisible depuis l'autre face) ; dans le maillage,
        # la face arrière a ses propres UV non miroir, donc même image.
        im.save(os.path.join(a.out, f'tag_{c}_verso.png'))
        tiles.append(im)
    # planche de contrôle sur fond bleu (comme la Figure 2)
    tw, th = tiles[0].size; cols = 6; rows = math.ceil(len(tiles) / cols)
    s = 0.5
    sheet = Image.new('RGB', (int(cols * (tw * s + 20)) + 20, int(rows * (th * s + 20)) + 20), (8, 94, 157))
    for i, t in enumerate(tiles):
        t2 = t.resize((int(tw * s), int(th * s)))
        sheet.paste(t2, (20 + int((i % cols) * (tw * s + 20)), 20 + int((i // cols) * (th * s + 20))), t2)
    sheet.save(os.path.join(a.out, 'planche_tags.png'))
    with open(os.path.join(a.out, 'codes.txt'), 'w') as fh:
        fh.write('\n'.join(codes) + '\n')
    print(len(codes), 'étiquettes,', tw, 'x', th, 'px, police', os.path.basename(font_path()))

if __name__ == '__main__':
    main()
