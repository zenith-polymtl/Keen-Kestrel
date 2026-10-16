#!/usr/bin/env python3
"""Génère un monde Gazebo Harmonic géoréférencé à partir de worlds/<site>/site.json.

Lancé par `make world` dans l'image geotools (docker/geotools.Dockerfile), pas dans le
conteneur sim : rasterio, pyproj et scipy n'y sont pas.

Étapes :
  1. relief : HRDEM 1 m (NRCan) là où il existe, ailleurs MRDEM-30 corrigé par les courbes
     de niveau 1 m de la Ville de Medicine Hat ; rééchantillonné à 1 m dans un repère local
     (Transverse Mercator centré sur l'origine, axes Est/Nord = ENU de Gazebo) ;
  2. texture : mosaïque des orthophotos passées en --ortho (par priorité), complétée par
     Sentinel-2 (10 m, libre) ;
  3. terrain : tuiles OBJ texturées de 256 m (pas de 2 m dans la zone de mission, 8 m
     autour) + un maillage de collision unique à 8 m ;
  4. repères (limites hard/soft, ligne de vol, décollage) et éoliennes ;
  5. world.sdf, world_clean.sdf (sans repères) et <site>.parm (SIM_OPOS_*).

L'origine du monde (0, 0, 0) est le home du SITL : le backend JSON d'ArduPilot calcule
lat/lon = home + position Gazebo, il ne lit jamais <spherical_coordinates>. Les deux doivent
donc être identiques, et le sol vaut z = 0 à l'origine.

Les sorties vont dans worlds/<site>/build/ et les téléchargements dans worlds/<site>/cache/,
tous deux ignorés par git : les orthophotos peuvent être sous licence restrictive.
"""
import argparse
import json
import math
import struct
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds
from scipy import ndimage
from scipy.interpolate import griddata
from scipy.spatial import cKDTree

NRCAN = "/vsicurl/https://canelevation-dem.s3.ca-central-1.amazonaws.com/"
HRDEM = NRCAN + "hrdem-mosaic-1m/3_3-mosaic-1m-dtm.tif"
MRDEM = NRCAN + "mrdem-30/mrdem-30-dtm.tif"
CONTOURS = ("https://services3.arcgis.com/2gQ2Dv4Fb016qFVU/arcgis/rest/services/"
            "1m_Elevation_Contours/FeatureServer/0/query")
STAC = "https://earth-search.aws.element84.com/v1/search"

TILE_M = 256          # côté d'une tuile visuelle
STEP_MISSION = 2      # pas du maillage visuel dans la zone de mission (m)
STEP_OUTER = 8        # pas du maillage visuel autour, et du maillage de collision (m)
GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="2")


def f32(x):
    """Valeur exactement représentable par un float32 (SIM_OPOS_* sont des float32)."""
    return struct.unpack("f", struct.pack("f", x))[0]


class Site:
    def __init__(self, site_dir):
        self.dir = Path(site_dir).resolve()
        self.cfg = json.loads((self.dir / "site.json").read_text())
        self.name = self.cfg["name"]
        self.lat0 = f32(self.cfg["origin"]["lat"])
        self.lon0 = f32(self.cfg["origin"]["lon"])
        self.crs = CRS.from_proj4(
            f"+proj=tmerc +lat_0={self.lat0!r} +lon_0={self.lon0!r} +k=1 +x_0=0 +y_0=0 "
            "+ellps=WGS84 +units=m +no_defs")
        self._to_local = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
        self._to_wgs = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        e = self.cfg["extent_m"]
        self.w, self.e, self.s, self.n = e["west"], e["east"], e["south"], e["north"]
        for v in (self.w, self.e, self.s, self.n):
            assert v % TILE_M == 0, "extent_m doit être un multiple de 256 m"
        self.cache = self.dir / "cache"
        self.build = self.dir / "build"
        self.cache.mkdir(exist_ok=True)
        self.build.mkdir(exist_ok=True)

    def local(self, latlon):
        """[(lat, lon)] -> array (N, 2) Est/Nord en mètres."""
        a = np.asarray(latlon, float)
        x, y = self._to_local.transform(a[:, 1], a[:, 0])
        return np.c_[x, y]

    def bbox_wgs(self, margin):
        xs = [self.w - margin, self.e + margin]
        ys = [self.s - margin, self.n + margin]
        lon, lat = self._to_wgs.transform([xs[0], xs[1], xs[0], xs[1]], [ys[0], ys[0], ys[1], ys[1]])
        return min(lon), min(lat), max(lon), max(lat)

    def grid(self, res):
        """Grille dont les CENTRES de pixels tombent sur les multiples de res (sommets de maillage)."""
        nx = int((self.e - self.w) / res) + 1
        ny = int((self.n - self.s) / res) + 1
        return from_origin(self.w - res / 2, self.n + res / 2, res, res), nx, ny


# ---------------------------------------------------------------------------- relief

def fetch_window(url, site, path, margin=300, bands=1):
    """Extrait l'emprise (+ marge) d'un COG distant dans sa projection d'origine, une seule fois.

    Reprojeter directement depuis /vsicurl relit les blocs distants plusieurs fois : bien plus lent."""
    if path.exists():
        return path
    with rasterio.Env(**GDAL_ENV), rasterio.open(url) as src:
        b = transform_bounds("EPSG:4326", src.crs, *site.bbox_wgs(margin), densify_pts=21)
        w = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
        w = w.intersection(Window(0, 0, src.width, src.height))
        a = src.read(list(range(1, bands + 1)), window=w)
        prof = dict(driver="GTiff", width=a.shape[2], height=a.shape[1], count=bands, dtype=a.dtype, crs=src.crs,
                    transform=src.window_transform(w), nodata=src.nodata, compress="deflate", tiled=True)
    with rasterio.open(path, "w", **prof) as o:
        o.write(a)
    return path


def warp(src_path, site, res, resampling):
    tr, nx, ny = site.grid(res)
    with rasterio.Env(**GDAL_ENV), rasterio.open(src_path) as src, \
            WarpedVRT(src, crs=site.crs, transform=tr, width=nx, height=ny, resampling=resampling,
                      src_nodata=src.nodata, nodata=-32767) as vrt:
        a = vrt.read(1).astype("float64")
    a[a < -1000] = np.nan
    return a


def fetch_contours(site):
    path = site.cache / "contours_cmh_1m.geojson"
    if not path.exists():
        xmin, ymin, xmax, ymax = site.bbox_wgs(150)
        feats, offset = [], 0
        while True:
            q = urllib.parse.urlencode(dict(
                where="1=1", geometry=f"{xmin},{ymin},{xmax},{ymax}", geometryType="esriGeometryEnvelope",
                inSR=4326, outSR=4326, spatialRel="esriSpatialRelIntersects", outFields="ELEVATION",
                returnGeometry="true", f="geojson", resultOffset=offset, resultRecordCount=1000,
                # Généralisées à ~1 m par le serveur : brutes, elles pèsent ~150 Mo (sommets aux 25 cm).
                maxAllowableOffset=0.00001, geometryPrecision=7))
            d = json.load(urllib.request.urlopen(f"{CONTOURS}?{q}", timeout=300))
            feats += d["features"]
            offset += len(d["features"])
            if not d.get("properties", {}).get("exceededTransferLimit") and not d.get("exceededTransferLimit"):
                break
        path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
        print(f"  courbes de niveau : {len(feats)} entités téléchargées")
    return json.loads(path.read_text())["features"]


def contour_points(site, feats, spacing=5.0):
    """Points densifiés (Est, Nord) et altitude des courbes, en repère local."""
    pts, z = [], []
    for f in feats:
        g = f["geometry"]
        lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        for line in lines:
            a = site.local([(lat, lon) for lon, lat, *_ in line])
            for p, q in zip(a[:-1], a[1:]):
                k = max(1, int(np.hypot(*(q - p)) / spacing))
                t = np.arange(k)[:, None] / k
                pts.append(p + t * (q - p))
                z.append(np.full(k, f["properties"]["ELEVATION"], float))
    pts, z = np.vstack(pts), np.concatenate(z)
    inside = (pts[:, 0] >= site.w) & (pts[:, 0] <= site.e) & (pts[:, 1] >= site.s) & (pts[:, 1] <= site.n)
    return pts[inside], z[inside]


def sample(grid_arr, site, res, x, y, order=1):
    """Interpolation dans une grille de site.grid(res) aux coordonnées locales x, y."""
    cols = (np.asarray(x) - site.w) / res
    rows = (site.n - np.asarray(y)) / res
    return ndimage.map_coordinates(grid_arr, [np.atleast_1d(rows), np.atleast_1d(cols)], order=order, mode="nearest")


def build_dem(site, refresh):
    """DEM fusionné à 1 m (CGVD2013, ≈ EGM2008 à ±2 cm ici), mis en cache en GeoTIFF local."""
    path = site.cache / "dem_fusion_1m.tif"
    if path.exists() and not refresh:
        with rasterio.open(path) as ds:
            return ds.read(1).astype("float64")
    print("  HRDEM 1 m et MRDEM-30 (NRCan)...")
    hr = warp(fetch_window(HRDEM, site, site.cache / "hrdem_1m_dtm.tif"), site, 1, Resampling.bilinear)
    mr = warp(fetch_window(MRDEM, site, site.cache / "mrdem30_dtm.tif"), site, 1, Resampling.cubic)
    assert not np.isnan(mr).any(), "MRDEM incomplet sur l'emprise"
    pts, z = contour_points(site, fetch_contours(site))
    res = z - sample(mr, site, 1, pts[:, 0], pts[:, 1])
    print(f"  courbes - MRDEM : {res.mean():+.2f} ± {res.std():.2f} m sur {len(z)} points")

    # Résidu courbes - MRDEM interpolé sur une grille de 4 m, pondéré par la proximité des courbes.
    cs = 4
    gx, gy = np.meshgrid(np.arange(site.w, site.e + 1, cs), np.arange(site.n, site.s - 1, -cs))
    r = griddata(pts, res, (gx, gy), method="linear")
    r = np.where(np.isnan(r), griddata(pts, res, (gx, gy), method="nearest"), r)
    r = ndimage.gaussian_filter(r, 1.0)
    d, _ = cKDTree(pts).query(np.c_[gx.ravel(), gy.ravel()])
    wc = np.clip(1 - (d.reshape(gx.shape) - 100) / 100, 0, 1)
    _, nx, ny = site.grid(1)
    yy, xx = np.mgrid[0:ny, 0:nx]
    up = lambda a: ndimage.map_coordinates(a, [yy / cs, xx / cs], order=1, mode="nearest")
    corr = mr + up(wc) * up(r)

    # HRDEM là où il existe, fondu sur 30 m vers la correction.
    hv = ~np.isnan(hr)
    wh = np.clip(ndimage.distance_transform_edt(hv) / 30, 0, 1)
    fused = np.where(hv, wh * np.nan_to_num(hr) + (1 - wh) * corr, corr)
    hd = sample(fused, site, 1, pts[:, 0], pts[:, 1]) - z
    print(f"  HRDEM : {100 * hv.mean():.1f} % de l'emprise ; fusion - courbes : {hd.mean():+.2f} ± {hd.std():.2f} m")

    tr, nx, ny = site.grid(1)
    with rasterio.open(path, "w", driver="GTiff", width=nx, height=ny, count=1, dtype="float32", crs=site.crs,
                       transform=tr, compress="deflate", tiled=True, blockxsize=256, blockysize=256) as o:
        o.write(fused.astype("float32"), 1)
        o.update_tags(VERTICAL_DATUM="CGVD2013", SOURCES="NRCan HRDEM 1m (AB-TideLake 2022) + MRDEM-30 "
                      "corrigé par les courbes 1 m de la City of Medicine Hat")
    return fused


# ---------------------------------------------------------------------------- texture

def fetch_sentinel2(site, refresh):
    """Scène Sentinel-2 L2A sans nuage la plus proche du 15 mai (2023-2026), à 10 m en repère local."""
    meta = site.cache / "sentinel2.json"
    path = site.cache / "sentinel2_10m.tif"
    if path.exists() and meta.exists() and not refresh:
        return path, json.loads(meta.read_text())
    xmin, ymin, xmax, ymax = site.bbox_wgs(0)
    cands = []
    for year in (2023, 2024, 2025, 2026):
        q = {"collections": ["sentinel-2-c1-l2a"], "bbox": [xmin, ymin, xmax, ymax],
             "datetime": f"{year}-04-25T00:00:00Z/{year}-06-10T23:59:59Z", "limit": 100,
             "query": {"eo:cloud_cover": {"lt": 5}}}
        req = urllib.request.Request(STAC, data=json.dumps(q).encode(), headers={"Content-Type": "application/json"})
        cands += json.load(urllib.request.urlopen(req, timeout=120))["features"]
    doy = lambda f: datetime.fromisoformat(f["properties"]["datetime"][:10]).timetuple().tm_yday
    cands.sort(key=lambda f: (abs(doy(f) - 135), f["properties"]["eo:cloud_cover"]))
    tr, nx, ny = site.grid(10)
    for f in cands:
        url = "/vsicurl/" + f["assets"]["visual"]["href"]
        raw = site.cache / "sentinel2_brut.tif"
        raw.unlink(missing_ok=True)
        fetch_window(url, site, raw, bands=3)
        with rasterio.open(raw) as src, WarpedVRT(src, crs=site.crs, transform=tr, width=nx, height=ny,
                                                  resampling=Resampling.bilinear, add_alpha=True) as vrt:
            a = vrt.read()
        raw.unlink()
        if (a[3] > 0).mean() < 0.999 or (a[:3].max(axis=0) == 0).mean() > 0.001:   # bord de tuile : suivante
            continue
        with rasterio.open(path, "w", driver="GTiff", width=nx, height=ny, count=3, dtype="uint8",
                           crs=site.crs, transform=tr, compress="deflate") as o:
            o.write(a[:3])
        m = {"id": f["id"], "datetime": f["properties"]["datetime"], "href": f["assets"]["visual"]["href"]}
        meta.write_text(json.dumps(m, indent=1))
        return path, m
    raise SystemExit("aucune scène Sentinel-2 couvrant l'emprise")


def native_res_m(path, lat):
    with rasterio.open(path) as ds:
        r = abs(ds.res[0])
        if ds.crs.is_geographic:
            return r * 111_320 * math.cos(math.radians(lat))
        if ds.crs.to_epsg() == 3857:
            return r * math.cos(math.radians(lat))
        return r


def ortho_mosaic(site, orthos, s2_path):
    """Mosaïque RGB sur toute l'emprise, bords de pixels alignés sur l'emprise (pas de centrage)."""
    best = min([native_res_m(p, site.lat0) for p in orthos] + [10.0])
    res = 0.25 if best <= 0.5 else 1.0
    nx, ny = int((site.e - site.w) / res), int((site.n - site.s) / res)
    tr = from_origin(site.w, site.n, res, res)
    rgb = np.zeros((3, ny, nx), np.uint8)
    filled = np.zeros((ny, nx), bool)
    used = []
    for p in list(orthos) + [s2_path]:
        resamp = Resampling.lanczos if native_res_m(p, site.lat0) <= res * 2 else Resampling.cubic
        with rasterio.open(p) as src, WarpedVRT(src, crs=site.crs, transform=tr, width=nx, height=ny,
                                                resampling=resamp, add_alpha=True) as vrt:
            a = vrt.read([1, 2, 3, vrt.count])
        m = (a[3] > 0) & ~filled
        rgb[:, m] = a[:3, m]
        filled |= m
        used.append((str(p), float(m.mean())))
        if filled.all():
            break
    return rgb.transpose(1, 2, 0), res, used


# ---------------------------------------------------------------------------- maillages

def write_obj(path, xs, ys, z, normals, uv_origin=None, with_uv=False):
    """Grille régulière -> OBJ. xs croissant (Est), ys croissant (Nord), z[j, i] avec j sur ys.

    Les normales sont obligatoires, même pour la collision : sans elles, DART ignore le maillage
    (« normal count [0] that matches its vertex count ») et gz sim plante au premier pas."""
    nxv, nyv = len(xs), len(ys)
    X, Y = np.meshgrid(xs, ys)
    lines = ["# généré par worlds/build_world.py"]
    v = np.c_[X.ravel(), Y.ravel(), z.ravel()]
    lines += ["v %.3f %.3f %.3f" % tuple(r) for r in v]
    if with_uv:
        x0, y0 = uv_origin
        uv = np.c_[(X.ravel() - x0) / TILE_M, (Y.ravel() - y0) / TILE_M]
        lines += ["vt %.6f %.6f" % tuple(r) for r in uv]
    lines += ["vn %.4f %.4f %.4f" % tuple(r) for r in normals.reshape(-1, 3)]
    idx = (np.arange(nyv - 1)[:, None] * nxv + np.arange(nxv - 1)[None, :]).ravel() + 1
    a, b, c, d = idx, idx + 1, idx + 1 + nxv, idx + nxv   # sud-ouest, sud-est, nord-est, nord-ouest
    tri = np.r_[np.c_[a, b, c], np.c_[a, c, d]]
    fmt = "f {0}/{0}/{0} {1}/{1}/{1} {2}/{2}/{2}" if with_uv else "f {0}//{0} {1}//{1} {2}//{2}"
    lines += [fmt.format(*t) for t in tri]
    path.write_text("\n".join(lines) + "\n")
    return len(v), len(tri)


def build_terrain(site, dem, z0, mosaic, mres, mission_box):
    mdir = site.build / "models" / f"{site.name}_terrain"
    (mdir / "meshes").mkdir(parents=True, exist_ok=True)
    (mdir / "textures").mkdir(exist_ok=True)
    z = dem - z0
    gy, gx = np.gradient(z)                       # lignes = Nord -> Sud : d/dy = -gy
    nrm = np.dstack([-gx, gy, np.ones_like(z)])
    nrm /= np.linalg.norm(nrm, axis=2, keepdims=True)

    def grid_vals(arr, xs, ys):
        cols = (xs - site.w).astype(int)
        rows = (site.n - ys).astype(int)
        return arr[np.ix_(rows, cols)]

    visuals, nverts, ntris = [], 0, 0
    tex_full = int(TILE_M / mres)
    for ty in range(site.s, site.n, TILE_M):
        for tx in range(site.w, site.e, TILE_M):
            inner = not (tx + TILE_M <= mission_box[0] or tx >= mission_box[2] or
                         ty + TILE_M <= mission_box[1] or ty >= mission_box[3])
            step = STEP_MISSION if inner else STEP_OUTER
            xs = np.arange(tx, tx + TILE_M + 1, step)
            ys = np.arange(ty, ty + TILE_M + 1, step)
            tag = f"{(tx - site.w) // TILE_M:02d}_{(ty - site.s) // TILE_M:02d}"
            nv, nt = write_obj(mdir / "meshes" / f"tuile_{tag}.obj", xs, ys, grid_vals(z, xs, ys),
                               grid_vals(nrm, xs, ys), uv_origin=(tx, ty), with_uv=True)
            nverts, ntris = nverts + nv, ntris + nt
            r0 = int((site.n - ty - TILE_M) / mres)
            c0 = int((tx - site.w) / mres)
            img = Image.fromarray(mosaic[r0:r0 + tex_full, c0:c0 + tex_full])
            px = tex_full if inner else min(tex_full, 256)
            if px != tex_full:
                img = img.resize((px, px), Image.LANCZOS)
            img.save(mdir / "textures" / f"tuile_{tag}.jpg", quality=90)
            visuals.append(f"""      <visual name="tuile_{tag}">
        <cast_shadows>false</cast_shadows>
        <geometry><mesh><uri>model://{site.name}_terrain/meshes/tuile_{tag}.obj</uri></mesh></geometry>
        <material>
          <ambient>1 1 1 1</ambient><diffuse>1 1 1 1</diffuse><specular>0.05 0.05 0.05 1</specular>
          <pbr><metal>
            <albedo_map>model://{site.name}_terrain/textures/tuile_{tag}.jpg</albedo_map>
            <roughness>1.0</roughness><metalness>0.0</metalness>
          </metal></pbr>
        </material>
      </visual>""")

    xs = np.arange(site.w, site.e + 1, STEP_OUTER)
    ys = np.arange(site.s, site.n + 1, STEP_OUTER)
    cv, ct = write_obj(mdir / "meshes" / "collision.obj", xs, ys, grid_vals(z, xs, ys), grid_vals(nrm, xs, ys))
    (mdir / "model.config").write_text(f"""<?xml version="1.0"?>
<model>
  <name>{site.name}_terrain</name>
  <version>1.0</version>
  <sdf version="1.9">model.sdf</sdf>
  <description>Terrain de {site.name} : relief NRCan/City of Medicine Hat, texture orthophoto. Généré par worlds/build_world.py.</description>
</model>
""")
    (mdir / "model.sdf").write_text(f"""<?xml version="1.0"?>
<sdf version="1.9">
  <model name="{site.name}_terrain">
    <static>true</static>
    <link name="sol">
      <collision name="sol">
        <geometry><mesh><uri>model://{site.name}_terrain/meshes/collision.obj</uri></mesh></geometry>
        <surface><friction><ode><mu>1.0</mu><mu2>1.0</mu2></ode></friction></surface>
      </collision>
{chr(10).join(visuals)}
    </link>
  </model>
</sdf>
""")
    return dict(visual_vertices=nverts, visual_triangles=ntris, collision_vertices=cv, collision_triangles=ct,
                tiles=len(visuals), texture_m_per_px_mission=mres, texture_m_per_px_outer=TILE_M / min(tex_full, 256))


def box_visual(name, p, q, width, height, rgba, lift):
    """Boîte mince de p à q (x, y, z), qui suit la pente."""
    d = q - p
    lh = math.hypot(d[0], d[1])
    c = (p + q) / 2
    yaw = math.atan2(d[1], d[0])
    pitch = -math.atan2(d[2], lh)
    col = " ".join(f"{v:.2f}" for v in rgba)
    return f"""      <visual name="{name}">
        <cast_shadows>false</cast_shadows>
        <pose>{c[0]:.3f} {c[1]:.3f} {c[2] + lift:.3f} 0 {pitch:.5f} {yaw:.5f}</pose>
        <geometry><box><size>{math.hypot(lh, d[2]) + width:.3f} {width} {height}</size></box></geometry>
        <material><ambient>{col}</ambient><diffuse>{col}</diffuse><emissive>{col}</emissive></material>
      </visual>"""


def build_markers(site, height_at):
    """Repères visuels seulement (pas de collision) : limites, ligne de vol, point de décollage."""
    out = []
    layers = [("hard", site.cfg["hard_boundary"], 0.6, (1, 0, 0, 1), True),
              ("soft", site.cfg["soft_boundary"], 0.4, (0.1, 0.9, 0.1, 1), True),
              ("ligne_de_vol", site.cfg["flight_line"], 0.3, (1, 0.85, 0, 1), True)]
    for name, poly, width, rgba, closed in layers:
        pts = site.local(poly)
        if closed:
            pts = np.vstack([pts, pts[:1]])
        k = 0
        for a, b in zip(pts[:-1], pts[1:]):
            n = max(1, int(math.ceil(np.hypot(*(b - a)) / 20)))
            for i in range(n):
                p = a + (b - a) * i / n
                q = a + (b - a) * (i + 1) / n
                p3 = np.r_[p, height_at(*p)]
                q3 = np.r_[q, height_at(*q)]
                out.append(box_visual(f"{name}_{k}", p3, q3, width, 0.05, rgba, 0.06))
                k += 1
    t = site.local([site.cfg["takeoff"]])[0]
    out.append(f"""      <visual name="decollage">
        <cast_shadows>false</cast_shadows>
        <pose>{t[0]:.3f} {t[1]:.3f} {height_at(*t) + 0.02:.3f} 0 0 0</pose>
        <geometry><cylinder><radius>1.0</radius><length>0.04</length></cylinder></geometry>
        <material><ambient>0 0.8 1 1</ambient><diffuse>0 0.8 1 1</diffuse><emissive>0 0.5 0.7 1</emissive></material>
      </visual>""")
    mdir = site.build / "models" / f"{site.name}_reperes"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "model.config").write_text(f"""<?xml version="1.0"?>
<model><name>{site.name}_reperes</name><version>1.0</version><sdf version="1.9">model.sdf</sdf>
<description>Limites hard (rouge) et soft (vert), ligne de vol (jaune), décollage (cyan). Visuels seulement.</description></model>
""")
    (mdir / "model.sdf").write_text(f"""<?xml version="1.0"?>
<sdf version="1.9">
  <model name="{site.name}_reperes">
    <static>true</static>
    <link name="reperes">
{chr(10).join(out)}
    </link>
  </model>
</sdf>
""")


def turbine_sdf(t, xy, z, wind_from_deg):
    """Éolienne simplifiée (tour, nacelle, rotor arrêté face au vent dominant). Collision : tour seule."""
    hub, rr = t["hub_m"], t["rotor_d_m"] / 2
    psi = math.radians(90 - (wind_from_deg + 180))      # axe nacelle -> rotor, pointé vers le vent (ENU)
    hx, hy = xy[0] + 4 * math.cos(psi), xy[1] + 4 * math.sin(psi)
    blades = []
    for k in range(3):
        th = math.radians(10 + 120 * k)
        # Rz(psi)·Rx(th)·(0, 0, rr/2) : centre de la pale dans le plan du rotor
        oy, oz = -math.sin(th) * rr / 2, math.cos(th) * rr / 2
        bx, by = hx - math.sin(psi) * oy, hy + math.cos(psi) * oy
        blades.append(f"""        <visual name="pale_{k}">
          <pose>{bx:.2f} {by:.2f} {z + hub + oz:.2f} {th:.4f} 0 {psi:.4f}</pose>
          <geometry><box><size>0.6 2.2 {rr - 1.5:.1f}</size></box></geometry>
          <material><ambient>0.92 0.92 0.92 1</ambient><diffuse>0.92 0.92 0.92 1</diffuse></material>
        </visual>""")
    return f"""    <model name="eolienne_{t['name']}">
      <static>true</static>
      <link name="structure">
        <collision name="tour">
          <pose>{xy[0]:.2f} {xy[1]:.2f} {z + hub / 2:.2f} 0 0 0</pose>
          <geometry><cylinder><radius>2.1</radius><length>{hub:.1f}</length></cylinder></geometry>
        </collision>
        <visual name="tour">
          <pose>{xy[0]:.2f} {xy[1]:.2f} {z + hub / 2:.2f} 0 0 0</pose>
          <geometry><cylinder><radius>2.1</radius><length>{hub:.1f}</length></cylinder></geometry>
          <material><ambient>0.92 0.92 0.92 1</ambient><diffuse>0.92 0.92 0.92 1</diffuse></material>
        </visual>
        <visual name="nacelle">
          <pose>{xy[0]:.2f} {xy[1]:.2f} {z + hub:.2f} 0 0 {psi:.4f}</pose>
          <geometry><box><size>10 3.5 3.5</size></box></geometry>
          <material><ambient>0.92 0.92 0.92 1</ambient><diffuse>0.92 0.92 0.92 1</diffuse></material>
        </visual>
        <visual name="moyeu">
          <pose>{hx:.2f} {hy:.2f} {z + hub:.2f} 0 0 0</pose>
          <geometry><sphere><radius>1.8</radius></sphere></geometry>
          <material><ambient>0.92 0.92 0.92 1</ambient><diffuse>0.92 0.92 0.92 1</diffuse></material>
        </visual>
{chr(10).join(blades)}
      </link>
    </model>"""


# ---------------------------------------------------------------------------- soleil, monde

def sun_position(lat, lon, when):
    """Azimut (depuis le nord, horaire) et élévation du soleil, en degrés (algorithme NOAA simplifié)."""
    d = (when - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ha = math.radians((18.697374558 + 24.06570982441908 * d) % 24 * 15 + lon) - ra
    la = math.radians(lat)
    el = math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))
    az = math.atan2(-math.sin(ha), math.tan(dec) * math.cos(la) - math.sin(la) * math.cos(ha))
    return math.degrees(az) % 360, math.degrees(el)


def world_sdf(site, z0, sun, wind, turbines, with_markers):
    az, el = sun
    ce = math.cos(math.radians(el))
    direction = (-ce * math.sin(math.radians(az)), -ce * math.cos(math.radians(az)), -math.sin(math.radians(el)))
    markers = f"""
    <include>
      <uri>model://{site.name}_reperes</uri>
    </include>
""" if with_markers else ""
    return f"""<?xml version="1.0" ?>
<!-- Généré par worlds/build_world.py depuis worlds/{site.name}/site.json : ne pas éditer à la main.
     Origine (0, 0, 0) = home du SITL = {site.lat0!r}, {site.lon0!r}, {z0:.2f} m AMSL (CGVD2013).
     Axes : x = Est, y = Nord, z = haut (ENU). -->
<sdf version="1.9">
  <world name="{site.name}">
    <physics name="1ms" type="ignore">
      <max_step_size>0.001</max_step_size>
      <real_time_factor>1.0</real_time_factor>
    </physics>
    <plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
    <plugin filename="gz-sim-sensors-system" name="gz::sim::systems::Sensors">
      <render_engine>ogre2</render_engine>
    </plugin>
    <plugin filename="gz-sim-user-commands-system" name="gz::sim::systems::UserCommands"/>
    <plugin filename="gz-sim-scene-broadcaster-system" name="gz::sim::systems::SceneBroadcaster"/>
    <plugin filename="gz-sim-air-pressure-system" name="gz::sim::systems::AirPressure"/>
    <plugin filename="gz-sim-air-speed-system" name="gz::sim::systems::AirSpeed"/>
    <plugin filename="gz-sim-altimeter-system" name="gz::sim::systems::Altimeter"/>
    <plugin filename="gz-sim-imu-system" name="gz::sim::systems::Imu"/>
    <plugin filename="gz-sim-magnetometer-system" name="gz::sim::systems::Magnetometer"/>
    <plugin filename="gz-sim-navsat-system" name="gz::sim::systems::NavSat"/>

    <spherical_coordinates>
      <surface_model>EARTH_WGS84</surface_model>
      <world_frame_orientation>ENU</world_frame_orientation>
      <latitude_deg>{site.lat0!r}</latitude_deg>
      <longitude_deg>{site.lon0!r}</longitude_deg>
      <elevation>{z0:.2f}</elevation>
      <heading_deg>0</heading_deg>
    </spherical_coordinates>

    <!-- Vent moyen (m/s, x = Est, y = Nord). Le SITL JSON ignore SIM_WIND_* : le vent se règle ici,
         ou en cours de sim sur /world/{site.name}/wind (gz.msgs.Wind). -->
    <wind>
      <linear_velocity>{wind[0]:.2f} {wind[1]:.2f} 0</linear_velocity>
    </wind>

    <scene>
      <ambient>0.45 0.45 0.45 1</ambient>
      <background>0.62 0.74 0.88 1</background>
      <grid>false</grid>
      <sky/>
      <shadows>true</shadows>
    </scene>

    <!-- Soleil : azimut {az:.1f}°, élévation {el:.1f}°. -->
    <light type="directional" name="soleil">
      <cast_shadows>true</cast_shadows>
      <pose>0 0 200 0 0 0</pose>
      <diffuse>1.0 0.97 0.92 1</diffuse>
      <specular>0.3 0.3 0.3 1</specular>
      <direction>{direction[0]:.4f} {direction[1]:.4f} {direction[2]:.4f}</direction>
    </light>

    <include>
      <uri>model://{site.name}_terrain</uri>
    </include>
{markers}
{chr(10).join(turbines)}
  </world>
</sdf>
"""


def preview(site, mosaic, mres, path):
    """Aperçu 1600 px de la texture avec les limites, pour vérifier le calage."""
    img = Image.fromarray(mosaic)
    scale = 1600 / img.width
    img = img.resize((1600, int(img.height * scale)), Image.LANCZOS)
    dr = ImageDraw.Draw(img)
    to_px = lambda p: ((p[0] - site.w) / mres * scale, (site.n - p[1]) / mres * scale)
    for key, col in (("hard_boundary", (255, 0, 0)), ("soft_boundary", (0, 230, 0)), ("flight_line", (255, 220, 0))):
        pts = [to_px(p) for p in site.local(site.cfg[key])]
        dr.line(pts + pts[:1], fill=col, width=3)
    o = to_px((0, 0))
    dr.ellipse([o[0] - 6, o[1] - 6, o[0] + 6, o[1] + 6], outline=(0, 220, 255), width=3)
    img.save(path, quality=88)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site", help="dossier du site (contient site.json)")
    ap.add_argument("--ortho", action="append", default=[],
                    help="GeoTIFF RGB géoréférencé, par priorité décroissante (répétable). Sentinel-2 complète.")
    ap.add_argument("--sun", default="2027-05-15T15:00:00-06:00", help="date et heure locales pour le soleil (ISO 8601)")
    ap.add_argument("--wind", default="0 0", help="vent moyen 'Est Nord' en m/s (ex. '3.90 2.25' : 4,5 m/s du 240°)")
    ap.add_argument("--refresh", action="store_true", help="retélécharger le relief et Sentinel-2")
    args = ap.parse_args()

    site = Site(args.site)
    print(f"[{site.name}] origine {site.lat0!r}, {site.lon0!r}")
    print("1/5 relief")
    dem = build_dem(site, args.refresh)
    z0 = float(sample(dem, site, 1, 0.0, 0.0)[0])
    print(f"  sol à l'origine : {z0:.2f} m AMSL ; emprise {np.nanmin(dem):.1f} à {np.nanmax(dem):.1f} m")

    print("2/5 texture")
    s2_path, s2 = fetch_sentinel2(site, args.refresh)
    orthos = [site.dir / p if not Path(p).is_absolute() else Path(p) for p in args.ortho]
    mosaic, mres, used = ortho_mosaic(site, orthos, s2_path)
    for p, frac in used:
        print(f"  {Path(p).name} : {100 * frac:.1f} % de l'emprise")

    print("3/5 terrain")
    m = site.cfg.get("mission_zone_margin_m", 100)
    poly = site.local(site.cfg["hard_boundary"])
    mission_box = (poly[:, 0].min() - m, poly[:, 1].min() - m, poly[:, 0].max() + m, poly[:, 1].max() + m)
    stats = build_terrain(site, dem, z0, mosaic, mres, mission_box)
    print("  " + ", ".join(f"{k} {v}" for k, v in stats.items()))

    print("4/5 repères et éoliennes")
    height_at = lambda x, y: float(sample(dem, site, 1, x, y)[0]) - z0
    build_markers(site, height_at)
    turbines = []
    for t in site.cfg.get("turbines", []):
        xy = site.local([(t["lat"], t["lon"])])[0]
        if site.w <= xy[0] <= site.e and site.s <= xy[1] <= site.n:
            turbines.append(turbine_sdf(t, xy, height_at(*xy), wind_from_deg=250))

    print("5/5 monde")
    when = datetime.fromisoformat(args.sun)
    sun = sun_position(site.lat0, site.lon0, when)
    wind = [float(v) for v in args.wind.split()]
    for fname, with_markers in (("world.sdf", True), ("world_clean.sdf", False)):
        (site.build / fname).write_text(world_sdf(site, z0, sun, wind, turbines, with_markers))
    (site.build / f"{site.name}.parm").write_text(
        f"# Généré par worlds/build_world.py : home du SITL = origine du monde Gazebo {site.name}.\n"
        f"# Les valeurs sont déjà des float32 exacts : aucun arrondi entre ArduPilot et Gazebo.\n"
        f"SIM_OPOS_LAT {site.lat0!r}\nSIM_OPOS_LNG {site.lon0!r}\nSIM_OPOS_ALT {z0:.2f}\nSIM_OPOS_HDG 90\n")
    preview(site, mosaic, mres, site.build / "apercu.jpg")
    meta = dict(site=site.name, origin_lat=site.lat0, origin_lon=site.lon0, origin_alt_amsl_cgvd2013=round(z0, 2),
                sun=dict(time=args.sun, azimuth_deg=round(sun[0], 1), elevation_deg=round(sun[1], 1)),
                wind_enu_mps=wind, orthos=used, sentinel2=s2, terrain=stats,
                boundaries_local_m={k: site.local(site.cfg[k]).round(2).tolist()
                                    for k in ("hard_boundary", "soft_boundary", "flight_line")},
                attribution=["NRCan HRDEM / MRDEM : Open Government Licence - Canada",
                             "City of Medicine Hat, courbes de niveau 1 m : City of Medicine Hat Open Data Licence",
                             "Contains modified Copernicus Sentinel data",
                             "OpenStreetMap contributors (éoliennes) : ODbL"] +
                            [f"Orthophoto {Path(p).name} : vérifier sa licence avant toute diffusion" for p in args.ortho])
    (site.build / "site_meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    print(f"  soleil az {sun[0]:.1f}° él {sun[1]:.1f}° ; sorties dans {site.build}")


if __name__ == "__main__":
    main()
