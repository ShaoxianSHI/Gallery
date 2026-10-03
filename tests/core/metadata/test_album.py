"""新建相册的测试（ADR 0022）。

全部在 ``tmp_path`` 里进行：这里测的操作**真的会往用户的目录里写东西**，所以每个
「库」都是当场造的临时目录，照片也是临时文件。

钉住的是四类风险：

1. **只增不改** —— 建出来的是一个**全新的空文件夹**，用户原有的目录与照片一个字节都没动。
2. **绝不覆盖** —— 同名时按访达的办法改叫「新建相册 2」。
3. **边界** —— 目标不是文件夹、不在库内、往配置目录里建、名字非法，一律拒绝且**什么都不建**。
4. **记账与撤销** —— 写进 ``journal/``，而它**不进撤销栈**（撤销就是删文件夹，红线 1）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gallery.core.errors import AlbumError
from gallery.core.metadata.album import DEFAULT_ALBUM_NAME, create_album
from gallery.core.metadata.journal import ACTION_CREATE, RESULT_OK, read_records
from gallery.core.metadata.undo import next_undo_label, undo_last_write
from gallery.core.paths import config_dir


def _listing(directory: Path) -> list[str]:
    """这一层里有什么（不含 ``.gallery``），按名字排好 —— 用来断言「一个字节都没动」。"""
    return sorted(item.name for item in directory.iterdir())


# ---- 真的会建 ----------------------------------------------------------------


def test_creates_an_empty_folder_where_it_should(library: Path) -> None:
    """建在指定的那一层里，名字是默认的「新建相册」，内容是空的。"""
    result = create_album(library, library / "奈良")

    created = library / "奈良" / DEFAULT_ALBUM_NAME
    assert created.is_dir()
    assert list(created.iterdir()) == [], "建出来的是一个空文件夹"
    assert result.album.path == created
    assert result.name == DEFAULT_ALBUM_NAME
    assert result.journaled is True


def test_nothing_else_on_that_level_is_touched(library: Path) -> None:
    """只增不改：用户的目录里只多了一项，别的什么都没动。

    这是红线 1 的落点 —— 新建相册绝不能顺手带走、改名或清空用户的东西。
    """
    before = _listing(library)
    inside = _listing(library / "奈良")
    photo = library / "京都" / "一张.jpg"
    content = photo.read_bytes()

    create_album(library, library / "奈良")

    assert _listing(library / "奈良") == inside + ["新建相册"], "这一层里只多了一项"
    assert _listing(library) == before, "库根与别的相册一个都没动"
    assert _listing(library / "京都") == ["一张.jpg", "另一张.jpg"]
    assert photo.read_bytes() == content


def test_a_custom_name_is_honoured(library: Path) -> None:
    """默认名只是一个默认值：要别的名字照样建得出来。"""
    result = create_album(library, library / "奈良", "2025 苏州")

    assert result.path == library / "奈良" / "2025 苏州"
    assert result.path.is_dir()


def test_the_new_folder_carries_no_gallery_sidecar(library: Path) -> None:
    """建出来的相册是**干净的**：里面只有用户自己往里放的东西。

    ``.gallery`` 只出现在库根上（红线 6），绝不会被顺手塞进每一个新建的相册里。
    """
    create_album(library, library / "奈良")

    assert list((library / "奈良" / DEFAULT_ALBUM_NAME).iterdir()) == []


# ---- 绝不覆盖 ---------------------------------------------------------------


def test_a_duplicate_name_is_numbered_like_the_finder(library: Path) -> None:
    """同名不覆盖，按访达的规矩改叫「新建相册 2」（与粘贴共用同一份实现）。"""
    first = create_album(library, library / "奈良")
    second = create_album(library, library / "奈良")

    assert first.name == "新建相册"
    assert second.name == "新建相册 2"
    assert first.path.is_dir(), "第一个必须原封不动"
    assert second.path.is_dir()


def test_the_existing_directory_is_left_alone(library: Path) -> None:
    """撞名的那个目录里有什么，建相册不该碰。"""
    (library / "奈良" / DEFAULT_ALBUM_NAME).mkdir()
    (library / "奈良" / DEFAULT_ALBUM_NAME / "我的照片.jpg").write_bytes(b"x")

    create_album(library, library / "奈良")

    mine = library / "奈良" / DEFAULT_ALBUM_NAME
    assert _listing(mine) == ["我的照片.jpg"], "用户自己那个目录里不该多出任何东西"


# ---- 边界：建不出来的时候，一个字节都不写 ------------------------------------


def test_refuses_a_target_that_is_not_a_folder(library: Path) -> None:
    photo = library / "京都" / "一张.jpg"

    with pytest.raises(AlbumError) as caught:
        create_album(library, photo)

    assert "不是一个文件夹" in caught.value.message
    assert photo.exists()


def test_refuses_a_target_outside_the_library(library: Path, tmp_path: Path) -> None:
    outside = tmp_path / "相册之外"
    outside.mkdir()

    with pytest.raises(AlbumError) as caught:
        create_album(library, outside)

    assert "只能在库里的文件夹中新建相册" in caught.value.message
    assert _listing(outside) == [], "库外一个字节都不能写"


def test_refuses_to_build_inside_the_config_directory(library: Path) -> None:
    """配置目录是 Gallery 自己的东西，不能被当成一层往里建相册。"""
    inside = config_dir(library)
    with pytest.raises(AlbumError) as caught:
        create_album(library, inside)

    assert "配置目录" in caught.value.message
    assert _listing(inside) == []


@pytest.mark.parametrize(
    ("name", "fragment"),
    [
        ("", "不能为空"),
        ("   ", "不能为空"),
        (".", "保留名字"),
        ("..", "保留名字"),
        (".隐藏", "不能以「.」开头"),
        ("京都/奈良", "不能带"),
        ("换行\n名字", "控制字符"),
        ("长" * 200, "太长"),
    ],
)
def test_refuses_names_that_may_not_be_used(library: Path, name: str, fragment: str) -> None:
    """名字的规矩与改名共用同一份实现（``naming.clean_name``），判据与文案都一样。

    而且**抛异常之前一个字节都没写**。
    """
    with pytest.raises(AlbumError) as caught:
        create_album(library, library / "奈良", name)

    assert fragment in caught.value.message
    assert _listing(library / "奈良") == []


def test_a_read_only_target_is_reported_not_swallowed(library: Path) -> None:
    """没有写权限时说清楚为什么 —— 用户看得懂、能动手修（红线：可行动的中文提示）。"""
    target = library / "只读"
    target.mkdir()
    target.chmod(0o500)
    try:
        with pytest.raises(AlbumError) as caught:
            create_album(library, target)
    finally:
        target.chmod(0o700)

    assert "没有权限" in caught.value.message
    assert _listing(target) == []


# ---- 记账与撤销 -------------------------------------------------------------


def test_the_creation_is_journaled(library: Path) -> None:
    """写一条 ``action=create`` 的记录：建在哪、建了什么、什么时候、结果如何。"""
    result = create_album(library, library / "奈良")

    records = read_records(library)
    assert len(records) == 1
    record = records[0]
    assert record.action == ACTION_CREATE
    assert record.source == library / "奈良"
    assert record.target == result.path
    assert record.is_dir is True
    assert record.result == RESULT_OK


def test_creating_an_album_is_not_undoable(library: Path) -> None:
    """撤销一次新建就是删掉一个文件夹，而删除只许用户亲手触发（红线 1）。

    所以 ``⌘Z`` 那一摞栈压根不收它 —— 紧接着的那一次改名才是能撤的。
    """
    create_album(library, library / "奈良")

    assert next_undo_label(library) is None
    assert undo_last_write(library) is None
    assert (library / "奈良" / DEFAULT_ALBUM_NAME).is_dir(), "文件夹还在原地"
