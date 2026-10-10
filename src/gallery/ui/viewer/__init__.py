"""大图页：缩放视图、它背后的纯计算，以及右侧那一栏元数据（``PageRole.DETAIL``）。

:mod:`~gallery.ui.viewer.zoom` 刻意不导入 Qt，所以缩放适配能用普通 pytest 覆盖。
`metadata` 是 ADR 0060 从 `panels/` 搬进来的：它只在这一页上，宿主就是这一页。
"""
