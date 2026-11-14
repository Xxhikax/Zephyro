-- Conversation activity per day: volume, sessions and what the memory keeps.

select
    turn_date,
    count(*)                                                       as turns,
    count(distinct session_key)                                    as sessions,
    quantile_cont(elapsed_s, 0.50)                                 as elapsed_p50_s,
    round(avg(is_saved_to_memory::int), 4)                         as saved_to_memory_rate,
    round(avg((importance_level = 'critical')::int), 4)            as critical_share,
    round(avg(input_chars), 1)                                     as avg_input_chars
from {{ ref('stg_turns') }}
group by turn_date
