"""scripts/bedrock-models: join an AWS Bedrock model listing with the gateway's price list."""

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bedrock-models"


def load():
    loader = importlib.machinery.SourceFileLoader("bedrock_models", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


bm = load()


def summary(model_id, name, maker, inputs=("TEXT",), outputs=("TEXT",), types=("ON_DEMAND",), status="ACTIVE"):
    return {"modelId": model_id, "modelName": name, "providerName": maker, "inputModalities": list(inputs), "outputModalities": list(outputs),
            "inferenceTypesSupported": list(types), "modelLifecycle": {"status": status}}


def listing():
    table = bm.price_table()
    global_anthropic = next(k for k in table if k.startswith("global.anthropic.claude"))
    return {"modelSummaries": [
        summary("amazon.nova-micro-v1:0", "Nova Micro", "Amazon"),
        summary(global_anthropic.removeprefix("global."), "A Claude", "Anthropic", inputs=("TEXT", "IMAGE"), types=("INFERENCE_PROFILE",)),
        summary("acme.imported-llm-v1:0", "Imported thing", "Acme"),
        summary("amazon.titan-embed-text-v2:0", "Titan Embeddings", "Amazon", outputs=("EMBEDDING",)),
        summary("old.model-v1:0", "Old model", "Old", status="LEGACY"),
        summary("acme.provisioned-v1:0", "Provisioned only", "Acme", types=("PROVISIONED",)),
    ]}, global_anthropic


def run(payload, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=json.dumps(payload), capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ":".join(p for p in sys.path if "site-packages" in p)})


def test_prices_come_from_the_gateways_price_list_not_from_aws():
    payload, _ = listing()
    found = {r["aws_id"]: r for r in bm.rows(payload, "us", bm.price_table(), images=False, pattern=None, everything=False)}
    assert found["amazon.nova-micro-v1:0"]["price"] == (0.035, 0.14) and found["amazon.nova-micro-v1:0"]["priced"]
    assert found["acme.imported-llm-v1:0"]["priced"] is False and found["acme.imported-llm-v1:0"]["price"] is None


def test_embeddings_and_legacy_models_are_hidden_unless_asked_for():
    payload, _ = listing()
    default = {r["aws_id"] for r in bm.rows(payload, "us", bm.price_table(), False, None, False)}
    everything = {r["aws_id"] for r in bm.rows(payload, "us", bm.price_table(), False, None, True)}
    assert "amazon.titan-embed-text-v2:0" not in default and "old.model-v1:0" not in default
    assert {"amazon.titan-embed-text-v2:0", "old.model-v1:0"} <= everything


def test_inference_profile_only_models_are_called_with_the_geo_prefix_and_still_found_in_the_price_list():
    payload, global_key = listing()
    rows = {r["aws_id"]: r for r in bm.rows(payload, "us", bm.price_table(), False, None, False)}
    claude = rows[global_key.removeprefix("global.")]
    assert claude["id"] == "us." + global_key.removeprefix("global.") and claude["priced"] and claude["image"]
    assert bm.callable_id(summary("x.y-v1:0", "n", "m", types=("PROVISIONED",)), "us") is None  # provisioned throughput: an ARN, not an id


def test_the_context_window_suffix_does_not_hide_a_price():
    key, entry = bm.find_price("amazon.nova-lite-v1:0:300k", "us", bm.price_table())
    assert key == "amazon.nova-lite-v1:0" and entry is not None


def test_the_printed_table_says_what_the_gateway_can_serve():
    payload, _ = listing()
    result = run(payload)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "Nova Micro" in out and "Imported thing" not in out and "Provisioned only" not in out  # unpriced models are skipped
    assert "the gateway can serve 2" in out
    assert "Titan Embeddings" not in out
    only_images = run(payload, "--images").stdout
    assert "A Claude" in only_images and "Nova Micro" not in only_images


def test_bad_input_is_explained():
    result = subprocess.run([sys.executable, str(SCRIPT)], input="not json", capture_output=True, text=True)
    assert result.returncode == 2 and "list-foundation-models" in result.stderr


def test_sorted_by_output_price_descending():
    payload, _ = listing()
    out = run(payload).stdout.splitlines()
    names = [line for line in out if "Nova Micro" in line or "A Claude" in line]
    assert "A Claude" in names[0] and "Nova Micro" in names[1]  # Claude costs more per output token than Nova Micro
