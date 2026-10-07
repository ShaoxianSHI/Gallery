"""守护性测试：把本仓库不可协商的架构约定变成会失败的红灯。

这些测试的价值在于「失败得早」——规范一旦只写在文档里，就没人看着了。
"""

from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath
from typing import Final

import pytest

from gallery.core.constants import CONFIG_DIR_NAME

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src" / "gallery"
CORE_ROOT = SRC_ROOT / "core"
UI_ROOT = SRC_ROOT / "ui"

#: 核心层禁止触碰的模块前缀。
FORBIDDEN_IN_CORE = ("PySide6", "PyQt5", "PyQt6", "shiboken6")

#: 界面层禁止直接使用的解码库：解码必须走 core，否则 HEIC、EXIF 方向、缓存策略
#: 会在两个地方各写一遍。
FORBIDDEN_IN_UI = ("PIL", "pillow_heif")


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: Path, *, full: bool = False) -> list[str]:
    """文件里导入的模块名（静态解析，不执行代码）。

    :param full: 保留点号后的全部（``gallery.ui.main_window``）；否则只取第一段。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = iter((node.module,))
        else:
            continue
        modules.extend(name if full else name.split(".")[0] for name in names)
    return modules


#: 界面层**允许**调用的 ``.open(...)``：它们不是「打开一个文件」，而是走 core 的门面。
#: 用白名单而不是枚举「已知的坏形态」（``Path.open()``、``os.open()``、内置 ``open``
#: 的别名……）—— 黑名单总会漏掉一种。
ALLOWED_METHOD_OPENS: Final = frozenset({"Library.open", "cls.open", "type(self).open"})


def _call_target(func: ast.expr) -> str | None:
    """把调用目标写成 ``A.b.c``；不是「名字 + 属性」链时返回 ``None``。"""
    parts: list[str] = []
    node: ast.expr = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _opens_files(path: Path) -> bool:
    """文件里是否直接打开了文件（静态解析）。

    挡住内置 ``open()``、``Path.open()``、``os.open`` / ``io.open`` / ``codecs.open``
    与 ``exec``；唯一放行的是 :data:`ALLOWED_METHOD_OPENS`。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in ("open", "exec"):
            return True
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "open"
            and _call_target(node.func) not in ALLOWED_METHOD_OPENS
        ):
            return True
    return False


def test_core_package_exists() -> None:
    assert CORE_ROOT.is_dir(), f"核心层目录缺失：{CORE_ROOT}"


def test_ui_package_exists() -> None:
    assert UI_ROOT.is_dir(), f"界面层目录缺失：{UI_ROOT}"


@pytest.mark.parametrize("path", _iter_python_files(CORE_ROOT), ids=lambda p: p.name)
def test_core_does_not_import_qt(path: Path) -> None:
    """core/ 必须保持平台无关、可脱离 GUI 测试。"""
    offenders = [m for m in _imported_modules(path) if m in FORBIDDEN_IN_CORE]
    assert not offenders, f"{path} 在核心层导入了 GUI 模块：{offenders}"


@pytest.mark.parametrize("path", _iter_python_files(CORE_ROOT), ids=lambda p: p.name)
def test_core_does_not_import_ui(path: Path) -> None:
    """分层是单向的：core 不允许反向依赖界面。

    **必须问完整模块名**：``_imported_modules`` 默认只给第一段，拿它去比
    ``"gallery.ui"`` 永远比不中 —— 那这条测试就成了一道永远为绿的灯。
    """
    offenders = [m for m in _imported_modules(path, full=True) if m.startswith("gallery.ui")]
    assert not offenders, f"{path} 反向依赖了界面层：{offenders}"


@pytest.mark.parametrize("path", _iter_python_files(UI_ROOT), ids=lambda p: p.name)
def test_ui_does_not_import_decoders(path: Path) -> None:
    """界面层不得自己解码图片：那是 core 的职责。"""
    offenders = [m for m in _imported_modules(path) if m in FORBIDDEN_IN_UI]
    assert not offenders, f"{path} 直接使用了图像解码库：{offenders}；请改为调用 gallery.core"


@pytest.mark.parametrize("path", _iter_python_files(UI_ROOT), ids=lambda p: p.name)
def test_ui_does_not_open_files(path: Path) -> None:
    """界面层不得自己打开文件，只能通过 core 的公开接口访问文件系统。"""
    assert not _opens_files(path), f"{path} 直接调用了 open()，请改为调用 gallery.core"


#: 菜单那三个模块是纯展示（ADR 0014）：它们一旦 import core，就说明「菜单」与
#: 「命令」两件事粘在一起了。
PRESENTATION_ONLY_UI = ("menus.py", "menu_style.py", "desktop.py")


def test_config_dir_name_is_exact() -> None:
    """配置目录名一旦拼错（如 .galley），用户的配置会被劈成两份。"""
    assert CONFIG_DIR_NAME == ".gallery"


def test_menu_modules_do_not_reach_into_core() -> None:
    """菜单拼装与访达调用都不许直接依赖 core（ADR 0014）。"""
    offenders = [
        path.name
        for path in _iter_python_files(UI_ROOT)
        if path.name in PRESENTATION_ONLY_UI
        and any(module.startswith("gallery.core") for module in _imported_modules(path, full=True))
    ]
    assert not offenders, f"{offenders} 直接依赖了 core；请让 MainWindow 把需要的东西递进来"


def test_config_dir_name_has_single_source() -> None:
    """除 constants.py 外，任何源码都不得硬编码配置目录名字面量。"""
    allowed = CORE_ROOT / "constants.py"
    offenders = [
        path
        for path in _iter_python_files(SRC_ROOT)
        if path != allowed and CONFIG_DIR_NAME in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"以下文件硬编码了 {CONFIG_DIR_NAME}，请改为导入常量：{offenders}"


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """所有文档字符串的 ``id()``：模块、类、函数的第一条字符串语句。

    文档里**提到**配置目录名是在解释规矩，不该被这条守护当成违规。
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def test_tests_do_not_hardcode_the_config_dir_name_either() -> None:
    """**测试也不许硬编码配置目录名** —— 用 ``paths.config_dir`` 或导入常量。

    写死字面量的测试会在常量改名时，于一个与改名无关的目录里凭空造出配置目录，
    然后照样全绿。**只认「当值用」**：文档字符串里提到它是在解释规矩，不算违规；
    判据是这个字符串按路径切开之后有没有哪一段正好等于配置目录名。
    """
    guard = Path(__file__).resolve()
    offenders: list[str] = []
    for path in _iter_python_files(guard.parent):
        if path == guard:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and CONFIG_DIR_NAME in PurePosixPath(node.value).parts
            ):
                offenders.append(f"{path.relative_to(guard.parent)}:{node.lineno}")
    assert not offenders, (
        f"以下测试把 {CONFIG_DIR_NAME} 当值用（不是当值用就没事），"
        f"请改用 paths.config_dir() 或导入常量：{offenders}"
    )


#: ``Library`` 上那几个**真的会改用户文件**的方法（红线 1）。界面上每一个调用点都应当
#: 在 ``ui/write_ops.py`` 里 —— 那一整个文件就是为了让这句话可以 grep。
WRITE_METHODS_ON_LIBRARY = (
    "rename_entry",
    "move_to_trash",
    "restore_entries",
    "copy_into",
    # 「复制」⌘D（ADR 0044）：造副本也是往用户的文件夹里写文件，与粘贴同一条红线。
    # **别漏掉它** —— 漏掉的话这个方法就是没有守护的，任何界面模块都能调它。
    "duplicate",
    "move_into",
    "create_album",
    "undo_last_write",
    # 元数据写回（ADR 0030）：改的是照片**里面**的字节。方法名必须与
    # ``facade.Library`` 上的逐字一致 —— 它扫的就是方法名。
    "write_metadata_edit",
)

#: 唯一一个「只组装、不写」的例外：它负责挑目标目录（见 ``_paste_target``）。
WRITE_OPS_MODULE = "write_ops.py"


def test_only_write_ops_calls_the_methods_that_change_user_files() -> None:
    """**界面上改用户文件的地方，只许在 ``ui/write_ops.py`` 里**（红线 1）。

    判据是**方法名**而不是「文件在不在 ui/ 下」：读元数据、清缓存那些调用当然
    可以在别处，动的不是用户文件。
    """
    offenders: list[str] = []
    for path in _iter_python_files(UI_ROOT):
        text = path.read_text(encoding="utf-8")
        for method in WRITE_METHODS_ON_LIBRARY:
            if f".{method}(" not in text:
                continue
            if path.name == WRITE_OPS_MODULE:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if f".{method}(" in line and not line.lstrip().startswith("#"):
                    offenders.append(f"{path.name}:{number} .{method}(")
    assert not offenders, f"界面上改用户文件的调用只许出现在 ui/{WRITE_OPS_MODULE}：{offenders}"


def test_write_ops_does_not_touch_the_file_system_itself() -> None:
    """**写操作那一半自己也不碰文件** —— 它只决定「什么时候调」，不决定「怎么动」。

    真正的写发生在 ``core/metadata/*`` 里，那里必记 journal、可 ⌘Z 撤销（红线 4）。
    判据复用 :func:`_opens_files`（只放行 ``Library.open``），与
    :func:`test_ui_does_not_open_files` 用的是同一份。
    """
    path = UI_ROOT / WRITE_OPS_MODULE
    assert not _opens_files(path), f"{WRITE_OPS_MODULE} 自己打开文件了；写操作一律走 core"


# ---- 编码：U+FFFD 替换字符 ----------------------------------------------------

#: U+FFFD。它在 UTF-8 里表示「这里本来有个字符，但我读不出来」。
#:
#: 用常量而不是字面量：**这条守护自己绝不能犯它守的毛病**。
REPLACEMENT_CHARACTER = "\ufffd"

#: 扫描时跳开的目录：虚拟环境、构建产物、缓存、用户自己的库目录。
EXCLUDED_DIRS: Final = frozenset({".venv", ".git", "build", "dist", "__pycache__", ".gallery"})

#: 按后缀认的文本文件。空后缀一并收下（``.python-version`` 那类无扩展名的配置）。
TEXT_SUFFIXES: Final = frozenset(
    {".py", ".pyi", ".toml", ".md", ".txt", ".cfg", ".ini", ".lock", ".tsv", ".csv"}
)


def _text_files_under(root: Path) -> list[Path]:
    """``root`` 下所有**确实是文本**的文件（跳开二进制与构建产物）。

    **「无扩展名」不等于「文本」**：``.DS_Store`` 就没有扩展名，按后缀收会把它的
    二进制字节读成一条「编码坏掉的中文」，把守护变成随机响的噪音。所以先按后缀挡掉
    已知的二进制，再按内容判 —— 含 NUL 的一律不是文本（UTF-8 与 UTF-16 的正文里都
    不会出现 NUL），且不许有解码错误。
    """
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or EXCLUDED_DIRS.intersection(path.parts):
            continue
        if path.suffix and path.suffix not in TEXT_SUFFIXES:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            continue  # 二进制（``.DS_Store``、图片、资源……）
        if data.decode("utf-8", errors="strict") is not None:
            found.append(path)
    return found


def _is_text_file(path: Path) -> bool:
    """这个文件是不是真的能当 UTF-8 文本读（判据见 :func:`_text_files_under`）。"""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return b"\x00" not in data


def test_no_text_file_carries_the_replacement_character() -> None:
    """**任何入库的文本文件里都不许有 U+FFFD** —— 一个字丢得无声无息。

    一个汉字在 UTF-8 里占三个字节，任何一个字节被截断、或某次编码转换走了岔，
    落地就是一个替换字符。它嵌在通顺的中文里，ruff、mypy、pytest 全都不会响：
    U+FFFD 本身是合法的 UTF-8 字符，``read_text()`` 不报错（要 ``errors="replace"``
    才会把它显出来）。
    """
    # 扫源码树、脚本、测试、文档与几个根文件。``src/gallery/resources/`` 下的应用图标与
    # 城市库是二进制（``.png`` / ``.gz``），按后缀就已经挡在门外了。
    files = [
        path
        for root in (
            PROJECT_ROOT / "src",
            PROJECT_ROOT / "tests",
            PROJECT_ROOT / "scripts",
            PROJECT_ROOT / "docs",
        )
        for path in _text_files_under(root)
    ]
    files += [
        path
        for path in (
            PROJECT_ROOT / "pyproject.toml",
            PROJECT_ROOT / "README.md",
            PROJECT_ROOT / "LICENSE",
        )
        if _is_text_file(path)
    ]

    offenders: list[str] = []
    for path in files:
        # ``errors="replace"``：文件已经坏了也要能**报出位置**，而不是抛一个异常。
        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            if REPLACEMENT_CHARACTER in line:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{number} {line.strip()[:60]}")
    assert not offenders, "以下文件里有编码坏掉的中文（U+FFFD），请改回原字：\n" + "\n".join(
        offenders
    )
