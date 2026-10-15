import difflib
import json
import logging
import os
import re
import threading
import time
import traceback
import uuid
import tempfile

import requests
from flask import Flask, jsonify, render_template, request, send_file

from helper.attribute_generator import AttributeGenerator
from logger import LogHandler

try:
    from supabase import create_client as _supabase_create
    _SUPABASE_AVAILABLE = True
except ImportError:
    _SUPABASE_AVAILABLE = False

LogHandler('attribute-checker')
logger = logging.getLogger('attribute-checker')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB

# ---------------------------------------------------------------------------
# Supabase client (lazy-initialised on first use)
# ---------------------------------------------------------------------------

_SUPABASE_URL = 'https://eesrzpgmsrbhjeenfojq.supabase.co'
_sb_instance = None


def _sb():
    global _sb_instance
    if not _SUPABASE_AVAILABLE:
        raise RuntimeError('supabase package not installed. Run: pip install supabase')
    if _sb_instance is None:
        key = os.environ.get('SUPABASE_KEY', '')
        if not key:
            raise RuntimeError('SUPABASE_KEY environment variable is not set.')
        _sb_instance = _supabase_create(
            os.environ.get('SUPABASE_URL', _SUPABASE_URL), key
        )
    return _sb_instance


# ---------------------------------------------------------------------------
# Creatives DB lookup (via rgmc-gcp-api, backed by creative_prod)
# ---------------------------------------------------------------------------

CREATIVE_API_BASE_URL = os.environ.get(
    'CREATIVE_API_BASE_URL', 'https://rgmc-gcp-api-935246372408.asia-southeast1.run.app'
).rstrip('/')


class CreativeApiError(Exception):
    pass


def _creative_lookup(table_name, where_column, where_value):
    try:
        resp = requests.get(
            '{}/creative/by_table/value'.format(CREATIVE_API_BASE_URL),
            params={'table_name': table_name, 'where_column': where_column, 'where_value': where_value},
            timeout=20,
        )
    except requests.RequestException as exc:
        raise CreativeApiError('Could not reach the creatives database: {}'.format(exc))

    if resp.status_code != 200:
        detail = resp.text
        try:
            detail = resp.json().get('detail', detail)
        except ValueError:
            pass
        raise CreativeApiError('Creatives database error ({}): {}'.format(resp.status_code, detail))

    return resp.json().get('data', [])


def _creative_lookup_name(table_name, pk_column, pk_value, name_column='name'):
    if pk_value in (None, ''):
        return None
    try:
        rows = _creative_lookup(table_name, pk_column, pk_value)
    except CreativeApiError:
        logger.warning('Creative lookup failed for %s.%s=%s', table_name, pk_column, pk_value, exc_info=True)
        return None
    return rows[0].get(name_column) if rows else None


# ProductDesign.fabricCode is a supplier/PO-style code (e.g. "WT-674248-01") and has no
# relation to the Fabric master table's own fabricCode scheme (e.g. "FR0140") — confirmed
# against live data, and there's no fabricId FK column on ProductDesign either. The only
# usable link is a fuzzy text match between ProductDesign.fabricName and Fabric.name.
_FABRIC_CACHE = {'rows': None, 'fetched_at': 0.0}
_FABRIC_CACHE_TTL_SECONDS = 600
_FABRIC_FETCH_ROW_LIMIT = 5000
_FABRIC_MATCH_THRESHOLD = 0.6


def _normalize_fabric_name(raw):
    if not raw:
        return ''
    text = raw.split('|')[0].upper()
    text = re.sub(r'[^A-Z0-9/]+', ' ', text)
    text = re.sub(r'\s*/\s*', '/', text)
    return re.sub(r'\s+', ' ', text).strip()


def _get_fabric_rows():
    now = time.time()
    if _FABRIC_CACHE['rows'] is not None and (now - _FABRIC_CACHE['fetched_at']) < _FABRIC_CACHE_TTL_SECONDS:
        return _FABRIC_CACHE['rows']
    try:
        resp = requests.get(
            '{}/creative/by_table/latest'.format(CREATIVE_API_BASE_URL),
            params={
                'table_name': 'Fabric',
                'date_column': 'createDate',
                'number_of_rows': _FABRIC_FETCH_ROW_LIMIT,
            },
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json().get('data', [])
    except requests.RequestException:
        logger.warning('Fabric table fetch failed', exc_info=True)
        return _FABRIC_CACHE['rows'] or []
    _FABRIC_CACHE['rows'] = rows
    _FABRIC_CACHE['fetched_at'] = now
    return rows


def _fabric_name_lookup(raw_fabric_name):
    """Best-effort fuzzy match of ProductDesign.fabricName against Fabric.name.
    Falls back to the original value when nothing clears the similarity threshold."""
    normalized_raw = _normalize_fabric_name(raw_fabric_name)
    if not normalized_raw:
        return raw_fabric_name

    best_name, best_ratio = None, 0.0
    for fabric_row in _get_fabric_rows():
        candidate = fabric_row.get('name')
        normalized_candidate = _normalize_fabric_name(candidate)
        if not normalized_candidate:
            continue
        ratio = difflib.SequenceMatcher(None, normalized_raw, normalized_candidate).ratio()
        if ratio > best_ratio:
            best_name, best_ratio = candidate, ratio

    return best_name if best_ratio >= _FABRIC_MATCH_THRESHOLD else raw_fabric_name


JOBS: dict = {}

PROMPT_INSTRUCTION = (
    'Generate a complete e-commerce product listing for the attached photo(s) by following the '
    'structure, rules, and required inputs below exactly. Do not invent brand names, measurements, '
    'materials, or care instructions that are not supported by the photos or the information provided.'
)

PROMPT_TEMPLATE_DEFAULT = """## 1. Product Title
Generate an SEO-optimized product title using the following format:

BRAND + FEMININE STYLE NAME + KEY SILHOUETTE + KEY DESIGN DETAIL

Requirements:
- Write the product title in ALL CAPS.
- Include the brand name only if it is visible in the provided photos or supplied as {{BRAND_NAME}} below.
- Generate a unique, random feminine first name as the style name. Avoid repeating the same name across different products.
- Identify the correct garment type (for example A-LINE DRESS, SHEATH DRESS, COLLARED TOP), using the supplied Product Category when available.
- Include relevant silhouette and design details that improve searchability.
- The title must be at least 30 characters long.
- Use relevant SEO keywords naturally, without keyword stuffing.
- Do not invent a brand name or features that are not supported by the photos or the inputs below.

## 2. Short Product Introduction
Write a concise introduction based on the Description Type supplied below.

For PD — Feminine Style:
- Write 1-2 sentences.
- Use a feminine, refined tone.
- Convey the garment's silhouette, structure, and natural movement.
- Highlight its defining characteristic without excessive adjectives.

For AE — Structured and Polished Style:
- Write exactly 1 sentence.
- Emphasize clean lines, tailored details, and the garment's overall structure.
- Maintain a polished, professional tone.

General requirements:
- Keep the introduction concise and easy to scan.
- Mention the material only when it matches the supplied Material Composition.
- Avoid storytelling, generic occasion recommendations, and repetitive design descriptions.
- Do not invent details that cannot be verified from the photos or the supplied inputs.

## 3. Product Details
Present the product details as concise bullet points, one short sentence each. Label every bullet with the exact attribute name followed by a colon (for example "Silhouette & Length: ..."). Prioritize the following attributes, in this order:

{{ATTRIBUTES}}

Then add up to two more bullets using the exact supplied values below — phrase them as a clean sentence, but do not change the facts. If a value is "(not provided)", omit that bullet entirely instead of guessing:
- Material: {{MATERIAL}}
- Care Instructions: {{CARE_INSTRUCTIONS}}

Also factor in this additional product information, when supplied: {{ADDITIONAL_INFO}}

Requirements:
- Keep each bullet to one short, informative sentence.
- Focus on useful purchasing information rather than decorative language.
- Avoid repeating information already covered in the introduction.
- Do not assume a feature exists simply because it is common for that garment type.
- Omit an attribute bullet entirely if it is not visible or not supported by the photos.

## 4. Fit & Sizing Notes
Use this standard notice exactly, with no changes:

"Please refer to the size chart in the product photos for the best fit."

Do not invent measurements or recommend a specific size.

## 5. Care Instructions
If Care / Wash Instructions is "(not provided)", omit the Care Instructions bullet entirely. Otherwise, restate the supplied Care / Wash Instructions ({{CARE_INSTRUCTIONS}}) as a short, standardized sentence. Do not assume the fabric's care requirements based on appearance alone, and do not add washing instructions that were not supplied.

## 6. Customer Support
End with this standard line exactly, with no changes:

"Need help with sizing or product details? Message us — we're happy to help."

## 7. Product Tags
Generate a list of relevant product tags for marketplace search and categorization, based on garment type, silhouette and cut, neckline and sleeve style, length and fit, distinctive design features, material or fabric type, and style category.

Requirements:
- Present tags as a list.
- Use concise, searchable keywords.
- Avoid duplicate, irrelevant, or unsupported tags.
- Do not include colors as tags — color is already selectable as a separate product option.

## 8. Supplied Inputs For This Product
Use the following information exactly as given. Where a value is "(not provided)", rely only on what is clearly visible in the photos, or omit that detail rather than inventing it.

- Brand Name: {{BRAND_NAME}}
- Product Category: {{PRODUCT_CATEGORY}}
- Description Type: {{DESCRIPTION_TYPE}}
- Material Composition: {{MATERIAL}}
- Care / Wash Instructions: {{CARE_INSTRUCTIONS}}
- Additional Product Information: {{ADDITIONAL_INFO}}
- Product Photos: all attached photos show the same item from different angles. Analyze every angle to identify design details, construction, silhouette, closures, and functional features before writing the description.

If required information is missing, rely only on what is clearly supported by the photos rather than fabricating details.

## 9. Final Output Format
Generate the product listing in exactly this order, with no extra sections:

**PRODUCT TITLE**

[SEO-optimized title in ALL CAPS, at least 30 characters]

**SHORT INTRODUCTION**

[1-2 sentences for PD, or exactly 1 sentence for AE]

**PRODUCT DETAILS**
- [One labeled bullet per attribute listed in section 3]
- Material: [restated from the supplied Material Composition — omit this bullet if not supplied]
- Care Instructions: [restated from the supplied Care / Wash Instructions — omit this bullet if not supplied]

**FIT & SIZING**

Please refer to the size chart in the product photos for the best fit.

**CUSTOMER SUPPORT**

Need help with sizing or product details? Message us — we're happy to help.

**PRODUCT TAGS**
- [Relevant tag]
- [Relevant tag]
- [Relevant tag]

## 10. Final Quality Checklist
Before returning the result, verify that:
- The title is in ALL CAPS, at least 30 characters, and uses relevant SEO keywords.
- The style name is feminine, varied, and appropriate for the garment.
- The introduction follows the sentence count for the supplied Description Type.
- The description is concise, polished, and easy to scan.
- Product details are factual, practical, non-repetitive, and each bullet is labeled with its attribute name.
- The Material and Care Instructions bullets match the supplied values, or are omitted entirely when not supplied.
- All attached photos have been considered.
- No unsupported features, measurements, materials, or care instructions have been invented.
- Tags are relevant, concise, and non-duplicative.
- The output excludes any "Why You'll Love It" section, color lists, and generic occasion-based phrases."""

DEFAULT_ATTRIBUTES = [
    {'name': 'Silhouette & Length', 'description': 'Garment shape, cut, and length — e.g. A-line, fitted, relaxed, mini, midi, or maxi.'},
    {'name': 'Design Details',      'description': 'Distinctive features such as collars, necklines, sleeves, pleats, ruffles, seams, darts, or paneling.'},
    {'name': 'Closure',             'description': 'Type and placement of closures — e.g. zippers, buttons, hooks, or pull-on construction.'},
    {'name': 'Stretch & Fit',       'description': 'Whether the garment is stretchy or non-stretch, along with relevant fit characteristics.'},
    {'name': 'Functional Features', 'description': 'Pockets, lining, adjustable straps, removable belts, or included accessories, when applicable.'},
]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template(
        'index.html',
        prompt_instruction=PROMPT_INSTRUCTION,
        prompt_template=PROMPT_TEMPLATE_DEFAULT,
        default_attributes=json.dumps(DEFAULT_ATTRIBUTES),
        script_version=os.environ.get('SCRIPT_VERSION', '2'),
        gpt_version=os.environ.get('GPT_VERSION', 'gpt-4.1'),
    )


DEFAULT_GROUP_KEY = '__default__'


def _group_files(valid_files, group_ids):
    """Combine files sharing a truthy group id into one group (one description, one
    settings/prompt-template slot); files with no group id each become their own
    singleton group using the DEFAULT_GROUP_KEY settings slot."""
    groups = []
    group_index = {}
    for (filename, data), gid in zip(valid_files, group_ids):
        key = gid or DEFAULT_GROUP_KEY
        if gid:
            if gid not in group_index:
                group_index[gid] = len(groups)
                groups.append({'files': [], 'group_key': key})
            groups[group_index[gid]]['files'].append((filename, data))
        else:
            groups.append({'files': [(filename, data)], 'group_key': key})
    return groups


@app.route('/process', methods=['POST'])
def process():
    files = request.files.getlist('images')

    raw_formats        = request.form.get('output_formats', '["xls"]')
    raw_attrs          = request.form.get('attributes', '[]')
    raw_group_map      = request.form.get('group_map', '[]')
    raw_group_settings = request.form.get('group_settings', '{}')

    try:
        output_formats = json.loads(raw_formats)
        attributes     = json.loads(raw_attrs)
        group_map      = json.loads(raw_group_map)
        group_settings = json.loads(raw_group_settings)
    except json.JSONDecodeError as exc:
        return jsonify({'error': 'Invalid JSON in form fields: {}'.format(exc)}), 400

    valid_files = []
    group_ids   = []
    for i, f in enumerate(files):
        if not f.filename:
            continue
        valid_files.append((f.filename, f.read()))
        group_ids.append(group_map[i] if i < len(group_map) else None)

    if not valid_files:
        return jsonify({'error': 'No files uploaded.'}), 400

    if not output_formats:
        return jsonify({'error': 'Select at least one output format.'}), 400

    settings = {
        'attributes':         attributes,
        'prompt_instruction': PROMPT_INSTRUCTION,
        'output_formats':     output_formats,
        'output_filename':    request.form.get('output_filename', '').strip(),
        'group_settings':     group_settings,
        'default_group_key':  DEFAULT_GROUP_KEY,
        'default_prompt_template': PROMPT_TEMPLATE_DEFAULT,
    }

    groups = _group_files(valid_files, group_ids)

    job_id     = str(uuid.uuid4())
    output_dir = tempfile.mkdtemp()

    JOBS[job_id] = {
        'status':            'queued',
        'progress':          0,
        'total':             len(groups),
        'message':           'Queued…',
        'current_file':      '',
        'zip_path':          '',
        'zip_download_name': 'attribute-checker-results.zip',
        'log_text':          '',
    }

    t = threading.Thread(
        target=_run_job,
        args=(job_id, groups, settings, output_dir),
        daemon=True,
    )
    t.start()

    return jsonify({'job_id': job_id})


@app.route('/status/<job_id>')
def job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found.'}), 404
    return jsonify({
        'status':       job['status'],
        'progress':     job['progress'],
        'total':        job['total'],
        'message':      job['message'],
        'current_file': job.get('current_file', ''),
    })


@app.route('/download/<job_id>')
def download_result(job_id):
    job = JOBS.get(job_id)
    if not job or job['status'] != 'done':
        return jsonify({'error': 'Result not ready.'}), 404
    return send_file(
        job['zip_path'],
        as_attachment=True,
        download_name=job.get('zip_download_name', 'attribute-checker-results.zip'),
        mimetype='application/zip',
    )


@app.route('/api/jobs/<job_id>/email-log', methods=['POST'])
def email_job_log(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found.'}), 404
    sent, err = _send_job_log_email(job_id, job)
    if not sent:
        return jsonify({'sent': False, 'error': err}), 502
    return jsonify({'sent': True})


# ---------------------------------------------------------------------------
# Configuration — page
# ---------------------------------------------------------------------------

@app.route('/config')
def config_page():
    return render_template('config.html')


# ---------------------------------------------------------------------------
# Configuration — Brands API
# ---------------------------------------------------------------------------

@app.route('/api/brands', methods=['GET'])
def api_brands_list():
    try:
        res = _sb().table('brands').select('*').order('brand_name').execute()
        return jsonify(res.data)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/brands', methods=['POST'])
def api_brands_create():
    data = request.get_json(force=True) or {}
    code = (data.get('brand_code') or '').strip()
    name = (data.get('brand_name') or '').strip()
    desc = (data.get('brand_desc') or '').strip()
    if not code or not name:
        return jsonify({'error': 'brand_code and brand_name are required.'}), 400
    try:
        res = _sb().table('brands').insert(
            {'brand_code': code, 'brand_name': name, 'brand_desc': desc}
        ).execute()
        return jsonify(res.data[0] if res.data else {}), 201
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/brands/<int:brand_id>', methods=['PUT'])
def api_brands_update(brand_id):
    data = request.get_json(force=True) or {}
    code = (data.get('brand_code') or '').strip()
    name = (data.get('brand_name') or '').strip()
    desc = (data.get('brand_desc') or '').strip()
    if not code or not name:
        return jsonify({'error': 'brand_code and brand_name are required.'}), 400
    try:
        res = _sb().table('brands').update(
            {'brand_code': code, 'brand_name': name, 'brand_desc': desc}
        ).eq('brand_id', brand_id).execute()
        return jsonify(res.data[0] if res.data else {})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/brands/<int:brand_id>', methods=['DELETE'])
def api_brands_delete(brand_id):
    try:
        _sb().table('brands').delete().eq('brand_id', brand_id).execute()
        return jsonify({'ok': True})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


# ---------------------------------------------------------------------------
# Configuration — Brand Prompts API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Attributes API
# ---------------------------------------------------------------------------

@app.route('/api/attributes', methods=['GET'])
def api_attributes_list():
    try:
        res = _sb().table('attributes').select('*').order('attribute_id').execute()
        return jsonify(res.data)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/attributes', methods=['POST'])
def api_attributes_create():
    data = request.get_json(force=True) or {}
    name = (data.get('attribute_name') or '').strip()
    desc = (data.get('attribute_desc') or '').strip()
    if not name:
        return jsonify({'error': 'attribute_name is required.'}), 400
    try:
        res = _sb().table('attributes').insert(
            {'attribute_name': name, 'attribute_desc': desc}
        ).execute()
        return jsonify(res.data[0] if res.data else {}), 201
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/attributes/<int:attr_id>', methods=['PUT'])
def api_attributes_update(attr_id):
    data = request.get_json(force=True) or {}
    name = (data.get('attribute_name') or '').strip()
    desc = (data.get('attribute_desc') or '').strip()
    if not name:
        return jsonify({'error': 'attribute_name is required.'}), 400
    try:
        res = _sb().table('attributes').update(
            {'attribute_name': name, 'attribute_desc': desc}
        ).eq('attribute_id', attr_id).execute()
        return jsonify(res.data[0] if res.data else {})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/attributes/<int:attr_id>', methods=['DELETE'])
def api_attributes_delete(attr_id):
    try:
        _sb().table('attributes').delete().eq('attribute_id', attr_id).execute()
        return jsonify({'ok': True})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


# ---------------------------------------------------------------------------
# Brand–Attribute Mapping API
# ---------------------------------------------------------------------------

@app.route('/api/brands/<int:brand_id>/attributes', methods=['GET'])
def api_brand_attributes_get(brand_id):
    try:
        res = (
            _sb().table('brand_attributes')
            .select('sort_order,attributes(attribute_id,attribute_name,attribute_desc)')
            .eq('brand_id', brand_id)
            .order('sort_order')
            .execute()
        )
        items = [
            {
                'attribute_id':   row['attributes']['attribute_id'],
                'attribute_name': row['attributes']['attribute_name'],
                'attribute_desc': row['attributes'].get('attribute_desc') or '',
                'sort_order':     row['sort_order'],
            }
            for row in res.data if row.get('attributes')
        ]
        return jsonify(items)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/brands/<int:brand_id>/attributes', methods=['POST'])
def api_brand_attributes_set(brand_id):
    data = request.get_json(force=True) or {}
    attr_ids = data.get('attribute_ids', [])
    try:
        client = _sb()
        client.table('brand_attributes').delete().eq('brand_id', brand_id).execute()
        if attr_ids:
            rows = [
                {'brand_id': brand_id, 'attribute_id': int(aid), 'sort_order': i}
                for i, aid in enumerate(attr_ids)
            ]
            client.table('brand_attributes').insert(rows).execute()
        return jsonify({'ok': True})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


# ---------------------------------------------------------------------------
# Configuration — active prompt (used by main page brand prompt source)
# ---------------------------------------------------------------------------

@app.route('/api/brands/<int:brand_id>/active-prompt', methods=['GET'])
def api_brand_active_prompt(brand_id):
    try:
        res = (
            _sb().table('brand_prompts')
            .select('id,brand_prompt')
            .eq('brand_id', brand_id)
            .eq('is_active', True)
            .limit(1)
            .execute()
        )
        if not res.data:
            return jsonify({'brand_prompt': None})
        return jsonify(res.data[0])
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/brands/<int:brand_id>/prompts', methods=['GET'])
def api_brand_prompts_list(brand_id):
    try:
        res = (
            _sb().table('brand_prompts')
            .select('*')
            .eq('brand_id', brand_id)
            .order('created_at', desc=True)
            .execute()
        )
        return jsonify(res.data)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/brands/<int:brand_id>/prompts', methods=['POST'])
def api_brand_prompts_create(brand_id):
    data = request.get_json(force=True) or {}
    prompt_text = (data.get('brand_prompt') or '').strip()
    make_active = bool(data.get('is_active', False))
    if not prompt_text:
        return jsonify({'error': 'brand_prompt is required.'}), 400
    try:
        client = _sb()
        if make_active:
            client.table('brand_prompts').update({'is_active': False}).eq('brand_id', brand_id).execute()
        res = client.table('brand_prompts').insert({
            'brand_id':     brand_id,
            'brand_prompt': prompt_text,
            'is_active':    make_active,
        }).execute()
        return jsonify(res.data[0] if res.data else {}), 201
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/prompts/<int:prompt_id>/activate', methods=['POST'])
def api_prompt_activate(prompt_id):
    try:
        client = _sb()
        info = client.table('brand_prompts').select('brand_id').eq('id', prompt_id).single().execute()
        brand_id = info.data['brand_id']
        client.table('brand_prompts').update({'is_active': False}).eq('brand_id', brand_id).execute()
        client.table('brand_prompts').update({'is_active': True}).eq('id', prompt_id).execute()
        return jsonify({'ok': True})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/prompts/<int:prompt_id>', methods=['DELETE'])
def api_prompt_delete(prompt_id):
    try:
        _sb().table('brand_prompts').delete().eq('id', prompt_id).execute()
        return jsonify({'ok': True})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/creative/search')
def api_creative_search():
    # NOTE: creative_prod table names are PascalCase (ProductDesign, Brand, ...) but their
    # column names are camelCase (stockNumber, brandId, ...) — confirmed against the live API.
    stock_number = request.args.get('stock_number', '').strip()
    if not stock_number:
        return jsonify({'error': 'Stock number is required.'}), 400

    try:
        rows = _creative_lookup('ProductDesign', 'stockNumber', stock_number)
    except CreativeApiError as exc:
        logger.error('Creative search failed for stock number %s: %s', stock_number, exc)
        return jsonify({'error': str(exc)}), 502

    if not rows:
        return jsonify({'error': 'No product found for stock number "{}".'.format(stock_number)}), 404

    # If more than one design shares the stock number, show the most recently created one.
    row = max(rows, key=lambda r: r.get('productDesignId') or 0)
    product_design_id = row.get('productDesignId')
    design_status_code = row.get('designStatusCode')

    season_name, season_year = None, None
    season_id = row.get('seasonId')
    if season_id not in (None, ''):
        season_rows = []
        try:
            season_rows = _creative_lookup('Season', 'seasonId', season_id)
        except CreativeApiError:
            logger.warning('Season lookup failed for seasonId=%s', season_id, exc_info=True)
        if season_rows:
            season_name = season_rows[0].get('name')
            season_year = season_rows[0].get('year')

    images = []
    try:
        pic_rows = _creative_lookup('ProductDesignPicture', 'productDesignId', product_design_id)
        for p in pic_rows:
            image_data = p.get('imageData')
            if not image_data:
                continue
            images.append({
                'id':          p.get('id'),
                'data_uri':    'data:image/png;base64,{}'.format(image_data),
                'remark':      p.get('remark') or '',
                'create_date': p.get('createDate'),
            })
    except CreativeApiError:
        logger.warning('Could not load pictures for productDesignId=%s', product_design_id, exc_info=True)

    return jsonify({
        'product_design_id':   product_design_id,
        'stock_number':        row.get('stockNumber'),
        'product_design_code': row.get('productDesignCode'),
        'description':         row.get('description'),
        'brand':               _creative_lookup_name('Brand', 'brandId', row.get('brandId')),
        'category':            _creative_lookup_name('Category', 'categoryId', row.get('categoryId')),
        'item_group':          _creative_lookup_name('ItemGroup', 'itemGroupId', row.get('itemGroupId')),
        'designer':            _creative_lookup_name('Designer', 'designerId', row.get('designerId')),
        'season':              season_name,
        'year':                season_year,
        'design_status':       _creative_lookup_name('DesignStatus', 'designStatusCode', design_status_code),
        'design_status_code':  design_status_code,
        'fabric_code':         row.get('fabricCode'),
        'fabric_name':         _fabric_name_lookup(row.get('fabricName')),
        'supplier':            _creative_lookup_name('Supplier', 'supplierId', row.get('supplierId')),
        'supplier_stock_no':   row.get('supplierStockNo'),
        'remark':              row.get('remark'),
        'images':              images,
    })


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------

def _send_job_log_email(job_id, job):
    """POST the job's log to rgmc-gcp-api's generic mail endpoint. Recipient is
    whatever that service's own MAIL_RECIPIENT env var is configured to — this app
    has no SMTP credentials of its own."""
    subject = 'Attribute Checker - Job {} ({})'.format(job_id, job.get('status', 'unknown'))
    body_parts = [
        'Status: {}'.format(job.get('status', '')),
        'Summary: {}'.format(job.get('message', '')),
    ]
    log_text = job.get('log_text', '')
    if log_text:
        body_parts.extend(['', log_text])
    body = '\n'.join(body_parts)

    try:
        resp = requests.post(
            '{}/notifications/send-mail'.format(CREATIVE_API_BASE_URL),
            json={'subject': subject, 'body': body},
            timeout=20,
        )
    except requests.RequestException as exc:
        logger.error('Failed to send log email for job %s: %s', job_id, exc)
        return False, str(exc)

    if resp.status_code != 200:
        return False, 'Mail service returned {}: {}'.format(resp.status_code, resp.text[:300])

    try:
        sent = bool(resp.json().get('sent'))
    except ValueError:
        sent = False
    if not sent:
        return False, 'Mail service reported it could not send the message.'
    return True, ''


def _run_job(job_id, groups, settings, output_dir):
    try:
        JOBS[job_id]['status'] = 'processing'

        def on_progress(idx, msg, filename=''):
            JOBS[job_id].update({'progress': idx, 'message': msg, 'current_file': filename})

        generator = AttributeGenerator(logger, groups, settings, output_dir)
        result    = generator.process(progress_callback=on_progress)

        JOBS[job_id].update({
            'status':            'done',
            'progress':          JOBS[job_id]['total'],
            'message':           result.get('message', 'Done.'),
            'current_file':      '',
            'zip_path':          result.get('zip_path', ''),
            'zip_download_name': '{}.zip'.format(result.get('output_basename', 'attribute-checker-results')),
            'log_text':          result.get('log_text', ''),
        })

        if result.get('success_count', 0) < result.get('total_groups', 0):
            _send_job_log_email(job_id, JOBS[job_id])
    except Exception as exc:
        logger.error('Job %s failed: %s', job_id, exc, exc_info=True)
        JOBS[job_id].update({
            'status':   'error',
            'message':  str(exc),
            'log_text': traceback.format_exc(),
        })
        _send_job_log_email(job_id, JOBS[job_id])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
