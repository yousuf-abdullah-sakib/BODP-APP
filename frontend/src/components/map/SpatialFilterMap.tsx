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
}

export default function SpatialFilterMap({
  onAoiChange,
  clearSignal,
  stations = [],
  enablePolygon = false,
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

  return <div id="spatialMap" ref={containerRef} />;
}
