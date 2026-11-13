"""
Multi-Agent Orchestrator.

Coordinates the flow between:
1. Quantitative strategy signals
2. Market Intelligence Agent (qualitative context)
3. Risk Advisor Agent (advisory risk assessment)
4. Deterministic Risk Engine (authorization gate)
5. Execution Engine (order placement)

The orchestrator follows the principle:
  - AI agents RECOMMEND and provide CONTEXT
  - Deterministic systems AUTHORIZE
  - Execution services EXECUTE
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import structlog

from src.agents.market_intelligence.agent import MarketIntelligenceAgent, MarketSentiment
from src.agents.risk_advisor.agent import RiskAdvisorAgent, RiskAssessment
from src.core.events import SignalGenerated, event_bus
from src.core.models import Portfolio, Signal, SignalDirection
from src.infrastructure.config import get_settings

log = structlog.get_logger(__name__)


class OrchestratorResult:
    """Result returned by the orchestrator after a full cycle."""

    def __init__(
        self,
        signals: list[Signal],
        market_sentiment: MarketSentiment | None,
        risk_assessment: RiskAssessment | None,
        filtered_signals: list[Signal],
        cycle_duration_ms: float,
    ) -> None:
        self.signals = signals
        self.market_sentiment = market_sentiment
        self.risk_assessment = risk_assessment
        self.filtered_signals = filtered_signals  # signals after agent filtering
        self.cycle_duration_ms = cycle_duration_ms
        self.timestamp = datetime.utcnow()


class AgentOrchestrator:
    """
    Coordinates AI agents with quantitative strategy signals.

    Agent inputs are advisory. Signals are passed to the risk engine
    regardless — the risk engine has final authority.
    """

    def __init__(
        self,
        enable_market_intelligence: bool = True,
        enable_risk_advisor: bool = True,
    ) -> None:
        self._market_intel = MarketIntelligenceAgent() if enable_market_intelligence else None
        self._risk_advisor = RiskAdvisorAgent() if enable_risk_advisor else None
        self._settings = get_settings()

    async def run_cycle(
        self,
        signals: list[Signal],
        portfolio: Portfolio,
        news_headlines: list[str] | None = None,
        market_summary: str = "",
        macro_context: str = "",
    ) -> OrchestratorResult:
        """
        Run a full orchestration cycle.

        Steps:
        1. Gather market intelligence (async, non-blocking)
        2. Get risk advisor assessment
        3. Apply advisory filtering (high-risk signals get downgraded)
        4. Return filtered signals for the deterministic risk engine

        Args:
            signals: Raw signals from quantitative strategies
            portfolio: Current portfolio state
            news_headlines: Recent news for market intelligence agent
            market_summary: Summary of recent price action
            macro_context: Macro data context

        Returns:
            OrchestratorResult with all agent outputs and filtered signals.
        """
        start_time = asyncio.get_event_loop().time()

        log.info(
            "orchestration_cycle_start",
            signal_count=len(signals),
            symbols=[s.symbol for s in signals],
        )

        market_sentiment: MarketSentiment | None = None
        risk_assessment: RiskAssessment | None = None

        # Run market intelligence and risk advisor in parallel
        agent_tasks = []
        if self._market_intel and news_headlines:
            agent_tasks.append(
                self._run_market_intelligence(
                    news_headlines,
                    list({s.symbol for s in signals}),
                    macro_context,
                    market_summary,
                )
            )
        else:
            agent_tasks.append(asyncio.coroutine(lambda: None)())  # type: ignore

        if self._risk_advisor:
            agent_tasks.append(
                self._run_risk_advisor(signals, portfolio, market_summary)
            )
        else:
            agent_tasks.append(asyncio.coroutine(lambda: None)())  # type: ignore

        try:
            results = await asyncio.gather(*agent_tasks, return_exceptions=True)
            market_sentiment = results[0] if not isinstance(results[0], Exception) else None
            risk_assessment = results[1] if not isinstance(results[1], BaseException) else None

            if isinstance(results[0], Exception):
                log.warning("market_intel_failed", error=str(results[0]))
            if isinstance(results[1], BaseException):
                log.warning("risk_advisor_failed", error=str(results[1]))

        except Exception as exc:
            log.error("agent_orchestration_error", error=str(exc))

        # Apply advisory filtering based on agent outputs
        filtered_signals = self._apply_advisory_filters(
            signals, market_sentiment, risk_assessment
        )

        # Publish filtered signals to the event bus
        for signal in filtered_signals:
            await event_bus.publish(SignalGenerated(signal=signal))

        duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000

        log.info(
            "orchestration_cycle_complete",
            original_signal_count=len(signals),
            filtered_signal_count=len(filtered_signals),
            duration_ms=round(duration_ms, 2),
            risk_level=risk_assessment.overall_risk_level if risk_assessment else "unknown",
            sentiment=market_sentiment.overall_sentiment if market_sentiment else "unknown",
        )

        return OrchestratorResult(
            signals=signals,
            market_sentiment=market_sentiment,
            risk_assessment=risk_assessment,
            filtered_signals=filtered_signals,
            cycle_duration_ms=duration_ms,
        )

    async def _run_market_intelligence(
        self,
        headlines: list[str],
        symbols: list[str],
        macro_context: str,
        market_summary: str,
    ) -> MarketSentiment | None:
        """Run market intelligence agent with timeout."""
        try:
            return await asyncio.wait_for(
                self._market_intel.analyze_market(  # type: ignore
                    headlines, symbols, macro_context, market_summary
                ),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            log.warning("market_intel_timeout")
            return None

    async def _run_risk_advisor(
        self,
        signals: list[Signal],
        portfolio: Portfolio,
        market_context: str,
    ) -> RiskAssessment | None:
        """Run risk advisor agent with timeout."""
        try:
            return await asyncio.wait_for(
                self._risk_advisor.assess_risk(  # type: ignore
                    signals, portfolio, market_context
                ),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            log.warning("risk_advisor_timeout")
            return None

    def _apply_advisory_filters(
        self,
        signals: list[Signal],
        sentiment: MarketSentiment | None,
        risk_assessment: RiskAssessment | None,
    ) -> list[Signal]:
        """
        Apply advisory filters from AI agents to the signal list.

        Rules:
        - If risk level is "critical": drop all non-exit signals
        - If risk level is "high": reduce signal strength by 30%
        - Per-symbol guidance "avoid" → downgrade to FLAT
        - Sentiment strongly bearish (-0.7) → suppress new long entries
        """
        if not signals:
            return signals

        # Critical risk: only allow exits
        if risk_assessment and risk_assessment.overall_risk_level == "critical":
            log.warning(
                "critical_risk_level_filtering_non_exits",
                concerns=risk_assessment.concerns[:3],
            )
            return [s for s in signals if s.direction == SignalDirection.FLAT]

        filtered: list[Signal] = []
        for signal in signals:
            modified = signal

            # Per-symbol risk guidance
            if risk_assessment:
                guidance = risk_assessment.position_guidance.get(signal.symbol, "ok")

                if guidance == "avoid" and signal.direction != SignalDirection.FLAT:
                    log.info(
                        "signal_suppressed_risk_advisor",
                        symbol=signal.symbol,
                        guidance=guidance,
                    )
                    # Convert to FLAT (exit, don't enter)
                    modified = signal.model_copy(
                        update={
                            "direction": SignalDirection.FLAT,
                            "rationale": f"[Risk Advisor: avoid] {signal.rationale}",
                        }
                    )
                elif guidance == "reduce" and signal.direction == SignalDirection.LONG:
                    # Reduce conviction
                    modified = signal.model_copy(
                        update={
                            "strength": signal.strength * 0.5,
                            "rationale": f"[Risk Advisor: reduce] {signal.rationale}",
                        }
                    )

                # High risk: apply strength penalty
                if risk_assessment.overall_risk_level == "high":
                    modified = modified.model_copy(
                        update={"strength": modified.strength * 0.7}
                    )

            # Sentiment filter: suppress new longs in very bearish environment
            if (
                sentiment
                and sentiment.sentiment_score < -0.7
                and signal.direction == SignalDirection.LONG
                and signal.strength < 0.8  # keep very strong signals
            ):
                log.info(
                    "signal_suppressed_bearish_sentiment",
                    symbol=signal.symbol,
                    sentiment_score=sentiment.sentiment_score,
                )
                modified = modified.model_copy(
                    update={
                        "strength": modified.strength * 0.4,
                        "rationale": f"[Sentiment: bearish {sentiment.sentiment_score:.2f}] {modified.rationale}",
                    }
                )

            filtered.append(modified)

        return filtered
