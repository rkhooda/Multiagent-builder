version: '3.8'

services:
  db:
    image: postgres:15-alpine
    container_name: freelanceflow_db
    restart: always
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: pass
      POSTGRES_DB: freelanceflow
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U user -d freelanceflow"]
      interval: 10s
      timeout: 5s
      retries: 5

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: freelanceflow_backend
    restart: always
    environment:
      - DATABASE_URL=${DATABASE_URL:-postgresql://user:pass@db:5432/freelanceflow}
      - JWT_SECRET=${JWT_SECRET}
      - STRIPE_SECRET_KEY=${STRIPE_SECRET_KEY}
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8000:8000"

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    container_name: freelanceflow_frontend
    restart: always
    ports:
      - "80:80"
    depends_on:
      - backend

volumes:
  postgres_data: