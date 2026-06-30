# Architecture Blueprint: FreelanceFlow

## Folder Structure
```text
freelance-flow/
├── app/
│   ├── (auth)/
│   │   ├── sign-in/
│   │   │   └── [[...sign-in]]/
│   │   │       └── page.tsx
│   │   ├── sign-up/
│   │   │   └── [[...sign-up]]/
│   │   │       └── page.tsx
│   │   └── layout.tsx
│   ├── (dashboard)/
│   │   ├── clients/
│   │   │   ├── [clientId]/
│   │   │   │   └── page.tsx
│   │   │   └── page.tsx
│   │   ├── projects/
│   │   │   ├── [projectId]/
│   │   │   │   └── page.tsx
│   │   │   └── page.tsx
│   │   ├── invoices/
│   │   │   ├── [invoiceId]/
│   │   │   │   ├── preview/
│   │   │   │   │   └── page.tsx
│   │   │   │   └── page.tsx
│   │   │   └── page.tsx
│   │   ├── time-logs/
│   │   │   └── page.tsx
│   │   ├── layout.tsx
│   │   └── page.tsx
│   ├── api/
│   │   ├── webhooks/
│   │   │   ├── clerk/
│   │   │   │   └── route.ts
│   │   │   └── stripe/
│   │   │       └── route.ts
│   │   ├── invoices/
│   │   │   └── [invoiceId]/
│   │   │       └── download/
│   │   │           └── route.ts
│   │   └── upload/
│   │       └── route.ts
│   ├── favicon.ico
│   ├── globals.css
│   └── layout.tsx
├── components/
│   ├── dashboard/
│   │   ├── RevenueChart.tsx
│   │   ├── StatCards.tsx
│   │   ├── RecentInvoices.tsx
│   │   └── CashFlowForecast.tsx
│   ├── clients/
│   │   ├── ClientForm.tsx
│   │   ├── ClientList.tsx
│   │   └── ClientDetailsCard.tsx
│   ├── projects/
│   │   ├── ProjectForm.tsx
│   │   ├── ProjectList.tsx
│   │   └── RateConfigurator.tsx
│   ├── time/
│   │   ├── StopWatch.tsx
│   │   ├── ManualEntryForm.tsx
│   │   └── TimeLogTable.tsx
│   ├── invoices/
│   │   ├── InvoiceBuilder.tsx
│   │   ├── InvoicePDFTemplate.tsx
│   │   ├── InvoiceStatusBadge.tsx
│   │   └── SendInvoiceButton.tsx
│   ├── ui/
│   │   ├── button.tsx
│   │   ├── card.tsx
│   │   ├── dialog.tsx
│   │   ├── input.tsx
│   │   ├── table.tsx
│   │   └── toast.tsx
│   ├── Navbar.tsx
│   └── Sidebar.tsx
├── lib/
│   ├── actions/
│   │   ├── client-actions.ts
│   │   ├── invoice-actions.ts
│   │   ├── project-actions.ts
│   │   └── time-actions.ts
│   ├── db.ts
│   ├── stripe.ts
│   ├── resend.ts
│   ├── pdf-generator.ts
│   └── utils.ts
├── prisma/
│   ├── schema.prisma
│   └── seed.ts
├── public/
│   ├── logos/
│   │   └── placeholder-logo.png
│   └── fonts/
│       └── Inter-Regular.ttf
├── types/
│   ├── index.ts
│   └── prisma-ext.ts
├── middleware.ts
├── next.config.ts
├── package.json
├── postcss.config.js
├── tailwind.config.ts
└── tsconfig.json
```

## Database Schema
```sql
-- Users Table (Extends Clerk Auth Data)
CREATE TABLE "User" (
    "id" TEXT PRIMARY KEY, -- Clerk User ID
    "email" TEXT UNIQUE NOT NULL,
    "name" TEXT,
    "companyName" TEXT,
    "logoUrl" TEXT,
    "stripeConnectId" TEXT,
    "createdAt" TIMESTAMP NOT NULL DEFAULT NOW(),
    "updatedAt" TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Clients Table
CREATE TABLE "Client" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "userId" TEXT NOT NULL REFERENCES "User"("id") ON DELETE CASCADE,
    "name" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "address" TEXT,
    "taxId" TEXT,
    "defaultTaxRate" DECIMAL(5,2) DEFAULT 0.00,
    "createdAt" TIMESTAMP NOT NULL DEFAULT NOW(),
    "updatedAt" TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Projects Table
CREATE TABLE "Project" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "clientId" UUID NOT NULL REFERENCES "Client"("id") ON DELETE CASCADE,
    "name" TEXT NOT NULL,
    "hourlyRate" DECIMAL(12,2) NOT NULL,
    "currency" TEXT DEFAULT 'USD',
    "status" TEXT DEFAULT 'ACTIVE',
    "createdAt" TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Time Entries Table
CREATE TABLE "TimeEntry" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "projectId" UUID NOT NULL REFERENCES "Project"("id") ON DELETE CASCADE,
    "description" TEXT,
    "startTime" TIMESTAMP NOT NULL,
    "endTime" TIMESTAMP,
    "duration" INTEGER, -- Duration in seconds
    "isBilled" BOOLEAN DEFAULT FALSE,
    "invoiceId" UUID, -- Link to Invoice if billed
    "createdAt" TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Invoices Table
CREATE TABLE "Invoice" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "userId" TEXT NOT NULL REFERENCES "User"("id"),
    "clientId" UUID NOT NULL REFERENCES "Client"("id"),
    "invoiceNumber" SERIAL,
    "status" TEXT DEFAULT 'DRAFT', -- DRAFT, SENT, OVERDUE, PAID
    "issueDate" TIMESTAMP NOT NULL DEFAULT NOW(),
    "dueDate" TIMESTAMP NOT NULL,
    "taxTotal" DECIMAL(12,2) DEFAULT 0.00,
    "grandTotal" DECIMAL(12,2) NOT NULL,
    "stripePaymentIntentId" TEXT,
    "pdfUrl" TEXT,
    "createdAt" TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Indexing for performance
CREATE INDEX "idx_time_entry_project" ON "TimeEntry"("projectId");
CREATE INDEX "idx_invoice_user" ON "Invoice"("userId");
CREATE INDEX "idx_client_user" ON "Client"("userId");
```

```mermaid
erDiagram
    USER ||--o{ CLIENT : manages
    CLIENT ||--o{ PROJECT : owns
    PROJECT ||--o{ TIME_ENTRY : records
    USER ||--o{ INVOICE : issues
    CLIENT ||--o{ INVOICE : receives
    INVOICE ||--o{ TIME_ENTRY : bills
```

## API Endpoints
| Method | Path | Auth Required | Request Body | Response | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GET | `/api/dashboard/stats` | Yes | None | `{ revenue: float, outstanding: float }` | Fetch KPI data for dashboard widgets |
| POST | `/api/clients` | Yes | `{ name, email, address }` | `Client` object | Create a new client profile |
| GET | `/api/clients` | Yes | None | `Client[]` | List all clients for the user |
| POST | `/api/projects` | Yes | `{ clientId, name, hourlyRate }` | `Project` object | Create a new project under a client |
| GET | `/api/projects` | Yes | None | `Project[]` | List all projects with billable totals |
| POST | `/api/time-entries` | Yes | `{ projectId, startTime, description }` | `TimeEntry` | Start a new timer or log manual entry |
| PATCH | `/api/time-entries/[id]` | Yes | `{ endTime, duration }` | `TimeEntry` | Stop a timer or update entry details |
| GET | `/api/time-entries/unbilled` | Yes | `?projectId=...` | `TimeEntry[]` | Get logs ready for invoicing |
| POST | `/api/invoices` | Yes | `{ clientId, projectId, dueDate }` | `Invoice` object | Generate draft invoice from unbilled logs |
| GET | `/api/invoices/[id]` | Yes | None | `Invoice` details | Fetch specific invoice data |
| POST | `/api/invoices/[id]/send` | Yes | None | `{ success: bool }` | Trigger PDF gen and email via Resend |
| GET | `/api/invoices/[id]/download` | No* | None | `application/pdf` | Download PDF (signed URL or public hash) |
| POST | `/api/webhooks/stripe` | No | Stripe event payload | `{ received: true }` | Handle payment success notifications |
| POST | `/api/webhooks/clerk` | No | Clerk event payload | `{ received: true }` | Sync user creation/deletion |
| GET | `/api/reports/cashflow` | Yes | `?range=6m` | `DataSeries[]` | Get visualization data for trends |

## Component Hierarchy
- `AppLayout (Shared)`
  - `ClerkProvider`
  - `Sidebar (Shared)`
    - `NavLinks`
    - `UserAccountSwitcher`
  - `Navbar (Shared)`
    - `GlobalSearch`
    - `NotificationsDropdown`
  - `DashboardPage`
    - `StatCards (Page-specific)`
    - `RevenueChart (Page-specific)`
    - `CashFlowForecast (Page-specific)`
    - `StopWatchWidget (Shared)`
  - `ClientsPage`
    - `ClientList (Page-specific)`
      - `ClientCard`
    - `AddClientModal (Shared)`
  - `InvoicesPage`
    - `InvoiceTable (Page-specific)`
    - `InvoiceFilters`
    - `InvoiceBuilder (Shared)`
      - `LineItemEditor`
      - `PDFPreview`
  - `ProjectsPage`
    - `ProjectGrid (Page-specific)`
      - `ProjectProgressCircle`
    - `ProjectForm`
  - `SettingsPage`
    - `ProfileSection`
    - `StripeConnectStatus`
    - `BrandingCustomizer`

## Data Flow
```mermaid
flowchart TD
    User([User]) -->|Starts Timer| Timer[StopWatch Component]
    Timer -->|On Stop| ServerAction[createTimeEntry Action]
    ServerAction -->|Writes| DB[(PostgreSQL)]
    
    User -->|Triggers Invoicing| InvoiceGen[Invoice Builder]
    InvoiceGen -->|Reads Unbilled Logs| DB
    InvoiceGen -->|Calls| Puppeteer[Puppeteer Service]
    Puppeteer -->|Generates PDF| S3[Uploadthing / S3]
    
    S3 -->|URL Saved| DB
    InvoiceGen -->|Calls| Resend[Resend API]
    Resend -->|Emails PDF| Client([Client])
    
    Client -->|Clicks Pay Now| Stripe[Stripe Checkout]
    Stripe -->|Successful Payment| StripeWebhook[Stripe Webhook Handler]
    StripeWebhook -->|Update Status to PAID| DB
    DB -->|Refreshes UI| User
```

## Security Approach
- **Authentication**: Managed via **Clerk**. Session tokens are stored in secure, HTTP-only cookies. Social SSO (Google/GitHub) and MFA are enabled for sensitive financial data.
- **Authorization**: Implementation of **Row Level Security (RLS)** logic within Next.js Server Actions. Every query/mutation checks if the `userId` in the session matches the `userId` of the target record (Client, Project, or Invoice).
- **Input Validation**: **Zod** is used to validate all incoming data in Server Actions and API routes. This prevents malformed data from reaching the database and ensures currency/tax rates are valid numbers.
- **Rate Limiting & CORS**: Vercel's Edge Middleware implements rate limiting for the `/api/invoices/[id]/download` and `/api/webhooks` routes to prevent DDoS and brute-force. CORS is restricted to the specific domain and Stripe's webhook IP ranges.
- **Secrets Management**: Sensitive keys (Stripe Secret, Resend API Key, Clerk Secret) are stored in **Vercel Environment Variables** and never exposed to the client-side code.

## Environment Variables
| Variable Name | Description | Example Value |
| :--- | :--- | :--- |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:pass@ep-flat-db.neon.tech/main` |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk public key for client-side auth | `pk_test_...` |
| `CLERK_SECRET_KEY` | Clerk secret for backend auth | `sk_test_...` |
| `STRIPE_SECRET_KEY` | Stripe backend key for processing | `sk_live_...` |
| `STRIPE_WEBHOOK_SECRET` | Secret to verify Stripe events | `whsec_...` |
| `RESEND_API_KEY` | API key for sending emails | `re_123456789` |
| `NEXT_PUBLIC_APP_URL` | Base URL of the application | `https://freelanceflow.com` |
| `UPLOADTHING_SECRET` | Secret key for S3 file storage | `sk_live_...` |
| `UPLOADTHING_APP_ID` | App ID for Uploadthing | `ut_app_...` |