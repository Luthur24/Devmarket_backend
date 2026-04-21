import os
import json
import requests
from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
from extensions import db

rivalscan = Blueprint("rivalscan", __name__, url_prefix="/rivalscan")

MISTRAL_API_KEY='yjvknUyDmAP6SKLQAUtqM5FH65cP69Id'
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"


# ─── db model ────────────────────────────────────────────────────────────────

class RivalscanStat(db.Model):
    __tablename__ = "rivalscan_stats"
    id       = db.Column(db.Integer, primary_key=True)
    visits   = db.Column(db.Integer, default=0, nullable=False)
    searches = db.Column(db.Integer, default=0, nullable=False)

    @classmethod
    def get(cls):
        row = cls.query.first()
        if not row:
            row = cls(visits=0, searches=0)
            db.session.add(row)
            db.session.commit()
        return row


# ─── helpers ─────────────────────────────────────────────────────────────────

def call_mistral(prompt: str) -> str:
    if not MISTRAL_API_KEY:
        raise ValueError("MISTRAL_API_KEY not set in environment")
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "mistral-small-latest",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.35,
        "max_tokens": 3000,
    }
    res = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=45)
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


# ─── routes ──────────────────────────────────────────────────────────────────

@rivalscan.route("/track-visit", methods=["POST", "OPTIONS"])
@cross_origin()
def track_visit():
    stat = RivalscanStat.get()
    stat.visits += 1
    db.session.commit()
    return jsonify({"ok": True, "visits": stat.visits})


@rivalscan.route("/stats", methods=["GET", "OPTIONS"])
@cross_origin()
def get_stats():
    stat = RivalscanStat.get()
    return jsonify({"visits": stat.visits, "searches": stat.searches})


@rivalscan.route("/search", methods=["POST", "OPTIONS"])
@cross_origin()
def search():
    data  = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()

    if not query:
        return jsonify({"error": "Query is required"}), 400
    if len(query) > 250:
        return jsonify({"error": "Query too long (max 250 characters)"}), 400

    stat = RivalscanStat.get()
    stat.searches += 1
    db.session.commit()

    try:
        raw = call_mistral(build_prompt(query))
    except requests.exceptions.Timeout:
        return jsonify({"error": "AI service timed out. Please try again."}), 504
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": f"AI service error: {e.response.status_code}"}), 502
    except ValueError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

    try:
        competitors = json.loads(strip_fences(raw))
    except json.JSONDecodeError:
        return jsonify({"error": "Could not parse AI response", "raw": raw[:500]}), 500

    if not isinstance(competitors, list):
        return jsonify({"error": "Unexpected AI response format"}), 500

    return jsonify({
        "query":       query,
        "competitors": competitors,
        "total":       len(competitors),
        "timestamp":   datetime.utcnow().isoformat() + "Z",
    })
