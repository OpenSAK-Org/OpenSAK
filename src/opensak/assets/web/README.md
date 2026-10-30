# Bundled web assets

The map (`opensak/gui/map_widget.py`) loads these files through the
`opensak://assets/<path>` scheme (`opensak/gui/web_assets.py`) instead of from
unpkg.com, so it works offline (#1111). Each folder holds the unmodified
`dist/` files from the npm package plus its licence; see
`THIRD_PARTY_NOTICES.md` in the repository root.

| Folder | npm package | Version | Files taken from the tarball |
|---|---|---|---|
| `leaflet/` | `leaflet` | 1.9.4 | `dist/leaflet.js`, `dist/leaflet.css`, `dist/images/*.png`, `LICENSE` |
| `leaflet.markercluster/` | `leaflet.markercluster` | 1.5.3 | `dist/leaflet.markercluster.js`, `dist/MarkerCluster.css`, `dist/MarkerCluster.Default.css`, `MIT-LICENCE.txt` (as `LICENSE`) |
| `leaflet-geoman/` | `@geoman-io/leaflet-geoman-free` | 2.19.3 | `dist/leaflet-geoman.js`, `dist/leaflet-geoman.css`, `LICENSE` |

Tarball integrity (npm registry `dist.integrity`, checked when the files were
added):

```
leaflet-1.9.4.tgz                sha512-nxS1ynzJOmOlHp+iL3FyWqK89GtNL8U8rvlMOsQdTTssxZwCXh8N2NB3GDQOL+YR3XnWyZAxwQixURb+FA74PA==
leaflet.markercluster-1.5.3.tgz  sha512-vPTw/Bndq7eQHjLBVlWpnGeLa3t+3zGiuM7fJwCkiMFq+nmRuG3RI3f7f4N4TDX7T4NpbAXpR2+NTRSEGfCSeA==
leaflet-geoman-free-2.19.3.tgz   sha512-HjbEpfAEUs0NyI1Dhvz3SMVG6m0pAN/1Eo0tRKsz9cpaROTrFtmJGY22swEir1Uj/8IeGF1NJId38C5Fu+nZGQ==
```

To update a library: download the tarball from
`https://registry.npmjs.org/<package>/-/<name>-<version>.tgz`, check it against
the registry's `dist.integrity`, copy the files listed above, and update the
versions here, in `map_widget.py`'s HTML comments and in
`THIRD_PARTY_NOTICES.md`.
