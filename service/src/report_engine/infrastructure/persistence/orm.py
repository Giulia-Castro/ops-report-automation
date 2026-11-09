"""ORM tables. Portable naming: lowercase, unquoted, at most 30 characters, no reserved words."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuditMixin:
    createdat: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    createdby: Mapped[str] = mapped_column(String(100))
    updatedat: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updatedby: Mapped[str] = mapped_column(String(100))


class ReportRow(AuditMixin, Base):
    __tablename__ = "ops_report"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reportdate: Mapped[date] = mapped_column(Date, unique=True)
    reportstatus: Mapped[str] = mapped_column(String(20))
    healthstatus: Mapped[str] = mapped_column(String(10))
    summarysource: Mapped[str | None] = mapped_column(String(10))
    totalrequests: Mapped[int] = mapped_column(Integer)
    servererrors: Mapped[int] = mapped_column(Integer)
    errorrate: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    p95latencyms: Mapped[int] = mapped_column(Integer)
    anomalycount: Mapped[int] = mapped_column(Integer)
    metricsjson: Mapped[str] = mapped_column(Text)
    anomaliesjson: Mapped[str] = mapped_column(Text)
    summaryjson: Mapped[str | None] = mapped_column(Text)
    llmmodel: Mapped[str | None] = mapped_column(String(60))
    inputtokens: Mapped[int | None] = mapped_column(Integer)
    outputtokens: Mapped[int | None] = mapped_column(Integer)
    costusd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    generationms: Mapped[int] = mapped_column(Integer)
    generatedat: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deliveredat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Optimistic lock: UPDATE ... WHERE rowversion = :old, then rowversion + 1.
    rowversion: Mapped[int] = mapped_column(Integer)
    __mapper_args__ = {"version_id_col": rowversion}


class FailureRow(AuditMixin, Base):
    __tablename__ = "pipeline_failure"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflowname: Mapped[str] = mapped_column(String(100))
    nodename: Mapped[str | None] = mapped_column(String(100))
    failuremessage: Mapped[str | None] = mapped_column(String(500))
    executionid: Mapped[str | None] = mapped_column(String(64))
    occurredat: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Optimistic lock: UPDATE ... WHERE rowversion = :old, then rowversion + 1.
    rowversion: Mapped[int] = mapped_column(Integer)
    __mapper_args__ = {"version_id_col": rowversion}
