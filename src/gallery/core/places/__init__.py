"""离线地名查询：GeoNames 数据下载、建索引与倒查最近的城市。

纯离线运行，不联网查；:func:`download_data` 是一次性的数据获取，
详见 ``docs/adr/0003-offline-place-lookup.md``。
"""

from gallery.core.places.countries import admin1_label, country_label, place_label
from gallery.core.places.geonames import Place, PlaceIndex, download_data, geo_data_dir

__all__ = [
    "Place",
    "PlaceIndex",
    "admin1_label",
    "country_label",
    "download_data",
    "geo_data_dir",
    "place_label",
]
