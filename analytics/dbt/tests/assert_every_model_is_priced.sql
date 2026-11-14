{{ config(severity='warn') }}
-- Calls to a model missing from seeds/model_prices.csv: their spend is invisible
-- to every cost audit until the price list is updated.
select model_key, count(*) as calls
from {{ ref('fct_llm_calls') }}
where not is_priced
group by model_key
