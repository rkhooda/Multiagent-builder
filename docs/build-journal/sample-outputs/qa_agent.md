# Quality Assurance Report: FreelanceFlow

## Critical Issues
- **File**: `backend/app/routers/invoices.py` / `frontend/src/pages/InvoicesPage.jsx`
  - *Issue*: **Data Schema Mismatch**: The frontend component `InvoicesPage.jsx` attempts to access fields `invoice_number`, `client_name`, and `total_amount`. However, the backend Pydantic schema `InvoiceRead` and SQLAlchemy model use `invoiceNumber`, `grandTotal`, and do not include a joined `client_name` (only `clientId`). This will result in `undefined` values and "Unknown Client" being displayed for all rows.
  - *Suggested Fix*: Update `InvoiceRead` schema to include client details and align field names.
    ```python
    # backend/app/routers/invoices.py
    class InvoiceRead(BaseModel):
        # ...
        invoiceNumber: int = Field(alias="invoice_number")
        grandTotal: Decimal = Field(alias="total_amount")
        client_name: Optional[str] = None # Populate via join
        # ...
    ```

- **File**: `backend/app/routers/invoices.py`
  - *Issue*: **Missing Endpoint Implementation**: The architecture blueprint explicitly requires a `POST /api/invoices/[id]/send` endpoint to trigger PDF generation and email via Resend. This route is missing from the backend implementation, but is actively called by the frontend `handleGeneratePDF` function. This will cause a 404 error in the UI.
  - *Suggested Fix*: Implement the route and integrate with a PDF service and Resend as per the blueprint.
    ```python
    @router.post("/{id}/send")
    async def send_invoice(id: UUID, db: Session = Depends(get_db)):
        # logic for PDF generation and Resend API call
        return {"success": True}
    ```

- **File**: `backend/app/models.py` vs `ARCHITECTURE_DOCUMENT`
  - *Issue*: **Tech Stack Deviation**: The Architecture Blueprint specifies a **Next.js** project using **Prisma** (`prisma/schema.prisma`) and **TypeScript**. The generated backend code is written in **Python/FastAPI** with **SQLAlchemy**. While functional in isolation, this violates the core architectural requirement of the project, making integration with Next.js Server Actions (defined in `lib/actions/`) impossible.
  - *Suggested Fix*: Re-generate the backend logic using Next.js API routes or Server Actions in TypeScript with Prisma as the ORM to match the blueprint.

## Warnings
- **File**: `frontend/src/pages/InvoicesPage.jsx` (Line: 71)
  - *Issue*: **Hardcoded Currency**: The currency formatting is hardcoded to 'USD' and 'en-US' locale. The `Project` model in the backend includes a `currency` field, which should be used to support international freelancers.
  - *Suggested Fix*: Pass the invoice's currency to the formatter: `formatCurrency(invoice.total_amount, invoice.currency)`.

- **File**: `backend/app/routers/invoices.py` (Line: 184)
  - *Issue*: **Suboptimal Update Logic**: The `update_invoice` endpoint allows updating `grandTotal` and `taxTotal` directly via PUT. Since these are calculated fields based on `TimeEntry` duration and `Project` rates, allowing manual overrides without recalculation logic can lead to data integrity issues between the invoice and its line items.
  - *Suggested Fix*: Restrict `InvoiceUpdate` to metadata (status, dueDate) or implement a server-side recalculation trigger when items change.

## Security Findings
- **File**: `backend/app/routers/invoices.py`
  - *Finding Type*: **Missing State Transition Validation**
  - *Details*: There are no checks on invoice status transitions. A user could potentially move an invoice from `PAID` back to `DRAFT` via the `update_invoice` endpoint, which could disrupt financial reporting or allow double-billing/deletion of paid records.
  - *Remediation*: Implement a state machine check in the `update_invoice` route to ensure valid transitions (e.g., DRAFT -> SENT -> PAID).

- **File**: `frontend/src/pages/InvoicesPage.jsx`
  - *Finding Type*: **Cross-Site Scripting (XSS) Risk**
  - *Details*: While React escapes data by default, the `client_name` and `invoiceNumber` are rendered directly. If a user provides a malicious client name via the (unseen) Client creation form, and that data is piped into an HTML-based PDF generator later, it could lead to Server-Side XSS or injection in the PDF context.
  - *Remediation*: Ensure strict Zod validation on the Client/Project creation side and sanitize inputs before rendering in PDF templates.

## Missing Pieces
- **File / Feature**: `backend/app/routers/invoices.py`
  - *Details*: The `GET /api/invoices/[id]/download` route defined in the API Endpoints table of the blueprint is missing. This is required for clients to access their invoices.
- **File / Feature**: `lib/pdf-generator.ts` & `lib/resend.ts`
  - *Details*: These files are defined in the folder structure but no logic was generated to handle the actual PDF creation or email delivery.
- **File / Feature**: `TimeEntry` Line Items in UI
  - *Details*: The `InvoicesPage.jsx` displays a table of invoices but lacks the "InvoiceBuilder" or "LineItemEditor" components defined in the Component Hierarchy, which are necessary for users to actually see what they are billing for.

## Summary
- **Critical Issues Count**: 3
- **Warnings Count**: 2
- **Security Findings Count**: 2
- **Overall Quality Rating**: **Needs Work**
- **Justification**: The implementation has a fundamental tech-stack mismatch, providing a Python backend for a Next.js blueprint. Furthermore, the frontend and backend are out of sync regarding field naming (camelCase vs snake_case), which would cause immediate runtime failures in the UI. Key features like PDF generation, downloading, and email sending are entirely absent despite being core requirements of the FreelanceFlow application.