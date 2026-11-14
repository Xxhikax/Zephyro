-- Daily reliability, latency and spend per model. Latency percentiles only count
-- successful calls: a timeout's latency measures the timeout, not the model.

select
    call_date,
    provider,
    model,
    count(*)                                                        as calls,
    count(*) filter (where not is_success)                          as failed_calls,
    round(count(*) filter (where not is_success) / count(*), 4)     as error_rate,
    quantile_cont(latency_ms, 0.50) filter (where is_success)       as latency_p50_ms,
    quantile_cont(latency_ms, 0.95) filter (where is_success)       as latency_p95_ms,
    sum(tokens_in)                                                  as tokens_in,
    sum(tokens_out)                                                 as tokens_out,
    sum(recorded_cost_usd)                                          as cost_usd,
    sum(recorded_cost_usd) / nullif(count(*) filter (where is_success), 0) as cost_per_success_usd
from {{ ref('fct_llm_calls') }}
group by all
