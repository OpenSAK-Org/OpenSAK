# src/opensak/geo/ — offline boundary engine (reverse geocoding, issue #60).

from opensak.geo.boundaries import GeoLocation, TerritoryResolver
from opensak.geo.store import BoundaryStore, Region, default_data_dir

__all__ = [
    "GeoLocation",
    "TerritoryResolver",
    "BoundaryStore",
    "Region",
    "default_data_dir",
]
