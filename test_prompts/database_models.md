import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    String,
    Text,
    DateTime,
    Numeric,
    ForeignKey,
    Integer,
    Boolean,
    Index,
    func,
    Computed
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship
)
from sqlalchemy.dialects.postgresql import UUID

# Assumptions:
# 1. User.id is defined as String/Text to accommodate Clerk Auth IDs as per the provided schema, 
#    overriding the general UUID rule for this specific external integration.
# 2. Although not explicitly in the CREATE TABLE statements for all tables, updated_at columns 
#    are included for all models to comply with the technical requirements.
# 3. TimeEntry serves as the line item for Invoices based on the invoiceId foreign key 
#    and the ER diagram relation.
# 4. Invoice.invoiceNumber is handled as a server-side identity/serial column.

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "User"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # Clerk User ID
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String)
    companyName: Mapped[Optional[str]] = mapped_column(String)
    logoUrl: Mapped[Optional[str]] = mapped_column(String)
    stripeConnectId: Mapped[Optional[str]] = mapped_column(String)
    
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    clients: Mapped[List["Client"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    invoices: Mapped[List["Invoice"]] = relationship(back_populates="user")

class Client(Base):
    __tablename__ = "Client"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    userId: Mapped[str] = mapped_column(ForeignKey("User.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text)
    taxId: Mapped[Optional[str]] = mapped_column(String)
    defaultTaxRate: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="0.00")
    
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="clients")
    projects: Mapped[List["Project"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    invoices: Mapped[List["Invoice"]] = relationship(back_populates="client")

    __table_args__ = (
        Index("idx_client_user", "userId"),
    )

class Project(Base):
    __tablename__ = "Project"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    clientId: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("Client.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    hourlyRate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String, server_default="USD")
    status: Mapped[str] = mapped_column(String, server_default="ACTIVE")
    
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    client: Mapped["Client"] = relationship(back_populates="projects")
    time_entries: Mapped[List["TimeEntry"]] = relationship(back_populates="project", cascade="all, delete-orphan")

class TimeEntry(Base):
    __tablename__ = "TimeEntry"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    projectId: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("Project.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    startTime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    endTime: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration: Mapped[Optional[int]] = mapped_column(Integer)  # Duration in seconds
    isBilled: Mapped[bool] = mapped_column(Boolean, server_default="FALSE")
    invoiceId: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("Invoice.id"))
    
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="time_entries")
    invoice: Mapped[Optional["Invoice"]] = relationship(back_populates="time_entries")

    __table_args__ = (
        Index("idx_time_entry_project", "projectId"),
    )

class Invoice(Base):
    __tablename__ = "Invoice"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    userId: Mapped[str] = mapped_column(ForeignKey("User.id"), nullable=False)
    clientId: Mapped[uuid.UUID] = mapped_column(ForeignKey("Client.id"), nullable=False)
    invoiceNumber: Mapped[int] = mapped_column(Integer, autoincrement=True)
    status: Mapped[str] = mapped_column(String, server_default="DRAFT")
    issueDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    dueDate: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    taxTotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default="0.00")
    grandTotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stripePaymentIntentId: Mapped[Optional[str]] = mapped_column(String)
    pdfUrl: Mapped[Optional[str]] = mapped_column(String)
    
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="invoices")
    client: Mapped["Client"] = relationship(back_populates="invoices")
    time_entries: Mapped[List["TimeEntry"]] = relationship(back_populates="invoice")

    __table_args__ = (
        Index("idx_invoice_user", "userId"),
    )