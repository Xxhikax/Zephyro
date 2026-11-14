with source as (
    select * from {{ source('raw', 'request_steps') }}
),

deduped as (
    select *, row_number() over (partition by request_id, step_index order by _loaded_at desc) as _rn
    from source
)

select
    request_id || '-' || step_index    as step_id,
    request_id,
    step_index,
    skill_name,
    coalesce(success, false)           as is_success,
    duration_ms,
    provider,
    model,
    provider is not null               as is_llm_step,
    coalesce(tokens_in, 0)             as tokens_in,
    coalesce(tokens_out, 0)            as tokens_out,
    coalesce(cost_usd, 0)              as cost_usd,
    error_type
from deduped
where _rn = 1
