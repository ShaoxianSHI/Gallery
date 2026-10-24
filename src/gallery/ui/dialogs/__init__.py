"""模态对话框：编辑元数据、编辑事件、导入影片、排外部应用的次序。

**这几个都不碰用户的文件**：各自只把用户填的那一份读成一个 dataclass 交出去，写盘 / 写库
分别走 :mod:`gallery.ui.write_ops` 与 ``core.events``。**依赖方向是反的**（顶层 import
子包）：那是 ADR 0059 决策 4 的代价，`write_ops.py` 得留在顶层，别为了方向好看往上搬。
"""

from gallery.ui.dialogs.event_edit import DELETE_REQUESTED, EventEdit, EventEditDialog
from gallery.ui.dialogs.external_order import ExternalOrderDialog, OrderItem, OrderPage
from gallery.ui.dialogs.metadata_edit import MetadataEditDialog
from gallery.ui.dialogs.movie_import import MovieImportDialog

__all__ = [
    "DELETE_REQUESTED",
    "EventEdit",
    "EventEditDialog",
    "ExternalOrderDialog",
    "MetadataEditDialog",
    "MovieImportDialog",
    "OrderItem",
    "OrderPage",
]
