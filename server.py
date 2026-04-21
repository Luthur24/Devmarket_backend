# app.py
import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Allow frontend to call this API

# 🔒 Use environment variable or replace with your key (keep secret in production)
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "yjvknUyDmAP6SKLQAUtqM5FH65cP69Id")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MODEL = "mistral-small-latest"

def build_prompt(query):
    return f"""You are an elite competitive intelligence analyst. Research competitors for: "{query}".

Return ONLY a valid JSON object with this exact structure (no markdown, no extra text):
{{
  "summary": "Executive summary of competitive landscape in 2-3 sentences.",
  "competitors": [
    {{
      "name": "Competitor name",
      "tagline": "Short tagline",
      "description": "Brief description of what they do",
      "category": "Industry category",
      "founded": "Year founded or 'N/A'",
      "hq": "Headquarters location",
      "threat": "High|Medium|Low",
      "threatReason": "Why this threat level",
      "activityLevel": 85,
      "activityNote": "Recent activity summary",
      "similarityScore": 90,
      "similarityNote": "Why they are similar",
      "marketShare": "Estimated market share (e.g., '15%')",
      "fundingStage": "Seed, Series A, etc.",
      "employees": "Estimated employee count",
      "recentMove": "Recent strategic move",
      "tags": ["tag1", "tag2", "tag3"],
      "emoji": "🔷",
      "trendDirection": "up|down|stable",
      "estimatedRevenue": "$10M-$50M",
      "valuation": "$500M"
    }}
  ],
  "newTrends": [
    {{ "title": "Trend title", "description": "Description", "urgency": "High|Medium|Low" }}
  ],
  "sources": [
    {{ "name": "Source name", "type": "News/Blog/Financial/etc", "description": "What this source provides", "reliability": "High|Medium", "emoji": "🔗" }}
  ],
  "financialSummary": "Brief financial insight on the sector and key players."
}}

Generate 4-6 competitors, 4-5 newTrends, 5-6 sources.
- threat must be exactly "High", "Medium", or "Low"
- trendDirection: "up", "down", or "stable"
- urgency: "High", "Medium", or "Low"
- reliability: "High" or "Medium"
- activityLevel and similarityScore: integers 1-100

Be realistic, insightful, and concise."""

@app.route('/api/search', methods=['POST'])
def search():
    data = request.get_json()
    query = data.get('query', '').strip()
    if not query:
        return jsonify({'error': 'Query is required'}), 400

    prompt = build_prompt(query)

    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a competitive intelligence assistant. Respond only with valid JSON, no markdown formatting."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 4000
    }

    try:
        resp = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        content = result['choices'][0]['message']['content']

        # Clean any accidental markdown code fences
        content = content.strip()
        if content.startswith('```'):
            content = content.split('```')[1]
            if content.startswith('json'):
                content = content[4:]
        content = content.strip()

        import json
        parsed = json.loads(content)
        return jsonify(parsed)

    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'API request failed: {str(e)}'}), 500
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Invalid JSON from AI: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)