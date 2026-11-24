"""Optional Gemini narrative pass over deterministic outputs; never a source of logistics facts."""
from __future__ import annotations
import json, os

class GeminiNarrator:
    def __init__(self):
        self.enabled=os.getenv("GEMINI_ENABLED","false").strip().lower()=="true"
        self.api_key=os.getenv("GEMINI_API_KEY")
        self.model=os.getenv("GEMINI_MODEL","gemini-2.5-flash-lite")
    def narrate(self,agent_results:dict)->dict:
        if not self.enabled: return {"status":"disabled","message":"Offline deterministic mode; Gemini is disabled."}
        if not self.api_key: return {"status":"unavailable","message":"Gemini is enabled but GEMINI_API_KEY is not configured."}
        try:
            from google import genai
            from google.genai import types
            client=genai.Client(api_key=self.api_key,http_options=types.HttpOptions(timeout=int(os.getenv("GEMINI_TIMEOUT_MS","5000"))))
            prompt=("Summarize this supplied JSON in at most 3 concise sentences. Use only facts and numbers present in it. "
                    "Do not infer live status, current prices, legal compliance, bookings, or missing values. "
                    "If information is unavailable, say so. Return plain text only.\nTOOLS_JSON="+json.dumps(agent_results,default=str))
            response=client.models.generate_content(model=self.model,contents=prompt,config=types.GenerateContentConfig(temperature=0,max_output_tokens=180))
            text=(response.text or "").strip()
            if not text: return {"status":"unavailable","message":"Gemini returned no narrative; deterministic results remain available."}
            return {"status":"completed","model":self.model,"narrative":text,"provenance":"MODEL_SUMMARY_OF_TOOL_OUTPUTS"}
        except Exception as exc:
            # Exception text may include request details; do not expose it or leak credentials.
            status="quota_or_rate_limited" if any(x in type(exc).__name__.casefold() for x in ("resourceexhausted","ratelimit","quota")) else "unavailable"
            return {"status":status,"message":"Gemini narrative unavailable; deterministic results remain available."}
