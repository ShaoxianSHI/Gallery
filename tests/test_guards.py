"""守护性测试：把本仓库不可协商的架构约定变成会失败的红灯。

这些测试的价值在于「失败得早」——规范一旦只写在文档里，就没人看着了。
"""

from __future__ import annotations

import ast
import re
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
UI_DOTTED = "gallery.ui"

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

#: 后台任务的提交与取消只许出现在这一个模块里（ADR 0051 实施）。
JOBS_MODULE = Path("window") / "jobs.py"

#: 「起一个后台任务」与「取消一个后台任务」这两句的形状。
#:
#: **回调末尾的 ``self._x_job = None`` 不在守卫范围内** —— 那是任务自己结束时把句柄放掉，
#: 不是取消。把它一起拦掉的话，每个回调都得改写成 ``_finish_job``，而那不换来任何东西。
JOB_LIFECYCLE_PATTERNS = (
    re.compile(r"self\._\w+_job\s*=\s*self\._runner\.submit\("),
    re.compile(r"self\._runner\.cancel\(\s*self\._\w+_job\s*\)"),
)


def test_only_the_jobs_module_starts_or_cancels_a_background_job() -> None:
    """**起任务与取消任务只许走 ``ui/window/jobs.py``**（ADR 0051 决策 4）。

    不收进那一处的话，「取消时机」就只剩注释在写 —— 而漏一处的症状是**静默**的：
    把「层」那一组写成「取消全部」，就会在换层时掐掉一个进行中的文件写操作（红线 1）。
    """
    offenders: list[str] = []
    for path in _iter_python_files(UI_ROOT):
        if path.name == JOBS_MODULE.name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(pat.search(line) for pat in JOB_LIFECYCLE_PATTERNS):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{number}")
    assert not offenders, (
        f"起后台任务与取消后台任务只许出现在 ui/{JOBS_MODULE}（ADR 0051）：{offenders}"
    )


def test_the_job_lifecycle_guard_would_notice_a_violation() -> None:
    """上一条自己必须验过一次 —— 一条永远为绿的测试比没有更坏。"""
    line = "        self._scan_job = self._runner.submit(work)"
    assert any(pat.search(line) for pat in JOB_LIFECYCLE_PATTERNS)
    # 回调末尾放句柄的那一句**不该**被它拦下来（那是任务自己结束，不是取消）。
    assert not any(pat.search("        self._scan_job = None") for pat in JOB_LIFECYCLE_PATTERNS)


#: 筛选 / 检索那四个字段的写入口只许在 ``ui/window/filters.py`` 里（ADR 0052）。
FILTERS_MODULE = Path("window") / "filters.py"

#: 横幅那一族的**写**已经从窗口上搬走了（ADR 0054）：七个字段现在住在
#: ``ui/window/state/banner_text.py``。这里守的是「别让写入口散回来」—— ``_thumbnail_failures``
#: 留了一个**只读属性**（三处测试在读那个数字），而 ``property`` 本身不是赋值，所以放行。
BANNER_TEXT_MODULE = Path("window") / "banner_text.py"
BANNER_TEXT_WRITE_PATTERNS = tuple(
    re.compile(rf"self\.{name}\s*(=[^=]|\+=|-=)")
    for name in (
        "_level_notice",
        "_progress_notice",
        "_progress_notice_text",
        "_place_notice_text",
        "_index_notice_text",
        "_scan_issue",
        "_thumbnail_failures",
        "_banner_sticky_until",
    )
)

#: 焦点 / 当前行那一族的写入口只许在 ``ui/window/focus.py`` 里（ADR 0053）。
#:
#: ``_current_row`` 在窗口上是**只读属性**，所以任何一处赋值都是绕过轴 —— 而它原先是 11 处裸赋值，
#: 其中 6 处是同一个意思（换层了，没人正看着任何一张）。那一族其余三个字段已经不在窗口上了，
#: 写它们会直接 ``AttributeError``，所以只盯这一个。
FOCUS_MODULE = Path("window") / "focus.py"
FOCUS_WRITE_PATTERNS = (re.compile(r"self\._current_row\s*(=[^=]|\+=|-=)"),)

#: 那四个字段在窗口上仍是**只读属性**，所以任何一处赋值都是绕过轴 —— 四支都拦：
#: 整个属性（``=``）、下标（``[i] =``）、``del``，以及改长度的调用。
FILTER_FIELD_NAMES = ("_conditions", "_search_scope", "_editing_condition", "_search_total")

#: 判据只认 ``self.<字段>``：``window._conditions`` 是测试在做的事，而那一侧不受这条约束。
FILTER_WRITE_PATTERNS = tuple(
    re.compile(pattern)
    for name in FILTER_FIELD_NAMES
    for pattern in (
        rf"self\.{name}\s*(\[[^\]]*\])?\s*(=[^=]|\+=|-=|\.append|\.clear|\.pop)",
        rf"del\s+self\.{name}\b",
    )
)


def test_only_the_filters_module_writes_the_filter_fields() -> None:
    """**那四个字段只许在 ``ui/window/filters.py`` 里写**（ADR 0052）。

    不收进那一处的话，「动条件要连带做哪几件事」就只剩注释在写 —— 而漏一处的症状是
    **静默**的：``_editing_condition`` 是**下标**，删掉一条不跟着挪的话，下一次回车会
    就地替换掉另一条条件（用户看到的是「刚敲的那条没生效」）。
    """
    offenders: list[str] = []
    for path in _iter_python_files(UI_ROOT):
        if path.name == FILTERS_MODULE.name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(pat.search(line) for pat in FILTER_WRITE_PATTERNS):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{number}")
    assert not offenders, (
        f"那四个筛选字段的写入口只许在 ui/{FILTERS_MODULE}（ADR 0052）：{offenders}"
    )


def test_only_the_focus_module_writes_the_current_row() -> None:
    """**``_current_row`` 只许在 ``ui/window/focus.py`` 里写**（ADR 0053）。

    它原先是 11 处裸赋值，其中 **6 处是完全同一句话**（「这一层换了，没人正看着任何一张」，
    散在换层、换库、换浏览方式、点日历格、摊开日历格、收日历格）。收成 ``clear_current()``
    与 ``set_current()`` 两个入口之后，「换层时忘了清」在代码里就长得不一样了。
    """
    offenders: list[str] = []
    for path in _iter_python_files(UI_ROOT):
        if path.name == FOCUS_MODULE.name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(pat.search(line) for pat in FOCUS_WRITE_PATTERNS):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{number}")
    assert not offenders, (
        f"``_current_row`` 的写入口只许在 ui/{FOCUS_MODULE}（ADR 0053）：{offenders}"
    )


def test_only_the_banner_text_module_writes_the_banner_fields() -> None:
    """**横幅那七个字段的写只许在 ``ui/window/state/banner_text.py`` 里**（ADR 0054）。

    它们原先在窗口上分七个字段，而「清屏」与「清账」必须同时做这件事只有
    ``_clear_banner`` 一处知道 —— 换层与换库那六处各手写一遍，**漏掉哪一个都不会立刻
    看出错**：漏 ``_level_notice`` 时「这一层是空的」就再也说不出来，漏 ``_scan_issue``
    时进度会去抢那句「哪些内容没读到」。
    """
    offenders: list[str] = []
    for path in _iter_python_files(UI_ROOT):
        if path.name == BANNER_TEXT_MODULE.name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(pat.search(line) for pat in BANNER_TEXT_WRITE_PATTERNS):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{number}")
    assert not offenders, (
        f"横幅那七个字段的写只许在 ui/{BANNER_TEXT_MODULE}（ADR 0054）：{offenders}"
    )


def test_the_banner_field_guard_would_notice_a_violation() -> None:
    """上一条自己必须验过一次 —— 而这一条尤其要验：它是「别让它们回来」的守卫，
    而七个字段搬走之后**代码里一个都不剩**，扫不到东西的守护是恒绿的。"""
    assert BANNER_TEXT_WRITE_PATTERNS[0].search("        self._level_notice = False")
    assert BANNER_TEXT_WRITE_PATTERNS[5].search("            self._scan_issue = True")
    assert BANNER_TEXT_WRITE_PATTERNS[2].search("        self._progress_notice_text = None")
    # 读不算 —— ``_thumbnail_failures`` 那个只读属性正是走这一支。
    for line in (
        "        if self._scan_issue:",
        "        return self._thumbnail_failures",
    ):
        assert not any(pat.search(line) for pat in BANNER_TEXT_WRITE_PATTERNS), f"误伤：{line!r}"


def test_the_focus_row_guard_would_notice_a_violation() -> None:
    """上一条自己必须验过一次 —— 扫正则最容易出的错是「扫不到东西而恒绿」。"""
    assert FOCUS_WRITE_PATTERNS[0].search("        self._current_row = -1")
    assert FOCUS_WRITE_PATTERNS[0].search("        self._current_row = row")
    # 读与比较不算 —— 那正是要放行的（25 处读一个字都没改）。
    for line in (
        "        if self._current_row < 0:",
        "            return self._current_row",
        "        row = self._current_row",
    ):
        assert not FOCUS_WRITE_PATTERNS[0].search(line), f"误伤：{line!r}"


def test_the_filter_field_guard_would_notice_a_violation() -> None:
    """上一条自己必须验过一次 —— 扫正则最容易出的错是「扫不到东西而恒绿」。"""
    # 三支都拦：整个属性、下标赋值、改长度的调用。
    for line in (
        "        self._editing_condition = None",
        "        self._conditions[position] = condition",
        "        self._conditions.clear()",
        "        del self._conditions[position]",
    ):
        assert any(pat.search(line) for pat in FILTER_WRITE_PATTERNS), f"没拦下来：{line!r}"
    # 读与比较不算 —— 那三支正是要放行的。
    for line in (
        "        if not self._conditions:",
        "        editing = self._editing_condition if conditions else None",
        "        return self._search_scope == SEARCH_SCOPE_LEVEL",
    ):
        assert not any(pat.search(line) for pat in FILTER_WRITE_PATTERNS), f"误伤：{line!r}"


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


# ---- 顶层只许留跨包共享的模块（ADR 0059 承诺、ADR 0060 落地）--------------------


def _ui_top_level_module_imported_by(path: Path) -> set[str]:
    """这个文件 import 了 ``ui/`` 顶层的哪些模块（两种写法都认）。

    两种形状都要认：``from gallery.ui import theme`` 与 ``from gallery.ui.theme import AppTheme``
    说的是同一件事，而只认一种的话那条守护会对一半的顶层模块恒绿。
    """
    names: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.startswith(f"{UI_DOTTED}."):
                rest = node.module[len(UI_DOTTED) + 1 :]
                if "." not in rest:
                    names.add(rest)
            elif node.module == UI_DOTTED:
                names.update(alias.name for alias in node.names)
    return names


def _subpackage_of(path: Path) -> str:
    """这个文件属于哪个子包；顶层模块本身记成 ``""``。"""
    relative = path.relative_to(UI_ROOT)
    return relative.parts[0] if len(relative.parts) > 1 else ""


def _top_level_consumers(module: str) -> set[str]:
    """``ui/<module>.py`` 被哪几个**子包** import（顶层模块之间的 import 不算）。

    判据是「有几个子包用它」而不是「有几个文件用它」—— 同一包里十个文件用它是**一个**包在用，
    按文件数算的话一个子包内部的自相依赖会被当成「跨包共享」。
    """
    consumers: set[str] = set()
    for path in _iter_python_files(UI_ROOT):
        package = _subpackage_of(path)
        if package == module:
            continue
        if module in _ui_top_level_module_imported_by(path):
            consumers.add(package)
    return consumers


#: ``ui/`` 顶层只许留**跨包共享**的模块（AGENTS.md：「只有确实跨领域共享的才放顶层」）。
#:
#: 值是「不搬它的理由」；``None`` 表示它确实被两个以上子包用。**新加一个顶层模块必须在这里
#: 登记**，否则那条守护会红 —— 这一条是 ADR 0059 承诺过、而 ``8451df0`` 从未落地的那条。
TOP_LEVEL_MODULES: Final[dict[str, str | None]] = {
    "imaging": None,  # browser / calendar / thumbs / window 四个包都用它
    "chips": None,  # 网格页的面包屑与日历的工具条摆同一批条件胶囊
    "layout": None,  # calendar 的工具条与窗口那几个面板都要拆控件（顺序错了会留下顶层小白窗）
    "outline": None,  # dialogs（两个编辑器）与 window（搜索框、菜单）两族
    "rename_edit": None,  # browser 的格子与 window 的标题栏共用同一个输入框
    "scrollbar_style": None,  # 网格、周视图、元数据面板三条滚动条
    "tasks": None,  # thumbs 与 window 都要起后台任务
    "theme": None,  # 全部四个子包都从它派生颜色
    "banners": "写操作结果的用户可见措辞，与窗口无关：write_ops 与各条 flow 都调它",
    "desktop": "访达与剪贴板那一座桥，与窗口无关：右键菜单、打开文件夹、拷贝都调它",
    "write_ops": "「只增不改」那条数据安全守护按文件名找它，搬了它那条守护就悄悄失效",
}

#: 入口模块：它们本来就不该被别人 import，进了共享登记表反而会逼人去 import 它们。
UI_ENTRYPOINTS: Final = ("app", "main_window")


#: ``main_window.py`` 的源码：页面模块那条守护要确认「登记的模块真的被它 import 了」。
_MAIN_WINDOW_SOURCE: Final = (UI_ROOT / "main_window.py").read_text(encoding="utf-8")


def _present_top_level_modules(root: Path = UI_ROOT) -> set[str]:
    """顶层有哪些模块（不含包的 ``__init__`` 与两个入口）。

    ``root`` 是参数而不是写死：验这条守护自己会红的那条用例要在 ``tmp_path`` 里摆一个
    假的顶层出来，而**摆假的就等于没验** —— 真正要验的是「读目录」这一段认不认得出违规。
    """
    return {
        path.stem for path in root.glob("*.py") if path.stem not in (*UI_ENTRYPOINTS, "__init__")
    }


def _unregistered_top_level(root: Path = UI_ROOT) -> list[str]:
    """顶层有哪些模块没进登记表；``root`` 只给验守护自己会红的那条用例用。"""
    return sorted(_present_top_level_modules(root) - TOP_LEVEL_MODULES.keys())


def test_ui_top_level_only_holds_shared_modules() -> None:
    """**顶层只许留被两个以上子包用的模块**（ADR 0059 决策，ADR 0060 落地）。

    不登记的话这条规矩就只剩文档里的一句话 —— 而它已经这样失效过一次：0059 把
    ``menu_style`` 等四个搬进 ``window/``，提交信息说加了这条守护，而那个 commit
    没有碰过本文件（``git show 8451df0 --stat`` 里没有它）。
    """
    unregistered = _unregistered_top_level()
    assert not unregistered, (
        f"这些顶层模块不在登记表里：{unregistered}；要么搬进唯一用它的那个子包，"
        f"要么在 TOP_LEVEL_MODULES 里登记并写明不搬的理由（ADR 0059）"
    )
    vanished = sorted(TOP_LEVEL_MODULES.keys() - _present_top_level_modules())
    assert not vanished, f"登记表里的顶层模块已经不在了，删掉那一行：{vanished}"


def test_a_shared_top_level_module_is_actually_shared() -> None:
    """**登记为「共享」的那个，至少要有两个子包真的用它**（ADR 0060）。

    这是上一条的反面：搬走一个模块的**消费者**不会让上一条变红（模块还在，登记还在），
    而它已经不再是共享的了 —— 症状是有人照着目录结构以为还有别的包在用它。
    """
    lonely = {
        name: sorted(_top_level_consumers(name))
        for name, reason in TOP_LEVEL_MODULES.items()
        if reason is None and len(_top_level_consumers(name)) < 2
    }
    assert not lonely, (
        f"这些顶层模块登记为跨包共享，实际只有一个子包在用：{lonely}；"
        f"搬进那个子包，或把理由改成一条具名的例外"
    )


def test_the_top_level_guard_would_notice_a_violation(tmp_path: Path) -> None:
    """上一条自己必须验过一次 —— 扫目录的守护最容易出的错就是「扫不到东西而恒绿」。

    造一个**真的**顶层模块出来（不是往集合里塞一个名字）：要验的是「读目录」这一段，
    而集合里塞名字验的是同一段代码之外的另一件事。
    """
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "widget_menu.py").write_text("", encoding="utf-8")
    assert _unregistered_top_level(tmp_path) == ["widget_menu"], (
        "注入了一个没登记的顶层模块，这条守护没有反应"
    )
    assert not _unregistered_top_level(), "真实的 ui/ 顶层被这条用例自己判成违规了"


# ---- 四个页面各住一处，与 PageRole 一一对应（ADR 0060）-------------------------


def test_every_page_role_has_a_page_module() -> None:
    """**``PageRole`` 的每个值都要有一个页面模块，而它们住在四个不同的地方**（ADR 0060）。

    ``panels/`` 那六件东西能混在一个包里，是因为它们**都不是页面** —— 一个页面（欢迎）、
    一栏侧板、一行面包屑、两个对话框、一个助手。拆完之后「四个页面」与 ``PageRole`` 的四个值
    又一一对应上了，于是这条可以断言：**加一种页面而忘了建控件**、**把某个页面塞进另一个包**、
    **把两个页面塞进同一个模块**，都红。

    判据有三层，任一层都不能省：模块在不在、模块里有没有 QWidget 子类、
    以及 ``main_window`` 是不是真的 import 了它（最后那层挡的是「登记了一个没人用的模块」）。
    """
    from gallery.ui.window.state.level_stack import PageRole

    missing = sorted(role.name for role in PageRole if role.name not in PAGE_MODULES)
    assert not missing, f"这些 PageRole 没有对应的页面模块：{missing}；加一种页面就要建它的控件"
    extra = sorted(set(PAGE_MODULES) - {role.name for role in PageRole})
    assert not extra, f"这些页面模块没有对应的 PageRole：{extra}；删掉，或给那一页加一个枚举值"

    modules = {
        name: UI_ROOT.joinpath(*module.split(".")).with_suffix(".py")
        for name, module in PAGE_MODULES.items()
    }
    absent = sorted(name for name, path in modules.items() if not path.is_file())
    assert not absent, f"这些页面模块不在了（ADR 0060 搬过一轮）：{absent}"
    no_widget = sorted(name for name, path in modules.items() if not _has_widget_class(path))
    assert not no_widget, f"这些页面模块里没有 QWidget 子类：{no_widget}"

    # 一页一处：模块两两不同。判据是「改了它牵连不牵连另一个页面」。
    by_module: dict[str, list[str]] = {}
    for name, module in PAGE_MODULES.items():
        by_module.setdefault(module, []).append(name)
    shared = sorted(module for module, names in by_module.items() if len(names) > 1)
    assert not shared, f"这些页面住在同一个模块里：{by_module}；一页一处"

    # 角色 ↔ 控件 ↔ 内容区：加一种页面要在这三处各落一次，漏一处就红。
    roles = _page_attributes_by_role()
    assert set(roles) == {role.name for role in PageRole}, (
        f"_page_for_role 那张表与 PageRole 对不上：{roles}"
    )
    stacked = _stacked_page_attributes()
    unstacked = sorted(set(roles.values()) - stacked)
    assert not unstacked, f"这些页面没有被摆进内容区：{unstacked}"
    unmapped = sorted(stacked - set(roles.values()))
    assert not unmapped, f"内容区里摆了这些页，而 PageRole 里没有对应的一个：{unmapped}"


#: ``_page_for_role`` 那张表的源码（它在 :mod:`~gallery.ui.window.level_stack_mixin` 里，
#: 而那张表的另一半 —— 控件本身 —— 在 ``main_window.py``）。
_PAGE_ROLE_SOURCE: Final = (UI_ROOT / "window" / "level_stack_mixin.py").read_text(encoding="utf-8")


def _page_attributes_by_role() -> dict[str, str]:
    """``_page_for_role`` 那张表：``PageRole`` 的成员名 → 它摆的是哪个控件。"""
    table: dict[str, str] = {}
    for node in ast.walk(ast.parse(_PAGE_ROLE_SOURCE)):
        if not isinstance(node, ast.FunctionDef) or node.name != "_page_for_role":
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Dict):
                continue
            for key, value in zip(child.keys, child.values, strict=True):
                if isinstance(key, ast.Attribute) and isinstance(value, ast.Attribute):
                    table[key.attr] = value.attr
    return table


def _stacked_page_attributes() -> set[str]:
    """``main_window`` 往内容区（那个 ``QStackedWidget``）里摆的是哪几个控件。

    **从 ``addWidget`` 读出来而不是从 ``_page_for_role`` 读**：后者是一张「角色 → 已有控件」
    的表，四个值它当然一个都不缺；真正说明「这一页存在」的是它被摆进了内容区。
    栈那个属性名也不写死 —— 找 ``QStackedWidget()`` 那个赋值。
    """
    tree = ast.parse(_MAIN_WINDOW_SOURCE)
    stack_attr = next(
        (
            node.targets[0].attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "QStackedWidget"
            and isinstance(node.targets[0], ast.Attribute)
        ),
        None,
    )
    if stack_attr is None:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "addWidget"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == stack_attr
            and node.args
            and isinstance(node.args[0], ast.Attribute)
        ):
            found.add(node.args[0].attr)
    return found


def _all_add_widget_arguments() -> set[str]:
    """``main_window`` 里所有 ``addWidget`` 的第一个参数 —— 不分摆进哪个布局。

    只给验守护自己会红的那条用例用：它证明 :func:`_stacked_page_attributes` 真的在筛。
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(_MAIN_WINDOW_SOURCE)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "addWidget"
            and node.args
            and isinstance(node.args[0], ast.Attribute)
        ):
            found.add(node.args[0].attr)
    return found


def _has_widget_class(path: Path) -> bool:
    """这个模块里有没有 QWidget 子类（页面得是控件，别的不是）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(isinstance(node, ast.ClassDef) and node.bases for node in ast.walk(tree))


#: ``PageRole`` 的四个值 ↔ 各自的页面模块（ADR 0060）。**只在这里写一份** ——
#: 上面那条守护是唯一读它的地方，改名改路径都只动这一处。
PAGE_MODULES: Final[dict[str, str]] = {
    "WELCOME": "window.widgets.welcome",
    "GRID": "browser.grid",
    "CALENDAR": "calendar.page",
    "DETAIL": "viewer.detail",
}


def test_the_page_module_guard_would_notice_a_violation() -> None:
    """上一条自己必须验过一次 —— 每一层判据都可能恒真，逐条注入一遍。"""
    from gallery.ui.window.state.level_stack import PageRole

    # 「模块在不在」：`browser` 是包、`grid` 才是模块 —— 分不清这两种就等于没查。
    assert (UI_ROOT / "browser" / "grid.py").is_file()
    assert not (UI_ROOT / "browser.grid.py").exists()
    # 「有没有 QWidget 子类」：一个只有函数的模块要被抓出来。
    assert not _has_widget_class(UI_ROOT / "layout.py")
    assert _has_widget_class(UI_ROOT / "calendar" / "page.py")
    # 「四个角色都在」：少登记一个就该被 `missing` 抓到。
    assert set(PAGE_MODULES) == {role.name for role in PageRole}
    assert set(PAGE_MODULES) - {"MAP"}  # 真的少一个时非空
    # 「角色 ↔ 控件 ↔ 内容区」三层对得上，且只认真正摆进 QStackedWidget 的那几个。
    roles = _page_attributes_by_role()
    assert set(roles) == {role.name for role in PageRole}
    stacked = _stacked_page_attributes()
    assert set(roles.values()) <= stacked
    # 摆进别的布局的不算页面（面包屑就摆在网格页那一行的布局里）。
    assert "_breadcrumb" in _all_add_widget_arguments() - stacked


# ---- mixin：同名方法会静默失效 ------------------------------------------------


def test_two_main_window_mixins_never_define_the_same_method() -> None:
    """**``MainWindow`` 的两个 mixin 不许有同名方法** —— Python 按 MRO 取第一个实现。

    这不是风格问题，是**静默失效**：后写的那个方法永远不会被调用，而没有任何报错。
    事件封面的 ``event_cover_candidate`` 就踩过一次 —— 它本来也叫 ``cover_candidate``，
    而 ``WriteOpsMixin``（相册封面，ADR 0028）在基类列表里排在 ``EventFlowMixin`` 前面，
    于是症状是「菜单里压根没有这一条」，查起来像是判据写错了。

    **只扫 ``MainWindow`` 真正继承的那几个 mixin**，不扫别的类：``ui/`` 下大量 QWidget 子类
    各自都有 ``paintEvent`` / ``__init__``，那是继承关系里正常的事，与 MRO 冲突无关。

    **一个 mixin 里同一个名字出现多次不算冲突**（``@property`` 的 getter 与 setter
    是**一处**定义）—— 判据在 :func:`_mixin_method_owners` 里。
    """
    clashes = {
        name: sorted(names)
        for name, names in _mixin_method_owners(set(_main_window_bases())).items()
        if len(names) > 1
    }
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
    """这条守护自己必须验过一次**：往一个 mixin 里注入一个同名方法，那条守护要红。

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


def _mixin_method_owners(bases: set[str]) -> dict[str, set[str]]:
    """方法名 → 定义了它的那些 mixin。**同一份解析，两处用例共用。**

    **一个 mixin 里同一名字只算一次** —— ``@property`` 展开成 getter 与 setter 两个
    ``FunctionDef``，而那是**一处**定义（``JobsMixin`` 上那 13 个转发 property，
    ADR 0058 阶段 1）。要断的是「不止一个 mixin 定义它」。
    """
    import ast

    owners: dict[str, set[str]] = {}
    for class_name in bases:
        module = _module_of_mixin(class_name)
        if module is None:
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                        owners.setdefault(item.name, set()).add(class_name)
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


def docstring(lines: int, *, released: bool = False) -> str:
    """造一个 ``lines`` 行的函数文档字符串源码 —— 验守护认不认得出过长的那一份。"""
    first = f"{LONG_COMMENT_PRAISMA}（跨文件耦合）" if released else "第一行"
    content = [first, *(f"第 {number} 行" for number in range(2, lines + 1))]
    return 'def f():\n    """' + "\n".join(content) + '"""\n    return 1\n'


def test_the_comment_guards_would_notice_a_violation(tmp_path: Path) -> None:
    """**这几条规则自己必须验过一次** —— 否则它们可能一直绿着而什么也没看住。

    判据是往临时文件里注入违规：``_long_comment_blocks`` 认不认得出三行无放行的注释、
    认不认得出带放行的那一段、以及模块级的注释**不算**（它不在这条守护范围内）。
    文档字符串与自造术语两条同理，各注入一次确认会红。
    """

    def block(indent: str, *, released: bool = False) -> str:
        head = f"{indent}# {LONG_COMMENT_PRAISMA}（硬性约束 4）\n" if released else ""
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

    # 文档字符串：七行无放行要报，六行不报；放行写在引号那一行。
    seven = _write(tmp_path / "seven.py", docstring(7))
    assert _long_docstrings(seven) == ["f:2（7 行）"]
    assert _long_docstrings(_write(tmp_path / "six.py", docstring(6))) == []
    released = _write(tmp_path / "released_doc.py", docstring(7, released=True))
    assert _long_docstrings(released) == []

    # 收尾引号单独占一行时，那一行只有缩进 —— 不该把它算进行数（否则六行被量成七行）。
    closing = _write(
        tmp_path / "closing.py",
        'def f():\n    """第一行\n\n    第二行\n    第三行\n    """\n    return 1\n',
    )
    assert _docstring_spans(closing) == [("f", 2, 4)], _docstring_spans(closing)

    # 自造术语：注释里的、文档字符串里的、行尾注释里的都要报，字符串字面量里的不算。
    jargon = _write(
        tmp_path / "jargon.py",
        '"""这个模块的闸门。"""\n'
        "def f():\n"
        "    # 落定之后才算\n"
        "    x = 1  # 在途那次\n"
        '    y = "闸门"\n'
        "    return x, y\n",
    )
    hits = _self_invented_terms(jargon)
    assert len(hits) == 3, hits
    assert all(term in "".join(hits) for term in ("闸门", "落定", "在途"))
    assert _self_invented_terms(_write(tmp_path / "plain.py", "x = 1\n")) == []


#: 文档字符串的行数上限。**第七行开始要问的是「哪几行在复述代码」**，而不是「怎么写得
#: 更简洁」—— 复述的那些删掉就够，剩下的多半是真约束，留着。
MAX_DOCSTRING_LINES: Final = 6

#: 自造词 → 该用的通用术语。报错的文本里直接把替换词写出来，省得再查一遍。
#:
#: **只收在这个仓库里不可能正当使用的词。** ``实测`` / ``曾经`` / ``记账`` / ``原地``
#: 这些词正当用法不少，按词拉黑会误伤，而误报的守护会被忽略 —— 那比没有守护更坏。
#: 判据是「读的人能不能不看别处就懂」：``闸``、``标脏``、``落定`` 要先在脑子里翻一遍
#: 才接到代码上，而 ``条件``、``标记为需要重绘``、``稳定下来`` 不需要。
PLAIN_TERMS: Final[tuple[tuple[str, str], ...]] = (
    # 长词在前：``攒着`` 必须在 ``攒`` 之前判，否则报出来的是半个词。
    ("闸门", "前置条件"),
    ("攒着", "暂存"),
    ("守护测试", "回归测试"),
    ("钉死", "固定"),
    ("钉住", "锁定"),
    ("闸", "前置条件"),
    ("红线", "硬性约束"),
    ("守护", "回归测试"),
    ("标脏", "标记为需要重绘"),
    ("落定", "稳定下来"),
    ("攒", "累积"),
    ("在途", "进行中"),
    ("回声", "回环"),
    ("补位", "占位"),
    ("抽风", "临时不可用"),
    ("收工", "结束"),
    ("收下", "记录"),
    ("吃掉", "忽略掉"),
    ("冒到", "传递到"),
    ("漏接", "漏掉"),
    ("兜底", "回退"),
    ("去抖", "防抖"),
    ("写死", "硬编码"),
    ("上屏", "显示"),
    ("认领", "适用"),
    ("摊开", "展开"),
)


def _docstring_spans(path: Path) -> list[tuple[str, int, int]]:
    """这个文件里每个文档字符串的 ``(名字, 起始行, 行数)``。"""
    return [(name, first, len(lines)) for name, first, lines in _docstring_blocks(path)]


def _docstring_blocks(path: Path) -> list[tuple[str, int, list[str]]]:
    """这个文件里每个文档字符串的 ``(名字, 起始行, 逐行正文)``。

    起始行取**第一条语句**那一行（文档字符串就挂在它前面），于是 ``docstring`` 那一行
    自己也在区间里 —— 放行标记写在第一行才量得到。

    **末尾那行只有缩进的要去掉**：收尾引号单独占一行时 ``get_docstring`` 的结果以
    ``"\\n    "`` 结尾，``splitlines()`` 会把它算成一行，于是「看得见的六行」被量成七行。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        lines = doc.splitlines()
        if lines and not lines[-1].strip():
            lines.pop()
        found.append((getattr(node, "name", "<module>"), node.body[0].lineno, lines))
    return found


def test_docstrings_stay_short_or_carry_a_praisma() -> None:
    """**文档字符串不超过六行** —— 第七行开始通常是在复述代码而不是解释它。

    与 :func:`test_inline_comments_stay_short_or_carry_a_praisma` 一样，过长的要在
    **自己第一行**写上 :data:`LONG_COMMENT_PRAISMA` 与理由。放行留给真约束：数据安全
    的那几条、Qt 的反直觉行为、性能数字，它们值得占六行以上。同样只扫 ``src/``。
    """
    offenders: list[str] = []
    for path in _iter_python_files(SRC_ROOT):
        offenders += _long_docstrings(path)
    assert not offenders, (
        f"文档字符串超过 {MAX_DOCSTRING_LINES} 行，且没有 {LONG_COMMENT_PRAISMA} 放行"
        "（放行要写在文档字符串第一行并附理由）：\n" + "\n".join(offenders)
    )


def _long_docstrings(path: Path) -> list[str]:
    """这个文件里过长的文档字符串，每条给出 ``名字:起始行（行数）``。"""
    source = path.read_text(encoding="utf-8").splitlines()
    found: list[str] = []
    for name, number, length in _docstring_spans(path):
        if length > MAX_DOCSTRING_LINES and LONG_COMMENT_PRAISMA not in source[number - 1]:
            found.append(f"{name}:{number}（{length} 行）")
    return found


def _prose_lines(path: Path) -> list[tuple[int, str]]:
    """注释与文档字符串的正文，``(行号, 文本)`` —— 字符串字面量不算。

    注释走 :mod:`tokenize`，于是引号里的 ``#`` 认得出来，而行尾注释也不必另开一支
    （写在那儿的术语同样要换）；文档字符串走 :mod:`ast`，于是同样写着那几个字的一段
    断言不会被算进来。
    """
    found: list[tuple[int, str]] = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type == tokenize.COMMENT:
                found.append((token.start[0], token.string.lstrip("#").strip()))
    for _, first, lines in _docstring_blocks(path):
        found += [(first + offset, line) for offset, line in enumerate(lines)]
    return found


def test_comments_speak_plain_software_terminology() -> None:
    """**注释里不许有自造术语** —— 读的人要能不看别处就懂。

    只收 :data:`PLAIN_TERMS` 里那些「在这个仓库里不可能正当使用」的词，所以这条守护
    不会因为 ``实测``、``曾经``、``记账`` 这些正当用法而误报。``docs/`` 不扫 ——
    ADR 记的是决策本身；本文件也跳过：它把这些词当字面量收着。
    """
    guard = Path(__file__).resolve()
    offenders: list[str] = []
    for root in (SRC_ROOT, guard.parent, PROJECT_ROOT / "scripts"):
        for path in _iter_python_files(root):
            if path == guard:
                continue
            offenders += [
                f"{path.relative_to(PROJECT_ROOT)}:{row}" for row in _self_invented_terms(path)
            ]
    assert not offenders, (
        "注释里用了自造术语，请换成通用软件术语（左边是本仓库的说法，右边是建议）：\n"
        + "\n".join(offenders)
    )


def _self_invented_terms(path: Path) -> list[str]:
    """这个文件里命中的自造术语，每条给出 ``行号「原词」→「建议」``。

    一段话里同时命中长短两个词（``闸门`` 与 ``闸``）时只报长的那个 —— 报两遍是同一条
    毛病说了两次，而修的人只要改一处。
    """
    terms = sorted(PLAIN_TERMS, key=lambda pair: -len(pair[0]))
    found: list[str] = []
    for number, text in _prose_lines(path):
        matched = [(term, replacement) for term, replacement in terms if term in text]
        longest = [
            pair
            for pair in matched
            if not any(pair[0] != other[0] and pair[0] in other[0] for other in matched)
        ]
        found += [f"{number}「{term}」→「{replacement}」" for term, replacement in longest]
    return found


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
