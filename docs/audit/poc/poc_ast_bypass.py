"""PoC: the codegen pre-check (build/codegen/bodies.py precheck) is a denylist; these bodies pass it."""
from sbam_workers.build.codegen.bodies import CodeBodies, precheck
from sbam_workers.build.codegen.render import RenderedSkeleton

SK = RenderedSkeleton.__new__(RenderedSkeleton)
object.__setattr__(SK, "files", {}) if hasattr(SK, "__dataclass_fields__") else setattr(SK, "files", {})

PAYLOADS = {
 "baseline eval (should be blocked)": "eval('1')",
 "builtins via string getattr": "b = getattr(__builtins__, '__imp' + 'ort__')\nm = b('o' + 's')\nm.getcwd()",
 "open() arbitrary file read": "data = open('/etc/passwd').read()\nreturn data",
 "class walk without __subclasses__": "k = getattr(getattr((), '__cla' + 'ss__'), '__ba' + 'se__')\nreturn getattr(k, '__subcl' + 'asses__')()",
 "globals()/vars()": "g = globals()\nreturn g",
 "frame escape": "import sys\nreturn sys._getframe().f_globals",
 "while is blocked (control)": "while True:\n    pass",
 "unbounded recursion / for-range DoS": "for i in range(10**12):\n    pass",
 "memory bomb": "x = 'a' * (10**11)",
}
def run(body):
    b = CodeBodies(action_body=body, check_body="pass", state_queries={}, helpers="")
    return precheck(b, ["json", "sys"], {"fresh_state": []}, SK)

for name, body in PAYLOADS.items():
    f = run(body)
    print(("BLOCKED " if f else "PASSES  ") + name, [x.code.value for x in f])
# state_queries are only regex-checked: cross-table / cross-tenant reads and DoS pass
Q = {
 "cross-schema read": "SELECT email FROM sbam.users WHERE %s IS NOT NULL",
 "server file read":  "SELECT pg_read_file('/etc/passwd') WHERE %s IS NOT NULL",
 "DoS":               "SELECT pg_sleep(3600) WHERE %s IS NOT NULL",
}
for name, q in Q.items():
    b = CodeBodies(action_body="pass", check_body="pass", state_queries={"s": q}, helpers="")
    f = precheck(b, ["json"], {"fresh_state": ["s"]}, SK)
    print(("BLOCKED " if f else "PASSES  ") + "state_query: " + name, [x.code.value for x in f])
