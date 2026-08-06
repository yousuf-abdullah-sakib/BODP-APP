import shp from "shpjs";

export async function parseShapefile(file: File): Promise<GeoJSON.FeatureCollection> {
  const buffer = await file.arrayBuffer();
  const result = await shp(buffer);
  const collection = Array.isArray(result) ? result[0] : result;
  if (!collection || collection.type !== "FeatureCollection") {
    throw new Error("Could not read a valid boundary from this file.");
  }
  return collection;
}

function ringCoordinates(geometry: GeoJSON.Geometry): number[][][] {
  if (geometry.type === "Polygon") return geometry.coordinates;
  if (geometry.type === "MultiPolygon") return geometry.coordinates.flat();
  return [];
}

// Ring coordinates are GeoJSON [lon, lat] pairs.
function pointInRing(lat: number, lon: number, ring: number[][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [loni, lati] = ring[i];
    const [lonj, latj] = ring[j];
    const intersects = lati > lat !== latj > lat && lon < ((lonj - loni) * (lat - lati)) / (latj - lati) + loni;
    if (intersects) inside = !inside;
  }
  return inside;
}

export function pointInFeatureCollection(lat: number, lon: number, fc: GeoJSON.FeatureCollection): boolean {
  for (const feature of fc.features) {
    if (!feature.geometry) continue;
    const rings = ringCoordinates(feature.geometry);
    for (const ring of rings) {
      if (pointInRing(lat, lon, ring)) return true;
    }
  }
  return false;
}
