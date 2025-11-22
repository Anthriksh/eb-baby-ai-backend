from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# This will create a file named baby.db in your project folder
DATABASE_URL = "sqlite:///./baby.db"

# Create the database engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}  # required for SQLite with FastAPI
)

# Each request will use this to talk to the database
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for our models (tables)
Base = declarative_base()

# Helper to get a DB session - we'll use this later in our routes
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()