-- Introduce independent chat sessions under each branch timeline.

ALTER TABLE chat_logs
ADD COLUMN session_id VARCHAR(64) DEFAULT 'default_session';
