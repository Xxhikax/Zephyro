with source as (
    select * from {{ source('raw', 'api_calls') }}
),

deduped as (
    -- The landing zone is append-only: keep the latest load of each call.
    select *, row_number() over (partition by call_id order by _loaded_at desc) as _rn
    from source
)

select
    call_id,
    to_timestamp(called_at_epoch)                as called_at,
    cast(to_timestamp(called_at_epoch) as date)  as call_date,
    provider,
    model,
    coalesce(tokens_in, 0)                       as tokens_in,
    coalesce(tokens_out, 0)                      as tokens_out,
    latency_ms,
    coalesce(cost_usd, 0)                        as cost_usd,
    coalesce(success, false)                     as is_success,
    error_type
from deduped
where _rn = 1
