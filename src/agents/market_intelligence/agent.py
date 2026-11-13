"""
Market Intelligence Agent.

Uses an LLM to analyze market conditions, news sentiment, macro factors,
and provide qualitative context that quantitative signals cannot capture.

The agent RECOMMENDS — it does not authorize. All recommendations must pass
through the risk engine before orders are placed.

Output schema is structured JSON validated by Pydantic.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.exceptions import AgentResponseInvalid, AgentTimeoutError
from src.infrastructure.config import get_settings

log = structlog.get_logger(__name__)


class MarketSentiment(BaseModel):
    """Structured output from the Market Intelligence Agent."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    overall_sentiment: str = Field(description="bullish | bearish | neutral | uncertain")
    sentiment_score: float = Field(ge=-1.0, le=1.0, description="-1 = very bearish, +1 = very bullish")
    key_themes: list[str] = Field(description="Top 3-5 market themes driving sentiment")
    sector_views: dict[str, str] = Field(
        description="Per-sector sentiment: {sector: bullish|bearish|neutral}"
    )
    macro_factors: list[str] = Field(description="Key macro tailwinds / headwinds")
    risk_flags: list[str] = Field(description="Specific risks the quant models may not capture")
    confidence: float = Field(ge=0.0, le=1.0, description="Agent's confidence in this analysis")
    reasoning: str = Field(description="Concise explanation of the analysis")


class MarketIntelligenceAgent:
    """
    LLM-powered market intelligence agent.

    Consumes news headlines, macro data, and market conditions to produce
    structured sentiment analysis and qualitative insights.

    This agent NEVER directly influences order sizing — it provides context
    that the orchestrator and risk advisor consider alongside quantitative signals.
    """

    SYSTEM_PROMPT = """You are a senior quantitative analyst and market intelligence expert.
Your role is to analyze current market conditions, news, and macro factors to provide
structured insights that complement quantitative trading signals.

You must respond with a valid JSON object matching the MarketSentiment schema.
Be objective, cite specific factors, and clearly distinguish between facts and inferences.
Never recommend specific position sizes or override risk limits.
Your analysis is advisory only — final trading decisions are made by deterministic systems."""

    def __init__(self, model: str | None = None) -> None:
        self._settings = get_settings().llm
        self._model = model or self._settings.openai_model
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy-initialize the OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI  # type: ignore

                self._client = AsyncOpenAI(
                    api_key=self._settings.openai_api_key.get_secret_value()
                )
            except ImportError as exc:
                raise ImportError("openai package not installed: pip install openai") from exc
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def analyze_market(
        self,
        news_headlines: list[str],
        symbols: list[str],
        macro_context: str = "",
        recent_market_summary: str = "",
    ) -> MarketSentiment:
        """
        Run market intelligence analysis.

        Args:
            news_headlines: Recent relevant news headlines (max 20)
            symbols: Symbols in the trading universe
            macro_context: Optional macro data summary (rates, GDP, etc.)
            recent_market_summary: Optional summary of recent price action

        Returns:
            Structured MarketSentiment analysis.

        Raises:
            AgentResponseInvalid: If the LLM returns malformed output.
            AgentTimeoutError: If the LLM call times out.
        """
        client = self._get_client()

        # Build structured prompt
        headlines_block = "\n".join(f"- {h}" for h in news_headlines[:20])
        symbols_str = ", ".join(symbols)

        user_prompt = f"""Analyze current market conditions for the following trading universe: {symbols_str}

## Recent News Headlines
{headlines_block}

## Macro Context
{macro_context or "No macro data provided."}

## Recent Market Action
{recent_market_summary or "No market summary provided."}

Provide your analysis as a JSON object with these fields:
- overall_sentiment: "bullish" | "bearish" | "neutral" | "uncertain"
- sentiment_score: float from -1.0 (very bearish) to +1.0 (very bullish)
- key_themes: list of 3-5 dominant market themes
- sector_views: dict of sector -> sentiment
- macro_factors: list of key macro tailwinds/headwinds
- risk_flags: list of specific risks quantitative models may miss
- confidence: float 0.0-1.0 (your confidence in this analysis)
- reasoning: 2-3 sentence explanation

Respond with ONLY valid JSON. No markdown, no code blocks."""

        try:
            response = await client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,  # Low temperature for consistent, analytical output
                max_tokens=1000,
                response_format={"type": "json_object"},
            )

            raw_output = response.choices[0].message.content
            if not raw_output:
                raise AgentResponseInvalid("Market Intelligence Agent returned empty response")

            parsed = json.loads(raw_output)
            sentiment = MarketSentiment(**parsed)

            log.info(
                "market_intelligence_analysis_complete",
                overall=sentiment.overall_sentiment,
                score=sentiment.sentiment_score,
                confidence=sentiment.confidence,
                risk_flags_count=len(sentiment.risk_flags),
            )
            return sentiment

        except json.JSONDecodeError as exc:
            raise AgentResponseInvalid(f"Agent returned invalid JSON: {exc}") from exc
        except Exception as exc:
            if "timeout" in str(exc).lower():
                raise AgentTimeoutError(f"Market Intelligence Agent timed out: {exc}") from exc
            raise AgentResponseInvalid(f"Agent analysis failed: {exc}") from exc

    async def assess_symbol_sentiment(
        self, symbol: str, news: list[str]
    ) -> dict[str, Any]:
        """
        Quick single-symbol sentiment assessment.

        Returns a simple dict with keys: sentiment, score, key_points, confidence.
        """
        client = self._get_client()

        headlines = "\n".join(f"- {h}" for h in news[:10])
        prompt = f"""Assess news sentiment for {symbol}:
{headlines}

Respond JSON only:
{{"sentiment": "bullish|bearish|neutral", "score": <-1.0 to 1.0>, "key_points": ["..."], "confidence": <0.0 to 1.0>}}"""

        try:
            response = await client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You are a financial news analyst. Respond with valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=200,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:
            log.warning("symbol_sentiment_failed", symbol=symbol, error=str(exc))
            return {"sentiment": "neutral", "score": 0.0, "key_points": [], "confidence": 0.0}
