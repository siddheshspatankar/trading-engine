"""
Risk Advisor Agent.

Provides AI-powered qualitative risk assessment that complements the
deterministic risk engine. Identifies tail risks, regime changes,
and contextual factors that quantitative limits cannot model.

IMPORTANT: This agent provides ADVISORY input only. The deterministic risk
engine has final authority on all order authorization decisions.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.exceptions import AgentResponseInvalid
from src.core.models import Portfolio, Signal
from src.infrastructure.config import get_settings

log = structlog.get_logger(__name__)


class RiskAssessment(BaseModel):
    """Structured output from the Risk Advisor Agent."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    overall_risk_level: str = Field(description="low | medium | high | critical")
    risk_score: float = Field(ge=0.0, le=1.0, description="0 = minimal risk, 1 = extreme risk")
    concerns: list[str] = Field(description="Specific risk concerns identified")
    tail_risks: list[str] = Field(description="Low-probability but high-impact risks")
    regime_assessment: str = Field(description="Current market regime characterization")
    position_guidance: dict[str, str] = Field(
        description="Per-symbol guidance: {symbol: reduce|hold|avoid|ok}"
    )
    recommended_max_exposure: float = Field(
        ge=0.0, le=1.0, description="Recommended max gross exposure as fraction of equity"
    )
    reasoning: str = Field(description="Concise risk reasoning")
    confidence: float = Field(ge=0.0, le=1.0)


class RiskAdvisorAgent:
    """
    LLM-powered risk advisor.

    Analyzes signals, portfolio, and market context to provide qualitative
    risk guidance. Advisory only — does not override deterministic limits.
    """

    SYSTEM_PROMPT = """You are a senior risk manager at a systematic trading fund.
Your role is to identify qualitative risk factors and tail risks that quantitative
models may miss, and provide structured advisory guidance.

You NEVER override hard risk limits — those are enforced by deterministic systems.
Your guidance is advisory and helps human oversight understand the risk landscape.
Be conservative. When in doubt, flag it.

Respond with valid JSON only."""

    def __init__(self, model: str | None = None) -> None:
        self._settings = get_settings().llm
        self._model = model or self._settings.openai_model
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI  # type: ignore

            self._client = AsyncOpenAI(
                api_key=self._settings.openai_api_key.get_secret_value()
            )
        return self._client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def assess_risk(
        self,
        signals: list[Signal],
        portfolio: Portfolio,
        market_context: str = "",
        sentiment_context: str = "",
    ) -> RiskAssessment:
        """
        Assess risk of proposed signals given current portfolio.

        Args:
            signals: Proposed trading signals from quantitative strategies
            portfolio: Current portfolio state
            market_context: Summary of current market conditions
            sentiment_context: Output from market intelligence agent

        Returns:
            Structured RiskAssessment.
        """
        client = self._get_client()

        # Summarize signals and portfolio for the prompt
        signals_summary = json.dumps(
            [
                {
                    "symbol": s.symbol,
                    "direction": s.direction.value,
                    "strength": s.strength,
                    "source": s.source,
                }
                for s in signals
            ],
            indent=2,
        )

        positions_summary = json.dumps(
            {
                sym: {
                    "quantity": float(pos.quantity),
                    "avg_cost": float(pos.average_cost),
                    "unrealized_pnl_pct": pos.unrealized_pnl_pct,
                }
                for sym, pos in portfolio.positions.items()
            },
            indent=2,
        )

        user_prompt = f"""Assess the risk of the following trading signals given the current portfolio.

## Proposed Signals
{signals_summary}

## Current Portfolio
Cash: ${float(portfolio.cash):,.2f}
Total Equity: ${float(portfolio.total_equity or 0):,.2f}
Positions:
{positions_summary}

## Market Context
{market_context or "No market context provided."}

## Sentiment Context
{sentiment_context or "No sentiment context provided."}

Provide structured risk assessment as JSON:
{{
  "overall_risk_level": "low|medium|high|critical",
  "risk_score": <0.0 to 1.0>,
  "concerns": ["...", "..."],
  "tail_risks": ["...", "..."],
  "regime_assessment": "...",
  "position_guidance": {{"SYMBOL": "reduce|hold|avoid|ok"}},
  "recommended_max_exposure": <0.0 to 1.0>,
  "reasoning": "...",
  "confidence": <0.0 to 1.0>
}}"""

        try:
            response = await client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=800,
                response_format={"type": "json_object"},
            )

            raw = response.choices[0].message.content
            if not raw:
                raise AgentResponseInvalid("Risk Advisor returned empty response")

            assessment = RiskAssessment(**json.loads(raw))

            log.info(
                "risk_advisor_assessment_complete",
                risk_level=assessment.overall_risk_level,
                risk_score=assessment.risk_score,
                concerns_count=len(assessment.concerns),
                confidence=assessment.confidence,
            )
            return assessment

        except json.JSONDecodeError as exc:
            raise AgentResponseInvalid(f"Risk Advisor returned invalid JSON: {exc}") from exc
        except Exception as exc:
            raise AgentResponseInvalid(f"Risk assessment failed: {exc}") from exc
