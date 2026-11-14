-- One row per request, with its steps rolled up.

with requests as (
    select * from {{ ref('stg_requests') }}
),

steps as (
    select
        request_id,
        count(*)                                         as n_steps,
        count(*) filter (where not is_success)           as n_failed_steps,
        count(*) filter (where is_llm_step)              as n_llm_steps,
        string_agg(skill_name, ',' order by step_index)  as skill_path,
        sum(duration_ms) filter (where is_llm_step)      as llm_duration_ms
    from {{ ref('stg_request_steps') }}
    group by request_id
)

select
    r.request_id,
    r.started_at,
    r.request_date,
    r.intent,
    r.channel,
    r.is_success,
    r.duration_ms,
    r.complexity_score,
    r.has_capability_mismatch,
    r.user_feedback,
    r.total_cost_usd,
    r.total_tokens,
    r.input_chars,
    r.response_chars,
    coalesce(s.n_steps, 0)          as n_steps,
    coalesce(s.n_failed_steps, 0)   as n_failed_steps,
    coalesce(s.n_llm_steps, 0)      as n_llm_steps,
    s.skill_path,
    coalesce(s.llm_duration_ms, 0)  as llm_duration_ms,
    -- Share of the wall time spent waiting on a model (0 for requests with no LLM step).
    case when r.duration_ms > 0 then coalesce(s.llm_duration_ms, 0) / r.duration_ms end as llm_time_share
from requests r
left join steps s using (request_id)
