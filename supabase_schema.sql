-- Run this in Supabase SQL Editor to create all tables

-- Projects table
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Sessions table (tracks each file upload, belongs to a project)
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploading',
    error_message TEXT,
    total_companies INTEGER DEFAULT 0,
    names_done INTEGER DEFAULT 0,
    postings_done INTEGER DEFAULT 0,
    contacts_done INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_sessions_project ON sessions(project_id);

-- Companies table
CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    legal_name TEXT NOT NULL,
    known_names TEXT[] DEFAULT '{}',
    inn TEXT,
    kpp TEXT,
    ogrn TEXT,
    registration_date TEXT,
    address TEXT,
    region TEXT,
    website_url TEXT,
    revenue TEXT,
    employee_count TEXT,
    ceo_name TEXT,
    ceo_position TEXT,
    phone TEXT,
    email TEXT,
    main_activity TEXT,

    raw_data JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_companies_session ON companies(session_id);

-- Postings table (job vacancies from hh.ru)
CREATE TABLE postings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
    hh_id TEXT,
    search_term TEXT,
    title TEXT,
    employer_name TEXT,
    area_name TEXT,
    salary_from INTEGER,
    salary_to INTEGER,
    salary_currency TEXT,
    snippet_requirement TEXT,
    snippet_responsibility TEXT,
    url TEXT,
    published_at TEXT,
    raw_data JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_postings_session ON postings(session_id);
CREATE INDEX idx_postings_company ON postings(company_id);

-- Contacts table (from Hunter.io)
CREATE TABLE contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
    email TEXT,
    confidence INTEGER,
    first_name TEXT,
    last_name TEXT,
    position TEXT,
    position_raw TEXT,
    seniority TEXT,
    department TEXT,
    linkedin TEXT,
    phone_number TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_contacts_session ON contacts(session_id);
CREATE INDEX idx_contacts_company ON contacts(company_id);

-- News articles table (from Yandex Search API)
CREATE TABLE news_articles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
    article_url TEXT,
    search_term TEXT,
    title TEXT,
    source_name TEXT,
    snippet TEXT,
    published_at TEXT,
    raw_data JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_news_session ON news_articles(session_id);
CREATE INDEX idx_news_company ON news_articles(company_id);

-- Keyword groups belonging to a project
CREATE TABLE keyword_groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_keyword_groups_project ON keyword_groups(project_id);

-- Individual keywords belonging to a group
CREATE TABLE keywords (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id UUID REFERENCES keyword_groups(id) ON DELETE CASCADE,
    keyword TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_keywords_group ON keywords(group_id);

-- Migration: add new columns to existing tables
-- Run this if the tables already exist (skip if running fresh)
ALTER TABLE companies ADD COLUMN IF NOT EXISTS registration_date TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS region TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ceo_position TEXT;

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS names_done INTEGER DEFAULT 0;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS postings_done INTEGER DEFAULT 0;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS contacts_done INTEGER DEFAULT 0;

-- Migration: add projects table and link sessions
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);

-- Migration: add news_articles table and news_done counter
CREATE TABLE IF NOT EXISTS news_articles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
    article_url TEXT,
    search_term TEXT,
    title TEXT,
    source_name TEXT,
    snippet TEXT,
    published_at TEXT,
    raw_data JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_news_session ON news_articles(session_id);
CREATE INDEX IF NOT EXISTS idx_news_company ON news_articles(company_id);

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS news_done INTEGER DEFAULT 0;

-- Migration: contact enrichment feature
ALTER TABLE projects ADD COLUMN IF NOT EXISTS target_roles TEXT[] DEFAULT '{}';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS enrichment_done INTEGER DEFAULT 0;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'hunter';

-- Migration: per-session stage toggles
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS run_postings BOOLEAN DEFAULT TRUE;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS run_news BOOLEAN DEFAULT TRUE;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS run_contacts BOOLEAN DEFAULT TRUE;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS run_enrichment BOOLEAN DEFAULT TRUE;
