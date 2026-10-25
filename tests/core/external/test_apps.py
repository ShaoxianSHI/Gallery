"""外部应用那一份名册：按 bundle id 认它们、跟系统要它们的图标。

**不读本机真的装了什么、也不真去画图标** —— 开发机上装着 QSpace 与 ImageGlass、CI 上一样都没有，
照真身写出来的断言在两边必然有一边是红的。名册是假的，应用包在临时目录里现场造，AppKit 也是
假的（与 ``test_pwa`` 同一套做法）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gallery.core.external import apps

PRO_ID, FREE_ID = apps.QSPACE.bundle_ids

#: 假的 AppKit「画」出来的那份 PNG 字节：只要能被一眼认出来就够。
FAKE_PNG = b"\x89PNG\r\n\x1a\n-fake"


class _FakeURL:
    """假的 ``NSURL``：只需要 ``path()`` 这一句。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def path(self) -> str:
        return str(self._path)


class _FakeData:
    """假的 ``NSData``：``bytes()`` 拿得到里面那份字节。"""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __bytes__(self) -> bytes:
        return self._payload


class _FakeImage:
    """假的 ``NSImage``：既是 ``iconForFile_`` 给回来的那张原图，也是我们要建的那块画布。

    **记下缩到了多大** —— 那句正是 :func:`apps.icon_png` 唯一容易写错的地方：整张原图是 1024，
    编码一次一百多毫秒、快 2 MB，不缩就往上层递。
    """

    def __init__(self, workspace: _FakeWorkspace) -> None:
        self._workspace = workspace

    def initWithSize_(self, size: tuple[int, int]) -> _FakeImage:  # noqa: N802 - 名字是 AppKit 定的
        self._workspace.canvas_sizes.append(size)
        return self

    def lockFocus(self) -> None:  # noqa: N802 - 名字是 AppKit 定的
        self._workspace.focus_calls.append("lock")

    def unlockFocus(self) -> None:  # noqa: N802 - 名字是 AppKit 定的
        self._workspace.focus_calls.append("unlock")

    def drawInRect_(self, rect: tuple[int, int, int, int]) -> None:  # noqa: N802 - 名字是 AppKit 定的
        self._workspace.drawn_rects.append(rect)

    def TIFFRepresentation(self) -> _FakeData:  # noqa: N802 - 名字是 AppKit 定的
        return _FakeData(b"TIFF")


class _FakeImageRep:
    """假的 ``NSBitmapImageRep``：编码那一步给回来的字节由假 workspace 说了算。"""

    def __init__(self, workspace: _FakeWorkspace) -> None:
        self._workspace = workspace

    def representationUsingType_properties_(self, kind, props):  # noqa: N802 - 名字是 AppKit 定的
        self._workspace.encoded_kinds.append(kind)
        return None if self._workspace.png is None else _FakeData(self._workspace.png)


class _FakeWorkspace:
    """假的 ``NSWorkspace``：按 bundle id 问名册、按路径要图标，两样都记下来。"""

    def __init__(self, found: dict[str, Path], *, icon: bytes | None = FAKE_PNG) -> None:
        self._found = found
        self.png = icon
        self.asked: list[str] = []
        self.icon_paths: list[str] = []
        self.canvas_sizes: list[tuple[int, int]] = []
        self.drawn_rects: list[tuple[int, int, int, int]] = []
        self.focus_calls: list[str] = []
        self.encoded_kinds: list[str] = []

    def URLForApplicationWithBundleIdentifier_(  # noqa: N802 - 名字是 AppKit 定的
        self, bundle_id: str
    ) -> _FakeURL | None:
        self.asked.append(bundle_id)
        path = self._found.get(bundle_id)
        return None if path is None else _FakeURL(path)

    def iconForFile_(self, path: str) -> _FakeImage | None:  # noqa: N802 - 名字是 AppKit 定的
        self.icon_paths.append(path)
        return None if self.png is None else _FakeImage(self)


class _FakeNSWorkspace:
    def __init__(self, workspace: _FakeWorkspace) -> None:
        self._workspace = workspace

    def sharedWorkspace(self) -> _FakeWorkspace:  # noqa: N802 - 名字是 AppKit 定的
        return self._workspace


class _FakeNSImageClass:
    """假的 ``NSImage`` 类：``alloc()`` 造出来的就是一块（假的）画布。"""

    def __init__(self, workspace: _FakeWorkspace) -> None:
        self._workspace = workspace

    def alloc(self) -> _FakeImage:
        return _FakeImage(self._workspace)


class _FakeNSBitmapImageRepClass:
    def __init__(self, workspace: _FakeWorkspace) -> None:
        self._workspace = workspace

    def imageRepWithData_(self, data: _FakeData) -> _FakeImageRep:  # noqa: N802 - 名字是 AppKit 定的
        return _FakeImageRep(self._workspace)


class _FakeAppKit:
    """假的 AppKit 模块：只有 :func:`apps.icon_png` 用得到的那几样。"""

    def __init__(self, workspace: _FakeWorkspace) -> None:
        self.NSWorkspace = _FakeNSWorkspace(workspace)
        self.NSImage = _FakeNSImageClass(workspace)
        self.NSBitmapImageRep = _FakeNSBitmapImageRepClass(workspace)
        self.NSBitmapImageFileTypePNG = "PNG"

    @staticmethod
    def NSMakeSize(width: int, height: int) -> tuple[int, int]:  # noqa: N802 - 名字是 AppKit 定的
        return (width, height)

    @staticmethod
    def NSMakeRect(  # noqa: N802 - 名字是 AppKit 定的
        x: int, y: int, width: int, height: int
    ) -> tuple[int, int, int, int]:
        return (x, y, width, height)


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    """``find_app`` 是**只问一次**的（缓存）；每个用例开跑前清掉，免得上一个用例的答案串过来。"""
    apps.find_app.cache_clear()


def use(monkeypatch: pytest.MonkeyPatch, workspace: _FakeWorkspace) -> _FakeWorkspace:
    monkeypatch.setattr(apps, "_appkit", lambda: _FakeAppKit(workspace))
    return workspace


def make_app(root: Path, name: str = "QSpace Pro") -> Path:
    """在 ``root`` 下造一个应用包 —— **只要是个目录就行**：判据是名册，不是包里的内容。"""
    app = root / f"{name}.app"
    (app / "Contents").mkdir(parents=True)
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


def test_a_viewer_is_also_found_by_bundle_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """图片应用走的是同一条路 —— 名字换发行版就变，bundle id 不变。"""
    glass = make_app(tmp_path, "ImageGlass")
    workspace = use(monkeypatch, _FakeWorkspace({apps.IMAGEGLASS.bundle_ids[0]: glass}))
    assert apps.find_app(apps.IMAGEGLASS) == glass
    assert workspace.asked == [apps.IMAGEGLASS.bundle_ids[0]]
    assert apps.find_app(apps.PREVIEW) is None


def test_without_pyobjc_it_is_simply_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 macOS（``import AppKit`` 就失败）时静默当作没装，不把下拉框弄挂。"""
    monkeypatch.setattr(apps, "_appkit", lambda: None)
    assert apps.find_app(apps.QSPACE) is None
    assert apps.icon_png(apps.QSPACE) is None


def test_the_viewer_list_is_the_one_we_mean() -> None:
    """那一列是**手写的白名单**（ADR 0067）。名字可能是中文，而它**只进菜单与横幅**：

    ``open -a`` 不认中文名，递给它的是名册上那个路径 —— 这一条与 :func:`apps.find_app` 无关，
    但没有别处适合锁定这份名单。
    """
    assert apps.VIEWERS == (apps.PREVIEW, apps.IMAGEGLASS, apps.PICVIEW)
    assert [viewer.id for viewer in apps.VIEWERS] == ["preview", "imageglass", "picview"]
    assert apps.PREVIEW.name == "预览"
    assert apps.PREVIEW.bundle_ids == ("com.apple.Preview",)
    assert apps.IMAGEGLASS.bundle_ids == ("com.duongdieuphap.imageglass",)
    assert apps.PICVIEW.bundle_ids == ("com.chitaner.Picview",)


# ---- 图标：跟系统要它画好的那一张 --------------------------------------


def test_the_icon_is_asked_of_the_system(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**按应用包路径问系统要图标**，拿到的是它画好的那份 PNG 字节。

    不自己从包里那个 ``.icns`` 拆：同一个包，系统在 Dock 里画的那一张与文件里存的那一份可以
    不一样（老式图标把圆角外面那片填成不透明白色），自己拆就把那圈白带出来了。
    """
    app = make_app(tmp_path)
    workspace = use(monkeypatch, _FakeWorkspace({PRO_ID: app}))
    assert apps.icon_png(apps.QSPACE) == FAKE_PNG
    assert workspace.icon_paths == [str(app)]
    assert workspace.encoded_kinds == ["PNG"]


def test_the_icon_is_drawn_at_a_small_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """缩到 :data:`apps.ICON_EDGE` 再编码 —— 菜单里最大也就 16 点，用不着那张 1024 的原图。"""
    app = make_app(tmp_path)
    workspace = use(monkeypatch, _FakeWorkspace({PRO_ID: app}))
    apps.icon_png(apps.QSPACE)
    assert workspace.canvas_sizes == [(apps.ICON_EDGE, apps.ICON_EDGE)]
    assert workspace.drawn_rects == [(0, 0, apps.ICON_EDGE, apps.ICON_EDGE)]
    assert workspace.focus_calls == ["lock", "unlock"]


def test_a_viewer_icon_is_asked_of_the_system_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    glass = make_app(tmp_path, "ImageGlass")
    workspace = use(monkeypatch, _FakeWorkspace({apps.IMAGEGLASS.bundle_ids[0]: glass}))
    assert apps.icon_png(apps.IMAGEGLASS) == FAKE_PNG
    assert apps.icon_png(apps.PREVIEW) is None
    assert workspace.icon_paths == [str(glass)]


def test_an_app_that_is_not_installed_has_no_icon(monkeypatch: pytest.MonkeyPatch) -> None:
    """没装就没有图标可要 —— 界面那一层靠这个 ``None`` 退回自画的轮廓。"""
    workspace = use(monkeypatch, _FakeWorkspace({}))
    assert apps.icon_png(apps.QSPACE) is None
    assert workspace.icon_paths == []


def test_a_system_that_hands_back_nothing_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """系统给不出图（``iconForFile_`` 返回 nil、编码器不吐字节）只是「没有图标」。"""
    app = make_app(tmp_path)
    use(monkeypatch, _FakeWorkspace({PRO_ID: app}, icon=None))
    assert apps.icon_png(apps.QSPACE) is None


def test_a_broken_appkit_call_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**少一个图标不该把窗口弄挂**：AppKit 那几步里任何一步抛，都只是退回自画的轮廓。"""
    app = make_app(tmp_path)
    workspace = _FakeWorkspace({PRO_ID: app})

    def explode(path: str) -> None:
        raise ValueError("画不出来")

    monkeypatch.setattr(workspace, "iconForFile_", explode)
    use(monkeypatch, workspace)
    assert apps.icon_png(apps.QSPACE) is None
