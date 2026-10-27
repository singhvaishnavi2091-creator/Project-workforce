from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    Attendance,
    AttendanceStatus,
    DutyAssignment,
    DutyLocation,
    DutyType,
    OrgUnit,
    OutOfUnitRecord,
    OutOfUnitStatus,
    Personnel,
)
from app.schemas import AttendanceBulkUpsert, AttendanceHistoryEntry, AttendanceRecordOut

MAX_HISTORY_DAYS = 366

router = APIRouter(
    prefix="/api/attendance",
    tags=["attendance"],
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


def _duty_coverage(
    db: Session, target_date: date, personnel_ids: set[int]
) -> dict[int, list[tuple[str, DutyLocation]]]:
    """personnel_id -> [(duty_name, location), ...] for duties covering that date."""
    if not personnel_ids:
        return {}
    rows = (
        db.query(DutyAssignment.personnel_id, DutyType.name, DutyType.location)
        .join(DutyType, DutyAssignment.duty_type_id == DutyType.id)
        .filter(
            DutyAssignment.personnel_id.in_(personnel_ids),
            DutyAssignment.from_date <= target_date,
            DutyAssignment.to_date >= target_date,
        )
        .all()
    )
    coverage: dict[int, list[tuple[str, DutyLocation]]] = {}
    for personnel_id, duty_name, location in rows:
        coverage.setdefault(personnel_id, []).append((duty_name, location))
    return coverage


# Status precedence when more than one out-of-unit record covers the same
# date (shouldn't normally happen, but resolved deterministically if it does).
_OUT_OF_UNIT_PRECEDENCE = {
    OutOfUnitStatus.ON_DEPUTATION: 3,
    OutOfUnitStatus.TRAINING: 2,
    OutOfUnitStatus.LEAVE: 1,
}

_OUT_OF_UNIT_TO_ATTENDANCE = {
    OutOfUnitStatus.ON_DEPUTATION: AttendanceStatus.ON_DEPUTATION,
    OutOfUnitStatus.TRAINING: AttendanceStatus.TRAINING,
    OutOfUnitStatus.LEAVE: AttendanceStatus.LEAVE,
}


def _out_of_unit_coverage(
    db: Session, target_date: date, personnel_ids: set[int]
) -> dict[int, OutOfUnitRecord]:
    """personnel_id -> the highest-precedence OutOfUnitRecord covering that date."""
    if not personnel_ids:
        return {}
    rows = (
        db.query(OutOfUnitRecord)
        .filter(
            OutOfUnitRecord.personnel_id.in_(personnel_ids),
            OutOfUnitRecord.from_date <= target_date,
            OutOfUnitRecord.to_date >= target_date,
        )
        .all()
    )
    coverage: dict[int, OutOfUnitRecord] = {}
    for record in rows:
        current = coverage.get(record.personnel_id)
        if current is None or _OUT_OF_UNIT_PRECEDENCE[record.status] > _OUT_OF_UNIT_PRECEDENCE[current.status]:
            coverage[record.personnel_id] = record
    return coverage


def _default_status(
    out_of_unit_record: OutOfUnitRecord | None, has_out_of_station_duty: bool
) -> AttendanceStatus:
    # Precedence (per BRD 14.1, minus Absent which stays a manual entry):
    # On-Deputation > Training > Leave > On-Duty (out-of-station) > Present.
    if out_of_unit_record is not None:
        return _OUT_OF_UNIT_TO_ATTENDANCE[out_of_unit_record.status]
    if has_out_of_station_duty:
        return AttendanceStatus.ON_DUTY
    return AttendanceStatus.PRESENT


def _to_out(
    person: Personnel,
    record: Attendance | None,
    duty_coverage: list[tuple[str, DutyLocation]] | None = None,
    out_of_unit_record: OutOfUnitRecord | None = None,
) -> AttendanceRecordOut:
    duty_coverage = duty_coverage or []
    duty_names = [name for name, _ in duty_coverage]
    has_out_of_station_duty = any(loc == DutyLocation.OUT_OF_STATION for _, loc in duty_coverage)
    default_status = _default_status(out_of_unit_record, has_out_of_station_duty)

    return AttendanceRecordOut(
        personnel_id=person.id,
        personnel_number=person.personnel_number,
        name=person.name,
        rank_id=person.rank_id,
        org_unit_id=person.org_unit_id,
        status=record.status if record is not None else default_status,
        remark=record.remark if record is not None else None,
        is_recorded=record is not None,
        duty_names=duty_names,
        out_of_unit_remark=out_of_unit_record.remarks if out_of_unit_record is not None else None,
        derived_status=default_status,
    )


@router.get("", response_model=list[AttendanceRecordOut])
def list_attendance(
    date: date,
    org_unit_id: int | None = None,
    rank_id: int | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Personnel).filter(Personnel.is_active.is_(True))
    if org_unit_id is not None:
        allowed_ids = _descendant_org_unit_ids(db, org_unit_id)
        query = query.filter(Personnel.org_unit_id.in_(allowed_ids))
    if rank_id is not None:
        query = query.filter(Personnel.rank_id == rank_id)
    personnel_list = query.order_by(Personnel.name).all()

    records_by_personnel = {
        record.personnel_id: record
        for record in db.query(Attendance).filter(Attendance.date == date).all()
    }
    personnel_ids = {person.id for person in personnel_list}
    duty_coverage = _duty_coverage(db, date, personnel_ids)
    out_of_unit_coverage = _out_of_unit_coverage(db, date, personnel_ids)

    return [
        _to_out(
            person,
            records_by_personnel.get(person.id),
            duty_coverage.get(person.id),
            out_of_unit_coverage.get(person.id),
        )
        for person in personnel_list
    ]


@router.get("/{personnel_id}", response_model=AttendanceRecordOut)
def get_attendance_for_personnel(personnel_id: int, date: date, db: Session = Depends(get_db)):
    person = db.get(Personnel, personnel_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Personnel not found")

    record = (
        db.query(Attendance)
        .filter(Attendance.personnel_id == personnel_id, Attendance.date == date)
        .first()
    )
    duty_coverage = _duty_coverage(db, date, {personnel_id}).get(personnel_id)
    out_of_unit_record = _out_of_unit_coverage(db, date, {personnel_id}).get(personnel_id)
    return _to_out(person, record, duty_coverage, out_of_unit_record)


@router.get("/{personnel_id}/history", response_model=list[AttendanceHistoryEntry])
def get_attendance_history(
    personnel_id: int,
    from_date: date,
    to_date: date,
    db: Session = Depends(get_db),
):
    person = db.get(Personnel, personnel_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Personnel not found")
    if to_date < from_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="to_date must not be before from_date"
        )
    if (to_date - from_date).days > MAX_HISTORY_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Range too large — max {MAX_HISTORY_DAYS} days",
        )

    records_by_date = {
        record.date: record
        for record in db.query(Attendance)
        .filter(
            Attendance.personnel_id == personnel_id,
            Attendance.date >= from_date,
            Attendance.date <= to_date,
        )
        .all()
    }

    duty_assignments = (
        db.query(DutyAssignment.from_date, DutyAssignment.to_date, DutyType.name, DutyType.location)
        .join(DutyType, DutyAssignment.duty_type_id == DutyType.id)
        .filter(
            DutyAssignment.personnel_id == personnel_id,
            DutyAssignment.from_date <= to_date,
            DutyAssignment.to_date >= from_date,
        )
        .all()
    )

    out_of_unit_records = (
        db.query(OutOfUnitRecord)
        .filter(
            OutOfUnitRecord.personnel_id == personnel_id,
            OutOfUnitRecord.from_date <= to_date,
            OutOfUnitRecord.to_date >= from_date,
        )
        .all()
    )

    entries: list[AttendanceHistoryEntry] = []
    current = from_date
    while current <= to_date:
        record = records_by_date.get(current)
        covering = [
            (duty_name, location)
            for (a_from, a_to, duty_name, location) in duty_assignments
            if a_from <= current <= a_to
        ]
        duty_names = [name for name, _ in covering]
        has_out_of_station_duty = any(loc == DutyLocation.OUT_OF_STATION for _, loc in covering)

        covering_out_of_unit = [r for r in out_of_unit_records if r.from_date <= current <= r.to_date]
        out_of_unit_record = None
        for r in covering_out_of_unit:
            if out_of_unit_record is None or _OUT_OF_UNIT_PRECEDENCE[r.status] > _OUT_OF_UNIT_PRECEDENCE[out_of_unit_record.status]:
                out_of_unit_record = r

        default_status = _default_status(out_of_unit_record, has_out_of_station_duty)
        entries.append(
            AttendanceHistoryEntry(
                date=current,
                status=record.status if record is not None else default_status,
                remark=record.remark if record is not None else None,
                is_recorded=record is not None,
                duty_names=duty_names,
                out_of_unit_remark=out_of_unit_record.remarks if out_of_unit_record is not None else None,
            )
        )
        current += timedelta(days=1)

    return entries


@router.put("/bulk", response_model=list[AttendanceRecordOut])
def bulk_upsert_attendance(payload: AttendanceBulkUpsert, db: Session = Depends(get_db)):
    personnel_ids = [item.personnel_id for item in payload.records]
    people_by_id = {
        person.id: person
        for person in db.query(Personnel).filter(Personnel.id.in_(personnel_ids)).all()
    }

    existing_by_personnel = {
        record.personnel_id: record
        for record in db.query(Attendance)
        .filter(Attendance.date == payload.date, Attendance.personnel_id.in_(personnel_ids))
        .all()
    }

    results: list[AttendanceRecordOut] = []
    for item in payload.records:
        person = people_by_id.get(item.personnel_id)
        if person is None or not person.is_active:
            continue

        record = existing_by_personnel.get(item.personnel_id)
        if record is None:
            record = Attendance(personnel_id=item.personnel_id, date=payload.date)
            db.add(record)
            existing_by_personnel[item.personnel_id] = record

        record.status = item.status
        record.remark = item.remark

    db.commit()

    duty_coverage = _duty_coverage(db, payload.date, set(people_by_id.keys()))
    out_of_unit_coverage = _out_of_unit_coverage(db, payload.date, set(people_by_id.keys()))
    for item in payload.records:
        person = people_by_id.get(item.personnel_id)
        record = existing_by_personnel.get(item.personnel_id)
        if person is None or record is None:
            continue
        db.refresh(record)
        results.append(
            _to_out(
                person,
                record,
                duty_coverage.get(item.personnel_id),
                out_of_unit_coverage.get(item.personnel_id),
            )
        )

    return results
