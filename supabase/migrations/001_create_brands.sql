-- ============================================================
-- Run this in: Supabase Dashboard → SQL Editor → New query
-- Project: https://eesrzpgmsrbhjeenfojq.supabase.co
-- ============================================================

-- Brands table
CREATE TABLE IF NOT EXISTS public.brands (
    brand_id  BIGSERIAL PRIMARY KEY,
    brand_code VARCHAR(50)  NOT NULL UNIQUE,
    brand_name VARCHAR(200) NOT NULL,
    brand_desc TEXT
);

-- Brand prompts table
CREATE TABLE IF NOT EXISTS public.brand_prompts (
    id           BIGSERIAL PRIMARY KEY,
    brand_id     BIGINT       NOT NULL REFERENCES public.brands(brand_id) ON DELETE CASCADE,
    brand_prompt TEXT         NOT NULL,
    is_active    BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Enforce only one active prompt per brand
CREATE UNIQUE INDEX IF NOT EXISTS brand_prompts_single_active_idx
    ON public.brand_prompts(brand_id)
    WHERE is_active = TRUE;
