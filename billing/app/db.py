"""Motor de base de datos y sesiones.

La app usa SQLite por defecto (suficiente para una instancia única de
facturación) y permite PostgreSQL cambiando DATABASE_URL.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, database_url: str) -> None:
        self.url = make_url(database_url)
        if self.url.get_backend_name() == "sqlite":
            database = self.url.database
            if database and database != ":memory:":
                Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

        connect_args = {"check_same_thread": False} if self.url.get_backend_name() == "sqlite" else {}
        self.engine: Engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args=connect_args,
            future=True,
        )
        if self.url.get_backend_name() == "sqlite":
            self._configure_sqlite(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

    @staticmethod
    def _configure_sqlite(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    def create_all(self) -> None:
        # Importa los modelos para registrar las tablas en Base.metadata.
        from app import models  # noqa: F401

        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self.session_factory()

    def dispose(self) -> None:
        self.engine.dispose()


def get_db(request: Request) -> Generator[Session, None, None]:
    db = request.app.state.db.session()
    try:
        yield db
    finally:
        db.close()
