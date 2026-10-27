from sqlalchemy.orm import Session

from app.models import User
from app.security import hash_password


def seed_default_admin(db: Session) -> None:
    exists = db.query(User).filter(User.username == "admin").first()
    if exists:
        return

    admin = User(
        username="admin",
        full_name="Administrator",
        hashed_password=hash_password("password"),
        is_active=True,
    )
    db.add(admin)
    db.commit()
