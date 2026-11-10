"""
Database Connection Helper
Loads DATABASE_URL from environment variables via python-dotenv.
No hardcoded fallbacks - fails fast if configuration is missing.
"""

import os
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Generator

import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH, override=False)


class DatabaseConfigError(Exception):
    """Raised when database configuration is missing or invalid."""
    pass


def get_database_url() -> str:
    """
    Get DATABASE_URL from environment.
    Raises DatabaseConfigError if not set.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise DatabaseConfigError(
            "DATABASE_URL not set in environment. "
            f"Ensure .env exists at {ENV_PATH} with DATABASE_URL configured."
        )
    return database_url


def get_pool_config() -> dict:
    """Get connection pool configuration from environment with sensible defaults."""
    return {
        "minconn": int(os.getenv("DB_POOL_MIN", "1")),
        "maxconn": int(os.getenv("DB_POOL_MAX", "10")),
    }


# Module-level connection pool (lazy initialization)
_pool: Optional[SimpleConnectionPool] = None


def get_connection_pool() -> SimpleConnectionPool:
    """Get or create the global connection pool."""
    global _pool
    if _pool is None:
        database_url = get_database_url()
        pool_config = get_pool_config()
        _pool = SimpleConnectionPool(
            minconn=pool_config["minconn"],
            maxconn=pool_config["maxconn"],
            dsn=database_url,
            cursor_factory=RealDictCursor,
        )
    return _pool


@contextmanager
def get_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context manager for a database connection from the pool.
    Automatically returns connection to pool on exit.
    """
    pool = get_connection_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor(commit: bool = True) -> Generator[psycopg2.extras.RealDictCursor, None, None]:
    """
    Context manager for a database cursor.
    Commits by default, rolls back on exception.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            yield cursor
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()


def execute_sql_file(sql_path: Path) -> None:
    """Execute a SQL migration file."""
    sql_content = sql_path.read_text(encoding="utf-8")
    with get_cursor() as cursor:
        cursor.execute(sql_content)


def run_migrations(migrations_dir: Optional[Path] = None) -> None:
    """Run all SQL migrations in order."""
    if migrations_dir is None:
        migrations_dir = PROJECT_ROOT / "db" / "migrations"

    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        print(f"No migration files found in {migrations_dir}")
        return

    for sql_file in migration_files:
        print(f"Running migration: {sql_file.name}")
        try:
            execute_sql_file(sql_file)
            print(f"  ✓ {sql_file.name} completed")
        except Exception as e:
            print(f"  ✗ {sql_file.name} failed: {e}")
            raise


def run_seed(seed_path: Optional[Path] = None) -> None:
    """Run seed data SQL file."""
    if seed_path is None:
        seed_path = PROJECT_ROOT / "db" / "seeds" / "mock_seed.sql"

    print(f"Running seed: {seed_path.name}")
    execute_sql_file(seed_path)
    print(f"  ✓ {seed_path.name} completed")


def close_pool() -> None:
    """Close the connection pool (call on shutdown)."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


def test_connection() -> bool:
    """Test database connectivity. Returns True if successful."""
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT 1 as test, version() as version")
            result = cursor.fetchone()
            print(f"Database connection successful: {result['version']}")
            return True
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False


if __name__ == "__main__":
    # Allow running as script for quick connection test
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        success = test_connection()
        sys.exit(0 if success else 1)
    elif len(sys.argv) > 1 and sys.argv[1] == "migrate":
        run_migrations()
    elif len(sys.argv) > 1 and sys.argv[1] == "seed":
        run_seed()
    else:
        print("Usage: python -m src.db.connection [test|migrate|seed]")