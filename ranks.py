from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Personnel, Rank
from app.schemas import RankCreate, RankOut, RankUpdate

router = APIRouter(
    prefix="/api/ranks",
    tags=["ranks"],
    dependencies=[Depends(get_current_user)],
)


def _personnel_counts(db: Session) -> dict[int, int]:
    return dict(
        db.query(Personnel.rank_id, func.count(Personnel.id))
        .filter(Personnel.is_active.is_(True))
        .group_by(Personnel.rank_id)
        .all()
    )


def _to_out(rank: Rank, counts: dict[int, int]) -> RankOut:
    return RankOut(
        id=rank.id,
        name=rank.name,
        category=rank.category,
        seniority_order=rank.seniority_order,
        personnel_count=counts.get(rank.id, 0),
    )


@router.get("", response_model=list[RankOut])
def list_ranks(db: Session = Depends(get_db)):
    ranks = db.query(Rank).order_by(Rank.seniority_order, Rank.name).all()
    counts = _personnel_counts(db)
    return [_to_out(rank, counts) for rank in ranks]


@router.post("", response_model=RankOut, status_code=status.HTTP_201_CREATED)
def create_rank(payload: RankCreate, db: Session = Depends(get_db)):
    exists = db.query(Rank).filter(func.lower(Rank.name) == payload.name.strip().lower()).first()
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A rank with this name already exists",
        )

    rank = Rank(**payload.model_dump())
    db.add(rank)
    db.commit()
    db.refresh(rank)
    return _to_out(rank, _personnel_counts(db))


@router.put("/{rank_id}", response_model=RankOut)
def update_rank(rank_id: int, payload: RankUpdate, db: Session = Depends(get_db)):
    rank = db.get(Rank, rank_id)
    if rank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    rank.name = payload.name
    rank.category = payload.category
    rank.seniority_order = payload.seniority_order
    db.commit()
    db.refresh(rank)
    return _to_out(rank, _personnel_counts(db))


@router.delete("/{rank_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rank(rank_id: int, db: Session = Depends(get_db)):
    rank = db.get(Rank, rank_id)
    if rank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    has_personnel = db.query(Personnel).filter(Personnel.rank_id == rank_id).first()
    if has_personnel:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reassign personnel holding this rank first",
        )

    db.delete(rank)
    db.commit()
