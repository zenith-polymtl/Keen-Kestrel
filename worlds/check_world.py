#!/usr/bin/env python3
"""Vérifie qu'un monde généré par build_world.py est cohérent avec le SITL, en vol réel.

À lancer dans le conteneur sim pendant `make sim WORLD=<site>` : `make check-world WORLD=<site>`.

1. Au sol : le GPS du SITL est sur l'origine du monde (lat/lon de site_meta.json).
2. Décollage à 30 m, vol GUIDED vers une cible lointaine : la position Gazebo de l'iris
   (gz model) doit correspondre au GPS du SITL converti en Est/Nord.
3. Atterrissage à la cible : l'iris doit reposer sur le maillage de collision, à la hauteur
   du relief à cet endroit (le visuel et la collision sortent du même DEM).
4. Facteur temps réel de Gazebo.
"""
import argparse
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from pymavlink import mavutil

R_EARTH = 6378137.0


def enu(lat, lon, lat0, lon0):
    """Est/Nord (m) approchés autour de l'origine ; erreur < 1 cm à 1 km ici."""
    e = math.radians(lon - lon0) * R_EARTH * math.cos(math.radians(lat0))
    n = math.radians(lat - lat0) * R_EARTH
    return e, n


def gz_pose(model):
    out = subprocess.run(["gz", "model", "-m", model, "-p"], capture_output=True, text=True, timeout=20).stdout
    m = re.search(r"\[([-\d.e+ ]+)\]\s*\n\s*\[", out)
    xyz = [float(v) for v in m.group(1).split()]
    return xyz


def ground_z(obj_path, x, y):
    """Hauteur du maillage de collision (grille régulière) en x, y, interpolée bilinéairement."""
    v = np.array([l.split()[1:4] for l in open(obj_path) if l.startswith("v ")], float)
    xs, ys = np.unique(v[:, 0]), np.unique(v[:, 1])
    z = v[:, 2].reshape(len(ys), len(xs))
    i = np.searchsorted(xs, x) - 1
    j = np.searchsorted(ys, y) - 1
    tx = (x - xs[i]) / (xs[i + 1] - xs[i])
    ty = (y - ys[j]) / (ys[j + 1] - ys[j])
    return float((1 - tx) * (1 - ty) * z[j, i] + tx * (1 - ty) * z[j, i + 1] +
                 (1 - tx) * ty * z[j + 1, i] + tx * ty * z[j + 1, i + 1])


def rtf(world):
    out = subprocess.run(["gz", "topic", "-e", "-n", "1", "-t", f"/world/{world}/stats"],
                         capture_output=True, text=True, timeout=20).stdout
    m = re.search(r"real_time_factor:\s*([\d.e+-]+)", out)
    return float(m.group(1)) if m else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site")
    ap.add_argument("--url", default="udpin:0.0.0.0:14551", help="MAVLink (2e sortie de MAVProxy par défaut)")
    ap.add_argument("--target", default="-400 700", help="cible 'Est Nord' en m (défaut : nord-ouest, relief +4 m)")
    ap.add_argument("--model", default="iris")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent / args.site / "build"
    meta = json.loads((here / "site_meta.json").read_text())
    lat0, lon0, z0 = meta["origin_lat"], meta["origin_lon"], meta["origin_alt_amsl_cgvd2013"]
    obj = here / "models" / f"{args.site}_terrain" / "meshes" / "collision.obj"
    ok = True

    def check(name, err, tol, unit="m"):
        nonlocal ok
        good = abs(err) <= tol
        ok &= good
        print(f"  [{'OK' if good else 'ÉCHEC'}] {name} : {err:+.2f} {unit} (tolérance ±{tol})")

    m = mavutil.mavlink_connection(args.url)
    m.wait_heartbeat(timeout=60)
    print(f"MAVLink : système {m.target_system}")
    m.mav.request_data_stream_send(m.target_system, m.target_component, mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1)

    def pos():
        msg = m.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=10)
        return msg.lat / 1e7, msg.lon / 1e7, msg.alt / 1e3, msg.relative_alt / 1e3

    print("1. Au sol, à l'origine")
    deadline = time.time() + 180
    while time.time() < deadline:      # attendre que l'EKF ait une position GPS
        msg = m.recv_match(type="GPS_RAW_INT", blocking=True, timeout=5)
        if msg and msg.fix_type >= 3:
            break
    time.sleep(15)
    lat, lon, alt, _ = pos()
    e, n = enu(lat, lon, lat0, lon0)
    gx, gy, gzz = gz_pose(args.model)
    check("GPS SITL - origine (horizontal)", math.hypot(e - gx, n - gy), 0.5)
    check("altitude GPS - (sol + hauteur Gazebo de l'iris)", alt - (z0 + gzz), 1.0)

    print("2. Décollage à 30 m et vol vers la cible")
    m.set_mode("GUIDED")
    m.arducopter_arm()
    m.motors_armed_wait()
    m.mav.command_long_send(m.target_system, m.target_component, mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                            0, 0, 0, 0, 0, 0, 0, 30)
    while pos()[3] < 28:
        pass
    te, tn = (float(v) for v in args.target.split())
    tlat = lat0 + math.degrees(tn / R_EARTH)
    tlon = lon0 + math.degrees(te / (R_EARTH * math.cos(math.radians(lat0))))
    m.mav.set_position_target_global_int_send(0, m.target_system, m.target_component,
                                              mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                                              0b0000111111111000, int(tlat * 1e7), int(tlon * 1e7), 30,
                                              0, 0, 0, 0, 0, 0, 0, 0)
    t0 = time.time()
    while True:
        lat, lon, alt, rel = pos()
        e, n = enu(lat, lon, lat0, lon0)
        if math.hypot(e - te, n - tn) < 1.5 or time.time() - t0 > 300:
            break
    time.sleep(5)
    lat, lon, alt, rel = pos()
    e, n = enu(lat, lon, lat0, lon0)
    gx, gy, gzz = gz_pose(args.model)
    print(f"  GPS -> ({e:.1f}, {n:.1f}) ; Gazebo ({gx:.1f}, {gy:.1f}, {gzz:.1f})")
    check("GPS - Gazebo en vol (horizontal)", math.hypot(e - gx, n - gy), 1.0)
    check("altitude GPS - (origine + z Gazebo)", alt - (z0 + gzz), 1.0)
    print(f"  facteur temps réel en vol : {rtf(args.site):.2f}")

    print("3. Atterrissage à la cible")
    m.set_mode("LAND")
    m.motors_disarmed_wait()
    time.sleep(3)
    gx, gy, gzz = gz_pose(args.model)
    g = ground_z(obj, gx, gy)
    print(f"  iris posé à z = {gzz:.2f} m ; relief sous lui = {g:.2f} m (soit {z0 + g:.2f} m AMSL)")
    check("iris posé - relief (origine du modèle iris ≈ 0,19 m au-dessus des patins)", gzz - g - 0.19, 0.15)
    print("RÉSULTAT :", "tout est cohérent" if ok else "écarts à corriger")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
