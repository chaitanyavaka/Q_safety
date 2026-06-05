from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from statistics import mean, median
from typing import Any


DEMAND_FACTORS = {
    "low": -0.04,
    "stable": 0.0,
    "rising": 0.03,
    "high": 0.055,
}

STRATEGY_FACTORS = {
    "balanced": 0.0,
    "protect_margin": 0.02,
    "win_share": -0.018,
    "clear_inventory": -0.035,
}

AUTO_APPROVE_MAX_CHANGE = 0.03
MEDIUM_RISK_MAX_CHANGE = 0.05
STOP_TOKENS = {"the", "and", "with", "for", "of", "by", "series"}


@dataclass(frozen=True)
class AgentInputs:
    sku: str
    demand: str = "stable"
    strategy: str = "balanced"
    demand_units: int | None = None
    stock_override: int | None = None
    competitor_override: list[dict[str, Any]] | None = None
    market_override_applied: bool = False
    excluded_competitors: list[dict[str, Any]] | None = None
    live_candidate_count: int = 0
    market_mode: str = "sample"
    market_provider: str = "sample"
    market_message: str = "Using sample competitor data."


class QuestPricingAgent:
    """Rule-and-market pricing agent for Quest Safety catalog recommendations."""

    agent_name = "Quest Price Intelligence Agent"
    agent_version = "QPIA-1.0"

    def __init__(self, catalog: dict[str, Any]):
        self.catalog = catalog
        self.products = catalog.get("products", [])

    def catalog_summary(self) -> dict[str, Any]:
        return {
            "currency": self.catalog.get("currency", "USD"),
            "sources": self.catalog.get("sources", []),
            "products": self.products,
            "agents": self.agent_stack(),
        }

    def find_product(self, sku: str) -> dict[str, Any] | None:
        return self._find_product(sku)

    def agent_stack(self) -> list[dict[str, str]]:
        return [
            {
                "name": "Catalog Matcher Agent",
                "basis": "SKU, MPN, brand, product title, category, UOM, Quest current price, cost, stock, and source URL.",
            },
            {
                "name": "Market Intelligence Agent",
                "basis": "Competitor prices, stock state, freshness hours, seller/source, and match score.",
            },
            {
                "name": "Demand Signal Agent",
                "basis": "User demand input plus existing category demand signal. Low, stable, rising, and high demand adjust the price factor.",
            },
            {
                "name": "Margin Guardrail Agent",
                "basis": "Product cost and minimum margin. The recommended price cannot go below the margin-safe floor.",
            },
            {
                "name": "Risk Router Agent",
                "basis": "Confidence score, price movement size, inventory/supply flags, and data quality flags.",
            },
            {
                "name": "Approval Agent",
                "basis": "Low risk routes to auto approval, medium risk to bulk review, and high risk to exception review.",
            },
        ]

    def analyze(self, inputs: AgentInputs) -> dict[str, Any]:
        product = self._find_product(inputs.sku)
        if product is None:
            return self._unknown_sku_response(inputs)

        demand = self._resolve_demand_band(inputs, product)
        strategy = inputs.strategy if inputs.strategy in STRATEGY_FACTORS else "balanced"
        stock = int(inputs.stock_override if inputs.stock_override is not None else product.get("stock", 0))
        pricing_source = "live" if inputs.market_override_applied else "sample"
        competitors = self._display_competitors(
            product,
            inputs.competitor_override if inputs.market_override_applied else product.get("competitors", []),
            pricing_source,
        )

        market = self._market_metrics(competitors)
        current_price = float(product["current_price"])
        cost = float(product["cost"])
        min_margin = float(product.get("min_margin", 0.25))
        cost_floor = self._round_money(cost / (1 - min_margin))

        demand_factor = DEMAND_FACTORS[demand]
        inventory_factor = self._inventory_factor(stock, product.get("supply", "good"))
        strategy_factor = STRATEGY_FACTORS[strategy]
        volatility_penalty = self._volatility_penalty(market)
        freshness_penalty = self._freshness_penalty(competitors)
        match_penalty = self._match_penalty(competitors)

        market_anchor = market["median"]
        raw_price = market_anchor * (1 + demand_factor + inventory_factor + strategy_factor)
        recommended_price = self._round_money(max(raw_price, cost_floor))
        price_change_pct = (recommended_price - current_price) / current_price
        projected_margin = (recommended_price - cost) / recommended_price

        confidence = self._confidence_score(
            market=market,
            competitors=competitors,
            price_change_pct=price_change_pct,
            freshness_penalty=freshness_penalty,
            match_penalty=match_penalty,
            volatility_penalty=volatility_penalty,
        )
        policy_flags = self._policy_flags(product, stock, projected_margin, min_margin, confidence)
        risk, route = self._risk_route(confidence, price_change_pct, policy_flags)

        return {
            "agent": {
                "name": self.agent_name,
                "version": self.agent_version,
                "basis": [
                    "Quest catalog price and cost",
                    "Competitor median, average, min, max, freshness, and match score",
                    "User demand input",
                    "Inventory and supply pressure",
                    "Pricing strategy",
                    "Minimum margin guardrail",
                    "Confidence and approval risk thresholds",
                ],
                "formula": {
                    "market_anchor": "median(competitor_prices)",
                    "raw_price": "market_anchor * (1 + demand_factor + inventory_factor + strategy_factor)",
                    "margin_floor": "cost / (1 - minimum_margin)",
                    "recommended_price": "max(raw_price, margin_floor)",
                    "risk": "confidence + price_change_pct + policy_flags",
                },
            },
            "input": {
                "sku": inputs.sku,
                "demand": demand,
                "demand_units": inputs.demand_units,
                "strategy": strategy,
                "stock": stock,
            },
            "market_data": {
                "mode": inputs.market_mode,
                "provider": inputs.market_provider,
                "message": inputs.market_message,
                "live_count": inputs.live_candidate_count or len(inputs.competitor_override or []),
                "excluded_count": len(inputs.excluded_competitors or []),
                "pricing_count": len(competitors),
                "pricing_source": pricing_source,
            },
            "product": product,
            "competitors": competitors,
            "excluded_competitors": inputs.excluded_competitors or [],
            "market": market,
            "calculation": {
                "current_price": current_price,
                "cost": cost,
                "minimum_margin": min_margin,
                "cost_floor": cost_floor,
                "market_anchor": market_anchor,
                "demand_factor": demand_factor,
                "inventory_factor": inventory_factor,
                "strategy_factor": strategy_factor,
                "raw_price": self._round_money(raw_price),
                "recommended_price": recommended_price,
                "price_change_pct": round(price_change_pct, 4),
                "projected_margin": round(projected_margin, 4),
                "confidence": confidence,
                "risk": risk,
                "route": route,
                "policy_flags": policy_flags,
            },
            "preview_requirements": self._preview_requirements(product, risk, route),
            "reasoning": self._reasoning(
                product=product,
                market=market,
                demand=demand,
                stock=stock,
                recommended_price=recommended_price,
                price_change_pct=price_change_pct,
                confidence=confidence,
                policy_flags=policy_flags,
            ),
            "steps": [
                "Matched SKU to Quest catalog record.",
                "Read competitor market prices and normalized all values to USD.",
                "Calculated market min, max, average, and median.",
                "Applied user demand input and inventory pressure.",
                "Applied strategy factor and margin floor.",
                "Scored confidence using match quality, freshness, volatility, and price movement.",
                "Routed the result to auto approval, bulk review, or exception review.",
            ],
        }

    def _find_product(self, sku: str) -> dict[str, Any] | None:
        normalized = self._normalize_lookup(sku)
        if not normalized:
            return None

        exact_match = next(
            (
                product
                for product in self.products
                if self._normalize_lookup(product["sku"]) == normalized
                or self._normalize_lookup(product.get("mpn", "")) == normalized
            ),
            None,
        )
        if exact_match:
            return exact_match

        exact_name_match = next(
            (
                product
                for product in self.products
                if self._normalize_lookup(product.get("product", "")) == normalized
            ),
            None,
        )
        if exact_name_match:
            return exact_name_match

        name_matches = [
            product
            for product in self.products
            if normalized in self._normalize_lookup(product.get("product", ""))
        ]
        if len(name_matches) == 1:
            return name_matches[0]

        scored_products = [
            (self._lookup_score(normalized, product), product)
            for product in self.products
        ]
        scored_products.sort(key=lambda item: item[0], reverse=True)
        best_score, best_product = scored_products[0]
        second_score = scored_products[1][0] if len(scored_products) > 1 else 0.0
        if best_score >= 0.72 and (best_score - second_score >= 0.08 or best_score >= 0.86):
            return best_product

        return None

    @staticmethod
    def _resolve_demand_band(inputs: AgentInputs, product: dict[str, Any]) -> str:
        if isinstance(inputs.demand_units, int):
            if inputs.demand_units <= 10:
                return "low"
            if inputs.demand_units <= 50:
                return "stable"
            if inputs.demand_units <= 150:
                return "rising"
            return "high"
        if inputs.demand in DEMAND_FACTORS:
            return inputs.demand
        return product.get("base_demand", "stable")

    def _unknown_sku_response(self, inputs: AgentInputs) -> dict[str, Any]:
        return {
            "agent": {
                "name": self.agent_name,
                "version": self.agent_version,
            },
            "input": {
                "sku": inputs.sku,
                "demand": inputs.demand,
                "demand_units": inputs.demand_units,
                "strategy": inputs.strategy,
                "stock": inputs.stock_override,
            },
            "market_data": {
                "mode": inputs.market_mode,
                "provider": inputs.market_provider,
                "message": inputs.market_message,
                "live_count": inputs.live_candidate_count or len(inputs.competitor_override or []),
                "excluded_count": len(inputs.excluded_competitors or []),
                "pricing_count": 0,
                "pricing_source": "none",
            },
            "product": None,
            "calculation": {
                "risk": "high",
                "route": "Exception review",
                "confidence": 0,
                "policy_flags": ["SKU not found in current Quest catalog dataset"],
            },
            "preview_requirements": [
                "Add SKU, MPN, brand, category, UOM, current price, cost, inventory, and source URL.",
                "Collect competitor prices from approved sources.",
                "Validate unit of measure before generating a price recommendation.",
            ],
            "reasoning": [
                "The SKU could not be matched to the current local Quest catalog sample.",
                "The agent cannot calculate a price without catalog cost and market observations.",
            ],
            "steps": ["SKU lookup failed. Send this item to exception review or add it to the catalog dataset."],
        }

    @staticmethod
    def _market_metrics(competitors: list[dict[str, Any]]) -> dict[str, Any]:
        prices = [float(item.get("normalized_price", item["price"])) for item in competitors]
        if not prices:
            return {"min": 0, "max": 0, "average": 0, "median": 0, "spread_pct": 0, "count": 0}
        market_min = min(prices)
        market_max = max(prices)
        market_avg = mean(prices)
        market_median = median(prices)
        spread_pct = 0 if market_median == 0 else (market_max - market_min) / market_median
        return {
            "min": round(market_min, 2),
            "max": round(market_max, 2),
            "average": round(market_avg, 2),
            "median": round(market_median, 2),
            "spread_pct": round(spread_pct, 4),
            "count": len(prices),
        }

    @staticmethod
    def _display_competitors(
        product: dict[str, Any],
        competitors: list[dict[str, Any]],
        pricing_source: str,
    ) -> list[dict[str, Any]]:
        rows = []
        for item in competitors:
            row = dict(item)
            row.setdefault("title", product.get("product", ""))
            row.setdefault("offer_sku", product.get("mpn") or product.get("sku", ""))
            row.setdefault("normalized_price", row.get("price"))
            row.setdefault("normalized_uom", product.get("uom", "EA"))
            row.setdefault("package_quantity", 1)
            row.setdefault("package_type", str(product.get("uom", "EA")).lower())
            row.setdefault("comparable", True)
            row.setdefault("data_source", pricing_source)
            rows.append(row)
        rows.sort(
            key=lambda row: (
                -float(row.get("normalized_price", row.get("price", 0)) or 0),
                -float(row.get("match_score", 0) or 0),
            )
        )
        return rows

    @staticmethod
    def _inventory_factor(stock: int, supply: str) -> float:
        supply = supply.lower()
        if stock <= 10 or supply == "constrained":
            return 0.04
        if stock <= 25 or supply == "limited":
            return 0.02
        if stock >= 200:
            return -0.02
        return 0.0

    @staticmethod
    def _confidence_score(
        market: dict[str, Any],
        competitors: list[dict[str, Any]],
        price_change_pct: float,
        freshness_penalty: int,
        match_penalty: int,
        volatility_penalty: int,
    ) -> int:
        if not competitors:
            return 20
        source_score = min(20, len(competitors) * 5)
        movement_penalty = min(20, int(abs(price_change_pct) * 100))
        base_score = 96 + source_score - freshness_penalty - match_penalty - volatility_penalty - movement_penalty
        if market["count"] < 3:
            base_score -= 12
        return max(0, min(98, int(round(base_score))))

    @staticmethod
    def _freshness_penalty(competitors: list[dict[str, Any]]) -> int:
        if not competitors:
            return 30
        average_freshness = mean(float(item.get("freshness_hours", 48)) for item in competitors)
        if average_freshness <= 12:
            return 0
        if average_freshness <= 24:
            return 5
        if average_freshness <= 36:
            return 10
        return 16

    @staticmethod
    def _match_penalty(competitors: list[dict[str, Any]]) -> int:
        if not competitors:
            return 30
        average_match = mean(float(item.get("match_score", 0.5)) for item in competitors)
        if average_match >= 0.9:
            return 0
        if average_match >= 0.82:
            return 5
        if average_match >= 0.74:
            return 10
        return 16

    @staticmethod
    def _volatility_penalty(market: dict[str, Any]) -> int:
        spread = market.get("spread_pct", 0)
        if spread <= 0.08:
            return 0
        if spread <= 0.14:
            return 4
        if spread <= 0.22:
            return 8
        return 14

    @staticmethod
    def _policy_flags(product: dict[str, Any], stock: int, projected_margin: float, min_margin: float, confidence: int) -> list[str]:
        flags = []
        if stock <= 10:
            flags.append("Low stock or constrained supply")
        if projected_margin < min_margin:
            flags.append("Projected margin below minimum")
        if confidence < 65:
            flags.append("Low confidence")
        if product.get("category", "").lower() in {"powered air purifying respirators", "respirator hoods"}:
            flags.append("Safety-critical respiratory category")
        return flags

    @staticmethod
    def _risk_route(confidence: int, price_change_pct: float, policy_flags: list[str]) -> tuple[str, str]:
        movement = abs(price_change_pct)
        if movement <= AUTO_APPROVE_MAX_CHANGE:
            return "low", "Auto approve"
        if movement <= MEDIUM_RISK_MAX_CHANGE:
            return "medium", "Bulk review"
        return "high", "Exception review"

    @staticmethod
    def _normalize_lookup(value: Any) -> str:
        text = str(value or "").lower().strip()
        text = text.replace("&", " and ")
        text = re.sub(r"(\d)(ft|in|oz|lb|mm|cm)\b", r"\1 \2", text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _normalized_tokens(value: Any) -> set[str]:
        tokens = []
        for token in QuestPricingAgent._normalize_lookup(value).split():
            if token in STOP_TOKENS:
                continue
            if len(token) > 3 and token.endswith("s"):
                token = token[:-1]
            tokens.append(token)
        return set(tokens)

    @classmethod
    def _lookup_score(cls, normalized_lookup: str, product: dict[str, Any]) -> float:
        name = cls._normalize_lookup(product.get("product", ""))
        sku = cls._normalize_lookup(product.get("sku", ""))
        mpn = cls._normalize_lookup(product.get("mpn", ""))

        if normalized_lookup in {name, sku, mpn}:
            return 1.0

        lookup_tokens = cls._normalized_tokens(normalized_lookup)
        product_tokens = cls._normalized_tokens(product.get("product", ""))
        token_overlap = 0.0
        if lookup_tokens and product_tokens:
            token_overlap = len(lookup_tokens & product_tokens) / len(lookup_tokens | product_tokens)

        sequence_score = max(
            SequenceMatcher(None, normalized_lookup, name).ratio(),
            SequenceMatcher(None, normalized_lookup, sku).ratio(),
            SequenceMatcher(None, normalized_lookup, mpn).ratio(),
        )

        contains_bonus = 0.08 if normalized_lookup and normalized_lookup in name else 0.0
        return round(sequence_score * 0.62 + token_overlap * 0.38 + contains_bonus, 4)

    @staticmethod
    def _preview_requirements(product: dict[str, Any], risk: str, route: str) -> list[str]:
        requirements = [
            "Show SKU, MPN, brand, category, UOM, current Quest price, cost, stock, and source URL.",
            "Show competitor market min, max, average, median, freshness, stock state, and match score.",
            "Show demand input, strategy input, margin floor, recommended price, confidence, risk, and route.",
            "Require audit fields: old price, new price, who approved, why, when, and model version.",
        ]
        if risk == "high":
            requirements.append("Require individual human exception review before publishing.")
        elif route == "Bulk review":
            requirements.append("Allow grouped approval by category, manufacturer, or price movement band.")
        else:
            requirements.append("Auto publish can be allowed after audit logging and margin guardrail validation.")
        if product.get("source_url"):
            requirements.append("Keep the Quest product page link attached to the recommendation.")
        return requirements

    @staticmethod
    def _reasoning(
        product: dict[str, Any],
        market: dict[str, Any],
        demand: str,
        stock: int,
        recommended_price: float,
        price_change_pct: float,
        confidence: int,
        policy_flags: list[str],
    ) -> list[str]:
        reasons = [
            f"Normalized comparable market median is ${market['median']:.2f}, compared with current Quest price ${product['current_price']:.2f}.",
            f"Demand input is {demand}, and available stock is {stock} {product['uom']}.",
            f"Recommended price is ${recommended_price:.2f}, a {price_change_pct * 100:+.1f}% movement.",
            f"Confidence is {confidence}% after source freshness, match quality, volatility, and movement checks.",
        ]
        if policy_flags:
            reasons.append("Policy flags: " + "; ".join(policy_flags) + ".")
        return reasons

    @staticmethod
    def _round_money(value: float) -> float:
        return round(value + 1e-9, 2)
