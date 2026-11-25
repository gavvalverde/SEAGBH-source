"""Private-to-grader truth; neither input construction nor wire code imports this."""
from copy import deepcopy

ORACLES = {
    # (function, [(args, before result, after result, before state, after state)])
    "K68E-01": ("accepts", (
        ((18,), True, False, None, None),
        ((17,), True, True, None, None),
        ((19,), False, False, None, None),
    ), True),
    "K68E-02": ("handoff", (
        (({"status": "draft"},), False, True, "draft", "sent"),
        (({"status": "packed"},), True, True, "sent", "sent"),
        (({"status": "sent"},), False, False, "sent", "sent"),
    ), True),
    "K68E-03": ("names", (
        (([("a", False), ("b", True)],), ["b"], ["b"], None, None),
        (([],), [], [], None, None),
    ), False),
    "K68E-04": ("suffix", (
        (([],), IndexError, None, None, None),
        ((["label"],), "label", "label", None, None),
    ), False),
}


def prove(case):
    name, probes, positive = ORACLES[case.id]
    environments = ({}, {})
    # Execute only fixed, local invented source from the four allowlisted inputs.
    for source, env in zip((case.before, case.after), environments):
        exec(compile(source, case.path, "exec"), env)
    for args, old, new, old_state, new_state in probes:
        for env, expected, state in zip(environments, (old, new), (old_state, new_state)):
            supplied = deepcopy(args)
            try:
                actual = env[name](*supplied)
            except IndexError:
                actual = IndexError
            if actual != expected or (state is not None and supplied[0]["status"] != state):
                raise ValueError(f"{case.id}: red/green oracle mismatch")
    return positive
