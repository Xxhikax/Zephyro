{{ config(severity='warn') }}
-- A call whose logged cost differs from the price list by more than 1% (and a
-- micro-dollar) means the logger used stale prices or the price list is outdated.
-- Warn, not error: historical calls legitimately keep the price of their day.
select call_id, model_key, recorded_cost_usd, expected_cost_usd, cost_drift_usd
from {{ ref('fct_llm_calls') }}
where is_priced
  and is_success
  and abs(cost_drift_usd) > greatest(1e-6, 0.01 * expected_cost_usd)
