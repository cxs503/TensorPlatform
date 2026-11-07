-- 001_saturation_pulse.sql -- idempotent DDL for the saturation pulse sink.
--
-- DISCIPLINE (IN15-O1): this is the *only* schema action of the mission.
-- The statement is written to be a no-op when the table already exists, and it
-- reproduces the deployed column set exactly:
--     ts | online | busy | held | released | note
-- No existing table is touched, no column of an existing table is altered.
--
-- Every statement is CREATE ... IF NOT EXISTS -> safe to run repeatedly
-- (``python3 -m tools.pulse.saturation_pulse --ensure-schema``).

CREATE TABLE IF NOT EXISTS saturation_pulse (
    ts        timestamptz DEFAULT now(),
    online    integer,
    busy      integer,
    held      integer,
    released  text,
    note      text
);

-- Trend queries read "last N samples"; the index makes that a bounded scan.
CREATE INDEX IF NOT EXISTS idx_saturation_pulse_ts ON saturation_pulse (ts DESC);