import os
import logging
from typing import Optional
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from core.models import VerificationResult
from core.pipeline import FreshGatePipeline

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("freshgate")

app = FastAPI(
    title="FreshGate AI Gateway Plugin & Verifier",
    description="Automatic fact-freshness verification for AI Gateways with Tavily and LLM Judge",
    version="1.0.0",
)

pipeline = FreshGatePipeline()


class VerifyRequest(BaseModel):
    answer: str


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "freshgate"}


@app.post("/verify", response_model=VerificationResult)
async def verify_answer(req: VerifyRequest):
    """Verify factual claims in an LLM answer text."""
    try:
        result = await pipeline.verify(req.answer)
        return result
    except Exception as e:
        logger.error("Verification failed unexpectedly: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# Mount dashboard directory if it exists
dashboard_dir = Path(__file__).resolve().parent.parent / "dashboard"
if dashboard_dir.exists():
    app.mount("/static", StaticFiles(directory=str(dashboard_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def get_dashboard():
        index_file = dashboard_dir / "index.html"
        if index_file.exists():
            return index_file.read_text(encoding="utf-8")
        return "<h1>FreshGate Gateway</h1><p>Dashboard index.html not found</p>"
