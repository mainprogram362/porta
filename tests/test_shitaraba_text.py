from media.shitaraba_text import (
    find_text_hits,
    load_shitaraba_saved_texts,
    parse_shitaraba_saved_text,
    reply_descendant_tree,
    replies_to,
)


THREAD_ONE = """[1] 名前A / 2026/08/24 / ID:a
最初のレス

[2] 名前B / 2026/08/24 / ID:b
>>1 への返信です。

[3] 名前C / 2026/08/24 / ID:c
>>1-2 と別の検索語
"""


def test_shitaraba_text_parser_builds_reply_and_reference_indexes(tmp_path):
    path = tmp_path / "Part 4__100_200.txt"
    path.write_text(THREAD_ONE, encoding="utf-8")

    document = parse_shitaraba_saved_text(path.read_text(encoding="utf-8"), path)

    assert document.title == "Part 4"
    assert [reply.number for reply in document.replies] == [1, 2, 3]
    assert document.replies[2].references == (1, 2)
    assert [reply.number for reply in replies_to(document, 1)] == [2, 3]


def test_cross_thread_search_stays_in_load_order_and_reports_invalid_files(tmp_path):
    first = tmp_path / "Part 1__100_201.txt"
    second = tmp_path / "Part 2__100_202.txt"
    invalid = tmp_path / "other.md"
    first.write_text(THREAD_ONE, encoding="utf-8")
    second.write_text(THREAD_ONE.replace("別の検索語", "検索語の続き"), encoding="utf-8")
    invalid.write_text("not text thread", encoding="utf-8")

    result = load_shitaraba_saved_texts([first, invalid, second])

    assert len(result.documents) == 2
    assert len(result.errors) == 1
    hits = find_text_hits(result.documents, "検索語")
    assert [(hit.document_index, hit.reply_number) for hit in hits] == [(0, 3), (1, 3)]


def test_cross_thread_search_supports_and_or_exclusion_and_quoted_phrases(tmp_path):
    path = tmp_path / "search__100_205.txt"
    text = "\n\n".join(
        (
            "[1] A / 2026 / ID:a\n猫と犬",
            "[2] B / 2026 / ID:b\n猫と鳥",
            "[3] C / 2026 / ID:c\n犬と鳥 禁止",
            "[4] D / 2026 / ID:d\n青い 猫を見た",
        )
    )
    document = parse_shitaraba_saved_text(text, path)
    documents = (document,)

    def numbers(query):
        return [hit.reply_number for hit in find_text_hits(documents, query)]

    assert numbers("猫 犬") == [1]
    assert numbers("猫 AND 鳥") == [2]
    assert numbers("猫 OR 犬") == [1, 2, 3, 4]
    assert numbers("猫 | 犬 -禁止") == [1, 2, 4]
    assert numbers("-禁止") == [1, 2, 4]
    assert numbers('"青い 猫"') == [4]


def test_reply_descendant_tree_nests_replies_and_stops_at_depth_limit(tmp_path):
    path = tmp_path / "tree__100_203.txt"
    text = "\n\n".join(
        ["[1] A / 2026 / ID:a\n親"]
        + [f"[{number}] B / 2026 / ID:{number}\n>>{number - 1} 子" for number in range(2, 13)]
    )
    path.write_text(text, encoding="utf-8")
    document = parse_shitaraba_saved_text(text, path)

    tree = reply_descendant_tree(document, 1, max_depth=10)
    node = tree[0]
    assert node.reply.number == 2
    for expected in range(3, 12):
        assert node.children[0].reply.number == expected
        node = node.children[0]
    assert node.reply.number == 11
    assert node.depth_limit_reached
    assert not node.children
