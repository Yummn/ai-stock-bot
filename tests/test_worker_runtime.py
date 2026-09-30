from ai_stock_bot.v2.jobs import external_runtime
from ai_stock_bot.v2.schema import JobRequest
from ai_stock_bot.v4.schema import Plan


def test_focused_tree_and_paper_stay_in_main_runtime():
    for kind in ("portfolio_test", "portfolio_update", "portfolio_frequency", "intraday_refresh"):
        request = JobRequest(kind=kind, investment=Plan(method="ml"), compare_models=False)
        assert external_runtime(request, model_python="neural") is None


def test_neural_and_legacy_workloads_keep_existing_gpu_environment():
    for kind in ("portfolio_test", "portfolio_update", "portfolio_frequency"):
        request = JobRequest(kind=kind, investment=Plan(method="cnn"), compare_models=False)
        assert external_runtime(request, model_python="neural") == "neural"
    request = JobRequest(kind="portfolio_test", investment=Plan(), compare_models=True)
    assert external_runtime(request, model_python="neural") == "neural"
    assert external_runtime(JobRequest(kind="backtest"), model_python="neural") == "neural"


def test_optional_intraday_runtime_and_unconfigured_compatibility():
    request = JobRequest(kind="intraday_test", investment=Plan())
    assert external_runtime(request, "research", "neural") == "research"
    assert external_runtime(request, model_python="neural") is None
    assert external_runtime(JobRequest(kind="portfolio_test", investment=Plan(method="cnn"))) is None


def test_forward_observation_uses_pinned_model_runtime():
    for method, expected in [('rotation', None), ('ml', None), ('cnn', 'neural')]:
        request = JobRequest(kind='portfolio_observe', observation_id='a' * 32,
                             investment=Plan(method=method), compare_models=False)
        assert external_runtime(request, model_python='neural') == expected
