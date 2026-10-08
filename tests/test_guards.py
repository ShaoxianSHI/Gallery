"""守护性测试：把本仓库不可协商的架构约定变成会失败的红灯。

这些测试的价值在于「失败得早」——规范一旦只写在文档里，就没人看着了。
"""

from __future__ import annotations

import ast
import tokenize
from pathlib import Path, PurePosixPath
from typing import Final

import pytest

from gallery.core.constants import CONFIG_DIR_NAME
from gallery.core.metadata.exif import (
    COORDINATE_TAGS,
    IPTC_PLACE_TAGS,
    WANTED_PLACE_TAGS,
    XMP_PLACE_TAGS,
)

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


def _tag_literals_outside(allowed: Path, tags: set[str]) -> list[str]:
    """源码里出现的这些标签**字符串字面量**，除了 ``allowed`` 那个文件。

    只看 ``ast.Constant`` 的字符串节点，且跳开文档字符串 —— 注释里拿它举例是在解释规矩。
    """
    guard = Path(__file__).resolve()
    offenders: list[str] = []
    for path in _iter_python_files(SRC_ROOT):
        if path in (allowed, guard):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and node.value in tags
            ):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} {node.value}")
    return offenders


#: 两个「一族标签只有一份来源」的清单：``(名字, 唯一来源, 全部标签)``。
#: 标签本身**从表里取**，于是给表加一项也自动被守着。
TAG_FAMILIES = (
    (
        "坐标",
        CORE_ROOT / "metadata" / "exif.py",
        COORDINATE_TAGS,
        "exif.COORDINATE_AXES",
        "少写一个的症状不是「少个字段」——是另一半留着**上一个位置**的值，一个自信的错答案。",
    ),
    (
        "地名",
        CORE_ROOT / "metadata" / "exif.py",
        (*XMP_PLACE_TAGS, *IPTC_PLACE_TAGS, *WANTED_PLACE_TAGS),
        "exif.PLACE_FIELDS",
        "三族标签（IPTC / XMP 一级名 / XMP 全名）原先在四处各写一遍，而漏一处的症状是"
        "「某类照片的地点字段永远是空的」——一个不报错、只是不对的答案。",
    ),
)


@pytest.mark.parametrize(
    ("name", "allowed", "tags", "table", "why"),
    TAG_FAMILIES,
    ids=[family[0] for family in TAG_FAMILIES],
)
def test_a_tag_family_has_a_single_source(
    name: str, allowed: Path, tags: tuple[str, ...], table: str, why: str
) -> None:
    """**一族标签只在 ``exif`` 里那张表写一遍。**

    判据是**源码里不再出现这些字符串字面量**（字符串节点，注释与文档字符串不算），
    只有表里那一处 —— 「读起来像同源」不算数。
    """
    offenders = _tag_literals_outside(allowed, set(tags))

    assert not offenders, f"{name}标签只许在 {table} 里写一遍，别处请从它取。{why}\n" + "\n".join(
        offenders
    )


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


# ---- mixin：同名方法会静默失效 ------------------------------------------------


def test_two_main_window_mixins_never_define_the_same_method() -> None:
    """**``MainWindow`` 的两个 mixin 不许有同名方法** —— Python 按 MRO 取第一个实现。

    这不是风格问题，是**静默失效**：后写的那个方法永远不会被调用，而没有任何报错。
    事件封面的 ``event_cover_candidate`` 就踩过一次 —— 它本来也叫 ``cover_candidate``，
    而 ``WriteOpsMixin``（相册封面，ADR 0028）在基类列表里排在 ``EventFlowMixin`` 前面，
    于是症状是「菜单里压根没有这一条」，查起来像是判据写错了。

    **只扫 ``MainWindow`` 真正继承的那几个 mixin**，不扫别的类：``ui/`` 下大量 QWidget 子类
    各自都有 ``paintEvent`` / ``__init__``，那是继承关系里正常的事，与 MRO 冲突无关。
    """
    import ast

    bases = _main_window_bases()
    owners: dict[str, list[str]] = {}
    for class_name in bases:
        module = _module_of_mixin(class_name)
        if module is None:
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name != class_name:
                continue
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    owners.setdefault(item.name, []).append(class_name)

    clashes = {name: names for name, names in owners.items() if len(names) > 1}
    assert not clashes, (
        f"这些方法名出现在 MainWindow 的不止一个 mixin 里（MRO 只认第一个）：{clashes}"
    )


def _main_window_bases() -> tuple[str, ...]:
    """``MainWindow`` 继承的那些 mixin（Qt 基类不算）。"""
    import ast

    tree = ast.parse((UI_ROOT / "main_window.py").read_text(encoding="utf-8"))
    window = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    return tuple(
        base.id  # type: ignore[union-attr]
        for base in window.bases
        if isinstance(base, ast.Name) and base.id.endswith("Mixin")
    )


def _module_of_mixin(class_name: str) -> Path | None:
    """这个 mixin 住在哪个文件里。``ui/window/`` 与 ``ui/`` 顶层各找一遍。

    **认的是 ``class X`` 那一行，不要求有括号** —— ``class EventFlowMixin:``（无基类）
    与 ``class WriteOpsMixin:`` 都是那种写法，而带括号的判据会把它们整个漏掉，
    于是守护在干净树上「通过」而其实什么都没查（这一条第一版就是这么写的）。
    """
    import re

    pattern = re.compile(rf"^class {re.escape(class_name)}\b", re.MULTILINE)
    for candidate in sorted(UI_ROOT.glob("**/window/*.py")) + sorted(UI_ROOT.glob("*.py")):
        if pattern.search(candidate.read_text(encoding="utf-8")):
            return candidate
    return None


def test_the_mixin_guard_would_notice_a_clash() -> None:
    """守护自己必须验过一次**：往一个 mixin 里注入一个同名方法，那条守护要红。

    没有这一步，一条「永远为绿」的守护比没有更坏 —— 它让人以为 MRO 那件事有人看着。
    """
    bases = set(_main_window_bases())
    assert len(bases) >= 2, "MainWindow 至少要有两个 mixin，这条守护才验得到"
    clean = {name: names for name, names in _mixin_method_owners(bases).items() if len(names) > 1}
    assert not clean, f"守护自己没验过：干净树上就已经有同名方法了（{clean}）"

    mixin = UI_ROOT / "window" / "event_flow.py"
    original = mixin.read_text(encoding="utf-8")
    # **注入一个别的 mixin 已经有了的名字** —— 撞车要两个才成立，只往一个里塞不算。
    victim = "_needs_facet_read"
    injected = _inject_into_class(original, "EventFlowMixin", victim)
    assert injected != original, "没找到 EventFlowMixin 的类体"
    try:
        mixin.write_text(injected, encoding="utf-8")
        probe = _mixin_method_owners(bases)
        assert len(probe.get(victim, [])) > 1, "注入了同名方法，守护却没报出来"
    finally:
        mixin.write_text(original, encoding="utf-8")


def _inject_into_class(source: str, class_name: str, method: str) -> str:
    """在 ``class_name`` 的类体末尾插一个方法。

    **按 AST 找类体的末尾** —— 追加到文件末尾不行：这个文件里那个类后面还跟着几个模块级
    函数，于是新方法落在类外面，解析时压根不算它的一个方法（第一版就是这么写的，守护没红）。
    """
    import ast

    tree = ast.parse(source)
    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.ClassDef) and item.name == class_name
    )
    lines = source.splitlines()
    return (
        "\n".join(
            [
                *lines[: node.end_lineno],
                f"    def {method}(self) -> None: ...",
                *lines[node.end_lineno :],
            ]
        )
        + "\n"
    )


def _mixin_method_owners(bases: set[str]) -> dict[str, list[str]]:
    """方法名 → 定义了它的那些 mixin。**同一份解析，两处用例共用。**"""
    import ast

    owners: dict[str, list[str]] = {}
    for class_name in bases:
        module = _module_of_mixin(class_name)
        if module is None:
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                        owners.setdefault(item.name, []).append(class_name)
    return owners


# ---- 注释：别让它长成事故复盘 -----------------------------------------------

#: 行内注释的连续行数上限。**到第三行就该拆成一个有名字的函数，或者删掉一半。**
MAX_INLINE_COMMENT_LINES: Final = 2

#: 放行标记。写在**那一段的第一行**，后面跟一句「凭什么」——
#: 没有理由的放行等于没有放行，那只是把守护关掉。
LONG_COMMENT_PRAISMA: Final = "guard: allow-long-comment"

#: 考古式的**整句**短语。用短语而不用单词，是因为单词在这个仓库里大量正当用法：
#: ``原来是`` 是 journal 里的领域词（「原来没有」对「原来是空串」）、``曾经`` 在
#: :func:`paths.legacy_cache_dir` 里解释那条函数为什么还在、``实测`` 是三十多处性能数字的
#: 出处（红线与性能数字本来就允许写进注释）。按单词拉黑会误伤它们，而误报的守护会被人忽略 ——
#: 那比没有守护更坏。所以这里只收**不可能正当使用**的整句。
ARCHAEOLOGY_PHRASES: Final = ("已作废", "原来写的是", "别再照着", "那一版转给")


def _function_spans(tree: ast.Module) -> list[tuple[int, int, str]]:
    """每个函数体的 ``(起始行, 结束行, 函数名)``，嵌套的也各算一份。

    起始行取 ``def`` 那一行而不是 ``body[0].lineno`` —— 后者是**第一条语句**，于是写在
    函数体最开头的那几行注释（最常见的位置）会落在区间之外，一条都量不到。
    ``def`` 行本身不是注释，所以拿它当区间起点不会把函数上方的说明算进来。
    """
    spans: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append((node.lineno, node.end_lineno or node.lineno, node.name))
    return spans


def _comment_lines(path: Path) -> dict[int, str]:
    """行号 → 该行**整行注释**的正文（``#`` 与空格已去掉）。

    用 :mod:`tokenize` 而不是扫原文：它认得引号里的 ``#`` 不是注释，也认得字符串跨行。

    **只收整行的，挂在代码末尾的那种不算** —— 行尾注释天然只有一行，凑不成一段话，
    而它紧挨着上一段话的时候会被当成那一段的一部分（``scanner.describe_entry`` 就是
    这么被算成三行的）。
    """
    found: dict[int, str] = {}
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type != tokenize.COMMENT:
                continue
            number, column = token.start
            if not 1 <= number <= len(lines):
                continue  # 越界就当没有 —— 守护崩掉比守护漏掉更坏
            if column and lines[number - 1][:column].strip():
                continue  # 前面有代码 —— 行尾注释
            found[number] = token.string.lstrip("#").strip()
    return found


def _long_comment_blocks(path: Path) -> list[str]:
    """这个文件里过长的行内注释块，每条给出 ``函数名:起始行（行数）``。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    spans = _function_spans(tree)
    comments = _comment_lines(path)

    # 按**函数体的区间**归堆而不是按函数名：一个文件里有两个 ``__init__``（不同类里的）是
    # 常事，按名字归堆会把它们的注释并成一段不相干的连续块。取最内层的那一层 ——
    # 嵌套函数里的注释不该同时算到外层头上，否则外层拿到放行、内层新写的散文就跟着过了。
    buckets: dict[tuple[int, int, str], list[int]] = {}
    for number in sorted(comments):
        covering = [span for span in spans if span[0] <= number <= span[1]]
        if not covering:
            continue  # 模块级或类级的注释，不在这条守护的范围内
        buckets.setdefault(min(covering, key=lambda span: span[1] - span[0]), []).append(number)

    offenders: list[str] = []
    for (_, _, name), numbers in buckets.items():
        run: list[int] = []
        for number in [*numbers, None]:  # 末尾那个哨兵把最后一段冲出来
            if number is not None and run and number == run[-1] + 1:
                run.append(number)
                continue
            if (
                len(run) > MAX_INLINE_COMMENT_LINES
                and LONG_COMMENT_PRAISMA not in (comments[run[0]])
            ):
                offenders.append(f"{name}:{run[0]}（{len(run)} 行）")
            run = [] if number is None else [number]
    return offenders


def test_inline_comments_stay_short_or_carry_a_praisma() -> None:
    """**行内注释连续不超过两行** —— 第三行说明该拆函数，或者该删一半。

    只量**函数体里**的行内注释：模块文档与类文档是说明文档，与行内注释不是一回事。
    过长的那一段要在**自己第一行**写上 :data:`LONG_COMMENT_PRAISMA` 与理由 ——
    红线、性能数字、跨文件耦合确实值得超过两行，而「值得」这件事本身要有人签字。

    **只扫 ``src/``**（测试里同样长度、同样正当的注释有不少，加进来只是多一批签字而
    没人受益）；考古短语那条则连测试一起扫 —— 症状叙述在测试里同样会过时。
    """
    offenders: list[str] = []
    for path in _iter_python_files(SRC_ROOT):
        for block in _long_comment_blocks(path):
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{block}")
    assert not offenders, (
        f"函数体里的行内注释超过 {MAX_INLINE_COMMENT_LINES} 行，且没有 {LONG_COMMENT_PRAISMA}"
        f" 放行（放行要写在那一段的第一行并附理由）：\n" + "\n".join(offenders)
    )


def test_comments_do_not_keep_patch_history() -> None:
    """**注释与文档字符串里不许留打补丁的痕迹** —— 那些属于提交信息与 ADR。

    一段描述「这里曾经坏成什么样」的说明，在病根修掉之后就成了**过时的误导**：它的读者
    是半年后维护这段代码的人，他要的是「这一行为什么是这样」，不是「它曾经坏在哪」。

    只收整句短语，理由见 :data:`ARCHAEOLOGY_PHRASES`。``docs/`` 不扫 —— ADR 与提交信息
    **本来就该**记下历史，那是它们存在的理由。本文件也跳过：它把这些短语当字面量收着。
    """
    guard = Path(__file__).resolve()
    roots = (SRC_ROOT, guard.parent, PROJECT_ROOT / "scripts")
    offenders: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in _iter_python_files(root):
            if path == guard:
                continue
            relative = path.relative_to(PROJECT_ROOT)
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.lstrip().startswith("#"):
                    continue
                offenders += [
                    f"{relative}:{number}「{phrase}」"
                    for phrase in ARCHAEOLOGY_PHRASES
                    if phrase in line
                ]
            for phrase, number in _docstring_hits(path):
                offenders.append(f"{relative}:{number}「{phrase}」（文档字符串）")
    assert not offenders, (
        "注释里留着打补丁的痕迹，请改成「这一行为什么是这样」，或把这段历史写进 ADR：\n"
        + "\n".join(offenders)
    )


def _docstring_hits(path: Path) -> list[tuple[str, int]]:
    """文档字符串里命中的考古式短语，连同它在文件里的行号。

    用 :mod:`ast` 而不是扫原文，否则字符串字面量里恰好写着这几个字也会被算进来。
    """
    found: list[tuple[str, int]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        first = node.body[0].lineno
        for offset, line in enumerate(doc.splitlines()):
            found += [(phrase, first + offset) for phrase in ARCHAEOLOGY_PHRASES if phrase in line]
    return found


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_the_comment_guards_would_notice_a_violation(tmp_path: Path) -> None:
    """**这两条守护自己必须验过一次** —— 否则它们可能一直绿着而什么也没看住。

    判据是往临时文件里注入违规：``_long_comment_blocks`` 认不认得出三行无放行的注释、
    认不认得出带放行的那一段、以及模块级的注释**不算**（它不在这条守护范围内）。
    """

    def block(indent: str, *, released: bool = False) -> str:
        head = f"{indent}# {LONG_COMMENT_PRAISMA}（红线 4）\n" if released else ""
        body = "".join(f"{indent}# 第 {number} 行\n" for number in range(1, 4))
        return head + body

    long = _write(tmp_path / "long.py", f"def f():\n{block('    ')}    return 1\n")
    assert len(_long_comment_blocks(long)) == 1

    ok = _write(
        tmp_path / "released.py",
        f"def f():\n{block('    ', released=True)}    return 1\n",
    )
    assert not _long_comment_blocks(ok)

    module_level = _write(tmp_path / "module.py", "# 第 1 行\n# 第 2 行\n# 第 3 行\nx = 1\n")
    assert not _long_comment_blocks(module_level)

    # 嵌套的那一段只算它自己那个函数体，不因为外层更大就重复计一次。
    nested = _write(
        tmp_path / "nested.py",
        f"def outer():\n{block('    ')}    def inner():\n{block('        ')}        return 1\n",
    )
    assert len(_long_comment_blocks(nested)) == 2

    # 行尾注释凑不成一段话，不该把它上面那段整行注释顶成三行。
    trailing = _write(
        tmp_path / "trailing.py",
        f"def f():\n    return None  # 已经删掉了\n{block('    ')}    return 1\n",
    )
    assert len(_long_comment_blocks(trailing)) == 1

    # 两行整行注释本来就合法，上下的行尾注释也不改变这一点。
    pair = "".join(f"    # 第 {number} 行\n" for number in (1, 2))
    ok_pair = _write(
        tmp_path / "pair.py",
        f"def f():\n    x = 1  # 上面那个\n{pair}    y = 2  # 下面那个\n    return x, y\n",
    )
    assert _long_comment_blocks(ok_pair) == []


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
