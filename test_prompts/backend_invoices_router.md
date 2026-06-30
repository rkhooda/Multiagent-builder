from uuid import UUID
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, func

from ..database import get_db
from ..dependencies import get_current_user
from ..models import Invoice, TimeEntry, Project, Client, User
from pydantic import BaseModel, Field, EmailStr

# Assumptions:
# 1. get_db and get_current_user are provided in the ..dependencies/..database module.
# 2. The User object returned by get_current_user has an 'id' attribute corresponding to Clerk ID.
# 3. Calculation of invoice totals is done based on Project hourly rate and TimeEntry duration (seconds).
# 4. Invoices are created as 'DRAFT' status by default.
# 5. Deleting an invoice reverts associated TimeEntries to 'isBilled=False'.

router = APIRouter(
    prefix="/api/v1/invoices",
    tags=["invoices"]
)

# --- Pydantic Schemas ---

class InvoiceBase(BaseModel):
    clientId: UUID
    projectId: UUID
    dueDate: datetime

class InvoiceCreate(InvoiceBase):
    pass

class InvoiceUpdate(BaseModel):
    status: Optional[str] = None
    dueDate: Optional[datetime] = None
    taxTotal: Optional[Decimal] = None
    grandTotal: Optional[Decimal] = None

class TimeEntryRead(BaseModel):
    id: UUID
    description: Optional[str]
    startTime: datetime
    endTime: Optional[datetime]
    duration: Optional[int]
    
    class Config:
        from_attributes = True

class InvoiceRead(BaseModel):
    id: UUID
    userId: str
    clientId: UUID
    invoiceNumber: int
    status: str
    issueDate: datetime
    dueDate: datetime
    taxTotal: Decimal
    grandTotal: Decimal
    stripePaymentIntentId: Optional[str]
    pdfUrl: Optional[str]
    createdAt: datetime
    updatedAt: datetime
    time_entries: List[TimeEntryRead] = []

    class Config:
        from_attributes = True

# --- Helper Functions ---

def calculate_invoice_totals(time_entries: List[TimeEntry], hourly_rate: Decimal, tax_rate: Decimal):
    """
    Calculates subtotal, tax, and grand total for a set of time entries.
    Duration is in seconds.
    """
    total_seconds = sum((te.duration or 0) for te in time_entries)
    # Convert seconds to hours
    total_hours = Decimal(total_seconds) / Decimal(3600)
    subtotal = total_hours * hourly_rate
    tax_amount = (subtotal * tax_rate) / Decimal(100)
    grand_total = subtotal + tax_amount
    return tax_amount, grand_total

# --- Route Handlers ---

@router.get("/", response_model=List[InvoiceRead])
async def list_invoices(
    status: Optional[str] = Query(None),
    client_id: Optional[UUID] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retrieves a list of invoices for the authenticated user.
    Supports filtering by status and client ID.
    """
    query = select(Invoice).where(Invoice.userId == current_user.id)
    
    if status:
        query = query.where(Invoice.status == status)
    if client_id:
        query = query.where(Invoice.clientId == client_id)
        
    result = db.execute(query)
    return result.scalars().all()

@router.post("/", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    invoice_data: InvoiceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Generates a draft invoice from unbilled time entries for a specific project.
    1. Validates client and project ownership.
    2. Fetches all unbilled time entries for the project.
    3. Calculates totals.
    4. Persists the invoice and updates time entries.
    """
    # Verify client ownership
    client = db.query(Client).filter(
        Client.id == invoice_data.clientId, 
        Client.userId == current_user.id
    ).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Verify project exists and belongs to client
    project = db.query(Project).filter(
        Project.id == invoice_data.projectId,
        Project.clientId == client.id
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Fetch unbilled time entries
    unbilled_entries = db.query(TimeEntry).filter(
        TimeEntry.projectId == project.id,
        TimeEntry.isBilled == False,
        TimeEntry.endTime.isnot(None)
    ).all()

    if not unbilled_entries:
        raise HTTPException(
            status_code=400, 
            detail="No unbilled time entries found for this project"
        )

    # Calculate totals
    tax_total, grand_total = calculate_invoice_totals(
        unbilled_entries, 
        project.hourlyRate, 
        client.defaultTaxRate
    )

    try:
        # Create Invoice
        new_invoice = Invoice(
            userId=current_user.id,
            clientId=client.id,
            dueDate=invoice_data.dueDate,
            taxTotal=tax_total,
            grandTotal=grand_total,
            status="DRAFT"
        )
        db.add(new_invoice)
        db.flush()  # Get invoice ID

        # Link and mark time entries as billed
        for entry in unbilled_entries:
            entry.invoiceId = new_invoice.id
            entry.isBilled = True

        db.commit()
        db.refresh(new_invoice)
        return new_invoice

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to create invoice: {str(e)}"
        )

@router.get("/{id}", response_model=InvoiceRead)
async def get_invoice(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Fetches details of a specific invoice including associated time entries.
    """
    invoice = db.query(Invoice).filter(
        Invoice.id == id,
        Invoice.userId == current_user.id
    ).first()
    
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    return invoice

@router.put("/{id}", response_model=InvoiceRead)
async def update_invoice(
    id: UUID,
    invoice_update: InvoiceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Updates invoice metadata such as status or due date.
    Note: Recalculating totals via PUT is restricted here; 
    primary use case is status transition (e.g., DRAFT -> SENT).
    """
    invoice = db.query(Invoice).filter(
        Invoice.id == id,
        Invoice.userId == current_user.id
    ).first()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    update_data = invoice_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(invoice, key, value)

    try:
        db.commit()
        db.refresh(invoice)
        return invoice
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update invoice")

@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Deletes an invoice and reverts associated time entries to 'unbilled' status.
    """
    invoice = db.query(Invoice).filter(
        Invoice.id == id,
        Invoice.userId == current_user.id
    ).first()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    try:
        # Revert time entries
        db.query(TimeEntry).filter(TimeEntry.invoiceId == id).update({
            "isBilled": False,
            "invoiceId": None
        })
        
        db.delete(invoice)
        db.commit()
        return None
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete invoice")