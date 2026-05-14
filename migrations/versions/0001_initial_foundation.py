"""initial foundation

Revision ID: 0001_initial_foundation
Revises:
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial_foundation"
down_revision = None
branch_labels = None
depends_on = None


def uuid_pk() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False)


def created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "data_sources",
        uuid_pk(),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        created_at(),
        updated_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_sources")),
        sa.UniqueConstraint("code", name=op.f("uq_data_sources_code")),
    )

    op.create_table(
        "exchanges",
        uuid_pk(),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        created_at(),
        updated_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exchanges")),
        sa.UniqueConstraint("code", name=op.f("uq_exchanges_code")),
    )

    op.create_table(
        "ingestion_runs",
        uuid_pk(),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collector_name", sa.String(length=120), nullable=False),
        sa.Column("run_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_collected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_inserted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        created_at(),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], name=op.f("fk_ingestion_runs_source_id_data_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_runs")),
    )
    op.create_index("ix_ingestion_runs_collector_name", "ingestion_runs", ["collector_name"])
    op.create_index("ix_ingestion_runs_source_id", "ingestion_runs", ["source_id"])
    op.create_index("ix_ingestion_runs_source_started_at", "ingestion_runs", ["source_id", "started_at"])
    op.create_index("ix_ingestion_runs_started_at", "ingestion_runs", ["started_at"])
    op.create_index("ix_ingestion_runs_status", "ingestion_runs", ["status"])

    op.create_table(
        "raw_payloads",
        uuid_pk(),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_endpoint", sa.Text(), nullable=True),
        sa.Column("payload_type", sa.String(length=50), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload_text", sa.Text(), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        created_at(),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"], name=op.f("fk_raw_payloads_ingestion_run_id_ingestion_runs")),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], name=op.f("fk_raw_payloads_source_id_data_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_payloads")),
        sa.UniqueConstraint("source_id", "payload_hash", name="uq_raw_payloads_source_payload_hash"),
    )
    op.create_index("ix_raw_payloads_collected_at", "raw_payloads", ["collected_at"])
    op.create_index("ix_raw_payloads_ingestion_run_id", "raw_payloads", ["ingestion_run_id"])
    op.create_index("ix_raw_payloads_payload_hash", "raw_payloads", ["payload_hash"])
    op.create_index("ix_raw_payloads_source_id", "raw_payloads", ["source_id"])
    op.create_index("ix_raw_payloads_status", "raw_payloads", ["status"])

    op.create_table(
        "sectors",
        uuid_pk(),
        sa.Column("code", sa.String(length=80), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        created_at(),
        updated_at(),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], name=op.f("fk_sectors_source_id_data_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sectors")),
        sa.UniqueConstraint("name", name=op.f("uq_sectors_name")),
    )
    op.create_index("ix_sectors_source_id", "sectors", ["source_id"])

    op.create_table(
        "companies",
        uuid_pk(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("sector_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_payload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        created_at(),
        updated_at(),
        sa.ForeignKeyConstraint(["raw_payload_id"], ["raw_payloads.id"], name=op.f("fk_companies_raw_payload_id_raw_payloads")),
        sa.ForeignKeyConstraint(["sector_id"], ["sectors.id"], name=op.f("fk_companies_sector_id_sectors")),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], name=op.f("fk_companies_source_id_data_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("slug", name=op.f("uq_companies_slug")),
    )
    op.create_index("ix_companies_is_active", "companies", ["is_active"])
    op.create_index("ix_companies_name", "companies", ["name"])
    op.create_index("ix_companies_sector_id", "companies", ["sector_id"])

    op.create_table(
        "instruments",
        uuid_pk(),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("exchange_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("isin", sa.String(length=20), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("instrument_type", sa.String(length=50), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("market_segment", sa.String(length=80), nullable=True),
        sa.Column("listing_date", sa.Date(), nullable=True),
        sa.Column("delisting_date", sa.Date(), nullable=True),
        sa.Column("shares_outstanding", sa.BigInteger(), nullable=True),
        sa.Column("free_float_percent", sa.Numeric(10, 6), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_payload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        created_at(),
        updated_at(),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name=op.f("fk_instruments_company_id_companies")),
        sa.ForeignKeyConstraint(["exchange_id"], ["exchanges.id"], name=op.f("fk_instruments_exchange_id_exchanges")),
        sa.ForeignKeyConstraint(["raw_payload_id"], ["raw_payloads.id"], name=op.f("fk_instruments_raw_payload_id_raw_payloads")),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], name=op.f("fk_instruments_source_id_data_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
        sa.UniqueConstraint("exchange_id", "isin", name="uq_instruments_exchange_isin"),
        sa.UniqueConstraint("exchange_id", "symbol", name="uq_instruments_exchange_symbol"),
    )
    op.create_index("ix_instruments_company_id", "instruments", ["company_id"])
    op.create_index("ix_instruments_exchange_id", "instruments", ["exchange_id"])
    op.create_index("ix_instruments_exchange_isin", "instruments", ["exchange_id", "isin"])
    op.create_index("ix_instruments_exchange_symbol", "instruments", ["exchange_id", "symbol"])
    op.create_index("ix_instruments_is_active", "instruments", ["is_active"])
    op.create_index("ix_instruments_isin", "instruments", ["isin"])
    op.create_index("ix_instruments_symbol", "instruments", ["symbol"])

    op.create_table(
        "market_indices",
        uuid_pk(),
        sa.Column("exchange_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_payload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        created_at(),
        updated_at(),
        sa.ForeignKeyConstraint(["exchange_id"], ["exchanges.id"], name=op.f("fk_market_indices_exchange_id_exchanges")),
        sa.ForeignKeyConstraint(["raw_payload_id"], ["raw_payloads.id"], name=op.f("fk_market_indices_raw_payload_id_raw_payloads")),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], name=op.f("fk_market_indices_source_id_data_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_indices")),
        sa.UniqueConstraint("exchange_id", "symbol", name="uq_market_indices_exchange_symbol"),
    )
    op.create_index("ix_market_indices_exchange_symbol", "market_indices", ["exchange_id", "symbol"])

    op.create_table(
        "latest_prices",
        uuid_pk(),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("price", sa.Numeric(20, 6), nullable=False),
        sa.Column("open_price", sa.Numeric(20, 6), nullable=True),
        sa.Column("high_price", sa.Numeric(20, 6), nullable=True),
        sa.Column("low_price", sa.Numeric(20, 6), nullable=True),
        sa.Column("previous_close", sa.Numeric(20, 6), nullable=True),
        sa.Column("change_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("change_percent", sa.Numeric(12, 6), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("traded_value", sa.Numeric(24, 6), nullable=True),
        sa.Column("market_cap", sa.Numeric(24, 6), nullable=True),
        sa.Column("price_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_payload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("data_quality_status", sa.String(length=30), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        created_at(),
        updated_at(),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"], name=op.f("fk_latest_prices_instrument_id_instruments")),
        sa.ForeignKeyConstraint(["raw_payload_id"], ["raw_payloads.id"], name=op.f("fk_latest_prices_raw_payload_id_raw_payloads")),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], name=op.f("fk_latest_prices_source_id_data_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_latest_prices")),
        sa.UniqueConstraint("instrument_id", name=op.f("uq_latest_prices_instrument_id")),
    )
    op.create_index("ix_latest_prices_data_quality_status", "latest_prices", ["data_quality_status"])
    op.create_index("ix_latest_prices_instrument_id", "latest_prices", ["instrument_id"])
    op.create_index("ix_latest_prices_price_timestamp", "latest_prices", ["price_timestamp"])
    op.create_index("ix_latest_prices_trading_date", "latest_prices", ["trading_date"])

    op.create_table(
        "price_bars",
        uuid_pk(),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeframe", sa.String(length=20), nullable=False),
        sa.Column("bar_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("open_price", sa.Numeric(20, 6), nullable=True),
        sa.Column("high_price", sa.Numeric(20, 6), nullable=True),
        sa.Column("low_price", sa.Numeric(20, 6), nullable=True),
        sa.Column("close_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("traded_value", sa.Numeric(24, 6), nullable=True),
        sa.Column("number_of_trades", sa.Integer(), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_payload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_adjusted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("data_quality_status", sa.String(length=30), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        created_at(),
        updated_at(),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"], name=op.f("fk_price_bars_instrument_id_instruments")),
        sa.ForeignKeyConstraint(["raw_payload_id"], ["raw_payloads.id"], name=op.f("fk_price_bars_raw_payload_id_raw_payloads")),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], name=op.f("fk_price_bars_source_id_data_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_bars")),
        sa.UniqueConstraint("instrument_id", "timeframe", "bar_timestamp", name="uq_price_bars_instrument_timeframe_timestamp"),
    )
    op.create_index("ix_price_bars_data_quality_status", "price_bars", ["data_quality_status"])
    op.create_index("ix_price_bars_instrument_timeframe_timestamp", "price_bars", ["instrument_id", "timeframe", "bar_timestamp"])
    op.create_index("ix_price_bars_trading_date", "price_bars", ["trading_date"])

    op.create_table(
        "latest_index_values",
        uuid_pk(),
        sa.Column("index_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.Numeric(20, 6), nullable=False),
        sa.Column("open_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("high_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("low_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("previous_close", sa.Numeric(20, 6), nullable=True),
        sa.Column("change_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("change_percent", sa.Numeric(12, 6), nullable=True),
        sa.Column("value_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_payload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("data_quality_status", sa.String(length=30), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        created_at(),
        updated_at(),
        sa.ForeignKeyConstraint(["index_id"], ["market_indices.id"], name=op.f("fk_latest_index_values_index_id_market_indices")),
        sa.ForeignKeyConstraint(["raw_payload_id"], ["raw_payloads.id"], name=op.f("fk_latest_index_values_raw_payload_id_raw_payloads")),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], name=op.f("fk_latest_index_values_source_id_data_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_latest_index_values")),
        sa.UniqueConstraint("index_id", name=op.f("uq_latest_index_values_index_id")),
    )

    op.create_table(
        "index_bars",
        uuid_pk(),
        sa.Column("index_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeframe", sa.String(length=20), nullable=False),
        sa.Column("bar_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("open_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("high_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("low_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("close_value", sa.Numeric(20, 6), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_payload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("data_quality_status", sa.String(length=30), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        created_at(),
        updated_at(),
        sa.ForeignKeyConstraint(["index_id"], ["market_indices.id"], name=op.f("fk_index_bars_index_id_market_indices")),
        sa.ForeignKeyConstraint(["raw_payload_id"], ["raw_payloads.id"], name=op.f("fk_index_bars_raw_payload_id_raw_payloads")),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], name=op.f("fk_index_bars_source_id_data_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_bars")),
        sa.UniqueConstraint("index_id", "timeframe", "bar_timestamp", name="uq_index_bars_index_timeframe_timestamp"),
    )
    op.create_index("ix_index_bars_index_timeframe_timestamp", "index_bars", ["index_id", "timeframe", "bar_timestamp"])
    op.create_index("ix_index_bars_trading_date", "index_bars", ["trading_date"])

    op.create_table(
        "normalization_errors",
        uuid_pk(),
        sa.Column("raw_payload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("error_type", sa.String(length=80), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("raw_fragment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        created_at(),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"], name=op.f("fk_normalization_errors_ingestion_run_id_ingestion_runs")),
        sa.ForeignKeyConstraint(["raw_payload_id"], ["raw_payloads.id"], name=op.f("fk_normalization_errors_raw_payload_id_raw_payloads")),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], name=op.f("fk_normalization_errors_source_id_data_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_normalization_errors")),
    )
    op.create_index("ix_normalization_errors_created_at", "normalization_errors", ["created_at"])
    op.create_index("ix_normalization_errors_error_type", "normalization_errors", ["error_type"])
    op.create_index("ix_normalization_errors_ingestion_run_id", "normalization_errors", ["ingestion_run_id"])
    op.create_index("ix_normalization_errors_raw_payload_id", "normalization_errors", ["raw_payload_id"])
    op.create_index("ix_normalization_errors_source_id", "normalization_errors", ["source_id"])
    op.create_index("ix_normalization_errors_status", "normalization_errors", ["status"])
    op.create_index("ix_normalization_errors_status_created_at", "normalization_errors", ["status", "created_at"])

    op.create_table(
        "sync_states",
        uuid_pk(),
        sa.Column("component_name", sa.String(length=120), nullable=False),
        sa.Column("component_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        created_at(),
        updated_at(),
        sa.ForeignKeyConstraint(["last_run_id"], ["ingestion_runs.id"], name=op.f("fk_sync_states_last_run_id_ingestion_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_states")),
        sa.UniqueConstraint("component_name", name=op.f("uq_sync_states_component_name")),
    )
    op.create_index("ix_sync_states_component_name", "sync_states", ["component_name"])


def downgrade() -> None:
    op.drop_index("ix_sync_states_component_name", table_name="sync_states")
    op.drop_table("sync_states")
    op.drop_index("ix_normalization_errors_status_created_at", table_name="normalization_errors")
    op.drop_index("ix_normalization_errors_status", table_name="normalization_errors")
    op.drop_index("ix_normalization_errors_source_id", table_name="normalization_errors")
    op.drop_index("ix_normalization_errors_raw_payload_id", table_name="normalization_errors")
    op.drop_index("ix_normalization_errors_ingestion_run_id", table_name="normalization_errors")
    op.drop_index("ix_normalization_errors_error_type", table_name="normalization_errors")
    op.drop_index("ix_normalization_errors_created_at", table_name="normalization_errors")
    op.drop_table("normalization_errors")
    op.drop_index("ix_index_bars_trading_date", table_name="index_bars")
    op.drop_index("ix_index_bars_index_timeframe_timestamp", table_name="index_bars")
    op.drop_table("index_bars")
    op.drop_table("latest_index_values")
    op.drop_index("ix_price_bars_trading_date", table_name="price_bars")
    op.drop_index("ix_price_bars_instrument_timeframe_timestamp", table_name="price_bars")
    op.drop_index("ix_price_bars_data_quality_status", table_name="price_bars")
    op.drop_table("price_bars")
    op.drop_index("ix_latest_prices_trading_date", table_name="latest_prices")
    op.drop_index("ix_latest_prices_price_timestamp", table_name="latest_prices")
    op.drop_index("ix_latest_prices_instrument_id", table_name="latest_prices")
    op.drop_index("ix_latest_prices_data_quality_status", table_name="latest_prices")
    op.drop_table("latest_prices")
    op.drop_index("ix_market_indices_exchange_symbol", table_name="market_indices")
    op.drop_table("market_indices")
    op.drop_index("ix_instruments_symbol", table_name="instruments")
    op.drop_index("ix_instruments_isin", table_name="instruments")
    op.drop_index("ix_instruments_is_active", table_name="instruments")
    op.drop_index("ix_instruments_exchange_symbol", table_name="instruments")
    op.drop_index("ix_instruments_exchange_isin", table_name="instruments")
    op.drop_index("ix_instruments_exchange_id", table_name="instruments")
    op.drop_index("ix_instruments_company_id", table_name="instruments")
    op.drop_table("instruments")
    op.drop_index("ix_companies_sector_id", table_name="companies")
    op.drop_index("ix_companies_name", table_name="companies")
    op.drop_index("ix_companies_is_active", table_name="companies")
    op.drop_table("companies")
    op.drop_index("ix_sectors_source_id", table_name="sectors")
    op.drop_table("sectors")
    op.drop_index("ix_raw_payloads_status", table_name="raw_payloads")
    op.drop_index("ix_raw_payloads_source_id", table_name="raw_payloads")
    op.drop_index("ix_raw_payloads_payload_hash", table_name="raw_payloads")
    op.drop_index("ix_raw_payloads_ingestion_run_id", table_name="raw_payloads")
    op.drop_index("ix_raw_payloads_collected_at", table_name="raw_payloads")
    op.drop_table("raw_payloads")
    op.drop_index("ix_ingestion_runs_status", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_started_at", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_source_started_at", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_source_id", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_collector_name", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    op.drop_table("exchanges")
    op.drop_table("data_sources")

