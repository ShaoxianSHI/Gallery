"""「设置」菜单：界面与播放的偏好，以及这个库自己的几项配置（ADR 0073）。

三组东西放同一张菜单，但**存的地方不同**：动画速度、外观、放映间隔、启动行为属于这个应用，
写进会话文件；影片网站与缓存上限属于**这个库**，经 ``Library`` 写进 ``config.toml``。
**本模块一个用户的照片文件都不碰**；两个库级入口都只调 ``Library`` 的公开方法。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import cast

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QWidget

from gallery import __version__
from gallery.core import paths
from gallery.core.constants import APP_NAME
from gallery.core.errors import GalleryError
from gallery.core.external import launch
from gallery.core.library.session import Preferences
from gallery.ui import theme

#: 影片网站预设：**只收「前缀 + 番号」拼得出地址的站点**。JavDB 按内部 id 或搜索参数走，
#: 拼 ``javdb.com/v/ipx-660`` 是死链，所以它当不了预设（它是抓取源，不是番号页）。
MOVIE_SITE_PRESETS: tuple[tuple[str, str], ...] = (
    ("https://jable.tv/videos", "Jable.tv"),
    ("https://missav.com/en", "MissAV"),
)

#: 缓存上限的界面档位（字节, 文案）。0 表示不限制。
CACHE_LIMIT_PRESETS: tuple[tuple[int, str], ...] = (
    (1 * 1024**3, "1 GB"),
    (2 * 1024**3, "2 GB"),
    (5 * 1024**3, "5 GB"),
    (10 * 1024**3, "10 GB"),
    (0, "不限制"),
)

SPEED_LABELS: dict[str, str] = {
    "slow": "慢",
    "normal": "中",
    "fast": "快",
    "off": "关",
}
APPEARANCE_LABELS: dict[str, str] = {
    "system": "跟随系统",
    "light": "浅色",
    "dark": "深色",
}
SLIDESHOW_SECONDS: tuple[int, ...] = (3, 5, 8, 15)


class SettingsFlowMixin:
    """拼「设置」菜单、处理它的动作，并把当前值映射回勾选态。"""

    def build_settings_menu(self) -> None:
        """把「设置」挂到菜单栏末尾。应用级在上、库级居中、维护在最后。"""
        menu = cast("QMenu", self.menuBar().addMenu("设置"))
        self._settings_actions = {
            "animation": self._add_choice_menu(
                menu, "动画速度", self._set_animation_speed, SPEED_LABELS
            ),
            "appearance": self._add_choice_menu(
                menu, "外观", self._set_appearance, APPEARANCE_LABELS
            ),
        }
        menu.addSeparator()
        self._settings_actions["movie_site"] = self._add_choice_menu(
            menu,
            "影片网站",
            self._set_movie_site,
            {value: label for value, label in MOVIE_SITE_PRESETS},
            custom=True,
        )
        self._settings_actions["cache"] = self._add_choice_menu(
            menu,
            "缓存上限",
            self._set_cache_limit,
            {value: label for value, label in CACHE_LIMIT_PRESETS},
        )
        self._library_setting_actions = [
            *self._settings_actions["movie_site"].values(),
            *self._settings_actions["cache"].values(),
        ]
        menu.addSeparator()
        self._settings_actions["slideshow"] = self._add_choice_menu(
            menu,
            "放映间隔",
            self._set_slideshow_seconds,
            {seconds: f"{seconds} 秒" for seconds in SLIDESHOW_SECONDS},
        )
        self._settings_extras: dict[str, QAction] = {}
        open_last = QAction("启动时打开上次的库", cast("QWidget", self))
        open_last.setCheckable(True)
        # 用 ``triggered`` 而不是 ``toggled``：同步勾选态时 setChecked 不该再写一次会话。
        open_last.triggered.connect(self._set_open_last_library)
        menu.addAction(open_last)
        self._settings_extras["open_last_library"] = open_last
        menu.addSeparator()
        log_action = QAction("打开日志文件夹", cast("QWidget", self))
        log_action.triggered.connect(self.open_log_folder)
        menu.addAction(log_action)
        self._settings_extras["log_folder"] = log_action
        about = QAction(f"关于 {APP_NAME}", cast("QWidget", self))
        about.triggered.connect(self.show_about)
        menu.addAction(about)
        self._settings_extras["about"] = about

    def _add_choice_menu(
        self,
        parent: QMenu,
        title: str,
        handler: Callable[[object], None],
        labels: dict[object, str],
        *,
        custom: bool = False,
    ) -> dict[object, QAction]:
        """建一个单选子菜单，返回「值 → 动作」。**单选靠 ``QActionGroup``，勾选态只由同步写。**"""
        menu = QMenu(title, cast("QWidget", self))
        group = QActionGroup(menu)
        group.setExclusive(True)
        actions: dict[object, QAction] = {}
        for value, label in labels.items():
            action = QAction(label, cast("QWidget", self))
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, v=value: handler(v))
            group.addAction(action)
            menu.addAction(action)
            actions[value] = action
        if custom:
            # 它也是一个「当前值」：手改过的网址要有一个地方显示成选中。
            custom_action = QAction("自定义…", cast("QWidget", self))
            custom_action.setCheckable(True)
            custom_action.triggered.connect(self._ask_movie_site)
            group.addAction(custom_action)
            menu.addAction(custom_action)
            actions[_CUSTOM] = custom_action
        parent.addMenu(menu)
        return actions

    # ---- 应用级：写会话文件，立刻生效 ----------------------------------------

    def _set_animation_speed(self, speed: object) -> None:
        prefs = replace(self._session.preferences, animation_speed=str(speed))
        self._store_preferences(prefs)
        self._grid.set_animation_speed(str(speed))  # type: ignore[attr-defined]
        self._detail.set_animation_speed(str(speed))  # type: ignore[attr-defined]
        self._sync_ui()

    def _set_appearance(self, mode: object) -> None:
        prefs = replace(self._session.preferences, appearance=str(mode))
        self._store_preferences(prefs)
        theme.apply_appearance(str(mode))
        self._sync_ui()

    def _set_slideshow_seconds(self, seconds: object) -> None:
        prefs = replace(self._session.preferences, slideshow_seconds=int(seconds))
        self._store_preferences(prefs)
        self._slideshow_timer.setInterval(int(seconds) * 1000)  # type: ignore[attr-defined]
        self._sync_ui()

    def _set_open_last_library(self, enabled: bool) -> None:
        prefs = replace(self._session.preferences, open_last_library=enabled)
        self._store_preferences(prefs)
        self._sync_ui()

    def _store_preferences(self, preferences: Preferences) -> None:
        self._session = self._session.with_preferences(preferences)  # type: ignore[attr-defined]
        if not self._save_session():  # type: ignore[attr-defined]
            self._show_banner("设置没能保存；这次运行仍然按新设置执行。")  # type: ignore[attr-defined]

    # ---- 库级：经 Library 写进 config.toml ------------------------------------

    def _set_movie_site(self, value: object) -> None:
        if self._library is None:
            return
        try:
            self._library.set_movie_site(str(value))  # type: ignore[attr-defined]
        except GalleryError as exc:
            self._report_error(exc.message)  # type: ignore[attr-defined]
            self._sync_ui()
            return
        self._sync_ui()

    def _ask_movie_site(self) -> None:
        library = self._library
        if library is None:
            return
        current = library.config.movie_site  # type: ignore[attr-defined]
        value, accepted = QInputDialog.getText(
            cast("QWidget", self),
            "影片网站",
            "番号页的地址前缀（例如 https://jable.tv/videos）：",
            text=current,
        )
        if accepted and value.strip():
            self._set_movie_site(value)
        else:
            # 取消时 Qt 已经把「自定义」勾上了（它是单选组里的一员），得按真正的值改回来。
            self._sync_ui()

    def _set_cache_limit(self, max_bytes: object) -> None:
        if self._library is None:
            return
        try:
            self._library.set_cache_limit(int(max_bytes))  # type: ignore[attr-defined]
        except GalleryError as exc:
            self._report_error(exc.message)  # type: ignore[attr-defined]
            self._sync_ui()
            return
        self._sync_ui()
        self._show_banner(f"缩略图缓存上限已改为{_cache_label(int(max_bytes))}。", auto_hide=True)

    # ---- 维护 ----------------------------------------------------------------

    def open_log_folder(self) -> None:
        """在访达里打开日志目录：用户报问题时唯一能自己拿到的证据。"""
        folder = paths.app_log_file().parent
        if not launch.open_folder(folder):
            self._show_banner(f"没能打开日志文件夹：{folder}")

    def show_about(self) -> None:
        """版本与当前库。**只读窗口上已有的状态，不碰任何文件。**"""
        library = self._library
        where = "还没打开照片文件夹" if library is None else str(library.root)  # type: ignore[attr-defined]
        QMessageBox.information(
            cast("QWidget", self),
            f"关于 {APP_NAME}",
            f"{APP_NAME} {__version__}\n\n照片文件夹：{where}",
        )

    # ---- 状态 → 勾选态 -------------------------------------------------------

    def _sync_settings(self) -> None:
        """把当前偏好与这个库的配置映射到勾选态（ADR 0050 的第六份映射）。"""
        prefs = self._session.preferences  # type: ignore[attr-defined]
        self._check("animation", prefs.resolved_animation_speed())
        self._check("appearance", prefs.resolved_appearance())
        self._check("slideshow", prefs.resolved_slideshow_seconds())
        self._settings_extras["open_last_library"].setChecked(prefs.open_last_library)
        library = self._library
        if library is None:
            self._uncheck("movie_site")
            self._uncheck("cache")
        else:
            site = library.config.movie_site  # type: ignore[attr-defined]
            self._check("movie_site", str(site).rstrip("/"), fallback=_CUSTOM)
            self._check("cache", library.config.max_cache_bytes)  # type: ignore[attr-defined]
        for action in self._library_setting_actions:
            action.setEnabled(library is not None)

    def _check(self, group: str, value: object, *, fallback: object | None = None) -> None:
        """勾上这一组的这一档；**值不在预设里就勾 ``fallback``**（手改过的配置）。"""
        if value in self._settings_actions[group]:
            for candidate, action in self._settings_actions[group].items():
                action.setChecked(candidate == value)
            return
        for candidate, action in self._settings_actions[group].items():
            action.setChecked(candidate == fallback)

    def _uncheck(self, group: str) -> None:
        for action in self._settings_actions[group].values():
            action.setChecked(False)


#: 「自定义…」那一行在动作表里的键 —— 它不是任何合法的配置值。
_CUSTOM = "<custom>"


def _cache_label(max_bytes: int) -> str:
    for size, label in CACHE_LIMIT_PRESETS:
        if size == max_bytes:
            return label
    return "自定义大小"


__all__ = [
    "APPEARANCE_LABELS",
    "CACHE_LIMIT_PRESETS",
    "MOVIE_SITE_PRESETS",
    "SLIDESHOW_SECONDS",
    "SPEED_LABELS",
    "SettingsFlowMixin",
]
