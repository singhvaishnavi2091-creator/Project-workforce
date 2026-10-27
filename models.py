import enum
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class OrgUnitLevel(str, enum.Enum):
    COMPANY = "company"
    PLATOON = "platoon"
    SECTION = "section"


class OrgUnit(Base):
    __tablename__ = "org_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    level: Mapped[OrgUnitLevel] = mapped_column(Enum(OrgUnitLevel))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("org_units.id"), nullable=True)


class PersonnelCategory(str, enum.Enum):
    OFFR = "Offr"
    JCO = "JCO"
    OR = "OR"


class Rank(Base):
    __tablename__ = "ranks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    category: Mapped[PersonnelCategory] = mapped_column(Enum(PersonnelCategory))
    seniority_order: Mapped[int] = mapped_column(default=0)


class StandingAppointment(Base):
    """A regular job / standing appointment (e.g. Master Chef, Unit Clerk,
    MT Driver) that a person can hold. Holding one is decision-support
    context for duty rostering (DTY-04) — an exemption warning, not a hard
    block — since the assigner may still need to pull that person in."""

    __tablename__ = "standing_appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Configurable by an administrator — which duty types this appointment
    # exempts its holders from. Empty means no exemptions configured yet
    # (never assumed / never hardcoded).
    exempt_duty_type_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)


class Personnel(Base):
    __tablename__ = "personnel"

    id: Mapped[int] = mapped_column(primary_key=True)
    personnel_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    rank_id: Mapped[int] = mapped_column(ForeignKey("ranks.id"))
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[PersonnelCategory] = mapped_column(Enum(PersonnelCategory))
    org_unit_id: Mapped[int] = mapped_column(ForeignKey("org_units.id"))
    date_of_posting_in: Mapped[date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    date_of_posting_out: Mapped[date | None] = mapped_column(Date, nullable=True)
    standing_appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("standing_appointments.id"), nullable=True
    )
    standing_appointment_since: Mapped[date | None] = mapped_column(Date, nullable=True)


class AttendanceStatus(str, enum.Enum):
    PRESENT = "Present"
    ON_DEPUTATION = "On-Deputation"
    ON_DUTY = "On-Duty"
    TRAINING = "Training"
    LEAVE = "Leave"
    ABSENT = "Absent"


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("personnel_id", "date", name="uq_attendance_personnel_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    personnel_id: Mapped[int] = mapped_column(ForeignKey("personnel.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[AttendanceStatus] = mapped_column(Enum(AttendanceStatus, name="attendancestatus"))
    remark: Mapped[str | None] = mapped_column(String(200), nullable=True)


class OutOfUnitStatus(str, enum.Enum):
    LEAVE = "Leave"
    ON_DEPUTATION = "On-Deputation"
    TRAINING = "Training"


class OutOfUnitRecord(Base):
    """A lightweight stand-in for the full Leave / Deputation / Training
    modules (BRD Phases 3-4): just enough to record a person being away for
    one of these three reasons over a date range, so Attendance can derive
    the correct status automatically instead of a clerk keying it by hand
    every day. No entitlement/balance/sanction workflow — that's deferred."""

    __tablename__ = "out_of_unit_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    personnel_id: Mapped[int] = mapped_column(ForeignKey("personnel.id"), index=True)
    status: Mapped[OutOfUnitStatus] = mapped_column(Enum(OutOfUnitStatus, name="outofunitstatus"))
    from_date: Mapped[date] = mapped_column(Date, index=True)
    to_date: Mapped[date] = mapped_column(Date, index=True)
    remarks: Mapped[str | None] = mapped_column(String(200), nullable=True)


class DutyLocation(str, enum.Enum):
    ON_CAMPUS = "On-Campus"
    OUT_OF_STATION = "Out-of-Station"


class DutyType(Base):
    __tablename__ = "duty_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    location: Mapped[DutyLocation] = mapped_column(Enum(DutyLocation, name="dutylocation"))
    number_required: Mapped[int] = mapped_column(default=1)
    eligible_categories: Mapped[list[str]] = mapped_column(ARRAY(String(10)), default=list)
    eligible_rank_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    requires_exclusive_presence: Mapped[bool] = mapped_column(Boolean, default=False)


class DutyShift(str, enum.Enum):
    MORNING = "Morning"
    AFTERNOON = "Afternoon"
    EVENING = "Evening"
    NIGHT = "Night"


class DutyAssignment(Base):
    __tablename__ = "duty_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    personnel_id: Mapped[int] = mapped_column(ForeignKey("personnel.id"), index=True)
    duty_type_id: Mapped[int] = mapped_column(ForeignKey("duty_types.id"), index=True)
    from_date: Mapped[date] = mapped_column(Date, index=True)
    to_date: Mapped[date] = mapped_column(Date, index=True)
    # Null means "whole day" (appointments, labor parties, out-of-station
    # duties) rather than a specific rostered shift (guard/sentry posts).
    shift: Mapped[DutyShift | None] = mapped_column(Enum(DutyShift, name="dutyshift"), nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(200), nullable=True)
