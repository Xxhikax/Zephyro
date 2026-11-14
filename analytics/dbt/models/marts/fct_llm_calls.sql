{{
    config(
        materialized='incremental',
        unique_key='call_id',
        incremental_strategy='delete+insert'
    )
}}

-- One row per LLM call, enriched with the price list so the recorded cost can be
-- audited (`cost_drift_usd`). Incremental: each run only processes calls newer
-- than the latest one already in the table.

with calls as (
    select * from {{ ref('stg_llm_calls') }}
    {% if is_incremental() %}
    where called_at > (select max(called_at) from {{ this }})
    {% endif %}
),

prices as (
    select * from {{ ref('model_prices') }}
)

select
    c.call_id,
    c.called_at,
    c.call_date,
    c.provider,
    c.model,
    c.provider || '/' || c.model                                    as model_key,
    c.tokens_in,
    c.tokens_out,
    c.tokens_in + c.tokens_out                                      as total_tokens,
    c.latency_ms,
    c.is_success,
    c.error_type,
    c.cost_usd                                                      as recorded_cost_usd,
    p.provider is not null                                          as is_priced,
    (c.tokens_in * p.input_per_million
        + c.tokens_out * p.output_per_million) / 1e6                as expected_cost_usd,
    c.cost_usd - (c.tokens_in * p.input_per_million
        + c.tokens_out * p.output_per_million) / 1e6                as cost_drift_usd
from calls c
left join prices p
    on p.provider = c.provider and p.model = c.model
