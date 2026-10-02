"""Gallery 的界面层（PySide6）。

分层规则：``ui`` 只能通过 ``gallery.core`` 的公开接口访问文件系统，
不得自己 ``open()`` 原图、不得自己调用 Pillow。这条规则由 ``tests/test_guards.py`` 强制。

按界面区域分成四个子包：

- ``gallery.ui.browser``  网格浏览：照片网格、列表模型、就地重命名
- ``gallery.ui.viewer``   单张查看：缩放视图与缩放计算
- ``gallery.ui.panels``   辅助面板：面包屑、元数据面板、欢迎页
- ``gallery.ui.thumbs``   缩略图的后台流水线

留在本层顶部的都是**跨区域共享**的东西：``app``（启动）、``main_window``（装配）、
``theme``（配色唯一入口）、``tasks``（后台任务）、``imaging``（像素→QImage 的桥）、
``menus``（五张右键菜单的拼装，网格与大图共用，ADR 0014 / 0021 / 0023）、
``desktop``（剪贴板与访达，全部走系统命令，见 ADR 0014）、
``watch``（watchdog 事件搬回主线程的桥），
以及不需要 Qt 的纯函数模块 ``navigation``。

本层里不需要 Qt 就能测的逻辑（缩放计算、缩略图排队策略、档位换算）刻意抽成独立模块，
其余部分是装配代码。
"""
