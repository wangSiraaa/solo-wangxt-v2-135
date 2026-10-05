"""SQLAlchemy 模型: 案例(含客流/建筑参数/随机种子)、仿真结果与事件日志。

默认连 PostgreSQL(DATABASE_URL),未设置时降级到本地 SQLite,便于无 PG 环境运行。
JSON 字段在 PG 上用 JSONB,SQLite 上用 TEXT 序列化。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from sqlalchemy import (Column, DateTime, Float, Integer, String, Text,
                        create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.types import TypeDecorator

DATABASE_URL = os.getenv(
    "DATABASE_URL", "sqlite:////workspace/data/elevator.db")

Base = declarative_base()


class JSONType(TypeDecorator):
    """跨后端 JSON: PG 用 JSONB,其他用 TEXT。"""
    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if dialect.name == "postgresql" or value is None:
            return value
        return json.dumps(value, ensure_ascii=False)

    def process_result_value(self, value, dialect):
        if dialect.name == "postgresql" or value is None:
            return value
        return json.loads(value)


class Scenario(Base):
    __tablename__ = "scenarios"
    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, default="")
    seed = Column(Integer, nullable=False, default=42)
    # 楼层需求(乘客 OD 与到达时间)、容量、开关门耗时等全部建筑参数
    config = Column(JSONType, nullable=False)
    passengers = Column(JSONType, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Run(Base):
    __tablename__ = "runs"
    id = Column(Integer, primary_key=True)
    scenario_id = Column(Integer, nullable=False, index=True)
    strategy = Column(String(32), nullable=False, index=True)
    seed = Column(Integer, nullable=False)
    metrics = Column(JSONType, nullable=False)
    validation = Column(JSONType, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    time = Column(Float, nullable=False)
    type = Column(String(48), nullable=False, index=True)
    payload = Column(JSONType, nullable=False)


connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)


def init_db():
    os.makedirs("/workspace/data", exist_ok=True)
    Base.metadata.create_all(engine)
