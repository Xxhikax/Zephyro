-- How each kind of request performs, per channel: the table to look at before
-- deciding what to optimize next.

select
    intent,
    channel,
    count(*)                                                          as requests,
    round(avg(is_success::int), 4)                                    as success_rate,
    quantile_cont(duration_ms, 0.50)                                  as duration_p50_ms,
    quantile_cont(duration_ms, 0.95)                                  as duration_p95_ms,
    round(avg(llm_time_share), 4)                                     as avg_llm_time_share,
    round(avg(has_capability_mismatch::int), 4)                       as capability_mismatch_rate,
    round(avg((user_feedback = 'negative')::int), 4)                  as negative_feedback_rate,
    sum(total_cost_usd) / count(*)                                    as cost_per_request_usd
from {{ ref('fct_requests') }}
group by all
