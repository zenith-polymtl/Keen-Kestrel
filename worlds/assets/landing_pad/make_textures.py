#!/usr/bin/env python3
"""Textures simples pour les objets de la Tâche 2 (CC0, générées).
- landing_pad_blue.png : tapis Ø 32 po face bleue, H blanc dans un cercle (motif courant STARTRC/PGYTECH).
  Le bleu (#0B5E9E) reprend le fond de la Figure 2 du CONOPS (8, 94, 157), qui ressemble à un tapis.
- landing_pad_orange.png : l'autre face.
Usage : nice -n 19 python3 make_textures.py
"""
from PIL import Image, ImageDraw, ImageFont
import os
HERE = os.path.dirname(os.path.abspath(__file__))
def pad(fill, name, px=1024):
    im = Image.new('RGBA', (px, px), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    c = px / 2
    d.ellipse([0, 0, px - 1, px - 1], fill=fill)
    w = int(px * 0.012)
    r1 = px * 0.47; d.ellipse([c - r1, c - r1, c + r1, c + r1], outline='white', width=w)   # bord
    r2 = px * 0.30; d.ellipse([c - r2, c - r2, c + r2, c + r2], outline='white', width=int(w * 2))
    f = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', int(px * 0.36))
    d.text((c, c), 'H', font=f, fill='white', anchor='mm')
    im.save(os.path.join(HERE, name))
pad((11, 94, 158, 255), 'landing_pad_blue.png')
pad((255, 106, 19, 255), 'landing_pad_orange.png')
print('ok')
