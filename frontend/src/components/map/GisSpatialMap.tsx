"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { colorForValue, rampColor, classifyValue, classifyByBreakpoints, type ColorRampName, type InterpolationDisplayMode } from "@/lib/geo/colorRamp";
import { pointInFeatureCollection } from "@/lib/geo/shapefileUpload";
import type { SpatialBounds, SpatialAOI } from "@/lib/geo/spatialAoi";
import type { SpatialGrid } from "@/lib/types/visualize";

export interface GisPoint {
  station: string;
  lat: number;
  lon: number;
  value: number;
  sizeValue: number;
}

export type InterpolationOutput = "contour" | "heatmap" | "points-only";
export type BaseMapName = "light" | "dark" | "satellite" | "terrain";

const TILE_LAYERS: Record<BaseMapName, { url: string; maxZoom: number }> = {
  light: { url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", maxZoom: 19 },
  dark: { url: "https://{s}.basemaps.cartocdn.com/dark_matter/{z}/{x}/{y}{r}.png", maxZoom: 19 },
  satellite: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    maxZoom: 18,
  },
  terrain: { url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", maxZoom: 17 },
};

export interface GisMapApi {
  zoomToFullExtent: () => void;
  zoomToLayer: () => void;
  zoomToSelection: () => void;
  fitToBoundary: () => void;
}

interface GisSpatialMapProps {
  points: GisPoint[];
  /** Real interpolated grid from the backend (Master Plan §3 Phase 7 task 2)
   * — null while loading/unavailable, in which case no raster overlay is
   * rendered even if showInterpolation is true. */
  grid: SpatialGrid | null;
  ramp: ColorRampName;
  showInterpolation: boolean;
  interpolationOutput: InterpolationOutput;
  displayMode: InterpolationDisplayMode;
  classCount: number;
  breakpoints: number[];
  uploadedBoundary: GeoJSON.FeatureCollection | null;
  defaultBoundaryOverride: GeoJSON.FeatureCollection | null;
  showBaseBoundary: boolean;
  showPoints: boolean;
  aoi: SpatialAOI | null;
  baseMap: BaseMapName;
  interpolationOpacity: number;
  pointsOpacity: number;
  height?: number;
  mapApiRef?: React.MutableRefObject<GisMapApi | null>;
}

const DEFAULT_CENTER: [number, number] = [22.8, 90.4];
const DEFAULT_ZOOM = 7;

function boundsContains(bounds: SpatialBounds, lat: number, lon: number): boolean {
  return lat >= bounds.latMin && lat <= bounds.latMax && lon >= bounds.lonMin && lon <= bounds.lonMax;
}

function pointInPolygonRing(lat: number, lon: number, ring: [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [lati, loni] = ring[i];
    const [latj, lonj] = ring[j];
    const intersects = lati > lat !== latj > lat && lon < ((lonj - loni) * (lat - lati)) / (latj - lati) + loni;
    if (intersects) inside = !inside;
  }
  return inside;
}

function aoiContains(aoi: SpatialAOI, lat: number, lon: number): boolean {
  if (aoi.kind === "rectangle") return boundsContains(aoi.bounds, lat, lon);
  return pointInPolygonRing(lat, lon, aoi.ring);
}

function aoiToLatLngs(aoi: SpatialAOI): L.LatLngExpression[] {
  if (aoi.kind === "rectangle") {
    const { latMin, latMax, lonMin, lonMax } = aoi.bounds;
    return [
      [latMin, lonMin],
      [latMin, lonMax],
      [latMax, lonMax],
      [latMax, lonMin],
    ];
  }
  return aoi.ring;
}

function aoiBoundingBox(aoi: SpatialAOI): SpatialBounds {
  if (aoi.kind === "rectangle") return aoi.bounds;
  const lats = aoi.ring.map((p) => p[0]);
  const lons = aoi.ring.map((p) => p[1]);
  return { latMin: Math.min(...lats), latMax: Math.max(...lats), lonMin: Math.min(...lons), lonMax: Math.max(...lons) };
}

export default function GisSpatialMap({
  points,
  grid,
  ramp,
  showInterpolation,
  interpolationOutput,
  displayMode,
  classCount,
  breakpoints,
  uploadedBoundary,
  defaultBoundaryOverride,
  showBaseBoundary,
  showPoints,
  aoi,
  baseMap,
  interpolationOpacity,
  pointsOpacity,
  height = 560,
  mapApiRef,
}: GisSpatialMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const tileLayerRef = useRef<L.TileLayer | null>(null);
  const baseBoundaryRef = useRef<L.GeoJSON | null>(null);
  const uploadedBoundaryRef = useRef<L.GeoJSON | null>(null);
  const aoiLayerRef = useRef<L.Polygon | null>(null);
  const pointsLayerRef = useRef<L.LayerGroup | null>(null);
  const overlayRef = useRef<L.ImageOverlay | null>(null);
  const boundaryDataRef = useRef<GeoJSON.FeatureCollection | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current, { zoomControl: true, attributionControl: false }).setView(DEFAULT_CENTER, DEFAULT_ZOOM);
    const tiles = L.tileLayer(TILE_LAYERS.light.url, { maxZoom: TILE_LAYERS.light.maxZoom }).addTo(map);
    tileLayerRef.current = tiles;

    pointsLayerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    if (mapApiRef) {
      mapApiRef.current = {
        zoomToFullExtent: () => map.setView(DEFAULT_CENTER, DEFAULT_ZOOM),
        zoomToLayer: () => {
          const target = uploadedBoundaryRef.current ?? aoiLayerRef.current ?? baseBoundaryRef.current;
          if (target) {
            try {
              map.fitBounds(target.getBounds(), { padding: [20, 20] });
              return;
            } catch {
              // fall through to points
            }
          }
          const markers = pointsLayerRef.current?.getLayers() ?? [];
          if (markers.length > 0) {
            try {
              map.fitBounds(L.featureGroup(markers as L.Layer[]).getBounds(), { padding: [30, 30] });
            } catch {
              // no valid bounds available
            }
          }
        },
        zoomToSelection: () => {
          if (!aoiLayerRef.current) return;
          try {
            map.fitBounds(aoiLayerRef.current.getBounds(), { padding: [20, 20] });
          } catch {
            // empty selection
          }
        },
        fitToBoundary: () => {
          const target = uploadedBoundaryRef.current ?? baseBoundaryRef.current;
          if (!target) return;
          try {
            map.fitBounds(target.getBounds(), { padding: [20, 20] });
          } catch {
            // no valid boundary yet
          }
        },
      };
    }

    if (defaultBoundaryOverride) {
      boundaryDataRef.current = defaultBoundaryOverride;
      baseBoundaryRef.current = L.geoJSON(defaultBoundaryOverride, {
        style: { color: "#0f766e", weight: 2, fillOpacity: 0, dashArray: "4 3" },
      });
      if (showBaseBoundary) baseBoundaryRef.current.addTo(map);
    } else {
      fetch("/geo/bangladesh-boundary.geojson")
        .then((r) => r.json())
        .then((geo: GeoJSON.FeatureCollection) => {
          boundaryDataRef.current = geo;
          baseBoundaryRef.current = L.geoJSON(geo, {
            style: { color: "#0f766e", weight: 2, fillOpacity: 0, dashArray: "4 3" },
          });
          if (showBaseBoundary && mapRef.current) baseBoundaryRef.current.addTo(mapRef.current);
        })
        .catch(() => {
          // boundary is a supplementary reference layer; map remains usable without it
        });
    }

    return () => {
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (tileLayerRef.current) map.removeLayer(tileLayerRef.current);
    const cfg = TILE_LAYERS[baseMap];
    const tiles = L.tileLayer(cfg.url, { maxZoom: cfg.maxZoom }).addTo(map);
    tileLayerRef.current = tiles;
    tiles.setOpacity(showInterpolation ? 0.55 : 1);
  }, [baseMap, showInterpolation]);

  useEffect(() => {
    if (!defaultBoundaryOverride) return;
    const map = mapRef.current;
    if (!map) return;
    if (baseBoundaryRef.current) map.removeLayer(baseBoundaryRef.current);
    boundaryDataRef.current = defaultBoundaryOverride;
    baseBoundaryRef.current = L.geoJSON(defaultBoundaryOverride, {
      style: { color: "#0f766e", weight: 2, fillOpacity: 0, dashArray: "4 3" },
    });
    if (showBaseBoundary) baseBoundaryRef.current.addTo(map);
  }, [defaultBoundaryOverride, showBaseBoundary]);

  useEffect(() => {
    const map = mapRef.current;
    const layer = baseBoundaryRef.current;
    if (!map || !layer) return;
    if (showBaseBoundary) layer.addTo(map);
    else map.removeLayer(layer);
  }, [showBaseBoundary, points]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (uploadedBoundaryRef.current) {
      map.removeLayer(uploadedBoundaryRef.current);
      uploadedBoundaryRef.current = null;
    }
    if (uploadedBoundary) {
      const layer = L.geoJSON(uploadedBoundary, { style: { color: "#7c3aed", weight: 2.5, fillOpacity: 0.04, fillColor: "#7c3aed" } });
      layer.addTo(map);
      uploadedBoundaryRef.current = layer;
      try {
        map.fitBounds(layer.getBounds(), { padding: [20, 20] });
      } catch {
        // empty/invalid bounds — keep current view
      }
    }
  }, [uploadedBoundary]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (aoiLayerRef.current) {
      map.removeLayer(aoiLayerRef.current);
      aoiLayerRef.current = null;
    }
    if (aoi) {
      const shape = L.polygon(aoiToLatLngs(aoi), {
        color: "#ea580c",
        weight: 2,
        fillColor: "#ea580c",
        fillOpacity: 0.06,
        dashArray: "6 4",
      }).addTo(map);
      aoiLayerRef.current = shape;
    }
  }, [aoi]);

  useEffect(() => {
    const layer = pointsLayerRef.current;
    if (!layer) return;
    layer.clearLayers();
    if (!showPoints || points.length === 0) return;

    const values = points.map((p) => p.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const sizeValues = points.map((p) => p.sizeValue);
    const sizeMin = Math.min(...sizeValues);
    const sizeMax = Math.max(...sizeValues);

    points.forEach((p) => {
      const color = colorForValue(p.value, min, max, ramp);
      const sizeT = sizeMax === sizeMin ? 0.5 : (p.sizeValue - sizeMin) / (sizeMax - sizeMin);
      const radius = 6 + sizeT * 14;
      L.circleMarker([p.lat, p.lon], {
        radius,
        color: "#1e293b",
        weight: 1,
        fillColor: color,
        fillOpacity: pointsOpacity,
      })
        .bindTooltip(`<b>${p.station}</b><br/>${p.value.toFixed(2)}`, { direction: "top" })
        .addTo(layer);
    });
  }, [points, ramp, showPoints, pointsOpacity]);

  // Interpolation overlay: rasterize the real backend grid to a canvas,
  // render as a Leaflet image overlay — this canvas is display-only and is
  // never wired to any export/download action (Master Plan §3 Phase 7 —
  // the spatial map has no download affordance by design).
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (overlayRef.current) {
      map.removeLayer(overlayRef.current);
      overlayRef.current = null;
    }
    if (!showInterpolation || !grid || grid.lats.length === 0) return;

    const zFlat = grid.z.flat();
    const zMin = Math.min(...zFlat);
    const zMax = Math.max(...zFlat);

    const size = grid.lats.length;
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const imageData = ctx.createImageData(size, size);

    const boundary = uploadedBoundary ?? boundaryDataRef.current;
    const aoiBox = aoi ? aoiBoundingBox(aoi) : null;

    for (let row = 0; row < size; row++) {
      for (let col = 0; col < size; col++) {
        const lat = grid.lats[size - 1 - row];
        const lon = grid.lons[col];
        const value = grid.z[size - 1 - row][col];
        const idx = (row * size + col) * 4;

        const insideBoundary = boundary ? pointInFeatureCollection(lat, lon, boundary) : true;
        const insideAoi = aoi ? (aoiBox && boundsContains(aoiBox, lat, lon) ? aoiContains(aoi, lat, lon) : false) : true;
        if (!insideBoundary || !insideAoi || interpolationOutput === "points-only") {
          imageData.data[idx + 3] = 0;
          continue;
        }

        let rgbString: string;
        if (displayMode === "classified") {
          rgbString = classifyValue(value, zMin, zMax, classCount, ramp);
        } else if (displayMode === "custom" && breakpoints.length > 0) {
          rgbString = classifyByBreakpoints(value, breakpoints, ramp);
        } else {
          const t = zMax === zMin ? 0.5 : (value - zMin) / (zMax - zMin);
          rgbString = rampColor(t, ramp);
        }
        const rgb = rgbString.match(/\d+/g)!.map(Number);
        imageData.data[idx] = rgb[0];
        imageData.data[idx + 1] = rgb[1];
        imageData.data[idx + 2] = rgb[2];
        imageData.data[idx + 3] = interpolationOutput === "contour" ? 200 : 235;
      }
    }
    ctx.putImageData(imageData, 0, 0);

    const dataUrl = canvas.toDataURL();
    const bounds = L.latLngBounds([grid.lats[0], grid.lons[0]], [grid.lats[grid.lats.length - 1], grid.lons[grid.lons.length - 1]]);
    const overlay = L.imageOverlay(dataUrl, bounds, { opacity: interpolationOpacity, interactive: false }).addTo(map);
    overlayRef.current = overlay;
  }, [grid, ramp, showInterpolation, interpolationOutput, displayMode, classCount, breakpoints, uploadedBoundary, aoi, interpolationOpacity]);

  return <div className="gis-spatial-map" ref={containerRef} style={{ height, width: "100%" }} />;
}
