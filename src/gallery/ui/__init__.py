"""Gallery 的界面层（PySide6）。

分层规则：``ui`` 只能通过 ``gallery.core`` 的公开接口访问文件系统，不得自己
``open()`` 原图、不得自己调用 Pillow；这条规则由 ``tests/test_guards.py`` 强制。
按界面区域分成 ``browser`` / ``viewer`` / ``panels`` / ``thumbs`` 四个子包，本层
顶部放跨区域共享的启动、装配、样式，以及 ``write_ops``（界面上唯一会动用户文件的入口）。
"""
