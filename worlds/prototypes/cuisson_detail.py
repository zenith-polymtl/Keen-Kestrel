#!/usr/bin/env python3
"""
PROTOTYPE NON TESTÉ (écrit le 2026-10-16 sans numpy/PIL sur le poste, et sans droit d'exécuter
quoi que ce soit de lourd). À relire et tester dans l'image geotools de Keen-Kestrel
(docker/geotools.Dockerfile a numpy, Pillow, scipy).

Cuisson « ortho basse fréquence + détail d'herbe haute fréquence » dans les tuiles de texture
de la zone de recherche.

Principe (multiplication luminance-préservante, dans l'espace linéaire) :

    R(x) = O↑(x) · [1 + k · (H(x) − 1)]          H = L_d / flou(L_d)   (moyenne locale 1)

  - O↑   : ortho rééchantillonnée (bicubique) à la résolution cible s (m/px) ; elle porte la couleur
           et toutes les fréquences qu'elle a vraiment (> ~2 px d'ortho, soit ~0,4-0,5 m pour Esri z19).
  - L_d  : luminance de la texture de détail (herbe), répétée avec une période P (m), ramenée à s
           par moyenne de surface (pas d'aliasing).
  - flou : gaussienne de sigma ≈ résolution de l'ortho (en px cibles) : on ne garde du détail que
           les fréquences que l'ortho n'a PAS (bande [2 s, ~2 r_ortho]). La moyenne locale de H vaut 1
           → la luminance moyenne de l'ortho est conservée à toute échelle > r_ortho.
  - k    : gain calé par extrapolation spectrale (spectre naturel en 1/f² : variance égale par
           octave) : std(log L) du détail dans la bande ajoutée = std(log L) de l'ortho dans les
           mêmes nombres d'octaves juste au-dessus de sa coupure. Borné à [0,3 ; 1,5].
  - Deux matériaux (paille / repousse verte) mélangés selon un masque issu de l'ortho (indice
    d'excès de vert ExG = 2g − r − b, lissé), pour que le grain vert tombe là où l'ortho est verte.
  - Anti-répétition : la texture de détail est tirée par cellules de P × P m avec rotation k·90°
    et décalage aléatoires (graine = coordonnées de la tuile → reproductible), fondues sur 15 %.
  - Enfin, correction d'histogramme : moyenne et écart type par canal (en Lab approximé par
    linéaire RVB) recalés sur ceux de l'ortho de la tuile, pour ne pas dériver la teinte
    calibrée (#817A60 ≈ réflectance MODIS mi-mai).

Tailles recommandées (voir rapport.md §2) : tuile 256 m → 4096 px (6,25 cm/px) ou sous-tuiles de
128 m → 4096 px (3,1 cm/px) sur la seule zone de recherche ; 1024 px (25 cm/px) ailleurs.

Licence : la sortie dérive de l'ortho Esri → USAGE INTERNE, à écrire dans build/ (ignoré par git).
Les textures de détail (Poly Haven, ambientCG) sont CC0.
"""
import argparse
import math
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, zoom

Image.MAX_IMAGE_PIXELS = None


# ---------------------------------------------------------------- couleur
def srgb_to_lin(a):
    a = a.astype(np.float32) / 255.0
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(a):
    a = np.clip(a, 0.0, 1.0)
    s = np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(a, 1 / 2.4) - 0.055)
    return (s * 255.0 + 0.5).astype(np.uint8)


def luminance(lin):
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


# ---------------------------------------------------------------- détail
def load_detail(path, period_m, s_m):
    """Charge la texture de détail et la ramène à s_m par px (moyenne de surface)."""
    img = Image.open(path).convert("RGB")
    px = max(8, int(round(period_m / s_m)))      # px cibles par période
    img = img.resize((px, px), Image.BOX)        # BOX = moyenne de surface, pas d'aliasing
    return srgb_to_lin(np.asarray(img))


def stochastic_tiling(det, out_px, rng, blend=0.15):
    """Pave out_px × out_px avec det (période p px) en cellules tournées/décalées au hasard,
    fondues sur `blend` de la cellule. Retourne la luminance pavée (float32)."""
    ld = luminance(det)
    p = ld.shape[0]
    n = math.ceil(out_px / p) + 1
    out = np.zeros((out_px + p, out_px + p), np.float32)
    w = np.zeros_like(out)
    b = max(1, int(blend * p))
    ramp = np.ones(p + b, np.float32)
    ramp[:b] = np.linspace(0, 1, b, endpoint=False)
    ramp[-b:] = np.linspace(1, 0, b, endpoint=False)
    win = np.outer(ramp, ramp)
    big = np.tile(ld, (2, 2))
    for i in range(n):
        for j in range(n):
            ox, oy = rng.integers(0, p, 2)
            cell = np.rot90(big[oy:oy + p + b, ox:ox + p + b], rng.integers(0, 4))
            y0, x0 = i * p, j * p
            ys, xs = slice(y0, min(y0 + p + b, out.shape[0])), slice(x0, min(x0 + p + b, out.shape[1]))
            hh, ww = ys.stop - ys.start, xs.stop - xs.start
            out[ys, xs] += cell[:hh, :ww] * win[:hh, :ww]
            w[ys, xs] += win[:hh, :ww]
    return (out / np.maximum(w, 1e-6))[:out_px, :out_px]


def band_log_std(L, lo_px, hi_px):
    """Écart type de log L dans la bande de périodes [lo_px, hi_px] (différence de gaussiennes)."""
    lg = np.log(np.maximum(L, 1e-4))
    return float(np.std(gaussian_filter(lg, lo_px / 2.0) - gaussian_filter(lg, hi_px / 2.0)))


# ---------------------------------------------------------------- cuisson d'une tuile
def bake_tile(ortho_rgb_u8, tile_m, out_px, ortho_res_m, straw_path, green_path,
              period_m=2.5, seed=0, k=None):
    s = tile_m / out_px                                  # m/px cible
    O = srgb_to_lin(ortho_rgb_u8)
    f = out_px / O.shape[0]
    O_up = np.clip(zoom(O, (f, f, 1), order=3), 0, 1)    # bicubique

    rng = np.random.default_rng(seed)
    sig = ortho_res_m / s                                # sigma du flou passe-bas (px cibles)
    H = []
    for p in (straw_path, green_path):
        Ld = stochastic_tiling(load_detail(p, period_m, s), out_px, rng)
        H.append(Ld / np.maximum(gaussian_filter(Ld, sig), 1e-4))

    # masque vert : ExG de l'ortho, lissé sur ~2 m, ramené à [0, 1]
    exg = 2 * O_up[..., 1] - O_up[..., 0] - O_up[..., 2]
    m = gaussian_filter(exg, 2.0 / s)
    m = np.clip((m - np.percentile(m, 30)) / (np.ptp(m) * 0.5 + 1e-6), 0, 1)
    Hm = (1 - m) * H[0] + m * H[1]

    # gain k par extrapolation spectrale (variance par octave constante)
    if k is None:
        L_o = luminance(O)
        r = 1.0                                          # px d'ortho
        octaves = math.log2(ortho_res_m / s)             # nb d'octaves ajoutées
        ref = band_log_std(L_o, 2 * r, 2 * r * 2 ** octaves) if octaves > 0 else 0.0
        got = float(np.std(np.log(np.maximum(Hm, 1e-4))))
        k = float(np.clip(ref / max(got, 1e-6), 0.3, 1.5))

    Hk = np.clip(1.0 + k * (Hm - 1.0), 0.4, 1.8)
    R = O_up * Hk[..., None]

    # recalage d'histogramme : moyenne par canal ramenée à celle de l'ortho (gain multiplicatif,
    # donc teinte et rapport R:V:B conservés). L'écart type, lui, augmente volontairement (grain ajouté).
    # Les quelques % de pixels écrêtés par Hk sont la seule déformation des extrêmes.
    for c in range(3):
        R[..., c] *= O_up[..., c].mean() / max(R[..., c].mean(), 1e-6)
    return lin_to_srgb(R), k


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ortho_tile", help="tuile d'ortho (jpg/png) couvrant tile_m × tile_m")
    ap.add_argument("out", help="sortie .jpg (qualité 92) ou .png")
    ap.add_argument("--tile-m", type=float, default=256.0)
    ap.add_argument("--out-px", type=int, default=4096)
    ap.add_argument("--ortho-res", type=float, default=0.19, help="résolution native de l'ortho (m/px)")
    ap.add_argument("--straw", default="textures/ph_withered_grass/withered_grass_diff_2k.jpg")
    ap.add_argument("--green", default="textures/ph_grass_ground/grass_ground_diff_2k.jpg")
    ap.add_argument("--period", type=float, default=2.5, help="période de répétition du détail (m)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=float, default=None)
    a = ap.parse_args()
    O = np.asarray(Image.open(a.ortho_tile).convert("RGB"))
    R, k = bake_tile(O, a.tile_m, a.out_px, a.ortho_res, a.straw, a.green, a.period, a.seed, a.k)
    Image.fromarray(R).save(a.out, quality=92)
    print(f"{a.out} : {a.out_px} px, {100 * a.tile_m / a.out_px:.2f} cm/px, k = {k:.2f}")


if __name__ == "__main__":
    main()
