"""守护性测试：强制 AGENTS.md 中不可协商的架构约定。

这些测试的价值在于「失败得早」——把规范从文档里的文字变成 CI 会拦住的红灯。
"""

from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath
from typing import Final

import pytest

from gallery.core.constants import CONFIG_DIR_NAME

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "gallery"
CORE_ROOT = SRC_ROOT / "core"
UI_ROOT = SRC_ROOT / "ui"

#: 核心层禁止触碰的模块前缀。
FORBIDDEN_IN_CORE = ("PySide6", "PyQt5", "PyQt6", "shiboken6")

#: 界面层禁止直接使用的解码库：解码必须走 core，否则 HEIC、EXIF 方向、
#: 缓存策略会在两个地方各写一遍。
FORBIDDEN_IN_UI = ("PIL", "pillow_heif")


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: Path, *, full: bool = False) -> list[str]:
    """文件里导入的模块名（静态解析，不执行代码）。

    :param full: 保留点号后的全部（``gallery.ui.main_window``）；否则只取**第一段**
        （``gallery``、``PySide6``）—— 大多数检查问的是「有没有碰某个第三方库」，
        那一层就够。
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
#:
#: 白名单而不是黑名单：``Path.open()``、``os.open()``、``io.open()``、``codecs.open``
#: 乃至内置 ``open`` 的别名都要挡住，靠枚举「已知的坏形态」总会漏掉一种。
#: 而 ``Library.open()`` 是这个项目里**唯一**正当的「打开」——它校验并打开一个库，
#: 不碰用户的任何一个文件。
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
    与 ``exec``：界面层只许走 ``core`` 的门面，自己开一个口子就等于把
    「哪些操作会碰用户的文件」变成不可审计。唯一放行的是
    :data:`ALLOWED_METHOD_OPENS`。
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
    ``"gallery.ui"`` 永远比不中 —— 那这条测试就成了一道永远为绿的灯，而它守的
    恰恰是 AGENTS.md 那条「core 不得反向依赖界面」。
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


#: 菜单那三个模块。它们是纯展示：菜单该复用的是已有的动作，而不是自己去取数据
#: （ADR 0014）。它们一旦开始 import core，就说明「菜单」与「命令」两件事粘在一起了。
#:
#: ``menu_style``（菜单长什么样）与 ``menus``（菜单里有什么）原本是一个 1148 行的文件，
#: 按 ADR 0006 的判据「改了它会不会牵连另一个包」拆开之后，规则反而更好守了：
#: 一个只画的模块，比一个既画又拼的模块更明显地不该去碰 core。
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

    文档里**提到**配置目录名是在解释规矩（``conftest.py`` 那句「测试里也不该硬编码
    ``.gallery`` 字面量」就是），不该被自己这条守护当成违规。真正要拦的是拿它当
    **值**用的那些。
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

    这条是被三份一模一样的 ``library`` fixture 逼出来的：它们各自抄了一份
    ``tests/core/metadata/conftest.py`` 里已有的 fixture，三份里都写着
    ``(root / ".gallery").mkdir()`` —— 而那份 conftest 的文档偏偏写着「测试里也不该
    硬编码 ``.gallery`` 字面量」。上面的 :func:`test_config_dir_name_has_single_source`
    只扫 ``src/``，于是那句话没有任何东西在看着。

    为什么值得单开一条：常量改名时（哪怕是拼错成 ``.galley``），写死字面量的测试
    会在一个**与改名无关的目录**里凭空造出一个配置目录，然后照样全绿 ——
    测试自己也劈成了两份。

    **只认「当值用」**：文档字符串里提到它是在解释规矩，不算违规；打包标识符
    ``com.shaoxianshi.gallery`` 里那截也不是路径段。判据是这个字符串按路径切开之后
    有没有哪一段**正好等于**配置目录名。
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


#: ``Library`` 上那几个**真的会改用户文件**的方法（红线 1）。
#:
#: 界面上每一个调用点都应当在 ``ui/write_ops.py`` 里 —— 那一整个文件就是为了让
#: 「界面上会动用户文件的地方」变成一句可以 grep 的话，而不是散在三千行装配代码里
#: 只能靠人记。
WRITE_METHODS_ON_LIBRARY = (
    "rename_entry",
    "move_to_trash",
    "restore_entries",
    "copy_into",
    "move_into",
    "create_album",
    "undo_last_write",
)

#: 唯一一个「只组装、不写」的例外：它负责挑目标目录（见 ``_paste_target``）。
WRITE_OPS_MODULE = "write_ops.py"


def test_only_write_ops_calls_the_methods_that_change_user_files() -> None:
    """**界面上改用户文件的地方，只许在 ``ui/write_ops.py`` 里**（红线 1）。

    以前这些调用散在 ``main_window.py`` 的各个段落里，于是红线 1 的第一句
    「绝不删除、移动用户照片」在代码里只能靠人记 —— 没有一处能一眼看全。
    把它们收进 ``write_ops.py`` 之后，这句话变成了一条守护测试。

    注意判据是**方法名**而不是「文件在不在 ui/ 下」：读元数据、清缓存那些调用
    当然可以在别处，动的不是用户文件。
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
    这一层要是开始自己 ``open()`` / ``os.replace`` / ``shutil.move``，
    「每一次写都留了可回滚记录」这条就漏了一个地方 —— 而那正是最不能漏的地方。

    判据复用 :func:`_opens_files`（只放行 ``Library.open``），与
    :func:`test_ui_does_not_open_files` 用的是同一份。
    """
    path = UI_ROOT / WRITE_OPS_MODULE
    assert not _opens_files(path), f"{WRITE_OPS_MODULE} 自己打开文件了；写操作一律走 core"
