"""Gallery 的界面层（PySide6）。

分层规则：``ui`` 只能通过 ``gallery.core`` 的公开接口访问文件系统，不得自己 ``open()`` 原图、
不得自己调用 Pillow。子包按**领域**分，不按「屏幕上摆在哪」分（ADR 0060）：``browser`` 网格、
``calendar`` 日历、``viewer`` 大图、``dialogs`` 模态对话框、``thumbs`` 缩略图流水线、
``window`` 主窗口那台机器。**顶层只许留被两个以上子包用的模块**。
"""
