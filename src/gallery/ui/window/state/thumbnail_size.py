"""缩略图档位（ADR 0058 阶段 4）。

两样东西：**当前档位**，与**还没执行的档位步进**。后者是「切档动画还在跑的时候按下的
那几下」累积起来的 —— 每一下都立刻重排整层不划算，所以先记着，等动画结束一起走。

原先那两个字段被 ``MainWindow`` / ``thumbnail_flow`` / ``library_flow`` 都写过。
"""

from __future__ import annotations

from gallery.ui.browser.thumb_sizes import edge_for_index, nearest_index, shifted_index

__all__ = ["ThumbnailSize"]


class ThumbnailSize:
    """**当前档位 + 还没落地的步进**（ADR 0058 阶段 4）。

    :param index: 初始档位；``None`` = 取当前屏幕最合适的那一档。

    **排不排队的判据是「算上已排着的之后，这一下还会不会换档」**，而不是
    「当前在不在头尾」—— 已经排了三下、正好到顶的那一下就该丢掉。
    """

    _size_index: int
    _pending_size_step: int

    def __init__(self, index: int | None = None) -> None:
        self._size_index = nearest_index(index)
        self._pending_size_step = 0

    @property
    def edge(self) -> int:
        """当前档位对应的边长（像素）。"""
        return edge_for_index(self._size_index)

    @property
    def effective(self) -> int:
        """**算上已排着的那些**之后会落在哪一档 —— 判「还能不能换档」用这个。"""
        return shifted_index(self._size_index, self._pending_size_step)

    def select(self, index: int) -> bool:
        """**切到某一档**；返回有没有真的切。

        **明确选了一档，排着的那些就都作废** —— 否则「动画期间按了一下（正好在顶上，
        本来什么也不该发生），接着又从菜单点了某一档」会让那一下在动画结束后才生效。
        """
        self._pending_size_step = 0
        if index == self._size_index:
            return False
        self._size_index = index
        return True

    def queue_step(self, step: int) -> bool:
        """**先存着这一步**（动画还在跑的时候按下），返回有没有存下。

        已经到头尾的那一下**丢掉** —— 它不会换档，留着只会在动画结束后做一次空动作。
        """
        effective = self.effective
        if shifted_index(effective, step) == effective:
            return False
        self._pending_size_step += step
        return True

    def step(self, step: int) -> int:
        """**立刻走一步**（不在动画里时），返回会落到的档位；走不动就是当前那一档。"""
        target = shifted_index(self._size_index, step)
        if target == self._size_index:
            return self._size_index
        return target

    def take_pending(self) -> int:
        """**把存着的步进一次取走**，返回累加出来的总数（``0`` = 没什么要做的）。

        **先清空再返回**：执行途中会再起一段新的过渡，不先清空就会一次调用套一次调用地
        往下钻。档位**不在这里落** —— 落的是调用点，它还要走一遍「走不动就放弃」的判断。
        """
        step, self._pending_size_step = self._pending_size_step, 0
        return step
