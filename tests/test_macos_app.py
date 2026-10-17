"""``gallery.macos_app`` 的测试：包结构必须摆对，摆放的规矩不能靠记忆。

重点守住三件事：

- ``CFBundleIconFile`` 指向的 ``.icns`` 真的在包里（否则 Dock 又变回通用图标）；
- 启动器 ``exec`` 的是**包内**那个符号链接（否则系统不认这个包），
  同时用 ``__PYVENV_LAUNCHER__`` 把解释器前缀指回 ``.venv``（否则依赖全丢）；
- 这个模块会删目录，所以只允许删「自己生成的包」，别的位置一律拒绝。
"""

from __future__ import annotations

import plistlib
import stat
import subprocess
from pathlib import Path

import pytest

from gallery import macos_app
from gallery.core.constants import APP_NAME, BUNDLE_IDENTIFIER
from gallery.macos_app import BundleError, build_bundle


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """一个像样的仓库根：有 ``pyproject.toml``、``src/gallery`` 与可用的 ``.venv``。"""
    root = tmp_path / "Gallery"
    (root / "src" / "gallery").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    interpreter = root / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(0o755)
    return root


@pytest.fixture
def fake_icns(tmp_path: Path) -> Path:
    """现成的 ``.icns``：测试不调用系统的 sips/iconutil。"""
    path = tmp_path / "Gallery.icns"
    path.write_bytes(b"icns-fake")
    return path


def _bundle(destination: Path) -> Path:
    return destination / "Contents"


def test_bundle_layout(project_root: Path, tmp_path: Path, fake_icns: Path) -> None:
    destination = build_bundle(tmp_path / "Gallery.app", project_root=project_root, icns=fake_icns)

    assert (destination / "Contents" / "Info.plist").is_file()
    assert (destination / "Contents" / "Resources" / "Gallery.icns").read_bytes() == b"icns-fake"
    launcher = destination / "Contents" / "MacOS" / "Gallery"
    assert launcher.is_file()
    assert launcher.stat().st_mode & stat.S_IXUSR


def test_info_plist_points_at_the_icon(project_root: Path, tmp_path: Path, fake_icns: Path) -> None:
    destination = build_bundle(tmp_path / "Gallery.app", project_root=project_root, icns=fake_icns)

    info = plistlib.loads((_bundle(destination) / "Info.plist").read_bytes())

    assert info["CFBundleName"] == APP_NAME
    assert info["CFBundleIdentifier"] == "com.example.gallery"
    assert info["CFBundleExecutable"] == "Gallery"
    assert info["CFBundlePackageType"] == "APPL"
    # Dock 图标就来自这一行，它指的文件必须在包里
    assert info["CFBundleIconFile"] == "Gallery.icns"
    assert (_bundle(destination) / "Resources" / info["CFBundleIconFile"]).is_file()
    assert info["NSHighResolutionCapable"] is True


def test_bundle_identifier_follows_the_app_name() -> None:
    """包标识符不能含大写字母，也不能和应用名脱节；组织名要改就改 ``core.constants``。

    **唯一来源在 core**：``core/external/recent_documents.py`` 要靠它认出「此刻跑的是不是
    真正的 Gallery.app」（ADR 0062），``macos_app`` 从那儿引进来。
    """
    assert macos_app.BUNDLE_IDENTIFIER == "com.example.gallery"
    assert macos_app.BUNDLE_IDENTIFIER.endswith(APP_NAME.lower())
    assert BUNDLE_IDENTIFIER == macos_app.BUNDLE_IDENTIFIER


def test_info_plist_says_it_can_open_folders(
    project_root: Path, tmp_path: Path, fake_icns: Path
) -> None:
    """声明「能打开文件夹」（ADR 0062）。

    ``LSHandlerRank`` 必须是 ``Alternate``：访达右键的「打开方式」里多一条 Gallery，
    而双击文件夹仍归访达 —— 抢走那一手就等于把用户的 Finder 换掉了。
    """
    destination = build_bundle(tmp_path / "Gallery.app", project_root=project_root, icns=fake_icns)

    info = plistlib.loads((_bundle(destination) / "Info.plist").read_bytes())

    (folder_types,) = info["CFBundleDocumentTypes"]
    assert folder_types["LSItemContentTypes"] == ["public.folder"]
    assert folder_types["LSHandlerRank"] == "Alternate"


def test_launcher_execs_the_in_bundle_symlink(
    project_root: Path, tmp_path: Path, fake_icns: Path
) -> None:
    """两件事必须成对：exec 包内的链接 + 把前缀指回 .venv。"""
    destination = build_bundle(tmp_path / "Gallery.app", project_root=project_root, icns=fake_icns)
    macos = destination / "Contents" / "MacOS"

    link = macos / macos_app.PYTHON_LINK_NAME
    assert link.is_symlink()
    assert link.readlink() == project_root / ".venv" / "bin" / "python"

    script = (macos / "Gallery").read_text(encoding="utf-8")
    assert f'export __PYVENV_LAUNCHER__="{project_root / ".venv" / "bin" / "python"}"' in script
    # 启动器自身是包内文件；exec 到包外的解释器，系统就不认这个包了
    exec_lines = [line for line in script.splitlines() if line.startswith("exec ")]
    assert exec_lines == ['exec "$HERE/python" -m gallery "$@"']


def test_launcher_restores_the_paths_gui_apps_are_missing(
    project_root: Path, tmp_path: Path, fake_icns: Path
) -> None:
    """从访达 / Dock 启动的进程 PATH 里没有 Homebrew，exiftool 会「凭空消失」。

    启动器必须在启动应用之前把它们补回去，否则 Gallery 会误报没装 exiftool。
    """
    destination = build_bundle(tmp_path / "Gallery.app", project_root=project_root, icns=fake_icns)
    script = (destination / "Contents" / "MacOS" / "Gallery").read_text(encoding="utf-8")

    lines = script.splitlines()
    fix = next(index for index, line in enumerate(lines) if line.startswith("PATH="))
    assert lines[fix] == f'PATH="$PATH:{":".join(macos_app.EXTRA_PATH_DIRS)}"'
    assert lines[fix + 1] == "export PATH"
    assert fix < lines.index('exec "$HERE/python" -m gallery "$@"')
    # Homebrew 的前缀必须在其中：exiftool 默认就装在这儿
    assert "/opt/homebrew/bin" in macos_app.EXTRA_PATH_DIRS


def test_rebuild_replaces_previous_bundle(
    project_root: Path, tmp_path: Path, fake_icns: Path
) -> None:
    destination = build_bundle(tmp_path / "Gallery.app", project_root=project_root, icns=fake_icns)
    stale = destination / "Contents" / "MacOS" / "stale"
    stale.write_text("上一版留下的", encoding="utf-8")

    build_bundle(destination, project_root=project_root, icns=fake_icns)

    assert not stale.exists()
    assert (destination / "Contents" / "Info.plist").is_file()


def test_refuses_to_delete_foreign_directory(
    project_root: Path, tmp_path: Path, fake_icns: Path
) -> None:
    """这个模块有权删东西，但不能靠猜：非本模块生成的目录一律不碰。"""
    destination = tmp_path / "我的照片"
    destination.mkdir()
    keep = destination / "IMG_0001.jpg"
    keep.write_bytes("用户的照片".encode())

    with pytest.raises(BundleError, match="换个位置"):
        build_bundle(destination, project_root=project_root, icns=fake_icns)

    assert keep.read_bytes() == "用户的照片".encode()


def test_missing_interpreter_is_a_clear_error(project_root: Path, tmp_path: Path) -> None:
    empty = tmp_path / "空仓库"
    empty.mkdir()

    with pytest.raises(BundleError, match="uv sync"):
        build_bundle(tmp_path / "Gallery.app", project_root=empty)


def test_missing_icon_source(
    tmp_path: Path, project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """图标源文件缺失要说清楚是哪一步没做，而不是让 sips 报一句天书。"""
    monkeypatch.setattr(macos_app, "app_icon_master", lambda: tmp_path / "没有这个.png")

    with pytest.raises(BundleError, match="找不到应用图标源文件"):
        build_bundle(tmp_path / "Gallery.app", project_root=project_root)


def test_find_project_root_walks_up(project_root: Path) -> None:
    nested = project_root / "src" / "gallery" / "ui"

    assert macos_app.find_project_root(nested) == project_root


def test_find_project_root_reports_plain_directory(tmp_path: Path) -> None:
    outside = tmp_path / "随便一个目录"
    outside.mkdir()

    with pytest.raises(BundleError, match="源码目录"):
        macos_app.find_project_root(outside)


def test_default_destination_is_under_dist(project_root: Path) -> None:
    assert macos_app.default_destination(project_root) == project_root / "dist" / "Gallery.app"


def test_make_icns_uses_the_master_icon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``.icns`` 由最大那档图标现生成；系统工具缺席时给出可行动的中文提示。"""
    master = tmp_path / "gallery-icon-1024.png"
    master.write_bytes(b"png-fake")
    destination = tmp_path / "Resources" / "Gallery.icns"
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[0] == "iconutil":
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"icns-generated")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(macos_app.subprocess, "run", fake_run)

    assert macos_app.make_icns(master, destination) == destination

    assert destination.read_bytes() == b"icns-generated"
    assert [command[0] for command in commands[: len(macos_app.ICONSET_ENTRIES)]] == ["sips"] * len(
        macos_app.ICONSET_ENTRIES
    )
    assert commands[-1][:3] == ["iconutil", "-c", "icns"]
    # iconset 是中间产物，不能留在包里
    assert not (destination.parent / ".Gallery.iconset").exists()


def test_make_icns_reports_missing_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    master = tmp_path / "gallery-icon-1024.png"
    master.write_bytes(b"png-fake")

    def missing(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(macos_app.subprocess, "run", missing)

    with pytest.raises(BundleError, match="sips"):
        macos_app.make_icns(master, tmp_path / "Gallery.icns")
