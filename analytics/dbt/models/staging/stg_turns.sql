with source as (
    select * from {{ source('raw', 'turns') }}
),

deduped as (
    select *, row_number() over (partition by turn_id order by _loaded_at desc) as _rn
    from source
)

select
    turn_id,
    session_key,
    user_key,
    user_role,
    to_timestamp(turn_at_epoch)                as turn_at,
    cast(to_timestamp(turn_at_epoch) as date)  as turn_date,
    importance_level,
    importance_score,
    coalesce(saved_to_memory, false)           as is_saved_to_memory,
    coalesce(manually_marked, false)           as is_manually_marked,
    intent,
    complexity,
    elapsed_s,
    mode_used,
    input_chars,
    response_chars
from deduped
where _rn = 1
