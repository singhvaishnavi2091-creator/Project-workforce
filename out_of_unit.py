from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import OutOfUnitRecord, Personnel
from app.schemas import OutOfUnitRecordBulkCreate, OutOfUnitRecordOut, OutOfUnitRecordUpdate

router = APIRouter(
    prefix="/api/out-of-unit",
    tags=["out-of-unit"],
    dependencies=[Depends(get_current_user)],
)


def _to_out(record: OutOfUnitRecord, person: Personnel) -> OutOfUnitRecordOut:
    return OutOfUnitRecordOut(
        id=record.id,
        personnel_id=person.id,
        personnel_number=person.personnel_number,
        name=person.name,
        rank_id=person.rank_id,
        org_unit_id=person.org_unit_id,
        status=record.status,
        from_date=record.from_date,
        to_date=record.to_date,
        remarks=record.remarks,
    )


def _validate_dates(from_date: date, to_date: date) -> None:
    if to_date < from_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="to_date must not be before from_date"
        )


@router.get("", response_model=list[OutOfUnitRecordOut])
def list_out_of_unit_records(
    from_date: date | None = None,
    to_date: date | None = None,
    personnel_id: int | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(OutOfUnitRecord)
    if from_date is not None and to_date is not None:
        query = query.filter(OutOfUnitRecord.from_date <= to_date, OutOfUnitRecord.to_date >= from_date)
    if personnel_id is not None:
        query = query.filter(OutOfUnitRecord.personnel_id == personnel_id)
    records = query.order_by(OutOfUnitRecord.from_date.desc()).all()

    people_by_id = {
        p.id: p
        for p in db.query(Personnel).filter(Personnel.id.in_({r.personnel_id for r in records})).all()
    }
    return [_to_out(r, people_by_id[r.personnel_id]) for r in records if r.personnel_id in people_by_id]


@router.post("/bulk", response_model=list[OutOfUnitRecordOut], status_code=status.HTTP_201_CREATED)
def bulk_create_out_of_unit_records(payload: OutOfUnitRecordBulkCreate, db: Session = Depends(get_db)):
    _validate_dates(payload.from_date, payload.to_date)

    people_by_id = {
        p.id: p for p in db.query(Personnel).filter(Personnel.id.in_(payload.personnel_ids)).all()
    }

    created: list[OutOfUnitRecord] = []
    for personnel_id in payload.personnel_ids:
        person = people_by_id.get(personnel_id)
        if person is None:
            continue
        record = OutOfUnitRecord(
            personnel_id=personnel_id,
            status=payload.status,
            from_date=payload.from_date,
            to_date=payload.to_date,
            remarks=payload.remarks,
        )
        db.add(record)
        created.append(record)

    db.commit()

    results = []
    for record in created:
        db.refresh(record)
        results.append(_to_out(record, people_by_id[record.personnel_id]))
    return results


@router.put("/{record_id}", response_model=OutOfUnitRecordOut)
def update_out_of_unit_record(record_id: int, payload: OutOfUnitRecordUpdate, db: Session = Depends(get_db)):
    record = db.get(OutOfUnitRecord, record_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    person = db.get(Personnel, payload.personnel_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Personnel not found")
    _validate_dates(payload.from_date, payload.to_date)

    record.personnel_id = payload.personnel_id
    record.status = payload.status
    record.from_date = payload.from_date
    record.to_date = payload.to_date
    record.remarks = payload.remarks
    db.commit()
    db.refresh(record)
    return _to_out(record, person)


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_out_of_unit_record(record_id: int, db: Session = Depends(get_db)):
    record = db.get(OutOfUnitRecord, record_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(record)
    db.commit()
