# Keen-Kestrel — sandbox ArduPilot SITL + ROS 2 + Gazebo en Docker

Sandbox Docker prête à l'emploi pour faire tourner **ArduPilot SITL + Gazebo Harmonic + ROS 2 Humble** sans installer aucune de ces trois piles sur l'hôte.

## Démarrage rapide

Aucun clone d'ArduPilot ni installation ROS/Gazebo à faire : tout est récupéré automatiquement.

```bash
git clone <url-du-repo> && cd Keen-Kestrel
make host-deps   # Ubuntu/WSL : installe Docker (+ toolkit NVIDIA si besoin) — sautez si déjà fait
                 # → se déconnecter/reconnecter si le script le demande (groupe docker)
make sim
```

C'est tout. Au premier lancement, `make sim` détecte la plateforme GPU, construit l'image (≈20-40 min) et le workspace ROS 2 (≈10-30 min), puis ouvre Gazebo et RViz. Les lancements suivants démarrent en quelques secondes. Aucun fichier à éditer.

## Table des matières

1. [Ce que c'est](#1-ce-que-cest)
2. [Prérequis hôte par plateforme](#2-prérequis-hôte-par-plateforme)
3. [Configuration (optionnelle)](#3-configuration-optionnelle)
4. [Démarrage](#4-démarrage)
5. [Voler](#5-voler)
6. [Interop avec un environnement mavros](#6-interop-avec-un-environnement-mavros)
7. [Cycle de vie](#7-cycle-de-vie)
8. [Dépannage](#8-dépannage)
9. [Annexe — prérequis ArduPilot](#9-annexe--prérequis-ardupilot-référence)

## 1. Ce que c'est

Sandbox Docker prête à l'emploi pour faire tourner **ArduPilot SITL + Gazebo Harmonic + ROS 2 Humble**, sans installer aucune de ces trois piles sur l'hôte. Le simulateur (moteur physique, drone `iris`, pont DDS `/ap/*`) tourne entièrement dans le conteneur ; seuls l'affichage (X11) et le réseau (MAVLink, DDS) traversent vers l'hôte. Le guide d'installation native dont ce projet est dérivé — utile pour comprendre chaque étape ou déboguer en profondeur — est disponible dans [`installardupilotgazeboros2.md`](./installardupilotgazeboros2.md).

```
Hôte (Ubuntu/WSL) ─── X11 (/tmp/.X11-unix) ──────────────────┐
   │                                                          │
   │ network_mode: host                                       ▼
   │                                          ┌───────────────────────────────┐
   │  MAVLink                                 │           Conteneur           │
   │  tcp 5760 (master)                       │                                │
   │  tcp 5762/5763 (serial)     ┌──────────► │  arducopter (SITL, --model json)
   │  udp 14550/14551 (GCS) ◄────┤            │        ▲                      │
   │                              │            │        │ JSON/FDM             │
   │                              │            │        ▼                      │
   │                              │            │  Gazebo (gz sim: server+GUI)  │
   │                              │            │   └─ plugin ArduPilotPlugin   │
   │                              │            │   └─ modèle iris (SDF_PATH,   │
   │                              │            │      GZ_SIM_RESOURCE_PATH)    │
   │                              │            │                                │
   │  DDS (UDP, domaine 0)        │            │  micro-ROS agent (udp4:2019)  │
   │  RMW_IMPLEMENTATION=         │            │   └─ FastDDS natif            │
   │  rmw_fastrtps_cpp    ◄───────┴──────────► │   └─ ns /iris → topics /ap/*  │
   │                                           │        ▲                      │
   │                                           │        │ ROS 2 (rclcpp)       │
   │                                           │        ▼                      │
   │                                           │  RViz2 / nœuds ROS 2          │
   │                                           │  (workspace ament dans le     │
   │                                           │   volume ardu_ws → /ardu_ws)  │
   │                                           └───────────────────────────────┘
```

Tout se pilote depuis `make` (voir `make help`) : le conteneur, le workspace ROS 2 (volume nommé, persistant), et les variantes GPU (`nvidia`/`intel`/`wsl`/`cpu`) détectées automatiquement.

## 2. Prérequis hôte par plateforme

Le conteneur fait tout le travail (ROS 2, Gazebo, ArduPilot) ; l'hôte n'a besoin que de Docker et, selon le GPU, d'un runtime graphique. Testé sur Ubuntu 22.04. `make host-deps` ([`scripts/install-host.sh`](./scripts/install-host.sh)) installe automatiquement tout ce qui suit sur Ubuntu/Debian/WSL (idempotent, liste les actions et demande confirmation avant sudo) ; les sous-sections ci-dessous détaillent l'installation manuelle. Ces prérequis sont vérifiés par `make configure` (appelé automatiquement au premier `make`), qui s'arrête avec la commande de correction si l'un d'eux manque.

### 2.1 Docker Engine + Compose (toutes plateformes)

Installer Docker Engine (pas Docker Desktop sous Linux) via le dépôt officiel : [docs.docker.com/engine/install](https://docs.docker.com/engine/install/). Le plugin Compose v2 (`docker compose`, sans tiret) est inclus dans le paquet `docker-compose-plugin` de ce dépôt.

Vérification :

```bash
docker compose version   # >= v2
docker run --rm hello-world
```

Ajouter son utilisateur au groupe `docker` si `docker run` exige `sudo` (`sudo usermod -aG docker $USER`, puis se reconnecter).

### 2.2 GPU NVIDIA

Nécessite le driver propriétaire installé sur l'hôte (`nvidia-smi` doit fonctionner en dehors de tout conteneur) ainsi que le [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) pour exposer le GPU aux conteneurs.

```bash
# Dépôt officiel
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Branche le runtime NVIDIA sur le daemon Docker
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

> **Attention** : `systemctl restart docker` coupe tous les conteneurs en cours sur l'hôte, pas seulement ce projet.

Vérification (doit afficher la table `nvidia-smi`, GPU visible depuis le conteneur) :

```bash
docker run --rm --gpus all ubuntu nvidia-smi
```

Une fois validé, `make configure` sélectionne `compose.nvidia.yaml` automatiquement — il ajoute `gpus: all` et `NVIDIA_DRIVER_CAPABILITIES=graphics,utility,compute`. Sans la capacité `graphics`, Gazebo bascule silencieusement sur le rendu logiciel `llvmpipe` (fonctionne mais sans accélération GPU, aucun message d'erreur). Si le toolkit est installé après un premier lancement : `make configure` puis `make sim`.

### 2.3 GPU Intel / AMD

Rendu direct via `/dev/dri`, rien à installer. `make` dérive le gid du groupe `render` de l'hôte (`getent group render`) et l'exporte en `RENDER_GID` pour Compose ; ne le renseigner dans `.env` que si cette auto-détection échoue. `compose.intel.yaml` monte `/dev/dri` et ajoute ce gid au conteneur via `group_add`, sinon l'utilisateur `dev` du conteneur n'a pas accès au périphérique de rendu même monté.

### 2.4 WSL2 (Windows 11)

Docker **natif dans la distro WSL** est obligatoire — pas Docker Desktop. Les compose de ce projet utilisent `network_mode: host` pour que le SITL (MAVLink sur `tcp:5760/5762/5763`, DDS sur `udp:2019`) soit joignable directement à `127.0.0.1` depuis l'hôte Windows/WSL. Le réseau « host » exposé par Docker Desktop est celui de sa propre VM utilitaire, pas le loopback de la distro WSL : les ports ne remontent pas côté distro et le SITL devient injoignable. Installer Docker Engine à l'intérieur de la distro (§2.1) évite ce problème.

WSLg (support GUI/X11, requis pour Gazebo et RViz) est intégré par défaut sur Windows 11 — aucune installation supplémentaire. Vérifier :

```bash
ls /mnt/wslg   # doit exister
```

`compose.wsl.yaml` monte `/mnt/wslg`, `/usr/lib/wsl`, `/dev/dxg` et règle `LD_LIBRARY_PATH=/usr/lib/wsl/lib` pour le rendu GPU via WSLg.

## 3. Configuration (optionnelle)

Rien n'est à configurer pour démarrer : au premier `make`, [`scripts/configure.sh`](./scripts/configure.sh) vérifie l'hôte, détecte la plateforme GPU et crée `.env` depuis [`.env.example`](./.env.example). Pour re-détecter (ex. après installation du toolkit NVIDIA) ou forcer une plateforme :

```bash
make configure                  # re-détection automatique
PLATFORM=intel make configure   # nvidia | intel | wsl | cpu
```

`make configure` ne réécrit que la ligne `COMPOSE_FILE` ; vos autres réglages dans `.env` sont conservés. Après toute modification de `.env`, relancez simplement la commande voulue (`make sim`…) : le conteneur est recréé et le workspace réinitialisé automatiquement.

| Variable | Défaut | Rôle |
|---|---|---|
| `COMPOSE_FILE` | écrit par `make configure` | Overlay Compose (device GPU, mounts) de la plateforme détectée. Forcer : `PLATFORM=intel make configure` (`nvidia`, `intel`, `wsl` ou `cpu`). |
| `ROS_DOMAIN_ID` | `0` | Domaine DDS. Doit être identique au paramètre ArduPilot `DDS_DOMAIN_ID` (défaut ArduPilot : `0`) pour que les topics `/ap/*` soient visibles côté ROS 2. |
| `RMW_IMPLEMENTATION` | `rmw_fastrtps_cpp` | RMW par défaut d'Humble. Le micro-ROS agent qui relie ArduPilot à ROS 2 est un agent **FastDDS natif**, pas un nœud RMW comme un autre — mais `rmw_cyclonedds_cpp` fonctionne tout aussi bien pour découvrir les topics `/ap/*`, à condition que `ROS_LOCALHOST_ONLY=0` (voir ligne suivante). |
| `ROS_LOCALHOST_ONLY` | `0` | **Doit rester à `0` pour voir les topics `/ap/*`.** L'agent micro-ROS ignore cette variable : il est FastDDS natif et annonce toujours sur les interfaces réseau réelles, jamais seulement sur `lo`. Un consommateur ROS 2 configuré en localhost-only (quel que soit son RMW, FastDDS ou CycloneDDS) ne le découvre donc jamais → `ros2 topic list` ne montre aucun `/ap/*`. Avec `loc=0`, testé et validé : 18 topics visibles, avec FastDDS **et** avec CycloneDDS. Revers de la médaille : le conteneur tourne en `network_mode: host`, donc `loc=0` rend le DDS (et MAVLink) visibles sur le LAN — pare-feu recommandé sur réseau non fiable. |
| `RENDER_GID` | auto-détecté par `make` | Intel/AMD uniquement : gid du groupe `render` de l'hôte (`getent group render`), ajouté aux groupes du conteneur pour l'accès `/dev/dri`. Sans objet sur NVIDIA/WSL. |
| `COLCON_JOBS` | auto : min(cœurs, RAM / 2 Go) | Workers parallèles de `colcon build` à l'init du workspace. Décommenter dans `.env` pour forcer une valeur. |

En résumé, ces défauts sont ceux de la **seule combinaison qui expose de façon fiable les 18 topics `/ap/*` de bout en bout** : domaine `0` + `ROS_LOCALHOST_ONLY=0`, avec FastDDS ou CycloneDDS indifféremment côté consommateur. Ne repassez `ROS_LOCALHOST_ONLY` à `1` que si vous n'avez pas besoin des topics `/ap/*` (MAVLink seul, par exemple) et voulez limiter l'exposition réseau.

**Paramètres ArduPilot personnalisés — [`config/sim.parm`](./config/sim.parm).** Ce fichier, édité sur l'hôte, est monté en lecture seule dans le conteneur (`./config:/ardu_ws/config:ro` dans `compose.yaml`) et placé en **dernier** dans la chaîne `--defaults` du SITL (le dernier fichier gagne sur les précédents : `copter.parm`, `gazebo-iris-gimbal.parm`, `dds_udp.parm`, `dds_use_ns.parm`, puis `sim.parm`). Toute modification est prise en compte au prochain relancement de `make sim` ou `make sitl` — pas besoin de rebuild. Le repo livre par défaut `BATT_CAPACITY 5000` (capacité batterie simulée en mAh), validé en lisant le paramètre depuis le SITL après boot.
>
> **Piège** : un paramètre modifié en vol via le GCS (`param set ...` dans MAVProxy) est écrit dans l'eeprom persistant du SITL (`/ardu_ws/eeprom.bin`, dans le volume `ardu_ws`), qui **gagne** sur `config/sim.parm` à chaque boot suivant pour ce paramètre précis. Pour redonner la main aux defaults + `sim.parm` : `make clean-eeprom`.

## 4. Démarrage

```bash
make sim     # tout-en-un : configure + build + init si nécessaire, puis lance la sim
```

Pour installer sans ouvrir de fenêtre (machine distante, CI, vérification) :

```bash
make setup   # configure + build + init + smoke-test, sans lancer la sim
```

- Le premier lancement est long (image ≈20-40 min, workspace ≈10-30 min selon machine et réseau) ; les suivants sont immédiats. En cas de coupure réseau, relancez la même commande : tout reprend où ça s'était arrêté.
- **Critère de succès de `make test`** (inclus dans `make setup`) : la sortie `glxinfo -B` doit annoncer un renderer GPU réel (ex. `NVIDIA GeForce RTX ...`, ou le renderer Mesa/Intel attendu) — **pas `llvmpipe`**. `llvmpipe` signifie un rendu logiciel silencieux, typiquement parce que la mauvaise plateforme a été détectée (`make configure` affiche celle retenue) ; Gazebo tournera mais très lentement.

## 5. Voler

### 5.1 Lancer la simulation

```bash
make sim
```

Deux fenêtres doivent s'ouvrir :

- **Gazebo** : le monde `iris_runway` avec le drone iris complet (corps + rotors + gimbal) posé sur la piste. Si le modèle apparaît incomplet ou sans textures (meshes manquants), voir la note SDF_PATH/GZ_SIM_RESOURCE_PATH dans [Dépannage](#8-dépannage).
- **RViz** : le `robot_state_publisher` affichant le TF de l'iris.

Laisser cette commande tourner dans son terminal — elle bloque tant que la simulation est active (`Ctrl+C` pour arrêter).

### 5.2 Vérifier le pont DDS

Dans un second terminal :

```bash
make shell
```

Puis, dans le shell du conteneur :

```bash
ros2 topic list | grep /ap/
```

18 topics `/ap/...` doivent apparaître (`/ap/geopose/filtered`, `/ap/imu/experimental`, `/ap/navsat`, etc.). Si la liste est vide, voir [Dépannage](#8-dépannage) (le cas le plus fréquent : `ROS_LOCALHOST_ONLY` pas à `0`, voir §3).

Vérifier ensuite qu'un topic publie effectivement :

```bash
ros2 topic echo /ap/geopose/filtered
```

Des messages doivent défiler en continu (position/orientation du drone).

### 5.3 Piloter avec MAVProxy

Dans un troisième terminal :

```bash
make mavproxy
```

wxPython est présent dans l'image, `--console --map` s'ouvrent normalement. À l'invite MAVProxy :

```
mode guided
arm throttle
takeoff 5
```

Le drone doit décoller et monter jusqu'à 5 m dans la fenêtre Gazebo. Suivre l'altitude via `ros2 topic echo /ap/geopose/filtered` ou directement dans la console MAVProxy.

Pour terminer le vol proprement :

```
mode land
```

Le drone descend et se désarme automatiquement à l'atterrissage. En cas de besoin (arrêt d'urgence, blocage), `disarm` force le désarmement immédiat — les moteurs s'arrêtent quelle que soit l'altitude.

### 5.4 Variante sans rendu graphique

Pour un cycle SITL + DDS sans lancer Gazebo (utile en CI ou sur une machine sans GPU/X11) :

```bash
make sitl
```

Cette cible démarre ArduPilot SITL seul et expose le même pont DDS (`/ap/...`), sans le serveur de rendu Gazebo ni RViz.

### 5.5 Aide-mémoire des cibles

```bash
make help
```

Liste toutes les cibles disponibles — c'est le point d'entrée à consulter en cas de doute, le Makefile est l'unique interface de ce projet :

| Cible | Description |
|---|---|
| `help` | Affiche cette aide |
| `build` | Construit l'image `keen-kestrel` |
| `up` | Démarre le conteneur `sim` en arrière-plan |
| `down` | Arrête et retire le conteneur `sim` |
| `init` | Initialise le workspace ROS 2 (1er run long, relançable ; `COLCON_JOBS=N` pour ajuster) |
| `sim` | Lance SITL + Gazebo + RViz (`iris_runway`, ou `WORLD=medicine_hat [REPERES=0]`), params : `config/sim.parm` |
| `world` | Génère un monde géoréférencé : `make world WORLD=medicine_hat [ORTHO=...]` (voir §5.6) |
| `check-world` | Pendant `make sim WORLD=...` : vérifie GPS SITL = Gazebo, vol et atterrissage sur le relief |
| `sitl` | Lance SITL + DDS seul, sans Gazebo |
| `mavproxy` | Ouvre une console MAVProxy (console + carte) sur le SITL local |
| `shell` | Ouvre un shell interactif dans le conteneur, ROS + workspace sourcés |
| `test` | Smoke-tests : OpenGL, Gazebo headless, DDS-Gen, MAVProxy |
| `clean-eeprom` | Supprime l'eeprom SITL : les params reviennent aux defaults + `sim.parm` |
| `clean` | Down + supprime le volume `ardu_ws` (destructif, confirmation requise) |

### 5.6 Mondes géoréférencés (site de compétition)

`worlds/<site>/site.json` décrit un vrai terrain : origine, emprise, limites, obstacles. `make world` en tire un monde Gazebo avec le relief réel et une orthophoto, et `make sim WORLD=<site>` le lance avec le SITL calé dessus. Le seul site pour l'instant est `medicine_hat` (AEAC 2027, Medicine Hat RC'ers Club).

```bash
make world WORLD=medicine_hat        # ≈ 10 min la 1re fois (relief à télécharger), ≈ 1 min ensuite ; internet requis
make sim WORLD=medicine_hat          # SITL + Gazebo + RViz sur le terrain
make sim WORLD=medicine_hat REPERES=0   # sans les limites dessinées au sol (images pour la vision)
make check-world WORLD=medicine_hat  # dans un 2e terminal : vol de validation (≈ 3 min)
```

**Ce que contient le monde**

- **Relief** : NRCan HRDEM 1 m (lidar) là où il existe, ailleurs MRDEM-30 corrigé par les courbes de niveau 1 m de la Ville de Medicine Hat ; ±0,25 m sur l'emprise. Maillages OBJ : tuiles visuelles de 256 m (pas de 2 m sur la zone de mission, 8 m autour) et un maillage de collision à 8 m.
- **Texture** : les orthophotos listées dans `orthos` de `site.json` (pour Medicine Hat : l'ortho de la Ville du 2024-05-15 via Esri, ≈ 0,19 m, dans `worlds/medicine_hat/ortho/`), complétées par Sentinel-2 (10 m). `ORTHO=autre.tif` remplace la liste (GeoTIFF RGB géoréférencé, chemin relatif à `worlds/<site>/`, plusieurs par priorité). Texture finale : 0,25 m/px sur la zone de mission, 1 m autour.
- **Repères** (monde `world`, pas `world_clean`) : limite hard en rouge, soft en vert, ligne de vol en jaune, point de décollage en cyan. Visuels seulement, sans collision.
- **Éoliennes** voisines (OSM), soleil calculé pour la date de la compétition (`--sun`), vent moyen (`--wind`) : options de `worlds/build_world.py` à passer par `WORLD_ARGS="..."`.

**Alignement avec le SITL.** Avec Gazebo, ArduPilot ne lit pas le monde : il calcule sa position GPS comme `home + position Gazebo`. L'origine du monde est donc le home du SITL, fixé par `SIM_OPOS_*` dans `worlds/<site>/build/<site>.parm`, placé avant `config/sim.parm` dans la chaîne `--defaults`. Ces valeurs sont des float32 exacts, identiques à `<spherical_coordinates>` : pas d'arrondi entre les deux. Le sol vaut z = 0 à l'origine. Validé par `check-world` : écart GPS-Gazebo de 0,01 m au sol et < 1 m à 800 m, atterrissage sur le relief à 1 cm près.

**Vent.** Avec Gazebo (`--model json`), `SIM_WIND_*` n'a aucun effet. Le vent moyen se règle dans le monde (`WORLD_ARGS='--wind "3.90 2.25"'`, Est Nord en m/s), ou en cours de sim :
`gz topic -t /world/medicine_hat/wind -m gz.msgs.Wind -p "linear_velocity: {x: 3.9, y: 2.25}, enable_wind: true"`.

**Licences et attributions.** Relief : NRCan (OGL-Canada), City of Medicine Hat (licence ouverte). Ortho : Esri, Vantor, Earthstar Geographics; City of Medicine Hat (conditions Esri, voir `worlds/medicine_hat/ortho/LISEZMOI.md`). Sentinel-2 : Copernicus. Éoliennes : OpenStreetMap (ODbL). Liste dans `build/site_meta.json`. Les sorties (`build/`) et téléchargements (`cache/`) sont ignorés par git : ils se régénèrent.

**Assets et prototypes.** `worlds/assets/` rassemble du matériel libre pas encore branché : textures d'herbe CC0, cerfs CC0 (GLB), générateur d'étiquettes d'oreille, tapis d'atterrissage (voir son README). `worlds/prototypes/` contient le générateur de rafales (`wind_gusts.py`) et la cuisson de texture de détail (`cuisson_detail.py`), **pas encore testés dans Gazebo**.

**Performance.** Le monde Medicine Hat fait environ 1 million de triangles et 100 Mo de textures. Facteur temps réel mesuré : 0,65 sous WSL2 (RTX 4060) avec la caméra du gimbal active et d'autres simulations en parallèle.

## 6. Interop avec un environnement mavros

Ce repo tourne dans son propre réseau de conteneurs, mais expose SITL (MAVLink) et les topics ArduPilot DDS (`/ap/*`) sur l'hôte via `network_mode: host` + `ipc: host`. Il peut donc interopérer avec un conteneur mavros voisin tournant sur la même machine, à condition d'aligner réseau, port et domaine DDS. **Testé empiriquement** avec une image mavros existante lancée en conteneur séparé.

### 6.1 Connexion MAVLink (mavros)

Lancez la simulation depuis ce repo :

```bash
make sim
```

SITL expose plusieurs endpoints MAVLink. Le port **5760** est réservé au GCS principal (celui que le launch ArduPilot utilise en interne comme `master`) — pour un client externe comme mavros, utilisez le second serial :

```
fcu_url:=tcp://127.0.0.1:5762
```

Les deux côtés doivent être en `network_mode: host` : sans ça, `127.0.0.1:5762` de l'un ne pointe pas vers l'autre.

### 6.2 Visibilité des topics `/ap/*` (DDS)

Les topics `/ap/*` viennent du micro-ROS agent, qui parle FastDDS natif. Pour qu'un consommateur externe les voie, il faut simultanément :

1. `ROS_DOMAIN_ID` du consommateur == paramètre `DDS_DOMAIN_ID` d'ArduPilot (défaut **0**, réglé par `dds_udp.parm` dans ce repo) ;
2. `ROS_LOCALHOST_ONLY=0` côté consommateur — sinon, quel que soit son RMW, il ne découvre jamais l'agent (voir §3).

Matrice validée (côté consommateur) :

| RMW consommateur | Domaine | `ROS_LOCALHOST_ONLY` | Résultat |
|---|---|---|---|
| `rmw_fastrtps_cpp` | 0 | 0 | 18 topics `/ap/*` visibles |
| `rmw_cyclonedds_cpp` | 0 | 0 | 18 topics `/ap/*` visibles |
| `rmw_fastrtps_cpp` | 0 | 1 | 0 (consommateur localhost-only, ne découvre pas l'agent) |
| `rmw_cyclonedds_cpp` | 0 | 1 | 0 (idem) |
| n'importe lequel | ≠ 0 | — | 0 (mauvais domaine) |

Le point qui piège le plus souvent un environnement mavros voisin déjà en place : ses défauts DDS à lui (`ROS_DOMAIN_ID` différent de 0, ou `ROS_LOCALHOST_ONLY=1`) ne verront **aucun** topic `/ap/*` tant qu'ils ne sont pas alignés — indépendamment du RMW utilisé.

### 6.3 Test validé — conteneur mavros isolé

Exemple de commande qui a fonctionné en test (adapter le nom d'image, et le sourcing du workspace si mavros est buildé depuis les sources dans l'image) :

```bash
docker run -d --name mavros-test --network host \
  -e ROS_DOMAIN_ID=99 -e ROS_LOCALHOST_ONLY=1 -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
  <image-mavros> \
  ros2 launch mavros apm.launch fcu_url:=tcp://127.0.0.1:5762 fcu_protocol:=v2.0

# Vérif MAVLink (mêmes -e que le conteneur, à répéter sur le docker exec !) :
docker exec -e ROS_DOMAIN_ID=99 -e ROS_LOCALHOST_ONLY=1 -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
  mavros-test ros2 topic echo --once /mavros/state
# → connected: true
```

Ce test valide la connexion MAVLink (`ROS_DOMAIN_ID=99`, `ROS_LOCALHOST_ONLY=1` ici : sans objet, MAVLink ne dépend pas du DDS). Pour voir en plus les topics `/ap/*` de ce repo depuis le **même** conteneur mavros (ou un autre nœud ROS 2), il faut repasser ce conteneur en domaine `0` et `ROS_LOCALHOST_ONLY=0`, conformément à la matrice ci-dessus — les deux besoins (MAVLink isolé vs. visibilité `/ap/*`) peuvent être contradictoires selon l'usage, à trancher au cas par cas. `ipc: host` est recommandé des deux côtés (déjà présent dans `compose.yaml` de ce repo) pour le transport SHM DDS inter-conteneurs.

### 6.4 (Optionnel) Changer le domaine DDS d'ArduPilot

Pour matcher un domaine déjà utilisé côté mavros (ex. 2) plutôt que de toucher cet environnement :

```bash
make mavproxy
```

Dans la console MAVProxy :

```
param set DDS_DOMAIN_ID 2
```

Le paramètre est écrit dans l'eeprom du SITL, qui vit sur le volume nommé `keen-kestrel_ardu_ws` : la valeur survit à un `make down`/`make up` (pas à un `make clean`, qui supprime le volume, ni à un `make clean-eeprom`).

## 7. Cycle de vie

Le workspace ROS 2 (sources vcs, build colcon, caches) vit dans le volume Docker nommé `keen-kestrel_ardu_ws`, monté sur `/ardu_ws`. Ce volume est indépendant du conteneur.

- **`make down`** arrête et retire le conteneur `sim` mais ne touche pas au volume : builds, sources et caches (`CCACHE_DIR`, `GZ_FUEL_CACHE_PATH`) sont conservés.
- **Recréation du conteneur** (modification de `.env`, `make build` après une modification du `Dockerfile`) : automatique au prochain `make sim`/`sitl`/`shell`. Les paquets `rosdep` vivant dans le layer du conteneur, l'init du workspace est rejouée automatiquement (≈1-2 min, `colcon build` quasi no-op). `make init` la force manuellement.
- **`make clean-eeprom`** supprime `/ardu_ws/eeprom.bin` : au prochain boot du SITL, tous les paramètres reviennent aux defaults + `config/sim.parm` (utile si un `param set` fait via GCS a écrasé une valeur de `sim.parm`, voir §3).
- **`make clean`** = `down` + suppression du volume `keen-kestrel_ardu_ws` (perte totale du workspace et des builds). Confirmation interactive `[y/N]` requise — opération destructive, pas de retour en arrière.

## 8. Dépannage

| Symptôme | Cause | Fix |
|---|---|---|
| L'iris n'apparaît jamais dans Gazebo (le nœud `create` attend `robot_description` indéfiniment) | `SDF_PATH` n'inclut pas la racine `share/` du paquet — `robot_state_publisher` ne résout pas `package://ardupilot_gazebo/...` et crashe (`std::runtime_error`) | Utiliser `make sim`/`make shell` (le Makefile exporte `SDF_PATH` automatiquement). En commande manuelle : sourcer le workspace puis `export SDF_PATH=$SDF_PATH:/ardu_ws/install/ardupilot_gazebo/share` |
| Le drone apparaît mais incomplet (pièces/meshes manquantes), logs `MeshManager.cc:211` | `GZ_SIM_RESOURCE_PATH` n'inclut pas `.../ardupilot_gazebo/share` — gz-sim ne trouve pas les `.dae`/`.stl` | Idem ci-dessus : `make sim` exporte la variable ; en manuel, ajouter le même chemin à `GZ_SIM_RESOURCE_PATH` |
| Aucun topic `/ap/*` (`ros2 topic list` vide) | `ROS_LOCALHOST_ONLY` pas à `0` (l'agent micro-ROS est FastDDS natif et un consommateur localhost-only ne le découvre jamais, quel que soit le RMW — voir §3), mismatch `ROS_DOMAIN_ID` / paramètre ArduPilot `DDS_DOMAIN_ID` (défaut 0) | Vérifier `.env` : `ROS_LOCALHOST_ONLY=0`, `ROS_DOMAIN_ID=0` ; si un `ros2 daemon` a mis en cache un ancien état DDS, `ros2 daemon stop` puis relancer la commande |
| `takeoff` refusé (`COMMAND_ACK` avec `result=4`, `MAV_RESULT_FAILED`) alors que le drone est armé et en mode `GUIDED` | L'EKF n'est pas encore prêt (attendre ~1 min après le lancement de `make sim`/`make sitl` — chercher `EKF3 ... complete` dans les logs du SITL) ; ou le véhicule est resté bloqué « en vol » (`landed_state=2`) après une tentative de décollage ratée | Attendre la stabilisation EKF avant `takeoff` ; si le véhicule reste bloqué en état « en vol », `disarm` (désarmement forcé) puis réarmer et retenter |
| Passage en mode `GUIDED` refusé | L'EKF n'a pas encore de position valide (pas de fix GPS) | Attendre le fix GPS après le boot du SITL (~30 s), puis réessayer `mode guided` |
| Rendu 3D très lent, CPU saturé | GPU non exposé au conteneur — `NVIDIA_DRIVER_CAPABILITIES` sans `graphics` bascule silencieusement sur le rasterizer logiciel `llvmpipe` | Vérifier `compose.nvidia.yaml` (`gpus: all`, `NVIDIA_DRIVER_CAPABILITIES=graphics,utility,compute`) ; contrôler le renderer avec `make test` (sortie `glxinfo -B`, doit citer le GPU NVIDIA, pas `llvmpipe`) |
| Aucune fenêtre Gazebo/RViz ne s'ouvre (`cannot open display`, `Authorization required`) | X11 non partagé avec le conteneur | Vérifier le montage de `/tmp/.X11-unix` et de `$XAUTHORITY` ; si le cookie n'est pas trouvé (session GDM), lancer `xhost +local:` sur l'hôte |
| `make init` échoue en cours de `colcon build` (erreur réseau `rosdep`/`vcs`) | Téléchargement de dépendances interrompu | Relancer la même commande (`make sim` ou `make init`) : idempotent, reprend le build de façon incrémentale |
| Agent micro-ROS qui meurt au lancement : `reference file '.../dds_xrce_profile.xml' does not exist` | Commande copiée d'une doc ArduPilot 4.5 avec `refs:=` : ce fichier n'existe plus depuis ArduPilot 4.6 | Retirer l'argument `refs:=` (`make sitl` ne le passe pas) |
| Intel/AMD : accès `/dev/dri` refusé dans le conteneur | `RENDER_GID` absent ou obsolète | `getent group render` sur l'hôte ; si `make` ne le détecte pas automatiquement, reporter le gid dans `RENDER_GID` (`.env`), relancer `make sim` |
| `bind: address already in use` sur 14550/14551/5760/5762/5763/2019 | Une autre instance SITL/agent tourne déjà sur l'hôte (`network_mode: host` partagé) | `make down`, ou arrêter l'autre instance (ex. un environnement mavros existant) avant `make up` |
| Environnement mavros externe : MAVLink connecte mais aucun `/ap` visible | `ROS_DOMAIN_ID` différent de 0, ou `ROS_LOCALHOST_ONLY=1` côté mavros | Repasser le conteneur mavros en `ROS_DOMAIN_ID=0`, `ROS_LOCALHOST_ONLY=0` (RMW indifférent, voir §6.2), ou changer `DDS_DOMAIN_ID` côté SITL (§6.4). Vérifier `ipc: host` des deux côtés pour le SHM |

## 9. Annexe — prérequis ArduPilot (référence)

Ces paquets sont installés automatiquement pendant `make build` par `install-prereqs-ubuntu.sh` (exécuté avec `USER=dev`, sur un clone shallow jetable d'ArduPilot). Liste condensée pour la branche Ubuntu 22.04 "jammy" — utile uniquement en cas d'installation native hors conteneur (voir `installardupilotgazeboros2.md`).

**Paquets système de base**
`build-essential ccache g++ gawk git make wget valgrind screen python3-pexpect astyle python-is-python3 realpath`

**Bibliothèques SITL / graphiques**
`libtool libtool-bin libxml2-dev libxslt1-dev python3-dev python3-pip python3-setuptools python3-numpy python3-pyparsing python3-psutil libpython3-stdlib xterm xfonts-base python3-matplotlib python3-serial python3-scipy python3-opencv python3-yaml python3-wxgtk4.0 libcsfml-dev libcsfml-{audio,graphics,network,system,window}2.5 libsfml-dev libsfml-{audio,graphics,network,system,window}2.5 fonts-freefont-ttf libfreetype6-dev libpng16-16 libportmidi-dev libsdl-image1.2-dev libsdl-mixer1.2-dev libsdl-ttf2.0-dev libsdl1.2-dev ppp`

**Toolchain croisée ARM** (installée par défaut, non requise pour SITL)
`g++-arm-linux-gnueabihf pkg-config-arm-linux-gnueabihf`

**Paquets Python (pip, hors dépôts apt)**
`future lxml pymavlink pyserial MAVProxy geocoder empy==3.3.4 ptyprocess dronecan flake8 junitparser wsproto tabulate pygame intelhex`

Jammy n'entre pas dans la branche `venv` du script (réservée à Bookworm/Noble/Trixie et suivants) : les paquets sont installés directement dans l'environnement système, sans virtualenv Python dédié.

## Licence

MIT — voir [LICENSE](LICENSE). ArduPilot, ROS 2 et Gazebo ne sont pas inclus dans ce dépôt : ils sont téléchargés au build et restent sous leurs propres licences (ArduPilot : GPLv3).
