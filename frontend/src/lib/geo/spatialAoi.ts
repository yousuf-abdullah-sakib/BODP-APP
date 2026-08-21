import { ringCoordinates } from "./shapefileUpload";

export interface SpatialBounds {
  latMin: number;
  latMax: number;
  lonMin: number;
  lonMax: number;
}

export type SpatialAOI =
  | { kind: "rectangle"; bounds: SpatialBounds }
  | { kind: "polygon"; ring: [number, number][] }
  // An uploaded Custom Boundary — the FULL parsed shapefile geometry
  // (every feature, every Polygon/MultiPolygon part, every hole), kept
  // exactly as parsed rather than reduced to one ring, so map rendering
  // and boundsOf() below both work from the real, complete shape.
  | { kind: "geometry"; geojson: GeoJSON.FeatureCollection };

/** Bounding-box reduction of an AOI, for consumers (the actual backend
 * spatial filter, area-estimate math) that only understand a rectangle —
 * this project's spatial filter is bbox-only today (ST_Intersects
 * against a rectangular envelope), same as a hand-drawn polygon AOI
 * already only ever contributes its bounding box to the real query. For
 * "geometry", the box spans every ring of every feature (holes included
 * — a hole's vertices are always inside its outer ring already, so
 * including them never widens the box beyond the true extent), not just
 * a single ring, so the filter honestly reflects the uploaded shape's
 * real extent. */
export function boundsOf(aoi: SpatialAOI): SpatialBounds {
  if (aoi.kind === "rectangle") return aoi.bounds;
  if (aoi.kind === "polygon") {
    const lats = aoi.ring.map((p) => p[0]);
    const lons = aoi.ring.map((p) => p[1]);
    return {
      latMin: Math.min(...lats),
      latMax: Math.max(...lats),
      lonMin: Math.min(...lons),
      lonMax: Math.max(...lons),
    };
  }
  let latMin = Infinity;
  let latMax = -Infinity;
  let lonMin = Infinity;
  let lonMax = -Infinity;
  for (const feature of aoi.geojson.features) {
    if (!feature.geometry) continue;
    for (const ring of ringCoordinates(feature.geometry)) {
      for (const [lon, lat] of ring) {
        if (lat < latMin) latMin = lat;
        if (lat > latMax) latMax = lat;
        if (lon < lonMin) lonMin = lon;
        if (lon > lonMax) lonMax = lon;
      }
    }
  }
  return { latMin, latMax, lonMin, lonMax };
}
