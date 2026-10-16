# Textures de détail — provenance et licences (téléchargées le 2026-10-16)

Toutes CC0 1.0 (domaine public) : redistribuables dans le dépôt public Keen-Kestrel, attribution facultative
(on la garde par courtoisie dans site_meta.json).

| Dossier | Source / page | Auteur | Taille réelle d'une répétition | Cartes (2048 px) | Usage proposé |
|---|---|---|---|---|---|
| ph_withered_grass/ | https://polyhaven.com/a/withered_grass (CC0, https://polyhaven.com/license) | Charlotte Baglioni | 2,00 m (≈ 1,0 mm/px) | diff, nor_gl, rough | Paille sèche beige : matériau principal de la prairie mi-mai |
| ph_grass_ground/ | https://polyhaven.com/a/grass_ground (CC0) | Charlotte Baglioni | 2,51 m (≈ 1,2 mm/px) | diff, nor_gl, rough | Herbe sèche kaki-olive avec repousse verte : le plus proche de #817A60 ; zones vertes (ExG de l'ortho) |
| ph_sparse_grass/ | https://polyhaven.com/a/sparse_grass (CC0) | Amal Kumar | 2,00 m | diff, nor_gl, rough | Repousse verte sur sol brun : bords de sloughs, fossés (trop sombre/rouge pour la prairie) |
| acg_Ground109/ | https://ambientcg.com/view?id=Ground109 (CC0, https://docs.ambientcg.com/license/) | ambientCG (Lennart Demes) | 2,80 m | Color, NormalGL, Roughness (+ Ground109.png aperçu) | Sol nu beige/alcalin, taches claires, chemins, aire de gravier |

URL de téléchargement directes :
- https://dl.polyhaven.org/file/ph-assets/Textures/jpg/2k/<id>/<id>_{diff,nor_gl,rough}_2k.jpg
- https://ambientcg.com/get?file=Ground109_2K-JPG.zip

Notes :
- Normales au format OpenGL (Y+), celui qu'attend Ogre/gz (`nor_gl`, `NormalGL`), pas DirectX.
- `rough` / `Roughness` : niveaux de gris, 1 canal ; Ogre2 lit la rugosité dans le canal R (vérifier).
- Écartés : ambientCG Grass001-008 (gazon vert vif, irréaliste mi-mai sauf gazon du club), Poly Haven
  dry_decay_leaves / winter_leaves (litière de feuilles brun-rouge, pas de prairie), forrest_ground_01
  (brindilles + mousse), rocky_terrain_02 (aérien 90 m, trop grossier pour du détail).
- Teinte : withered_grass est plus claire et plus rosée que #817A60 ; grass_ground plus sombre/verte.
  En cuisson luminance-préservante seule la luminance relative du détail est utilisée → la couleur
  vient de l'ortho ; en détail par shader, normaliser chaque diffuse par sa moyenne (diff / mean(diff)).
