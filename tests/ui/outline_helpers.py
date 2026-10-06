"""界面测试共用的**轮廓像素**助手 —— 那一圈 1 点宽的圆角边框。

收在这里的理由与 :mod:`waiting` 一样：**量法只有一份**。轮廓上那八个等分点深浅的判据
（哪几个算「等分点」、邻域取多大、底色那一层怎么算）在三份测试里都要用到，各写一遍
必然出现「三处量法不一样，于是只有一处会红」。

- :func:`outline_darkness` —— 轮廓上**那八个等分点**与其余各点的暗度。
- :func:`assert_no_darker_dots` —— 断「那八个点不比别处更深」，**并且先断那一圈确实画出来了**。

**为什么用 ``grab()``**：它会**强制**重画，所以量到的是此刻真的会被画出来的东西 ——
屏幕上的陈旧像素在那种探针里永远看不见（``PhotoGrid`` 那条教训）。
"""

from __future__ import annotations

import math
import statistics

from PySide6.QtWidgets import QWidget

#: 邻域取多大（控件坐标的点）。1 就是中心加周围一圈。
NEIGHBORHOOD = 1

#: 「那一圈确实画出来了」要比底色深这么多（0..255）才算数。太小的阈值会被抗锯齿的边缘
#: 混进来：中心那一格只有 30% 覆盖时也已经比底色深 26。
MIN_CONTRAST = 10

#: 八个等分点允许比别处深这么多。**留了 6 是量测本身的噪声**：轮廓是抗锯齿出来的，
#: 同一个形状在不同 dpr 下采样落点差半格就是好几档。
TOLERANCE = 6


def outline_darkness(widget: QWidget, radius: float | None = None) -> tuple[list[int], list[int]]:
    """轮廓上**那八个等分点**与其余各点的暗度（越小越深）。

    轮廓按控件自己的圆角矩形走：左右两段圆角（胶囊、输入框）或整圈（圆形按钮）各取 361 个
    采样点。八个等分点 = 每段圆角的两端（与直边相接）与两个 45° —— **正是 Qt 把那一圈描成八段时
    接缝所在的位置**。

    :param radius: 那一圈的圆角半径。**胶囊与圆形按钮可以不传**（半径就是高度的一半），
        但浮层那种「圆角比高度一半小」的形状**必须给** —— 不给就按高度一半去采样，量到的
        是轮廓内侧一块空白，八个点与别处自然一样深，:func:`assert_no_darker_dots` 于是恒真
        （这一条自己第一版就栽在这里：候选列表与下拉菜单按胶囊量了半年，绿得毫无意义）。

    采样按**控件坐标**换算到图片像素，所以离屏（dpr 1）与真机（dpr 2）量的是同一处。
    """
    image = widget.grab().toImage()
    width, height = widget.width(), widget.height()
    scale = image.width() / width
    radius = min(height / 2, width / 2) if radius is None else radius

    def dark(x: float, y: float) -> int:
        px, py = int(round(x * scale)), int(round(y * scale))
        if not (0 <= px < image.width() and 0 <= py < image.height()):
            return 255
        color = image.pixelColor(px, py)
        return (color.red() + color.green() + color.blue()) // 3

    def darkest(x: float, y: float) -> int:
        return min(
            dark(x + dx, y + dy)
            for dx in range(-NEIGHBORHOOD, NEIGHBORHOOD + 1)
            for dy in range(-NEIGHBORHOOD, NEIGHBORHOOD + 1)
        )

    divisions: list[int] = []
    elsewhere: list[int] = []
    for i in range(0, 361):
        angle = math.radians(-90 + 180 * i / 360)
        for center_x in (width - radius, radius):
            value = darkest(center_x + radius * math.cos(angle), radius + radius * math.sin(angle))
            (divisions if i in (0, 45, 135, 180) else elsewhere).append(value)
    if width > 2 * height:  # 上下那两段直边只在这个形状上真的存在
        for i in range(0, 361):
            elsewhere.append(darkest(radius + (width - 2 * radius) * i / 360, 0.0))
            elsewhere.append(darkest(width - radius - (width - 2 * radius) * i / 360, height - 1.0))
    return divisions, elsewhere


def assert_no_darker_dots(
    widget: QWidget, background: int, *, radius: float | None = None, label: str = ""
) -> None:
    """断轮廓上**那八个等分点不比别处更深**；顺带先断「那一圈确实画出来了」。

    :param background: 紧挨着轮廓那一层的底色暗度（0..255）—— 用来判「量到了轮廓」而不是
        量到了底色。控件压根没画轮廓时采样全落在底色上，八个点与别处自然一样深，那条断言
        就成了恒真（这一条自己第一版就是这么空转的）。
    :param radius: 透传给 :func:`outline_darkness`；浮层那种圆角小于高度一半的形状**必须给**。

    ``label`` 只进消息，用来在多控件那条用例里指明是哪一个。
    """
    divisions, elsewhere = outline_darkness(widget, radius)
    base = statistics.median(elsewhere)
    name = label or widget.objectName() or type(widget).__name__
    assert base <= background - MIN_CONTRAST, f"{name}：压根量不到那一圈轮廓（量到的是底色）"
    worst = min(divisions)
    assert worst >= base - TOLERANCE, f"{name}：轮廓上那八个等分点最深 {worst}，别处中位 {base}"
