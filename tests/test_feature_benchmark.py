from bench.evaluate_features import run_scenarios


def test_deterministic_feature_benchmark_scenarios_pass():
    result = run_scenarios()
    assert result["mode"] == "deterministic-no-network"
    assert result["passed"] == result["total"] == 10
    assert result["accuracy_percent"] == 100.0
    assert {"policy", "watchtower", "replay", "cli"} == set(result["groups"])
    batch = result["groups"]["watchtower"]["scheduled_job_batch"]
    assert batch["completed_jobs"] == batch["selected_jobs"] == 3
    assert batch["maximum_observed_concurrency"] == batch["configured_concurrency"] == 3
