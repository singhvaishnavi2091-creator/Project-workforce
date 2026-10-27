from datetime import date

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
    OutOfUnitRecord,
    Personnel,
    StandingAppointment,
)
from app.schemas import (
    DutyAssignmentBulkCreate,
    DutyAssignmentOut,
    DutyAssignmentUpdate,
    DutyClashCheckRequest,
    DutyClashWarning,
)

router = APIRouter(
    prefix="/api/duty-assignments",
    tags=["duty-assignments"],
    dependencies=[Depends(get_current_user)],
)


def _to_out(assignment: DutyAssignment, person: Personnel, duty_type: DutyType) -> DutyAssignmentOut:
    return DutyAssignmentOut(
        id=assignment.id,
        personnel_id=person.id,
        personnel_number=person.personnel_number,
        name=person.name,
        rank_id=person.rank_id,
        org_unit_id=person.org_unit_id,
        duty_type_id=duty_type.id,
        duty_type_name=duty_type.name,
        location=duty_type.location,
        from_date=assignment.from_date,
        to_date=assignment.to_date,
        shift=assignment.shift,
        remarks=assignment.remarks,
    )


def _validate_dates(from_date: date, to_date: date) -> None:
    if to_date < from_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="to_date must not be before from_date"
        )


@router.get("", response_model=list[DutyAssignmentOut])
def list_duty_assignments(
    from_date: date | None = None,
    to_date: date | None = None,
    duty_type_id: int | None = None,
    personnel_id: int | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(DutyAssignment)
    if from_date is not None and to_date is not None:
        query = query.filter(DutyAssignment.from_date <= to_date, DutyAssignment.to_date >= from_date)
    if duty_type_id is not None:
        query = query.filter(DutyAssignment.duty_type_id == duty_type_id)
    if personnel_id is not None:
        query = query.filter(DutyAssignment.personnel_id == personnel_id)
    assignments = query.order_by(DutyAssignment.from_date).all()

    personnel_ids = {a.personnel_id for a in assignments}
    duty_type_ids = {a.duty_type_id for a in assignments}
    people_by_id = {
        p.id: p for p in db.query(Personnel).filter(Personnel.id.in_(personnel_ids)).all()
    }
    duty_types_by_id = {
        d.id: d for d in db.query(DutyType).filter(DutyType.id.in_(duty_type_ids)).all()
    }

    return [
        _to_out(a, people_by_id[a.personnel_id], duty_types_by_id[a.duty_type_id])
        for a in assignments
        if a.personnel_id in people_by_id and a.duty_type_id in duty_types_by_id
    ]


def _is_exclusive(duty_type: DutyType) -> bool:
    """Out-of-station duties are always exclusive (can't be away in two
    places at once); on-campus duties are exclusive only if flagged as such
    (e.g. a guard/sentry post, as opposed to a flexible labor party)."""
    return duty_type.location == DutyLocation.OUT_OF_STATION or duty_type.requires_exclusive_presence


def _shifts_overlap(shift_a, shift_b) -> bool:
    """A shift of None means 'whole day' (out-of-station duties, or any
    exclusive duty with no shift set) and so overlaps every shift. Two named
    shifts only overlap if they're the same one (Morning vs Morning)."""
    if shift_a is None or shift_b is None:
        return True
    return shift_a == shift_b


@router.post("/check-clashes", response_model=list[DutyClashWarning])
def check_duty_clashes(payload: DutyClashCheckRequest, db: Session = Depends(get_db)):
    new_duty_type = db.get(DutyType, payload.duty_type_id)
    if new_duty_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Duty type not found")

    if not payload.personnel_ids:
        return []

    warnings: list[DutyClashWarning] = []

    # DTY-05: same-day clash — only exclusive duties can clash with each other.
    if _is_exclusive(new_duty_type):
        query = (
            db.query(DutyAssignment, DutyType, Personnel)
            .join(DutyType, DutyAssignment.duty_type_id == DutyType.id)
            .join(Personnel, DutyAssignment.personnel_id == Personnel.id)
            .filter(
                DutyAssignment.personnel_id.in_(payload.personnel_ids),
                DutyAssignment.from_date <= payload.to_date,
                DutyAssignment.to_date >= payload.from_date,
            )
        )
        if payload.exclude_assignment_id is not None:
            query = query.filter(DutyAssignment.id != payload.exclude_assignment_id)

        for assignment, duty_type, person in query.all():
            if _is_exclusive(duty_type) and _shifts_overlap(payload.shift, assignment.shift):
                warnings.append(
                    DutyClashWarning(
                        personnel_id=person.id,
                        name=person.name,
                        kind="clash",
                        conflicting_duty_type_name=duty_type.name,
                        conflicting_from_date=assignment.from_date,
                        conflicting_to_date=assignment.to_date,
                        conflicting_shift=assignment.shift,
                    )
                )

    # DTY-06: decision-support only — a standing appointment never blocks
    # assignment, it just flags that this person is exempt from *this*
    # duty type per the administrator-configured exemption list, so the
    # assigner can decide whether to pull them in anyway.
    exempt_people = (
        db.query(Personnel, StandingAppointment)
        .join(StandingAppointment, Personnel.standing_appointment_id == StandingAppointment.id)
        .filter(Personnel.id.in_(payload.personnel_ids))
        .all()
    )
    for person, appointment in exempt_people:
        if payload.duty_type_id in appointment.exempt_duty_type_ids:
            warnings.append(
                DutyClashWarning(
                    personnel_id=person.id,
                    name=person.name,
                    kind="exemption",
                    standing_appointment_name=appointment.name,
                )
            )

    # DTY-04: flag personnel already unavailable on any date within the new
    # assignment's range (on leave, on deputation, or away on training) —
    # decision-support only, same as the checks above. OutOfUnitRecord is
    # the authoritative source (the Leave/Deputation/Training module);
    # Attendance status is a fallback for a day marked by hand without one.
    out_of_unit_flagged_ids: set[int] = set()
    out_of_unit_rows = (
        db.query(OutOfUnitRecord, Personnel)
        .join(Personnel, OutOfUnitRecord.personnel_id == Personnel.id)
        .filter(
            OutOfUnitRecord.personnel_id.in_(payload.personnel_ids),
            OutOfUnitRecord.from_date <= payload.to_date,
            OutOfUnitRecord.to_date >= payload.from_date,
        )
        .order_by(OutOfUnitRecord.from_date)
        .all()
    )
    for record, person in out_of_unit_rows:
        out_of_unit_flagged_ids.add(person.id)
        warnings.append(
            DutyClashWarning(
                personnel_id=person.id,
                name=person.name,
                kind="unavailable",
                unavailable_status=AttendanceStatus(record.status.value),
                conflicting_from_date=record.from_date,
                conflicting_to_date=record.to_date,
            )
        )

    unavailable_statuses = (AttendanceStatus.LEAVE, AttendanceStatus.ON_DEPUTATION, AttendanceStatus.TRAINING)
    unavailable_rows = (
        db.query(Attendance, Personnel)
        .join(Personnel, Attendance.personnel_id == Personnel.id)
        .filter(
            Attendance.personnel_id.in_(payload.personnel_ids),
            Attendance.date >= payload.from_date,
            Attendance.date <= payload.to_date,
            Attendance.status.in_(unavailable_statuses),
        )
        .order_by(Attendance.date)
        .all()
    )
    for record, person in unavailable_rows:
        if person.id in out_of_unit_flagged_ids:
            continue
        warnings.append(
            DutyClashWarning(
                personnel_id=person.id,
                name=person.name,
                kind="unavailable",
                unavailable_status=record.status,
                unavailable_date=record.date,
            )
        )

    return warnings


@router.post("/bulk", response_model=list[DutyAssignmentOut], status_code=status.HTTP_201_CREATED)
def bulk_create_duty_assignments(payload: DutyAssignmentBulkCreate, db: Session = Depends(get_db)):
    duty_type = db.get(DutyType, payload.duty_type_id)
    if duty_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Duty type not found")
    _validate_dates(payload.from_date, payload.to_date)

    people_by_id = {
        p.id: p
        for p in db.query(Personnel)
        .filter(Personnel.id.in_(payload.personnel_ids), Personnel.is_active.is_(True))
        .all()
    }

    created: list[DutyAssignment] = []
    for personnel_id in payload.personnel_ids:
        person = people_by_id.get(personnel_id)
        if person is None:
            continue
        assignment = DutyAssignment(
            personnel_id=personnel_id,
            duty_type_id=payload.duty_type_id,
            from_date=payload.from_date,
            to_date=payload.to_date,
            shift=payload.shift,
            remarks=payload.remarks,
        )
        db.add(assignment)
        created.append(assignment)

    db.commit()

    results = []
    for assignment in created:
        db.refresh(assignment)
        results.append(_to_out(assignment, people_by_id[assignment.personnel_id], duty_type))
    return results


@router.put("/{assignment_id}", response_model=DutyAssignmentOut)
def update_duty_assignment(
    assignment_id: int, payload: DutyAssignmentUpdate, db: Session = Depends(get_db)
):
    assignment = db.get(DutyAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    person = db.get(Personnel, payload.personnel_id)
    if person is None or not person.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Personnel not found")
    duty_type = db.get(DutyType, payload.duty_type_id)
    if duty_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Duty type not found")
    _validate_dates(payload.from_date, payload.to_date)

    assignment.personnel_id = payload.personnel_id
    assignment.duty_type_id = payload.duty_type_id
    assignment.from_date = payload.from_date
    assignment.to_date = payload.to_date
    assignment.shift = payload.shift
    assignment.remarks = payload.remarks
    db.commit()
    db.refresh(assignment)
    return _to_out(assignment, person, duty_type)


@router.delete("/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_duty_assignment(assignment_id: int, db: Session = Depends(get_db)):
    assignment = db.get(DutyAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(assignment)
    db.commit()
