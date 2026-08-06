import type { SpatialBounds } from "./spatialAoi";

const EARTH_RADIUS_KM = 6371.0;

/** Approximate area of a lat/lon rectangle in km² — same flat-earth
 * approximation (scaled by cos(mean latitude) for the east-west side) as
 * the backend's _bbox_area_km2 in visualize_service.py, kept as an
 * independent client-side utility so the AOI area can be shown live while
 * drawing, with no round-trip. */
export function bboxAreaKm2(bounds: SpatialBounds): number {
  const latSpanKm = (bounds.latMax - bounds.latMin) * (Math.PI / 180) * EARTH_RADIUS_KM;
  const meanLatRad = ((bounds.latMin + bounds.latMax) / 2) * (Math.PI / 180);
  const lonSpanKm =
    (bounds.lonMax - bounds.lonMin) * (Math.PI / 180) * EARTH_RADIUS_KM * Math.cos(meanLatRad);
  return Math.abs(latSpanKm * lonSpanKm);
}
