from sqlalchemy import JSON, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

JSONBType = JSONB().with_variant(JSON(), "sqlite")
TextArrayType = ARRAY(Text()).with_variant(JSON(), "sqlite")
