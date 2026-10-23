"""``collation_key``：名字按拼音排先后。"""

from __future__ import annotations

from gallery.core.collation import collation_key


def test_chinese_names_sort_by_pinyin_not_by_codepoint() -> None:
    """码点序是「张李王赵」，拼音序是「李王张赵」—— 后者才是用户期待的。"""
    names = ["张.jpg", "李.jpg", "王.jpg", "赵.jpg"]

    assert sorted(names, key=collation_key) == ["李.jpg", "王.jpg", "张.jpg", "赵.jpg"]


def test_latin_and_chinese_share_one_reading_order() -> None:
    names = ["张.jpg", "Apple.jpg", "北京.jpg", "banana.jpg"]

    assert sorted(names, key=collation_key) == ["Apple.jpg", "banana.jpg", "北京.jpg", "张.jpg"]


def test_plain_latin_names_keep_the_casefold_key() -> None:
    """纯拉丁名字的键就是 ``casefold()`` —— 既有排序行为不受这条改动影响。"""
    assert collation_key("B.JPG") == "b.jpg"


def test_homophones_are_ordered_by_the_original_character() -> None:
    """同音不等于同键：否则排序结果只能随输入顺序漂。"""
    first = collation_key("张三.jpg")
    second = collation_key("章三.jpg")

    assert first.startswith("zhangsan.jpg")
    assert second.startswith("zhangsan.jpg")
    assert first != second


def test_polyphonic_characters_use_the_word_reading() -> None:
    """词组里的读音要按词组取：``重庆`` 是 chongqing 而不是 zhongqing。"""
    assert collation_key("重庆.jpg").startswith("chongqing")
    assert collation_key("长沙.jpg").startswith("changsha")
