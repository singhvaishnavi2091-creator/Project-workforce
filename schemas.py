from datetime import date

from pydantic import BaseModel, ConfigDict

from app.models import AttendanceStatus, DutyLocation, DutyShift, OrgUnitLevel, OutOfUnitStatus, PersonnelCategory


class RankCreate(BaseModel):
    name: str
    category: PersonnelCategory
    seniority_order: int = 0


class RankUpdate(BaseModel):
    name: str
    category: PersonnelCategory
    seniority_order: int = 0


class RankOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: PersonnelCategory
    seniority_order: int
    personnel_count: int = 0


class StandingAppointmentCreate(BaseModel):
    name: str
    description: str | None = None
    exempt_duty_type_ids: list[int] = []


class StandingAppointmentUpdate(StandingAppointmentCreate):
    pass


class StandingAppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    exempt_duty_type_ids: list[int] = []
    holder_count: int = 0


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    is_active: bool


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class OrgUnitCreate(BaseModel):
    name: str
    level: OrgUnitLevel
    parent_id: int | None = None


class OrgUnitUpdate(BaseModel):
    name: str


class OrgUnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    level: OrgUnitLevel
    parent_id: int | None
    personnel_count: int = 0


class PersonnelBase(BaseModel):
    personnel_number: str
    rank_id: int
    name: str
    category: PersonnelCategory
    org_unit_id: int
    date_of_posting_in: date
    is_active: bool = True
    date_of_posting_out: date | None = None
    standing_appointment_id: int | None = None
    standing_appointment_since: date | None = None


class PersonnelCreate(PersonnelBase):
    pass


class PersonnelUpdate(PersonnelBase):
    pass


class PersonnelOut(PersonnelBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class PersonnelImportRowError(BaseModel):
    row: int
    personnel_number: str | None
    error: str


class PersonnelImportResult(BaseModel):
    total_rows: int
    imported: int
    skipped_duplicates: int
    errors: list[PersonnelImportRowError]


class AttendanceBulkUpsertItem(BaseModel):
    personnel_id: int
    status: AttendanceStatus
    remark: str | None = None


class AttendanceBulkUpsert(BaseModel):
    date: date
    records: list[AttendanceBulkUpsertItem]


class AttendanceRecordOut(BaseModel):
    personnel_id: int
    personnel_number: str
    name: str
    rank_id: int
    org_unit_id: int
    status: AttendanceStatus
    remark: str | None = None
    is_recorded: bool = False
    duty_names: list[str] = []
    out_of_unit_remark: str | None = None
    derived_status: AttendanceStatus = AttendanceStatus.PRESENT


class AttendanceHistoryEntry(BaseModel):
    date: date
    status: AttendanceStatus
    remark: str | None = None
    is_recorded: bool = False
    duty_names: list[str] = []
    out_of_unit_remark: str | None = None


class OutOfUnitRecordUpdate(BaseModel):
    personnel_id: int
    status: OutOfUnitStatus
    from_date: date
    to_date: date
    remarks: str | None = None


class OutOfUnitRecordBulkCreate(BaseModel):
    personnel_ids: list[int]
    status: OutOfUnitStatus
    from_date: date
    to_date: date
    remarks: str | None = None


class OutOfUnitRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    personnel_id: int
    personnel_number: str
    name: str
    rank_id: int
    org_unit_id: int
    status: OutOfUnitStatus
    from_date: date
    to_date: date
    remarks: str | None = None


class DutyTypeCreate(BaseModel):
    name: str
    location: DutyLocation
    number_required: int = 1
    eligible_categories: list[PersonnelCategory] = []
    eligible_rank_ids: list[int] = []
    requires_exclusive_presence: bool = False


class DutyTypeUpdate(DutyTypeCreate):
    pass


class DutyTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    location: DutyLocation
    number_required: int
    eligible_categories: list[PersonnelCategory]
    eligible_rank_ids: list[int]
    requires_exclusive_presence: bool


class DutyAssignmentUpdate(BaseModel):
    personnel_id: int
    duty_type_id: int
    from_date: date
    to_date: date
    shift: DutyShift | None = None
    remarks: str | None = None


class DutyAssignmentBulkCreate(BaseModel):
    personnel_ids: list[int]
    duty_type_id: int
    from_date: date
    to_date: date
    shift: DutyShift | None = None
    remarks: str | None = None


class DutyAssignmentOut(BaseModel):
    id: int
    personnel_id: int
    personnel_number: str
    name: str
    rank_id: int
    org_unit_id: int
    duty_type_id: int
    duty_type_name: str
    location: DutyLocation
    from_date: date
    to_date: date
    shift: DutyShift | None = None
    remarks: str | None = None


class DutyClashCheckRequest(BaseModel):
    personnel_ids: list[int]
    duty_type_id: int
    from_date: date
    to_date: date
    shift: DutyShift | None = None
    exclude_assignment_id: int | None = None


class DutyClashWarning(BaseModel):
    personnel_id: int
    name: str
    # "clash" (DTY-05 same-day clash), "exemption" (DTY-06 standing appointment),
    # or "unavailable" (DTY-04 on leave / deputation / training)
    kind: str = "clash"
    conflicting_duty_type_name: str | None = None
    conflicting_from_date: date | None = None
    conflicting_to_date: date | None = None
    conflicting_shift: DutyShift | None = None
    standing_appointment_name: str | None = None
    unavailable_status: AttendanceStatus | None = None
    unavailable_date: date | None = None


class AvailabilityConflict(BaseModel):
    # "duty" (an overlapping exclusive/out-of-station duty) or the out-of-unit
    # reason: "Leave" / "On-Deputation" / "Training".
    kind: str
    label: str
    from_date: date
    to_date: date
    shift: DutyShift | None = None
    remark: str | None = None


class AvailabilityOut(BaseModel):
    personnel_id: int
    personnel_number: str
    name: str
    rank_id: int
    from_date: date
    to_date: date
    is_available: bool
    conflicts: list[AvailabilityConflict] = []
