"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet-draw/dist/leaflet.draw.css";
import "leaflet-draw";
import type { SpatialAOI } from "@/lib/geo/spatialAoi";
import type { StationOption } from "@/lib/types/catalog";

export type { SpatialBounds, SpatialAOI } from "@/lib/geo/spatialAoi";

interface SpatialFilterMapProps {
  onAoiChange: (aoi: SpatialAOI | null) => void;
  clearSignal: number;
  /** Reference station markers shown on the map — omit for an empty base map. */
  stations?: StationOption[];
  /** Enables the polygon draw tool alongside rectangle. Off by default to keep existing rectangle-only consumers unchanged. */
  enablePolygon?: boolean;
  /**
   * Programmatically draws this shape onto the map's draw layer (e.g. an
   * uploaded Custom Boundary), the same layer/styling a hand-drawn AOI
   * uses, and fits the view to it — a one-way sync from parent state to
   * the map, never fed back through onAoiChange (the caller already
   * knows this value; only genuine hand-drawing should trigger
   * onAoiChange). Ignored when null/omitted.
   */
  externalAoi?: SpatialAOI | null;
}

export default function SpatialFilterMap({
  onAoiChange,
  clearSignal,
  stations = [],
  enablePolygon = false,
  externalAoi = null,
}: SpatialFilterMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const drawLayerRef = useRef<L.FeatureGroup | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      zoomControl: true,
      attributionControl: false,
    }).setView([22.5, 90.8], 6);

    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
      maxZoom: 19,
    }).addTo(map);

    const drawLayer = new L.FeatureGroup();
    map.addLayer(drawLayer);
    drawLayerRef.current = drawLayer;

    const drawControl = new L.Control.Draw({
      draw: {
        rectangle: { shapeOptions: { color: "#0f766e", weight: 2 } },
        polygon: enablePolygon
          ? { shapeOptions: { color: "#0f766e", weight: 2 }, allowIntersection: false, showArea: true }
          : false,
        polyline: false,
        circle: false,
        circlemarker: false,
        marker: false,
      },
      edit: { featureGroup: drawLayer },
    });
    map.addControl(drawControl);

    stations.forEach((s) => {
      L.circleMarker([s.lat, s.lon], {
        radius: 5,
        color: "#0f766e",
        fillColor: "#0f766e",
        fillOpacity: 0.7,
        weight: 1,
      })
        .addTo(map)
        .bindTooltip(s.name, { direction: "top" });
    });

    map.on(L.Draw.Event.CREATED, (e) => {
      const event = e as unknown as L.DrawEvents.Created;
      const layer = event.layer;
      drawLayer.clearLayers();
      drawLayer.addLayer(layer);

      if (event.layerType === "polygon") {
        const latLngs = (layer as L.Polygon).getLatLngs()[0] as L.LatLng[];
        const ring: [number, number][] = latLngs.map((ll) => [ll.lat, ll.lng]);
        onAoiChange({ kind: "polygon", ring });
      } else {
        const bounds = (layer as L.Rectangle).getBounds();
        onAoiChange({
          kind: "rectangle",
          bounds: {
            latMin: bounds.getSouth(),
            latMax: bounds.getNorth(),
            lonMin: bounds.getWest(),
            lonMax: bounds.getEast(),
          },
        });
      }
    });

    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (clearSignal > 0 && drawLayerRef.current) {
      drawLayerRef.current.clearLayers();
    }
  }, [clearSignal]);

  useEffect(() => {
    if (!externalAoi || !mapRef.current || !drawLayerRef.current) return;
    drawLayerRef.current.clearLayers();
    // "geometry" (an uploaded Custom Boundary) renders via Leaflet's own
    // GeoJSON handling -- every feature, every Polygon/MultiPolygon
    // part, every interior hole, exactly as parsed, not reconstructed
    // from a single ring. L.GeoJSON, L.Polygon, and L.Rectangle all
    // implement getBounds() (just not through a common Leaflet type),
    // which is all this effect needs from whichever one gets built.
    const layer: L.Layer & { getBounds(): L.LatLngBounds } =
      externalAoi.kind === "geometry"
        ? L.geoJSON(externalAoi.geojson, { style: { color: "#0f766e", weight: 2 } })
        : externalAoi.kind === "polygon"
          ? L.polygon(externalAoi.ring, { color: "#0f766e", weight: 2 })
          : L.rectangle(
              [
                [externalAoi.bounds.latMin, externalAoi.bounds.lonMin],
                [externalAoi.bounds.latMax, externalAoi.bounds.lonMax],
              ],
              { color: "#0f766e", weight: 2 }
            );
    drawLayerRef.current.addLayer(layer);
    const bounds = layer.getBounds();
    if (bounds.isValid()) mapRef.current.fitBounds(bounds, { padding: [20, 20] });
  }, [externalAoi]);

  return <div id="spatialMap" ref={containerRef} />;
}
