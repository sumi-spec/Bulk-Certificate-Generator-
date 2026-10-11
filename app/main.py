import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.health import router as health_router
from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.logging import logger, set_request_id
from app.core.metrics import metrics_registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for application startup and shutdown."""
    logger.info("Starting up %s (env: %s)...", settings.APP_NAME, settings.APP_ENV)
    # Ensure storage directory exists
    storage_path = settings.resolved_storage_path
    logger.info("Storage directory verified at: %s", storage_path)
    yield
    logger.info("Shutting down %s...", settings.APP_NAME)


TAGS_METADATA = [
    {
        "name": "Health",
        "description": "System readiness, liveness, and component connectivity probes (Database, Redis broker).",
    },
    {
        "name": "Certificate Jobs",
        "description": "Asynchronous bulk generation job creation, real-time progress polling, and paginated recipient results.",
    },
    {
        "name": "Certificates",
        "description": "Authorized binary streaming of vector PDF certificates and cryptographic SHA256 integrity verification.",
    },
]


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bulk Certificate Generator - Portal</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --primary: #1e3a8a;
      --primary-light: #3b82f6;
      --accent-gold: #f59e0b;
      --card-bg: rgba(30, 41, 59, 0.75);
      --border: rgba(255, 255, 255, 0.12);
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --success: #10b981;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: radial-gradient(circle at 50% 0%, #1e293b 0%, #0f172a 100%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 2.5rem 1rem;
    }
    .container { max-width: 820px; width: 100%; }
    .header { text-align: center; margin-bottom: 2rem; }
    .header h1 {
      font-size: 2.4rem;
      font-weight: 800;
      background: linear-gradient(135deg, #60a5fa, #fbbf24);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 0.5rem;
      letter-spacing: -0.02em;
    }
    .header p { color: var(--text-muted); font-size: 1.05rem; }
    .card {
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 2rem;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
      margin-bottom: 2rem;
    }
    .tabs {
      display: flex;
      background: rgba(15, 23, 42, 0.7);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 4px;
      margin-bottom: 1.5rem;
      gap: 4px;
    }
    .tab-btn {
      flex: 1;
      padding: 0.65rem 1rem;
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-weight: 600;
      font-size: 0.95rem;
      border-radius: 8px;
      cursor: pointer;
      transition: all 0.2s ease;
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 0.5rem;
    }
    .tab-btn:hover { color: #fff; background: rgba(255, 255, 255, 0.05); }
    .tab-btn.active {
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #fff;
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
    }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; margin-bottom: 1.25rem; }
    .form-group { display: flex; flex-direction: column; gap: 0.5rem; }
    .form-group.full-width { grid-column: span 2; }
    label { font-size: 0.875rem; font-weight: 600; color: #cbd5e1; display: flex; justify-content: space-between; align-items: center; }
    input, textarea {
      background: rgba(15, 23, 42, 0.65);
      border: 1px solid rgba(255, 255, 255, 0.15);
      color: #fff;
      padding: 0.75rem 1rem;
      border-radius: 8px;
      font-size: 0.95rem;
      font-family: inherit;
    }
    textarea {
      font-family: 'JetBrains Mono', Consolas, monospace;
      font-size: 0.9rem;
      line-height: 1.5;
      resize: vertical;
      min-height: 130px;
    }
    input:focus, textarea:focus {
      outline: none;
      border-color: var(--primary-light);
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.25);
    }
    .badge {
      font-size: 0.75rem;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 9999px;
      background: rgba(59, 130, 246, 0.2);
      color: #93c5fd;
      border: 1px solid rgba(59, 130, 246, 0.3);
    }
    .badge-success { background: rgba(16, 185, 129, 0.2); color: #6ee7b7; border-color: rgba(16, 185, 129, 0.3); }
    .badge-danger { background: rgba(239, 68, 68, 0.2); color: #fca5a5; border-color: rgba(239, 68, 68, 0.3); }
    .btn {
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #fff;
      border: none;
      padding: 0.875rem 1.75rem;
      font-size: 1rem;
      font-weight: 600;
      border-radius: 8px;
      cursor: pointer;
      width: 100%;
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 0.5rem;
      transition: all 0.2s ease;
    }
    .btn:hover {
      background: linear-gradient(135deg, #3b82f6, #2563eb);
      transform: translateY(-1px);
    }
    .btn:disabled {
      opacity: 0.6;
      cursor: not-allowed;
      transform: none;
    }
    .btn-zip {
      background: linear-gradient(135deg, #059669, #10b981);
      margin-top: 1rem;
      text-decoration: none;
      text-align: center;
      font-size: 1.05rem;
      padding: 1rem;
    }
    .btn-zip:hover {
      background: linear-gradient(135deg, #10b981, #34d399);
      box-shadow: 0 6px 16px rgba(16, 185, 129, 0.3);
    }
    .result-box {
      margin-top: 1.5rem;
      padding: 1.5rem;
      background: rgba(16, 185, 129, 0.08);
      border: 1px solid rgba(16, 185, 129, 0.3);
      border-radius: 12px;
      display: none;
    }
    .result-header { font-weight: 700; color: #34d399; margin-bottom: 0.75rem; font-size: 1.15rem; }
    .footer-links { text-align: center; color: var(--text-muted); font-size: 0.9rem; margin-top: 1rem; }
    .footer-links a { color: #60a5fa; text-decoration: none; margin: 0 0.5rem; font-weight: 500; }
    .footer-links a:hover { text-decoration: underline; }
    .spinner {
      border: 3px solid rgba(255, 255, 255, 0.3);
      border-radius: 50%;
      border-top: 3px solid #fff;
      width: 18px;
      height: 18px;
      animation: spin 1s linear infinite;
      display: none;
    }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    .stats-row {
      display: flex;
      gap: 1rem;
      margin: 1rem 0;
      background: rgba(0,0,0,0.25);
      padding: 0.75rem 1rem;
      border-radius: 8px;
    }
    .stat-item { flex: 1; text-align: center; }
    .stat-val { font-size: 1.25rem; font-weight: 700; color: #fff; }
    .stat-lbl { font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; }
    .table-container {
      overflow-x: auto;
      margin-top: 1.25rem;
      background: rgba(15, 23, 42, 0.5);
      border-radius: 8px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      max-height: 280px;
    }
    table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
    th { background: rgba(30, 41, 59, 0.8); text-align: left; padding: 0.65rem 0.85rem; color: #cbd5e1; font-weight: 600; position: sticky; top: 0; }
    td { padding: 0.6rem 0.85rem; border-top: 1px solid rgba(255, 255, 255, 0.05); }
    .action-btn {
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
      padding: 0.3rem 0.65rem;
      border-radius: 6px;
      text-decoration: none;
      font-size: 0.78rem;
      font-weight: 600;
      color: #fff;
    }
    .btn-view { background: #2563eb; }
    .btn-view:hover { background: #3b82f6; }
    .btn-dl { background: #059669; }
    .btn-dl:hover { background: #10b981; }
    .file-input-wrapper { display: flex; gap: 0.5rem; align-items: center; margin-top: 0.35rem; }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>Bulk Certificate Generator</h1>
      <p>Enterprise vector PDF certificate engine with 1-click batch ZIP packaging</p>
    </div>

    <div class="card">
      <div class="tabs">
        <button type="button" class="tab-btn active" id="tabSingle" onclick="switchMode('single')">
          👤 Single Certificate
        </button>
        <button type="button" class="tab-btn" id="tabBulk" onclick="switchMode('bulk')">
          👥 Bulk Generation (CSV / List)
        </button>
      </div>

      <form id="certForm">
        <div class="form-grid">
          <div class="form-group">
            <label for="eventName">Course / Event Name</label>
            <input type="text" id="eventName" value="Cloud Native Architecture Bootcamp" required>
          </div>
          <div class="form-group">
            <label for="issuerName">Issuing Organization</label>
            <input type="text" id="issuerName" value="Cloud Native Foundation" required>
          </div>
          <div class="form-group full-width">
            <label for="issueDate">Issue Date</label>
            <input type="date" id="issueDate" value="2026-10-10" required>
          </div>

          <!-- Single Mode Fields -->
          <div id="singleModeFields" style="grid-column: span 2; display: contents;">
            <div class="form-group">
              <label for="recipientName">Recipient Name</label>
              <input type="text" id="recipientName" value="Alex Johnson">
            </div>
            <div class="form-group">
              <label for="recipientEmail">Recipient Email</label>
              <input type="email" id="recipientEmail" value="alex.johnson@example.com">
            </div>
          </div>

          <!-- Bulk Mode Fields -->
          <div id="bulkModeFields" class="form-group full-width" style="display: none;">
            <label for="bulkRecipients">
              <span>Recipients (CSV or Line-by-Line: <code>Name, Email</code>)</span>
              <span id="bulkCountBadge" class="badge">👥 5 recipients detected</span>
            </label>
            <textarea id="bulkRecipients" placeholder="Alex Johnson, alex.johnson@example.com&#10;Sarah Connor, sarah.c@example.com">Alex Johnson, alex.johnson@example.com
Sarah Connor, sarah.connor@example.com
Michael Scott, michael.scott@dundermifflin.com
Pam Beesly, pam.beesly@dundermifflin.com
Jim Halpert, jim.halpert@dundermifflin.com</textarea>
            <div class="file-input-wrapper">
              <input type="file" id="csvFilePicker" accept=".csv,.txt" style="display: none;" onchange="handleCsvUpload(event)">
              <button type="button" class="btn" style="width: auto; padding: 0.4rem 0.85rem; font-size: 0.8rem; background: rgba(255,255,255,0.1);" onclick="document.getElementById('csvFilePicker').click()">
                📂 Load from .CSV File
              </button>
              <span style="font-size: 0.8rem; color: var(--text-muted);">Format: Name, Email on each line</span>
            </div>
          </div>
        </div>

        <button type="submit" id="submitBtn" class="btn">
          <span class="spinner" id="spinner"></span>
          <span id="btnText">✨ Generate Certificate PDF</span>
        </button>
      </form>

      <!-- Processing Progress indicator -->
      <div id="progressBox" style="display: none; margin-top: 1.25rem; text-align: center; color: var(--text-muted); font-size: 0.95rem;">
        <span id="progressText">Processing job...</span>
      </div>

      <!-- Result Container -->
      <div class="result-box" id="resultBox">
        <div class="result-header" id="resHeader">
          ✓ Certificates Generated Successfully!
        </div>

        <!-- Single Recipient Info -->
        <div id="singleResultDetails">
          <p style="color: #cbd5e1; font-size: 0.95rem; margin-bottom: 0.5rem;">
            Recipient: <strong id="resName" style="color:#fff;"></strong>
          </p>
          <p style="color: #94a3b8; font-size: 0.85rem; margin-bottom: 1rem;">
            Certificate ID: <code id="resCertId" style="color: #f1f5f9; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px;"></code>
          </p>
          <div style="display: flex; gap: 0.75rem; margin-top: 1rem; flex-wrap: wrap;">
            <a id="viewLink" href="#" class="btn" style="flex: 1; background: linear-gradient(135deg, #2563eb, #1d4ed8); text-decoration: none; text-align: center;" target="_blank">
              👁️ Open PDF in New Tab
            </a>
            <a id="downloadLink" href="#" class="btn btn-zip" style="flex: 1; margin-top: 0;" target="_blank" download>
              📥 Download PDF File
            </a>
          </div>
          <iframe id="pdfIframe" src="" style="width: 100%; height: 500px; border: 1px solid rgba(255,255,255,0.2); border-radius: 8px; margin-top: 1.25rem; display: none; background: #fff;"></iframe>
        </div>

        <!-- Bulk Recipients Info -->
        <div id="bulkResultDetails" style="display: none;">
          <div class="stats-row">
            <div class="stat-item">
              <div class="stat-val" id="statTotal">0</div>
              <div class="stat-lbl">Total</div>
            </div>
            <div class="stat-item">
              <div class="stat-val" id="statSuccess" style="color: #34d399;">0</div>
              <div class="stat-lbl">Issued</div>
            </div>
            <div class="stat-item">
              <div class="stat-val" id="statFailed" style="color: #f87171;">0</div>
              <div class="stat-lbl">Failed</div>
            </div>
          </div>

          <a id="downloadZipBtn" href="#" class="btn btn-zip">
            📦 Download All Certificates as ZIP Archive (.zip)
          </a>

          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Recipient Name</th>
                  <th>Email</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody id="bulkResultsTableBody"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <div class="footer-links">
      <a href="/docs" target="_blank">📖 Swagger UI Documentation (/docs)</a> •
      <a href="/health" target="_blank">🩺 Health Probe (/health)</a> •
      <a href="/metrics" target="_blank">📊 Metrics (/metrics)</a>
    </div>
  </div>

  <script>
    let currentMode = 'single';

    function switchMode(mode) {
      currentMode = mode;
      const tabSingle = document.getElementById('tabSingle');
      const tabBulk = document.getElementById('tabBulk');
      const singleFields = document.getElementById('singleModeFields');
      const bulkFields = document.getElementById('bulkModeFields');
      const btnText = document.getElementById('btnText');

      if (mode === 'single') {
        tabSingle.classList.add('active');
        tabBulk.classList.remove('active');
        singleFields.style.display = 'contents';
        bulkFields.style.display = 'none';
        btnText.innerText = '✨ Generate Certificate PDF';
      } else {
        tabBulk.classList.add('active');
        tabSingle.classList.remove('active');
        singleFields.style.display = 'none';
        bulkFields.style.display = 'flex';
        updateBulkCount();
        btnText.innerText = '🚀 Generate Bulk Certificates';
      }
    }

    function parseRecipients(text) {
      const lines = text.split('\n');
      const list = [];
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;
        const parts = trimmed.split(',').map(p => p.trim());
        if (parts.length >= 2 && parts[0] && parts[1]) {
          list.push({
            name: parts[0],
            email: parts[1],
            custom_identifier: parts[2] || ('CERT-' + Math.floor(1000 + Math.random() * 9000))
          });
        }
      }
      return list;
    }

    function updateBulkCount() {
      const list = parseRecipients(document.getElementById('bulkRecipients').value);
      const badge = document.getElementById('bulkCountBadge');
      badge.innerText = `👥 ${list.length} recipient${list.length === 1 ? '' : 's'} detected`;
    }

    document.getElementById('bulkRecipients').addEventListener('input', updateBulkCount);

    function handleCsvUpload(event) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = function(e) {
        document.getElementById('bulkRecipients').value = e.target.result;
        updateBulkCount();
      };
      reader.readAsText(file);
    }

    const form = document.getElementById('certForm');
    const submitBtn = document.getElementById('submitBtn');
    const spinner = document.getElementById('spinner');
    const btnText = document.getElementById('btnText');
    const progressBox = document.getElementById('progressBox');
    const progressText = document.getElementById('progressText');
    const resultBox = document.getElementById('resultBox');
    const singleResultDetails = document.getElementById('singleResultDetails');
    const bulkResultDetails = document.getElementById('bulkResultDetails');
    const pdfIframe = document.getElementById('pdfIframe');

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      spinner.style.display = 'inline-block';
      btnText.innerText = 'Submitting Job...';
      submitBtn.disabled = true;
      resultBox.style.display = 'none';
      pdfIframe.style.display = 'none';
      progressBox.style.display = 'block';
      progressText.innerText = 'Queueing generation job...';

      let recipients = [];
      if (currentMode === 'single') {
        recipients = [{
          name: document.getElementById('recipientName').value.trim(),
          email: document.getElementById('recipientEmail').value.trim(),
          custom_identifier: 'CERT-' + Math.floor(1000 + Math.random() * 9000)
        }];
      } else {
        recipients = parseRecipients(document.getElementById('bulkRecipients').value);
        if (recipients.length === 0) {
          alert('Please enter at least one valid recipient in format: Name, Email');
          submitBtn.disabled = false;
          spinner.style.display = 'none';
          progressBox.style.display = 'none';
          btnText.innerText = '🚀 Generate Bulk Certificates';
          return;
        }
      }

      const payload = {
        event_name: document.getElementById('eventName').value,
        issuer_name: document.getElementById('issuerName').value,
        issue_date: document.getElementById('issueDate').value,
        recipients: recipients
      };

      try {
        const postRes = await fetch('/api/v1/certificate-jobs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (!postRes.ok) {
          const errData = await postRes.json();
          throw new Error(errData.detail || 'Failed to submit job');
        }

        const job = await postRes.json();
        progressText.innerText = `Job submitted (${recipients.length} recipients). Generating vector PDFs...`;

        // Poll job status until completed
        let jobStatus = 'PENDING';
        let attempts = 0;
        let jobDetails = null;

        while (attempts < 60) {
          await new Promise(r => setTimeout(r, 450));
          attempts++;
          const statusRes = await fetch(`/api/v1/certificate-jobs/${job.job_id}`);
          if (statusRes.ok) {
            jobDetails = await statusRes.json();
            jobStatus = jobDetails.status;
            progressText.innerText = `Processed ${jobDetails.processed_count} of ${jobDetails.total_recipients} certificates...`;
            if (jobStatus === 'COMPLETED' || jobStatus === 'FAILED' || jobDetails.processed_count >= jobDetails.total_recipients) {
              break;
            }
          }
        }

        // Fetch detailed results
        const resQuery = await fetch(`/api/v1/certificate-jobs/${job.job_id}/results?page_size=100`);
        const resData = await resQuery.json();
        const results = resData.results || [];

        progressBox.style.display = 'none';
        resultBox.style.display = 'block';

        if (currentMode === 'single' && results.length > 0) {
          singleResultDetails.style.display = 'block';
          bulkResultDetails.style.display = 'none';
          const cert = results[0];
          document.getElementById('resName').innerText = cert.name;
          document.getElementById('resCertId').innerText = cert.certificate_id || 'N/A';
          const pdfUrl = `/api/v1/certificates/${cert.certificate_id}`;
          document.getElementById('viewLink').href = pdfUrl + '?inline=true';
          const dlLink = document.getElementById('downloadLink');
          dlLink.href = pdfUrl;
          dlLink.setAttribute('download', cert.name.replace(/\s+/g, '_') + '_Certificate.pdf');
          pdfIframe.src = pdfUrl + '?inline=true';
          pdfIframe.style.display = 'block';
        } else {
          singleResultDetails.style.display = 'none';
          bulkResultDetails.style.display = 'block';

          document.getElementById('statTotal').innerText = jobDetails ? jobDetails.total_recipients : results.length;
          document.getElementById('statSuccess').innerText = jobDetails ? jobDetails.success_count : results.filter(r => r.status === 'SUCCESS').length;
          document.getElementById('statFailed').innerText = jobDetails ? jobDetails.failure_count : results.filter(r => r.status === 'FAILED').length;

          const zipBtn = document.getElementById('downloadZipBtn');
          zipBtn.href = `/api/v1/certificate-jobs/${job.job_id}/download-zip`;

          const tbody = document.getElementById('bulkResultsTableBody');
          tbody.innerHTML = '';
          results.forEach((r, idx) => {
            const tr = document.createElement('tr');
            const isSuccess = r.status === 'SUCCESS' && r.certificate_id;
            tr.innerHTML = `
              <td style="color: var(--text-muted);">${idx + 1}</td>
              <td style="font-weight: 600; color: #fff;">${r.name}</td>
              <td style="color: var(--text-muted);">${r.email}</td>
              <td><span class="badge ${isSuccess ? 'badge-success' : 'badge-danger'}">${r.status}</span></td>
              <td>
                ${isSuccess ? `
                  <a href="/api/v1/certificates/${r.certificate_id}?inline=true" target="_blank" class="action-btn btn-view">👁️ View</a>
                  <a href="/api/v1/certificates/${r.certificate_id}" download class="action-btn btn-dl">📥 PDF</a>
                ` : `<span style="color: #f87171; font-size: 0.75rem;">${r.error_message || 'Failed'}</span>`}
              </td>
            `;
            tbody.appendChild(tr);
          });
        }
      } catch (err) {
        alert('Error: ' + err.message);
        progressBox.style.display = 'none';
      } finally {
        spinner.style.display = 'none';
        btnText.innerText = currentMode === 'single' ? '✨ Generate Certificate PDF' : '🚀 Generate Bulk Certificates';
        submitBtn.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


def create_application() -> FastAPI:
    """Application factory for FastAPI."""
    application = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description=(
            "### Bulk Certificate Generator Backend API\n\n"
            "An enterprise-grade, asynchronous bulk certificate generation engine.\n\n"
            "**Key Features:**\n"
            "- Asynchronous bulk processing via Celery & Redis\n"
            "- Fault-tolerant partial success: invalid recipients fail independently\n"
            "- ReportLab vector PDF rendering with dynamic font scaling & decorative borders\n"
            "- Idempotent job submission with `Idempotency-Key` headers\n"
            "- Cryptographic SHA256 integrity checksums\n"
            "- Sliding-window rate limiting & path-traversal prevention\n"
        ),
        openapi_tags=TAGS_METADATA,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={
            "name": "Engineering Team",
            "url": "https://github.com/org/bulk-certificate-generator",
        },
        license_info={
            "name": "MIT License",
            "url": "https://opensource.org/licenses/MIT",
        },
        lifespan=lifespan,
    )

    # CORS Configuration
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.ALLOWED_ORIGINS.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Observability: Correlation ID and Telemetry Middleware
    @application.middleware("http")
    async def correlation_and_metrics_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        set_request_id(request_id)
        request.state.request_id = request_id
        start_time = time.perf_counter()

        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration = time.perf_counter() - start_time
            status_code = response.status_code if response else 500
            metrics_registry.record_http_request(
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_seconds=duration,
            )
            if response:
                response.headers["X-Request-ID"] = request_id
            set_request_id(None)

    # Security Headers and Payload Size Middleware
    @application.middleware("http")
    async def security_headers_and_limits_middleware(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > settings.MAX_REQUEST_BODY_BYTES:
            return JSONResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content={
                    "error": "Payload Too Large",
                    "message": f"Request body exceeds maximum allowed size of {settings.MAX_REQUEST_BODY_BYTES} bytes.",
                },
            )

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response

    # Global Exception Handlers
    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        logger.warning("Validation error on %s: %s", request.url.path, exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "Unprocessable Entity",
                "message": "The request payload failed validation checks.",
                "details": exc.errors(),
            },
        )

    @application.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled server exception on %s: %s", request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred while processing the request.",
            },
        )

    # Interactive Web Interface
    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        return HTMLResponse(content=INDEX_HTML)

    # Mount Routers
    application.include_router(health_router)
    application.include_router(api_v1_router, prefix=settings.API_V1_STR)

    return application


app = create_application()
