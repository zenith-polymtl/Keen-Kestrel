# Orthophoto du site

Orthophoto de la City of Medicine Hat, prise le 2024-05-15 (Pictometry, 7,5 cm), telle que diffusée par Esri World Imagery (tuiles z19 et z18, assemblées en GeoTIFF EPSG:3857 le 2026-10-15). Elle sert de texture au monde Gazebo : `site.json` la déclare dans `orthos`, par priorité ; Sentinel-2 complète le reste de l'emprise.

| Fichier | Tuiles | Résolution au sol | Emprise |
|---|---|---|---|
| `esri_world_imagery_z19_epsg3857.tif` | z19 | ≈ 0,19 m | limite de vol + 300 m |
| `esri_world_imagery_z18_epsg3857.tif` | z18 | ≈ 0,38 m | carré de 2 km |

**Attribution** : Esri, Vantor, Earthstar Geographics; City of Medicine Hat. Ces images restent soumises aux conditions d'utilisation d'Esri (https://www.esri.com/en-us/legal/terms/full-master-agreement).

Une version sous licence ouverte peut être demandée à la Ville (GIS@medicinehat.ca) ; elle remplacerait ces fichiers sans autre changement.
