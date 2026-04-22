import os
import json
import uuid
import threading
import requests
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ─── in-memory stores ────────────────────────────────────────────────────────
_stats = {"visits": 0, "searches": 0}

# job store: { job_id: { "status": "pending|done|error", "result": {...} } }
_jobs = {}

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "yjvknUyDmAP6SKLQAUtqM5FH65cP69Id")
MISTRAL_URL     = "https://api.mistral.ai/v1/chat/completions"


# ─── helpers ─────────────────────────────────────────────────────────────────

def call_mistral(prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "mistral-small-latest",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.35,
        "max_tokens": 2500,
    }
    res = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=170)
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"]


def build_prompt(query: str) -> str:
    return f"""You are a world-class competitive intelligence engine. The user has searched for: "{query}"

Identify the top 5 real-world competitors or closely related companies/products to "{query}".
Return ONLY a raw JSON array, no markdown, no backticks, no explanation, nothing else.

Each item must follow this exact structure:
[
  {{
    "name": "Competitor Name",
    "tagline": "One sharp sentence describing what they do",
    "website": "https://example.com",
    "category": "e.g. SaaS / Fintech / Social Media / EdTech",
    "founded": "Year or Unknown",
    "hq": "City, Country or Unknown",
    "sources": ["Crunchbase", "TechCrunch", "Wikipedia"],
    "activity_level": "High",
    "threat_rating": 8,
    "threat_reason": "One sentence explaining why they are a threat",
    "similarity_score": 7,
    "similarity_reason": "One sentence on how they overlap with the search",
    "new_trends": ["Recent product launch or move", "Another recent development"],
    "strengths": ["Key strength 1", "Key strength 2", "Key strength 3"],
    "weaknesses": ["Weakness 1", "Weakness 2"],
    "market_share": "~12% or Unlisted or Unknown",
    "funding": "Series B - $40M or Bootstrapped or Unknown",
    "employee_count": "200-500 or Unknown",
    "image_query": "3-word visual search phrase for this company logo or brand"
  }}
]

Rules:
- activity_level must be exactly one of: High, Medium, Low
- threat_rating and similarity_score must be integers between 1 and 10
- Return exactly 5 competitors
- Raw JSON array only. Absolutely no other text."""


def strip_fences(raw: str) -> str:
    clean = raw.strip()
    if clean.startswith("```"):
        parts = clean.split("```")
        clean = parts[1] if len(parts) > 1 else clean
        if clean.lower().startswith("json"):
            clean = clean[4:]
    return clean.strip()


def process_job(job_id: str, query: str):
    """Runs in a background thread. Updates _jobs[job_id] when done."""
    try:
        raw         = call_mistral(build_prompt(query))
        competitors = json.loads(strip_fences(raw))

        if not isinstance(competitors, list):
            raise ValueError("AI returned non-list response")

        _jobs[job_id] = {
            "status": "done",
            "result": {
                "query":       query,
                "competitors": competitors,
                "total":       len(competitors),
                "timestamp":   datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        }

    except requests.exceptions.Timeout:
        _jobs[job_id] = {"status": "error", "error": "AI service timed out. Please try again."}
    except requests.exceptions.HTTPError as e:
        _jobs[job_id] = {"status": "error", "error": f"AI service error: {e.response.status_code}"}
    except json.JSONDecodeError:
        _jobs[job_id] = {"status": "error", "error": "Could not parse AI response. Try again."}
    except Exception as e:
        _jobs[job_id] = {"status": "error", "error": str(e)}


# ─── routes ──────────────────────────────────────────────────────────────────

@app.route("/rivalscan/track-visit", methods=["POST", "OPTIONS"])
def track_visit():
    _stats["visits"] += 1
    return jsonify({"ok": True, "visits": _stats["visits"]})


@app.route("/rivalscan/stats", methods=["GET", "OPTIONS"])
def get_stats():
    return jsonify({"visits": _stats["visits"], "searches": _stats["searches"]})


@app.route("/rivalscan/start", methods=["POST", "OPTIONS"])
def start_search():
    """Instantly returns a job_id and kicks off Mistral in a background thread."""
    data  = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()

    if not query:
        return jsonify({"error": "Query is required"}), 400
    if len(query) > 250:
        return jsonify({"error": "Query too long (max 250 characters)"}), 400

    _stats["searches"] += 1

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending"}

    t = threading.Thread(target=process_job, args=(job_id, query), daemon=True)
    t.start()

    return jsonify({"job_id": job_id}), 202


@app.route("/rivalscan/result/<job_id>", methods=["GET", "OPTIONS"])
def get_result(job_id):
    """Frontend polls this every 3s until status is done or error."""
    job = _jobs.get(job_id)

    if job is None:
        return jsonify({"status": "error", "error": "Job not found"}), 404

    if job["status"] == "pending":
        return jsonify({"status": "pending"}), 202

    if job["status"] == "error":
        # clean up and return error
        _jobs.pop(job_id, None)
        return jsonify({"status": "error", "error": job.get("error", "Unknown error")}), 500

    # done — return result and clean up
    result = job["result"]
    _jobs.pop(job_id, None)
    return jsonify({"status": "done", **result}), 200


@app.route("/")
def serve_frontend():
    try:
        return send_from_directory(".", "index.html")
    except Exception:
        return jsonify({"status": "RivalScan backend is running"})


# ─── start ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
