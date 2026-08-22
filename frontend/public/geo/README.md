# geo/

`bangladesh-boundary.geojson` — the site-wide default reference boundary
shown on the Visualize page's Spatial Mapping module (used until an admin
uploads a replacement via the admin "Data Visualization" section — see
`GET/POST /boundary-shapefiles`).

Source: [Natural Earth](https://www.naturalearthdata.com), 1:10m Cultural
Vectors, Admin 0 – Countries, version 5.1.1 — public domain, no
attribution legally required, credited here as standard practice.
Filtered to the Bangladesh feature (`ADM0_A3 == "BGD"`) and simplified
(Douglas–Peucker, tolerance ≈100m) to keep the file lightweight as a
reference/backdrop layer — not used for precision scientific work, which
is what the admin-uploadable override exists for.
