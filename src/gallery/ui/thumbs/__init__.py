"""缩略图的后台流水线：可见区优先队列 + 取图任务。

解码一律走 ``gallery.core.media``，本包只负责「什么时候去要哪几张」；
:mod:`~gallery.ui.thumbs.queue` 不导入 Qt，可直接用 pytest 覆盖排队策略。
"""
