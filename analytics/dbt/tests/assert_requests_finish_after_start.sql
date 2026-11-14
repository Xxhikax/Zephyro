-- A request that finishes before it starts means broken clocks or a bad join.
select request_id, started_at, finished_at
from {{ ref('stg_requests') }}
where finished_at < started_at
