"""元数据编辑器那一格补全背后的模型。

``_refresh_candidates`` 每 250 毫秒（防抖的那一头）就会走一遍，所以它**不能每次新建一个
``QStringListModel``** 再挂给 ``QCompleter``：候选本身没变，变的只是它背后的那几行，
而每次新建都要连着重新接一遍补全那一串内部连接。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QStringListModel  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.metadata.capture_time import CaptureTime  # noqa: E402
from gallery.core.metadata.metadata_edit import MetadataState  # noqa: E402
from gallery.core.metadata.place_edit import PlaceState  # noqa: E402
from gallery.ui.dialogs.metadata_edit import MetadataEditDialog  # noqa: E402


class _Place:
    def __init__(self, city: str, region: str | None, country: str) -> None:
        self.city = city
        self.region = region
        self.country = country
        self.latitude = 31.311
        self.longitude = 120.621


class StubResolver:
    """城市库的替身：只按名字给地点。"""

    def __init__(self) -> None:
        self.by_name = {"苏州": ("苏州", "江苏省", "中国")}

    @property
    def ready(self) -> bool:
        return True

    def place_for(self, latitude: float, longitude: float, *, status: object = None) -> object:
        return None

    def place_named(
        self,
        name: str,
        *,
        region: str | None = None,
        country: str | None = None,
        status: object = None,
    ) -> tuple[object, ...]:
        found = self.by_name.get(name.strip())
        if found is None:
            return ()
        city, province, nation = found
        return (_Place(city, province, nation),)


def _dialog(resolver: StubResolver | None) -> MetadataEditDialog:
    state = MetadataState(capture=CaptureTime(), place=PlaceState())
    return MetadataEditDialog(None, state, resolver=resolver)  # type: ignore[arg-type]


def test_the_candidate_model_is_built_once(qt_app: QApplication) -> None:  # noqa: ARG001
    """查一次、查两次、补全背后是**同一个**模型对象。"""
    dialog = _dialog(StubResolver())
    dialog._refresh_candidates("苏州")
    model = dialog._completer.model()

    assert isinstance(model, QStringListModel)
    dialog._refresh_candidates("苏州")
    dialog._refresh_candidates("苏州")

    assert dialog._completer.model() is model, "每次查都新建了一个模型 —— 连接要重接一遍"
    assert model.stringList(), "候选没喂进去"


def test_no_candidates_means_no_model_is_attached(qt_app: QApplication) -> None:  # noqa: ARG001
    """一个都查不到时**什么都不做** —— 挂上一个空模型补全窗口就会弹出来。"""
    dialog = _dialog(StubResolver())

    dialog._refresh_candidates("查不到的地方")

    assert dialog._completer.model() is None
