-- Global attribute catalog
CREATE TABLE IF NOT EXISTS public.attributes (
    attribute_id   BIGSERIAL PRIMARY KEY,
    attribute_name VARCHAR(200) NOT NULL UNIQUE,
    attribute_desc TEXT
);

-- Seed with defaults from main.py DEFAULT_ATTRIBUTES
INSERT INTO public.attributes (attribute_name, attribute_desc) VALUES
    ('Material', 'Actual material made, Feel, weight, breathability, structure'),
    ('Style',    'Occasion-fit or lifestyle relevance'),
    ('Fit',      'Body feel, cinch, looseness'),
    ('Details',  'Standout design features'),
    ('Sleeves',  'Length and weather-appropriateness'),
    ('Design',   'Observable colors and Print if visible')
ON CONFLICT (attribute_name) DO NOTHING;

-- Brand ↔ Attribute mapping
CREATE TABLE IF NOT EXISTS public.brand_attributes (
    id           BIGSERIAL PRIMARY KEY,
    brand_id     BIGINT NOT NULL REFERENCES public.brands(brand_id) ON DELETE CASCADE,
    attribute_id BIGINT NOT NULL REFERENCES public.attributes(attribute_id) ON DELETE CASCADE,
    sort_order   INT NOT NULL DEFAULT 0,
    UNIQUE(brand_id, attribute_id)
);
