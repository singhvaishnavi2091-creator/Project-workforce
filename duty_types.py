from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import DutyAssignment, DutyType, Rank
from app.schemas import DutyTypeCreate, DutyTypeOut, DutyTypeUpdate

router = APIRouter(
    prefix="/api/duty-types",
    tags=["duty-types"],
    dependencies=[Depends(get_current_user)],
)


def _validate_rank_ids(db: Session, rank_ids: list[int]) -> None:
    if not rank_ids:
        return
    found = db.query(Rank.id).filter(Rank.id.in_(rank_ids)).count()
    if found != len(set(rank_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown rank in eligible ranks")


@router.get("", response_model=list[DutyTypeOut])
def list_duty_types(db: Session = Depends(get_db)):
    return db.query(DutyType).order_by(DutyType.name).all()


@router.post("", response_model=DutyTypeOut, status_code=status.HTTP_201_CREATED)
def create_duty_type(payload: DutyTypeCreate, db: Session = Depends(get_db)):
    exists = db.query(DutyType).filter(func.lower(DutyType.name) == payload.name.strip().lower()).first()
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A duty type with this name already exists",
        )
    _validate_rank_ids(db, payload.eligible_rank_ids)

    duty_type = DutyType(
        name=payload.name,
        location=payload.location,
        number_required=payload.number_required,
        eligible_categories=[c.value for c in payload.eligible_categories],
        eligible_rank_ids=payload.eligible_rank_ids,
        requires_exclusive_presence=payload.requires_exclusive_presence,
    )
    db.add(duty_type)
    db.commit()
    db.refresh(duty_type)
    return duty_type


@router.put("/{duty_type_id}", response_model=DutyTypeOut)
def update_duty_type(duty_type_id: int, payload: DutyTypeUpdate, db: Session = Depends(get_db)):
    duty_type = db.get(DutyType, duty_type_id)
    if duty_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    _validate_rank_ids(db, payload.eligible_rank_ids)

    duty_type.name = payload.name
    duty_type.location = payload.location
    duty_type.number_required = payload.number_required
    duty_type.eligible_categories = [c.value for c in payload.eligible_categories]
    duty_type.eligible_rank_ids = payload.eligible_rank_ids
    duty_type.requires_exclusive_presence = payload.requires_exclusive_presence
    db.commit()
    db.refresh(duty_type)
    return duty_type


@router.delete("/{duty_type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_duty_type(duty_type_id: int, db: Session = Depends(get_db)):
    duty_type = db.get(DutyType, duty_type_id)
    if duty_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    has_assignments = (
        db.query(DutyAssignment).filter(DutyAssignment.duty_type_id == duty_type_id).first()
    )
    if has_assignments:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Remove or reassign duty roster entries using this duty type first",
        )

    db.delete(duty_type)
    db.commit()
