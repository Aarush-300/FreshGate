import pytest
import json
from pathlib import Path

from bench.evaluate import run_evaluation, compute_percentile

ROOT_DIR = Path(__file__).resolve().parent.parent


def test_production_dataset_schema_and_size():
    dataset_path = ROOT_DIR / "bench" / "dataset_production.json"
    assert dataset_path.exists(), "dataset_production.json must exist"

    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    # Must contain at least 500 independently labelled items
    assert len(dataset) >= 500, f"Expected >= 500 items, got {len(dataset)}"

    categories = set()
    verdicts = set()
    adversarial_types = set()

    for item in dataset:
        assert "id" in item
        assert "category" in item
        assert "difficulty" in item
        assert "input_text" in item
        assert "target_claim" in item
        assert "ground_truth_verdict" in item
        assert "simulated_sources" in item

        categories.add(item["category"])
        verdicts.add(item["ground_truth_verdict"])
        if item.get("adversarial_type"):
            adversarial_types.add(item["adversarial_type"])

    # Verify category coverage (11 volatile categories + control)
    required_cats = {"pricing", "version", "leadership", "law", "product_availability",
                     "cybersecurity", "financial", "government_policy", "public_health",
                     "scientific_findings", "company_info", "control"}
    for cat in required_cats:
        assert cat in categories, f"Missing category: {cat}"

    # Verify all 4 verdicts are represented
    assert "confirmed" in verdicts
    assert "contradicted" in verdicts
    assert "partially_supported" in verdicts
    assert "unverifiable" in verdicts

    # Verify adversarial coverage
    assert "prompt_injection" in adversarial_types
    assert "conflicting_sources" in adversarial_types
    assert "spoofed_dates" in adversarial_types
    assert "ambiguous_entities" in adversarial_types


def test_compute_percentile():
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    p50 = compute_percentile(data, 0.50)
    assert p50 == 30.0
    p0 = compute_percentile(data, 0.0)
    assert p0 == 10.0
    p100 = compute_percentile(data, 1.0)
    assert p100 == 50.0


@pytest.mark.asyncio
async def test_run_unit_evaluation_suite():
    summary = await run_evaluation(suite="unit", live_mode=False)

    assert summary["suite"] == "unit"
    assert summary["total_items"] == 50
    assert summary["precision"] >= 90.0
    assert summary["recall"] >= 90.0
    assert summary["f1"] >= 90.0
    assert summary["false_positive_rate"] <= 5.0
    assert summary["cache_hit_rate"] == 100.0
    assert summary["mean_warm_latency_ms"] < 10.0
