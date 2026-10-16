# Prototypes non testés

Écrits le 2026-10-16 sans pouvoir lancer Gazebo : une campagne SITL occupait le poste. Chaque script le dit en tête et liste ce qu'il faut vérifier au premier lancement. À tester, puis à intégrer dans `build_world.py` ou dans une cible `make`.

| Script | But | Testé |
|---|---|---|
| `wind_gusts.py` | Publie `gz.msgs.Wind` sur `/world/<monde>/wind` à environ 20 Hz, en temps simulé : vent moyen du scénario, turbulence de Dryden (MIL-F-8785C), rafales discrètes « 1-cos », bascule frontale. Scénarios `nominal`, `venteux`, `extreme`. Demande le système `WindEffects` dans le monde, en mode passe-plat (voir l'en-tête). | `--dry-run` seulement : moyennes et rafales de 3 s conformes aux cibles (nominal 4,3-4,7 m/s et pointes 7,2-7,7 ; venteux 7,5-7,8 et 12,4-13,4 ; extrême 11,3-11,6 et 19,4-21,1). |
| `cuisson_detail.py` | « Cuit » une texture d'herbe (`assets/textures/`) dans les tuiles d'ortho de la zone de recherche, en multiplication luminance-préservante : la couleur et les basses fréquences restent celles de l'ortho. Cible : 6,25 cm/px (tuiles de 4096 px par 256 m). | Non. À lancer dans l'image `keen-kestrel-geotools` (numpy, Pillow, scipy). |

Pièges connus de l'API de vent de gz-sim 8, relevés dans le code :

- un message `/wind` **remplace** la consigne, il ne s'y ajoute pas ;
- un message sans `enable_wind: true` coupe et fige le vent ;
- un seul message est traité par pas de physique ;
- `force_approximation_scaling_factor` donne une force en `m·s·(v_vent − v_lien)` : partir de s ≈ 0,1 s⁻¹ et caler sur l'inclinaison réelle du drone en stationnaire ;
- le modèle iris n'a pas `<enable_wind>` : sans ce drapeau, seule la portance des pales (LiftDrag) voit le vent.
