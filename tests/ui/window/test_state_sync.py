"""界面那几份映射**只在 `_sync_ui` 里编排** —— 各条路径不再各写一遍「刷新尾巴」（ADR 0050）。

原先换层、换页、筛选生效、改名之后各写一遍 `_refresh_title` / `_update_action_states` /
`_update_breadcrumb`，而这几份都是**从状态算出来的纯映射**：漏掉一个的症状是界面与状态对不上
（标题留着旧名、命令亮着却指不到东西），而不是报错 —— ADR 0041 修的「日历页摆在前面、标题却
写着大图页」就是这么来的。

所以这一份钉的是**结构**而不是行为：任何一处**同时**调两个以上成员，就是有人又开始手拼尾巴了。
单独调一个不算 —— 那是那一处精确需要的那一份（例如 ``leave_detail_page`` 必须在
``_flush_pending_watch`` 之前换页，那里判据是「当前页是不是网格页」）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from gallery.ui.window.state_sync import StateSyncMixin

#: 「从状态到界面」那几份映射。**编排只在 `_sync_ui` 里**（ADR 0050）。
SYNC_MEMBERS = (
    "_sync_page_to_stack",
    "_refresh_title",
    "_update_breadcrumb",
    "_update_action_states",
    "_sync_order_actions",
)

#: 编排点自己那份文件（豁免）。
ORCHESTRATOR = "window/state_sync.py"


def _source_root() -> Path:
    """``src/gallery/ui`` 那一层。

    **``parents[3]`` 不是 ``[2]``**：本文件在 ``tests/ui/window/`` 下，往上三级才是仓库根。
    写成 ``[2]`` 时那个路径指向 ``tests/src/gallery/ui``（不存在），于是 ``rglob`` 扫到
    **零个文件**而回归测试一路绿 —— 而「永远为绿的回归测试比没有更坏」（AGENTS.md）。
    """
    root = Path(__file__).resolve().parents[3] / "src" / "gallery" / "ui"
    assert root.is_dir(), f"扫的目录不存在：{root}（守护会静默扫零个文件而一路绿）"
    return root


def _members_called_in(path: Path) -> dict[str, set[str]]:
    """这个文件里每个函数各自直接调了哪几个成员（按函数名归并）。"""
    called: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names: set[str] = set()
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr in SYNC_MEMBERS
            ):
                names.add(inner.func.attr)
        if names:
            called[node.name] = names
    return called


def test_no_function_hand_assembles_a_tail() -> None:
    """**一个函数里不许同时调两个以上成员** —— 那就是又一条手拼的尾巴。"""
    root = _source_root()
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative == ORCHESTRATOR:
            continue  # 编排点本身
        for name, names in _members_called_in(path).items():
            if len(names) > 1:
                offenders.append(f"{relative}:{name} 调了 {sorted(names)}")
    assert not offenders, "这些地方自己拼了「刷新尾巴」，该交给 `_sync_ui()`：\n" + "\n".join(
        offenders
    )


def test_the_early_page_switch_is_the_only_exception_and_it_is_documented() -> None:
    """唯一那处「单独调一个成员」是 ``leave_detail_page`` 的提前换页，**理由必须写在旁边**。

    它换页早于 ``_flush_pending_watch``：那一句判的是「当前页是不是网格页」，晚一步这批暂存的
    变化会被再推迟一次。判据在代码里（``_defer_watch_until_idle``），所以这一条盯的是**注释**
    而不是行为 —— 免得后来的人把它挪到 ``_sync_ui`` 之后，症状是「照片变了但网格不更新」。
    """
    root = _source_root() / "window" / "viewer_flow.py"
    for name, names in _members_called_in(root).items():
        if len(names) == 1:
            assert name == "leave_detail_page", (
                f"{name} 单独调了 {sorted(names)}，而唯一获准的那一处是 leave_detail_page"
            )


def _by_at(pair: tuple[str, int]) -> int:
    """按成员在源码里出现的下标排序（``zip`` 出来的是「名字, 下标」）。"""
    return pair[1]


def test_the_sync_runs_every_member_with_the_page_first() -> None:
    """编排那一处**五份都在**，且**换页排第一**。

    其余几份映射都读「现在在哪一页」，晚一步就按上一页摆 —— 次序不是随意的。
    """
    body = (_source_root() / ORCHESTRATOR).read_text(encoding="utf-8").split("def _sync_ui", 1)[1]
    positions = [body.find(f"self.{member}()") for member in SYNC_MEMBERS]
    missing = [member for member, at in zip(SYNC_MEMBERS, positions, strict=True) if at < 0]
    assert not missing, f"`_sync_ui` 少了这几份映射：{missing}"
    order = [member for member, _ in sorted(zip(SYNC_MEMBERS, positions, strict=True), key=_by_at)]
    assert positions == sorted(positions), (
        f"次序不对：换页必须第一（其余几份都读「现在在哪一页」），实际是 {order}"
    )


def test_the_orchestrator_is_reachable_from_the_window() -> None:
    """编排点是 ``MainWindow`` 身上真有的方法，不是混在某个没人继承的类里。"""
    from gallery.ui.main_window import MainWindow

    assert issubclass(MainWindow, StateSyncMixin)
    assert callable(MainWindow._sync_ui)
