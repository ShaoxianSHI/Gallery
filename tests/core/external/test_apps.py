"""外部应用那一份名册：按 bundle id 认它们、它们的图标在包里的哪儿。

**不读本机真的装了什么** —— 开发机上装着 QSpace 与 ImageGlass、CI 上一样都没有，照真身写出来
的断言在两边必然有一边是红的。名册是假的，应用包在临时目录里现场造（与 ``test_pwa`` 同一套
做法）。
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from gallery.core.external import apps

PRO_ID, FREE_ID = apps.QSPACE.bundle_ids
PREVIEW, IMAGEGLASS = apps.VIEWERS


class _FakeURL:
    """假的 ``NSURL``：只需要 ``path()`` 这一句。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def path(self) -> str:
        return str(self._path)


class _FakeWorkspace:
    """假的 ``NSWorkspace``：只有「按 bundle id 问名册」这一句，且记下问了哪几个。"""

    def __init__(self, found: dict[str, Path]) -> None:
        self._found = found
        self.asked: list[str] = []

    def URLForApplicationWithBundleIdentifier_(  # noqa: N802 - 名字是 AppKit 定的
        self, bundle_id: str
    ) -> _FakeURL | None:
        self.asked.append(bundle_id)
        path = self._found.get(bundle_id)
        return None if path is None else _FakeURL(path)


class _FakeNSWorkspace:
    def __init__(self, workspace: _FakeWorkspace) -> None:
        self._workspace = workspace

    def sharedWorkspace(self) -> _FakeWorkspace:  # noqa: N802 - 名字是 AppKit 定的
        return self._workspace


class _FakeAppKit:
    """假的 AppKit 模块：入口只有 ``NSWorkspace.sharedWorkspace()``。"""

    def __init__(self, workspace: _FakeWorkspace) -> None:
        self.NSWorkspace = _FakeNSWorkspace(workspace)


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    """``find_app`` 是**只问一次**的（缓存）；每个用例开跑前清掉，免得上一个用例的答案串过来。"""
    apps.find_app.cache_clear()


def use(monkeypatch: pytest.MonkeyPatch, workspace: _FakeWorkspace) -> _FakeWorkspace:
    monkeypatch.setattr(apps, "_appkit", lambda: _FakeAppKit(workspace))
    return workspace


def make_app(
    root: Path,
    *,
    name: str = "QSpace Pro",
    bundle_id: str = PRO_ID,
    icon_name: str | None = "QSpace Pro.icns",
    icon_file: str | None = "QSpace Pro.icns",
    plist_bytes: bytes | None = None,
) -> Path:
    """在 ``root`` 下造一个应用包（plist 用 :mod:`plistlib` 写）。"""
    app = root / f"{name}.app"
    (app / apps.ICONS_DIR).mkdir(parents=True)
    info: dict[str, object] = {"CFBundleIdentifier": bundle_id}
    if icon_name is not None:
        info[apps.ICON_KEY] = icon_name
    (app / apps.INFO_PLIST).write_bytes(
        plistlib.dumps(info) if plist_bytes is None else plist_bytes
    )
    if icon_file is not None:
        (app / apps.ICONS_DIR / icon_file).write_bytes(b"\x00icon")
    return app


# ---- 装没装：按 bundle id 问名册 ---------------------------------------


def test_finds_the_app_by_bundle_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """判据是 bundle id，不是 ``.app`` 的名字 —— 包叫「QSpace」还是「QSpace Pro」都认得出。"""
    app = make_app(tmp_path)
    workspace = use(monkeypatch, _FakeWorkspace({PRO_ID: app}))
    assert apps.find_app(apps.QSPACE) == app
    assert workspace.asked == [PRO_ID]


def test_pro_wins_when_both_are_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """两个发行版都装着时认 Pro —— :attr:`apps.App.bundle_ids` 的次序就是这个意思。"""
    pro, free = make_app(tmp_path / "pro"), make_app(tmp_path / "free")
    use(monkeypatch, _FakeWorkspace({PRO_ID: pro, FREE_ID: free}))
    assert apps.find_app(apps.QSPACE) == pro


def test_a_viewer_is_also_found_by_bundle_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """图片应用走的是同一条路 —— 名字换发行版就变，bundle id 不变。"""
    glass = make_app(
        tmp_path, name="ImageGlass", bundle_id=IMAGEGLASS.bundle_ids[0], icon_file="app.icns"
    )
    workspace = use(monkeypatch, _FakeWorkspace({IMAGEGLASS.bundle_ids[0]: glass}))
    assert apps.find_app(IMAGEGLASS) == glass
    assert workspace.asked == [IMAGEGLASS.bundle_ids[0]]
    assert apps.find_app(PREVIEW) is None


def test_the_viewer_list_is_the_one_we_mean(tmp_path: Path) -> None:
    """那一列是**手写的白名单**，内容与次序都是有意的（ADR 0067）。

    名字可能是中文（「预览」），而它**只进菜单与横幅**：``open -a`` 不认中文名，递给它的是
    名册上那个路径 —— 这一条与 :func:`apps.find_app` 无关，但没有别处适合锁定这份名单。
    """
    assert [viewer.id for viewer in apps.VIEWERS] == ["preview", "imageglass"]
    assert [viewer.name for viewer in apps.VIEWERS] == ["预览", "ImageGlass"]
    assert PREVIEW.bundle_ids == ("com.apple.Preview",)
    assert IMAGEGLASS.bundle_ids == ("com.duongdieuphap.imageglass",)


def test_a_stale_registration_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """名册里留着一条指向已被删掉的应用的记录时跳过它，往下一个 bundle id 找。

    不判这一下的话，下拉框里会一直挂着一项，点下去什么都不会发生。
    """
    free = make_app(tmp_path)
    workspace = use(monkeypatch, _FakeWorkspace({PRO_ID: tmp_path / "早就删了.app", FREE_ID: free}))
    assert apps.find_app(apps.QSPACE) == free
    assert workspace.asked == [PRO_ID, FREE_ID]


def test_nothing_registered_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """没装只是「没装」，不是错误 —— 界面据此决定这一项不出现。"""
    workspace = use(monkeypatch, _FakeWorkspace({}))
    assert apps.find_app(apps.QSPACE) is None
    assert workspace.asked == list(apps.QSPACE.bundle_ids)


def test_without_pyobjc_it_is_simply_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 macOS（import AppKit 就失败）时静默当作没装，不把下拉框弄挂。"""
    monkeypatch.setattr(apps, "_appkit", lambda: None)
    assert apps.find_app(apps.QSPACE) is None


# ---- 图标：从应用包里读那一份 ------------------------------------------


def test_icon_is_the_bundle_one(tmp_path: Path) -> None:
    """**图标用应用包自带的那一份** —— 别人的商标不随包分发（ADR 0034 决策 6）。"""
    app = make_app(tmp_path)
    assert apps.icon_in(app) == app / apps.ICONS_DIR / "QSpace Pro.icns"


def test_icon_name_without_the_extension_is_still_found(tmp_path: Path) -> None:
    """``CFBundleIconFile`` 有的包写 ``QSpace Pro``、有的写 ``QSpace Pro.icns``，两种都认。"""
    app = make_app(tmp_path, icon_name="QSpace Pro")
    assert apps.icon_in(app) == app / apps.ICONS_DIR / "QSpace Pro.icns"


def test_damaged_plist_has_no_icon(tmp_path: Path) -> None:
    """plist 损坏只是「没有图标」，不是错误。"""
    app = make_app(tmp_path, plist_bytes=b"\x00not a plist at all")
    assert apps.icon_in(app) is None


def test_plist_without_the_icon_key_has_no_icon(tmp_path: Path) -> None:
    app = make_app(tmp_path, icon_name=None)
    assert apps.icon_in(app) is None


def test_missing_icon_file_is_not_an_error(tmp_path: Path) -> None:
    """plist 指着那一项、文件却不在（包正在被更新）：一样只是没有图标。"""
    app = make_app(tmp_path, icon_file=None)
    assert apps.icon_in(app) is None


def test_the_icon_needs_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """没装就没有图标可读 —— 界面那一层靠这个 ``None`` 退回自画的轮廓。"""
    monkeypatch.setattr(apps, "find_app", lambda app: None)
    assert apps.icon_path(apps.QSPACE) is None


def test_the_icon_follows_the_app_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path)
    use(monkeypatch, _FakeWorkspace({PRO_ID: app}))
    assert apps.icon_path(apps.QSPACE) == app / apps.ICONS_DIR / "QSpace Pro.icns"
