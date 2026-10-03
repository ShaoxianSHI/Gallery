"""编辑草稿：一份**只存在于内存里**的操作日志（见 ADR 0027）。

用户在大图页上转一下、镜一下、裁一下，画面立刻变，但**磁盘上一个字节都没有动** ——
草稿就是这里描述的这一串操作，它跟着会话走，⌘S 才把它变成一个新文件。

## 为什么是「操作日志」而不是「最终状态」

先转 90° 再裁剪：裁剪框是**转完之后那张图上的**一个框。如果存「最终结果」，第二次操作就得
把已经裁好的框在新图里重新定位一遍，而那个映射要分四种情况讨论。这里存顺序，于是
「把第 1、2 条依次作用在解码出来的像素上」永远就是用户看到的那张图，不需要任何换算。

## 规范化：为什么能简化成一个「元素」

旋转与镜像都属于**二面体群 D4**：任意一串旋转与镜像合成下来，最终只有 8 种可能
（转 0/90/180/270 × 镜像与否），正好是 EXIF ``Orientation`` 的 1–8。于是

- :func:`normalized` 把每一段**连续**的旋转/镜像并成至多一步：两次左转等于一次右转，
  镜像两次等于没镜像 —— 于是「转了 360°」不会白白产出一份与原文件一模一样的副本；
- :func:`compose_orientation` 把整串操作合成到文件**原有的** Orientation 上，答出
  「这份副本该写哪个 Orientation 值」。

**合成是以文件里已有的 Orientation 为基数的**，不是从 1 开始：竖拍照片本来就带
``Orientation=6``，在它之上再转 90° 的答案是 8。从 1 算起会在镜像那一半全错。

**合成不了的只有一种**：裁剪 —— 它切掉像素，不是 D4 里的元素。这一档由
:func:`compose_orientation` 返回 ``None`` 表示，调用方改走重新编码那条路。

## 这一层不碰 ``gallery.core`` 的别处

唯一的同层依赖是 Pillow。用 ``edit`` 的地方（解码、另存副本）都在它之下或之上，
不构成环 —— 尤其 :func:`orientation_of` 放在这里而不是 ``decode``：解码要用它摆正，
另存副本也要用它当合成基数，而解码又要给另存副本提供像素，谁都不该反向依赖谁。

入参必须是**已经按 EXIF 摆正**的图像（摆正由 :mod:`~gallery.core.media.decode` 负责）：
合成 Orientation 用的基数与摆正用的是同一个方向，两边不一致的话，存出来的副本
在 Gallery 里与在别的软件里会长得不一样。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, TypeGuard

from PIL import Image

#: EXIF ``Orientation`` 标签号。
ORIENTATION_TAG: Final = 274

#: 合法的 ``Orientation`` 取值范围。
_MIN_ORIENTATION: Final = 1
_MAX_ORIENTATION: Final = 8

#: 裁剪之后最短边至少要留这么多像素 —— 挡住「手滑拖出一个 8 像素的框」。
MIN_CROP_EDGE: Final = 320

#: 裁剪之后至少要留下这么多像素（约 800×500）。只卡最短边的话，一条 4000×400 的细长
#: 也会被放过去，而那显然不是一张能看的照片。
MIN_CROP_PIXELS: Final = 400_000


@dataclass(frozen=True, slots=True)
class Rotate:
    """逆时针旋转。:attr:`degrees` 只取 90 / 180 / 270（转 0° 不必记一笔）。"""

    degrees: int

    def __post_init__(self) -> None:
        if self.degrees not in (90, 180, 270):
            raise ValueError(f"旋转只支持 90 / 180 / 270 度，收到 {self.degrees}")


@dataclass(frozen=True, slots=True)
class Mirror:
    """左右镜像。**只有这一种** —— 上下镜像在常用相册软件里几乎不出现（ADR 0027 决策 3）。"""


@dataclass(frozen=True, slots=True)
class Crop:
    """裁剪。矩形是**归一化坐标** ``(x0, y0, x1, y1)``，与分辨率无关。

    它指的是**这一条作用时**那张图的坐标 —— 这正是「存操作日志而不是最终状态」要买的东西。
    """

    rect: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        x0, y0, x1, y1 = self.rect
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError(f"裁剪框必须在 0–1 之间且不能是空的：{self.rect}")


type EditStep = Rotate | Mirror | Crop


# ---- D4：旋转与镜像的合成 ----------------------------------------------------------
#
# 每个元素作用在坐标上的**线性部分**写成四元组 ``(a, b, c, d)``，省掉的那一截平移
# 只与画布尺寸有关，与合成无关。约定是「先左右镜像、再逆时针转若干个 90 度」。

#: 逆时针转 0 / 90 / 180 / 270 度的线性部分。
_ROTATION_MATRICES: Final[tuple[tuple[int, int, int, int], ...]] = (
    (1, 0, 0, 1),
    (0, 1, -1, 0),
    (-1, 0, 0, -1),
    (0, -1, 1, 0),
)

#: 左右镜像的线性部分。
_MIRROR_MATRIX: Final[tuple[int, int, int, int]] = (-1, 0, 0, 1)

#: 线性部分 → ``Orientation``。这张表**是**「EXIF 八个值」与「旋转/镜像」的对应关系，
#: 合成与测试两边都引用它 —— 写两遍必然漂移，而漂移出来的后果是「存出来的副本在
#: 别的软件里转错了方向」。
#:
#: **注意两个方向是反的**：EXIF 说的是顺时针（Pillow 按它实现成 ``ROTATE_270``），
#: 而 :class:`Rotate` 记的是逆时针。于是逆时针 90°（``_ROTATION_MATRICES[1]``）对应的是
#: ``8`` 而不是 ``6``。这张表写反过一次，测试立刻抓到 —— 别动它。
_ORIENTATION_BY_MATRIX: Final[dict[tuple[int, int, int, int], int]] = {
    (1, 0, 0, 1): 1,
    (-1, 0, 0, 1): 2,
    (-1, 0, 0, -1): 3,
    (1, 0, 0, -1): 4,
    (0, 1, 1, 0): 5,
    (0, -1, 1, 0): 6,
    (0, -1, -1, 0): 7,
    (0, 1, -1, 0): 8,
}

#: ``Orientation`` 的取值 → 线性部分（反查表，建一次就够）。
_MATRIX_BY_ORIENTATION: Final[dict[int, tuple[int, int, int, int]]] = {
    orientation: matrix for matrix, orientation in _ORIENTATION_BY_MATRIX.items()
}

_IDENTITY: Final[tuple[int, int, int, int]] = (1, 0, 0, 1)


def _multiply(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """两个 2×2 矩阵相乘，写成四元组。"""
    a1, b1, c1, d1 = first
    a2, b2, c2, d2 = second
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
    )


def _then(
    earlier: tuple[int, int, int, int], later: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """「先施加 ``earlier``，再施加 ``later``」的合成。

    顺序写死在名字与签名里（而不是让调用处自己保证）：矩阵乘法不可交换，两边写反了
    算出来仍然是 D4 里的一个元素，测试要等到比对像素时才会发现转错了方向。
    """
    return _multiply(later, earlier)


def _step_matrix(step: EditStep) -> tuple[int, int, int, int] | None:
    """一步操作的线性部分；裁剪不是 D4 元素，返回 ``None``。"""
    if isinstance(step, Rotate):
        return _ROTATION_MATRICES[(step.degrees // 90) % 4]
    if isinstance(step, Mirror):
        return _MIRROR_MATRIX
    return None


def _is_plain_rotation(matrix: tuple[int, int, int, int]) -> bool:
    return matrix in _ROTATION_MATRICES


def _turns_of(matrix: tuple[int, int, int, int]) -> int:
    """这个元素转了多少个逆时针 90 度（0–3）。"""
    if _is_plain_rotation(matrix):
        return _ROTATION_MATRICES.index(matrix)
    # 带镜像的元素满足「矩阵 = 旋转 × 镜像」，而镜像自乘为单位矩阵，于是左乘它就还原出旋转。
    return _ROTATION_MATRICES.index(_multiply(matrix, _MIRROR_MATRIX))


def orientation_of(image: Image.Image) -> int:
    """这张图**存着的**方向（EXIF ``Orientation`` 的 1–8，没有就是 1）。

    **先看 ``info["original_orientation"]``，再看 EXIF 标签 —— 顺序不能反。**
    ``pillow-heif`` 解码 HEIF 时会把 EXIF 里那个标签**重置成 1**，把真值挪进
    ``info["original_orientation"]``（实测：exiftool 写进去的 ``Orientation=6``，
    Pillow 读标签是 1，读 info 才是 6）。先看标签的话，那个 1 会被当成「方向正常」，
    于是**所有竖拍 HEIC 都是躺着的** —— 而 HEIC 正是 iPhone 照片的默认格式。

    JPEG 没有那个 info 键，自然落到 EXIF 那一支。

    读出来的是**文件里存着的**方向，不是「应该显示成什么样」：合成草稿时以它为基数，
    显示那一侧则由 :mod:`~gallery.core.media.decode` 负责摆正。
    """
    carried = image.info.get("original_orientation")
    if _usable(carried) and carried != 1:
        return carried
    tag = image.getexif().get(ORIENTATION_TAG)
    if _usable(tag):
        return tag
    return 1


def _usable(value: object) -> TypeGuard[int]:
    """这是个能用的方向值吗（1–8 的整数；``bool`` 是 ``int`` 的子类，要单独挡掉）。"""
    return isinstance(value, int) and not isinstance(value, bool) and _valid(value)


def _valid(value: int) -> bool:
    return _MIN_ORIENTATION <= value <= _MAX_ORIENTATION


def normalized(steps: tuple[EditStep, ...]) -> tuple[EditStep, ...]:
    """把每一段**连续**的旋转 / 镜像并成至多一步，裁剪原样留着。

    「连续」是刻意的：中间隔着一个裁剪时，两侧的旋转**不能**合并 —— 裁剪框记的是它
    当时那张图的坐标，把它挪到另一个坐标系里去算，比保留三步更难解释，也更容易错。

    规范化之后，「转了 90° 又转回来」变成空列表，:func:`is_identity` 因此能答出
    「这张其实没改动」，于是 ⌘S 不会白白产出一份与原文件一模一样的副本。
    """
    result: list[EditStep] = []
    pending: list[tuple[int, int, int, int]] = []
    for step in steps:
        matrix = _step_matrix(step)
        if matrix is None:
            result.extend(_flush(pending))
            pending = []
            result.append(step)
            continue
        pending.append(matrix)
    result.extend(_flush(pending))
    return tuple(result)


def _flush(pending: list[tuple[int, int, int, int]]) -> list[EditStep]:
    """把攒下的一段旋转 / 镜像并成一步（恒等时返回空）。"""
    if not pending:
        return []
    matrix = pending[0]
    for element in pending[1:]:
        matrix = _then(matrix, element)
    turns = _turns_of(matrix)
    mirrored = not _is_plain_rotation(matrix)
    if turns == 0 and not mirrored:
        return []
    # 顺序固定为「先镜像、再旋转」：那正是这个元素的定义（见模块里那张表的推导）。
    steps: list[EditStep] = []
    if mirrored:
        steps.append(Mirror())
    if turns:
        steps.append(Rotate(turns * 90))
    return steps


def is_identity(steps: tuple[EditStep, ...]) -> bool:
    """这些操作合起来什么都没做（转了 360°、镜像了两次、什么都没点）。"""
    return not normalized(steps)


def compose_orientation(base: int, steps: tuple[EditStep, ...]) -> int | None:
    """在 ``base``（文件里原有的 Orientation）之上施加 ``steps``，答出新的 Orientation。

    :param base: 1–8，通常由 :func:`orientation_of` 读出；范围外的值按 1 处理。
    :return: 新的 Orientation；**含裁剪时返回 ``None``** —— 那一条路只能重新编码像素。

    以 ``base`` 为基数是必须的：竖拍照片本来就带 6，在它之上再转 90° 的答案是 8，
    从 1 算起会在镜像那一半全错（见模块文档）。
    """
    matrix = _MATRIX_BY_ORIENTATION.get(base, _IDENTITY)
    for step in normalized(steps):
        element = _step_matrix(step)
        if element is None:
            return None  # 裁剪：像素被切掉了，标签表达不出来
        matrix = _then(matrix, element)
    return _ORIENTATION_BY_MATRIX.get(matrix)


# ---- 作用在像素上 ------------------------------------------------------------------


def apply_steps(image: Image.Image, steps: tuple[EditStep, ...]) -> Image.Image:
    """按顺序施加 ``steps``，返回新图。``image`` 本身不动（没操作时原样返回）。"""
    result = image
    for step in steps:
        if isinstance(step, Rotate):
            result = result.transpose(_rotation_of(step.degrees))
        elif isinstance(step, Mirror):
            result = result.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        else:
            result = result.crop(crop_box(step.rect, result.size))
    return result


def _rotation_of(degrees: int) -> Image.Transpose:
    return {
        90: Image.Transpose.ROTATE_90,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_270,
    }[degrees]


def crop_box(
    rect: tuple[float, float, float, float], size: tuple[int, int]
) -> tuple[int, int, int, int]:
    """归一化的裁剪框 → 该图尺寸下的像素框。

    边界一律**夹在画布内**，右/下取整用 ``ceil`` —— 取整必须朝「多留一点」的方向，
    否则一个正正好落在半像素上的框会少一列，而用户拖出来的框看着就是那么宽。
    """
    width, height = size
    x0, y0, x1, y1 = rect
    left = _clamp(math.floor(x0 * width), 0, width)
    top = _clamp(math.floor(y0 * height), 0, height)
    right = _clamp(math.ceil(x1 * width), left + 1, width)
    bottom = _clamp(math.ceil(y1 * height), top + 1, height)
    return (left, top, right, bottom)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def is_crop_big_enough(rect: tuple[float, float, float, float], size: tuple[int, int]) -> bool:
    """裁完还剩不剩一张能看的东西（最短边与总像素各有一道下限）。"""
    left, top, right, bottom = crop_box(rect, size)
    width = right - left
    height = bottom - top
    return width >= MIN_CROP_EDGE and height >= MIN_CROP_EDGE and width * height >= MIN_CROP_PIXELS


__all__ = [
    "MIN_CROP_EDGE",
    "MIN_CROP_PIXELS",
    "ORIENTATION_TAG",
    "Crop",
    "EditStep",
    "Mirror",
    "Rotate",
    "apply_steps",
    "compose_orientation",
    "crop_box",
    "is_crop_big_enough",
    "is_identity",
    "normalized",
    "orientation_of",
]
