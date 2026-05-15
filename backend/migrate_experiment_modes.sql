-- Rename neutral experiment-mode labels for academic blind validation.
-- Run against backend/loop_research.db before the final MVP data collection.

BEGIN TRANSACTION;

UPDATE chat_logs
SET experiment_mode = 'mode_alpha'
WHERE experiment_mode = 'full_iacl';

UPDATE chat_logs
SET experiment_mode = 'mode_beta'
WHERE experiment_mode = 'static_prompt';

COMMIT;
