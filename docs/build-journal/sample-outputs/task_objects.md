[
  {
    "id": "db_001",
    "phase": "database",
    "filename": "schema.prisma",
    "filepath": "prisma/schema.prisma",
    "description": "Define the core Prisma schema including User (Clerk ID as PK), Client, Project (hourlyRate, currency), TimeEntry (duration in seconds), and Invoice (status, totals) models with all specified relations and indexes.",
    "requires": [],
    "context_sections": ["Database Schema"],
    "estimated_complexity": "medium"
  },
  {
    "id": "db_002",
    "phase": "database",
    "filename": "db.ts",
    "filepath": "lib/db.ts",
    "description": "Initialize the Prisma Client singleton to manage database connections across the Next.js application.",
    "requires": ["db_001"],
    "context_sections": ["Folder Structure"],
    "estimated_complexity": "low"
  },
  {
    "id": "be_001",
    "phase": "backend",
    "filename": "middleware.ts",
    "filepath": "middleware.ts",
    "description": "Configure Clerk authentication middleware to protect dashboard and API routes, ensuring only authenticated users can access private resources.",
    "requires": ["db_001"],
    "context_sections": ["Security Approach"],
    "estimated_complexity": "medium"
  },
  {
    "id": "be_002",
    "phase": "backend",
    "filename": "route.ts",
    "filepath": "app/api/webhooks/clerk/route.ts",
    "description": "Implement Clerk webhook handler to sync user creation and deletion from Clerk to the local User table.",
    "requires": ["db_002"],
    "context_sections": ["API Endpoints", "Data Flow"],
    "estimated_complexity": "medium"
  },
  {
    "id": "be_003",
    "phase": "backend",
    "filename": "client-actions.ts",
    "filepath": "lib/actions/client-actions.ts",
    "description": "Develop Server Actions for creating and fetching clients with Row Level Security checks using the session userId.",
    "requires": ["db_002"],
    "context_sections": ["API Endpoints", "Security Approach"],
    "estimated_complexity": "medium"
  },
  {
    "id": "be_004",
    "phase": "backend",
    "filename": "project-actions.ts",
    "filepath": "lib/actions/project-actions.ts",
    "description": "Develop Server Actions for project management, including hourly rate configuration and client association.",
    "requires": ["be_003"],
    "context_sections": ["API Endpoints", "Database Schema"],
    "estimated_complexity": "medium"
  },
  {
    "id": "be_005",
    "phase": "backend",
    "filename": "time-actions.ts",
    "filepath": "lib/actions/time-actions.ts",
    "description": "Implement Server Actions for starting timers, logging manual entries, and updating time entry durations.",
    "requires": ["be_004"],
    "context_sections": ["API Endpoints", "Data Flow"],
    "estimated_complexity": "medium"
  },
  {
    "id": "be_006",
    "phase": "backend",
    "filename": "pdf-generator.ts",
    "filepath": "lib/pdf-generator.ts",
    "description": "Create a PDF generation utility using Puppeteer to convert invoice data into professional PDF documents.",
    "requires": ["be_002"],
    "context_sections": ["Data Flow"],
    "estimated_complexity": "high"
  },
  {
    "id": "be_007",
    "phase": "backend",
    "filename": "invoice-actions.ts",
    "filepath": "lib/actions/invoice-actions.ts",
    "description": "Implement complex logic for generating invoices from unbilled time logs, calculating tax/totals, and integrating Resend for email delivery.",
    "requires": ["be_005", "be_006"],
    "context_sections": ["API Endpoints", "Data Flow"],
    "estimated_complexity": "high"
  },
  {
    "id": "be_008",
    "phase": "backend",
    "filename": "route.ts",
    "filepath": "app/api/webhooks/stripe/route.ts",
    "description": "Implement Stripe webhook handler to update invoice status to 'PAID' upon successful checkout completion.",
    "requires": ["be_007"],
    "context_sections": ["API Endpoints", "Security Approach"],
    "estimated_complexity": "medium"
  },
  {
    "id": "fe_001",
    "phase": "frontend",
    "filename": "layout.tsx",
    "filepath": "app/(dashboard)/layout.tsx",
    "description": "Build the main dashboard shell including Sidebar, Navbar, and authentication state wrappers.",
    "requires": ["be_001"],
    "context_sections": ["Component Hierarchy"],
    "estimated_complexity": "medium"
  },
  {
    "id": "fe_002",
    "phase": "frontend",
    "filename": "page.tsx",
    "filepath": "app/(dashboard)/page.tsx",
    "description": "Implement the main dashboard page featuring StatCards, RevenueChart, and the StopWatchWidget.",
    "requires": ["fe_001", "be_007"],
    "context_sections": ["Component Hierarchy", "API Endpoints"],
    "estimated_complexity": "high"
  },
  {
    "id": "fe_003",
    "phase": "frontend",
    "filename": "page.tsx",
    "filepath": "app/(dashboard)/clients/page.tsx",
    "description": "Develop the clients listing page with search, filtering, and the ClientForm modal for creating new entries.",
    "requires": ["fe_001", "be_003"],
    "context_sections": ["Component Hierarchy"],
    "estimated_complexity": "medium"
  },
  {
    "id": "fe_004",
    "phase": "frontend",
    "filename": "InvoiceBuilder.tsx",
    "filepath": "components/invoices/InvoiceBuilder.tsx",
    "description": "Create the interactive invoice creation interface allowing selection of unbilled time entries and live total previews.",
    "requires": ["fe_001", "be_007"],
    "context_sections": ["Component Hierarchy", "Data Flow"],
    "estimated_complexity": "high"
  },
  {
    "id": "fe_005",
    "phase": "frontend",
    "filename": "page.tsx",
    "filepath": "app/(dashboard)/invoices/page.tsx",
    "description": "Build the invoice management dashboard for viewing, filtering, and triggering the sending process for invoices.",
    "requires": ["fe_004"],
    "context_sections": ["Component Hierarchy"],
    "estimated_complexity": "medium"
  },
  {
    "id": "dv_001",
    "phase": "devops",
    "filename": "env.example",
    "filepath": ".env.example",
    "description": "Document all required environment variables including Clerk, Stripe, Resend, and Database credentials.",
    "requires": ["be_008"],
    "context_sections": ["Environment Variables"],
    "estimated_complexity": "low"
  },
  {
    "id": "dv_002",
    "phase": "devops",
    "filename": "next.config.ts",
    "filepath": "next.config.ts",
    "description": "Configure Next.js settings, including remote image patterns for logo uploads and optimized build parameters.",
    "requires": ["fe_005"],
    "context_sections": ["Folder Structure"],
    "estimated_complexity": "low"
  }
]