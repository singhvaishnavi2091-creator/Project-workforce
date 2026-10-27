from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import DutyType, Personnel, StandingAppointment
from app.schemas import StandingAppointmentCreate, StandingAppointmentOut, StandingAppointmentUpdate

router = APIRouter(
    prefix="/api/standing-appointments",
    tags=["standing-appointments"],
    dependencies=[Depends(get_current_user)],
)


def _validate_duty_type_ids(db: Session, duty_type_ids: list[int]) -> None:
    if not duty_type_ids:
        return
    found = db.query(DutyType.id).filter(DutyType.id.in_(duty_type_ids)).count()
    if found != len(set(duty_type_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown duty type in exemption list")


def _holder_counts(db: Session) -> dict[int, int]:
    return dict(
        db.query(Personnel.standing_appointment_id, func.count(Personnel.id))
        .filter(Personnel.standing_appointment_id.isnot(None), Personnel.is_active.is_(True))
        .group_by(Personnel.standing_appointment_id)
        .all()
    )


def _to_out(appointment: StandingAppointment, counts: dict[int, int]) -> StandingAppointmentOut:
    return StandingAppointmentOut(
        id=appointment.id,
        name=appointment.name,
        description=appointment.description,
        exempt_duty_type_ids=appointment.exempt_duty_type_ids,
        holder_count=counts.get(appointment.id, 0),
    )


@router.get("", response_model=list[StandingAppointmentOut])
def list_standing_appointments(db: Session = Depends(get_db)):
    appointments = db.query(StandingAppointment).order_by(StandingAppointment.name).all()
    counts = _holder_counts(db)
    return [_to_out(a, counts) for a in appointments]


@router.post("", response_model=StandingAppointmentOut, status_code=status.HTTP_201_CREATED)
def create_standing_appointment(payload: StandingAppointmentCreate, db: Session = Depends(get_db)):
    exists = (
        db.query(StandingAppointment)
        .filter(func.lower(StandingAppointment.name) == payload.name.strip().lower())
        .first()
    )
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A standing appointment with this name already exists",
        )
    _validate_duty_type_ids(db, payload.exempt_duty_type_ids)

    appointment = StandingAppointment(**payload.model_dump())
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return _to_out(appointment, _holder_counts(db))


@router.put("/{appointment_id}", response_model=StandingAppointmentOut)
def update_standing_appointment(
    appointment_id: int, payload: StandingAppointmentUpdate, db: Session = Depends(get_db)
):
    appointment = db.get(StandingAppointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    _validate_duty_type_ids(db, payload.exempt_duty_type_ids)

    appointment.name = payload.name
    appointment.description = payload.description
    appointment.exempt_duty_type_ids = payload.exempt_duty_type_ids
    db.commit()
    db.refresh(appointment)
    return _to_out(appointment, _holder_counts(db))


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_standing_appointment(appointment_id: int, db: Session = Depends(get_db)):
    appointment = db.get(StandingAppointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    has_holders = (
        db.query(Personnel).filter(Personnel.standing_appointment_id == appointment_id).first()
    )
    if has_holders:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reassign personnel holding this standing appointment first",
        )

    db.delete(appointment)
    db.commit()
