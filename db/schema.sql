-- DMPC — PostgreSQL schema (data model backbone).
--
-- Design rules encoded here:
--   * UUID primary keys everywhere (defeats IDOR enumeration of submissions etc).
--   * Raw per-case scores are stored; DISPLAY/relative score is a *view* over the
--     whole field, recomputed by the scoring service — never a stored attribute.
--   * Test data is identified by (gen_version, seed); the input is regenerated
--     deterministically, never stored. Seeds for interim/final sets stay secret
--     until their round runs (enforced in the API, plus `reveal_at` here).
--   * "Opponents hidden until the contest ends" is enforced in the API by gating
--     on `contests.ends_at <= now()` — never by a UI flag. The columns here make
--     that gate cheap and auditable.
--
-- Apply:  psql "$DATABASE_URL" -f db/schema.sql

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS citext;     -- case-insensitive email

-- --------------------------------------------------------------------------
-- Enums
-- --------------------------------------------------------------------------
CREATE TYPE user_role         AS ENUM ('student', 'admin');
CREATE TYPE contest_status    AS ENUM ('draft', 'scheduled', 'live', 'ended', 'archived');
CREATE TYPE problem_kind      AS ENUM ('stepup', 'challenge');
CREATE TYPE testcase_set      AS ENUM ('sample', 'interim', 'final');
CREATE TYPE eval_round_type   AS ENUM ('provisional', 'interim', 'final');
CREATE TYPE eval_round_status AS ENUM ('pending', 'generating', 'judging', 'scoring', 'done', 'failed');
CREATE TYPE submission_state  AS ENUM (
    'queued', 'compiling', 'compile_error',
    'sample_running', 'sample_done', 'errored');
CREATE TYPE case_verdict      AS ENUM ('ok', 'tle', 'mle', 're', 'compile_error', 'illegal', 'internal');
CREATE TYPE notification_type AS ENUM ('grading_done', 'round_published', 'contest_ended', 'system');

-- --------------------------------------------------------------------------
-- Users  (domain policy — @dimigo.hs.kr plus any ALLOW_EMAILS exceptions — is enforced
-- SERVER-SIDE on the verified OAuth id-token, see app/oidc.py. The DB constraint is a
-- format-only sanity check so the configurable allowlist isn't pinned in the schema.)
-- --------------------------------------------------------------------------
CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         citext UNIQUE NOT NULL CHECK (email LIKE '%@%.%'),
    google_sub    text UNIQUE,                 -- OIDC subject; null for magic-link
    display_name  text NOT NULL,               -- name from Google (reference)
    nickname      citext UNIQUE,               -- chosen on first login; case-insensitive unique
    role          user_role NOT NULL DEFAULT 'student',
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz,
    is_disabled   boolean NOT NULL DEFAULT false
);

-- Server-side session store. The cookie holds a high-entropy secret; only its
-- sha256 is stored here, so a DB read alone cannot reconstruct a live cookie.
CREATE TABLE sessions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),  -- internal id (not the bearer)
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_sha256  text UNIQUE NOT NULL,         -- sha256 of the cookie secret
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    ip_hash       text,                         -- hashed; for abuse/multi-account signals
    user_agent    text
);
CREATE INDEX ON sessions (user_id);
CREATE INDEX ON sessions (expires_at);

-- --------------------------------------------------------------------------
-- Contests & problems
-- --------------------------------------------------------------------------
CREATE TABLE contests (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title           text NOT NULL,
    status          contest_status NOT NULL DEFAULT 'draft',
    starts_at       timestamptz NOT NULL,
    ends_at         timestamptz NOT NULL,      -- the reveal gate hinges on this
    registration_opens_at timestamptz,
    -- each PART is scored out of 1,000,000; the final total is the 2:8 weighted blend
    -- (judge/scoring.py weighted_total), so the two budgets are each 1,000,000 (not a sum split).
    stepup_budget   int NOT NULL DEFAULT 1000000,
    challenge_budget int NOT NULL DEFAULT 1000000,
    created_by      uuid REFERENCES users(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at)
);

CREATE TABLE problems (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contest_id      uuid NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
    kind            problem_kind NOT NULL,
    problem_key     text NOT NULL,             -- problems/<key>/problem.py (generator+checker)
    title           text NOT NULL,
    statement_md    text NOT NULL DEFAULT '',
    -- execution limits (NYPC: 2s / 1024MB; per-language multipliers applied by the grader)
    time_limit_ms   int NOT NULL DEFAULT 2000,
    memory_limit_mb int NOT NULL DEFAULT 1024,
    -- generator / checker pinned versions (reproducibility + tamper-evidence)
    gen_version     text NOT NULL DEFAULT 'v1',
    checker_version text NOT NULL DEFAULT 'v1',
    simulator_key   text,                      -- which client-side sim module to load
    -- stepup: { "cost_ref": <number> }   challenge: { "seed_range": [lo,hi], "params": {...} }
    scoring_config  jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON problems (contest_id);

-- Test identity = (problem, gen_version, seed). Input is NOT stored; it is
-- regenerated by the pinned generator. Seeds in interim/final sets are secret
-- until reveal_at (the API also blocks reads before then).
CREATE TABLE test_seeds (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    problem_id   uuid NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    set          testcase_set NOT NULL,
    seed         bigint NOT NULL,
    gen_version  text NOT NULL,
    params_hash  text,                         -- hash of derived params, for audit
    reveal_at    timestamptz,                  -- null = never (final), else publish time
    UNIQUE (problem_id, set, seed, gen_version)
);
CREATE INDEX ON test_seeds (problem_id, set);

-- --------------------------------------------------------------------------
-- Registrations (RSVP roster)
-- A user opts in to a contest; the roster is the participant count shown on the
-- contest page. Submissions are NOT hard-gated on this (open participation), so a
-- registered user who never submits simply scores 0.
-- --------------------------------------------------------------------------
CREATE TABLE registrations (
    contest_id   uuid NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (contest_id, user_id)
);
CREATE INDEX ON registrations (contest_id);

-- --------------------------------------------------------------------------
-- Submissions  (append-only; the LATEST per (user,problem) drives scoring)
-- --------------------------------------------------------------------------
CREATE TABLE submissions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    problem_id    uuid NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    language_id   text NOT NULL,               -- judge/languages.py id
    source_key    text,                         -- object-storage key (NULL in free-tier inline mode)
    source_text   text,                         -- free-tier: Challenge source stored inline in PG
    source_sha256 text NOT NULL,                -- for identical-code / plagiarism checks
    code_bytes    int NOT NULL,                 -- source size (<= 1MB enforced at submit)
    data_bin      bytea,                         -- optional uploaded data.bin (<=10MB); placed in the box, read via file I/O
    data_sha256   text,
    data_bytes    int,
    state         submission_state NOT NULL DEFAULT 'queued',
    claimed_at    timestamptz,                    -- lease: set when a worker claims; used to recover crashed grades
    attempts      int NOT NULL DEFAULT 0,         -- claim/grade attempts; caps re-queue of infra-flaked (INTERNAL) runs
    sample_score_sum int,                       -- shown in the "grading done" toast
    sample_results jsonb,                        -- per-sample {seed,cost,valid,verdict,runtime_ms} for the detail view
    compile_log   text,                          -- compiler stderr on compile_error (shown in the detail view)
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON submissions (problem_id, user_id, created_at DESC);
CREATE INDEX ON submissions (source_sha256);   -- duplicate-code detection
-- Recovery sweep: find rows whose worker died mid-grade (stuck in an in-flight state).
CREATE INDEX ON submissions (state, claimed_at);

-- Step Up submissions = an OUTPUT for one mission (test case). No code, no
-- sandbox: graded instantly by the trusted checker. The user's Step Up score for
-- a problem = sum of their best score per mission_seed.
CREATE TABLE stepup_submissions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    problem_id    uuid NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mission_seed  bigint NOT NULL,
    output_text   text NOT NULL,
    cost          double precision,            -- null = invalid output (0 score)
    valid         boolean NOT NULL,
    score         int NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON stepup_submissions (problem_id, user_id, mission_seed, created_at DESC);
-- Round driver snapshots best-per-mission for ALL users of a problem AS OF a cutoff.
CREATE INDEX ON stepup_submissions (problem_id, created_at);

-- --------------------------------------------------------------------------
-- Evaluation rounds  (provisional@submit, interim@09/18 KST, final@close)
-- --------------------------------------------------------------------------
CREATE TABLE evaluation_rounds (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contest_id    uuid NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
    type          eval_round_type NOT NULL,
    -- deterministic idempotency key: same (contest,date,slot) => same round, so a
    -- crashed/retried cron fire is a no-op rather than a double-count.
    idem_key      text NOT NULL,
    scheduled_at  timestamptz NOT NULL,         -- the immutable AS-OF cutoff for this round
    status        eval_round_status NOT NULL DEFAULT 'pending',
    claimed_at    timestamptz,                  -- lease: set when a grader claims; recover if it crashes mid-grade
    claimed_by    uuid,                          -- owner token; terminal writes guard on it so a stolen lease can't publish
    attempts      int NOT NULL DEFAULT 0,        -- grade attempts (caps re-dispatch of a wedged round)
    error         text,                          -- failure reason when status='failed' (operator triage)
    published_at  timestamptz,                  -- when results became visible to users
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (contest_id, idem_key)
);
-- standings reveal selects the latest type='final' done round; scheduler scans due rounds.
CREATE INDEX ON evaluation_rounds (contest_id, type, status);
CREATE INDEX ON evaluation_rounds (status, scheduled_at);
-- At most ONE final round per contest (the published leaderboard depends on it).
CREATE UNIQUE INDEX ON evaluation_rounds (contest_id) WHERE type = 'final';

-- Raw per-case execution result. raw_cost + verdict are stored; the relative
-- per-case score is computed by the scoring service over the whole field.
CREATE TABLE case_results (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id      uuid NOT NULL REFERENCES evaluation_rounds(id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    problem_id    uuid NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    seed          bigint NOT NULL,
    verdict       case_verdict NOT NULL,
    raw_cost      double precision,             -- null when invalid; minimization
    runtime_ms    int,
    memory_kb     int,
    -- stepup absolute points OR challenge per-case relative score (0..1e6),
    -- written by the scoring pass; nullable until scored.
    case_score    int,
    case_rank     int,                          -- per-case rank (shown in "interim eval")
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (round_id, submission_id, seed)
);
CREATE INDEX ON case_results (round_id, problem_id, seed);
CREATE INDEX ON case_results (user_id, round_id);

-- Per-round standings snapshot (recomputed; never trusted from the client).
CREATE TABLE standings (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id      uuid NOT NULL REFERENCES evaluation_rounds(id) ON DELETE CASCADE,
    contest_id    uuid NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stepup_score   int NOT NULL DEFAULT 0,
    challenge_score int NOT NULL DEFAULT 0,
    total_score    int NOT NULL DEFAULT 0,
    rank           int,
    computed_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (round_id, user_id)
);
CREATE INDEX ON standings (contest_id, round_id, rank);

-- --------------------------------------------------------------------------
-- Replays (top-3 AI writeups) & notifications
-- --------------------------------------------------------------------------
CREATE TABLE replays (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contest_id    uuid NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body_md       text NOT NULL DEFAULT '',     -- optional text note; rendered sanitized
    pdf           bytea,                         -- optional PDF writeup (시상 보고서)
    pdf_name      text,
    is_shared     boolean NOT NULL DEFAULT false,
    moderated     boolean NOT NULL DEFAULT false,
    -- enforced in API: only a verified final top-3 may insert; visible to others
    -- only when is_shared AND moderated AND contest.status='ended'.
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (contest_id, user_id)
);

CREATE TABLE notifications (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type          notification_type NOT NULL,
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- e.g. {"sample_score_sum": 740123}
    read_at       timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON notifications (user_id, created_at DESC);

-- --------------------------------------------------------------------------
-- Audit log (auth events, access-denials, uploads, admin actions, grading)
-- No secrets/PII in here.
-- --------------------------------------------------------------------------
CREATE TABLE audit_log (
    id            bigserial PRIMARY KEY,
    at            timestamptz NOT NULL DEFAULT now(),
    actor_id      uuid REFERENCES users(id),
    action        text NOT NULL,
    object_type   text,
    object_id     uuid,
    ip_hash       text,
    detail        jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX ON audit_log (at);
CREATE INDEX ON audit_log (actor_id, at);
