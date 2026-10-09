"""模态对话框：编辑一张照片的元数据、编辑一个事件（ADR 0060 从 `panels/` 搬出来）。

**两个都不碰用户的文件**：各只把用户填的那一份读成一个 dataclass 交出去，写盘 / 写库
分别走 :mod:`gallery.ui.write_ops` 与 ``core.events``。**依赖方向是反的**（顶层 import
子包）：那是 ADR 0059 决策 4 的代价，`write_ops.py` 得留在顶层，别为了方向好看往上搬。
"""

from gallery.ui.dialogs.event_edit import DELETE_REQUESTED, EventEdit, EventEditDialog
from gallery.ui.dialogs.metadata_edit import MetadataEditDialog

__all__ = ["DELETE_REQUESTED", "EventEdit", "EventEditDialog", "MetadataEditDialog"]
