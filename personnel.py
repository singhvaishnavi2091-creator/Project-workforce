from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import OrgUnit, Personnel, Rank, StandingAppointment
from app.nominal_roll_import import (
    ImportFileError,
    clean,
    missing,
    parse_category,
    parse_date,
    parse_rows,
    parse_status,
    require,
    resolve_org_unit_path,
    resolve_rank,
)
from app.schemas import (
    PersonnelCreate,
    PersonnelImportResult,
    PersonnelImportRowError,
    PersonnelOut,
    PersonnelUpdate,
)

router = APIRouter(
    prefix="/api/personnel",
    tags=["personnel"],
    dependencies=[Depends(get_current_user)],
)


def _descendant_org_unit_ids(db: Session, root_id: int) -> set[int]:
    children_map: dict[int | None, list[int]] = {}
    for unit_id, parent_id in db.query(OrgUnit.id, OrgUnit.parent_id).all():
        children_map.setdefault(parent_id, []).append(unit_id)

    result: set[int] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        result.add(current)
        stack.extend(children_map.get(current, []))
    return result


def _validate_org_unit(db: Session, org_unit_id: int) -> None:
    if db.get(OrgUnit, org_unit_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown org unit"
        )


def _validate_rank(db: Session, rank_id: int) -> None:
    if db.get(Rank, rank_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown rank")


def _validate_standing_appointment(db: Session, standing_appointment_id: int | None) -> None:
    if standing_appointment_id is not None and db.get(StandingAppointment, standing_appointment_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown standing appointment")


def _normalize_posting_status(payload):
    """Enforce that a posting-out date is recorded whenever someone is marked inactive,
    and that active personnel don't carry a stale posting-out date."""
    if payload.is_active:
        payload.date_of_posting_out = None
    elif payload.date_of_posting_out is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Date of posting-out is required when marking a person inactive",
        )


def _normalize_standing_appointment(payload):
    """A standing-appointment 'since' date only makes sense alongside an
    actual appointment — clear it if none is held."""
    if payload.standing_appointment_id is None:
        payload.standing_appointment_since = None


@router.get("", response_model=list[PersonnelOut])
def list_personnel(
    org_unit_id: int | None = None, rank_id: int | None = None, db: Session = Depends(get_db)
):
    query = db.query(Personnel)
    if org_unit_id is not None:
        allowed_ids = _descendant_org_unit_ids(db, org_unit_id)
        query = query.filter(Personnel.org_unit_id.in_(allowed_ids))
    if rank_id is not None:
        query = query.filter(Personnel.rank_id == rank_id)
    return query.order_by(Personnel.name).all()


@router.post("", response_model=PersonnelOut, status_code=status.HTTP_201_CREATED)
def create_personnel(payload: PersonnelCreate, db: Session = Depends(get_db)):
    exists = (
        db.query(Personnel)
        .filter(Personnel.personnel_number == payload.personnel_number)
        .first()
    )
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A personnel record with this number already exists",
        )
    _validate_org_unit(db, payload.org_unit_id)
    _validate_rank(db, payload.rank_id)
    _validate_standing_appointment(db, payload.standing_appointment_id)
    _normalize_posting_status(payload)
    _normalize_standing_appointment(payload)

    record = Personnel(**payload.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.put("/{personnel_id}", response_model=PersonnelOut)
def update_personnel(
    personnel_id: int, payload: PersonnelUpdate, db: Session = Depends(get_db)
):
    record = db.get(Personnel, personnel_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    _validate_org_unit(db, payload.org_unit_id)
    _validate_rank(db, payload.rank_id)
    _validate_standing_appointment(db, payload.standing_appointment_id)
    _normalize_posting_status(payload)
    _normalize_standing_appointment(payload)

    for field, value in payload.model_dump().items():
        setattr(record, field, value)

    db.commit()
    db.refresh(record)
    return record


@router.post("/import", response_model=PersonnelImportResult)
def import_personnel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = file.file.read()
    try:
        rows = parse_rows(file.filename or "", content)
    except ImportFileError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    existing_numbers = {number for (number,) in db.query(Personnel.personnel_number).all()}
    unit_cache = {
        (unit.parent_id, unit.level, unit.name.strip().lower()): unit.id
        for unit in db.query(OrgUnit).all()
    }
    rank_cache = {rank.name.strip().lower(): rank.id for rank in db.query(Rank).all()}

    errors: list[PersonnelImportRowError] = []
    imported = 0
    skipped_duplicates = 0

    for row_number, row in enumerate(rows, start=2):
        personnel_number = clean(row.get("personnel_number"))
        try:
            if not personnel_number:
                raise missing("Service ID")
            if personnel_number in existing_numbers:
                skipped_duplicates += 1
                continue

            category = parse_category(row.get("category"))

            record = Personnel(
                personnel_number=personnel_number,
                rank_id=resolve_rank(db, rank_cache, row.get("rank"), category),
                name=require(row, "name", "Name"),
                category=category,
                org_unit_id=resolve_org_unit_path(
                    db,
                    unit_cache,
                    row.get("company_name"),
                    row.get("platoon_name"),
                    row.get("section_name"),
                ),
                date_of_posting_in=parse_date(row.get("date_of_posting_in")),
                is_active=parse_status(row.get("is_active")),
            )
            db.add(record)
            existing_numbers.add(personnel_number)
            imported += 1
        except ValueError as exc:
            errors.append(
                PersonnelImportRowError(
                    row=row_number, personnel_number=personnel_number or None, error=str(exc)
                )
            )

    db.commit()

    return PersonnelImportResult(
        total_rows=len(rows),
        imported=imported,
        skipped_duplicates=skipped_duplicates,
        errors=errors,
    )
