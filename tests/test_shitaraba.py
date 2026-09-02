from media.shitaraba import (
    inspect_thread_urls,
    inspect_thread_url,
    parse_thread_url,
    render_replies,
    save_replies,
)


RAW_THREAD = (
    "1<>名無しさん<>sage<>2026/08/24(日) 12:00:00 ID:abc<>"
    "最初の本文<br>二行目です<>題名<>abc\n"
    "2<>名前<> <>2026/08/24(日) 12:01:00 ID:def<>次の本文<> <>def\n"
).encode("euc_jp")


def test_parse_thread_url_normalizes_read_and_individual_reply_urls():
    reference = parse_thread_url(
        "http://jbbs.livedoor.jp/bbs/read.cgi/computer/10298/1158291064/123"
    )

    assert reference.source_url == "https://jbbs.shitaraba.net/bbs/read.cgi/computer/10298/1158291064/"
    assert reference.raw_url == "https://jbbs.shitaraba.net/bbs/rawmode.cgi/computer/10298/1158291064/"

    assert parse_thread_url(
        "https://jbbs.shitaraba.net/bbs/read.cgi/computer/10298/1158291064/l50?from=1#2"
    ) == reference


def test_inspection_requires_a_thread_url_and_parses_response_only_data():
    rejected = inspect_thread_url("https://jbbs.shitaraba.net/computer/10298/")
    assert not rejected.ok

    seen: list[str] = []
    inspection = inspect_thread_url(
        "https://jbbs.shitaraba.net/bbs/read.cgi/computer/10298/1158291064/",
        fetcher=lambda url: (seen.append(url) or RAW_THREAD),
    )

    assert inspection.ok
    assert seen == ["https://jbbs.shitaraba.net/bbs/rawmode.cgi/computer/10298/1158291064/"]
    assert inspection.thread is not None
    assert inspection.thread.title == "題名"
    assert len(inspection.thread.replies) == 2
    assert inspection.thread.replies[0].body == "最初の本文\n二行目です"
    assert render_replies(inspection.thread) == (
        "[1] 名無しさん / 2026/08/24(日) 12:00:00 ID:abc / abc\n最初の本文\n二行目です\n\n"
        "[2] 名前 / 2026/08/24(日) 12:01:00 ID:def / def\n次の本文"
    )


def test_batch_ignores_view_suffixes_and_saves_one_title_based_file_per_thread(tmp_path):
    batch = inspect_thread_urls(
        "\n".join(
            (
                "https://jbbs.shitaraba.net/bbs/read.cgi/computer/10298/1158291064/l50",
                "https://jbbs.shitaraba.net/bbs/read.cgi/computer/10298/1158291064/123",
            )
        ),
        fetcher=lambda _url: RAW_THREAD,
    )

    assert batch.is_ready
    assert len(batch.threads) == 1
    report = save_replies(batch.threads, tmp_path)
    assert len(report.paths) == 1
    assert report.paths[0].name.startswith("題名__10298_1158291064")
    assert "最初の本文" in report.paths[0].read_text(encoding="utf-8")


def test_batch_rejects_more_than_one_hundred_url_lines_without_fetching():
    batch = inspect_thread_urls(
        "\n".join(
            f"https://jbbs.shitaraba.net/bbs/read.cgi/computer/10298/11582910{index}/"
            for index in range(101)
        ),
        fetcher=lambda _url: (_ for _ in ()).throw(AssertionError("fetch must not run")),
    )

    assert not batch.is_ready
    assert "最大 100 件" in batch.inspections[0].message
