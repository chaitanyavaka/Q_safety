from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import requests


COMPETITOR_HINTS = ("amazon", "grainger", "zoro", "fastenal", "uline")
SIZE_TOKENS = ("xs", "s", "m", "l", "xl", "2xl", "3xl", "4xl", "small", "medium", "large")
COLOR_TOKENS = (
    "black",
    "white",
    "blue",
    "clear",
    "gray",
    "grey",
    "green",
    "orange",
    "red",
    "yellow",
)


@dataclass(frozen=True)
class MarketFetchResult:
    status: str
    provider: str
    query: str
    observations: list[dict[str, Any]]
    message: str
    rejected_observations: list[dict[str, Any]] = field(default_factory=list)


class LiveMarketDataService:
    """Fetch live competitor price observations from approved API providers."""

    provider_name = "SerpAPI Google Shopping"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY")
        self.location = os.getenv("SERPAPI_LOCATION", "United States")

    def fetch_for_product(self, sku: str, product: dict[str, Any] | None) -> MarketFetchResult:
        queries = self._queries_for_product(sku, product)
        query = queries[0]
        if not self.api_key:
            return MarketFetchResult(
                status="not_configured",
                provider=self.provider_name,
                query=query,
                observations=[],
                message="Live market API key is not configured, so the backend used sample competitor data.",
                rejected_observations=[],
            )

        try:
            observations: list[dict[str, Any]] = []
            rejected: list[dict[str, Any]] = []
            executed_queries: list[str] = []
            seen_keys: set[tuple[str, float, str]] = set()

            for item_query in queries:
                payload = self._search_google_shopping(item_query)
                executed_queries.append(item_query)
                parsed_observations, parsed_rejected = self._parse_google_shopping(payload, sku, product)

                for row in parsed_observations:
                    key = (str(row.get("source", "")).lower(), float(row.get("price", 0) or 0), str(row.get("title", "")).lower())
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    observations.append(row)

                for row in parsed_rejected:
                    key = (str(row.get("source", "")).lower(), float(row.get("price", 0) or 0), str(row.get("title", "")).lower())
                    if key in seen_keys:
                        continue
                    rejected.append(row)

                if len(observations) >= 3 and self._has_amazon_observation(observations):
                    break
        except requests.RequestException:
            return MarketFetchResult(
                status="error",
                provider=self.provider_name,
                query=query,
                observations=[],
                message="Live market lookup failed, so available catalog competitor data was used.",
                rejected_observations=[],
            )

        observations.sort(key=lambda item: (-item["match_score"], -item["normalized_price"]))
        rejected.sort(key=lambda item: (-item["match_score"], -item["price"]))
        observations = observations[:6]
        rejected = rejected[:8]

        used_name_fallback = len(executed_queries) > 1
        if len(observations) >= 3:
            status = "live"
            message = (
                f"Using {len(observations)} comparable live observations; "
                f"{len(rejected)} non-comparable offers were excluded."
            )
        elif observations:
            status = "partial_live"
            message = (
                f"Only {len(observations)} comparable live observations found; "
                "sample data may still be needed."
            )
        else:
            status = "no_results"
            message = "No competitor found in live market search."

        if used_name_fallback:
            message += " Product-name fallback search was used."

        return MarketFetchResult(
            status=status,
            provider=self.provider_name,
            query=" | ".join(executed_queries),
            observations=observations,
            message=message,
            rejected_observations=rejected,
        )

    @staticmethod
    def _queries_for_product(sku: str, product: dict[str, Any] | None) -> list[str]:
        if not product:
            return [sku, f"Amazon {sku}"]

        primary_parts = [
            product.get("mpn") or sku,
            product.get("brand", ""),
            product.get("product", ""),
        ]
        fallback_parts = [
            product.get("brand", ""),
            product.get("product", ""),
        ]

        queries: list[str] = []
        for parts in (primary_parts, fallback_parts):
            query = " ".join(str(part).strip() for part in parts if str(part).strip())
            if query and query not in queries:
                queries.append(query)
                amazon_query = f"Amazon {query}"
                if amazon_query not in queries:
                    queries.append(amazon_query)
        return queries or [sku]

    @staticmethod
    def _has_amazon_observation(observations: list[dict[str, Any]]) -> bool:
        return any("amazon" in str(item.get("source", "")).lower() for item in observations)

    def _search_google_shopping(self, query: str) -> dict[str, Any]:
        response = requests.get(
            "https://serpapi.com/search",
            params={
                "engine": "google_shopping",
                "q": query,
                "gl": "us",
                "hl": "en",
                "location": self.location,
                "api_key": self.api_key,
            },
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def _parse_google_shopping(
        self,
        payload: dict[str, Any],
        sku: str,
        product: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows = payload.get("shopping_results") or []
        observations: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        seen: set[tuple[str, float, str]] = set()

        for row in rows:
            price = self._extract_price(row)
            if price is None or price <= 0:
                continue

            source = str(row.get("source") or row.get("seller") or "Google Shopping").strip()
            title = str(row.get("title") or "").strip()
            key = (source.lower(), price, title.lower())
            if key in seen:
                continue
            seen.add(key)

            observation = self._build_observation(row, source, title, price, sku, product)
            if observation["comparable"]:
                observations.append(observation)
            else:
                rejected.append(observation)

        observations.sort(key=lambda item: (-item["match_score"], item["normalized_price"]))
        rejected.sort(key=lambda item: (-item["match_score"], item["price"]))
        return observations[:6], rejected[:8]

    def _build_observation(
        self,
        row: dict[str, Any],
        source: str,
        title: str,
        price: float,
        sku: str,
        product: dict[str, Any] | None,
    ) -> dict[str, Any]:
        package = self._package_info(title)
        normalized_price = self._normalized_price(price, package, product)
        match_score = self._match_score(title, source, sku, product)
        exclusion_reason = self._exclusion_reason(title, source, sku, product, package, match_score)

        return {
            "source": source,
            "price": round(price, 2),
            "normalized_price": round(normalized_price, 2),
            "normalized_uom": (product or {}).get("uom", "EA"),
            "package_quantity": package["quantity"],
            "package_type": package["type"],
            "package_confidence": package["confidence"],
            "stock": self._stock_from_row(row),
            "freshness_hours": 0,
            "match_score": match_score,
            "title": title,
            "offer_sku": self._offer_identifier(title, sku, product),
            "image": row.get("thumbnail") or row.get("serpapi_thumbnail"),
            "link": row.get("link") or row.get("product_link"),
            "provider": self.provider_name,
            "comparable": exclusion_reason is None,
            "exclusion_reason": exclusion_reason,
        }

    @staticmethod
    def _extract_price(row: dict[str, Any]) -> float | None:
        extracted = row.get("extracted_price")
        if isinstance(extracted, (int, float)):
            return float(extracted)

        value = str(row.get("price") or "")
        match = re.search(r"(\d+(?:,\d{3})*(?:\.\d{1,2})?)", value)
        if not match:
            return None
        return float(match.group(1).replace(",", ""))

    @staticmethod
    def _stock_from_row(row: dict[str, Any]) -> str:
        text = " ".join(
            str(row.get(field) or "")
            for field in ("delivery", "snippet", "tag", "badge")
        ).lower()
        if "out of stock" in text:
            return "Out of stock"
        if "in stock" in text:
            return "In stock"
        if "limited" in text:
            return "Limited"
        return "Available"

    def _normalized_price(self, price: float, package: dict[str, Any], product: dict[str, Any] | None) -> float:
        if not product:
            return price

        expected = self._expected_package(product)
        offer_qty = max(float(package["quantity"]), 1.0)
        expected_qty = max(float(expected["quantity"]), 1.0)
        expected_uom = str(product.get("uom", "EA")).upper()

        if expected_uom == "EA":
            return price / offer_qty
        if expected_uom in {"CS", "CASE"}:
            return price * (expected_qty / offer_qty)
        return price

    def _exclusion_reason(
        self,
        title: str,
        source: str,
        sku: str,
        product: dict[str, Any] | None,
        package: dict[str, Any],
        match_score: float,
    ) -> str | None:
        if not product:
            return "No Quest catalog product found for SKU."

        text = f"{title} {source}".lower()
        mpn = str(product.get("mpn", "")).lower()
        brand = str(product.get("brand", "")).lower()
        has_identifier = bool((mpn and mpn in text) or sku.lower() in text)

        if match_score < 0.72:
            return "Low product match score."
        strong_name_match = match_score >= 0.86

        if brand and brand not in text and not has_identifier and not strong_name_match:
            return "Brand or MPN was not confirmed."
        if mpn and not has_identifier and match_score < 0.82 and not strong_name_match:
            return "MPN/SKU was not confirmed."

        mismatch = self._attribute_mismatch(product, title)
        if mismatch:
            return mismatch

        expected = self._expected_package(product)
        if expected["type"] == "case" and expected["quantity"] > 1 and package["confidence"] == "unknown":
            return "Package quantity could not be normalized to Quest case UOM."

        return None

    @staticmethod
    def _expected_package(product: dict[str, Any]) -> dict[str, Any]:
        title = str(product.get("product", ""))
        detected = LiveMarketDataService._package_info(title)
        uom = str(product.get("uom", "EA")).upper()
        if uom in {"CS", "CASE"}:
            detected["type"] = "case"
            if detected["confidence"] == "unknown":
                detected["confidence"] = "uom"
        return detected

    @staticmethod
    def _package_info(title: str) -> dict[str, Any]:
        text = title.lower()
        patterns = [
            (r"(?:case|cs)\s*(?:of|pack)?\s*(\d{1,4})", "case"),
            (r"(\d{1,4})\s*(?:per|/)\s*(?:case|cs)", "case"),
            (r"(?:box|bx)\s*(?:of)?\s*(\d{1,4})", "box"),
            (r"(\d{1,4})\s*(?:per|/)\s*(?:box|bx)", "box"),
            (r"(?:pack|pk)\s*(?:of)?\s*(\d{1,4})", "pack"),
            (r"(\d{1,4})\s*[- ]?(?:pack|pk)\b", "pack"),
            (r"(\d{1,4})\s*(?:pair|pairs)\b", "pair"),
        ]
        for pattern, package_type in patterns:
            match = re.search(pattern, text)
            if match:
                quantity = int(match.group(1))
                return {
                    "quantity": max(quantity, 1),
                    "type": package_type,
                    "confidence": "title",
                }
        if "case" in text:
            return {"quantity": 1, "type": "case", "confidence": "label"}
        if "box" in text:
            return {"quantity": 1, "type": "box", "confidence": "label"}
        if "pair" in text:
            return {"quantity": 1, "type": "pair", "confidence": "label"}
        return {"quantity": 1, "type": "each", "confidence": "unknown"}

    @staticmethod
    def _offer_identifier(title: str, sku: str, product: dict[str, Any] | None) -> str:
        text = title.lower()
        if sku.lower() in text:
            return sku
        if product:
            mpn = str(product.get("mpn", ""))
            if mpn and mpn.lower() in text:
                return mpn

        for token in re.findall(r"\b[A-Z0-9][A-Z0-9-]{4,}\b", title):
            if any(char.isdigit() for char in token):
                return token
        return ""

    @staticmethod
    def _attribute_mismatch(product: dict[str, Any], offer_title: str) -> str | None:
        product_text = str(product.get("product", "")).lower()
        offer_text = offer_title.lower()

        product_dim = LiveMarketDataService._first_dimension(product_text)
        offer_dim = LiveMarketDataService._first_dimension(offer_text)
        if product_dim and offer_dim and product_dim != offer_dim:
            return f"Dimension mismatch: expected {product_dim}, found {offer_dim}."

        product_size = LiveMarketDataService._first_token(product_text, SIZE_TOKENS)
        offer_size = LiveMarketDataService._first_token(offer_text, SIZE_TOKENS)
        if product_size and offer_size and product_size != offer_size:
            return f"Size mismatch: expected {product_size}, found {offer_size}."

        product_color = LiveMarketDataService._first_token(product_text, COLOR_TOKENS)
        offer_color = LiveMarketDataService._first_token(offer_text, COLOR_TOKENS)
        if product_color and offer_color and product_color != offer_color:
            return f"Color mismatch: expected {product_color}, found {offer_color}."

        return None

    @staticmethod
    def _first_dimension(text: str) -> str:
        match = re.search(r"\b\d+(?:\.\d+)?\s*x\s*\d+(?:\.\d+)?(?:\s*x\s*\d+(?:\.\d+)?)?\b", text)
        return re.sub(r"\s+", "", match.group(0)) if match else ""

    @staticmethod
    def _first_token(text: str, options: tuple[str, ...]) -> str:
        for token in options:
            if re.search(rf"\b{re.escape(token)}\b", text):
                return token
        return ""

    @staticmethod
    def _match_score(title: str, source: str, sku: str, product: dict[str, Any] | None) -> float:
        text = f"{title} {source}".lower()
        score = 0.58
        if sku.lower() in text:
            score += 0.18
        if product:
            mpn = str(product.get("mpn", "")).lower()
            brand = str(product.get("brand", "")).lower()
            if mpn and mpn in text:
                score += 0.2
            if brand and brand in text:
                score += 0.12
            title_tokens = {
                token
                for token in re.findall(r"[a-z0-9]+", str(product.get("product", "")).lower())
                if len(token) >= 4
            }
            if title_tokens:
                found = sum(1 for token in title_tokens if token in text)
                score += min(0.15, found / len(title_tokens) * 0.15)
                if found >= max(2, min(4, len(title_tokens))):
                    score += 0.05
        if any(name in source.lower() for name in COMPETITOR_HINTS):
            score += 0.05
        return round(min(score, 0.98), 2)
