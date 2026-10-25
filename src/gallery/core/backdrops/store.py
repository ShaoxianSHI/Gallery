"""背景库：清单在应用状态目录，图片在应用缓存目录。

**清单不进缓存** —— 名字是用户填的，重扫重建不出来。图片可以丢：生成图按配方重算，
照片按记下的来源路径重新导入。这条自愈是「把图放缓存」能成立的前提。
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from typing import Any, Final

from PIL import Image, ImageOps

from gallery.core import paths
from gallery.core.atomic import read_text_or_none, write_bytes_atomic, write_text_atomic
from gallery.core.backdrops.generate import render
from gallery.core.backdrops.recipe import Recipe, palette_of
from gallery.core.errors import GalleryError
from gallery.core.media.decode import RasterImage

#: 清单格式版本。读的时候认它，结构一变好让老文件按「读不懂就回空」处理。
REGISTRY_VERSION: Final = 1

#: 生成图与照片两种来源。
KIND_INK: Final = "ink"
KIND_PHOTO: Final = "photo"
KINDS: Final = (KIND_INK, KIND_PHOTO)

#: 照片转存时长边缩到这么多、按 JPEG 存。原样存 PNG 的话一张就十几 MB。
MAX_PHOTO_EDGE: Final = 2560
PHOTO_QUALITY: Final = 90

#: 名字的最大长度。菜单里那一行放不下更长的，而它只是个标签。
MAX_NAME_LENGTH: Final = 40


class BackdropError(GalleryError):
    """背景库的操作失败（名字不合法、照片解不开、写不进去）。"""


@dataclass(frozen=True, slots=True)
class Backdrop:
    """背景库里的一张图。``file`` 是缓存目录下的文件名，不含目录。"""

    id: str
    name: str
    kind: str
    file: str
    created: int
    recipe: Recipe | None = None
    source: str = ""

    def to_mapping(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "created": self.created,
        }
        if self.recipe is not None:
            data["recipe"] = self.recipe.to_mapping()
        if self.source:
            data["source"] = self.source
        return data

    @classmethod
    def from_mapping(cls, data: Any) -> Backdrop:
        """读一条。**任何一处不对都抛** —— 由调用方把这一条丢掉，不牵连其余。"""
        kind = str(data["kind"])
        if kind not in KINDS:
            raise ValueError(f"不认识的背景类型：{kind}")
        recipe = data.get("recipe")
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            kind=kind,
            file=str(data["file"]),
            created=int(data["created"]),
            recipe=Recipe.from_mapping(recipe) if recipe is not None else None,
            source=str(data.get("source") or ""),
        )


class BackdropStore:
    """背景库的读写。**构造不碰磁盘**，第一次取值时再读清单。"""

    def __init__(self, *, cache_dir: Path | None = None, registry: Path | None = None) -> None:
        self._cache_dir = cache_dir if cache_dir is not None else paths.app_backdrop_dir()
        self._registry_path = registry if registry is not None else paths.app_backdrop_registry()
        self._items: tuple[Backdrop, ...] | None = None

    # ---- 读 ----------------------------------------------------------------

    def items(self) -> tuple[Backdrop, ...]:
        """清单里的全部背景，按保存顺序。**读不懂就回空**（与 ``state.json`` 同一套待遇）。"""
        if self._items is None:
            self._items = self._load()
        return self._items

    def get(self, backdrop_id: str) -> Backdrop | None:
        for item in self.items():
            if item.id == backdrop_id:
                return item
        return None

    def path_of(self, backdrop: Backdrop) -> Path:
        return self._cache_dir / backdrop.file

    def available(self, backdrop: Backdrop) -> bool:
        return self.path_of(backdrop).is_file()

    def load(self, backdrop: Backdrop) -> RasterImage:
        """把一张背景图读成裸像素。**界面不解码图片**，解码与亮度都在这一侧。"""
        path = self.path_of(backdrop)
        try:
            with Image.open(path) as image:
                return RasterImage.from_pil(image.convert("RGBA"), has_alpha=False)
        except OSError as exc:
            raise BackdropError(
                f"无法读取这张背景图：{path.name}", path=path, detail=str(exc)
            ) from exc

    # ---- 写 ----------------------------------------------------------------

    def add_generated(self, recipe: Recipe, name: str) -> Backdrop:
        """存一张生成的图。同一份配方只存一次（``id`` 就是配方哈希）。"""
        clean = self._clean_name(name)
        backdrop_id = f"{KIND_INK}-{recipe.digest()}"
        existing = self.get(backdrop_id)
        if existing is not None:
            return existing
        raster = render(recipe)
        file_name = f"{backdrop_id}.png"
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            write_bytes_atomic(self._cache_dir / file_name, _png_bytes(raster), durable=False)
        except OSError as exc:
            raise BackdropError(
                f"背景图未能写进缓存：{self._cache_dir}", path=self._cache_dir, detail=str(exc)
            ) from exc
        backdrop = Backdrop(
            id=backdrop_id,
            name=self._unique_name(clean),
            kind=KIND_INK,
            file=file_name,
            created=int(time.time()),
            recipe=recipe,
        )
        self._commit((*self.items(), backdrop))
        return backdrop

    def add_photo(self, source: Path, name: str) -> Backdrop:
        """把用户挑的一张照片转存进缓存。**只读原文件，一个字节都不写回去。**"""
        source = Path(source)
        clean = self._clean_name(name)
        try:
            payload = source.read_bytes()
        except OSError as exc:
            raise BackdropError(
                f"无法读取这张照片：{source}", path=source, detail=str(exc)
            ) from exc
        backdrop_id = f"{KIND_PHOTO}-{hashlib.sha1(payload).hexdigest()[:12]}"
        existing = self.get(backdrop_id)
        if existing is not None:
            return existing
        file_name = f"{backdrop_id}.jpg"
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            write_bytes_atomic(self._cache_dir / file_name, _photo_bytes(source), durable=False)
        except OSError as exc:
            raise BackdropError(
                f"无法解码这张照片，请更换后重试：{source}", path=source, detail=str(exc)
            ) from exc
        backdrop = Backdrop(
            id=backdrop_id,
            name=self._unique_name(clean),
            kind=KIND_PHOTO,
            file=file_name,
            created=int(time.time()),
            source=str(source),
        )
        self._commit((*self.items(), backdrop))
        return backdrop

    def rename(self, backdrop_id: str, name: str) -> Backdrop:
        clean = self._clean_name(name)
        items = list(self.items())
        for index, item in enumerate(items):
            if item.id != backdrop_id:
                continue
            others = [other.name for other in items if other.id != backdrop_id]
            items[index] = replace(item, name=_dedupe(clean, others))
            self._commit(tuple(items))
            return items[index]
        raise BackdropError("这张背景已不存在，可能刚被删除。")

    def remove(self, backdrop_id: str) -> None:
        """删一张。**只删缓存目录下的自有文件**，用户的照片一个字节都不碰。"""
        kept = tuple(item for item in self.items() if item.id != backdrop_id)
        if len(kept) == len(self.items()):
            return
        for item in self.items():
            if item.id == backdrop_id:
                with suppress(OSError):
                    self.path_of(item).unlink(missing_ok=True)
        self._commit(kept)

    # ---- 自愈 --------------------------------------------------------------

    def repair(self) -> list[str]:
        """把缓存里丢了的图补回来。返回做过的事，交给横幅去说。

        生成图按配方重算；照片按来源路径重新导入。补不回来（原图也没了）就**留着清单那一条**，
        等用户自己处理 —— 删掉等于替他做了决定。
        """
        done: list[str] = []
        rebuilt: list[Backdrop] = []
        changed = False
        for item in self.items():
            if self.available(item):
                rebuilt.append(item)
                continue
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                if item.kind == KIND_INK and item.recipe is not None:
                    raster = render(item.recipe)
                    write_bytes_atomic(self.path_of(item), _png_bytes(raster), durable=False)
                    done.append(f"背景「{item.name}」已按配方重新生成")
                    changed = True
                elif item.kind == KIND_PHOTO and item.source:
                    source = Path(item.source)
                    if source.is_file():
                        write_bytes_atomic(self.path_of(item), _photo_bytes(source), durable=False)
                        done.append(f"背景「{item.name}」已从原图重新导入")
                        changed = True
            except (OSError, GalleryError):
                continue
            rebuilt.append(item)
        if changed:
            self._commit(tuple(rebuilt))
        return done

    # ---- 内部 --------------------------------------------------------------

    def _load(self) -> tuple[Backdrop, ...]:
        text = read_text_or_none(self._registry_path)
        if not text:
            return ()
        try:
            data = json.loads(text)
        except ValueError:
            return ()
        if not isinstance(data, dict) or data.get("version") != REGISTRY_VERSION:
            return ()
        raw = data.get("items")
        if not isinstance(raw, list):
            return ()
        items: list[Backdrop] = []
        for entry in raw:
            try:
                items.append(Backdrop.from_mapping(entry))
            except (KeyError, TypeError, ValueError):
                continue
        return tuple(items)

    def _commit(self, items: tuple[Backdrop, ...]) -> None:
        payload = {
            "version": REGISTRY_VERSION,
            "items": [item.to_mapping() for item in items],
        }
        text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)
        try:
            self._registry_path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(self._registry_path, text)
        except OSError as exc:
            raise BackdropError(
                f"背景清单不可写入：{self._registry_path}",
                path=self._registry_path,
                detail=str(exc),
            ) from exc
        self._items = items

    def _clean_name(self, name: str) -> str:
        clean = " ".join(name.split())[:MAX_NAME_LENGTH]
        if not clean:
            raise BackdropError("请为这张背景填写名称。")
        return clean

    def _unique_name(self, name: str) -> str:
        return _dedupe(name, [item.name for item in self.items()])


def _dedupe(name: str, taken: list[str]) -> str:
    """重名就加序号。菜单里两行一模一样的字，用户分不出哪行是哪张。"""
    if name not in taken:
        return name
    for index in range(2, 100):
        candidate = f"{name} {index}"
        if candidate not in taken:
            return candidate
    return name


def _png_bytes(raster: RasterImage) -> bytes:
    image = Image.frombytes("RGBA", raster.size, raster.rgba).convert("RGB")
    buffer = BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _photo_bytes(source: Path) -> bytes:
    with Image.open(source) as image:
        upright = ImageOps.exif_transpose(image)
        upright.thumbnail((MAX_PHOTO_EDGE, MAX_PHOTO_EDGE), Image.Resampling.LANCZOS)
        rgb = upright.convert("RGB")
        buffer = BytesIO()
        rgb.save(buffer, "JPEG", quality=PHOTO_QUALITY, optimize=True)
        return buffer.getvalue()


def name_suggestion(recipe: Recipe, *, fallback: str = "背景") -> str:
    """生成图的默认名字：配色名。用户在对话框里可以改。

    自定义配色不在预设表里，名字取不到 —— 由调用方把它那套配色的标题当 ``fallback`` 递进来。
    """
    palette = palette_of(recipe)
    return palette.title if palette is not None else fallback


__all__ = [
    "KINDS",
    "KIND_INK",
    "KIND_PHOTO",
    "MAX_NAME_LENGTH",
    "MAX_PHOTO_EDGE",
    "PHOTO_QUALITY",
    "REGISTRY_VERSION",
    "Backdrop",
    "BackdropError",
    "BackdropStore",
    "name_suggestion",
]
