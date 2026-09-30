"""Base service with DB session injection."""
from sqlalchemy.orm import Session


class BaseService:
    """All domain services should inherit from this."""

    def __init__(self, db: Session):
        self.db = db
