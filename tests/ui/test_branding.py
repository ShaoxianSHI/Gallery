"""应用图标与名字的测试：窗口标题栏左侧那一枚，得真的是我们的图标。"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from gallery.core.constants import APP_ICON_SIZES, APP_NAME
from gallery.core.paths import app_icon_files
from gallery.ui.app import apply_app_branding


def test_window_icon_is_set(qt_app: QApplication) -> None:
    apply_app_branding(qt_app)

    icon = qt_app.windowIcon()
    assert not icon.isNull(), "窗口图标为空，说明 src/gallery/resources/ 里的图标没被找到"

    available = {size.width() for size in icon.availableSizes()}
    assert set(APP_ICON_SIZES) <= available


def test_icon_files_are_all_readable(qt_app: QApplication) -> None:
    """``QIcon`` 只收下真正读得到的档位；缺一档就等于那一档没配。"""
    apply_app_branding(qt_app)

    assert all(path.is_file() for path in app_icon_files())


def test_application_name_comes_from_constants(qt_app: QApplication) -> None:
    apply_app_branding(qt_app)

    assert qt_app.applicationName() == APP_NAME
    assert qt_app.applicationDisplayName() == APP_NAME
