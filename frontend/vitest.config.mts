import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Minimal Vitest setup — added specifically for useDatasetFilters.ts's
// AOI/Custom-Boundary -> Latitude/Longitude sync regression tests (Data
// Page issue, mirrors Visualization's existing handleAoiChange behavior).
// No other test infra existed in this project before this; kept
// deliberately small (jsdom + the @/ path alias tsconfig.json already
// declares) rather than pulling in a larger test framework.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname,
    },
  },
});
