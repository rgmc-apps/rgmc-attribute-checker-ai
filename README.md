# Attribute Checker

A Flask web service that analyzes clothing product images using GPT-4 Vision and generates structured product attributes for e-commerce listings.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12 · Flask 3 |
| AI | OpenAI GPT-4.1 Vision API |
| Output generation | XlsxWriter (Excel) · csv (stdlib) |
| Image processing | Pillow |
| HTTP server | Gunicorn (1 worker · 8 threads) |
| Containerization | Docker |
| Deployment | Google Cloud Run |

## Features

- **Multi-image upload** — drag-and-drop or browse; supports JPG, JPEG, PNG, WEBP
- **Editable prompt template** — full prompt visible and editable in the browser; the fixed instruction line is non-editable
- **Dynamic attribute editor** — add, remove, and describe any number of product attributes; these are injected into the prompt at `{{ATTRIBUTES}}` and become table columns in the output
- **Three output formats** (select any combination):
  - **Excel (.xlsx)** — embedded product thumbnail, Product Title, Product Description, one column per attribute
  - **CSV (.csv)** — same structure as Excel; original filename replaces the thumbnail column
  - **Text (.txt)** — one file per product, named after the product title, contains the full GPT response
- **Background processing** — file upload returns immediately with a job ID; the browser polls for progress
- **Rate-limit handling** — automatically pauses when the GPT requests-per-minute limit is reached
- **ZIP download** — all generated files are packaged into a single download

## Environment Variables

Set these in Cloud Run (or a local `.env`):

| Variable | Description | Default |
|---|---|---|
| `API_KEY` | OpenAI API key | *(required)* |
| `GPT_VERSION` | OpenAI model to use | `gpt-4.1` |
| `GPT_LIMIT` | Requests per minute limit | `10` |
| `SCRIPT_VERSION` | Processing version | `2` |
| `PORT` | HTTP port | `8080` |

## Local Development

```bash
pip install -r requirements.txt
API_KEY=sk-... GPT_VERSION=gpt-4.1 python main.py
# Open http://localhost:8080
```

## Docker

```bash
docker build -t attribute-checker .
docker run -p 8080:8080 -e API_KEY=sk-... attribute-checker
```

## Deploy to Google Cloud Run

```bash
gcloud run deploy attribute-checker \
  --image gcr.io/YOUR_PROJECT/attribute-checker \
  --platform managed \
  --region asia-southeast1 \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300 \
  --concurrency 10 \
  --set-env-vars API_KEY=sk-...,GPT_VERSION=gpt-4.1,GPT_LIMIT=10
```

## Output Column Reference

### Excel / CSV

| Column | Description |
|---|---|
| Picture *(Excel only)* | Thumbnail of the uploaded image |
| File Name *(CSV only)* | Original filename |
| Product Title | First line of the GPT response |
| Product Description | Full GPT response text |
| *Attribute columns* | One column per attribute defined in the UI |

### Text files

Named `<Product Title>.txt`. Contains the full GPT response for that product.

## Project Structure

```
rgmc-attribute-checker/
├── main.py                      # Flask app & routes
├── requirements.txt
├── Dockerfile
├── .dockerignore
├── .gitignore
├── README.md
├── templates/
│   └── index.html               # Single-page UI
├── helper/
│   └── attribute_generator.py   # OpenAI calls · output file generation
└── logger/
    └── __init__.py              # Rotating file + console logger
```
