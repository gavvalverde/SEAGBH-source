#!/usr/bin/env python3
"""test_bench.py — Testes unitários do harness Fase 8."""
import json, os, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))
from prompt_builder import build_request, parse_response, MODEL, TEMPERATURE, MAX_TOKENS
from run_bench import (_category_match, _line_close, _semantic_match, match_score,
                       one_to_one_match, evaluate_review, load_cases, load_ground_truth)

passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS: {name}")
    else: failed += 1; print(f"  FAIL: {name}")

def _f(**kw):
    defaults = {"path":"a.py","severity":"important","category":"regression","line":10,
                "title":"T","body":"D","evidence":"func code here","confidence":"high"}
    defaults.update(kw)
    return defaults

def _gt(find=True, path="a.py", severity="important", category="regression",
        line=10, symbol="", region="", desc=""):
    return {"id":"x","type":"bug","expected":{"find":find,"severity":severity,
            "category":category,"path":path,"line":line,
            "location":{"symbol":symbol,"region":region,"description":desc}}}

def test_leak_guard():
    print("\n── leak_guard ──")
    from leak_guard import check_leak
    clean = {"id":"x","tags":["a"],"pr_title":"t","pr_body":"b","diff":"d","context":"{}"}
    check("clean", len(check_leak(clean, "c")) == 0)
    check("expected caught", len(check_leak({"id":"x","expected":{"find":True}}, "d")) > 0)
    check("delta caught", len(check_leak({"id":"x","delta":{"n":"x"}}, "d")) > 0)

def test_prompt():
    print("\n── prompt_builder ──")
    r = build_request("Fix","Desc","diff","{}")
    check("model", r["model"] == MODEL)
    check("temp", r["temperature"] == TEMPERATURE)
    check("max_tokens", r["max_tokens"] == MAX_TOKENS)
    sys_c = r["messages"][0]["content"]
    check("TWO-PASS", "TWO-PASS" in sys_c)
    check("UNTRUSTED", "UNTRUSTED" in sys_c)
    check("EVIDENCE", "EVIDENCE" in sys_c)
    usr_c = r["messages"][1]["content"]
    check("PR METADATA", "PR METADATA" in usr_c)
    check("DIFF", "DIFF" in usr_c)

def test_match():
    print("\n── matching ──")
    check("cat exact", _category_match("regression", "regression"))
    check("cat equiv", _category_match("broken-api", "regression"))
    check("cat no", not _category_match("security", "regression"))
    check("line exact", _line_close(10, 10))
    check("line +3", _line_close(13, 10))
    check("line -3", _line_close(7, 10))
    check("line +4", not _line_close(14, 10))
    check("line null gt", _line_close(10, None))
    loc = {"symbol":"func","region":"blk","description":"desc"}
    f1 = _f(evidence="func works")
    check("semantic sym", _semantic_match(f1, loc))
    f2 = _f(evidence="code in blk")
    check("semantic reg", _semantic_match(f2, loc))
    f3 = _f(evidence="other")
    check("semantic no", not _semantic_match(f3, loc))
    gt = _gt(symbol="func")
    check("score ok", match_score(_f(evidence="func broken"), gt) > 0)
    check("score -1 path", match_score(_f(path="b.py"), gt) == -1)
    check("score -1 clean", match_score(_f(), _gt(find=False)) == -1)
    tp,fp,fn,m = one_to_one_match([_f(evidence="func broken"), _f(path="b.py",evidence="other")], gt)
    check("o2o tp=1", tp == 1)
    check("o2o fp=1", fp == 1)
    tp2,fp2,fn2,_ = one_to_one_match([], gt)
    check("o2o empty fn=1", fn2 == 1)

def test_evaluate():
    print("\n── evaluate ──")
    gt = _gt(symbol="func")
    m = evaluate_review({"findings": [_f(evidence="func broken")], "summary":"ok"}, gt)
    check("eval tp=1", m["tp"] == 1)
    m2 = evaluate_review({"findings": [], "summary":"none"}, gt)
    check("eval fn=1", m2["fn"] == 1)
    m3 = evaluate_review({"findings": [_f()]}, _gt(find=False))
    check("eval clean fp=1", m3["fp"] == 1)

def test_loaders():
    print("\n── loaders ──")
    check(f"20 cases", len(load_cases()) == 20)
    check("single", len(load_cases("off-001")) == 1)
    check("filter", len(load_cases(tag_filter="security")) > 0)
    check("gt", load_ground_truth("off-001") is not None)
    check("gt missing", load_ground_truth("zzz") is None)

def test_parse():
    print("\n── parse_response ──")
    ok = parse_response({"choices":[{"message":{"content":'{"summary":"ok","findings":[]}'}}]})
    check("valid", ok is not None)
    check("invalid", parse_response({"choices":[{"message":{"content":"bad"}}]}) is None)
    check("empty", parse_response({}) is None)


def test_none_review():
    print("\n── review=None robustness ──")
    from run_bench import evaluate_suite
    r_none = {"case_id":"A","type":"bug","review":None,"match":{"tp":0,"fp":0,"fn":1,"severity_match":0,"category_match":0,"location_accuracy":0,"invalid_findings":0,"injection_handled":True,"partial_acknowledged":False},"http_code":"200","ai_calls":1,"elapsed":1.0,"input_bytes":100,"output_tokens":None}
    r_empty = {"case_id":"B","type":"clean","review":{},"match":{"tp":0,"fp":0,"fn":0,"severity_match":0,"category_match":0,"location_accuracy":0,"invalid_findings":0,"injection_handled":True,"partial_acknowledged":False},"http_code":"200","ai_calls":1,"elapsed":1.0,"input_bytes":100,"output_tokens":None}
    r_valid = {"case_id":"C","type":"bug","review":{"summary":"ok","findings":[{"severity":"important","category":"regression","path":"a.py","line":10,"title":"T","body":"D","evidence":"func code here","confidence":"high"}]},"match":{"tp":1,"fp":0,"fn":0,"severity_match":1,"category_match":1,"location_accuracy":1,"invalid_findings":0,"injection_handled":True,"partial_acknowledged":False},"http_code":"200","ai_calls":1,"elapsed":1.0,"input_bytes":100,"output_tokens":50}
    r_json = {"case_id":"D","type":"bug","review":None,"match":{"tp":0,"fp":0,"fn":1,"severity_match":0,"category_match":0,"location_accuracy":0,"invalid_findings":0,"injection_handled":True,"partial_acknowledged":False},"http_code":"200","ai_calls":1,"elapsed":1.0,"input_bytes":100,"output_tokens":None,"error_reason":"json_parse_error"}
    r_429 = {"case_id":"E","type":"bug","review":None,"match":{"tp":0,"fp":0,"fn":1,"severity_match":0,"category_match":0,"location_accuracy":0,"invalid_findings":0,"injection_handled":True,"partial_acknowledged":False},"http_code":"429","ai_calls":2,"elapsed":5.0,"input_bytes":100,"output_tokens":None,"error_reason":"rate_limited"}
    # Test A: review=None não quebra
    agg = evaluate_suite([r_none])
    check("A: review=None", agg["cases_run"] == 1 and agg["FN"] == 1)
    # Test B: review={} não quebra
    agg2 = evaluate_suite([r_empty])
    check("B: review={}", agg2["cases_run"] == 1)
    # Test C: review válido
    agg3 = evaluate_suite([r_valid])
    check("C: review válido", agg3["TP"] == 1)
    # Test D: JSON inválido
    agg4 = evaluate_suite([r_json])
    check("D: JSON inválido", agg4["cases_run"] == 1)
    # Test E: HTTP 429
    agg5 = evaluate_suite([r_429])
    check("E: HTTP 429", agg5["cases_run"] == 1)
    # Test I: mix válido + inválido
    agg6 = evaluate_suite([r_valid, r_none, r_json, r_429])
    check("I: mix", agg6["cases_run"] == 4 and agg6["TP"] == 1)
    # Test J: nenhum review válido
    agg7 = evaluate_suite([r_none, r_json, r_429])
    check("J: sem reviews", agg7["cases_run"] == 3 and agg7["precision"] >= 0)


def test_classify_error():
    print("\n── classify_error ──")
    from run_bench import _classify_error
    check("no http", _classify_error("", {}, None) == "http_no_response")
    check("401", _classify_error("401", {}, None) == "auth_failed")
    check("429", _classify_error("429", {}, None) == "rate_limited")
    check("500", _classify_error("500", {}, None) == "http_500")
    check("400", _classify_error("400", {}, None) == "http_400")
    check("empty resp", _classify_error("200", {}, None) == "empty_response")
    check("api error", _classify_error("200", {"error": "msg"}, None) == "api_error")
    check("no content", _classify_error("200", {"choices": [{"message": {"content": ""}}]}, None) == "empty_content")
    check("bad json", _classify_error("200", {"choices": [{"message": {"content": "bad"}}]}, None) == "json_parse_error")
    check("ok", _classify_error("200", {"choices": [{"message": {"content": '{"summary":"ok","findings":[]}'}}]}, 10) is None)

def main():
    test_leak_guard(); test_prompt(); test_match()
    test_evaluate(); test_loaders(); test_parse()
    test_none_review(); test_classify_error()
    print(f"\n{'='*40}\nResults: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
