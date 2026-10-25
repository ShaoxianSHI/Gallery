"""背景库：生成的按配方重算、照片只读不写、清单丢了不牵连别的条；配色那一行也在这里。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from gallery.core.backdrops import (
    CUSTOM_PRESET,
    PALETTE_CUSTOM,
    PALETTE_PRESET,
    PALETTES,
    BackdropStore,
    PaletteEntry,
    default_palette_row,
    palette_from_colors,
)
from gallery.core.backdrops.generate import colorize, render_field
from gallery.core.backdrops.scrim import scrim_alpha


def _store(tmp_path: Path) -> BackdropStore:
    return BackdropStore(cache_dir=tmp_path / "缓存", registry=tmp_path / "清单.json")


def _photo(tmp_path: Path, color: tuple[int, int, int] = (30, 60, 120)) -> Path:
    path = tmp_path / "IMG_0001.jpg"
    Image.new("RGB", (640, 480), color).save(path, quality=95)
    return path


def test_the_same_recipe_renders_the_same_bytes(tmp_path: Path) -> None:
    """**同配方同像素** —— 颗粒、噪声、配色全走自己的种子，不碰进程内的全局随机数。"""
    recipe = PALETTES[3].recipe(20261025)
    first = colorize(recipe, render_field(recipe))
    second = colorize(recipe, render_field(recipe))

    assert first.rgba == second.rgba


def test_a_different_seed_gives_a_different_picture() -> None:
    first = colorize(PALETTES[3].recipe(1), render_field(PALETTES[3].recipe(1)))
    second = colorize(PALETTES[3].recipe(2), render_field(PALETTES[3].recipe(2)))

    assert first.rgba != second.rgba


def test_a_generated_backdrop_lands_in_the_cache(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recipe = PALETTES[0].recipe(7)

    item = store.add_generated(recipe, "雾蓝")

    assert item.kind == "ink"
    assert item.recipe == recipe
    assert store.path_of(item).is_file()
    assert (tmp_path / "清单.json").is_file(), "清单要落在缓存目录之外"


def test_the_same_recipe_is_not_stored_twice(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recipe = PALETTES[0].recipe(7)

    first = store.add_generated(recipe, "雾蓝")
    second = store.add_generated(recipe, "另一个名字")

    assert first.id == second.id
    assert len(store.items()) == 1


def test_a_repeated_name_gets_a_number(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_generated(PALETTES[0].recipe(1), "雾蓝")
    second = store.add_generated(PALETTES[1].recipe(1), "雾蓝")

    assert second.name == "雾蓝 2"


def test_importing_a_photo_does_not_touch_the_original(tmp_path: Path) -> None:
    """**只读用户那张照片**：导入之后它的字节与 mtime 一个都不许变。"""
    source = _photo(tmp_path)
    before = (hashlib.sha1(source.read_bytes()).hexdigest(), source.stat().st_mtime_ns)

    item = _store(tmp_path).add_photo(source, "海边")

    after = (hashlib.sha1(source.read_bytes()).hexdigest(), source.stat().st_mtime_ns)
    assert after == before
    assert item.kind == "photo"
    assert item.source == str(source)


def test_a_photo_is_scaled_down_and_stored_as_jpeg(tmp_path: Path) -> None:
    path = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 3000), (10, 20, 30)).save(path, quality=95)

    item = _store(tmp_path).add_photo(path, "大图")

    with Image.open(tmp_path / "缓存" / item.file) as stored:
        assert max(stored.size) <= 2560
        assert stored.format == "JPEG"


def test_removing_a_backdrop_leaves_the_users_photo_alone(tmp_path: Path) -> None:
    source = _photo(tmp_path)
    store = _store(tmp_path)
    item = store.add_photo(source, "海边")

    store.remove(item.id)

    assert store.items() == ()
    assert not store.path_of(item).exists()
    assert source.is_file(), "删背景不许删用户那张照片"


def test_removing_a_backdrop_that_is_gone_is_harmless(tmp_path: Path) -> None:
    _store(tmp_path).remove("ink-不存在")


def test_renaming_dedupes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.add_generated(PALETTES[0].recipe(1), "甲")
    store.add_generated(PALETTES[1].recipe(1), "乙")

    renamed = store.rename(first.id, "乙")

    assert renamed.name == "乙 2"


def test_a_generated_backdrop_is_rebuilt_from_its_recipe(tmp_path: Path) -> None:
    """**缓存丢了也能长回来** —— 这正是「把图放缓存」成立的前提。"""
    store = _store(tmp_path)
    item = store.add_generated(PALETTES[4].recipe(11), "灰阶")
    store.path_of(item).unlink()

    notes = store.repair()

    assert store.available(store.get(item.id))
    assert notes and "灰阶" in notes[0]


def test_a_photo_backdrop_is_reimported_from_its_source(tmp_path: Path) -> None:
    source = _photo(tmp_path)
    store = _store(tmp_path)
    item = store.add_photo(source, "海边")
    store.path_of(item).unlink()

    store.repair()

    assert store.available(store.get(item.id))


def test_a_photo_whose_source_is_gone_stays_listed(tmp_path: Path) -> None:
    """补不回来**也不替用户删清单那一条** —— 那等于替他做了决定。"""
    source = _photo(tmp_path)
    store = _store(tmp_path)
    item = store.add_photo(source, "海边")
    store.path_of(item).unlink()
    source.unlink()

    store.repair()

    assert store.get(item.id) is not None
    assert not store.available(store.get(item.id))  # type: ignore[arg-type]


def test_a_broken_registry_reads_as_empty(tmp_path: Path) -> None:
    (tmp_path / "清单.json").write_text("{ 这不是 json", encoding="utf-8")

    assert _store(tmp_path).items() == ()


def test_one_broken_entry_does_not_take_the_others_down(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_generated(PALETTES[0].recipe(1), "好的一条")
    path = tmp_path / "清单.json"
    payload = path.read_text(encoding="utf-8")
    path.write_text(
        payload.replace('"kind": "ink"', '"kind": "ink"').replace(
            '"items": [', '"items": [{"id": "坏的"}, ', 1
        ),
        encoding="utf-8",
    )

    assert [item.name for item in _store(tmp_path).items()] == ["好的一条"]


def test_a_blank_name_is_refused(tmp_path: Path) -> None:
    from gallery.core.backdrops import BackdropError

    with pytest.raises(BackdropError):
        _store(tmp_path).add_generated(PALETTES[0].recipe(1), "   ")


def test_a_dark_picture_gets_a_scrim_in_the_light_slot() -> None:
    assert scrim_alpha(0.1, 0.95, dark=False) > 0.5


def test_a_light_picture_needs_no_scrim_in_the_light_slot() -> None:
    assert scrim_alpha(0.93, 0.95, dark=False) == 0.0


def test_a_light_picture_gets_a_scrim_in_the_dark_slot() -> None:
    assert scrim_alpha(0.9, 0.1, dark=True) > 0.5


# ---- 那一行配色（ADR 0105）---------------------------------------------------


def _own_entry(colours: tuple[str, ...] = ("#336699", "#b06a3b")) -> PaletteEntry:
    return PaletteEntry("palette-0123456789ab", PALETTE_CUSTOM, "自定义", colours)


def _registry(tmp_path: Path) -> Path:
    return tmp_path / "清单.json"


def test_the_palette_row_is_kept_next_to_the_backdrops(tmp_path: Path) -> None:
    store = _store(tmp_path)
    item = store.add_generated(PALETTES[0].recipe(7), "雾蓝")
    row = (*default_palette_row(), _own_entry())

    store.save_palettes(row)

    reopened = _store(tmp_path)
    assert reopened.palettes() == row
    assert [entry.id for entry in reopened.items()] == [item.id], "写配色不许动背景那几条"


def test_the_same_registry_carries_both_kinds_of_data(tmp_path: Path) -> None:
    """清单只有一份：背景与配色写进去之后，谁也不会把谁顶掉。"""
    store = _store(tmp_path)
    row = (*default_palette_row(), _own_entry())
    store.save_palettes(row)
    store.add_generated(PALETTES[1].recipe(7), "奶白")

    data = json.loads(_registry(tmp_path).read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert len(data["items"]) == 1
    assert len(data["palettes"]) == len(row)
    assert _store(tmp_path).palettes() == row


def test_a_palette_row_equal_to_the_defaults_is_not_written(tmp_path: Path) -> None:
    """用户没动过就不写 —— 以后版本新增的预设才出得来（ADR 0105）。"""
    store = _store(tmp_path)

    store.save_palettes(default_palette_row())

    assert store.palettes() == ()
    assert not _registry(tmp_path).exists()


def test_resetting_the_row_takes_the_section_out(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_palettes((*default_palette_row(), _own_entry()))
    assert "palettes" in json.loads(_registry(tmp_path).read_text(encoding="utf-8"))

    store.save_palettes(default_palette_row())

    assert _store(tmp_path).palettes() == ()
    assert "palettes" not in json.loads(_registry(tmp_path).read_text(encoding="utf-8"))


def test_a_pristine_preset_comes_back_as_the_constant() -> None:
    """没改过的预设整份取常量：停点与形状参数都在，不是拿那串颜色重算一遍。"""
    assert default_palette_row()[0].palette() is PALETTES[0]


def test_an_edited_preset_is_rebuilt_from_its_colours() -> None:
    entry = PaletteEntry(
        PALETTES[0].name, PALETTE_PRESET, PALETTES[0].title, ("#336699", "#b06a3b")
    )

    palette = entry.palette()

    assert palette.name == CUSTOM_PRESET
    assert palette.title == PALETTES[0].title
    assert palette.colors == palette_from_colors(["#336699", "#b06a3b"]).colors


def test_a_version_one_registry_still_loads_its_backdrops(tmp_path: Path) -> None:
    """升级不许把老清单读成空：v1 里没有配色，背景那几条照样要回来（ADR 0105）。"""
    store = _store(tmp_path)
    item = store.add_generated(PALETTES[0].recipe(3), "雾蓝")
    path = _registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = 1
    data.pop("palettes", None)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    reopened = _store(tmp_path)

    assert [entry.name for entry in reopened.items()] == [item.name]
    assert reopened.palettes() == ()


def test_an_unknown_registry_version_reads_as_empty(tmp_path: Path) -> None:
    _registry(tmp_path).write_text('{"version": 99, "items": []}', encoding="utf-8")

    assert _store(tmp_path).items() == ()
    assert _store(tmp_path).palettes() == ()


def test_one_broken_palette_entry_does_not_take_the_others_down(tmp_path: Path) -> None:
    store = _store(tmp_path)
    good = _own_entry()
    store.save_palettes((*default_palette_row(), good))
    path = _registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["palettes"].insert(
        1,
        {"key": "ink-不存在", "kind": "preset", "title": "不认识", "colors": list(good.colours)},
    )
    data["palettes"].insert(
        2, {"key": "palette-坏的", "kind": "custom", "title": "", "colors": list(good.colours)}
    )
    data["palettes"].insert(
        3, {"key": "palette-坏色", "kind": "custom", "title": "坏色", "colors": ["#000"]}
    )
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    assert _store(tmp_path).palettes() == (*default_palette_row(), good)
