export interface SpatialBounds {
  latMin: number;
  latMax: number;
  lonMin: number;
  lonMax: number;
}

export type SpatialAOI =
  | { kind: "rectangle"; bounds: SpatialBounds }
  | { kind: "polygon"; ring: [number, number][] };

/** Bounding-box reduction of an AOI, for consumers that only understand a rectangle. */
export function boundsOf(aoi: SpatialAOI): SpatialBounds {
  if (aoi.kind === "rectangle") return aoi.bounds;
  const lats = aoi.ring.map((p) => p[0]);
  const lons = aoi.ring.map((p) => p[1]);
  return {
    latMin: Math.min(...lats),
    latMax: Math.max(...lats),
    lonMin: Math.min(...lons),
    lonMax: Math.max(...lons),
  };
}
