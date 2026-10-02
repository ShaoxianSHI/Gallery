"""离线地名查询：读随包分发的城市库，按坐标找最近的城市。

纯离线运行，**不联网也不下载** —— 数据在 ``resources/places.tsv.gz`` 里，
由 ``scripts/build_places_db.py`` 生成，详见 ``docs/adr/0012-bundled-places-database.md``。
"""

from gallery.core.places.countries import place_label
from gallery.core.places.dataset import Place, PlaceIndex

__all__ = [
    "Place",
    "PlaceIndex",
    "place_label",
]
