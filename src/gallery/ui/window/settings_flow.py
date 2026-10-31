"""「设置」菜单：界面与播放的偏好，以及这个库自己的几项配置（ADR 0073 / 0099 / 0110）。

五项放同一张菜单，按话题分五段（界面 · 放映 · 外部应用与启动 · 缓存 · 维护），
但**每一项存的地方各按各的**：应用级写会话文件，库级经 ``Library`` 写进 ``config.toml``。
**本模块一个用户的照片文件都不碰**；两个库级入口都只调 ``Library`` 的公开方法。
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox, QWidget

from gallery import __version__
from gallery.core import paths
from gallery.core.backdrops import BackdropError, BackdropStore, PaletteEntry
from gallery.core.backdrops.scrim import mean_lightness
from gallery.core.constants import APP_NAME
from gallery.core.errors import GalleryError
from gallery.core.external import launch
from gallery.core.i18n import t
from gallery.core.library.session import (
    EXTERNAL_PAGE_CALENDAR,
    EXTERNAL_PAGE_DETAIL,
    EXTERNAL_PAGE_GRID,
    EXTERNAL_PAGE_VIDEO,
    MUSIC_VOLUMES,
    Preferences,
)
from gallery.core.locales import LANGUAGE_NAMES
from gallery.ui import imaging, theme
from gallery.ui.dialogs import (
    BackdropGenerateDialog,
    BackdropManageDialog,
    BackdropNameDialog,
    BackgroundColorDialog,
    ExternalOrderDialog,
    OrderItem,
    OrderPage,
)
from gallery.ui.window.widgets.external_button import order_for_page

#: 影片网站预设：**只收「前缀 + 番号」拼得出地址的站点**。JavDB 按内部 id 或搜索参数走，
#: 拼 ``javdb.com/v/ipx-660`` 是死链，所以它当不了预设（它是抓取源，不是番号页）。
MOVIE_SITE_PRESETS: tuple[tuple[str, str], ...] = (
    ("https://jable.tv/videos", "Jable.tv"),
    ("https://missav.com/en", "MissAV"),
)

#: 缓存上限的界面档位（字节）。0 表示不限制；文案在 :func:`_cache_label` 里按当前语言取。
CACHE_LIMIT_PRESETS: tuple[int, ...] = (1 * 1024**3, 2 * 1024**3, 5 * 1024**3, 10 * 1024**3, 0)

SPEED_LABEL_KEYS: dict[str, str] = {
    "slow": "settings.speed.slow",
    "normal": "settings.speed.normal",
    "fast": "settings.speed.fast",
    "off": "settings.speed.off",
}
APPEARANCE_LABEL_KEYS: dict[str, str] = {
    "system": "settings.appearance.system",
    "light": "settings.appearance.light",
    "dark": "settings.appearance.dark",
}
SLIDESHOW_SECONDS: tuple[int, ...] = (3, 5, 8, 15)

#: 「外部应用顺序」对话框里那四个标签页 —— 与 ``PageRole`` 一一对应（ADR 0081 / 0094）。
EXTERNAL_PAGE_LABEL_KEYS: dict[str, str] = {
    EXTERNAL_PAGE_DETAIL: "settings.page.detail",
    EXTERNAL_PAGE_VIDEO: "settings.page.video",
    EXTERNAL_PAGE_GRID: "settings.page.grid",
    EXTERNAL_PAGE_CALENDAR: "settings.page.calendar",
}


@dataclass(slots=True)
class BackdropMenus:
    """背景图这一族：仓库、两档各一组子菜单、以及启动时自愈做过的事。

    **一个字段装下五样东西**：窗口上的耦合面按「用到一个新成员」计，而这几样从来一起出现。
    """

    store: BackdropStore
    menus: dict[bool, QMenu] = field(default_factory=dict)
    actions: dict[bool, dict[str, QAction]] = field(default_factory=dict)
    groups: dict[bool, QActionGroup] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class SettingsFlowMixin:
    """拼「设置」菜单、处理它的动作，并把当前值映射回勾选态。"""

    def build_settings_menu(self) -> None:
        """把「设置」挂到菜单栏末尾。五段：界面 · 放映 · 外部应用与启动 · 缓存 · 维护。"""
        menu = cast("QMenu", self.menuBar().addMenu(t("settings.title")))
        self._settings_extras: dict[str, QAction] = {}
        self._settings_actions: dict[str, dict[object, QAction]] = {}
        self._settings_actions["appearance"] = self._add_choice_menu(
            menu,
            t("settings.theme"),
            self._set_appearance,
            {value: t(key) for value, key in APPEARANCE_LABEL_KEYS.items()},
        )
        self._settings_actions["language"] = self._add_choice_menu(
            menu,
            t("settings.language"),
            self._set_language_preference,
            {"system": t("settings.language.system"), **LANGUAGE_NAMES},
        )
        self._build_background_menu(menu)
        self._settings_actions["animation"] = self._add_choice_menu(
            menu,
            t("settings.animation"),
            self._set_animation_speed,
            {value: t(key) for value, key in SPEED_LABEL_KEYS.items()},
        )
        menu.addSeparator()
        self._settings_actions["slideshow"] = self._add_choice_menu(
            menu,
            t("settings.slideshow"),
            self._set_slideshow_seconds,
            {seconds: t("settings.seconds", seconds=seconds) for seconds in SLIDESHOW_SECONDS},
        )
        self._build_slideshow_music_menu(menu)
        self._settings_actions["music_volume"] = self._add_choice_menu(
            menu,
            t("settings.music_volume"),
            self._set_music_volume,
            {value: _volume_label(value) for value in MUSIC_VOLUMES},
        )
        menu.addSeparator()
        self._settings_actions["movie_site"] = self._add_choice_menu(
            menu,
            t("settings.movie_site"),
            self._set_movie_site,
            {value: label for value, label in MOVIE_SITE_PRESETS},
            custom=True,
        )
        order_action = QAction(t("settings.external_order"), cast("QWidget", self))
        order_action.triggered.connect(self._edit_external_order)
        menu.addAction(order_action)
        self._settings_extras["external_order"] = order_action
        open_last = QAction(t("settings.open_last"), cast("QWidget", self))
        open_last.setCheckable(True)
        # 用 ``triggered`` 而不是 ``toggled``：同步勾选态时 setChecked 不该再写一次会话。
        open_last.triggered.connect(self._set_open_last_library)
        menu.addAction(open_last)
        self._settings_extras["open_last_library"] = open_last
        menu.addSeparator()
        self._settings_actions["cache"] = self._add_choice_menu(
            menu,
            t("settings.cache_limit"),
            self._set_cache_limit,
            {value: _cache_label(value) for value in CACHE_LIMIT_PRESETS},
        )
        # 预算与清扫放在一起：它们管的是同一批文件。
        menu.addAction(self._clear_cache_action)  # type: ignore[attr-defined]
        self._library_setting_actions = [
            *self._settings_actions["movie_site"].values(),
            *self._settings_actions["cache"].values(),
            self._settings_extras["slideshow_music_choose"],
        ]
        menu.addSeparator()
        log_action = QAction(t("settings.log_folder"), cast("QWidget", self))
        log_action.triggered.connect(self.open_log_folder)
        menu.addAction(log_action)
        self._settings_extras["log_folder"] = log_action
        about = QAction(t("settings.about", name=APP_NAME), cast("QWidget", self))
        about.triggered.connect(self.show_about)
        menu.addAction(about)
        self._settings_extras["about"] = about

    def _build_background_action(self) -> QAction:
        """「背景」里那一项：挑两档背景色（ADR 0097）。"""
        action = QAction(t("settings.background.solid"), cast("QWidget", self))
        action.triggered.connect(self._edit_backgrounds)
        self._settings_extras["background"] = action
        return action

    def _build_background_menu(self, parent: QMenu) -> QMenu:
        """「背景」子菜单：两档各挑一张图，或者退回纯色（ADR 0097 / 0098）。"""
        menu = parent.addMenu(t("settings.background"))
        for dark, key in ((False, "light"), (True, "dark")):
            self._backdrops.menus[dark] = QMenu(
                t(f"settings.background.{key}"), cast("QWidget", self)
            )
            menu.addMenu(self._backdrops.menus[dark])
        self._rebuild_backdrop_menus()
        menu.addSeparator()
        menu.addAction(self._build_background_action())
        for key, label, slot in (
            ("backdrop_generate", "settings.background.generate", self._generate_backdrop),
            ("backdrop_photo", "settings.background.photo", self._pick_backdrop_photo),
            ("backdrop_manage", "settings.background.manage", self._manage_backdrops),
        ):
            action = QAction(t(label), cast("QWidget", self))
            action.triggered.connect(slot)
            self._settings_extras[key] = action
            menu.addAction(action)
        return menu

    def _build_slideshow_music_menu(self, parent: QMenu) -> None:
        """「放映背景音乐」：当前那一首、挑一首、不再放（ADR 0109）。"""
        menu = parent.addMenu(t("settings.slideshow_music"))
        current = QAction(t("settings.slideshow_music.none"), cast("QWidget", self))
        current.setEnabled(False)
        menu.addAction(current)
        self._settings_extras["slideshow_music_current"] = current
        menu.addSeparator()
        choose = QAction(t("settings.slideshow_music.choose"), cast("QWidget", self))
        choose.triggered.connect(self._pick_slideshow_music)
        menu.addAction(choose)
        self._settings_extras["slideshow_music_choose"] = choose
        clear = QAction(t("settings.slideshow_music.clear"), cast("QWidget", self))
        clear.triggered.connect(self._clear_slideshow_music)
        menu.addAction(clear)
        self._settings_extras["slideshow_music_clear"] = clear

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
            custom_action = QAction(t("settings.custom"), cast("QWidget", self))
            custom_action.setCheckable(True)
            custom_action.triggered.connect(self._ask_movie_site)
            group.addAction(custom_action)
            menu.addAction(custom_action)
            actions[_CUSTOM] = custom_action
        parent.addMenu(menu)
        return actions

    def _edit_external_order(self) -> None:
        """打开四页的排序对话框；保存时把四份次序一起写进会话（ADR 0081 / 0094）。"""
        pages = tuple(
            self._external_order_page(page, t(key))
            for page, key in EXTERNAL_PAGE_LABEL_KEYS.items()
        )
        dialog = ExternalOrderDialog(cast("QWidget", self), pages)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        builtins = {page.page: page.builtin for page in pages}
        prefs = self._session.preferences  # type: ignore[attr-defined]
        for page, ids in dialog.orders().items():
            clean = _complete_order(ids, builtins[page])
            # 拖回内置次序就存空：不为一个等于默认的结果留下自定义记录。
            prefs = prefs.with_external_order(page, () if clean == builtins[page] else clean)
        self._store_preferences(prefs)
        self._sync_ui()

    def _external_order_page(self, page: str, label: str) -> OrderPage:
        """这一页能摆的那几项（装没装是 ``available`` 的事），按当前次序。"""
        entries = [
            entry
            for entry in self._external_button.entries()  # type: ignore[attr-defined]
            if page in entry.pages and entry.available()
        ]
        builtin = tuple(entry.id for entry in entries)
        ranked = {
            entry_id: index
            for index, entry_id in enumerate(
                order_for_page(self._session.preferences, page)  # type: ignore[attr-defined]
            )
        }
        ordered = sorted(entries, key=lambda entry: ranked.get(entry.id, len(ranked)))
        items = tuple(OrderItem(id=entry.id, name=entry.name, icon=entry.icon) for entry in ordered)
        return OrderPage(page=page, label=label, items=items, builtin=builtin)

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

    def _edit_backgrounds(self) -> None:
        """挑两档背景色（ADR 0097）；存的是应用级偏好，换库不重置。"""
        prefs = self._session.preferences  # type: ignore[attr-defined]
        dialog = BackgroundColorDialog(
            cast("QWidget", self),
            light=prefs.background_for(dark=False),
            dark=prefs.background_for(dark=True),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        light, dark = dialog.values()
        if (light, dark) == (prefs.background_for(dark=False), prefs.background_for(dark=True)):
            return
        # 两档各自写回（``with_background`` 一次只管一档，「清掉另一档」也要一并生效）。
        self._store_preferences(
            prefs.with_background(light, dark=False).with_background(dark, dark=True)
        )
        theme.ensure_installed().set_backgrounds(light=light, dark=dark)
        self._sync_ui()

    # ---- 背景图（ADR 0098）---------------------------------------------------

    def _init_backdrops(self, *, session_base: Path | None, cache_base: Path | None) -> None:
        """建背景库，顺手把缓存里丢掉的那几张补回来。窗口建界面时调一次。

        两个路径由组合根递进来（与 ``AppSession`` 同一对），于是这一族只往窗口上留一个字段。
        """
        store = BackdropStore(
            cache_dir=paths.app_backdrop_dir(base=cache_base),
            registry=paths.app_backdrop_registry(base=session_base),
        )
        self._backdrops = BackdropMenus(store=store, notes=list(store.repair()))  # type: ignore[attr-defined]

    def _apply_backdrops(self) -> None:
        """把两档选中的背景图装进主题。认不出来的 id 静默退回背景色。"""
        prefs = self._session.preferences  # type: ignore[attr-defined]
        images = {
            dark: self._load_backdrop(prefs.backdrop_for(dark=dark)) for dark in (False, True)
        }
        theme.ensure_installed().set_backdrops(light=images[False], dark=images[True])

    def _load_backdrop(self, backdrop_id: str) -> theme.BackdropImage | None:
        if not backdrop_id:
            return None
        item = self._backdrops.store.get(backdrop_id)  # type: ignore[attr-defined]
        if item is None:
            return None
        try:
            raster = self._backdrops.store.load(item)  # type: ignore[attr-defined]
        except BackdropError:
            return None
        return theme.BackdropImage(
            image=imaging.to_qimage(raster), mean_lightness=mean_lightness(raster)
        )

    def _assign_backdrop(self, dark: bool, backdrop_id: str) -> None:
        prefs = self._session.preferences.with_backdrop(backdrop_id, dark=dark)  # type: ignore[attr-defined]
        self._store_preferences(prefs)
        self._apply_backdrops()
        self._sync_ui()
        item = self._backdrops.store.get(backdrop_id) if backdrop_id else None  # type: ignore[attr-defined]
        if item is None:
            self._show_banner(  # type: ignore[attr-defined]
                t(f"settings.background.solid_banner.{'dark' if dark else 'light'}"),
                auto_hide=True,
            )
        else:
            self._show_banner(  # type: ignore[attr-defined]
                t(
                    f"settings.background.set_banner.{'dark' if dark else 'light'}",
                    name=item.name,
                ),
                auto_hide=True,
            )

    def _generate_backdrop(self) -> None:
        seed = random.SystemRandom().randrange(1, 2**31)
        store = self._backdrops.store  # type: ignore[attr-defined]
        dialog = BackdropGenerateDialog(cast("QWidget", self), seed=seed, entries=store.palettes())
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        self._write_palette_row(dialog.palette_row())
        if not accepted:
            return
        try:
            item = store.add_generated(dialog.recipe(), dialog.name())
        except BackdropError as exc:
            self._report_error(exc.message)  # type: ignore[attr-defined]
            return
        self._after_backdrop_saved(item, dialog.outcome())

    def _write_palette_row(self, entries: tuple[PaletteEntry, ...]) -> None:
        """配色是独立的一件事：**关掉生成对话框也算数**，调好的那一行下次还在（ADR 0105）。"""
        try:
            self._backdrops.store.save_palettes(entries)  # type: ignore[attr-defined]
        except BackdropError as exc:
            self._report_error(exc.message)  # type: ignore[attr-defined]

    def _pick_backdrop_photo(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            cast("QWidget", self),
            t("settings.background.pick_title"),
            str(Path.home()),
            t("settings.photo_filter"),
        )
        if not chosen:
            return
        source = Path(chosen)
        dialog = BackdropNameDialog(cast("QWidget", self), default_name=source.stem)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            item = self._backdrops.store.add_photo(source, dialog.name())  # type: ignore[attr-defined]
        except BackdropError as exc:
            self._report_error(exc.message)  # type: ignore[attr-defined]
            return
        self._after_backdrop_saved(item, dialog.outcome())

    def _after_backdrop_saved(self, item: object, outcome: str) -> None:
        """存下之后共用的一段：重建菜单，按用户的决定赋值。"""
        self._rebuild_backdrop_menus()
        if outcome in ("light", "dark"):
            self._assign_backdrop(outcome == "dark", item.id)  # type: ignore[attr-defined]
            return
        self._sync_ui()
        self._show_banner(  # type: ignore[attr-defined]
            t("settings.background.saved", name=item.name), auto_hide=True
        )

    def _manage_backdrops(self) -> None:
        items = tuple((entry.id, entry.name) for entry in self._backdrops.store.items())  # type: ignore[attr-defined]
        if not items:
            self._show_banner(t("settings.background.empty_banner"), auto_hide=True)  # type: ignore[attr-defined]
            return
        dialog = BackdropManageDialog(cast("QWidget", self), items)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        for backdrop_id in dialog.removed():
            self._backdrops.store.remove(backdrop_id)  # type: ignore[attr-defined]
        for backdrop_id, name in dialog.renames().items():
            try:
                self._backdrops.store.rename(backdrop_id, name)  # type: ignore[attr-defined]
            except BackdropError as exc:
                self._report_error(exc.message)  # type: ignore[attr-defined]
                break
        self._drop_missing_backdrops()
        self._rebuild_backdrop_menus()
        self._apply_backdrops()
        self._sync_ui()

    def _drop_missing_backdrops(self) -> None:
        """删掉的那张如果正被某一档用着，那一档退回背景色。"""
        known = {entry.id for entry in self._backdrops.store.items()}  # type: ignore[attr-defined]
        prefs = self._session.preferences  # type: ignore[attr-defined]
        for dark in (False, True):
            value = prefs.backdrop_for(dark=dark)
            if value and value not in known:
                prefs = prefs.with_backdrop("", dark=dark)
        if prefs != self._session.preferences:  # type: ignore[attr-defined]
            self._store_preferences(prefs)

    def _rebuild_backdrop_menus(self) -> None:
        """两档各自的背景图清单。**每次背景库一变就整份重建** —— 名字与顺序都由它决定。"""
        items = [(entry.id, entry.name) for entry in self._backdrops.store.items()]  # type: ignore[attr-defined]
        for dark, submenu in self._backdrops.menus.items():
            submenu.clear()
            group = QActionGroup(submenu)
            group.setExclusive(True)
            actions: dict[str, QAction] = {}
            for value, label in [("", t("settings.background.use_solid")), *items]:
                action = QAction(label, submenu)
                action.setCheckable(True)
                action.triggered.connect(
                    lambda _checked=False, target=value, slot=dark: self._assign_backdrop(
                        slot, target
                    )
                )
                group.addAction(action)
                submenu.addAction(action)
                actions[str(value)] = action
            if not items:
                empty = submenu.addAction(t("settings.background.empty"))
                empty.setEnabled(False)
            self._backdrops.actions[dark] = actions
            self._backdrops.groups[dark] = group

    def _set_slideshow_seconds(self, seconds: object) -> None:
        prefs = replace(self._session.preferences, slideshow_seconds=int(seconds))
        self._store_preferences(prefs)
        self._slideshow_timer.setInterval(int(seconds) * 1000)  # type: ignore[attr-defined]
        self._sync_ui()

    def _set_music_volume(self, percent: object) -> None:
        """音量与应用其余播放偏好一样是应用级的，换库不重置（ADR 0109）。"""
        prefs = replace(self._session.preferences, music_volume=int(percent))
        self._store_preferences(prefs)
        self._slideshow_music.set_volume(int(percent))  # type: ignore[attr-defined]
        self._sync_ui()

    def _set_language_preference(self, value: object) -> None:
        """语言是应用级偏好，**改完要重启才生效**（ADR 0106 决策 6）。"""
        prefs = replace(self._session.preferences, language=str(value))
        self._store_preferences(prefs)
        self._sync_ui()
        self._show_banner(t("settings.language.restart"), auto_hide=True)

    def _set_open_last_library(self, enabled: bool) -> None:
        prefs = replace(self._session.preferences, open_last_library=enabled)  # type: ignore[attr-defined]
        self._store_preferences(prefs)
        self._sync_ui()

    def set_show_filmstrip(self, enabled: bool) -> None:
        """视图菜单的「显示画廊」：应用级偏好，换库不重置（与文件扩展名同一类）。"""
        prefs = replace(self._session.preferences, show_filmstrip=enabled)  # type: ignore[attr-defined]
        self._store_preferences(prefs)
        self._detail.set_filmstrip_enabled(enabled)  # type: ignore[attr-defined]
        self._refresh_thumbnail_window()  # type: ignore[attr-defined]
        self._sync_ui()

    def set_show_file_extensions(self, enabled: bool) -> None:
        """视图菜单的「显示文件扩展名」：应用级偏好，换库不重置（ADR 0073 的判据）。"""
        prefs = replace(self._session.preferences, show_file_extensions=enabled)
        self._store_preferences(prefs)
        self._model.set_show_extensions(enabled)
        self._sync_ui()

    def _store_preferences(self, preferences: Preferences) -> None:
        self._session = self._session.with_preferences(preferences)  # type: ignore[attr-defined]
        if not self._save_session():  # type: ignore[attr-defined]
            self._show_banner(t("settings.save_failed"))  # type: ignore[attr-defined]

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
            t("settings.movie_site"),
            t("settings.movie_site.prompt"),
            text=current,
        )
        if accepted and value.strip():
            self._set_movie_site(value)
        else:
            # 取消时 Qt 已经把「自定义」勾上了（它是单选组里的一员），得按真正的值改回来。
            self._sync_ui()

    def _pick_slideshow_music(self) -> None:
        """挑一首歌写进这个库的 ``config.toml``；**文件一个字节都不动**（ADR 0109）。"""
        library = self._library
        if library is None:
            return
        current = library.slideshow_music  # type: ignore[attr-defined]
        start = str(current.parent) if current is not None else str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(
            cast("QWidget", self),
            t("settings.slideshow_music.pick_title"),
            start,
            t("settings.music_filter"),
        )
        if not chosen:
            return
        path = Path(chosen)
        try:
            library.set_slideshow_music(path)  # type: ignore[attr-defined]
        except GalleryError as exc:
            self._report_error(exc.message)  # type: ignore[attr-defined]
            self._sync_ui()
            return
        self._sync_ui()
        self._show_banner(t("settings.slideshow_music.selected", name=path.name), auto_hide=True)

    def _clear_slideshow_music(self) -> None:
        """不再放背景音乐：**正在放的那一首立刻停**，下一次放映也不会再起。"""
        library = self._library
        if library is None:
            return
        try:
            library.clear_slideshow_music()  # type: ignore[attr-defined]
        except GalleryError as exc:
            self._report_error(exc.message)  # type: ignore[attr-defined]
            self._sync_ui()
            return
        self._stop_slideshow_music()  # type: ignore[attr-defined]
        self._sync_ui()
        self._show_banner(t("settings.slideshow_music.cleared"), auto_hide=True)

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
        self._show_banner(
            t("settings.cache.set", label=_cache_label(int(max_bytes))), auto_hide=True
        )

    # ---- 维护 ----------------------------------------------------------------

    def open_log_folder(self) -> None:
        """在访达里打开日志目录：用户报问题时唯一能自己拿到的证据。"""
        folder = paths.app_log_file().parent
        if not launch.open_folder(folder):
            self._show_banner(t("settings.log_folder.failed", folder=folder))

    def show_about(self) -> None:
        """版本与当前库。**只读窗口上已有的状态，不碰任何文件。**"""
        library = self._library
        where = t("settings.no_library") if library is None else str(library.root)
        QMessageBox.information(
            cast("QWidget", self),
            t("settings.about", name=APP_NAME),
            t("settings.about.body", name=APP_NAME, version=__version__, where=where),
        )

    # ---- 状态 → 勾选态 -------------------------------------------------------

    def _sync_settings(self) -> None:
        """把当前偏好与这个库的配置映射到勾选态（ADR 0050 的第六份映射）。"""
        prefs = self._session.preferences  # type: ignore[attr-defined]
        self._check("animation", prefs.resolved_animation_speed())
        self._check("appearance", prefs.resolved_appearance())
        self._check("language", prefs.resolved_language())
        self._check("slideshow", prefs.resolved_slideshow_seconds())
        self._check("music_volume", prefs.resolved_music_volume())
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
        # 「当前那一首」与「不再放」比「有没有库」更细一层：没配歌时后者不该亮着。
        song = library.slideshow_music if library is not None else None  # type: ignore[attr-defined]
        if song is None:
            self._settings_extras["slideshow_music_current"].setText(
                t("settings.slideshow_music.none")
            )
            self._settings_extras["slideshow_music_clear"].setEnabled(False)
        else:
            self._settings_extras["slideshow_music_current"].setText(
                t("settings.slideshow_music.current", name=song.name)
            )
            self._settings_extras["slideshow_music_clear"].setEnabled(True)
        # 背景图那两组：认不出来的 id（背景库被清空过）按「用背景色」显示。
        for dark, actions in self._backdrops.actions.items():
            wanted = prefs.backdrop_for(dark=dark)
            chosen = wanted if wanted in actions else ""
            for value, action in actions.items():
                action.setChecked(value == chosen)

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
    if max_bytes <= 0:
        return t("settings.cache.unlimited")
    if max_bytes in CACHE_LIMIT_PRESETS:
        return f"{max_bytes // 1024**3} GB"
    return t("settings.cache.custom")


def _volume_label(percent: int) -> str:
    if percent == 0:
        return t("settings.music_volume.mute")
    return t("settings.music_volume.percent", percent=percent)


def _complete_order(ids: Sequence[str], builtin: Sequence[str]) -> tuple[str, ...]:
    """把对话框交出来的 id 收成一个**一个不缺的排列**：去重、丢掉不认识的，缺的补在最后。

    正常路径上它什么也不改。留着它是为了让「对话框少了一行」这种缺陷只让那一项排到最后，
    而不是变成一份缺项设置。
    """
    known = set(builtin)
    ordered: list[str] = []
    for entry_id in ids:
        if entry_id in known and entry_id not in ordered:
            ordered.append(entry_id)
    ordered.extend(entry_id for entry_id in builtin if entry_id not in ordered)
    return tuple(ordered)


__all__ = [
    "APPEARANCE_LABEL_KEYS",
    "CACHE_LIMIT_PRESETS",
    "EXTERNAL_PAGE_LABEL_KEYS",
    "MOVIE_SITE_PRESETS",
    "SLIDESHOW_SECONDS",
    "SPEED_LABEL_KEYS",
    "SettingsFlowMixin",
]
