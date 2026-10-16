#!/usr/bin/env python3
"""
wind_gusts.py — rafales de vent réalistes pour Gazebo Harmonic (gz-sim 8) + ArduPilot SITL (JSON).

*** PROTOTYPE NON TESTÉ AVEC GAZEBO (2026-10-16) ***
Écrit sans pouvoir lancer gz sim (campagne SITL en cours sur le poste). Seul le mode --dry-run
(génération de la série, sans gz) a été exécuté pour vérifier les statistiques. La partie
gz-transport suit l'API lue dans gz-transport13/python/src/__init__.py et WindEffects.cc (gz-sim8) :
à valider au premier lancement (voir « À vérifier » en bas).

Statistiques --dry-run (1800 s, graines 1-3, à 30 m) :
    nominal : moyenne 4,3-4,7 m/s, TI 14-17 %, rafale 3 s max 7,2-7,7 m/s, direction ±30°
    venteux : moyenne 7,5-7,8 m/s, TI 16-18 %, rafale 3 s max 12,4-13,4 m/s, direction −44/+69°
    extreme : moyenne 11,3-11,6 m/s, TI 18-19 %, rafale 3 s max 19,4-21,1 m/s, σ_w ≈ 0,9 m/s

Ce que fait le script
---------------------
Publie gz.msgs.Wind sur /world/<monde>/wind à ~20 Hz (temps SIMULÉ, lu sur /world/<monde>/clock) :
    vent(t) = vent moyen du scénario (+ bascule frontale)
            + turbulence de Dryden basse altitude (MIL-F-8785C), u longitudinal, v latéral, w vertical
            + rafales discrètes « 1-cos » (arrivées de Poisson), qui donnent les pointes du scénario.

Scénarios (rapport site_medicine_hat_research.md §5, vent à ~30 m) :
    nominal  : 4,5 m/s du 240°, pointes ~7,5 m/s
    venteux  : 7,5 m/s du 250°, pointes ~13 m/s (25 kt), direction ±30°
    extreme  : 11,5 m/s du 270° puis bascule vers 340° (front), pointes 18-20 m/s

Prérequis côté monde (sinon le message est perdu en silence ou re-filtré) :
  1. Système WindEffects chargé DANS le <world> (personne d'autre ne s'abonne à /wind), en mode
     « passe-plat » : time_for_rise = max_step_size (k = dt/τ = 1), sans <sin> ni <noise> :

     <plugin filename="gz-sim-wind-effects-system" name="gz::sim::systems::WindEffects">
       <!-- facteur s (1/s) : F = m·s·(v_vent − v_lien). Forme <k>+<rz> pour une constante exacte :
            la forme scalaire <force_approximation_scaling_factor>c</...> donne c² (piège gz-math). -->
       <force_approximation_scaling_factor><k>0.1</k><rz>0 0 0 1</rz></force_approximation_scaling_factor>
       <horizontal>
         <magnitude><time_for_rise>0.001</time_for_rise></magnitude>
         <direction><time_for_rise>0.001</time_for_rise></direction>
       </horizontal>
       <vertical><time_for_rise>0.001</time_for_rise></vertical>
     </plugin>

     ATTENTION : time_for_rise doit être ≥ max_step_size (k > 1 → filtre instable).
  2. <enable_wind>true</enable_wind> sur le base_link du drone pour une traînée de cellule ; sans lui,
     le vent n'agit que par LiftDrag sur les pales (gz-sim ≥ 8.1 soustrait le vent).

Usage
-----
    python3 wind_gusts.py --world medicine_hat --scenario venteux
    python3 wind_gusts.py --scenario extreme --front-at 300 --front-duration 90
    python3 wind_gusts.py --scenario nominal --dry-run --duration 600 --csv nominal.csv   # sans Gazebo

Repères : Gazebo ENU (x Est, y Nord). gz.msgs.Wind.linear_velocity = direction VERS laquelle souffle
le vent (ArduPilotPlugin.cc : « Gazebo specifies where wind is going to »). Les scénarios sont en
direction météo D'OÙ il vient : vers = -U·(sin θ, cos θ).
"""
import argparse
import csv
import math
import random
import sys
import threading
import time

FT = 0.3048
KT = 0.514444

# --------------------------------------------------------------------------- scénarios
# U : vent moyen à la hauteur de référence (m/s) ; dir : d'où il vient (°, météo) ;
# gust_peak : pointe visée (m/s) ; gust_rate : rafales discrètes par minute ;
# gust_dur : durée d'une rafale 1-cos (s, min max) ; gust_dir_sd : écart type de la direction d'une rafale (°).
SCENARIOS = {
    "nominal": dict(U=4.5, dir=240.0, gust_peak=7.5, gust_rate=1.0, gust_dur=(4.0, 10.0), gust_dir_sd=10.0),
    "venteux": dict(U=7.5, dir=250.0, gust_peak=13.0, gust_rate=2.0, gust_dur=(3.0, 8.0), gust_dir_sd=20.0),
    "extreme": dict(U=11.5, dir=270.0, gust_peak=19.0, gust_rate=3.0, gust_dur=(3.0, 8.0), gust_dir_sd=20.0,
                    front_dir=340.0, front_U=11.5),
}


# --------------------------------------------------------------------------- Dryden
def dryden_low_alt(h_m, w20_mps):
    """Échelles et intensités MIL-F-8785C basse altitude (h < 1000 ft). Retourne (Lu, Lv, Lw) en m,
    (su, sv, sw) en m/s."""
    h = max(h_m / FT, 10.0)                     # ft (plancher 10 ft)
    k = 0.177 + 0.000823 * h
    Lw = h
    Lu = Lv = h / k ** 1.2
    sw = 0.1 * (w20_mps / KT) * KT              # σ_w = 0,1·W20 (même unité)
    su = sv = sw / k ** 0.4
    return (Lu * FT, Lv * FT, Lw * FT), (su, sv, sw)


class FirstOrder:
    """H(s) = σ·sqrt(2L/πV) / (1 + (L/V)s) — discrétisation exacte (Ornstein-Uhlenbeck) : variance σ²."""
    def __init__(self, rng):
        self.x, self.rng = 0.0, rng

    def step(self, dt, tau, sigma):
        a = math.exp(-dt / tau)
        self.x = a * self.x + sigma * math.sqrt(1.0 - a * a) * self.rng.gauss(0.0, 1.0)
        return self.x


class SecondOrder:
    """H(s) ∝ (1 + √3·τ s)/(1 + τ s)², τ = L/V (forme Dryden v et w, MIL-F-8785C).
    Entrée bruit blanc unitaire (DSP bilatérale 1) → variance de sortie (1+3)/(4τ) = 1/τ ;
    on multiplie donc par σ·sqrt(τ) pour obtenir la variance σ². Euler-Maruyama avec sous-pas."""
    def __init__(self, rng):
        self.x1 = self.x2 = 0.0
        self.rng = rng

    def step(self, dt, tau, sigma):
        b = math.sqrt(3.0) * tau
        n = max(1, int(math.ceil(dt / (0.02 * tau))))
        h = dt / n
        for _ in range(n):
            eta = self.rng.gauss(0.0, 1.0) / math.sqrt(h)      # bruit blanc de DSP 1
            dx2 = (-self.x1 - 2.0 * tau * self.x2 + eta) / (tau * tau)
            self.x1 += h * self.x2
            self.x2 += h * dx2
        return sigma * math.sqrt(tau) * (self.x1 + b * self.x2)


# --------------------------------------------------------------------------- générateur
class GustModel:
    def __init__(self, sc, alt_m, ref_alt_m, alpha, seed, front_at, front_duration, v_floor):
        self.sc = dict(sc)
        self.rng = random.Random(seed)
        self.shear = (max(alt_m, 1.0) / ref_alt_m) ** alpha          # loi de puissance (α ≈ 0,13, ERA5)
        self.alt = alt_m
        self.front_at, self.front_dur = front_at, front_duration
        self.v_floor = v_floor
        self.fu, self.fv, self.fw = FirstOrder(self.rng), SecondOrder(self.rng), SecondOrder(self.rng)
        self.gusts = []                                               # (t0, durée, amplitude, Δdir°)
        self.next_gust = self._draw_next(0.0)

    # vent moyen (U, d'où) à l'instant t, bascule frontale lissée (1-cos) pour le scénario extreme
    def mean(self, t):
        U, d = self.sc["U"], self.sc["dir"]
        if "front_dir" in self.sc and self.front_at is not None and t > self.front_at:
            s = min(1.0, (t - self.front_at) / self.front_dur)
            s = 0.5 * (1.0 - math.cos(math.pi * s))
            dd = ((self.sc["front_dir"] - d + 180.0) % 360.0) - 180.0   # chemin le plus court
            d = d + s * dd
            U = U + s * (self.sc["front_U"] - U)
        return U * self.shear, d % 360.0

    def _draw_next(self, t):
        lam = self.sc["gust_rate"] / 60.0
        return t + self.rng.expovariate(lam) if lam > 0 else float("inf")

    def step(self, t, dt):
        U, d_from = self.mean(t)
        V = max(U, self.v_floor)                                      # Taylor : V = vitesse air ≈ vent moyen
        # W20 = vent moyen à 20 ft, déduit de U (loi de puissance)
        w20 = U * ((20.0 * FT) / max(self.alt, 20.0 * FT)) ** 0.13
        (Lu, Lv, Lw), (su, sv, sw) = dryden_low_alt(self.alt, w20)
        u = self.fu.step(dt, Lu / V, su)
        v = self.fv.step(dt, Lv / V, sv)
        w = self.fw.step(dt, Lw / V, sw)

        # rafales discrètes 1-cos : l'amplitude amène la pointe à gust_peak (la turbulence fournit ~2σ_u)
        lo, hi = self.sc["gust_dur"]
        amp_mean = max(0.0, self.sc["gust_peak"] * self.shear - U - 2.0 * su)
        # les rafales ajoutent en moyenne rate·amp·durée/2 : on le retire du vent moyen pour le conserver
        U_bias = self.sc["gust_rate"] / 60.0 * amp_mean * 0.5 * (lo + hi) / 2.0
        while t >= self.next_gust:
            amp = max(0.0, self.rng.gauss(amp_mean, 0.25 * amp_mean))
            self.gusts.append((self.next_gust, self.rng.uniform(lo, hi), amp,
                               self.rng.gauss(0.0, self.sc["gust_dir_sd"])))
            self.next_gust = self._draw_next(self.next_gust)
        gu = gd = 0.0
        alive = []
        for (t0, dur, amp, ddir) in self.gusts:
            x = (t - t0) / dur
            if x <= 1.0:
                alive.append((t0, dur, amp, ddir))
                if x >= 0.0:
                    shape = 0.5 * (1.0 - math.cos(2.0 * math.pi * x))  # pulse 1-cos complet (monte et retombe)
                    gu += amp * shape
                    gd += ddir * shape
        self.gusts = alive

        # axes : u selon le vent moyen (VERS où il souffle), v à gauche, w vers le haut
        to = math.radians(d_from + 180.0 + gd)                        # azimut « vers », depuis le nord
        ex, ny = math.sin(to), math.cos(to)                           # vecteur unitaire ENU
        lx, ly = -ny, ex                                              # +90° (gauche)
        speed = U - U_bias + u + gu
        ve = speed * ex + v * lx
        vn = speed * ny + v * ly
        return ve, vn, w, U, d_from


# --------------------------------------------------------------------------- transport
def run_gz(args, model):
    from gz.transport13 import Node, AdvertiseMessageOptions          # python3-gz-transport13
    from gz.msgs10.wind_pb2 import Wind                                # python3-gz-msgs10
    from gz.msgs10.clock_pb2 import Clock

    topic = f"/world/{args.world}/wind"
    node = Node()
    opts = AdvertiseMessageOptions()
    opts.msgs_per_sec = int(args.rate) + 1
    pub = node.advertise(topic, Wind, opts)

    clock = {"t": None}
    lock = threading.Lock()

    def on_clock(msg):
        with lock:
            clock["t"] = msg.sim.sec + 1e-9 * msg.sim.nsec

    # ordre des arguments : (type, topic, callback)
    node.subscribe(Clock, f"/world/{args.world}/clock", on_clock)
    time.sleep(0.5)                                                   # découverte (sinon 1er message perdu)

    writer = None
    if args.csv:
        f = open(args.csv, "w", newline="")
        writer = csv.writer(f)
        writer.writerow(["t_sim", "east", "north", "up", "speed_h", "from_deg", "mean_U", "mean_from_deg"])

    period = 1.0 / args.rate
    last_t = None
    t_start = None
    print(f"[wind_gusts] {args.scenario} → {topic} à {args.rate} Hz (temps simulé). Ctrl-C pour arrêter.")
    try:
        while True:
            time.sleep(period * 0.5)
            with lock:
                t = clock["t"]
            if t is None:
                continue
            if t_start is None:
                t_start = last_t = t
                continue
            dt = t - last_t
            if dt < period:                 # pause ou pas encore le moment : ne rien publier (file LIFO !)
                continue
            last_t = t
            ts = t - t_start
            ve, vn, vu, U, dfrom = model.step(ts, dt)
            msg = Wind()
            msg.enable_wind = True          # OBLIGATOIRE : défaut proto3 = False → coupe WindEffects
            msg.linear_velocity.x = ve
            msg.linear_velocity.y = vn
            msg.linear_velocity.z = vu
            pub.publish(msg)
            if writer:
                sp = math.hypot(ve, vn)
                writer.writerow([f"{ts:.3f}", f"{ve:.3f}", f"{vn:.3f}", f"{vu:.3f}", f"{sp:.3f}",
                                 f"{(math.degrees(math.atan2(-ve, -vn))) % 360:.1f}", f"{U:.2f}", f"{dfrom:.1f}"])
            if args.duration and ts >= args.duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        # laisser un vent moyen propre plutôt qu'une rafale figée
        U, dfrom = model.mean(0.0)
        msg = Wind()
        msg.enable_wind = True
        msg.linear_velocity.x = -U * math.sin(math.radians(dfrom))
        msg.linear_velocity.y = -U * math.cos(math.radians(dfrom))
        pub.publish(msg)
        print("[wind_gusts] arrêt : vent moyen republié.")


def run_dry(args, model):
    dt = 1.0 / args.rate
    n = int(args.duration * args.rate)
    rows, sp = [], []
    for k in range(n):
        t = k * dt
        ve, vn, vu, U, dfrom = model.step(t, dt)
        s = math.hypot(ve, vn)
        sp.append(s)
        rows.append((t, ve, vn, vu, s, (math.degrees(math.atan2(-ve, -vn))) % 360, U, dfrom))
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_sim", "east", "north", "up", "speed_h", "from_deg", "mean_U", "mean_from_deg"])
            for r in rows:
                w.writerow([f"{x:.3f}" for x in r])
    m = sum(sp) / len(sp)
    sd = math.sqrt(sum((x - m) ** 2 for x in sp) / len(sp))
    # rafale « 3 s » (moyenne glissante 3 s) max, comme les METAR
    win = max(1, int(3 * args.rate))
    acc = sum(sp[:win]); g3 = acc / win
    for i in range(win, len(sp)):
        acc += sp[i] - sp[i - win]
        g3 = max(g3, acc / win)
    # direction : écart circulaire au vent moyen
    dirs = [((r[5] - r[7] + 180.0) % 360.0) - 180.0 for r in rows]
    print(f"{args.scenario}: {args.duration:.0f} s, vitesse moyenne {m:.2f} m/s, σ {sd:.2f} (TI {sd / m:.0%}), "
          f"max instantané {max(sp):.2f}, rafale 3 s max {g3:.2f} m/s, "
          f"écart de direction au vent moyen min/max {min(dirs):+.0f}/{max(dirs):+.0f}°, σ_w {math.sqrt(sum(r[3]**2 for r in rows)/n):.2f} m/s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", default="medicine_hat")
    ap.add_argument("--scenario", choices=SCENARIOS, default="nominal")
    ap.add_argument("--rate", type=float, default=20.0, help="Hz, temps simulé (≤ 1 message par pas physique)")
    ap.add_argument("--alt", type=float, default=30.0, help="hauteur AGL où le vent est évalué (m) : le vent "
                    "Gazebo est uniforme dans l'espace, on choisit donc la hauteur de travail du drone")
    ap.add_argument("--ref-alt", type=float, default=30.0, help="hauteur où les scénarios sont définis (m)")
    ap.add_argument("--alpha", type=float, default=0.13, help="exposant de cisaillement (ERA5 : 0,13)")
    ap.add_argument("--front-at", type=float, default=300.0, help="extreme : début de la bascule (s)")
    ap.add_argument("--front-duration", type=float, default=90.0, help="extreme : durée de la bascule (s)")
    ap.add_argument("--v-floor", type=float, default=2.0, help="plancher de V dans L/V (m/s)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--duration", type=float, default=0.0, help="s de temps simulé (0 = sans fin)")
    ap.add_argument("--csv", default=None, help="journal de la série publiée (vérité terrain du vent)")
    ap.add_argument("--dry-run", action="store_true", help="génère la série sans Gazebo et affiche ses stats")
    a = ap.parse_args()
    seed = a.seed if a.seed is not None else int(time.time())
    model = GustModel(SCENARIOS[a.scenario], a.alt, a.ref_alt, a.alpha, seed,
                      a.front_at, a.front_duration, a.v_floor)
    if a.dry_run:
        if not a.duration:
            a.duration = 600.0
        run_dry(a, model)
    else:
        run_gz(a, model)


if __name__ == "__main__":
    sys.exit(main())

# À vérifier au premier lancement (avec Gazebo) :
#  - `gz topic -l | grep wind` montre /world/medicine_hat/wind ; `gz topic -e -t /world/medicine_hat/wind`
#    voit les messages ; `gz service -s /world/medicine_hat/wind_info --reqtype gz.msgs.Empty
#    --reptype gz.msgs.Wind --timeout 1000 --req ''` renvoie la dernière consigne.
#  - Le topic d'horloge du monde est bien /world/<w>/clock (sinon /clock).
#  - AdvertiseMessageOptions est exporté par gz.transport13 (sinon retirer opts).
#  - Avec un drone en stationnaire à 30 m : inclinaison moyenne cohérente avec la traînée attendue
#    (calibrer <k> de force_approximation_scaling_factor, ~0,1 s⁻¹ pour 1,5 kg).
