import base64
import csv
import io
import os
import re
import time
import zipfile
import requests
import xlsxwriter
from datetime import datetime
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


THUMB_WIDTH = 100
THUMB_HEIGHT = 133
ROW_HEIGHT_PTS = 105  # ~140px at 96dpi


def _sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name or '').strip().strip('.')
    return name[:100]


_PIL_FORMAT_TO_MIME = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'GIF': 'image/gif', 'WEBP': 'image/webp'}
_EXT_TO_MIME = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'}


def _detect_mime(filename, image_bytes):
    if PIL_AVAILABLE:
        try:
            fmt = (Image.open(BytesIO(image_bytes)).format or '').upper()
            if fmt in _PIL_FORMAT_TO_MIME:
                return _PIL_FORMAT_TO_MIME[fmt]
        except Exception:
            pass
    return _EXT_TO_MIME.get(Path(filename).suffix.lower(), 'image/png')


class AttributeGenerator:
    def __init__(self, logger, groups, settings, output_dir):
        self.__logger = logger
        self.__groups = groups                # list of {'files': [(filename, bytes), ...], 'group_key': str}
        self.__output_dir = output_dir

        self.__api_key = os.environ.get('API_KEY', '')
        self.__gpt_version = os.environ.get('GPT_VERSION', 'gpt-4.1')
        self.__rpm_limit = int(os.environ.get('GPT_LIMIT', 10))

        self.__attributes = settings.get('attributes', [])
        self.__prompt_instruction = settings.get('prompt_instruction', '')
        self.__output_formats = [f.lower() for f in settings.get('output_formats', ['xls'])]
        self.__output_filename = settings.get('output_filename', '').strip()

        # Required Inputs + Prompt Template are per description-batch (per group), so a
        # batch with multiple products can use a different prompt/brand/material for each.
        self.__group_settings = settings.get('group_settings', {})
        self.__default_group_key = settings.get('default_group_key', '__default__')
        self.__default_prompt_template = settings.get('default_prompt_template', '')

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def __resolve_group_settings(self, group_key):
        gs = self.__group_settings.get(group_key) or self.__group_settings.get(self.__default_group_key) or {}
        desc_type = (gs.get('description_type') or 'PD').strip().upper()
        desc_label = 'AE (Structured and Polished Style)' if desc_type == 'AE' else 'PD (Feminine Style)'
        return {
            'prompt_template':   gs.get('prompt_template') or self.__default_prompt_template,
            'brand_name':        (gs.get('brand_name') or '').strip(),
            'product_category':  (gs.get('product_category') or '').strip(),
            'material':          (gs.get('material') or '').strip(),
            'care_instructions': (gs.get('care_instructions') or '').strip(),
            'additional_info':   (gs.get('additional_info') or '').strip(),
            'description_type_label': desc_label,
        }

    def __build_prompt(self, group_key):
        resolved = self.__resolve_group_settings(group_key)
        attr_lines = '\n'.join(
            '- {}: {}'.format(a['name'], a['description'])
            for a in self.__attributes
        )
        template = resolved['prompt_template']
        template = template.replace('{{ATTRIBUTES}}', attr_lines)
        template = template.replace('{{BRAND_NAME}}', resolved['brand_name'] or '(not provided)')
        template = template.replace('{{PRODUCT_CATEGORY}}', resolved['product_category'] or '(not provided)')
        template = template.replace('{{DESCRIPTION_TYPE}}', resolved['description_type_label'])
        template = template.replace('{{MATERIAL}}', resolved['material'] or '(not provided)')
        template = template.replace('{{CARE_INSTRUCTIONS}}', resolved['care_instructions'] or '(not provided)')
        template = template.replace('{{ADDITIONAL_INFO}}', resolved['additional_info'] or '(not provided)')
        return '{}\n\n{}'.format(self.__prompt_instruction, template)

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    def __encode_image(self, filename, image_bytes):
        mime = _detect_mime(filename, image_bytes)
        b64 = base64.b64encode(image_bytes).decode('utf-8')
        return 'data:{};base64,{}'.format(mime, b64)

    def __get_gpt_response(self, image_data_urls, prompt):
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer {}'.format(self.__api_key),
        }
        content = [{'type': 'text', 'text': prompt}]
        for data_url in image_data_urls:
            content.append({'type': 'image_url', 'image_url': {'url': data_url}})
        payload = {
            'model': self.__gpt_version,
            'messages': [{'role': 'user', 'content': content}],
            'max_completion_tokens': 1000,
        }
        return requests.post('https://api.openai.com/v1/chat/completions', headers=headers, json=payload, timeout=60)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def __clean_line(self, line):
        line = re.sub(r'\*\*(.*?)\*\*', r'\1', line)       # strip markdown bold
        line = re.sub(r'^[\-\*•]+\s*', '', line)            # strip leading bullet markers
        return line.strip()

    def __parse_response(self, content):
        raw_lines = [l.strip() for l in content.strip().split('\n') if l.strip()]
        lines = [self.__clean_line(l) for l in raw_lines]

        product_title = ''
        for idx, line in enumerate(lines):
            if re.match(r'^product title\s*:?\s*$', line, re.IGNORECASE):
                for nxt in lines[idx + 1:]:
                    if nxt:
                        product_title = nxt.strip('"').strip()
                        break
                break
        if not product_title:
            for line in lines:
                clean = line.strip('"').strip()
                if clean:
                    product_title = clean
                    break

        attr_values = {}
        for attr in self.__attributes:
            name = attr['name']
            pattern = re.compile(r'^{}[\s\-:]+(.+)$'.format(re.escape(name)), re.IGNORECASE)
            for line in lines:
                m = pattern.match(line)
                if m:
                    attr_values[name] = m.group(1).strip()
                    break

        return {
            'product_title': product_title,
            'product_description': content,
            'attributes': attr_values,
        }

    # ------------------------------------------------------------------
    # Image thumbnail for XLS
    # ------------------------------------------------------------------

    def __make_thumb(self, image_bytes):
        if not PIL_AVAILABLE:
            return BytesIO(image_bytes)
        try:
            img = Image.open(BytesIO(image_bytes))
            img.thumbnail((THUMB_WIDTH, THUMB_HEIGHT), Image.Resampling.LANCZOS)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            buf = BytesIO()
            img.save(buf, format='JPEG', quality=85)
            buf.seek(0)
            return buf
        except Exception:
            return BytesIO(image_bytes)

    # ------------------------------------------------------------------
    # Main processing
    # ------------------------------------------------------------------

    def process(self, progress_callback=None):
        if not self.__api_key:
            raise ValueError('API_KEY environment variable is not set.')

        do_xls = 'xls' in self.__output_formats
        do_csv = 'csv' in self.__output_formats
        do_txt = 'txt' in self.__output_formats
        attr_names = [a['name'] for a in self.__attributes]

        output_files = []
        ts = datetime.now().strftime('%Y%m%dT%H%M')
        base_name = _sanitize_filename(self.__output_filename) or 'attributes_{}'.format(ts)

        # --- XLS setup ---
        xls_path, workbook, worksheet, xls_row = None, None, None, 1
        if do_xls:
            xls_path = os.path.join(self.__output_dir, '{}.xlsx'.format(base_name))
            workbook = xlsxwriter.Workbook(xls_path)
            worksheet = workbook.add_worksheet('Products')
            header_fmt = workbook.add_format({'bold': True, 'bg_color': '#1B2A4A', 'font_color': '#FFFFFF', 'align': 'center', 'valign': 'vcenter', 'border': 1})
            cell_fmt = workbook.add_format({'valign': 'top', 'text_wrap': True, 'border': 1})

            headers = ['Picture', 'Product Title', 'Product Description'] + attr_names
            worksheet.set_row(0, 20, header_fmt)
            worksheet.set_column(0, 0, 16)   # Picture
            worksheet.set_column(1, 1, 28)   # Title
            worksheet.set_column(2, 2, 50)   # Description
            worksheet.set_column(3, 3 + len(attr_names), 22)  # Attributes
            for col, h in enumerate(headers):
                worksheet.write(0, col, h, header_fmt)

        # --- CSV setup ---
        csv_path, csv_file, csv_writer = None, None, None
        if do_csv:
            csv_path = os.path.join(self.__output_dir, '{}.csv'.format(base_name))
            csv_file = open(csv_path, 'w', newline='', encoding='utf-8-sig')
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(['File Name', 'Product Title', 'Product Description'] + attr_names)

        txt_paths = []
        attempt_count = 0
        success_count = 0
        last_error = ''
        job_log = []

        for idx, group_entry in enumerate(self.__groups):
            group = group_entry['files']
            prompt = self.__build_prompt(group_entry['group_key'])
            group_filenames = [fn for fn, _ in group]
            primary_filename = group_filenames[0]
            display_name = primary_filename if len(group) == 1 else '{} (+{} more)'.format(primary_filename, len(group) - 1)

            if progress_callback:
                progress_callback(idx, 'Processing {} ({}/{})...'.format(display_name, idx + 1, len(self.__groups)), display_name)
            job_log.append('[{}/{}] Processing {}'.format(idx + 1, len(self.__groups), display_name))

            # Rate limiting — counts API calls (one per group), not raw photos
            if attempt_count > 0 and attempt_count % self.__rpm_limit == 0:
                self.__logger.info('RPM limit reached — pausing 21 seconds.')
                time.sleep(21)

            self.__logger.info('Processing: %s', display_name)

            try:
                data_urls = [self.__encode_image(fn, file_bytes) for fn, file_bytes in group]
                resp = self.__get_gpt_response(data_urls, prompt)
                attempt_count += 1

                data = resp.json()
                choices = data.get('choices', [])
                if not choices:
                    last_error = (data.get('error') or {}).get('message') or str(data)[:300]
                    job_log.append('  FAILED: {}'.format(last_error))
                    self.__logger.warning('No choices in response for %s — API error: %s', display_name, data)
                    continue

                content = choices[0].get('message', {}).get('content', '')
                parsed = self.__parse_response(content)
                title = parsed['product_title']
                desc = parsed['product_description']
                attrs = parsed['attributes']

                # XLS row
                if do_xls and worksheet:
                    worksheet.set_row(xls_row, ROW_HEIGHT_PTS, cell_fmt)
                    thumb = self.__make_thumb(group[0][1])
                    # Use unique name per row so xlsxwriter doesn't deduplicate images
                    thumb_name = '{}_row{}.jpg'.format(Path(primary_filename).stem, xls_row)
                    worksheet.insert_image(xls_row, 0, thumb_name, {
                        'image_data': thumb,
                        'x_offset': 2,
                        'y_offset': 2,
                        'x_scale': 1,
                        'y_scale': 1,
                        'positioning': 1,
                    })
                    worksheet.write(xls_row, 1, title, cell_fmt)
                    worksheet.write(xls_row, 2, desc, cell_fmt)
                    for col_off, attr_name in enumerate(attr_names):
                        worksheet.write(xls_row, 3 + col_off, attrs.get(attr_name, ''), cell_fmt)
                    xls_row += 1

                # CSV row
                if do_csv and csv_writer:
                    row = ['; '.join(group_filenames), title, desc] + [attrs.get(n, '') for n in attr_names]
                    csv_writer.writerow(row)

                # TXT file (one per product)
                if do_txt:
                    safe = re.sub(r'[<>:"/\\|?*\n\r]', '', title)[:120].strip() or Path(primary_filename).stem
                    txt_path = os.path.join(self.__output_dir, '{}.txt'.format(safe))
                    # Avoid name collision
                    counter = 1
                    base_path = txt_path
                    while os.path.exists(txt_path):
                        txt_path = base_path.replace('.txt', '_{}.txt'.format(counter))
                        counter += 1
                    with open(txt_path, 'w', encoding='utf-8', newline='\n') as tf:
                        tf.write(content)
                    txt_paths.append(txt_path)

                success_count += 1
                job_log.append('  OK: "{}"'.format(title))

            except Exception as e:
                last_error = str(e)
                job_log.append('  FAILED: {}'.format(last_error))
                self.__logger.error('Error processing %s: %s', display_name, e, exc_info=True)
                continue

        # Finalize
        if do_xls and workbook:
            workbook.close()
            output_files.append(xls_path)

        if do_csv and csv_file:
            csv_file.close()
            output_files.append(csv_path)

        if do_txt:
            output_files.extend(txt_paths)

        zip_path = os.path.join(self.__output_dir, 'results.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fp in output_files:
                if fp and os.path.exists(fp):
                    zf.write(fp, os.path.basename(fp))

        if progress_callback:
            progress_callback(len(self.__groups), 'Complete', '')

        total_groups = len(self.__groups)
        total_images = sum(len(g['files']) for g in self.__groups)
        message = '{} product listing(s) generated from {} image(s).'.format(success_count, total_images)
        if success_count < total_groups:
            failed = total_groups - success_count
            message += ' {} failed{}'.format(failed, ' — {}'.format(last_error) if last_error else '.')

        return {
            'message': message,
            'status': 'done',
            'zip_path': zip_path,
            'output_basename': _sanitize_filename(self.__output_filename) or 'attribute-checker-results',
            'success_count': success_count,
            'total_groups': total_groups,
            'log_text': '\n'.join(job_log),
        }
