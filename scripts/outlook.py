#!/usr/bin/env python3
"""Lädt die Outlook-GeoJSONs (sturm / gewitter / starkregen) von gewittermodell.de,
schließt die Lücken der StormCast-Grenze und schneidet sie bündig auf die eigene
Deutschland-Grenze (data/bund-deutschland.json) zu. Schreibt pro Typ eine <typ>.json.

Warum: StormCast schneidet auf eine vereinfachte Grenze (grenze_deutschland.geojson) zu.
Gegenüber der eigenen Grenze entstehen dadurch Lücken am Rand und kleine Löcher im Land.
Diese Bereiche (eigene Grenze minus StormCast-Grenze) werden pro Tag mit der jeweils
nächstgelegenen Stufe aufgefüllt, danach wird auf die eigene Grenze geschnitten.

Nutzung:  python scripts/outlook.py --out output/outlook
"""
import argparse, json, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

import shapely
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.validation import make_valid

BASE = "https://gewittermodell.de/autoforecast"
BORDER_URL = f"{BASE}/grenze_deutschland.geojson"  # Grenze, auf die StormCast zuschneidet
TYPES = {"sturm": "STURM", "gewitter": "GEWITTER", "starkregen": "STARKREGEN"}
DE_FILE = Path(__file__).resolve().parent.parent / "data" / "bund-deutschland.json"
UA = {"User-Agent": "Mozilla/5.0"}
GRID = 1e-5  # Rundung nach dem Zuschnitt (~1 m)

# Auffüllen der Lücken (Grad; 0.01 ~ 1 km). Die Schritte wachsen von der Datenkante aus,
# so bekommt jede Lücke die Stufe, die ihr am nächsten liegt.
FILL_STEPS = [0.003, 0.006, 0.01, 0.015, 0.02, 0.03, 0.045, 0.07, 0.1, 0.15, 0.25, 0.4]
FILL_EPS = 3e-4    # leichte Überlappung (~30 m), damit keine Haarlinien zur Datenkante bleiben
MIN_AREA = 1e-8    # kleinere Lücken-Stücke (~80 m²) ignorieren


def http_get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def iso(s):
    """String -> ISO-8601 UTC (2026-11-15T12:00:00.000Z) oder None."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    d = d.astimezone(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def fix(g):
    return g if g.is_valid else make_valid(g)


def only_polygons(g):
    """Aus dem Ergebnis nur Flächen behalten (Linien/Punkte verwerfen). None wenn leer."""
    if g is None or g.is_empty:
        return None
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [x for x in getattr(g, "geoms", []) if x.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else None


def union_of(fc, what):
    """FeatureCollection (z. B. 16 Bundesländer) -> ein Polygon."""
    geoms = [fix(shape(f["geometry"])) for f in fc.get("features", []) if f.get("geometry")]
    if not geoms:
        sys.exit(f"Keine Geometrien in: {what}")
    return fix(unary_union(geoms))


def load_germany(path):
    if not Path(path).is_file():
        sys.exit(f"Deutschland-Datei fehlt: {path}")
    return union_of(json.loads(Path(path).read_text(encoding="utf-8")), str(path))


def load_stormcast_border():
    return union_of(json.loads(http_get(BORDER_URL).strip()), BORDER_URL)


def border_gap(germany, stormcast):
    """Eigene Grenze minus StormCast-Grenze = Bereiche ohne Daten (Randstreifen + kleine Löcher)."""
    gap = only_polygons(fix(germany.difference(stormcast)))
    if gap is None:
        return None
    parts = [p for p in getattr(gap, "geoms", [gap]) if p.area >= MIN_AREA]
    return unary_union(parts) if parts else None


def fill_gaps(items, gap, germany):
    """items = [(geom, level)] eines Tages. Füllt `gap` auf: jede Lücke bekommt die nächstgelegene
    Stufe (bei Gleichstand die höhere). Gibt die erweiterten Geometrien zurück (gleiche Reihenfolge)."""
    geoms = [g for g, _ in items]
    if gap is None or not geoms:
        return geoms

    covered = unary_union(geoms)
    remaining = only_polygons(fix(gap.buffer(FILL_EPS).intersection(germany).difference(covered)))
    if remaining is None:
        return geoms

    reach = remaining.buffer(FILL_STEPS[-1])
    order = sorted(range(len(items)), key=lambda i: -(items[i][1] or 0))  # höchste Stufe zuerst
    near = {}
    for i in order:
        n = geoms[i].intersection(reach)
        near[i] = None if n.is_empty else n

    added = {i: [] for i in range(len(items))}
    for step in FILL_STEPS:
        for i in order:
            if remaining is None:
                break
            if near[i] is None:
                continue
            grown = only_polygons(near[i].buffer(step).intersection(remaining))
            if grown is None:
                continue
            added[i].append(grown)
            remaining = only_polygons(remaining.difference(grown))
        if remaining is None or remaining.area < MIN_AREA:
            break

    return [fix(unary_union([g] + added[i])) if added[i] else g for i, g in enumerate(geoms)]


def clip(geom, germany, germany_prep):
    """Bündig auf die eigene Grenze schneiden. None wenn komplett außerhalb."""
    if not germany_prep.intersects(geom):
        return None
    if not germany_prep.contains(geom):  # komplett innerhalb braucht keinen Zuschnitt
        geom = only_polygons(geom.intersection(germany))
        if geom is None:
            return None
    return only_polygons(shapely.set_precision(geom, GRID))


def build(type_, data, germany, germany_prep, gap):
    label = TYPES[type_]
    meta = data.get("metadata") or {}
    issued = iso(meta.get("erstellt"))

    # 1) Features einlesen und pro Tag gruppieren
    entries = []
    for f in data.get("features", []):
        p = f.get("properties") or {}
        if not f.get("geometry") or not float(p.get("stufe") or 0) > 0:
            continue
        entries.append({"props": p, "geom": fix(shape(f["geometry"]))})

    by_tag = {}
    for e in entries:
        by_tag.setdefault(e["props"].get("tag"), []).append(e)

    # 2) pro Tag Lücken auffüllen
    for group in by_tag.values():
        filled = fill_gaps([(e["geom"], float(e["props"].get("stufe") or 0)) for e in group], gap, germany)
        for e, g in zip(group, filled):
            e["geom"] = g

    # 3) zuschneiden und Features bauen
    features = []
    for e in entries:
        p = e["props"]
        g = clip(e["geom"], germany, germany_prep)  # Fehler -> Abbruch (Retry im Workflow)
        if g is None:
            continue
        datum = p.get("datum")
        features.append({
            "type": "Feature",
            "geometry": mapping(g),
            "properties": {
                "id": len(features) + 1,
                "type": p.get("gefahr") or type_,
                "category": label,
                "categoryLabel": f"{label} AUSBLICK",
                "title": p.get("titel"),
                "tag": p.get("tag"),
                "date": datum,
                "validLabel": p.get("gueltig"),
                "level": p.get("stufe"),
                "levelLabel": p.get("stufe_text"),
                "radiusKm": p.get("umkreis_km"),
                "model": p.get("modell"),
                "name": p.get("name"),
                "description": p.get("description"),
                "start": iso(f"{datum}T00:00:00Z") if datum else None,
                "end": iso(f"{datum}T23:59:59Z") if datum else None,
                "issued": issued,
                "clipped": True,
                "fill": p.get("fill"),
                "fillOpacity": p.get("fill-opacity"),
                "stroke": p.get("stroke"),
                "strokeWidth": p.get("stroke-width"),
                "strokeOpacity": p.get("stroke-opacity"),
            },
        })

    # nach Tag, dann Stufe aufsteigend (höchste Stufe liegt oben)
    features.sort(key=lambda x: (x["properties"]["tag"] or 0, x["properties"]["level"] or 0))
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {"type": type_, "issued": issued, "clipped": True, "source": meta},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="output/outlook", help="Ausgabeordner")
    ap.add_argument("--de", default=str(DE_FILE), help="eigene Landesgrenzen-GeoJSON")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    germany = load_germany(args.de)
    germany_prep = prep(germany)
    stormcast = load_stormcast_border()
    gap = border_gap(germany, stormcast)
    print(f"Lücken-Fläche (eigene minus StormCast-Grenze): {0 if gap is None else gap.area:.6f} Grad²")

    # erst alles berechnen, dann schreiben -> nie halbe Ausgaben
    results = {}
    for t in TYPES:
        url = f"{BASE}/{t}.geojson"
        print(f"[{t}] lade {url}")
        data = json.loads(http_get(url).strip())
        results[t] = build(t, data, germany, germany_prep, gap)
        print(f"[{t}] {len(results[t]['features'])} Features, issued={results[t]['metadata']['issued']}")

    for t, body in results.items():
        path = out / f"{t}.json"
        path.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"[{t}] -> {path} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
