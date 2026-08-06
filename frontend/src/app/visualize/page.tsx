import type { Metadata } from "next";
import "./visualize.css";
import VisualizeClient from "./VisualizeClient";

export const metadata: Metadata = {
  title: "Visualization — BODP",
};

export default function VisualizePage() {
  return <VisualizeClient />;
}
