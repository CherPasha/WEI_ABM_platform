-- ============================================================
-- ABM Platform — Complete Database Schema
-- Paste this entire file into the Supabase SQL Editor on a
-- fresh project. All tables, indexes, and constraints are
-- included. No migration steps needed for a new installation.
-- ============================================================


-- Projects
CREATE TABLE projects (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                        TEXT        NOT NULL,
    target_roles                TEXT[]      DEFAULT '{}',
    contact_scan_use_roles      BOOLEAN     NOT NULL DEFAULT true,
    contact_scan_keyword_only   BOOLEAN     NOT NULL DEFAULT false,
    created_at                  TIMESTAMPTZ DEFAULT now()
);


-- Contact scans (one per manual scan trigger, per project)
CREATE TABLE contact_scans (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status              TEXT        NOT NULL DEFAULT 'running',
    use_roles           BOOLEAN     NOT NULL,
    keyword_only        BOOLEAN     NOT NULL,
    total_companies     INTEGER     NOT NULL DEFAULT 0,
    hunter_done         INTEGER     NOT NULL DEFAULT 0,
    enrichment_done     INTEGER     NOT NULL DEFAULT 0,
    total_verification  INTEGER     NOT NULL DEFAULT 0,
    verification_done   INTEGER     NOT NULL DEFAULT 0,
    contacts_added      INTEGER     NOT NULL DEFAULT 0,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_contact_scans_project ON contact_scans(project_id);


-- Sessions (one per uploaded file, belongs to a project)
CREATE TABLE sessions (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       UUID        REFERENCES projects(id) ON DELETE CASCADE,
    filename         TEXT        NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'uploading',
    error_message    TEXT,
    total_companies  INTEGER     DEFAULT 0,
    names_done       INTEGER     DEFAULT 0,
    postings_done    INTEGER     DEFAULT 0,
    news_done        INTEGER     DEFAULT 0,
    contacts_done    INTEGER     DEFAULT 0,
    enrichment_done  INTEGER     DEFAULT 0,
    run_postings     BOOLEAN     DEFAULT TRUE,
    run_news         BOOLEAN     DEFAULT TRUE,
    run_contacts     BOOLEAN     DEFAULT TRUE,
    run_enrichment   BOOLEAN     DEFAULT TRUE,
    run_verification  BOOLEAN     DEFAULT TRUE,
    verification_done INTEGER     DEFAULT 0,
    total_verification INTEGER    DEFAULT 0,
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_sessions_project ON sessions(project_id);


-- Companies (parsed from the uploaded Excel/CSV file)
CREATE TABLE companies (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        UUID        REFERENCES sessions(id) ON DELETE CASCADE,
    legal_name        TEXT        NOT NULL,
    known_names       TEXT[]      DEFAULT '{}',
    inn               TEXT,
    kpp               TEXT,
    ogrn              TEXT,
    registration_date TEXT,
    address           TEXT,
    region            TEXT,
    website_url       TEXT,
    revenue           TEXT,
    employee_count    TEXT,
    ceo_name          TEXT,
    ceo_position      TEXT,
    phone             TEXT,
    email             TEXT,
    main_activity     TEXT,
    raw_data          JSONB,
    keyword_hit_count   INTEGER     NOT NULL DEFAULT 0,
    keyword_group_count INTEGER     NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_companies_session ON companies(session_id);


-- Postings (job vacancies from hh.ru)
CREATE TABLE postings (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              UUID        REFERENCES sessions(id) ON DELETE CASCADE,
    company_id              UUID        REFERENCES companies(id) ON DELETE CASCADE,
    hh_id                   TEXT,
    search_term             TEXT,
    title                   TEXT,
    employer_name           TEXT,
    area_name               TEXT,
    salary_from             INTEGER,
    salary_to               INTEGER,
    salary_currency         TEXT,
    snippet_requirement     TEXT,
    snippet_responsibility  TEXT,
    url                     TEXT,
    published_at            TEXT,
    raw_data                JSONB,
    created_at              TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_postings_session ON postings(session_id);
CREATE INDEX idx_postings_company ON postings(company_id);


-- Contacts (from Hunter.io)
CREATE TABLE contacts (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id       UUID        REFERENCES sessions(id) ON DELETE CASCADE,
    contact_scan_id  UUID        REFERENCES contact_scans(id) ON DELETE CASCADE,
    company_id       UUID        REFERENCES companies(id) ON DELETE CASCADE,
    email            TEXT,
    confidence       INTEGER,
    first_name       TEXT,
    last_name        TEXT,
    position         TEXT,
    position_raw     TEXT,
    seniority        TEXT,
    department       TEXT,
    linkedin         TEXT,
    phone_number     TEXT,
    source           TEXT        DEFAULT 'hunter',
    -- Email verification (Hunter.io Email Verifier API v2)
    email_status     TEXT,
    email_score      INTEGER,
    email_regexp     BOOLEAN,
    email_gibberish  BOOLEAN,
    email_disposable BOOLEAN,
    email_webmail    BOOLEAN,
    email_mx_records BOOLEAN,
    email_smtp_server BOOLEAN,
    email_smtp_check BOOLEAN,
    email_accept_all BOOLEAN,
    email_block      BOOLEAN,
    email_verified_at TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_contacts_session ON contacts(session_id);
CREATE INDEX idx_contacts_company ON contacts(company_id);
CREATE INDEX idx_contacts_scan ON contacts(contact_scan_id);


-- News articles (from Yandex Search API + Playwright scraper)
CREATE TABLE news_articles (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID        REFERENCES sessions(id) ON DELETE CASCADE,
    company_id   UUID        REFERENCES companies(id) ON DELETE CASCADE,
    article_url  TEXT,
    search_term  TEXT,
    title        TEXT,
    source_name  TEXT,
    snippet      TEXT,
    full_text    TEXT,
    published_at TEXT,
    raw_data     JSONB,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_news_session  ON news_articles(session_id);
CREATE INDEX idx_news_company  ON news_articles(company_id);


-- Keyword groups (belong to a project, used for scan analysis)
CREATE TABLE keyword_groups (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID        REFERENCES projects(id) ON DELETE CASCADE,
    name       TEXT        NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_keyword_groups_project ON keyword_groups(project_id);


-- Keywords (belong to a keyword group)
CREATE TABLE keywords (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id   UUID        REFERENCES keyword_groups(id) ON DELETE CASCADE,
    keyword    TEXT        NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_keywords_group ON keywords(group_id);


-- Stop words (project-level; publications containing any stop word
-- are excluded entirely from keyword scan counts)
CREATE TABLE stop_words (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID        REFERENCES projects(id) ON DELETE CASCADE,
    word       TEXT        NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX        idx_stop_words_project      ON stop_words(project_id);
CREATE UNIQUE INDEX idx_stop_words_project_word ON stop_words(project_id, lower(word));
