"""大图页：缩放视图、它背后的纯计算，以及右侧那一栏元数据。

:mod:`~gallery.ui.viewer.zoom` 刻意不导入 Qt，所以缩放适配能用普通 pytest 覆盖。
`metadata` 是 ADR 0060 从 `panels/` 搬进来的：它只在大图页上（宿主是 `detail.py` 那一页），
而 `panels/` 剩下的那几件与它一条牵连都不成立。
"""
