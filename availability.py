from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import DutyAssignment, DutyType, OutOfUnitRecord, OutOfUnitStatus, Personnel
from app.routers.duty_assignments import _is_exclusive
from app.schemas import AvailabilityConflict, AvailabilityOut

router = APIRouter(
    prefix="/api/availability",
    tags=["availability"],
    dependencies=[Depends(get_current_user)],
)

# Out-of-unit status -> a stable machine token the frontend styles by.
_OUT_OF_UNIT_KIND = {
    OutOfUnitStatus.LEAVE: "leave",
    OutOfUnitStatus.ON_DEPUTATION: "deputation",
    OutOfUnitStatus.TRAINING: "training",
}


def _clip(a_from: date, a_to: date, window_from: date, window_to: date) -> tuple[date, date]:
    """Clip a commitment's dates to the queried window so the reported range
    only shows the days that actually fall inside what was asked about."""
    return max(a_from, window_from), min(a_to, window_to)


@router.get("/{personnel_id}", response_model=AvailabilityOut)
def check_availability(
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

    conflicts: list[AvailabilityConflict] = []

    # Exclusive duties (out-of-station, or a guard/sentry post) that overlap the
    # window make the person unavailable. Flexible on-campus duties (gardening,
    # fatigue) don't — the person is still present in the unit for those.
    duty_rows = (
        db.query(DutyAssignment, DutyType)
        .join(DutyType, DutyAssignment.duty_type_id == DutyType.id)
        .filter(
            DutyAssignment.personnel_id == personnel_id,
            DutyAssignment.from_date <= to_date,
            DutyAssignment.to_date >= from_date,
        )
        .all()
    )
    for assignment, duty_type in duty_rows:
        if not _is_exclusive(duty_type):
            continue
        clipped_from, clipped_to = _clip(assignment.from_date, assignment.to_date, from_date, to_date)
        conflicts.append(
            AvailabilityConflict(
                kind="duty",
                label=f"{duty_type.name} ({duty_type.location.value})",
                from_date=clipped_from,
                to_date=clipped_to,
                shift=assignment.shift,
            )
        )

    # Leave / Deputation / Training records overlapping the window.
    out_of_unit_rows = (
        db.query(OutOfUnitRecord)
        .filter(
            OutOfUnitRecord.personnel_id == personnel_id,
            OutOfUnitRecord.from_date <= to_date,
            OutOfUnitRecord.to_date >= from_date,
        )
        .all()
    )
    for record in out_of_unit_rows:
        clipped_from, clipped_to = _clip(record.from_date, record.to_date, from_date, to_date)
        conflicts.append(
            AvailabilityConflict(
                kind=_OUT_OF_UNIT_KIND[record.status],
                label=record.status.value,
                from_date=clipped_from,
                to_date=clipped_to,
                remark=record.remarks,
            )
        )

    conflicts.sort(key=lambda c: (c.from_date, c.to_date))

    return AvailabilityOut(
        personnel_id=person.id,
        personnel_number=person.personnel_number,
        name=person.name,
        rank_id=person.rank_id,
        from_date=from_date,
        to_date=to_date,
        is_available=len(conflicts) == 0,
        conflicts=conflicts,
    )
