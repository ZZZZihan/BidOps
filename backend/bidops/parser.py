"""Conservative rules over a versioned canonical text; no seed-field enrichment."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from bidops.models import Evidence, Money, Notice, Package, Snapshot

CST = ZoneInfo("Asia/Shanghai")
DATE = (
    r"(?:\d{4}|[二〇零一三四五六七八九]{4})[年/-]"
    r"[\d一二三四五六七八九十]{1,3}[月/-][\d一二三四五六七八九十]{1,3}日?"
)
TIME = r"(?:\s*\d{1,2}(?::|：|时)\d{1,2}(?:(?::|：)\d{1,2}|分(?:\d{1,2}秒)?)?)?"
DATE_TIME = DATE + TIME


def canonical_text(data: bytes, content_type: str, encoding: str) -> str:
    value = data.decode(encoding, errors="strict").lstrip("\ufeff")
    if "\ufffd" in value or "\x00" in value:
        raise ValueError("invalid_text_encoding")
    if content_type == "text/html":
        soup = BeautifulSoup(value, "html.parser")
        for el in soup(["script", "style", "noscript", "template"]):
            el.decompose()
        for row in soup.find_all("tr"):
            row.replace_with(
                "\n"
                + " | ".join(cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"]))
                + "\n"
            )
        for block in reversed(soup.find_all(["p", "li", "h1", "h2", "h3"])):
            block.replace_with("\n" + block.get_text(" ", strip=True) + "\n")
        value = soup.get_text("\n")
    return "\n".join(
        re.sub(r"[\t \u3000\xa0]+", " ", line).strip()
        for line in value.splitlines()
        if line.strip()
    )


def _chinese_number(value: str) -> int:
    if value.isascii() and value.isdigit():
        return int(value)
    digits = {c: n for n, c in enumerate("零一二三四五六七八九")}
    digits["〇"] = 0
    if "十" in value:
        left, right = value.split("十")
        return (digits[left] if left else 1) * 10 + (digits[right] if right else 0)
    return int("".join(str(digits[c]) for c in value))


def parse_date(raw: str, require_time: bool = False) -> tuple[str | None, str, str]:
    date_match = re.search(DATE, raw)
    if not date_match:
        raise ValueError("unsupported_date")
    parts = re.split(r"[年/月日-]", date_match.group().rstrip("日"))
    year, month, day = [_chinese_number(part) for part in parts]
    trailing = raw[date_match.end() :].strip()
    time_match = re.match(r"(\d{1,2})(?::|：|时)(\d{1,2})(?:(?::|：|分)(\d{1,2})秒?)?", trailing)
    dt = datetime(year, month, day, tzinfo=CST)
    if time_match:
        hour, minute, second = time_match.groups()
        dt = dt.replace(hour=int(hour), minute=int(minute), second=int(second or 0))
        return dt.isoformat(), dt.date().isoformat(), "second" if second else "minute"
    if require_time:
        raise ValueError("time_missing")
    return None, dt.date().isoformat(), "date"


def parse_money(raw: str, basis: str = "total") -> Money:
    match = re.fullmatch(
        r"\s*(?:人民币|￥|¥)?\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(万元|元)\s*(?:（人民币）|\(人民币\))?\s*",
        raw,
    )
    if not match:
        raise ValueError("amount_or_unit_unknown")
    amount, unit = match.groups()
    try:
        number = Decimal(amount.replace(",", "")) * (10000 if unit == "万元" else 1)
        rounded = number.quantize(Decimal("0.01"))
        if number != rounded:
            raise ValueError("sub_cent_precision")
    except InvalidOperation as exc:
        raise ValueError("invalid_amount") from exc
    return Money(amount=format(rounded, ".2f"), raw=raw, unit=unit, basis=basis)


def parse_notice(text: str, snapshot: Snapshot) -> Notice:
    notice = Notice(
        notice_id=snapshot.snapshot_id, source=snapshot, observed_at=snapshot.fetched_at.isoformat()
    )

    def evidence(start, end):
        return Evidence(
            source_snapshot_id=snapshot.snapshot_id, start=start, end=end, text=text[start:end]
        )

    def pick(field: str, pattern: str, area: str | None = None):
        matches = list(re.finditer(pattern, text if area is None else area, re.M))
        if not matches:
            notice.abstain_reason[field] = "not_found"
            return None
        values = {m.group("v").strip() for m in matches}
        if len(values) != 1:
            notice.abstain_reason[field] = "conflicting_values"
            notice.parse_warnings.append(f"{field}:conflicting_values")
            return None
        notice.field_evidence[field] = [evidence(m.start("v"), m.end("v")) for m in matches]
        return matches[0].group("v").strip()

    def label(names: str, value: str = r"[^\n|]+"):
        # An empty field must never consume the following field's label as its value.
        return rf"(?:^|[ |])(?:{names})[ \t]*[：:|][ \t]*(?P<v>{value})"

    for field, names in [
        ("project_code", "项目编号|项目编码"),
        ("title", "项目名称|公告标题"),
        ("buyer_name", "采购人名称|采购单位|采购人|采购单位名称"),
    ]:
        result = pick(field, label(names))
        if result:
            setattr(notice, field, result)

    # Title suffixes and explicit type labels only; avoid guessing from body mentions.
    kind_raw = pick("notice_kind", label("公告类型|公告类别"))
    kind_map = [
        (r"更正|变更", "correction"),
        (r"终止|废标", "termination"),
        (r"中标|成交", "award"),
        (r"招标|竞争性磋商|竞争性谈判|询价", "tender_announcement"),
    ]
    for pattern, kind in kind_map:
        if kind_raw and re.search(pattern, kind_raw):
            notice.notice_kind = kind
            break
    if kind_raw and not notice.notice_kind:
        notice.abstain_reason["notice_kind"] = "unsupported_kind"
    if not kind_raw and notice.abstain_reason.get("notice_kind") != "conflicting_values":
        titles = list(
            re.finditer(
                r"^(?P<v>[^\n|]*(?:更正|变更|终止|废标|中标|成交|招标|磋商|谈判|询价)公告)\s*$",
                text,
                re.M,
            )
        )
        if len(titles) == 1:
            title = titles[0]
            for pattern, kind in kind_map:
                if re.search(pattern, title.group("v")):
                    notice.notice_kind = kind
                    notice.field_evidence["notice_kind"] = [
                        evidence(title.start("v"), title.end("v"))
                    ]
                    notice.abstain_reason.pop("notice_kind", None)
                    break

    for field, names, requires_time in [
        ("published_at", "公告日期|发布日期|发布时间", False),
        ("bid_deadline_at", "提交投标文件截止时间|投标截止时间|响应文件提交截止时间", True),
        (
            "document_deadline_at",
            "获取采购文件截止时间|获取招标文件截止时间|文件获取截止时间",
            True,
        ),
    ]:
        raw = pick(field, label(names, DATE_TIME))
        if raw:
            try:
                instant, day, precision = parse_date(raw, requires_time)
                setattr(notice, field, instant)
                if field == "published_at":
                    notice.published_date = day
                    notice.publication_precision = precision
                    if instant is None:
                        notice.abstain_reason[field] = "date_only_no_time_in_source"
            except (ValueError, KeyError) as exc:
                notice.abstain_reason[field] = str(exc)

    headers = list(
        re.finditer(
            r"^(?P<v>(?:第[0-9一二三四五六七八九十]+包|(?:采购包|包)[号]?\s*[0-9一二三四五六七八九十]+))[^\n]*",
            text,
            re.M,
        )
    )
    total_area = text[: headers[0].start()] if headers else text
    raw = pick("budget", label("预算金额|采购预算|项目预算|预算总金额"), total_area)
    if raw:
        try:
            notice.budget = parse_money(raw)
        except ValueError as exc:
            notice.abstain_reason["budget"] = str(exc)
    for idx, header in enumerate(headers):
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
        section = text[header.start() : end]
        budgets = list(re.finditer(label("预算金额|采购预算|预算"), section, re.M))
        package = Package(label=header.group("v"), evidence=evidence(header.start(), end))
        if len(budgets) == 1:
            try:
                package.budget = parse_money(budgets[0].group("v"), "package")
            except ValueError:
                notice.parse_warnings.append(f"package:{idx}:amount_or_unit_unknown")
        else:
            notice.parse_warnings.append(f"package:{idx}:missing_or_ambiguous_budget")
        notice.packages.append(package)

    for match in re.finditer(r"^.*(?:中小企业|小微企业).*$", text, re.M):
        notice.eligibility_conditions.append(evidence(match.start(), match.end()))
    if not notice.eligibility_conditions:
        notice.abstain_reason["eligibility_conditions"] = "not_found"
    if notice.notice_kind in {"correction", "termination", "award"}:
        notice.status_as_observed = "review_required"
        notice.parse_warnings.append("historical_version_retained_no_automatic_supersession")
    notice.parse_warnings.append("notice_only:attachments_and_eligibility_require_human_review")
    return notice
