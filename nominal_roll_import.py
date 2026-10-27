import csv
import io
from datetime import date, datetime

import openpyxl
from sqlalchemy.orm import Session

from app.models import OrgUnit, OrgUnitLevel, PersonnelCategory, Rank

CATEGORY_BY_VALUE = {category.value.lower(): category for category in PersonnelCategory}

REQUIRED_COLUMNS = {
    "service id": "personnel_number",
    "rank": "rank",
    "name": "name",
    "category": "category",
    "company": "company_name",
    "posted in": "date_of_posting_in",
    "status": "is_active",
}

OPTIONAL_COLUMNS = {
    "platoon": "platoon_name",
    "section": "section_name",
}


class ImportFileError(Exception):
    pass


def parse_rows(filename: str, content: bytes) -> list[dict]:
    lowered_name = filename.lower()
    if lowered_name.endswith((".xlsx", ".xlsm")):
        raw_rows = _parse_excel(content)
    elif lowered_name.endswith(".csv"):
        raw_rows = _parse_csv(content)
    else:
        raise ImportFileError("Unsupported file type. Upload a .csv or .xlsx file.")

    if not raw_rows:
        raise ImportFileError("The file is empty.")

    header = [str(cell or "").strip().lower() for cell in raw_rows[0]]
    all_columns = {**REQUIRED_COLUMNS, **OPTIONAL_COLUMNS}
    field_by_index = {
        index: all_columns[cell] for index, cell in enumerate(header) if cell in all_columns
    }

    missing = set(REQUIRED_COLUMNS.values()) - set(field_by_index.values())
    if missing:
        missing_labels = sorted(
            label.title() for label, field in REQUIRED_COLUMNS.items() if field in missing
        )
        raise ImportFileError(
            f"Missing column(s): {', '.join(missing_labels)}. "
            "Please re-download the template and use its exact column headers."
        )

    rows = []
    for raw_row in raw_rows[1:]:
        if all(cell in (None, "") for cell in raw_row):
            continue
        row = {field: raw_row[index] if index < len(raw_row) else None for index, field in field_by_index.items()}
        rows.append(row)
    return rows


def _parse_excel(content: bytes) -> list[list]:
    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    sheet = workbook.active
    return [list(row) for row in sheet.iter_rows(values_only=True)]


def _parse_csv(content: bytes) -> list[list]:
    text = content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def clean(value) -> str:
    return "" if value is None else str(value).strip()


def missing(label: str) -> ValueError:
    return ValueError(f"Missing value for '{label}'. Please follow the template.")


def invalid(label: str, value, expected: str) -> ValueError:
    return ValueError(
        f"Invalid value for '{label}': '{value}'. Expected {expected} — please follow the template."
    )


def require(row: dict, field: str, label: str) -> str:
    value = clean(row.get(field))
    if not value:
        raise missing(label)
    return value


def parse_category(raw) -> PersonnelCategory:
    value = clean(raw)
    if not value:
        raise missing("Category")
    category = CATEGORY_BY_VALUE.get(value.lower())
    if category is None:
        raise invalid("Category", value, "Offr, JCO, or OR")
    return category


def parse_status(raw) -> bool:
    value = clean(raw)
    if not value:
        raise missing("Status")
    lowered = value.lower()
    if lowered == "active":
        return True
    if lowered == "inactive":
        return False
    raise invalid("Status", value, "Active or Inactive")


def parse_date(raw) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = clean(raw)
    if not text:
        raise missing("Posted In")
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        raise invalid("Posted In", text, "date format YYYY-MM-DD")


def resolve_rank(db: Session, cache: dict[str, int], raw_name, category: PersonnelCategory) -> int:
    name = clean(raw_name)
    if not name:
        raise missing("Rank")
    key = name.lower()
    if key not in cache:
        rank = Rank(name=name, category=category, seniority_order=0)
        db.add(rank)
        db.flush()
        cache[key] = rank.id
    return cache[key]


def get_or_create_org_unit(
    db: Session,
    cache: dict[tuple[int | None, OrgUnitLevel, str], int],
    parent_id: int | None,
    level: OrgUnitLevel,
    name: str,
) -> int:
    key = (parent_id, level, name.lower())
    if key not in cache:
        unit = OrgUnit(name=name, level=level, parent_id=parent_id)
        db.add(unit)
        db.flush()
        cache[key] = unit.id
    return cache[key]


def resolve_org_unit_path(
    db: Session,
    cache: dict[tuple[int | None, OrgUnitLevel, str], int],
    raw_company,
    raw_platoon,
    raw_section,
) -> int:
    company = clean(raw_company)
    if not company:
        raise missing("Company")
    unit_id = get_or_create_org_unit(db, cache, None, OrgUnitLevel.COMPANY, company)

    platoon = clean(raw_platoon)
    if not platoon:
        return unit_id
    unit_id = get_or_create_org_unit(db, cache, unit_id, OrgUnitLevel.PLATOON, platoon)

    section = clean(raw_section)
    if not section:
        return unit_id
    return get_or_create_org_unit(db, cache, unit_id, OrgUnitLevel.SECTION, section)
