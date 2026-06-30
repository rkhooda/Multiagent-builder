# FreelanceFlow

FreelanceFlow is a comprehensive project and client management platform designed for freelancers. It provides a robust suite of tools for tracking projects, managing invoices, and streamlining communication within a high-performance, containerized environment.

## Tech Stack

- **Frontend**: React 19 + Vite + TailwindCSS
- **Backend**: FastAPI (Python 3.11)
- **Database**: PostgreSQL 15
- **Authentication**: JWT (JSON Web Tokens) with bcrypt password hashing
- **Infrastructure**: Docker & Docker Compose
- **Key Libraries**:
    - **SQLAlchemy**: ORM for database interactions.
    - **Alembic**: Database migration management.
    - **Stripe**: Payment processing and invoicing.
    - **SendGrid**: Transactional email services.

## Prerequisites

Before setting up the project, ensure you have the following installed:
- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)
- [Git](https://git-scm.com/)

## Local Setup

Follow these steps to get the development environment running:

### 1. Clone the Repository
```bash
git clone https://github.com/your-org/freelanceflow.git
cd freelanceflow
```

### 2. Configure Environment Variables
Copy the example environment file and update it with your local configurations.
```bash
cp .env.example .env
```
Ensure the `DATABASE_URL` matches the service names defined in the `docker-compose.yml`:
`DATABASE_URL=postgresql://user:pass@db:5432/freelanceflow`

### 3. Build and Start the Application
Run the following command to build the images and start the containers:
```bash
docker compose up --build
```

The application will be accessible at:
- **Frontend**: `http://localhost`
- **Backend API**: `http://localhost/api`
- **Interactive API Docs (Swagger)**: `http://localhost/api/docs`

## System Architecture

FreelanceFlow utilizes a containerized architecture managed by Nginx as a reverse proxy:

- **Nginx**: Acts as the main entry point (Port 80). It serves the compiled React static files and proxies requests starting with `/api/` to the FastAPI backend.
- **Frontend (React)**: A modern SPA (Single Page Application) optimized with Vite, built in a multi-stage Docker process.
- **Backend (FastAPI)**: An asynchronous Python API service that handles business logic, authentication, and third-party integrations (Stripe, SendGrid).
- **Database (PostgreSQL)**: A persistent relational database storing user profiles, project metadata, and financial records.

## Development Workflow

### Database Migrations
When changes are made to the database models, generate and apply migrations using Alembic inside the backend container:
```bash
docker compose exec backend alembic revision --autogenerate -m "description of changes"
docker compose exec backend alembic upgrade head
```

### Running Tests
To run the backend test suite:
```bash
docker compose exec backend pytest
```

### Environment Variables
The following core variable is required for the application to start:
- `DATABASE_URL`: The connection string for the PostgreSQL instance.

## Deployment

The application is designed to be deployed to any Docker-capable VPS. The production configuration utilizes Nginx to handle SSL termination and serve the frontend efficiently.

1. Set up a VPS with Docker and Docker Compose.
2. Clone the repository.
3. Configure the `.env` file with production secrets.
4. Run `docker compose up -d`.