# past

Lagrangian dispersion modelling and Bayesian inversion of SO2 emissions from
fissure eruptions on the Reykjanes peninsula, Iceland.

The aim is to estimate SO2 flux and injection height through time from
ground-level SO2 (Umhverfis- og orkustofnun stations) and TROPOMI SO2 columns,
using source–receptor matrices computed from CARRA reanalysis model levels.

## Layout

- `config/project.yaml` – domain box and eruption windows
- `src/past_so2/fetch/` – data fetchers (stations, TROPOMI, reanalysis)
- `src/past_so2/storage.py` – Backblaze B2 access
- `.github/workflows/` – fetch jobs; each skips what is already in the bucket

## Private bucket layout

| Prefix | Contents |
|---|---|
| `stations/` | `stations.parquet`, `so2_<year>.parquet` (hourly, µg/m³, UTC hour end) |
| `tropomi/so2/YYYY/MM/DD/` | L2 SO2 orbits cut to the scanlines crossing the domain |
| `met/` | reanalysis subsets |

## Repository configuration

Secrets: `CDSAPI_KEY`, `CDSE_S3_ACCESS_KEY`, `CDSE_S3_SECRET_KEY`,
`B2_PRIVATE_KEY_ID`, `B2_PRIVATE_APPLICATION_KEY`, `B2_PUBLIC_KEY_ID`,
`B2_PUBLIC_APPLICATION_KEY`.

Variables: `CDSAPI_URL`, `B2_ENDPOINT`, `B2_PRIVATE_BUCKET`, `B2_PUBLIC_BUCKET`,
`B2_PUBLIC_URL`.

## Web viewer

`web/` is a static React page (OpenLayers map, uPlot graphs) deployed to GitHub
Pages by the "Deploy site" workflow. It reads the files that the "Build web
data" workflow publishes under `web/v1/` in the public bucket.

```
cd web && npm install && npm run dev
```

## Development

```
pip install -e ".[met,dev]"
pytest
```
