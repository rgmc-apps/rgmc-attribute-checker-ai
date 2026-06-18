import json
import logging
import os
import threading
import uuid
import tempfile

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


JOBS: dict = {}

PROMPT_INSTRUCTION = (
    'Make me a product description of the attached photo. '
    'Follow the format below for the result. Remove Bold formatting.'
)

PROMPT_TEMPLATE_DEFAULT = '''"[Product Title in all caps. Add brand name if visible in photo. Also add a random female first name. Product Name should include what type of Dress (E.g. A-Line Dress, Sheath Dress, Collared Top)]

[Descriptive Paragraph]

A 2-3 sentence paragraph describing the item with key benefits woven in naturally. Talk about versatility, fit, material, and suggested use.

✅ Why You\'ll Love It [Items should be one short sentence only]

{{ATTRIBUTES}}

📌 Sizing & Color Notice

Color may appear slightly different due to lighting or screen settings. For best fit, please refer to our size chart in the product photos.

Need help? Message us about size help, returns, or product info."

Tags: [Possible tags that can be used in the product in list form]'''

DEFAULT_ATTRIBUTES = [
    {'name': 'Material', 'description': 'Actual material made, Feel, weight, breathability, structure'},
    {'name': 'Style',    'description': 'Occasion-fit or lifestyle relevance'},
    {'name': 'Fit',      'description': 'Body feel, cinch, looseness'},
    {'name': 'Details',  'description': 'Standout design features'},
    {'name': 'Sleeves',  'description': 'Length and weather-appropriateness'},
    {'name': 'Design',   'description': 'Observable colors and Print if visible'},
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


@app.route('/process', methods=['POST'])
def process():
    files = request.files.getlist('images')
    valid_files = [(f.filename, f.read()) for f in files if f.filename]
    if not valid_files:
        return jsonify({'error': 'No files uploaded.'}), 400

    raw_formats = request.form.get('output_formats', '["xls"]')
    raw_attrs   = request.form.get('attributes', '[]')

    try:
        output_formats = json.loads(raw_formats)
        attributes     = json.loads(raw_attrs)
    except json.JSONDecodeError as exc:
        return jsonify({'error': 'Invalid JSON in form fields: {}'.format(exc)}), 400

    if not output_formats:
        return jsonify({'error': 'Select at least one output format.'}), 400

    settings = {
        'attributes':        attributes,
        'prompt_template':   request.form.get('prompt_template', PROMPT_TEMPLATE_DEFAULT),
        'prompt_instruction': PROMPT_INSTRUCTION,
        'output_formats':    output_formats,
    }

    job_id     = str(uuid.uuid4())
    output_dir = tempfile.mkdtemp()

    JOBS[job_id] = {
        'status':     'queued',
        'progress':   0,
        'total':      len(valid_files),
        'message':    'Queued…',
        'zip_path':   '',
    }

    t = threading.Thread(
        target=_run_job,
        args=(job_id, valid_files, settings, output_dir),
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
        'status':   job['status'],
        'progress': job['progress'],
        'total':    job['total'],
        'message':  job['message'],
    })


@app.route('/download/<job_id>')
def download_result(job_id):
    job = JOBS.get(job_id)
    if not job or job['status'] != 'done':
        return jsonify({'error': 'Result not ready.'}), 404
    return send_file(
        job['zip_path'],
        as_attachment=True,
        download_name='attribute-checker-results.zip',
        mimetype='application/zip',
    )


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


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------

def _run_job(job_id, file_data, settings, output_dir):
    try:
        JOBS[job_id]['status'] = 'processing'

        def on_progress(idx, msg):
            JOBS[job_id].update({'progress': idx, 'message': msg})

        generator = AttributeGenerator(logger, file_data, settings, output_dir)
        result    = generator.process(progress_callback=on_progress)

        JOBS[job_id].update({
            'status':   'done',
            'progress': JOBS[job_id]['total'],
            'message':  result.get('message', 'Done.'),
            'zip_path': result.get('zip_path', ''),
        })
    except Exception as exc:
        logger.error('Job %s failed: %s', job_id, exc, exc_info=True)
        JOBS[job_id].update({'status': 'error', 'message': str(exc)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
