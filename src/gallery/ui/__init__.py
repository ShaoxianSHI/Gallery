"""Gallery 的界面层（PySide6）。

分层规则：``ui`` 只能通过 ``gallery.core`` 的公开接口访问文件系统，
不得自己 ``open()`` 原图、不得自己调用 Pillow。这条规则由 ``tests/test_guards.py`` 强制。

本层里不需要 Qt 就能测的逻辑（缩放计算、缩略图排队策略）刻意抽成独立模块，
其余部分是装配代码。
"""
