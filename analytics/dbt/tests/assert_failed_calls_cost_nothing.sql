-- A failed call that still carries tokens or cost would inflate the spend marts.
select call_id, recorded_cost_usd, total_tokens
from {{ ref('fct_llm_calls') }}
where not is_success and (recorded_cost_usd > 0 or total_tokens > 0)
