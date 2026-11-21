from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EditorStream(Base):
    __tablename__ = "editor_streams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255))

    published_offers: Mapped[list] = mapped_column(JSON, default=list)
    draft_offers: Mapped[list] = mapped_column(JSON, default=list)