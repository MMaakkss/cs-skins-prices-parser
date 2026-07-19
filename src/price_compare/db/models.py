from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Marketplace(Base):
    __tablename__ = "marketplaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    url: Mapped[str] = mapped_column(String(255), nullable=True)

    # Fee reference read by the API to compute net profit. Not used for any
    # computation here — price_compare only stores it. Fractions, e.g. 0.1304.
    sell_fee_percent: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, server_default=text("0")
    )
    buy_fee_percent: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, server_default=text("0")
    )
    # Whether sale proceeds can be withdrawn to real money. Steam wallet funds
    # cannot, so the API must not present Steam "profit" as cashable.
    payout_withdrawable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    prices: Mapped[list["PriceRecord"]] = relationship(back_populates="marketplace")


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        Index("ix_items_weapon_exterior", "weapon", "exterior"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_hash_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    weapon: Mapped[str | None] = mapped_column(String(100), nullable=True)
    skin_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    exterior: Mapped[str | None] = mapped_column(String(50), nullable=True)
    icon_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stattrak: Mapped[bool] = mapped_column(Boolean, default=False)
    souvenir: Mapped[bool] = mapped_column(Boolean, default=False)

    prices: Mapped[list["PriceRecord"]] = relationship(back_populates="item")


class PriceRecord(Base):
    __tablename__ = "price_records"
    __table_args__ = (
        UniqueConstraint(
            "item_id", "marketplace_id", "price_type", "recorded_at",
            name="uq_price_snapshot",
        ),
        # Supports "latest price per (item, marketplace, price_type)" queries
        # (DISTINCT ON ... ORDER BY recorded_at DESC) on the API side.
        Index(
            "ix_price_records_latest",
            "item_id", "marketplace_id", "price_type",
            text("recorded_at DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "ask" (lowest listing) is all we collect now; "bid" (buy order) is
    # reserved for a future phase — the schema is ready for it.
    price_type: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'ask'")
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    item: Mapped["Item"] = relationship(back_populates="prices")
    marketplace: Mapped["Marketplace"] = relationship(back_populates="prices")
