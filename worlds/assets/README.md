# Assets des mondes

Matériel libre pour habiller les mondes générés par `worlds/build_world.py` : textures de détail, cerfs, étiquettes d'oreille, tapis d'atterrissage. Pas encore branché dans `build_world.py` : voir la feuille de route dans le README principal (§5.6) et `worlds/prototypes/`.

Rassemblé le 2026-10-16. Tout est redistribuable dans ce dépôt public.

| Dossier | Contenu | Source | Licence |
|---|---|---|---|
| `textures/ph_withered_grass/` | Paille sèche beige, 2 m par répétition ; albedo, normale GL, rugosité en 2K | Poly Haven, Charlotte Baglioni | CC0 1.0 |
| `textures/ph_grass_ground/` | Herbe sèche kaki-olive avec repousse verte, 2,51 m ; la plus proche de la prairie mi-mai | Poly Haven, Charlotte Baglioni | CC0 1.0 |
| `textures/ph_sparse_grass/` | Repousse verte sur sol brun, 2 m ; bords de sloughs, fossés | Poly Haven, Amal Kumar | CC0 1.0 |
| `textures/acg_Ground109/` | Sol nu beige ou alcalin, 2,8 m ; taches claires, chemins | ambientCG | CC0 1.0 |
| `deer/quaternius_deer/Deer.glb` | Biche low-poly animée (2 096 triangles, couleurs unies) | Quaternius, https://poly.pizza/m/T6Cs7tmMHJ | CC0 1.0 |
| `deer/quaternius_stag/Stag.glb` | Cerf avec bois, low-poly animé (3 667 triangles) | Quaternius, https://poly.pizza/m/tQdzbZ1Cmw | CC0 1.0 |
| `tags/` | Générateur d'étiquettes d'oreille (`make_tags.py`, PNG recto/verso), maillage `tag.obj` à deux faces, planche d'exemples | Généré | CC0 1.0 |
| `fonts/texgyretermes-*.otf` | TeX Gyre Termes, clone libre de Times : la Figure 2 du CONOPS est en Times New Roman | GUST e-foundry | GUST Font License (`fonts/GUST-FONT-LICENSE.txt`) |
| `landing_pad/` | Textures du tapis de 32 po, faces bleue et orange, et leur générateur | Généré | CC0 1.0 |

Détail des textures et de leur usage : `textures/LICENCES.md`.

## Notes d'usage

- **Cerfs** : les GLB passent par assimp dans gz-sim 8 (`<mesh><uri>…/Deer.glb</uri></mesh>`). Ils sont à remettre à l'échelle du leurre cité par le CONOPS, le Flambeau Masters Series « Boss Buck » : 152 cm du nez à la queue, 104 cm au garrot, 147 cm à la pointe des bois, flanc mat `#756249`. Ce sont des stand-ins : la cible est un scan par photogrammétrie d'un vrai leurre.
- **Étiquettes** : `nice -n 19 python3 tags/make_tags.py --codes 3V K7` écrit `tag_<code>_recto.png` et `_verso.png` (trou transparent). Rectangle vertical de rapport 1,75, code centré à 54 % de la hauteur sur 75 % de la largeur. La taille réelle n'est pas fixée par le CONOPS : 43 × 75 mm si le code fait 85 pt, 63 × 110 mm si « ~3 cm » désigne la hauteur des lettres. Pour la vision, simuler le plus petit cas.
- **Textures de détail** : normales au format OpenGL (Y+), celui qu'attend Ogre. Pour les « cuire » dans l'ortho de la zone de recherche, voir `worlds/prototypes/cuisson_detail.py`.
