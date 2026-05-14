from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

JSONBType = JSONB().with_variant(JSON(), "sqlite")

