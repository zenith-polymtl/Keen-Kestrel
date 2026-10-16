SHELL := /bin/bash
.DEFAULT_GOAL := help

# Gid du groupe render de l'hôte (Intel/AMD) — dérivé automatiquement; le défaut 992
# de compose.intel.yaml ne sert que si le groupe n'existe pas.
export RENDER_GID ?= $(shell getent group render 2>/dev/null | cut -d: -f3)

# uid/gid de l'hôte passés au build : l'utilisateur dev du conteneur a le même uid,
# donc accès au cookie X11 et aux fichiers montés sans réglage.
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

# Empreinte de .env (évaluée à chaque recette) : compose recrée le conteneur quand
# .env change, ce qu'il ne détecte pas seul pour un env_file.
export KK_ENV_HASH = $(shell sha1sum .env 2>/dev/null | cut -c1-12)

# Marqueur posé par init-workspace.sh dans le layer du conteneur (pas le volume) :
# absent = conteneur neuf ou recréé → deps rosdep à réinstaller → init automatique.
INIT_MARKER = /var/lib/init-workspace-done

# Ordre de sourcing important : ROS d'abord, workspace ensuite (sinon les overlays
# colcon ne surchargent pas correctement les paquets de /opt/ros/humble).
# SDF_PATH + GZ_SIM_RESOURCE_PATH : libsdformat (robot_state_publisher) et gz-sim
# résolvent package://ardupilot_gazebo/... depuis la racine share/, que les hooks du
# paquet n'ajoutent pas. Sans SDF_PATH l'iris ne spawn pas (create attend robot_description) ;
# sans GZ_SIM_RESOURCE_PATH les meshes (.dae/.stl) sont introuvables dans Gazebo.
AP_SHARE = /ardu_ws/install/ardupilot_gazebo/share
SRC = source /opt/ros/humble/setup.bash && if [ -f /ardu_ws/install/setup.bash ]; then source /ardu_ws/install/setup.bash; export SDF_PATH=$${SDF_PATH:+$$SDF_PATH:}$(AP_SHARE) GZ_SIM_RESOURCE_PATH=$${GZ_SIM_RESOURCE_PATH:+$$GZ_SIM_RESOURCE_PATH:}$(AP_SHARE); fi

EXEC = docker compose exec sim bash -lc

.PHONY: help host-deps setup configure build up down init ready world check-world sim sitl mavproxy shell test clean clean-eeprom

help: ## Affiche cette aide
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

host-deps: ## Installe les prérequis hôte : Docker (+ nvidia-container-toolkit si GPU NVIDIA) — sudo
	@scripts/install-host.sh

setup: ready test ## Installation complète sans lancer la sim (configure, build, init, smoke-test)
	@echo; echo "Installation terminée. Lance la simulation avec : make sim"

.env:
	@scripts/configure.sh

configure: ## Re-détecte la plateforme GPU et met à jour .env (forcer : PLATFORM=intel|nvidia|wsl|cpu)
	@scripts/configure.sh

build: .env ## (Re)construit l'image — à relancer seulement après une modif du Dockerfile
	docker compose build

up: .env ## Démarre le conteneur (construit l'image au 1er lancement, ≈20-40 min)
	@f="$${XAUTHORITY:-$$HOME/.Xauthority}"; [ -e "$$f" ] || touch "$$f"  # sinon docker crée un RÉPERTOIRE root à sa place
	@# Autorise l'uid courant (celui de dev dans le conteneur) sur le serveur X : couvre
	@# les sessions Wayland/GDM où le cookie monté ne suffit pas.
	@if [ -n "$$DISPLAY" ] && command -v xhost >/dev/null; then xhost +SI:localuser:$$(id -un) >/dev/null 2>&1 || true; fi
	@# Build explicite si l'image manque : sinon compose tente d'abord un pull voué à
	@# l'échec (« pull access denied ») qui ressemble à une erreur.
	@docker image inspect keen-kestrel >/dev/null 2>&1 || docker compose build
	docker compose up -d

down: ## Arrête et retire le conteneur sim
	docker compose down

init: up ## Force la (ré)initialisation du workspace ROS 2 (idempotent ; COLCON_JOBS=N pour ajuster)
	docker compose exec -e COLCON_JOBS sim bash -lc 'init-workspace.sh'

# Prérequis de toutes les cibles d'usage : conteneur démarré + workspace initialisé.
# 1er run ≈10-30 min, puis ~1-2 min après une recréation du conteneur, sinon instantané.
ready: up
	@docker compose exec sim test -f $(INIT_MARKER) || $(MAKE) --no-print-directory init

# Chaîne de fichiers de params du SITL : celle du launch iris + config/sim.parm en
# dernier (le dernier gagne). L'eeprom persistant (/ardu_ws/eeprom.bin) garde toutefois
# priorité pour tout param déjà modifié via GCS — voir clean-eeprom.
SITL_SHARE = /ardu_ws/install/ardupilot_sitl/share/ardupilot_sitl/config/default_params
DEFAULTS_BASE = $(SITL_SHARE)/copter.parm,$(AP_SHARE)/ardupilot_gazebo/config/gazebo-iris-gimbal.parm,$(SITL_SHARE)/dds_udp.parm,$(SITL_SHARE)/dds_use_ns.parm
DEFAULTS = $(DEFAULTS_BASE),/ardu_ws/config/sim.parm

# Monde géoréférencé généré par `make world` (worlds/$(WORLD)/site.json). Vide : iris_runway d'origine.
# Son <site>.parm (SIM_OPOS_* = origine du monde) passe avant sim.parm, qui garde le dernier mot.
WORLD ?=
REPERES ?= 1     # 1 : limites et ligne de vol dessinées au sol ; 0 : monde propre (images pour la vision)
ORTHO ?=         # orthophotos, chemins relatifs à worlds/$(WORLD)/, par priorité (ex. private/ortho.tif)
WORLD_ARGS ?=    # options de build_world.py (ex. --wind "3.90 2.25" --sun 2027-05-15T10:00:00-06:00)
WORLD_DEFAULTS = $(DEFAULTS_BASE),/ardu_ws/worlds/$(WORLD)/build/$(WORLD).parm,/ardu_ws/config/sim.parm
WORLD_SRC = export GZ_SIM_RESOURCE_PATH=$$GZ_SIM_RESOURCE_PATH:/ardu_ws/worlds/$(WORLD)/build/models

world: ## Génère un monde géoréférencé : make world WORLD=medicine_hat [ORTHO=private/ortho.tif] (image Python légère, internet requis)
	@test -n "$(WORLD)" || { echo "WORLD manquant, ex. : make world WORLD=medicine_hat"; exit 1; }
	@docker build -q -t keen-kestrel-geotools -f docker/geotools.Dockerfile docker >/dev/null
	docker run --rm --user $(HOST_UID):$(HOST_GID) -v $(CURDIR)/worlds:/worlds keen-kestrel-geotools \
	  python build_world.py $(WORLD) $(foreach o,$(ORTHO),--ortho $(o)) $(WORLD_ARGS)

sim: ready ## Lance SITL + Gazebo + RViz (iris_runway, ou WORLD=medicine_hat [REPERES=0]), params: config/sim.parm
ifeq ($(strip $(WORLD)),)
	$(EXEC) '$(SRC) && ros2 launch ardupilot_gz_bringup iris_runway.launch.py defaults:=$(DEFAULTS)'
else
	@test -f worlds/$(WORLD)/build/world.sdf || $(MAKE) --no-print-directory world
	$(EXEC) '$(SRC) && $(WORLD_SRC) && ros2 launch /ardu_ws/worlds/world.launch.py site:=$(WORLD) world_file:=$(if $(filter 0,$(strip $(REPERES))),world_clean,world) defaults:=$(WORLD_DEFAULTS)'
endif

sitl: ready ## Lance SITL + DDS seul, sans Gazebo
	$(EXEC) '$(SRC) && ros2 launch ardupilot_sitl sitl_dds_udp.launch.py transport:=udp4 synthetic_clock:=True wipe:=False model:=quad speedup:=1 slave:=0 instance:=0 defaults:=$$(ros2 pkg prefix ardupilot_sitl)/share/ardupilot_sitl/config/default_params/copter.parm,$$(ros2 pkg prefix ardupilot_sitl)/share/ardupilot_sitl/config/default_params/dds_udp.parm,/ardu_ws/config/sim.parm sim_address:=127.0.0.1 master:=tcp:127.0.0.1:5760 sitl:=127.0.0.1:5501'

check-world: up ## Vérifie le monde WORLD pendant que `make sim WORLD=...` tourne : GPS SITL = Gazebo, vol, atterrissage sur le relief
	$(EXEC) '$(SRC) && python3 /ardu_ws/worlds/check_world.py $(WORLD)'

mavproxy: up ## Ouvre une console MAVProxy (console + carte) sur le SITL local
	$(EXEC) '$(SRC) && mavproxy.py --console --map --master=:14550'

shell: ready ## Ouvre un shell interactif dans le conteneur, ROS + workspace sourcés
	docker compose exec sim bash -c '$(SRC); exec bash'

test: ready ## Smoke-tests : OpenGL, Gazebo headless, DDS-Gen, MAVProxy
	$(EXEC) '$(SRC) && glxinfo -B && gz sim -v4 -s -r --iterations 100 shapes.sdf && microxrceddsgen -help > /dev/null && echo OK && mavproxy.py --version'

clean-eeprom: up ## Supprime l'eeprom SITL : les params reviennent aux defaults + sim.parm
	$(EXEC) 'rm -f /ardu_ws/eeprom.bin' && echo "eeprom supprimé — prochain boot sur defaults + config/sim.parm"

clean: ## Down + supprime le volume ardu_ws (destructif, confirmation requise)
	docker compose down
	@read -p "Supprimer le volume ardu_ws (perte des workspaces/builds) ? [y/N] " ans; [ "$$ans" = "y" ] || [ "$$ans" = "Y" ] || exit 1
	docker compose down -v
