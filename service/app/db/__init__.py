# 新架构持久化层(SQLModel ORM 模型 + 后续 engine/session)。
# 模型对齐 core/records.py 的 delayDB Write 载荷;落库由 shell 的 PersistWriter/OrmPersister 经此(见 db.md)。
# 原型 app/user、app/handrecord 的 SQLModel 是被取代物,Alembic 不再追踪(见 alembic/env.py / docs/db-migrations.md)。
