-- Name saved evaluation records so workflow outputs remain identifiable.
ALTER TABLE model_evaluation_results
    ADD COLUMN IF NOT EXISTS name VARCHAR(255);
