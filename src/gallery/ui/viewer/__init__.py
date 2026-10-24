"""大图页：缩放视图、它背后的纯计算、右侧那一栏元数据，以及视频播放页（``PageRole.DETAIL``）。

:mod:`~gallery.ui.viewer.zoom` 刻意不导入 Qt，所以缩放适配能用普通 pytest 覆盖。
`metadata` 是 ADR 0060 从 `panels/` 搬进来的：它只在这一页上，宿主就是这一页。
`video` 是同一页位置上的播放器（ADR 0091），与照片页二选一显示。
"""
