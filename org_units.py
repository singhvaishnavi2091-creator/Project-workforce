from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import OrgUnit, OrgUnitLevel, Personnel
from app.schemas import OrgUnitCreate, OrgUnitOut, OrgUnitUpdate

router = APIRouter(
    prefix="/api/org-units",
    tags=["org-units"],
    dependencies=[Depends(get_current_user)],
)

EXPECTED_PARENT_LEVEL = {
    OrgUnitLevel.COMPANY: None,
    OrgUnitLevel.PLATOON: OrgUnitLevel.COMPANY,
    OrgUnitLevel.SECTION: OrgUnitLevel.PLATOON,
}


def _cumulative_personnel_counts(db: Session) -> dict[int, int]:
    """Personnel count per org unit, including everyone posted in its descendants."""
    units = db.query(OrgUnit.id, OrgUnit.parent_id).all()
    children: dict[int | None, list[int]] = {}
    for unit_id, parent_id in units:
        children.setdefault(parent_id, []).append(unit_id)

    direct_counts = dict(
        db.query(Personnel.org_unit_id, func.count(Personnel.id))
        .filter(Personnel.is_active.is_(True))
        .group_by(Personnel.org_unit_id)
        .all()
    )

    memo: dict[int, int] = {}

    def total(unit_id: int) -> int:
        if unit_id not in memo:
            memo[unit_id] = direct_counts.get(unit_id, 0) + sum(
                total(child_id) for child_id in children.get(unit_id, [])
            )
        return memo[unit_id]

    return {unit_id: total(unit_id) for unit_id, _ in units}


def _to_out(unit: OrgUnit, counts: dict[int, int]) -> OrgUnitOut:
    return OrgUnitOut(
        id=unit.id,
        name=unit.name,
        level=unit.level,
        parent_id=unit.parent_id,
        personnel_count=counts.get(unit.id, 0),
    )


@router.get("", response_model=list[OrgUnitOut])
def list_org_units(db: Session = Depends(get_db)):
    units = db.query(OrgUnit).order_by(OrgUnit.level, OrgUnit.name).all()
    counts = _cumulative_personnel_counts(db)
    return [_to_out(unit, counts) for unit in units]


@router.post("", response_model=OrgUnitOut, status_code=status.HTTP_201_CREATED)
def create_org_unit(payload: OrgUnitCreate, db: Session = Depends(get_db)):
    expected_parent_level = EXPECTED_PARENT_LEVEL[payload.level]

    if expected_parent_level is None:
        if payload.parent_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A Company cannot have a parent unit",
            )
    else:
        parent = db.get(OrgUnit, payload.parent_id) if payload.parent_id else None
        if parent is None or parent.level != expected_parent_level:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A {payload.level.value.title()} must have a {expected_parent_level.value} as its parent",
            )

    unit = OrgUnit(**payload.model_dump())
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return _to_out(unit, _cumulative_personnel_counts(db))


@router.put("/{org_unit_id}", response_model=OrgUnitOut)
def update_org_unit(org_unit_id: int, payload: OrgUnitUpdate, db: Session = Depends(get_db)):
    unit = db.get(OrgUnit, org_unit_id)
    if unit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    unit.name = payload.name
    db.commit()
    db.refresh(unit)
    return _to_out(unit, _cumulative_personnel_counts(db))


@router.delete("/{org_unit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_org_unit(org_unit_id: int, db: Session = Depends(get_db)):
    unit = db.get(OrgUnit, org_unit_id)
    if unit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    has_children = db.query(OrgUnit).filter(OrgUnit.parent_id == org_unit_id).first()
    if has_children:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Remove or reassign the child units under this one first",
        )

    has_personnel = db.query(Personnel).filter(Personnel.org_unit_id == org_unit_id).first()
    if has_personnel:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reassign personnel posted to this unit first",
        )

    db.delete(unit)
    db.commit()
