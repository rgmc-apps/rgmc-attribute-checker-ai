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


class AttributeGenerator:
    def __init__(self, logger, file_data, settings, output_dir):
        self.__logger = logger
        self.__file_data = file_data          # list of (filename, bytes)
        self.__output_dir = output_dir

        self.__api_key = os.environ.get('API_KEY', '')
        self.__gpt_version = os.environ.get('GPT_VERSION', 'gpt-4.1')
        self.__rpm_limit = int(os.environ.get('GPT_LIMIT', 10))

        self.__attributes = settings.get('attributes', [])
        self.__prompt_template = settings.get('prompt_template', '')
        self.__prompt_instruction = settings.get('prompt_instruction', '')
        self.__output_formats = [f.lower() for f in settings.get('output_formats', ['xls'])]

        self.__brand_name = settings.get('brand_name', '').strip()
        self.__product_category = settings.get('product_category', '').strip()
        self.__material = settings.get('material', '').strip()
        self.__care_instructions = settings.get('care_instructions', '').strip()
        self.__additional_info = settings.get('additional_info', '').strip()

        desc_type = settings.get('description_type', 'PD').strip().upper()
        self.__description_type_label = (
            'AE (Structured and Polished Style)' if desc_type == 'AE'
            else 'PD (Feminine Style)'
        )

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def __build_prompt(self):
        attr_lines = '\n'.join(
            '- {}: {}'.format(a['name'], a['description'])
            for a in self.__attributes
        )
        template = self.__prompt_template
        template = template.replace('{{ATTRIBUTES}}', attr_lines)
        template = template.replace('{{BRAND_NAME}}', self.__brand_name or '(not provided)')
        template = template.replace('{{PRODUCT_CATEGORY}}', self.__product_category or '(not provided)')
        template = template.replace('{{DESCRIPTION_TYPE}}', self.__description_type_label)
        template = template.replace('{{MATERIAL}}', self.__material)
        template = template.replace('{{CARE_INSTRUCTIONS}}', self.__care_instructions)
        template = template.replace('{{ADDITIONAL_INFO}}', self.__additional_info or '(not provided)')
        return '{}\n\n{}'.format(self.__prompt_instruction, template)

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    def __encode_image(self, image_bytes):
        return base64.b64encode(image_bytes).decode('utf-8')

    def __get_gpt_response(self, image_b64, prompt):
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer {}'.format(self.__api_key),
        }
        payload = {
            'model': self.__gpt_version,
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': prompt},
                        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,{}'.format(image_b64)}},
                    ],
                }
            ],
            'max_tokens': 1000,
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
        if not self.__material:
            raise ValueError('Material Composition is required.')
        if not self.__care_instructions:
            raise ValueError('Care / Wash Instructions is required.')

        prompt = self.__build_prompt()
        do_xls = 'xls' in self.__output_formats
        do_csv = 'csv' in self.__output_formats
        do_txt = 'txt' in self.__output_formats
        attr_names = [a['name'] for a in self.__attributes]

        output_files = []
        ts = datetime.now().strftime('%Y%m%dT%H%M')

        # --- XLS setup ---
        xls_path, workbook, worksheet, xls_row = None, None, None, 1
        if do_xls:
            xls_path = os.path.join(self.__output_dir, 'attributes_{}.xlsx'.format(ts))
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
            csv_path = os.path.join(self.__output_dir, 'attributes_{}.csv'.format(ts))
            csv_file = open(csv_path, 'w', newline='', encoding='utf-8-sig')
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(['File Name', 'Product Title', 'Product Description'] + attr_names)

        txt_paths = []
        entry_count = 0

        for idx, (filename, file_bytes) in enumerate(self.__file_data):
            if progress_callback:
                progress_callback(idx, 'Processing {} ({}/{})...'.format(filename, idx + 1, len(self.__file_data)), filename)

            # Rate limiting
            if entry_count > 0 and entry_count % self.__rpm_limit == 0:
                self.__logger.info('RPM limit reached — pausing 21 seconds.')
                time.sleep(21)

            self.__logger.info('Processing: %s', filename)

            try:
                b64 = self.__encode_image(file_bytes)
                resp = self.__get_gpt_response(b64, prompt)
                entry_count += 1

                data = resp.json()
                choices = data.get('choices', [])
                if not choices:
                    self.__logger.warning('No choices in response for %s — API error: %s', filename, data)
                    continue

                content = choices[0].get('message', {}).get('content', '')
                parsed = self.__parse_response(content)
                title = parsed['product_title']
                desc = parsed['product_description']
                attrs = parsed['attributes']

                # XLS row
                if do_xls and worksheet:
                    worksheet.set_row(xls_row, ROW_HEIGHT_PTS, cell_fmt)
                    thumb = self.__make_thumb(file_bytes)
                    # Use unique name per row so xlsxwriter doesn't deduplicate images
                    thumb_name = '{}_row{}.jpg'.format(Path(filename).stem, xls_row)
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
                    row = [filename, title, desc] + [attrs.get(n, '') for n in attr_names]
                    csv_writer.writerow(row)

                # TXT file (one per product)
                if do_txt:
                    safe = re.sub(r'[<>:"/\\|?*\n\r]', '', title)[:120].strip() or Path(filename).stem
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

            except Exception as e:
                self.__logger.error('Error processing %s: %s', filename, e, exc_info=True)
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
            progress_callback(len(self.__file_data), 'Complete', '')

        processed = entry_count
        return {
            'message': '{} image(s) processed successfully.'.format(processed),
            'status': 'done',
            'zip_path': zip_path,
        }
