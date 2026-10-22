"""Atomic self-authored syntax tests; not government notices, demos, or gold data."""

import pytest
from bidops.parser import canonical_text, parse_date, parse_money


@pytest.mark.parametrize(
    ("raw", "amount"),
    [
        ("112万元", "1120000.00"),
        ("人民币 1,234.56 元", "1234.56"),
        ("0.0001万元", "1.00"),
        ("0元", "0.00"),
        ("12.50万元（人民币）", "125000.00"),
    ],
)
def test_money_exact_decimal(raw, amount):
    assert parse_money(raw).amount == amount


@pytest.mark.parametrize(
    "raw", ["100", "待定", "-1元", "1亿美元", "1,00元", "1.001元", "约100万元"]
)
def test_money_abstains(raw):
    with pytest.raises(ValueError):
        parse_money(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026年10月22日 08时30分", "2026-10-22T08:30:00+08:00"),
        ("2026-10-22 8:30:12", "2026-10-22T08:30:12+08:00"),
        ("二〇二六年十月二十二日 08:30", "2026-10-22T08:30:00+08:00"),
    ],
)
def test_dates_cst(raw, expected):
    assert parse_date(raw)[0] == expected


def test_date_only_does_not_invent_midnight():
    assert parse_date("2026年10月22日") == (None, "2026-10-22", "date")
    with pytest.raises(ValueError, match="time_missing"):
        parse_date("2026年10月22日", require_time=True)


@pytest.mark.parametrize("raw", ["2026-02-30 12:00", "2026-10-22 25:00", "unknown"])
def test_invalid_date(raw):
    with pytest.raises(ValueError):
        parse_date(raw)


def test_unknown_fields_and_evidence(store, ingest):
    snapshot = ingest("项目编号：UNIT-001\n预算金额：1.20万元\n公告日期：2026年10月22日")
    notice = store.get_notice(snapshot.snapshot_id)
    assert notice.project_code == "UNIT-001"
    assert notice.title is None and notice.buyer_name is None
    assert notice.bid_deadline_at is None and notice.status_as_observed == "unknown"
    assert notice.budget.amount == "12000.00"
    assert notice.published_at is None and notice.published_date == "2026-10-22"
    text = canonical_text((store.raw / snapshot.sha256).read_bytes(), "text/plain", "utf-8")
    for spans in notice.field_evidence.values():
        for span in spans:
            assert text[span.start : span.end] == span.text
            assert span.source_snapshot_id == snapshot.snapshot_id


def test_packages_are_not_summed(store, ingest):
    snapshot = ingest("第1包\n预算金额：1万元\n第2包\n预算金额：200元")
    notice = store.get_notice(snapshot.snapshot_id)
    assert notice.budget is None
    assert [p.budget.amount for p in notice.packages] == ["10000.00", "200.00"]


def test_total_separate_from_package(store, ingest):
    snapshot = ingest("预算总金额：2万元\n第1包\n预算金额：1万元\n第2包\n预算金额：1万元")
    assert store.get_notice(snapshot.snapshot_id).budget.amount == "20000.00"


def test_conflicting_values_abstain(store, ingest):
    snapshot = ingest("投标截止时间：2026-10-22 08:30\n投标截止时间：2026-10-23 08:30")
    notice = store.get_notice(snapshot.snapshot_id)
    assert notice.bid_deadline_at is None
    assert notice.abstain_reason["bid_deadline_at"] == "conflicting_values"


def test_sme_is_quotation_not_eligibility_conclusion(store, ingest):
    snapshot = ingest("本条为解析单元测试片段。\n资格条件：专门面向中小企业。")
    notice = store.get_notice(snapshot.snapshot_id)
    assert notice.eligibility_conditions[0].text == "资格条件：专门面向中小企业。"
    assert notice.status_as_observed == "unknown"


def test_html_table_and_script_removal(store, url):
    body = (
        '<html><script>document.write("ignore")</script><table><tr>'
        "<td>项目编号</td><td>UNIT-TABLE</td></tr></table></html>"
    ).encode()
    snap, _ = store.ingest(url, body, content_type="text/html", manual_attestation=True)
    assert store.get_notice(snap.snapshot_id).project_code == "UNIT-TABLE"
    assert "document.write" not in canonical_text(body, "text/html", "utf-8")


def test_broken_encoding_refused(store, url):
    with pytest.raises(UnicodeError):
        store.ingest(url, b"\xff\xff", manual_attestation=True)
    assert store.db.rows("SELECT count(*) AS n FROM snapshots")[0]["n"] == 0


def test_gb18030(store, url):
    snap, _ = store.ingest(
        url, "项目编号：UNIT-GB".encode("gb18030"), encoding="gb18030", manual_attestation=True
    )
    assert store.get_notice(snap.snapshot_id).project_code == "UNIT-GB"


def test_empty_label_never_consumes_next_field(store, ingest):
    snap = ingest("项目编号：\n项目名称：UNIT-EMPTY\n投标截止时间：\n采购单位：")
    notice = store.get_notice(snap.snapshot_id)
    assert notice.project_code is None and notice.buyer_name is None
    assert notice.bid_deadline_at is None and notice.title == "UNIT-EMPTY"


def test_html_inline_label_keeps_evidence(store, url):
    html = "<p><strong>项目编号：</strong><span>UNIT-INLINE</span></p>".encode()
    snap, _ = store.ingest(url, html, content_type="text/html", manual_attestation=True)
    notice = store.get_notice(snap.snapshot_id)
    assert notice.project_code == "UNIT-INLINE"
    span = notice.field_evidence["project_code"][0]
    assert canonical_text(html, "text/html", "utf-8")[span.start : span.end] == span.text


@pytest.mark.parametrize("kind", ["更正公告", "终止公告", "中标公告"])
def test_amendments_retained(store, ingest, kind):
    first = ingest("项目编号：UNIT-HISTORY\n公告类型：公开招标公告")
    second = ingest(f"项目编号：UNIT-HISTORY\n公告类型：{kind}")
    assert first.snapshot_id != second.snapshot_id
    assert store.get_notice(first.snapshot_id).notice_kind == "tender_announcement"
    assert store.get_notice(second.snapshot_id).status_as_observed == "review_required"
    assert store.db.rows("SELECT COUNT(*) AS n FROM notices")[0]["n"] == 2
