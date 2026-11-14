with source as (
    select * from {{ source('raw', 'requests') }}
),

deduped as (
    select *, row_number() over (partition by request_id order by _loaded_at desc) as _rn
    from source
)

select
    request_id,
    to_timestamp(started_at_epoch)                as started_at,
    to_timestamp(finished_at_epoch)               as finished_at,
    cast(to_timestamp(started_at_epoch) as date)  as request_date,
    intent,
    channel,
    coalesce(success, false)                      as is_success,
    duration_ms,
    complexity_score,
    coalesce(capability_mismatch, false)          as has_capability_mismatch,
    user_feedback,
    coalesce(total_cost_usd, 0)                   as total_cost_usd,
    coalesce(total_tokens, 0)                     as total_tokens,
    user_key,
    input_chars,
    response_chars,
    step_count
from deduped
where _rn = 1
